"""
网格执行引擎实现

功能：
- 与交易所适配器交互，执行订单操作
- 通过 WebSocket 实时监控订单成交
- 支持批量下单、订单健康检查、价格监控

核心机制：
1. 订单追踪：使用 client_id 映射订单（Lighter支持双ID系统）
2. 成交触发：WebSocket 推送 FILLED 状态时立即触发反手单
3. 健康检查：定时验证订单和持仓状态，自动修复异常
4. 智能监控：WebSocket 优先，REST API 备用

Lighter 交易所特殊处理：
- 订单有两个ID：client_id（下单时）和 order_index（WebSocket推送时）
- 使用 _pending_orders_by_client_id 缓存实现快速匹配
- 批量下单时串行执行，避免 nonce 冲突
- 全局下单锁确保所有下单操作（反手单、止盈单、健康检查补单）串行

健康检查机制：
1. 等待5秒内无订单成交（避免高频交易时检查）
2. 3次快照验证（间隔0.5秒，确保数据一致且稳定）
3. 通过验证后执行订单和持仓修复
"""

import asyncio
import inspect
import json
import os
import re
import time
from typing import List, Optional, Callable, Dict, Set, Tuple, Any
from decimal import Decimal
from datetime import datetime

from ....logging import get_logger
from ....adapters.exchanges import ExchangeInterface, OrderSide as ExchangeOrderSide, OrderType
from ....adapters.exchanges.models import ExchangeType
from collections import deque
from ..interfaces.grid_engine import IGridEngine
from ..models import GridConfig, GridOrder, GridOrderSide, GridOrderStatus, GridType


# ---------------------------------------------------------------------------
# 🔥 API 熔斷器跨進程持久化（防止重啟期間繼續打 API 加劇 BAN）
#
# 狀態檔位置：data/.circuit_breaker_state.json
# 內容範例：
#   {"code": "-1003", "until_ts": 1777801349.452,
#    "until_iso": "2026-05-03 15:22:29", "saved_at": 1777800000.0}
#   {"code": "-2015", "until_ts": null, "until_iso": "permanent", ...}
# ---------------------------------------------------------------------------

CIRCUIT_STATE_FILENAME = ".circuit_breaker_state.json"


def _get_circuit_state_file_path() -> str:
    """取得熔斷狀態檔絕對路徑（位於 repo 根的 data/ 目錄）"""
    repo_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
    return os.path.join(repo_root, "data", CIRCUIT_STATE_FILENAME)


def read_persisted_circuit_state() -> Optional[Dict[str, object]]:
    """
    讀取持久化的熔斷狀態。

    Returns:
        - None：無檔案 / 已過期 / 解析失敗
        - dict：{"code": str, "until_ts": float|None, "until_iso": str}
                until_ts 為 None 表示永久熔斷（-2015）
    """
    path = _get_circuit_state_file_path()
    try:
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        until_ts = data.get("until_ts")
        # 永久熔斷：保留
        if until_ts is None:
            return data
        # 已過期：清理檔案後回傳 None
        if float(until_ts) <= time.time():
            try:
                os.remove(path)
            except OSError:
                pass
            return None
        return data
    except Exception:
        return None


def write_persisted_circuit_state(code: str, until_ts: Optional[float]) -> None:
    """
    寫入熔斷狀態到磁碟。

    Args:
        code: 錯誤碼，如 '-1003' 或 '-2015'
        until_ts: 熔斷到期 unix 時間戳；None = 永久熔斷
    """
    path = _get_circuit_state_file_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if until_ts is None:
            until_iso = "permanent"
        else:
            until_iso = datetime.fromtimestamp(float(until_ts)).strftime(
                "%Y-%m-%d %H:%M:%S")
        payload = {
            "code": code,
            "until_ts": until_ts,
            "until_iso": until_iso,
            "saved_at": time.time(),
            "saved_at_iso": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        # 持久化失敗不應影響主流程
        pass


def clear_persisted_circuit_state() -> None:
    """刪除熔斷狀態檔（BAN 解除後可呼叫以清除磁碟殘留）"""
    path = _get_circuit_state_file_path()
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


class GridEngineImpl(IGridEngine):
    """
    网格执行引擎实现

    复用现有组件：
    - 交易所适配器（ExchangeInterface）
    - 订单管理
    - WebSocket订阅
    """

    # 🔥 类变量：API 熔斷器狀態（所有實例共享）
    _shared_api_circuit_until: float = 0.0
    _shared_last_api_error_code: Optional[str] = None
    _shared_circuit_initialized: bool = False

    # 實例變數預設值（避免 __new__ mock 遺漏）
    _price_failures: int = 0
    _price_successes: int = 0
    _last_price_failure_time: float = 0.0
    _price_failure_reset_timeout: float = 30.0
    _price_max_failures: int = 15
    _price_success_required_to_reset: int = 3

    def __init__(self, exchange_adapter: ExchangeInterface):
        """
        初始化执行引擎

        Args:
            exchange_adapter: 交易所适配器（通过DI注入）
        """
        self.logger = get_logger(__name__)
        self.exchange = exchange_adapter
        self.config: GridConfig = None
        self.coordinator = None

        # 價格獲取故障計數器（實例隔離）
        self._price_failures = 0
        self._price_successes = 0
        self._last_price_failure_time = 0
        self._price_failure_reset_timeout = 30
        self._price_max_failures = 15
        self._price_success_required_to_reset = 3

        self._order_callbacks: List[Callable] = []

        self._pending_orders: Dict[str, GridOrder] = {}
        self._expected_cancellations: set = set()

        self._current_price: Optional[Decimal] = None
        self._last_price_update_time: float = 0
        self._price_ws_enabled = False

        self._expected_total_orders: int = 0
        self._health_check_task = None
        self._last_health_check_time: float = 0
        self._last_order_fill_time: float = 0
        self._last_health_repair_count: int = 0
        self._last_health_repair_time: float = 0
        self._health_checker = None

        self._last_position_warning_time: float = 0
        self._position_warning_interval: float = 60

        self._lighter_order_lock = asyncio.Lock()

        self._pending_orders_by_client_id: Dict[str, GridOrder] = {}

        self._running = False

        self._order_rate_window_seconds: int = 0
        self._order_rate_max_orders: int = 0
        self._order_rate_timestamps = deque()
        self._order_rate_lock = asyncio.Lock()
        self._symbol_open_orders_cache: Dict[str, Tuple[int, float]] = {}
        self._symbol_open_orders_cache_lock = asyncio.Lock()
        self._price_fetch_lock = asyncio.Lock()

        if not GridEngineImpl._shared_circuit_initialized:
            GridEngineImpl._shared_circuit_initialized = True
            persisted = read_persisted_circuit_state()
            if persisted is not None:
                until_ts = persisted.get("until_ts")
                GridEngineImpl._shared_last_api_error_code = persisted.get("code")
                if until_ts is None:
                    GridEngineImpl._shared_api_circuit_until = float('inf')
                else:
                    GridEngineImpl._shared_api_circuit_until = float(until_ts)
        else:
            persisted = None

        exchange_id = getattr(exchange_adapter.config,
                              'exchange_id', 'unknown')
        self.logger.info(f"网格执行引擎初始化: {exchange_id}")
        if persisted is not None:
            self.logger.warning(
                f"⛔ 從磁碟載入熔斷狀態: code={GridEngineImpl._shared_last_api_error_code}, "
                f"until={persisted.get('until_iso')}（所有引擎實例共享此熔斷狀態）"
            )

    def _get_exchange_id(self) -> str:
        """獲取底層交易所 ID（兼容實盤與 PAPER 模擬盤）"""
        if self.config and getattr(self.config, 'exchange', None):
            return str(self.config.exchange).lower()
        if hasattr(self.exchange, 'config') and self.exchange.config:
            extra = getattr(self.exchange.config, 'extra_params', {}) or {}
            venue = extra.get('venue')
            if venue:
                return str(venue).lower()
            ex_id = getattr(self.exchange.config, 'exchange_id', '')
            if ex_id:
                return str(ex_id).lower()
        return ''

    @property
    def _api_circuit_until(self) -> float:
        return GridEngineImpl._shared_api_circuit_until

    @_api_circuit_until.setter
    def _api_circuit_until(self, value: float):
        GridEngineImpl._shared_api_circuit_until = value

    @property
    def _last_api_error_code(self) -> Optional[str]:
        return GridEngineImpl._shared_last_api_error_code

    @_last_api_error_code.setter
    def _last_api_error_code(self, value: Optional[str]):
        GridEngineImpl._shared_last_api_error_code = value

    def _parse_and_set_circuit(self, error_str: str) -> Optional[str]:
        """
        解析 API 錯誤字串，提取錯誤碼並設定熔斷狀態。
        """
        m = re.search(r'"?code"?\s*:\s*(-?\d+)', error_str)
        if not m:
            m = re.search(r'(?<!\d)(-\d{4})(?!\d)', error_str)
        code = str(m.group(1)) if m else None

        if code == '-2015':
            GridEngineImpl._shared_last_api_error_code = code
            GridEngineImpl._shared_api_circuit_until = float('inf')
            self.logger.critical(
                "🚨 API Key/IP 白名單無效 (-2015)，已永久熔斷所有引擎實例的 REST 呼叫！"
                " 請至 Binance 後台更新 IP 白名單後手動重啟交易引擎。"
            )
            write_persisted_circuit_state(code, None)
        elif code == '-1003':
            GridEngineImpl._shared_last_api_error_code = code
            bm = re.search(r'banned until (\d+)', error_str)
            if bm:
                until_ms = int(bm.group(1))
                new_until = until_ms / 1000.0
                if new_until > GridEngineImpl._shared_api_circuit_until:
                    GridEngineImpl._shared_api_circuit_until = new_until
                wait_sec = max(0, GridEngineImpl._shared_api_circuit_until - time.time())
                self.logger.error(
                    f"🚫 IP 被 BAN (-1003)，全局熔斷 {wait_sec:.0f} 秒"
                    f"（直到 {datetime.fromtimestamp(GridEngineImpl._shared_api_circuit_until).strftime('%H:%M:%S')}）"
                    f"（影響所有並發引擎實例）"
                )
            else:
                fallback_until = time.time() + 120.0
                if fallback_until > GridEngineImpl._shared_api_circuit_until:
                    GridEngineImpl._shared_api_circuit_until = fallback_until
                self.logger.error(
                    "🚫 IP 被 BAN (-1003)，未解析到 banned until 時間戳，"
                    "全局預設退避 120 秒（影響所有並發引擎實例）"
                )
            write_persisted_circuit_state(code, GridEngineImpl._shared_api_circuit_until)

        return code

    # 修補 3 (C): 不可重試 API 錯誤碼
    # 這些錯誤重試也不會改變結果,只會浪費 API 配額
    _NON_RETRYABLE_API_CODES = frozenset({
        '-5022',  # Post Only 訂單會立即成為 taker(價格穿越 spread) - 重試無用,需重算價格
        '-2015',  # API key / IP 白名單無效(已由 _parse_and_set_circuit 觸發永久熔斷)
        '-2019',  # 餘額不足 - 重試無用,需入金
        '-4003',  # 價格低於最小允許
        '-4004',  # 價格高於最大允許
        '-4014',  # 價格精度無效
        '-4104',  # 金額精度無效(已熔斷在 -2015 之外的客戶端錯)
        '-4164',  # 訂單名義價值過小(notional too small)
    })

    def _is_non_retryable_code(self, code: Optional[str]) -> bool:
        """判斷此錯誤碼是否屬於「邏輯性失敗,重試無用」。"""
        return code is not None and code in self._NON_RETRYABLE_API_CODES

    def _is_circuit_open(self) -> bool:
        return time.time() < GridEngineImpl._shared_api_circuit_until

    async def initialize(self, config: GridConfig):
        """
        初始化执行引擎
        """
        self.config = config
        try:
            ex_id = self._get_exchange_id()
            ex_type = getattr(self.exchange.config, 'exchange_type', None)
            if ex_id == 'binance':
                if ex_type in [ExchangeType.FUTURES, ExchangeType.PERPETUAL, ExchangeType.MARGIN, ExchangeType.OPTIONS]:
                    self._order_rate_window_seconds = 60
                    self._order_rate_max_orders = 1200
                else:
                    self._order_rate_window_seconds = 10
                    self._order_rate_max_orders = 100
            if ex_id == 'binance' and ex_type in [ExchangeType.FUTURES, ExchangeType.PERPETUAL]:
                try:
                    if getattr(self.config, 'apply_margin_settings', False):
                        margin_mode = getattr(self.config, 'margin_mode', 'isolated')
                        leverage = int(getattr(self.config, 'leverage', 10))
                        await self.exchange.set_margin_mode(self.config.symbol, margin_mode)
                        await self.exchange.set_leverage(self.config.symbol, leverage)
                        self.logger.info(f"✅ 已設置杠桿與保證金模式: {margin_mode}, {leverage}x")
                    else:
                        self.logger.info("ℹ️ 跳過自動設置保證金/杠桿（apply_margin_settings=False）")
                except Exception as e:
                    self.logger.warning(f"⚠️ 設置杠桿/保證金模式失敗（已跳過，且不影響運行）: {e}")
        except Exception:
            pass

        if not self.exchange.is_connected():
            await self.exchange.connect()
            self.logger.info(f"连接到交易所: {config.exchange}")

        self._ws_monitoring_enabled = False
        self._polling_task = None
        self._last_ws_check_time = 0
        self._ws_check_interval = 30
        self._last_ws_message_time = time.time()
        
        self._min_poll_interval = float(getattr(self.config, 'rest_position_query_interval', 3.0))
        self._current_poll_interval = self._min_poll_interval
        self._max_poll_interval = float(self._ws_check_interval)
        
        self._ws_timeout_threshold = 600

        try:
            self.logger.info("🔄 正在订阅WebSocket用户数据流...")
            await self.exchange.subscribe_user_data(self._on_order_update)
            if hasattr(self.exchange, 'is_user_stream_connected') and self.exchange.is_user_stream_connected:
                self._ws_monitoring_enabled = True
                self.logger.info("✅ 订单更新流订阅成功 (WebSocket)")
                self.logger.info("📡 使用WebSocket实时监控订单成交")
            else:
                self._ws_monitoring_enabled = False
                self.logger.warning("⚠️ 用户数据流未连接，使用REST轮询模式监控订单成交")
        except Exception as e:
            self.logger.error(f"❌ 订单更新流订阅失败: {e}")
            self.logger.error(f"❌ 错误类型: {type(e).__name__}")
            import traceback
            self.logger.error(f"❌ 错误堆栈:\n{traceback.format_exc()}")
            self.logger.warning("⚠️ WebSocket暂时不可用，启用REST轮询作为临时备用")

        self._start_smart_monitor()
        await self._start_price_monitor()
        self._expected_total_orders = config.grid_count

        self.logger.info(
            f"✅ 执行引擎初始化完成: {config.exchange}/{config.symbol}"
        )

    async def place_order(self, order: GridOrder, batch_mode: bool = False, source: str = "未知") -> GridOrder:
        exchange_id = str(self.config.exchange).lower(
        ) if self.config.exchange else ''
        if exchange_id == 'lighter':
            async with self._lighter_order_lock:
                return await self._place_order_internal(order, batch_mode, source)
        else:
            return await self._place_order_internal(order, batch_mode, source)

    async def _place_order_internal(self, order: GridOrder, batch_mode: bool = False, source: str = "未知") -> GridOrder:
        try:
            await self._apply_order_rate_limit()
            await self._guard_symbol_order_cap_before_single()
            # 修補 B:餘額預檢,沒錢直接 mark_failed 並 return(不浪費 API 配額)
            if not await self._has_sufficient_balance(order):
                order.mark_failed()
                return order
            exchange_side = self._convert_order_side(order.side)
            try:
                max_pos = getattr(self.config, 'max_position', None)
                if max_pos and self.coordinator and self.coordinator.tracker:
                    current_pos = self.coordinator.tracker.get_current_position()
                    projected = current_pos + order.amount if order.side == GridOrderSide.BUY else current_pos - order.amount
                    if abs(projected) > abs(Decimal(str(max_pos))):
                        order.mark_failed()
                        return order
            except Exception:
                pass

            margin_mode_value = 1 if self.config.margin_mode.lower() == "isolated" else 0
            exchange_id = str(self.config.exchange).lower() if self.config and self.config.exchange else ''

            current_px = await self.get_current_price()
            min_tick = Decimal('1') / (Decimal('10') ** int(getattr(self.config, 'price_decimals', 2)))
            target_price = order.price
            # 修補 A:用訂單簿 top 計算 maker 安全價,避免 -5022 race
            ob_top = await self._get_orderbook_top(self.config.symbol, order.side.value)
            best_bid = ob_top[0] if ob_top else None
            best_ask = ob_top[1] if ob_top else None
            if order.side == GridOrderSide.BUY:
                if not (target_price <= current_px - min_tick):
                    target_price = current_px - min_tick
                # 進一步收斂到 best_bid - 1 tick(若可用)
                if best_bid is not None and target_price > best_bid - min_tick:
                    target_price = best_bid - min_tick
            else:
                if not (target_price >= current_px + min_tick):
                    target_price = current_px + min_tick
                # 進一步收斂到 best_ask + 1 tick(若可用)
                if best_ask is not None and target_price < best_ask + min_tick:
                    target_price = best_ask + min_tick

            maker_params = None
            if exchange_id == 'binance':
                maker_params = {'timeInForce': 'GTX', 'postOnly': True}
            else:
                maker_params = {'postOnly': True}

            if exchange_id == 'lighter':
                exchange_order = await self.exchange.create_order(
                    symbol=self.config.symbol,
                    side=exchange_side,
                    order_type=OrderType.LIMIT,
                    amount=order.amount,
                    price=target_price,
                    params={"margin_mode": margin_mode_value, **(maker_params or {})},
                    batch_mode=batch_mode
                )
            else:
                exchange_order = await self.exchange.create_order(
                    symbol=self.config.symbol,
                    side=exchange_side,
                    order_type=OrderType.LIMIT,
                    amount=order.amount,
                    price=target_price,
                    params=maker_params
                )

            if exchange_order is None:
                self.logger.warning(
                    f"⚠️ 下单返回None，1秒后重试: Grid {order.grid_id}, {order.side.value} {order.amount}@{order.price}"
                )
                await asyncio.sleep(1.5)

                if exchange_id == 'lighter':
                    exchange_order = await self.exchange.create_order(
                        symbol=self.config.symbol,
                        side=exchange_side,
                        order_type=OrderType.LIMIT,
                        amount=order.amount,
                        price=target_price,
                        params=maker_params,
                        batch_mode=batch_mode
                    )
                else:
                    exchange_order = await self.exchange.create_order(
                        symbol=self.config.symbol,
                        side=exchange_side,
                        order_type=OrderType.LIMIT,
                        amount=order.amount,
                        price=target_price,
                        params=maker_params
                    )

                if exchange_order is None:
                    error_msg = f"下单失败: 交易所返回None（已重试1次） (Grid {order.grid_id}, {order.side.value} {order.amount}@{order.price})"
                    self.logger.error(error_msg)
                    raise Exception(error_msg)
                else:
                    self.logger.info(f"✅ 重试成功: Grid {order.grid_id}")

            order.order_id = str(getattr(exchange_order, 'id', '') or '')
            order.status = GridOrderStatus.PENDING

            if hasattr(exchange_order, 'client_id') and exchange_order.client_id:
                order.client_id = str(exchange_order.client_id)

            if order.order_id == "pending" or not order.order_id:
                temp_id = f"grid_{order.grid_id}_{int(order.price)}_{int(order.amount*1000000)}"
                order.order_id = temp_id
                self.logger.warning(
                    f"订单ID为临时值，使用组合ID: {temp_id} "
                    f"(Grid {order.grid_id}, {order.side.value} {order.amount}@{order.price})"
                )

            order._placed_time = time.time()
            self._pending_orders[order.order_id] = order

            if order.client_id:
                self._pending_orders_by_client_id[order.client_id] = order
                self.logger.debug(
                    f"📝 订单已缓存: client_id={order.client_id}, "
                    f"price={order.price}, grid={order.grid_id}"
                )

            if source == "反手单":
                prefix = "🔄 [反手]"
            elif source == "健康检查":
                prefix = "🏥 [补单]"
            elif source == "批量初始化":
                prefix = "🎯 [初始]"
            elif source == "止盈单":
                prefix = "💰 [止盈]"
            else:
                prefix = "📝 [下单]"

            self.logger.info(
                f"{prefix} {order.side.value.upper()} {order.amount}@{order.price} "
                f"(Grid {order.grid_id}, OrderID: {order.order_id})"
            )
            try:
                if order.client_id and (order.order_id == "pending" or not order.order_id):
                    self._pending_orders.pop(order.order_id, None)
            except Exception:
                pass
            await self._increment_symbol_open_cache()

            return order

        except Exception as e:
            self.logger.error(f"下单失败: {e}")
            order.mark_failed()
            raise

    async def place_market_order(self, side: GridOrderSide, amount: Decimal) -> None:
        try:
            exchange_side = self._convert_order_side(side)
            self.logger.info(f"📊 下市价单: {side.value} {amount}")
            if getattr(self.config, 'limit_only', False):
                raise Exception("已啟用僅限價模式，禁止市價單")
            exchange_order = await self.exchange.create_order(
                symbol=self.config.symbol,
                side=exchange_side,
                order_type=OrderType.MARKET,
                amount=amount,
                price=None,
                params=None
            )
            if exchange_order is None:
                error_msg = f"市价单失败: 交易所返回None ({side.value} {amount})"
                self.logger.error(error_msg)
                raise Exception(error_msg)
            self.logger.info(
                f"✅ 市价单成功: {side.value} {amount}, "
                f"OrderID: {str(getattr(exchange_order, 'id', ''))}"
            )
        except Exception as e:
            self.logger.error(f"❌ 市价单失败: {e}")
            raise

    async def place_batch_orders(self, orders: List[GridOrder], max_retries: int = 2, current_price: 'Optional[Decimal]' = None) -> List[GridOrder]:
        ex_id = self._get_exchange_id()
        total_orders = len(orders)

        if current_price is None:
            current_price = await self.get_current_price()

        if ex_id == 'binance':
            try:
                if self.config.grid_type == GridType.NEUTRAL and not orders:
                    self.logger.info("中性网格：初始不生成订单，跳过批量下单")
                    return []
                if total_orders <= 0:
                    return []
                allowed = await self._allowed_orders_for_symbol(total_orders)
                if allowed <= 0:
                    self.logger.warning("跳過批量下單：該交易對已達200掛單上限")
                    return []

                window_each_side = int(getattr(self.config, 'active_window_each_side', 0) or 0)
                if window_each_side <= 0:
                    window_each_side = 10
                below = sorted([o for o in orders if o.price <= current_price], key=lambda o: abs(float(o.price - current_price)))
                above = sorted([o for o in orders if o.price > current_price], key=lambda o: abs(float(o.price - current_price)))
                target_total = window_each_side * 2 if window_each_side > 0 else allowed
                target_total = min(target_total, allowed)
                half = target_total // 2
                sel_below = below[:min(len(below), half)]
                sel_above = above[:min(len(above), half)]
                remain = target_total - len(sel_below) - len(sel_above)
                if remain > 0:
                    rest = below[len(sel_below):] + above[len(sel_above):]
                    rest_sorted = sorted(rest, key=lambda o: abs(float(o.price - current_price)))
                    extra = rest_sorted[:remain]
                else:
                    extra = []
                orders = sel_below + sel_above + extra
                total_orders = len(orders)
                self.logger.info(f"📦 Binance窗口選擇: 目標={target_total}, 實際下單={total_orders}")
            except Exception:
                pass
        self.logger.info(f"开始批量下单: {total_orders}个订单")

        batch_size = 50
        successful_orders = []
        failed_orders = []

        for i in range(0, total_orders, batch_size):
            batch = orders[i:i + batch_size]
            batch_num = i // batch_size + 1
            total_batches = (total_orders + batch_size - 1) // batch_size

            self.logger.info(
                f"处理第{batch_num}/{total_batches}批订单 "
                f"({len(batch)}个订单)"
            )

            exchange_id = str(self.config.exchange).lower(
            ) if self.config.exchange else ''
            if exchange_id == 'lighter':
                self.logger.info("🔥 Lighter交易所：使用串行批量下单模式（避免nonce冲突）")
                results = []
                for order in batch:
                    try:
                        result = await self.place_order(order, batch_mode=True, source="批量初始化")
                        results.append(result)
                    except Exception as e:
                        results.append(e)
                        self.logger.error(f"订单下单异常: {e}")
            else:
                if ex_id == 'binance':
                    try:
                        window_each_side = int(getattr(self.config, 'active_window_each_side', 0) or 0)
                        if window_each_side <= 0:
                            window_each_side = 10
                        if window_each_side > 0:
                            def _distance(o):
                                return abs(float(o.price - current_price))
                            below = sorted([o for o in batch if o.price <= current_price], key=_distance)
                            above = sorted([o for o in batch if o.price > current_price], key=_distance)
                            sel_below = below[:min(len(below), window_each_side)]
                            sel_above = above[:min(len(above), window_each_side)]
                            selected = sel_below + sel_above
                            if len(selected) < window_each_side * 2:
                                rest = [o for o in (below[window_each_side:] + above[window_each_side:])]
                                rest_sorted = sorted(rest, key=_distance)
                                need = window_each_side * 2 - len(selected)
                                selected += rest_sorted[:need]
                            batch = selected
                    except Exception:
                        pass
                    now = time.time()
                    if hasattr(self, '_last_batch_place_time') and now - getattr(self, '_last_batch_place_time', 0) < 15:
                        self.logger.warning("⚠️ 批量下單過於頻繁，跳過本次請求避免限頻")
                        return []
                    self._last_batch_place_time = now

                    exchange_open_pairs = await self._get_open_grid_side_pairs_from_exchange(force_refresh=True)
                    orders_to_place = []
                    for order in batch:
                        order_side_str = getattr(order.side, 'value', str(order.side)).lower()
                        if (order.grid_id, order_side_str) not in exchange_open_pairs:
                            orders_to_place.append(order)

                    if not orders_to_place:
                        results = []
                        self.logger.info("🔧 窗口內掛單已充足，跳過本批補單")
                    else:
                        self.logger.info(f"🔧 窗口內需補單: {len(orders_to_place)}/{len(batch)}")
                        results = await self._place_orders_via_batch_api(
                            orders_to_place, current_price
                        )
                else:
                    try:
                        exchange_open_pairs_else = await self._get_open_grid_side_pairs_from_exchange(force_refresh=True)
                        batch_filtered = [
                            o for o in batch 
                            if (o.grid_id, getattr(o.side, 'value', str(o.side)).lower()) not in exchange_open_pairs_else
                        ]
                        if len(batch_filtered) < len(batch):
                            self.logger.info(
                                f"🔍 批量下單去重：過濾掉 {len(batch) - len(batch_filtered)} 個已存在格位/方向的訂單"
                            )
                        batch = batch_filtered
                    except Exception:
                        pass

                    if not batch:
                        results = []
                    else:
                        tasks = [self.place_order(order) for order in batch]
                        results = await asyncio.gather(*tasks, return_exceptions=True)

            batch_success = 0
            for idx, result in enumerate(results):
                if isinstance(result, GridOrder):
                    successful_orders.append(result)
                    batch_success += 1
                else:
                    error_str = str(result)
                    # 修補 C:不可重試的錯誤碼(邏輯性失敗)直接放棄,不丟進重試佇列
                    parsed_code = self._parse_and_set_circuit(error_str)
                    if self._is_non_retryable_code(parsed_code):
                        self.logger.warning(
                            f"🚫 不可重試錯誤 {parsed_code},放棄此訂單: "
                            f"Grid {batch[idx].grid_id}, {batch[idx].side.value} "
                            f"{batch[idx].amount}@{batch[idx].price}, 錯誤: {error_str}"
                        )
                        # 不加進 failed_orders,避免後續重試浪費 API 配額
                        if hasattr(batch[idx], 'mark_failed'):
                            batch[idx].mark_failed()
                        continue
                    failed_orders.append((batch[idx], error_str))
                    self.logger.error(f"订单下单失败: {result}")

            self.logger.info(
                f"第{batch_num}批完成: 成功{batch_success}/{len(batch)}个，"
                f"总进度: {len(successful_orders)}/{total_orders}"
            )

            if i + batch_size < total_orders:
                await asyncio.sleep(0.5)

        if failed_orders and max_retries > 0:
            if self._is_circuit_open():
                self.logger.error(
                    f"⛔ API 熔斷器開啟 (code={self._last_api_error_code})，"
                    f"跳過 {len(failed_orders)} 筆訂單重試，避免加劇 BAN"
                )
                failed_orders = []
            else:
                self.logger.warning(
                    f"⚠️ 检测到{len(failed_orders)}个失败订单，开始重试..."
                )

        if failed_orders and max_retries > 0:
            for retry_attempt in range(1, max_retries + 1):
                if not failed_orders:
                    break

                if self._is_circuit_open():
                    self.logger.error(
                        f"⛔ API 熔斷器開啟 (code={self._last_api_error_code})，"
                        f"停止第 {retry_attempt} 輪重試"
                    )
                    break

                self.logger.info(
                    f"🔄 第{retry_attempt}次重试: {len(failed_orders)}个订单"
                )

                await asyncio.sleep(1.0)

                retry_orders = [order for order, _ in failed_orders]
                failed_orders = []

                exchange_id = str(self.config.exchange).lower(
                ) if self.config.exchange else ''
                if exchange_id == 'lighter':
                    results = []
                    for order in retry_orders:
                        try:
                            result = await self.place_order(order, batch_mode=True, source="批量初始化重试")
                            results.append(result)
                        except Exception as e:
                            results.append(e)
                else:
                    tasks = [self.place_order(order) for order in retry_orders]
                    results = await asyncio.gather(*tasks, return_exceptions=True)

                retry_success = 0
                saw_non_retryable = False
                for idx, result in enumerate(results):
                    if isinstance(result, GridOrder):
                        successful_orders.append(result)
                        retry_success += 1
                    else:
                        error_str = str(result)
                        parsed_code = self._parse_and_set_circuit(error_str)
                        # 修補 C:重試中遇 -5022/-2019 等邏輯性失敗,直接停損,不再重試
                        if self._is_non_retryable_code(parsed_code):
                            saw_non_retryable = True
                            self.logger.warning(
                                f"🚫 重試遇不可重試錯誤 {parsed_code},停止本輪重試: "
                                f"Grid {retry_orders[idx].grid_id}, "
                                f"錯誤: {error_str}"
                            )
                            if hasattr(retry_orders[idx], 'mark_failed'):
                                retry_orders[idx].mark_failed()
                            continue
                        failed_orders.append((retry_orders[idx], error_str))
                        if parsed_code == '-2015':
                            self.logger.critical(
                                f"⛔ 檢測到 -2015（重試中），立即停止重試循環"
                            )
                            break
                # 修補 C:若本輪全部失敗都是不可重試碼,跳出外層重試迴圈
                if saw_non_retryable and not failed_orders:
                    self.logger.info(
                        f"⏹️ 重試遇不可重試錯誤且已無可重試項,結束重試循環"
                    )
                    break

                self.logger.info(
                    f"重试结果: 成功{retry_success}/{len(retry_orders)}个，"
                    f"剩余失败{len(failed_orders)}个"
                )

                if failed_orders and retry_attempt < max_retries:
                    await asyncio.sleep(1.0)

        final_failed_count = len(failed_orders)
        success_rate = (len(successful_orders) / total_orders *
                        100) if total_orders > 0 else 0

        if final_failed_count > 0:
            self.logger.warning(
                f"⚠️ 批量下单完成: 成功{len(successful_orders)}/{total_orders}个 "
                f"({success_rate:.1f}%), 最终失败{final_failed_count}个"
            )
            for order, error in failed_orders:
                self.logger.error(
                    f"订单最终失败: Grid {order.grid_id}, "
                    f"{order.side.value} {order.amount}@{order.price}, "
                    f"错误: {error}"
                )
        else:
            self.logger.info(
                f"✅ 批量下单完成: 成功{len(successful_orders)}/{total_orders}个 "
                f"({success_rate:.1f}%)"
            )

        self.logger.info("🔍 正在同步订单状态，检测立即成交的订单...")
        await asyncio.sleep(3)
        await self._sync_order_status_after_batch()

        return successful_orders

    async def _place_orders_via_batch_api(
        self,
        orders: List[GridOrder],
        current_price: Decimal,
    ) -> List:
        from core.adapters.exchanges.adapters.binance_rest import BinanceRest

        min_tick = Decimal('1') / (Decimal('10') ** int(getattr(self.config, 'price_decimals', 2)))

        batch_order_dicts: List[Dict[str, Any]] = []
        # 修補 A:批量前先一次性取訂單簿 top,所有訂單共用(避免每筆重複 fetch)
        ob_top = await self._get_orderbook_top(self.config.symbol, '_batch_')
        best_bid = ob_top[0] if ob_top else None
        best_ask = ob_top[1] if ob_top else None
        # 修補 B:批量前先一次性 fetch balance(命中 3s 緩存)
        await self._get_cached_balances()
        # 🔥 只把真正送出的訂單放進 active_orders，回應必須按這個清單對位。
        # 舊寫法用 orders[idx]：一旦餘額預檢跳過某張單，後續 order_id 會整批錯位，
        # 把 A 單的交易所 ID 掛到 B 單上（幽靈成交 / 錯誤方向的來源）。
        active_orders: List[GridOrder] = []
        for order in orders:
            # 修補 B:單筆餘額預檢
            if not await self._has_sufficient_balance(order):
                order.mark_failed()
                continue
            target_price = order.price
            if order.side == GridOrderSide.BUY:
                if not (target_price <= current_price - min_tick):
                    target_price = current_price - min_tick
                if best_bid is not None and target_price > best_bid - min_tick:
                    target_price = best_bid - min_tick
            else:
                if not (target_price >= current_price + min_tick):
                    target_price = current_price + min_tick
                if best_ask is not None and target_price < best_ask + min_tick:
                    target_price = best_ask + min_tick

            params = {
                'timeInForce': 'GTX',
                'postOnly': True,
            }
            grid_id = order.grid_id
            if grid_id:
                cid = f"grid_{grid_id}_{int(time.time() * 1000) % 1000000}"
                params['clientOrderId'] = cid

            batch_order_dicts.append({
                'side': ExchangeOrderSide.BUY if order.side == GridOrderSide.BUY else ExchangeOrderSide.SELL,
                'order_type': OrderType.LIMIT,
                'amount': order.amount,
                'price': target_price,
                'params': params,
            })
            active_orders.append(order)

        rest_adapter = None
        adapter = self.exchange
        if hasattr(adapter, 'rest') and isinstance(adapter.rest, BinanceRest):
            rest_adapter = adapter.rest
        elif hasattr(adapter, '_rest') and isinstance(adapter._rest, BinanceRest):
            rest_adapter = adapter._rest
        elif isinstance(adapter, BinanceRest):
            rest_adapter = adapter

        results: List = []
        if rest_adapter is not None:
            symbol = self.config.symbol
            self.logger.info(
                f"📦 [Batch API] 正在透過 batchOrders 下 {len(orders)} 張訂單 "
                f"(~{(len(orders) + 4) // 5} 個 REST 請求)"
            )
            api_results = await rest_adapter.create_batch_orders(symbol, batch_order_dicts)
            for idx, res in enumerate(api_results):
                if idx >= len(active_orders):
                    break
                order = active_orders[idx]
                real_order_id = ""
                real_client_id = ""
                if res is not None:
                    if hasattr(res, 'order_id') and res.order_id:
                        real_order_id = str(res.order_id)
                    elif hasattr(res, 'id') and res.id:
                        real_order_id = str(res.id)
                    if hasattr(res, 'client_id') and res.client_id:
                        real_client_id = str(res.client_id)

                if res is not None and (real_order_id or real_client_id):
                    order.status = GridOrderStatus.PENDING
                    if real_order_id:
                        order.exchange_order_id = real_order_id
                        order.order_id = real_order_id
                    if real_client_id:
                        order.client_id = real_client_id
                        self._pending_orders_by_client_id[order.client_id] = order

                    order._placed_time = time.time()
                    target_key = order.order_id or order.client_id
                    self._pending_orders[target_key] = order

                    await self._increment_symbol_open_cache()
                    results.append(order)
                else:
                    self.logger.error(
                        f"❌ 批量下單單筆無效 (grid_id={order.grid_id}, price={order.price}, side={order.side}): "
                        f"交易所未回傳有效訂單 ID (res={res})"
                    )
                    results.append(Exception(f"批量下單失敗: grid_id={order.grid_id}"))
        else:
            self.logger.warning("⚠️ 無法取得 BinanceRest adapter，退回逐筆下單")
            for order in orders:
                try:
                    result = await self.place_order(order)
                    results.append(result)
                    await asyncio.sleep(0.1)
                except Exception as e:
                    results.append(e)
                    await asyncio.sleep(0.5)

        return results

    # ==================== 修補 1 (A) ====================
    # 訂單簿 top 緩存：用於計算 maker 安全價，避免 -5022 race condition
    # 鍵: (symbol, side)，值: (best_bid, best_ask, fetch_time)
    # 短 TTL (0.5s) 避免重複 fetch，同時容忍 WS 推播延遲
    _ORDERBOOK_TOP_CACHE: Dict[Tuple[str, str], Tuple[Decimal, Decimal, float]] = {}
    _ORDERBOOK_TOP_TTL = 0.5  # 秒

    async def _get_orderbook_top(
        self,
        symbol: str,
        side: str,
    ) -> Optional[Tuple[Decimal, Decimal]]:
        """
        取得訂單簿 top-of-book (best_bid, best_ask)。

        優先使用 WebSocket 緩存(免費,零延遲),失敗時退回 REST(1 次/0.5s)。
        回傳 None 表示無法取得(網路斷、WS 未連線、REST 失敗)。
        """
        key = (symbol, side)
        now = time.time()
        cached = self._ORDERBOOK_TOP_CACHE.get(key)
        if cached and (now - cached[2]) < self._ORDERBOOK_TOP_TTL:
            return (cached[0], cached[1])

        best_bid: Optional[Decimal] = None
        best_ask: Optional[Decimal] = None

        # 1) 優先用 WebSocket 緩存(零 weight)
        try:
            if hasattr(self.exchange, 'get_cached_orderbook'):
                ob = self.exchange.get_cached_orderbook(symbol)
                if ob is not None and ob.best_bid and ob.best_ask:
                    best_bid = Decimal(str(ob.best_bid.price))
                    best_ask = Decimal(str(ob.best_ask.price))
        except Exception:
            pass

        # 2) WS 緩存不可用 → 走 REST(1 次 weight)
        if best_bid is None or best_ask is None:
            try:
                ob = await self.exchange.get_orderbook(symbol, limit=5)
                if ob and ob.best_bid and ob.best_ask:
                    best_bid = Decimal(str(ob.best_bid.price))
                    best_ask = Decimal(str(ob.best_ask.price))
            except Exception as e:
                self.logger.debug(f"取得訂單簿失敗 {symbol}: {e}")
                return None

        if best_bid is None or best_ask is None or best_bid <= 0 or best_ask <= 0:
            return None
        if best_bid >= best_ask:
            # 異常 cross book,跳過
            return None

        self._ORDERBOOK_TOP_CACHE[key] = (best_bid, best_ask, now)
        return (best_bid, best_ask)

    def _calculate_maker_price(
        self,
        order: 'GridOrder',
        current_px: Decimal,
        min_tick: Decimal,
        best_bid: Optional[Decimal] = None,
        best_ask: Optional[Decimal] = None,
    ) -> Decimal:
        """
        計算可保證 Post Only (GTX) 成功的 maker 價格。

        規則:
        - BUY  : price <= best_bid (嚴格小於,GTX 要求)
        - SELL : price >= best_ask (嚴格大於,GTX 要求)
        - 若 orderbook top 不可得,退到 current_px ± min_tick(舊邏輯,並記 warning)
        - 若 order.price 已位於 maker 安全區,保留 order.price
        """
        try:
            if order.side == GridOrderSide.BUY:
                if best_bid is not None and best_bid > 0:
                    # 嚴格低於 best_bid 1 tick,確保在掛單簿上排隊
                    return best_bid - min_tick
                self.logger.debug(
                    f"[maker price] BUY 無 best_bid,退到 current_px - min_tick "
                    f"(Grid {order.grid_id})"
                )
                return current_px - min_tick
            else:  # SELL
                if best_ask is not None and best_ask > 0:
                    return best_ask + min_tick
                self.logger.debug(
                    f"[maker price] SELL 無 best_ask,退到 current_px + min_tick "
                    f"(Grid {order.grid_id})"
                )
                return current_px + min_tick
        except Exception as e:
            self.logger.error(f"_calculate_maker_price 失敗,退到 order.price: {e}")
            return order.price

    # ==================== 修補 2 (B) ====================
    # 餘額預檢:避免在 balance=0 或不足時無腦下單,把所有訂單都丟出去被 -2019/-5022 退件
    # 短 TTL (3s) 避免每筆訂單都 fetch balance(每次 fetch_balance 都消耗 5~20 weight)
    _BALANCE_CACHE: Dict[str, Tuple[Tuple[Optional[Decimal], Optional[Decimal]], float]] = {}
    _BALANCE_CACHE_TTL = 3.0  # 秒

    def _parse_symbol_currencies(self, symbol: str) -> Tuple[str, str, bool]:
        """
        解析 symbol,取得 (base, quote, is_perp)。
        範例:
          'SOL/USDC:USDC' -> ('SOL', 'USDC', True)
          'ETH/USDT'      -> ('ETH', 'USDT', False)
          'BTC/USDC'      -> ('BTC', 'USDC', False)
        """
        try:
            ex_type = getattr(self.exchange.config, 'exchange_type', None) if hasattr(self.exchange, 'config') else None
            is_perp = (':' in symbol) or (ex_type in [ExchangeType.FUTURES, ExchangeType.PERPETUAL]) or (self.config and int(getattr(self.config, 'leverage', 1) or 1) > 1)
            main = symbol.split(':')[0] if ':' in symbol else symbol
            parts = main.split('/')
            if len(parts) == 2:
                return parts[0], parts[1], is_perp
            return '', '', is_perp
        except Exception:
            return '', '', False

    async def _get_cached_balances(
        self,
    ) -> Optional[Tuple[Optional[Decimal], Optional[Decimal], Decimal]]:
        """
        取得 (free_quote, free_base, total_stable_collateral) 餘額,3 秒緩存。

        free_quote: 報價幣(USDC/USDT)可用餘額,用於保證金與現貨買單
        free_base:  基礎幣(SOL/ETH)可用餘額,用於現貨賣單
        total_stable_collateral: 帳戶內所有主要穩定幣(USDT/USDC/USD)總可用餘額(支援多資產保證金模式)

        回傳 None 表示 fetch 失敗(此時應視為「無法判斷」,由上層決定是否放行)
        """
        key = self.config.symbol
        now = time.time()
        cached = self._BALANCE_CACHE.get(key)
        if cached and (now - cached[1]) < self._BALANCE_CACHE_TTL:
            return cached[0]

        try:
            balances = await self.exchange.get_balances()
        except Exception as e:
            self.logger.warning(f"⚠️ 取得餘額失敗(放行預檢): {e}")
            return None

        _, quote, _ = self._parse_symbol_currencies(self.config.symbol)
        # 對永續合約,基礎幣可能是 USDⓈ-M 的結算幣(通常用 quote 即可)
        # 對現貨,需要同時知道 base 與 quote
        base, _, is_perp = self._parse_symbol_currencies(self.config.symbol)

        free_quote: Optional[Decimal] = None
        free_base: Optional[Decimal] = None
        total_stable_collateral: Decimal = Decimal('0')

        for b in balances:
            cur = (b.currency or '').upper()
            if cur == quote.upper() and free_quote is None:
                free_quote = b.free
            if cur == base.upper() and free_base is None:
                free_base = b.free
            # 同時記 USDⓈ 形式(SOLUSDC 永續有時以 USDⓈ 為單位)
            if cur in ('USDⓈ', 'USDⓈ-M') and free_quote is None:
                free_quote = b.free
            if cur in ('USDT', 'USDC', 'USD', 'BUSD', 'FDUSD') and b.free:
                total_stable_collateral += b.free

        result = (free_quote, free_base, total_stable_collateral)
        self._BALANCE_CACHE[key] = (result, now)
        return result

    async def _has_sufficient_balance(self, order: 'GridOrder') -> bool:
        """
        檢查餘額是否足夠支撐此訂單。

        規則:
        - 永續合約(BUY/SELL):需 free_quote >= amount * price / leverage，或 total_stable_collateral 足夠（Multi-Assets）
        - 現貨 BUY            :需 free_quote >= amount * price
        - 現貨 SELL           :需 free_base >= amount

        緩存 3s 避免重複查詢;若 fetch 失敗,放行(不阻斷下單)
        """
        try:
            price = Decimal(str(order.price))
            amount = Decimal(str(order.amount))
            if amount <= 0 or price <= 0:
                return True  # 異常值不擋,讓下單流程自然報錯

            balances = await self._get_cached_balances()
            if balances is None:
                # 無法取得餘額 → 放行,避免把整個網格卡死
                return True
            free_quote, free_base, total_stable_collateral = balances

            base, quote, is_perp = self._parse_symbol_currencies(self.config.symbol)

            if is_perp:
                # 永續合約:不論 BUY/SELL 都需要保證金 = notional / leverage
                leverage = max(1, int(getattr(self.config, 'leverage', 1) or 1))
                required = (amount * price) / Decimal(leverage)
                if free_quote is not None and free_quote >= required:
                    return True
                # 🔥 支援多資產保證金（如在 Binance U本位使用 USDT 作為 USDC 合約保證金）
                if total_stable_collateral >= required:
                    return True
                if free_quote is None:
                    return True
                self.logger.warning(
                    f"💸 餘額不足跳過下單: Grid {order.grid_id}, "
                    f"{order.side.value} {amount}@{price}, "
                    f"需保證金 {required:.4f} {quote}, "
                    f"可用 {free_quote:.4f} {quote} (總可用穩定幣: {total_stable_collateral:.4f})"
                )
                return False
            else:
                # 現貨
                if order.side == GridOrderSide.BUY:
                    required = amount * price
                    if free_quote is None or free_quote < required:
                        self.logger.warning(
                            f"💸 現貨買單餘額不足跳過: Grid {order.grid_id}, "
                            f"BUY {amount}@{price}, 需 {required:.4f} {quote}, "
                            f"可用 {free_quote}"
                        )
                        return False
                    return True
                else:  # SELL
                    if free_base is None or free_base < amount:
                        self.logger.warning(
                            f"💸 現貨賣單餘額不足跳過: Grid {order.grid_id}, "
                            f"SELL {amount}@{price}, 需 {amount} {base}, "
                            f"可用 {free_base}"
                        )
                        return False
                    return True
        except Exception as e:
            self.logger.debug(f"_has_sufficient_balance 例外(放行): {e}")
            return True

    async def _apply_order_rate_limit(self) -> None:
        try:
            if self._order_rate_window_seconds <= 0 or self._order_rate_max_orders <= 0:
                return
            async with self._order_rate_lock:
                now = time.time()
                w = self._order_rate_window_seconds
                m = self._order_rate_max_orders
                while self._order_rate_timestamps and now - self._order_rate_timestamps[0] > w:
                    self._order_rate_timestamps.popleft()
                if len(self._order_rate_timestamps) >= m:
                    sleep_time = w - (now - self._order_rate_timestamps[0]) + 0.05
                    await asyncio.sleep(sleep_time)
                    now = time.time()
                    while self._order_rate_timestamps and now - self._order_rate_timestamps[0] > w:
                        self._order_rate_timestamps.popleft()
                self._order_rate_timestamps.append(time.time())
        except Exception:
            return

    async def _allowed_orders_for_symbol(self, planned: int) -> int:
        try:
            ex_id = self._get_exchange_id()
            if ex_id != 'binance':
                return planned
            if self._is_circuit_open():
                return planned
            existing = await self.exchange.get_open_orders(self.config.symbol)
            cap = 200
            rem = cap - len(existing)
            if rem < 0:
                rem = 0
            return min(planned, rem)
        except Exception:
            return planned

    async def _guard_symbol_order_cap_before_single(self) -> None:
        try:
            ex_id = self._get_exchange_id()
            if ex_id != 'binance':
                return
            if self._is_circuit_open():
                return
            async with self._symbol_open_orders_cache_lock:
                key = self.config.symbol
                now = time.time()
                ttl = 1.0
                cached = self._symbol_open_orders_cache.get(key)
                if cached and now - cached[1] < ttl:
                    count = cached[0]
                else:
                    existing = await self.exchange.get_open_orders(self.config.symbol)
                    count = len(existing)
                    self._symbol_open_orders_cache[key] = (count, now)
                if count >= 200:
                    raise Exception("Binance 每交易對最大掛單數量限制為200")
        except Exception as e:
            if str(e).startswith("Binance"):
                raise
            return

    async def _increment_symbol_open_cache(self) -> None:
        try:
            ex_id = self._get_exchange_id()
            if ex_id != 'binance':
                return
            async with self._symbol_open_orders_cache_lock:
                key = self.config.symbol
                cached = self._symbol_open_orders_cache.get(key)
                if cached:
                    self._symbol_open_orders_cache[key] = (cached[0] + 1, time.time())
        except Exception:
            return

    def _remove_order_from_pending(self, order_id: str) -> int:
        order_obj = self._pending_orders.get(order_id) or self._pending_orders_by_client_id.get(order_id)
        if not order_obj:
            if order_id in self._pending_orders:
                del self._pending_orders[order_id]
                return 1
            if order_id in self._pending_orders_by_client_id:
                del self._pending_orders_by_client_id[order_id]
                return 1
            return 0
        order_obj_id = id(order_obj)
        keys_to_remove = [
            key for key, order in self._pending_orders.items()
            if id(order) == order_obj_id
        ]
        for key in keys_to_remove:
            del self._pending_orders[key]
        client_keys_to_remove = [
            key for key, order in self._pending_orders_by_client_id.items()
            if id(order) == order_obj_id
        ]
        for key in client_keys_to_remove:
            del self._pending_orders_by_client_id[key]
        return max(len(keys_to_remove), len(client_keys_to_remove), 1)

    async def cancel_order(self, order_id: str) -> bool:
        try:
            if not order_id or order_id == "pending":
                self.logger.warning(f"⚠️ 跳过取消无效订单ID: {order_id}")
                return False
            self._expected_cancellations.add(order_id)
            try:
                await self.exchange.cancel_order(order_id, self.config.symbol)
            except Exception as e:
                error_str = str(e)
                if "Unknown order sent" in error_str or "-2011" in error_str or "Order does not exist" in error_str:
                    self.logger.warning(f"⚠️ 订单 {order_id} 已不存在 (Binance -2011)，视为取消成功")
                else:
                    self._remove_order_from_pending(order_id)
                    raise e
            order_obj = self._pending_orders.get(order_id) or self._pending_orders_by_client_id.get(order_id)
            if order_obj:
                order_obj.mark_cancelled()
            self._remove_order_from_pending(order_id)
            self.logger.info(f"✅ 主动取消订单成功: {order_id}")
            return True
        except Exception as e:
            self.logger.error(f"取消订单失败 {order_id}: {e}")
            self._remove_order_from_pending(order_id)
            return False

    async def cancel_all_orders(self) -> int:
        try:
            if self.config is None:
                self.logger.debug("引擎未初始化，跳过取消订单")
                return 0
            pending_order_ids = list(self._pending_orders.keys())
            for order_id in pending_order_ids:
                self._expected_cancellations.add(order_id)
            cancelled_orders = await self.exchange.cancel_all_orders(self.config.symbol)
            count = len(cancelled_orders)
            for order_id in pending_order_ids:
                if order_id in self._pending_orders:
                    order = self._pending_orders[order_id]
                    order.mark_cancelled()
                    del self._pending_orders[order_id]
            self.logger.info(f"✅ 主动批量取消所有订单: {count}个")
            return count
        except Exception as e:
            self.logger.error(f"取消所有订单失败: {e}")
            return 0

    async def get_order_status(self, order_id: str) -> Optional[GridOrder]:
        try:
            exchange_order = await self.exchange.get_order(order_id, self.config.symbol)
            if order_id in self._pending_orders:
                grid_order = self._pending_orders[order_id]
                if exchange_order.status.value == "filled":
                    grid_order.mark_filled(
                        filled_price=exchange_order.price,
                        filled_amount=exchange_order.filled
                    )
                return grid_order
            return None
        except Exception as e:
            self.logger.error(f"查询订单状态失败 {order_id}: {e}")
            return None

    async def get_current_price(self) -> Decimal:
        current_time = time.time()
        try:
            if self._current_price is not None:
                price_age = current_time - self._last_price_update_time
                ws_healthy = (current_time - getattr(self, '_last_ws_message_time', 0)) < 60
                if price_age < 5 or (ws_healthy and getattr(self.config, 'ws_only_mode', False)):
                    await self._handle_price_success()
                    return self._current_price

            async with self._price_fetch_lock:
                current_time = time.time()
                if self._current_price is not None:
                    price_age = current_time - self._last_price_update_time
                    ws_healthy = (current_time - getattr(self, '_last_ws_message_time', 0)) < 60
                    if price_age < 5 or (ws_healthy and getattr(self.config, 'ws_only_mode', False)):
                        await self._handle_price_success()
                        return self._current_price

                if hasattr(self, '_last_rest_attempt_time'):
                    time_since_attempt = current_time - self._last_rest_attempt_time
                    if time_since_attempt < 2:
                        if self._current_price is not None:
                            self.logger.debug("REST API频率限制，返回缓存价格")
                            return self._current_price
                        else:
                            self.logger.warning("REST API频率限制且无缓存，等待...")
                            await asyncio.sleep(2 - time_since_attempt)
                            current_time = time.time()
                
                self._last_rest_attempt_time = current_time

                if getattr(self.config, 'ws_only_mode', False) and self._current_price is not None:
                    try:
                        if self._price_ws_enabled and hasattr(self.exchange, 'get_cached_ticker'):
                            cached = self.exchange.get_cached_ticker(self.config.symbol)
                            if cached:
                                if cached.last is not None:
                                    price = cached.last
                                elif cached.bid is not None and cached.ask is not None:
                                    price = (cached.bid + cached.ask) / Decimal('2')
                                elif cached.bid is not None:
                                    price = cached.bid
                                elif cached.ask is not None:
                                    price = cached.ask
                                else:
                                    price = None
                                if price is not None:
                                    self._current_price = price
                                    self._last_price_update_time = current_time
                                    await self._handle_price_success()
                                    return price
                    except Exception:
                        pass
                    if self._current_price is not None:
                        ws_healthy_now = (current_time - getattr(self, '_last_ws_message_time', 0)) < 60
                        if ws_healthy_now:
                            self.logger.debug(
                                f"WS-only 模式：使用緩存價格（WS 健康，報價超過5秒未更新）"
                            )
                            await self._handle_price_success()
                            return self._current_price
                        else:
                            self.logger.info(
                                f"ℹ️ WS-only 模式：WS 超過60秒無訊息，降級向 REST 獲取最新報價"
                            )
                            # 不直接 return 過期價格，向下執行 REST get_ticker 兜底

                ticker_res = self.exchange.get_ticker(self.config.symbol)
                if inspect.isawaitable(ticker_res):
                    ticker = await ticker_res
                else:
                    ticker = ticker_res

                if ticker.last is not None:
                    price = ticker.last
                elif ticker.bid is not None and ticker.ask is not None:
                    price = (ticker.bid + ticker.ask) / Decimal('2')
                elif ticker.bid is not None:
                    price = ticker.bid
                elif ticker.ask is not None:
                    price = ticker.ask
                else:
                    raise ValueError("Ticker数据不包含有效价格信息")

                self._current_price = price
                self._last_price_update_time = current_time
                await self._handle_price_success()
                return price

        except Exception as e:
            self.logger.error(f"获取当前价格失败: {e}")
            await self._handle_price_failure()
            if self._current_price is not None:
                price_age = current_time - self._last_price_update_time
                self.logger.warning(
                    f"使用缓存价格（{price_age:.0f}秒前）")
                return self._current_price
            raise

    async def _handle_price_success(self):
        current_time = time.time()
        if (self._last_price_failure_time > 0 and
                current_time - self._last_price_failure_time > self._price_failure_reset_timeout):
            if self._price_failures > 0:
                self.logger.info(
                    f"⏰ 距离上次价格获取失败已超过{self._price_failure_reset_timeout}秒，"
                    f"重置价格失败计数器（之前失败{self._price_failures}次）"
                )
                self._price_failures = 0
                self._price_successes = 0

        if self._price_failures > 0:
            self._price_successes += 1
            if self._price_successes >= self._price_success_required_to_reset:
                self.logger.info(
                    f"✅ 价格获取网络恢复，重置失败计数器"
                    f"（之前失败{self._price_failures}次，"
                    f"连续成功{self._price_successes}次）"
                )
                self._price_failures = 0
                self._price_successes = 0
                self._last_price_failure_time = 0
                if self.coordinator and self.coordinator._paused and self.coordinator._paused_reason == 'network':
                    self.logger.info(
                        "✅ 价格获取已恢复，正在自动恢复系统..."
                    )
                    await self.coordinator.resume(auto=True)
            else:
                self.logger.debug(
                    f"⚠️ 价格获取部分恢复，连续成功{self._price_successes}/"
                    f"{self._price_success_required_to_reset}次"
                    f"（需要更多成功才能重置失败计数器）"
                )

    async def _handle_price_failure(self):
        current_time = time.time()
        if (self._last_price_failure_time > 0 and
                current_time - self._last_price_failure_time > self._price_failure_reset_timeout):
            if self._price_failures > 0:
                self.logger.info(
                    f"⏰ 距离上次价格获取失败已超过{self._price_failure_reset_timeout}秒，"
                    f"重置价格失败计数器（之前失败{self._price_failures}次）"
                )
                self._price_failures = 0

        self._price_failures += 1
        self._price_successes = 0
        self._last_price_failure_time = current_time

        if self._price_failures >= self._price_max_failures:
            self.logger.error(
                f"🚨 价格获取网络故障！连续失败{self._price_failures}次 "
                f"(阈值={self._price_max_failures}次)"
            )
            if self.coordinator and not self.coordinator._paused:
                self.logger.error(
                    f"⏸️ 因价格获取网络故障暂停系统（失败{self._price_failures}次）"
                )
                await self.coordinator.pause(reason='network')
        else:
            self.logger.warning(
                f"⚠️ 价格获取失败 [{self._price_failures}/{self._price_max_failures}]"
            )

    def get_pending_orders(self) -> List[GridOrder]:
        seen_objects = set()
        unique_orders = []
        for order in self._pending_orders.values():
            order_obj_id = id(order)
            if order_obj_id not in seen_objects:
                seen_objects.add(order_obj_id)
                unique_orders.append(order)
        return unique_orders

    async def _get_open_grid_side_pairs_from_exchange(self, force_refresh: bool = False) -> Set[Tuple[int, str]]:
        """取得交易所當前開放訂單的 (grid_id, side) 集合（輕量、帶容差）"""
        try:
            if self._is_circuit_open():
                cached = getattr(self, '_cached_open_grid_side_pairs', set())
                return cached if cached else set()

            now = time.time()
            if not force_refresh and hasattr(self, '_last_open_orders_query_time'):
                if now - getattr(self, '_last_open_orders_query_time') < 5:
                    cached = getattr(self, '_cached_open_grid_side_pairs', set())
                    if cached:
                        return cached
            self._last_open_orders_query_time = now

            open_orders = await self.exchange.get_open_orders(self.config.symbol)
            grid_side_pairs: Set[Tuple[int, str]] = set()
            min_tick = Decimal('1') / (Decimal('10') ** int(getattr(self.config, 'price_decimals', 2)))
            for ex_order in open_orders:
                try:
                    px = Decimal(str(ex_order.price)) if ex_order.price is not None else None
                    if not px:
                        continue
                    side_str = getattr(ex_order.side, 'value', str(ex_order.side)).lower()
                    for delta in range(-2, 3):
                        gid_d = self.config.get_grid_index_by_price(px + delta * min_tick)
                        grid_side_pairs.add((int(gid_d), side_str))
                except Exception:
                    continue
            self._cached_open_grid_side_pairs = grid_side_pairs
            return grid_side_pairs
        except Exception:
            try:
                local = {(int(o.grid_id), getattr(o.side, 'value', str(o.side)).lower()) for o in self.get_pending_orders() if o.grid_id}
                return local
            except Exception:
                return set()

    async def _get_open_grid_ids_from_exchange(self, force_refresh: bool = False) -> Set[int]:
        """取得交易所當前開放訂單的 grid_id 集合（輕量、帶容差）"""
        pairs = await self._get_open_grid_side_pairs_from_exchange(force_refresh=force_refresh)
        return {gid for gid, _ in pairs}

    async def sync_with_exchange(self):
        try:
            if self._is_circuit_open():
                self.logger.warning(
                    f"⛔ 深度同步：API 熔斷中 (code={self._last_api_error_code})，本次跳過"
                )
                return
            self.logger.info("🔄 執行深度同步：強制拉取交易所掛單狀態...")
            open_orders = await self.exchange.get_open_orders(self.config.symbol)
            await self._sync_orders_from_exchange(open_orders)
            try:
                grid_ids: Set[int] = set()
                min_tick = Decimal('1') / (Decimal('10') ** int(getattr(self.config, 'price_decimals', 2)))
                for ex_order in open_orders:
                    try:
                        px = Decimal(str(ex_order.price)) if ex_order.price is not None else None
                        if not px: continue
                        gid = self.config.get_grid_index_by_price(px)
                        grid_ids.add(int(gid))
                        gid_minus = self.config.get_grid_index_by_price(px - min_tick)
                        gid_plus = self.config.get_grid_index_by_price(px + min_tick)
                        grid_ids.add(int(gid_minus))
                        grid_ids.add(int(gid_plus))
                    except Exception:
                        continue
                self._cached_open_grid_ids = grid_ids
                self._last_open_orders_query_time = time.time()
            except Exception:
                pass
        except Exception as e:
            self.logger.error(f"深度同步失敗: {e}")

    def clear_all_caches(self):
        cleared_count = len(self._pending_orders)
        cleared_client_count = len(self._pending_orders_by_client_id)
        self._pending_orders.clear()
        self._pending_orders_by_client_id.clear()
        self.logger.info(
            f"🧹 订单缓存已清空: "
            f"order_id缓存={cleared_count}个, "
            f"client_id缓存={cleared_client_count}个"
        )

    def subscribe_order_updates(self, callback: Callable):
        self._order_callbacks.append(callback)
        self.logger.debug(f"添加订单更新回调: {callback}")

    def get_monitoring_mode(self) -> str:
        if self._ws_monitoring_enabled:
            return "WebSocket"
        else:
            return "REST轮询"

    async def get_real_time_position(self, symbol: str) -> Dict[str, Decimal]:
        try:
            if hasattr(self.exchange, '_position_cache'):
                cached_position = self.exchange._position_cache.get(symbol)
                if cached_position:
                    cache_age = (datetime.now() -
                                 cached_position['timestamp']).total_seconds()
                    self.logger.debug(
                        f"📊 使用WebSocket持仓缓存: {symbol} "
                        f"数量={cached_position['size']}, "
                        f"成本=${cached_position['entry_price']}, "
                        f"缓存年龄={cache_age:.1f}秒"
                    )
                    return {
                        'size': cached_position['size'],
                        'entry_price': cached_position['entry_price'],
                        'unrealized_pnl': cached_position['unrealized_pnl'],
                        'has_cache': True
                    }
            current_time = time.time()
            if current_time - self._last_position_warning_time >= self._position_warning_interval:
                self.logger.debug(
                    f"📊 WebSocket持仓缓存暂无数据: {symbol} "
                    f"(使用PositionTracker数据)"
                )
                self._last_position_warning_time = current_time
            return {
                'size': Decimal('0'),
                'entry_price': Decimal('0'),
                'unrealized_pnl': Decimal('0'),
                'has_cache': False
            }
        except Exception as e:
            self.logger.error(f"获取WebSocket持仓缓存失败: {e}")
            return {
                'size': Decimal('0'),
                'entry_price': Decimal('0'),
                'unrealized_pnl': Decimal('0'),
                'has_cache': False
            }

    def _start_smart_monitor(self):
        if self._polling_task is None or self._polling_task.done():
            self._polling_task = asyncio.create_task(
                self._smart_monitor_loop())
            if self._ws_monitoring_enabled:
                self.logger.info("✅ 智能监控已启动：WebSocket (主)")
            else:
                self.logger.info("✅ 智能监控已启动：REST轮询 (临时备用)")

    async def _smart_monitor_loop(self):
        self.logger.info("📡 智能监控循环已启动")

        while True:
            try:
                if self._ws_monitoring_enabled:
                    self._current_poll_interval = self._min_poll_interval
                    await asyncio.sleep(self._ws_check_interval)

                    current_time = time.time()
                    time_since_last_message = current_time - self._last_ws_message_time

                    ws_connected = True
                    if hasattr(self.exchange, '_ws_connected'):
                        ws_connected = self.exchange._ws_connected

                    if not ws_connected:
                        self.logger.error("❌ WebSocket连接断开，切换到REST轮询模式")
                        self.logger.info(
                            f"📊 最后收到消息时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self._last_ws_message_time))}")
                        self.logger.info(
                            f"📊 当前挂单数量: {len(self.get_pending_orders())}")
                        self._ws_monitoring_enabled = False
                        self._last_ws_check_time = current_time
                        continue

                    exchange_name = self.config.exchange.lower() if hasattr(
                        self.config, 'exchange') else 'unknown'

                    if exchange_name == 'lighter':
                        self.logger.info(
                            f"💓 WebSocket健康: 连接正常, "
                            f"消息 {time_since_last_message:.0f}秒前"
                        )
                        if time_since_last_message > 600:
                            self.logger.info(
                                f"💡 提示: {time_since_last_message:.0f}秒未收到订单更新 "
                                f"(无订单成交时的正常现象)"
                            )
                    else:
                        heartbeat_age = 0
                        if hasattr(self.exchange, '_last_heartbeat'):
                            last_heartbeat = self.exchange._last_heartbeat
                            if isinstance(last_heartbeat, datetime):
                                last_heartbeat = last_heartbeat.timestamp()
                            heartbeat_age = current_time - last_heartbeat

                            if heartbeat_age > self._ws_timeout_threshold:
                                self.logger.error(
                                    f"❌ WebSocket心跳超时（{heartbeat_age:.0f}秒未更新），"
                                    f"切换到REST轮询模式"
                                )
                                self.logger.info(
                                    f"📊 最后心跳时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(last_heartbeat))}")
                                self.logger.info(
                                    f"📊 最后消息时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self._last_ws_message_time))}")
                                self.logger.info(
                                    f"📊 当前挂单数量: {len(self.get_pending_orders())}")
                                self._ws_monitoring_enabled = False
                                self._last_ws_check_time = current_time
                                continue

                        self.logger.info(
                            f"💓 WebSocket健康: 连接正常, 心跳 {heartbeat_age:.0f}秒前, "
                            f"消息 {time_since_last_message:.0f}秒前"
                        )

                if getattr(self.config, 'ws_only_mode', False):
                    ws_silence_max = int(getattr(self.config, 'ws_silence_max_seconds', 180))
                    if (self._pending_orders
                            and time_since_last_message > ws_silence_max):
                        self.logger.warning(
                            f"⚠️ WS應用層死寂：{time_since_last_message:.0f}秒無消息"
                            f"（閾值{ws_silence_max}秒），有{len(self._pending_orders)}筆掛單，"
                            f"觸發單次REST兜底檢查"
                        )
                        filled_by_rest = 0
                        try:
                            filled_by_rest = int(await self._check_pending_orders() or 0)
                        except Exception as e:
                            self.logger.warning(f"死寂兜底REST檢查失敗: {e}")
                        if filled_by_rest > 0:
                            # REST 已證明 WS 漏推送（不是「沒有成交所以安靜」）→ 重建用戶數據流
                            reconnect = getattr(self.exchange, "force_user_stream_reconnect", None)
                            self.logger.error(
                                f"🧟 REST 偵測到 {filled_by_rest} 筆 WS 未推送的成交，"
                                f"判定用戶數據流為殭屍連線"
                            )
                            if callable(reconnect):
                                try:
                                    await reconnect("rest_detected_missed_fills")
                                    self._last_ws_message_time = time.time()
                                except Exception as e:
                                    self.logger.warning(f"強制重建用戶數據流失敗: {e}")
                else:
                    try:
                        now = time.time()
                        last_sync = getattr(self, '_last_sync_after_batch_time', 0)
                        if self._pending_orders and time_since_last_message > 15 and (now - last_sync > 15):
                            self.logger.info(
                                f"🔄 WebSocket {time_since_last_message:.0f}秒無消息，啟用一次REST輪詢檢查掛單成交"
                            )
                            await self._check_pending_orders()
                    except Exception as e:
                        self.logger.warning(f"REST備援輪詢失敗: {e}")

                    continue

                pending_orders = self.get_pending_orders()
                if not pending_orders:
                    await asyncio.sleep(self._max_poll_interval)
                else:
                    await asyncio.sleep(self._current_poll_interval)

                if not getattr(self.config, 'ws_only_mode', False):
                    if pending_orders:
                        filled_count = await self._check_pending_orders()
                        if filled_count > 0:
                            self._current_poll_interval = self._min_poll_interval
                        else:
                            self._current_poll_interval = min(
                                self._current_poll_interval * 1.5, 
                                self._max_poll_interval
                            )
                    else:
                        self._current_poll_interval = self._min_poll_interval

                current_time = time.time()
                if current_time - self._last_ws_check_time >= self._ws_check_interval:
                    self._last_ws_check_time = current_time
                    await self._try_restore_websocket()

            except asyncio.CancelledError:
                self.logger.info("智能监控已停止")
                break
            except Exception as e:
                self.logger.error(f"智能监控出错: {e}")
                await asyncio.sleep(5)

    async def _try_restore_websocket(self):
        if self._ws_monitoring_enabled:
            return
        try:
            self.logger.info("🔄 尝试恢复WebSocket监控...")
            await self.exchange.subscribe_user_data(self._on_order_update)
            self._ws_monitoring_enabled = True
            self._last_ws_message_time = time.time()
            self.logger.info("✅ WebSocket监控已恢复！切换回WebSocket模式")
            self.logger.info("📡 使用WebSocket实时监控订单成交")
        except Exception as e:
            self.logger.warning(f"⚠️ WebSocket恢复失败: {type(e).__name__}: {e}")
            self.logger.debug(f"详细错误: {e}，继续使用REST轮询")
            import traceback
            self.logger.debug(f"错误堆栈:\n{traceback.format_exc()}")

    async def _sync_order_status_after_batch(self):
        try:
            if not self._pending_orders:
                self.logger.debug("没有挂单需要同步")
                return

            if getattr(self.config, 'ws_only_mode', False):
                self.logger.info("WS-only 模式：跳過批量後的REST同步，等待WebSocket處理成交")
                return

            now = time.time()
            if hasattr(self, '_last_sync_after_batch_time') and now - self._last_sync_after_batch_time < 30:
                self.logger.info("跳過批量後的REST同步，避免請求過於頻繁")
                return
            if self._is_circuit_open():
                self.logger.warning(
                    f"⛔ 批量後REST同步：API 熔斷中 (code={self._last_api_error_code})，本次跳過"
                )
                return
            self._last_sync_after_batch_time = now

            open_orders = await self.exchange.get_open_orders(self.config.symbol)

            if not open_orders:
                self.logger.warning("⚠️ 未获取到任何挂单，等待 WebSocket 推送处理成交")
                return

            open_order_ids = {order.id for order in open_orders if order.id}
            open_client_ids = {
                order.client_id for order in open_orders if order.client_id}

            self.logger.debug(f"🔍 挂单 order_index 集合: {len(open_order_ids)}个")
            self.logger.debug(f"🔍 挂单 client_id 集合: {len(open_client_ids)}个")

            mapped_count = 0
            pending_order_ids = list(self._pending_orders.keys())

            for order_id in pending_order_ids:
                if order_id in open_client_ids:
                    for ex_order in open_orders:
                        if ex_order.client_id == order_id:
                            order_index = ex_order.id
                            if order_index and order_index not in self._pending_orders:
                                grid_order = self._pending_orders[order_id]
                                self._pending_orders[order_index] = grid_order
                                mapped_count += 1
                                self.logger.debug(
                                    f"✅ 映射订单ID: client_id={order_id} → "
                                    f"order_index={order_index} "
                                    f"(Grid {grid_order.grid_id})"
                                )
                            break

            pending_count = len(self.get_pending_orders())
            self.logger.info(
                f"✅ 同步完成: 建立 {mapped_count} 个ID映射，"
                f"当前挂单 {pending_count} 个（等待 WebSocket 推送处理成交）"
            )

        except Exception as e:
            self.logger.error(f"同步订单状态失败: {e}")
            import traceback
            self.logger.error(traceback.format_exc())

    async def _check_pending_orders(self) -> int:
        try:
            if self._is_circuit_open():
                self.logger.debug(
                    f"⛔ _check_pending_orders：API 熔斷中 (code={self._last_api_error_code})，本輪跳過"
                )
                return 0
            open_orders = await self.exchange.get_open_orders(self.config.symbol)
            open_order_ids = {order.id for order in open_orders if order.id}
            open_client_ids = {order.client_id for order in open_orders if order.client_id}
            filled_orders = []

            for order_id, grid_order in list(self._pending_orders.items()):
                # 1. 仍在交易所 open_orders 中：跳過
                if order_id in open_order_ids or (grid_order.client_id and grid_order.client_id in open_client_ids):
                    continue

                # 2. 屬於預期主動撤單（如窗口刷新、手動撤單）：清理並移出 pending
                is_expected = (
                    order_id in self._expected_cancellations
                    or (grid_order.client_id and grid_order.client_id in self._expected_cancellations)
                )
                if is_expected:
                    self.logger.info(f"ℹ️ 主動撤單確認已自掛單中消失: {order_id}，移出 pending")
                    self._expected_cancellations.discard(order_id)
                    if grid_order.client_id:
                        self._expected_cancellations.discard(grid_order.client_id)
                    grid_order.mark_cancelled()
                    self._remove_order_from_pending(order_id)
                    continue

                # 3. 既不在 open_orders，亦非已知主動撤單：調用 get_order 反查真實狀態，絕不武斷推定成交！
                try:
                    ex_order = await self.exchange.get_order(order_id, self.config.symbol)
                except Exception as query_err:
                    err_msg = str(query_err).lower()
                    if "-2013" in err_msg or "order does not exist" in err_msg or "ordernotfound" in type(query_err).__name__.lower():
                        self.logger.info(
                            f"ℹ️ 交易所確認訂單 {order_id} 不存在（-2013 / 已撤單或已過期），移出 pending（不計入成交）"
                        )
                        grid_order.mark_cancelled()
                        self._remove_order_from_pending(order_id)
                        continue
                    self.logger.warning(
                        f"⚠️ 反查訂單 {order_id} 狀態失敗（保留 pending 待下輪重試）: {query_err}"
                    )
                    continue

                if ex_order is None:
                    self.logger.info(
                        f"ℹ️ 交易所無訂單 {order_id} 記錄，移出 pending（不計入成交）"
                    )
                    grid_order.mark_cancelled()
                    self._remove_order_from_pending(order_id)
                    continue

                status_val = (
                    getattr(ex_order.status, "value", str(ex_order.status))
                    if ex_order.status else ""
                ).lower()

                if status_val in ("filled", "closed"):
                    filled_price = Decimal(str(ex_order.average or ex_order.price or grid_order.price))
                    filled_amount = Decimal(str(ex_order.filled if (ex_order.filled and ex_order.filled > 0) else grid_order.amount))
                    grid_order.filled_price = filled_price
                    grid_order.filled_amount = filled_amount
                    filled_orders.append((order_id, grid_order))
                elif status_val in ("canceled", "cancelled", "rejected", "expired"):
                    self.logger.info(
                        f"ℹ️ REST輪詢確認訂單 {order_id} 狀態為 {status_val.upper()}，移出 pending（不計入成交）"
                    )
                    grid_order.mark_cancelled()
                    self._remove_order_from_pending(order_id)
                elif status_val in ("open", "pending"):
                    self.logger.debug(f"訂單 {order_id} 交易所狀態仍為 {status_val}，保留 pending")
                    continue
                else:
                    self.logger.warning(
                        f"⚠️ 訂單 {order_id} 狀態未知 ({status_val})，保留 pending 待觀察"
                    )
                    continue

            for order_id, grid_order in filled_orders:
                # 🔥 以交易所回報的成交價/量入帳，不可用本地網格價覆寫。
                # 舊寫法 mark_filled(grid_order.price, grid_order.amount) 會把
                # 0.52 的實際成交記成 0.52798、11 記成 11.4（帳本 vs 交易所永久漂移）。
                exchange_price = grid_order.filled_price or grid_order.price
                exchange_amount = grid_order.filled_amount or grid_order.amount
                self.logger.info(
                    f"📊 REST轮询检测到订单真实成交: {grid_order.side.value} "
                    f"{exchange_amount}@{exchange_price} (Grid {grid_order.grid_id})"
                )
                grid_order.mark_filled(exchange_price, exchange_amount)
                self._remove_order_from_pending(order_id)
                for callback in self._order_callbacks:
                    try:
                        if asyncio.iscoroutinefunction(callback):
                            await callback(grid_order)
                        else:
                            callback(grid_order)
                    except Exception as e:
                        self.logger.error(f"订单回调执行失败: {e}")

            if filled_orders:
                self.logger.info(f"✅ REST轮询处理了 {len(filled_orders)} 个真实成交订单")
                self._last_order_fill_time = time.time()

            return len(filled_orders)

        except Exception as e:
            self.logger.error(f"检查挂单状态失败: {e}")
            return 0

    async def _on_order_update(self, update_data: dict):
        try:
            self.logger.debug(
                f"📨 收到WebSocket订单更新，类型={type(update_data).__name__}")
            self._last_ws_message_time = time.time()
            self.logger.debug(f"📨 完整订单更新数据: {update_data}")
            self.logger.debug(
                f"📊 WebSocket消息时间戳已更新: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self._last_ws_message_time))}")

            from ....adapters.exchanges.models import OrderData as ExchangeOrderData

            if isinstance(update_data, ExchangeOrderData):
                if update_data.symbol != self.config.symbol:
                    return

                order_id = str(update_data.id)
                status = update_data.status.value.upper() if update_data.status else ""

                self.logger.debug(
                    f"收到OrderData: id={order_id}, status={status}, "
                    f"side={update_data.side.value}, filled={update_data.filled}/{update_data.amount}")

                if status in ["FILLED", "CLOSED"]:
                    filled_amount = update_data.filled
                    total_amount = update_data.amount
                    filled_price = update_data.average or update_data.price
                    side = update_data.side

                    if filled_amount >= total_amount:
                        self._last_order_fill_time = time.time()
                        self.logger.info(
                            f"✅ [成交] {side.value.upper()} {filled_amount}@{filled_price} "
                            f"(OrderID: {order_id})"
                        )

                        client_id = str(
                            update_data.client_id) if update_data.client_id else None
                        grid_order = None

                        if client_id:
                            grid_order = self._pending_orders_by_client_id.get(
                                client_id)
                            if grid_order:
                                self.logger.info(
                                    f"   🔍 原始订单: Grid {grid_order.grid_id}, "
                                    f"价格={grid_order.price}, 方向={grid_order.side.value}"
                                )
                            else:
                                self.logger.warning(
                                    f"   ⚠️ client_id={client_id} 不在缓存中（可能是历史订单或健康检查市价单）"
                                )

                        if not grid_order:
                            grid_order = self._pending_orders.get(order_id)
                            if grid_order:
                                self.logger.info(
                                    f"   ✅ 通过 order_id 找到订单: Grid {grid_order.grid_id}"
                                )

                        if grid_order:
                            grid_order.mark_filled(filled_price, filled_amount)
                            if order_id in self._pending_orders:
                                del self._pending_orders[order_id]
                            if client_id and client_id in self._pending_orders_by_client_id:
                                del self._pending_orders_by_client_id[client_id]
                            for callback in self._order_callbacks:
                                try:
                                    if asyncio.iscoroutinefunction(callback):
                                        await callback(grid_order)
                                    else:
                                        callback(grid_order)
                                except Exception as e:
                                    self.logger.error(f"订单回调执行失败: {e}")
                        else:
                            # 未知成交＝帳本不會入帳，持倉必然漂移（重啟前掛單最常見）。
                            # 不可靜默：至少讓它變成可觀測事件，由 [GRID_ALERT] 對帳。
                            self.logger.error(
                                f"🚨 未找到原始订单 (order_id={order_id}, client_id={client_id or 'N/A'})，"
                                f"成交未入帳；可能為重啟前掛單或其他 bot 的成交，持倉將出現漂移"
                            )
                            try:
                                print(
                                    "[GRID_ALERT] " + json.dumps({
                                        "event": "unknown_fill",
                                        "symbol": self.config.symbol,
                                        "order_id": order_id,
                                        "client_id": client_id,
                                        "side": side.value,
                                        "price": str(filled_price),
                                        "amount": str(filled_amount),
                                    }, ensure_ascii=False, separators=(",", ":")),
                                    flush=True,
                                )
                            except Exception:
                                pass
                    else:
                        remaining = total_amount - filled_amount
                        self.logger.info(
                            f"⏳ 部分成交: {side.value} 已成交{filled_amount}/{total_amount}, "
                            f"剩余{remaining}, 继续等待..."
                        )

                    return

                elif status in ["CANCELLED", "CANCELED"]:
                    self.logger.debug(f"订单被取消: order_id={order_id}")
                    if order_id in self._pending_orders:
                        del self._pending_orders[order_id]
                    client_id = str(
                        update_data.client_id) if update_data.client_id else None
                    if client_id and client_id in self._pending_orders_by_client_id:
                        del self._pending_orders_by_client_id[client_id]
                    is_expected_cancellation = order_id in self._expected_cancellations
                    if is_expected_cancellation:
                        self._expected_cancellations.remove(order_id)
                        self.logger.info(f"ℹ️ 订单已主动取消: {order_id}")
                    else:
                        self.logger.warning(f"⚠️ 订单被手动取消: {order_id}")
                    return

                else:
                    self.logger.debug(f"订单状态更新: {status}, OrderID={order_id}")
                    return

            if isinstance(update_data, list):
                self.logger.debug(f"收到Hyperliquid订单列表，包含{len(update_data)}个订单")
                processed_count = 0
                for order_item in update_data:
                    if isinstance(order_item, dict):
                        order_id = str(order_item.get('id', ''))
                        status = order_item.get('status', '').lower()
                        if order_id not in self._pending_orders:
                            continue
                        grid_order = self._pending_orders[order_id]
                        if status in ['closed', 'filled']:
                            filled_price = Decimal(
                                str(order_item.get('price', grid_order.price)))
                            filled_amount = Decimal(
                                str(order_item.get('filled', grid_order.amount)))
                            grid_order.mark_filled(filled_price, filled_amount)
                            del self._pending_orders[order_id]
                            self.logger.info(
                                f"✅ WebSocket订单成交: {grid_order.side.value} {filled_amount}@{filled_price} "
                                f"(Grid {grid_order.grid_id}, OrderID: {order_id})"
                            )
                            for callback in self._order_callbacks:
                                try:
                                    if asyncio.iscoroutinefunction(callback):
                                        await callback(grid_order)
                                    else:
                                        callback(grid_order)
                                except Exception as e:
                                    self.logger.error(f"订单回调执行失败: {e}")
                            processed_count += 1
                if processed_count > 0:
                    self.logger.debug(f"处理了{processed_count}个订单成交")
                return

            if not isinstance(update_data, dict):
                self.logger.warning(f"未知的订单更新格式: {type(update_data)}")
                return

            self.logger.debug("使用Backpack格式处理")
            data = update_data.get('data', update_data)

            if not isinstance(data, dict):
                self.logger.debug(f"data字段不是字典格式，跳过: {type(data)}")
                return

            event_symbol = data.get('s')
            if event_symbol:
                expected_symbol = self.exchange._map_symbol(self.config.symbol)
                expected_clean = expected_symbol.replace('/', '').replace(':', '').upper()
                event_clean = event_symbol.replace('/', '').replace(':', '').upper()
                if event_clean != expected_clean:
                    return

            order_id = str(data.get('i', ''))
            status = str(data.get('X', '')).upper()
            event_type = data.get('e')

            if not order_id or order_id == "":
                self.logger.debug(f"订单更新缺少订单ID: {update_data}")
                return

            grid_order = None
            if order_id in self._pending_orders:
                grid_order = self._pending_orders[order_id]
            else:
                client_id = str(data.get('c', ''))
                if client_id and client_id in self._pending_orders_by_client_id:
                    grid_order = self._pending_orders_by_client_id[client_id]
                    order_id = grid_order.order_id or order_id
                else:
                    self.logger.debug(f"收到非监控订单的更新: {order_id} / {client_id}")
                    return

            if not grid_order:
                return

            self.logger.info(
                f"📨 订单更新: ID={order_id}, "
                f"事件={event_type}, 状态={status}, "
                f"Grid={grid_order.grid_id}"
            )

            if status == 'FILLED' or event_type == 'orderFilled':
                filled_price = Decimal(str(data.get('p', grid_order.price)))
                filled_amount = Decimal(str(data.get('z', data.get('q', grid_order.amount))))
                if data.get('z') and Decimal(str(data.get('z'))) > 0:
                    filled_amount = Decimal(str(data.get('z')))
                elif data.get('l') and Decimal(str(data.get('l'))) > 0:
                    filled_amount = Decimal(str(data.get('l')))

                grid_order.mark_filled(filled_price, filled_amount)
                if order_id in self._pending_orders:
                    del self._pending_orders[order_id]
                if grid_order.order_id and grid_order.order_id in self._pending_orders:
                    del self._pending_orders[grid_order.order_id]
                if grid_order.client_id and grid_order.client_id in self._pending_orders_by_client_id:
                    del self._pending_orders_by_client_id[grid_order.client_id]

                self.logger.info(
                    f"✅ 订单成交: {grid_order.side.value} {filled_amount}@{filled_price} "
                    f"(Grid {grid_order.grid_id})"
                )

                for callback in self._order_callbacks:
                    try:
                        if asyncio.iscoroutinefunction(callback):
                            await callback(grid_order)
                        else:
                            callback(grid_order)
                    except Exception as e:
                        self.logger.error(f"订单回调执行失败: {e}")

            elif status in ['CANCELLED', 'CANCELED'] or event_type == 'orderCancelled':
                if order_id in self._pending_orders:
                    del self._pending_orders[order_id]
                if grid_order.order_id and grid_order.order_id in self._pending_orders:
                    del self._pending_orders[grid_order.order_id]
                if grid_order.client_id and grid_order.client_id in self._pending_orders_by_client_id:
                    del self._pending_orders_by_client_id[grid_order.client_id]

                is_expected_cancellation = order_id in self._expected_cancellations
                if is_expected_cancellation:
                    self._expected_cancellations.remove(order_id)
                    self.logger.info(
                        f"ℹ️ 订单已主动取消，不重新挂单: {grid_order.side.value} {grid_order.amount}@{grid_order.price} "
                        f"(Grid {grid_order.grid_id}, OrderID: {order_id})"
                    )
                else:
                    self.logger.info("ℹ️ 订单被手动取消，遵循嚴格窗口策略：不自動補單")

        except Exception as e:
            import traceback
            self.logger.error(f"处理订单更新失败: {e}")
            self.logger.error(traceback.format_exc())

    def _convert_order_side(self, grid_side: GridOrderSide) -> ExchangeOrderSide:
        if grid_side == GridOrderSide.BUY:
            return ExchangeOrderSide.BUY
        else:
            return ExchangeOrderSide.SELL

    async def start(self):
        self._running = True
        self.logger.info("网格执行引擎已启动")
        try:
            await self._start_price_monitor()
        except Exception as e:
            self.logger.warning(f"⚠️ 价格监控启动失败: {e}")

        try:
            refresh_interval = int(getattr(self.config, 'active_window_refresh_interval', 0) or 0)
            window_each_side = int(getattr(self.config, 'active_window_each_side', 0) or 0)
            if window_each_side > 0:
                asyncio.create_task(self._active_window_refresh_loop())
                eff_interval = refresh_interval if refresh_interval > 0 else 90
                self.logger.info(
                    f"✅ 活躍窗口刷新已啟動：每{eff_interval}秒，現價±{window_each_side}格")
        except Exception:
            pass

        # ==== FIX: 接入訂單健康檢查調度鏈（之前是空實現從未調度）====
        self._start_order_health_check()

    async def stop(self):
        self._running = False
        await self.cancel_all_orders()
        self.logger.info("网格执行引擎已停止")

    def is_running(self) -> bool:
        return self._running

    def __repr__(self) -> str:
        return f"GridEngine({self.exchange}, running={self._running})"

    async def _start_price_monitor(self):
        try:
            self.logger.info("🔄 正在订阅WebSocket价格数据流...")
            await self.exchange.subscribe_ticker(self.config.symbol, self._on_price_update)
            self._price_ws_enabled = True
            self.logger.info("✅ 价格数据流订阅成功 (WebSocket)")
            self.logger.info("📡 使用WebSocket实时监控价格")
        except Exception as e:
            self.logger.error(f"❌ 价格数据流订阅失败: {e}")
            self.logger.error(f"❌ 错误类型: {type(e).__name__}")
            import traceback
            self.logger.error(f"❌ 错误堆栈:\n{traceback.format_exc()}")
            self.logger.warning("⚠️ WebSocket价格订阅失败，将使用REST API获取价格")
            self._price_ws_enabled = False

    def _on_price_update(self, ticker_data) -> None:
        try:
            if ticker_data.last is not None:
                price = ticker_data.last
            elif ticker_data.bid is not None and ticker_data.ask is not None:
                price = (ticker_data.bid + ticker_data.ask) / Decimal('2')
            elif ticker_data.bid is not None:
                price = ticker_data.bid
            elif ticker_data.ask is not None:
                price = ticker_data.ask
            else:
                return
            self._current_price = price
            self._last_price_update_time = time.time()
        except Exception as e:
            self.logger.error(f"处理价格更新失败: {e}", exc_info=True)

    def get_price_monitor_mode(self) -> str:
        if self._price_ws_enabled and self._current_price is not None:
            price_age = time.time() - self._last_price_update_time
            if price_age < 10:
                return "WebSocket"
        return "REST"

    async def _active_window_refresh_loop(self):
        try:
            refresh_interval = int(getattr(self.config, 'active_window_refresh_interval', 0) or 0)
            window_each_side = int(getattr(self.config, 'active_window_each_side', 0) or 0)
            if window_each_side <= 0:
                return
            if refresh_interval <= 0:
                refresh_interval = 90
                self.logger.info(f"📡 活躍窗口刷新循環啟動（設定為0，使用預設 {refresh_interval}s）")
            else:
                self.logger.info(f"📡 活躍窗口刷新循環啟動（間隔 {refresh_interval}s）")
            while self._running:
                if self._is_circuit_open():
                    self.logger.debug(
                        f"⛔ API 熔斷中 (code={self._last_api_error_code})，"
                        f"跳過本輪活躍窗口刷新"
                    )
                    await asyncio.sleep(refresh_interval)
                    continue
                try:
                    await self._refresh_active_window_once(window_each_side)
                except Exception as e:
                    self.logger.debug(f"活躍窗口刷新失敗: {e}")
                await asyncio.sleep(refresh_interval)
        except asyncio.CancelledError:
            self.logger.info("活躍窗口刷新已停止")
        except Exception as e:
            self.logger.error(f"活躍窗口刷新循環異常: {e}")

    async def _refresh_active_window_once(self, window_each_side: int):
        current_price = await self.get_current_price()
        # ==== FIX-A-1: 建立嚴格帶方向的目標窗口 (Target Map: gid -> (side, price)) ====
        target_orders: Dict[int, Tuple[GridOrderSide, Decimal]] = {}
        price_decimals = int(getattr(self.config, 'price_decimals', 2))
        quant = Decimal(10) ** (-price_decimals)
        target_prices_q: Set['Decimal'] = set()
        center_grid = None
        try:
            is_long_type = self.config.grid_type in [
                GridType.LONG, GridType.FOLLOW_LONG, GridType.MARTINGALE_LONG, GridType.NEUTRAL
            ]
            if is_long_type:
                # 價格從下限 lower_price 向上遞增
                k_floor = int((current_price - self.config.lower_price) // self.config.grid_interval) + 1
                k_floor = max(1, min(self.config.grid_count - 1, k_floor))
                # 現價下方買單格: 從 k_floor 往下取 window_each_side 格
                for d in range(window_each_side):
                    g = k_floor - d
                    if 1 <= g <= self.config.grid_count:
                        px = self.config.get_grid_price(g)
                        if px < current_price:
                            target_orders[g] = (GridOrderSide.BUY, px)
                            target_prices_q.add(px.quantize(quant))
                # 現價上方賣單格: 從 k_floor + 1 往上取 window_each_side 格
                for d in range(1, window_each_side + 1):
                    g = k_floor + d
                    if 1 <= g <= self.config.grid_count:
                        px = self.config.get_grid_price(g)
                        if px > current_price:
                            target_orders[g] = (GridOrderSide.SELL, px)
                            target_prices_q.add(px.quantize(quant))
                center_grid = k_floor
            else:  # SHORT, FOLLOW_SHORT, MARTINGALE_SHORT
                # 價格從上限 upper_price 向下遞減
                k_floor = int((self.config.upper_price - current_price) // self.config.grid_interval) + 1
                k_floor = max(1, min(self.config.grid_count - 1, k_floor))
                # 現價上方賣單格 (價格 > current_price，即 index <= k_floor)
                for d in range(window_each_side):
                    g = k_floor - d
                    if 1 <= g <= self.config.grid_count:
                        px = self.config.get_grid_price(g)
                        if px > current_price:
                            target_orders[g] = (GridOrderSide.SELL, px)
                            target_prices_q.add(px.quantize(quant))
                # 現價下方買單格 (價格 < current_price，即 index > k_floor)
                for d in range(1, window_each_side + 1):
                    g = k_floor + d
                    if 1 <= g <= self.config.grid_count:
                        px = self.config.get_grid_price(g)
                        if px < current_price:
                            target_orders[g] = (GridOrderSide.BUY, px)
                            target_prices_q.add(px.quantize(quant))
                center_grid = k_floor
        except Exception:
            return

        # ==== FIX-A-2: 讀取交易所掛單與本地掛單，依價格準確反推 gid 並歸類 ====
        pending_orders_local = self.get_pending_orders()

        if self._is_circuit_open():
            self.logger.debug(
                f"⛔ 活躍窗口：API 熔斷中 (code={self._last_api_error_code})，降級使用本地 pending"
            )
            raw_open = pending_orders_local
        else:
            try:
                raw_open = list(await self.exchange.get_open_orders(self.config.symbol))
                self.logger.debug(f"🔍 活躍窗口: 交易所掛單 {len(raw_open)} 筆")
                # 每次成功取得交易所掛單時，主動校驗並清理本地不存在的幽靈掛單
                await self._sync_orders_from_exchange(raw_open)
                pending_orders_local = self.get_pending_orders()
            except Exception as e:
                self.logger.warning(f"⚠️ 活躍窗口無法取得交易所掛單，降級使用本地 pending: {e}")
                raw_open = pending_orders_local

        # open_by_grid: {grid_id: List[order]} (整數主鍵，由實際價格計算得出，避免舊 grid_id 干擾)
        open_by_grid: Dict[int, List[Any]] = {}
        for o in raw_open:
            try:
                # 依訂單實際價格精準反推 grid_id
                gid = self.config.get_grid_index_by_price(o.price)
                if gid is not None and 1 <= gid <= self.config.grid_count:
                    if gid not in open_by_grid:
                        open_by_grid[gid] = []
                    open_by_grid[gid].append(o)
            except Exception:
                pass

        # ==== FIX-B: 調整時序「先補缺失，再取消多餘」====
        # Step 1: 找出缺失的目標格（目標格在 target_orders 中，但在 open_by_grid 中沒有相同 side 的掛單）
        place_orders: List = []
        now_ts = time.time()
        for g, (target_side, target_price) in target_orders.items():
            existing_orders = open_by_grid.get(g, [])
            has_matching_open = any(
                (getattr(o.side, 'value', str(o.side)).lower() == getattr(target_side, 'value', str(target_side)).lower())
                for o in existing_orders
            )
            has_matching_local = any(
                o.grid_id == g and (getattr(o.side, 'value', str(o.side)).lower() == getattr(target_side, 'value', str(target_side)).lower())
                and o.status == GridOrderStatus.PENDING
                and (now_ts - getattr(o, '_placed_time', (o.created_at.timestamp() if hasattr(o.created_at, 'timestamp') else 0)) < 15)
                for o in pending_orders_local
            )
            if not has_matching_open and not has_matching_local:
                # 避開在現價 0.05 格距內下單（防止穿價/倒掛）
                if abs(target_price - current_price) < (self.config.grid_interval * Decimal('0.05')):
                    continue
                try:
                    amount = self.config.get_formatted_grid_order_amount(g)
                    order = GridOrder(
                        order_id="",
                        grid_id=g,
                        side=target_side,
                        price=target_price,
                        amount=amount,
                        status=GridOrderStatus.PENDING,
                        created_at=datetime.now()
                    )
                    place_orders.append(order)
                except Exception:
                    continue

        if place_orders:
            try:
                self.logger.info(
                    f"🧩 活躍窗口補單: center_grid={center_grid}, "
                    f"補 {len(place_orders)}/{len(target_orders)} 格 "
                    f"(Grids {[o.grid_id for o in place_orders]})")
                await self.place_batch_orders(place_orders, current_price=current_price)
            except Exception as e:
                self.logger.warning(
                    f"❌ 活躍窗口補單失敗 ({len(place_orders)} 張): {e}，"
                    f"預計下一輪 refresh 重試")

        # Step 2: 取消多餘、重複或方向倒掛的無效訂單
        to_cancel = []
        for gid, orders_at_g in open_by_grid.items():
            if gid in target_orders:
                target_side, target_price = target_orders[gid]
                target_side_val = getattr(target_side, 'value', str(target_side))
                kept_one = False
                for o in orders_at_g:
                    o_side_val = getattr(o.side, 'value', str(o.side))
                    if o_side_val == target_side_val and not kept_one:
                        # 正確方向的第一張訂單：保留！
                        kept_one = True
                    else:
                        # 方向錯誤（如回落後殘留的買單在現價上方）或同格重複單：取消！
                        to_cancel.append(o)
            else:
                # 非目標窗口格內的訂單
                for o in orders_at_g:
                    # 額外安全檢查（最近鄰單防撤保護）：若為離現價最近且方向正確的買/賣單（1.05 格距內），保留不撤
                    try:
                        p = o.price
                        diff = abs(p - current_price)
                        o_side_val = getattr(o.side, 'value', str(o.side))
                        is_correct_side = (o_side_val == 'buy' and p < current_price) or (o_side_val == 'sell' and p > current_price)
                        if diff <= (self.config.grid_interval * Decimal('1.05')) and is_correct_side:
                            continue
                    except Exception:
                        pass
                    to_cancel.append(o)

        for o in to_cancel:
            try:
                oid = getattr(o, 'id', '') or getattr(o, 'order_id', '') or ''
                if not oid:
                    continue
                await self.cancel_order(oid)
                await asyncio.sleep(0.1)
            except Exception as cancel_err:
                self.logger.warning(f"窗口刷新撤單異常 {oid}: {cancel_err}")
                await asyncio.sleep(0.5)

        if to_cancel or place_orders:
            self.logger.info(
                f"✅ 活躍窗口刷新完成: 保留窗口 {len(target_orders)} 格 "
                f"(center={center_grid}, Grids {sorted(target_orders.keys())})，"
                f"取消 {len(to_cancel)} 張（非窗口/倒掛/重複），"
                f"新補 {len(place_orders)} 張（缺失）"
            )

    # ==================== 订单健康检查相关方法 ====================

    def _start_order_health_check(self):
        # ==== FIX: 之前是空 return，現在真正啟動健康檢查 loop ====
        enabled = getattr(self.config, 'order_health_check_enabled', False)
        if not enabled:
            self.logger.info("⏭️  訂單健康檢查已在配置中關閉，跳過啟動")
            return
        asyncio.create_task(self._order_health_check_loop())
        interval = getattr(self.config, 'order_health_check_interval', 300)
        self.logger.info(f"✅ 訂單健康檢查器已啟動：每 {interval} 秒執行一次")

    async def _order_health_check_loop(self):
        # ==== FIX: 之前是空 return，現在真正實現健康檢查無限迴圈 ====
        try:
            # 延遲導入避免循環引用（OrderHealthChecker 依賴 GridEngine 介面）
            from .order_health_checker import OrderHealthChecker
            checker = OrderHealthChecker(self.config, self)
        except Exception as e:
            self.logger.error(f"❌ 無法初始化 OrderHealthChecker: {e}")
            return

        interval = int(getattr(self.config, 'order_health_check_interval', 300) or 300)
        first_run = True

        while self._running:
            try:
                if first_run:
                    # 首次跑等 15s，讓初始化掛單有時間落地
                    await asyncio.sleep(min(15, interval))
                    first_run = False
                else:
                    await asyncio.sleep(interval)

                if not self._running:
                    break

                if self._is_circuit_open():
                    self.logger.debug("⛔ 健康檢查跳過：API 熔斷中")
                    continue

                try:
                    await checker.perform_health_check()
                except Exception as inner_e:
                    self.logger.warning(f"⚠️ perform_health_check 丟出例外: {inner_e}")

            except asyncio.CancelledError:
                break
            except Exception as outer_e:
                self.logger.error(f"❌ 健康檢查 loop 丟出未預期例外: {outer_e}")
                await asyncio.sleep(max(10, interval // 2))

    async def _validate_market_snapshots(self) -> Tuple[bool, Optional[Tuple[List, List]]]:
        try:
            snapshot_count = self.config.health_check_snapshot_count
            self.logger.info(f"📸 开始市场快照验证（{snapshot_count}次快照，间隔0.5秒）")
            snapshot_start_fill_time = self._last_order_fill_time
            snapshots = []
            last_orders = None
            last_positions = None

            for i in range(snapshot_count):
                self.logger.info(f"📸 获取第{i+1}次快照...")
                try:
                    orders_task = self.exchange.get_open_orders(self.config.symbol)
                    positions_task = self.exchange.get_positions()
                    orders, positions = await asyncio.gather(
                        orders_task, positions_task, return_exceptions=False
                    )
                    last_orders = orders
                    last_positions = positions
                    snapshot_data = {
                        'order_count': len(orders),
                        'order_prices': sorted([float(o.price) for o in orders]),
                        'position_size': float(positions[0].size) if positions else 0.0
                    }
                    snapshots.append(snapshot_data)
                    self.logger.info(
                        f"   ✅ 快照{i+1}: 订单={snapshot_data['order_count']}个, "
                        f"持仓={snapshot_data['position_size']}"
                    )
                except Exception as e:
                    self.logger.error(f"❌ 获取快照{i+1}失败: {e}")
                    return False, None

                current_fill_time = self._last_order_fill_time
                if current_fill_time > snapshot_start_fill_time:
                    self.logger.info(
                        f"❌ 第{i+1}次快照后检测到新订单成交，立即停止快照 "
                        f"(成交时间: {time.strftime('%H:%M:%S', time.localtime(current_fill_time))})"
                    )
                    return False, None

                if i < snapshot_count - 1:
                    await asyncio.sleep(0.5)

            self.logger.info(f"✅ {snapshot_count}次快照期间无订单成交")

            order_counts = [snap['order_count'] for snap in snapshots]
            if len(set(order_counts)) > 1:
                counts_str = ' vs '.join(str(c) for c in order_counts)
                self.logger.info(f"❌ 快照订单数量不一致: {counts_str}")
                return False, None

            price_tolerance = 10 ** (-self.config.price_decimals)
            first_order_count = len(snapshots[0]['order_prices'])

            for snap in snapshots:
                if len(snap['order_prices']) != first_order_count:
                    self.logger.info(f"❌ 快照订单数量结构不一致")
                    return False, None

            for idx in range(first_order_count):
                prices = [snap['order_prices'][idx] for snap in snapshots]
                for j in range(len(prices) - 1):
                    if abs(prices[j] - prices[j+1]) > price_tolerance:
                        prices_str = ' vs '.join(f"{p:.2f}" for p in prices)
                        self.logger.info(
                            f"❌ 快照订单价格不一致 (订单{idx}): {prices_str}"
                        )
                        return False, None

            position_tolerance = 10 ** (-self.config.quantity_precision)
            position_sizes = [snap['position_size'] for snap in snapshots]
            for j in range(len(position_sizes) - 1):
                if abs(position_sizes[j] - position_sizes[j+1]) > position_tolerance:
                    positions_str = ' vs '.join(str(p) for p in position_sizes)
                    self.logger.info(f"❌ 快照持仓数量不一致: {positions_str}")
                    return False, None

            self.logger.info(
                f"✅ 快照验证通过: 订单={snapshots[0]['order_count']}个, "
                f"持仓={snapshots[0]['position_size']}, 数据一致且稳定"
            )
            self.logger.info("🚀 直接使用快照数据，无需重复API调用")
            return True, (last_orders, last_positions)

        except Exception as e:
            self.logger.error(f"❌ 快照验证过程出错: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return False, None

    def _notify_health_check_complete(self, filled_count: int):
        try:
            pending_orders = self.get_pending_orders()
            buy_count = sum(1 for o in pending_orders if o.side.value.lower() == 'buy')
            sell_count = sum(1 for o in pending_orders if o.side.value.lower() == 'sell')
            total_count = len(pending_orders)
            self.logger.info(
                f"📊 健康检查后订单统计: "
                f"总计={total_count}个, 买单={buy_count}个, 卖单={sell_count}个"
            )
            if filled_count > 0:
                self._last_health_repair_count = filled_count
                self._last_health_repair_time = time.time()
                self.logger.info(
                    f"✅ 健康检查已完成订单补充，补充数量={filled_count}个"
                )
        except Exception as e:
            self.logger.error(f"通知健康检查完成失败: {e}")

    async def _sync_orders_from_exchange(self, exchange_orders: List):
        try:
            from ..models import GridOrder, GridOrderSide, GridOrderStatus
            from datetime import datetime

            exchange_order_ids = {
                str(order.id) for order in exchange_orders if getattr(order, 'id', None)
            }
            exchange_client_ids = {
                str(order.client_id) for order in exchange_orders if getattr(order, 'client_id', None)
            }

            removed_count = 0
            removed_client_ids = 0
            now_ts = time.time()

            for order_id, order_obj in list(self._pending_orders.items()):
                # 在途單保護：下單未滿 15 秒暫不刪除
                placed_time = getattr(order_obj, '_placed_time', (order_obj.created_at.timestamp() if hasattr(order_obj.created_at, 'timestamp') else 0))
                if (now_ts - placed_time) < 15:
                    continue
                oid = str(getattr(order_obj, 'order_id', order_id))
                cid = str(getattr(order_obj, 'client_id', '') or '')
                eid = str(getattr(order_obj, 'exchange_order_id', '') or '')
                if (order_id not in exchange_order_ids and order_id not in exchange_client_ids and
                    oid not in exchange_order_ids and oid not in exchange_client_ids and
                    cid not in exchange_order_ids and cid not in exchange_client_ids and
                    eid not in exchange_order_ids and eid not in exchange_client_ids):
                    self._remove_order_from_pending(order_id)
                    removed_count += 1

            for client_id, cached_order in list(self._pending_orders_by_client_id.items()):
                placed_time = getattr(cached_order, '_placed_time', (cached_order.created_at.timestamp() if hasattr(cached_order.created_at, 'timestamp') else 0))
                if (now_ts - placed_time) < 15:
                    continue
                oid = str(getattr(cached_order, 'order_id', ''))
                cid = str(getattr(cached_order, 'client_id', client_id) or client_id)
                eid = str(getattr(cached_order, 'exchange_order_id', '') or '')
                if (client_id not in exchange_client_ids and
                    oid not in exchange_order_ids and
                    cid not in exchange_client_ids and
                    eid not in exchange_order_ids):
                    del self._pending_orders_by_client_id[client_id]
                    removed_client_ids += 1

            if removed_count > 0 or removed_client_ids > 0:
                self.logger.debug(
                    f"🗑️ 清理本地缓存：移除{removed_count}个订单（_pending_orders）, "
                    f"{removed_client_ids}个client_id（_pending_orders_by_client_id）"
                )

            added_count = 0
            added_client_ids = 0
            synced_client_ids = 0

            for ex_order in exchange_orders:
                if not ex_order.id:
                    continue
                if ex_order.id not in self._pending_orders:
                    try:
                        grid_id = self.config.get_grid_index_by_price(ex_order.price)
                        side = GridOrderSide.BUY if ex_order.side.value.lower() == 'buy' else GridOrderSide.SELL
                        grid_order = GridOrder(
                            order_id=ex_order.id,
                            grid_id=grid_id,
                            side=side,
                            price=ex_order.price,
                            amount=ex_order.amount,
                            status=GridOrderStatus.PENDING,
                            created_at=datetime.now(),
                            client_id=ex_order.client_id if hasattr(
                                ex_order, 'client_id') and ex_order.client_id else None
                        )
                        self._pending_orders[ex_order.id] = grid_order
                        added_count += 1
                        if grid_order.client_id:
                            self._pending_orders_by_client_id[grid_order.client_id] = grid_order
                            added_client_ids += 1
                    except Exception as e:
                        self.logger.warning(
                            f"⚠️ 同步订单{ex_order.id[:10]}...失败: {e}")
                else:
                    try:
                        existing_order = self._pending_orders[ex_order.id]
                        if existing_order.client_id and existing_order.client_id not in self._pending_orders_by_client_id:
                            self._pending_orders_by_client_id[existing_order.client_id] = existing_order
                            synced_client_ids += 1
                        elif not existing_order.client_id and ex_order.client_id:
                            existing_order.client_id = ex_order.client_id
                            self._pending_orders_by_client_id[ex_order.client_id] = existing_order
                            synced_client_ids += 1
                    except Exception as e:
                        self.logger.warning(
                            f"⚠️ 同步已存在订单的client_id失败: {e}")

            total_local = len(self.get_pending_orders())
            total_exchange = len(exchange_orders)
            total_client_id_cache = len(self._pending_orders_by_client_id)

            self.logger.info(
                f"✅ 订单同步完成: 交易所={total_exchange}个, 本地缓存={total_local}个, "
                f"client_id缓存={total_client_id_cache}个"
            )
            self.logger.info(
                f"   新增订单={added_count}个, 移除订单={removed_count}个, "
                f"同步client_id={synced_client_ids}个"
            )

            if total_local != total_exchange:
                self.logger.warning(
                    f"⚠️ 同步后数量仍不匹配: 本地{total_local}个 vs 交易所{total_exchange}个, "
                    f"差异={abs(total_local - total_exchange)}个"
                )

            if total_client_id_cache != total_local:
                missing_count = total_local - total_client_id_cache
                self.logger.warning(
                    f"⚠️ client_id缓存不完整: 本地订单={total_local}个, client_id缓存={total_client_id_cache}个, "
                    f"缺失={missing_count}个 (部分订单可能无client_id，将尝试下次同步)"
                )

        except Exception as e:
            self.logger.error(f"❌ 同步订单失败: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
