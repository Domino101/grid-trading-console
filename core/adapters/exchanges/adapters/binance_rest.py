"""
Binance交易所REST API模块 - 重构版

包含Binance交易所的REST API接口实现
使用 ccxt 库进行API调用，支持期货和现货交易
"""

import asyncio
import collections
import ccxt
import functools
import hmac
import hashlib
import re
import time
from datetime import datetime
from typing import Dict, List, Optional, Any, Set
from decimal import Decimal

from .binance_base import BinanceBase
from ..models import (
    TickerData, OrderBookData, TradeData, BalanceData, OrderData,
    PositionData, OHLCVData, ExchangeInfo, ExchangeType,
    OrderSide, OrderType, OrderStatus
)


class BinanceRest(BinanceBase):
    """Binance REST API接口实现"""

    # ---------------------------------------------------------------------------
    # 🔥 適配器級別 API 熔斷器（類變量，所有 BinanceRest 實例共享）
    # ---------------------------------------------------------------------------
    _adapter_circuit_until: float = 0.0
    _adapter_last_error_code: Optional[str] = None
    _adapter_circuit_initialized: bool = False

    # ---------------------------------------------------------------------------
    # 🔥 過濾器拒單計數（class 級別，按 symbol）
    #
    # 目的：把「掛單全被 -4014/-1111 拒絕」從一行行日誌變成可查詢的健康訊號，
    # 讓 supervisor 能在活躍掛單為 0 時主動告警，而不是安靜地跑 1.5 天。
    # ---------------------------------------------------------------------------
    _rejection_stats: Dict[str, Dict[str, Any]] = {}
    _quantize_log_at: Dict[str, float] = {}

    #: 交易所過濾器相關的拒單代碼（價格/數量不合規）
    FILTER_ERROR_CODES: tuple = ("-4014", "-1111", "-4024", "-4164", "-4003")

    # ---------------------------------------------------------------------------
    # 🔥 全局 REST 限速器（類變量，所有 BinanceRest 實例共享）
    #
    # 機制：滑動窗口 (sliding window)
    #   - 記錄最近 _throttle_window_sec 秒內的 REST 調用時間戳
    #   - 若窗口內已達 _throttle_max_calls 次，後續請求排隊等待
    #
    # 資源消耗：
    #   記憶體 ~200 bytes（deque 最多 8 個 float + Lock + 計數器）
    #   CPU < 10μs/call（比較 8 個時間戳）
    # ---------------------------------------------------------------------------
    # 8 次 / 5 秒 ≈ 96 次/分鐘，仍遠低於 Binance 2400 權重/分鐘；
    # 原本 5 次/5 秒（60 次/分鐘）在 5 台 DCA + 網格同時運行時已吃掉三分之一配額。
    _throttle_max_calls: int = 8
    _throttle_window_sec: float = 5.0
    _throttle_timestamps: collections.deque = collections.deque()
    _throttle_lock: Optional[asyncio.Lock] = None
    _throttle_total_calls: int = 0        # 累計 REST 調用次數
    _throttle_total_waits: int = 0        # 累計排隊等待次數
    _throttle_total_wait_sec: float = 0.0 # 累計排隊等待秒數

    def __init__(self, config, logger=None):
        super().__init__(config)
        self.logger = logger
        self.exchange = None
        
        # API限制配置
        self.rate_limit_orders = 1200  # 1分钟内最大订单数
        self.rate_limit_requests = 2400  # 1分钟内最大请求数
        
        # 🔥 新增：API 快取保護配置
        self._balances_cache = None
        self._balances_cache_time = 0
        self._open_orders_cache = {}  # symbol -> (data, timestamp)
        self._positions_cache = {}    # symbol_key -> (data, timestamp)
        self.api_cache_ttl = 30       # 快取存活時間（秒）—— 提高到30秒避免頻繁REST調用導致429
        
        # 重试配置
        self.max_retries = 3
        self.retry_delay = 1.0

        # 🔥 首次實例化時從磁碟載入持久化熔斷狀態（僅加載一次）
        if not BinanceRest._adapter_circuit_initialized:
            BinanceRest._adapter_circuit_initialized = True
            try:
                from core.services.grid.implementations.grid_engine_impl import read_persisted_circuit_state
                persisted = read_persisted_circuit_state()
                if persisted is not None:
                    until_ts = persisted.get('until_ts')
                    BinanceRest._adapter_last_error_code = persisted.get('code')
                    if until_ts is None:
                        BinanceRest._adapter_circuit_until = float('inf')
                    else:
                        BinanceRest._adapter_circuit_until = float(until_ts)
                    if self.logger:
                        self.logger.warning(
                            f"⛔ [BinanceRest] 從磁碟載入熔斷狀態: code={persisted.get('code')}, "
                            f"until={persisted.get('until_iso')}"
                        )
            except Exception:
                pass  # 首次导入失败不影响功能
        
    async def initialize(self) -> bool:
        """初始化CCXT交易所实例（經過全局限速器）"""
        try:
            # 创建ccxt交易所实例
            self.exchange = ccxt.binance(self.ccxt_config)
            if self.testnet:
                self.exchange.set_sandbox_mode(True)
                if self.logger:
                    self.logger.info("🧪 Binance REST 已啟用測試網模式 (Sandbox Mode -> testnet.binancefuture.com)")
            
            # 加载市场信息（經過限速器）
            await BinanceRest._throttle_acquire(self.logger, 'load_markets')
            await asyncio.get_event_loop().run_in_executor(
                None, self.exchange.load_markets
            )
            
            # 缓存市场信息
            self._market_info = self.exchange.markets
            
            # 测试公共API连接（經過限速器）
            await BinanceRest._throttle_acquire(self.logger, 'fetch_time')
            await asyncio.get_event_loop().run_in_executor(None, self.exchange.fetch_time)
            
            if self.logger:
                self.logger.info(f"✅ Binance REST初始化成功，加载 {len(self.exchange.markets)} 个市场")
            return True
        
        except Exception as e:
            if self.logger:
                self.logger.error(f"❌ Binance REST初始化失败: {str(e)}")
            return False
    
    async def close(self):
        """关闭连接"""
        if self.exchange:
            # ccxt没有显式的close方法，只需清理引用
            self.exchange = None
    
    # ------------------------------------------------------------------
    # 🔥 適配器級別熔斷器輔助方法
    # ------------------------------------------------------------------

    @classmethod
    def _is_adapter_circuit_open(cls) -> bool:
        """判斷適配器級別 API 熔斷器是否開啟"""
        return time.time() < cls._adapter_circuit_until

    @classmethod
    def _update_adapter_circuit(cls, error_str: str, logger=None) -> Optional[str]:
        """
        解析 API 錯誤字串並更新適配器級別熔斷狀態。
        同時同步到 GridEngineImpl 的類變量和磁碟持久化。

        Returns:
            解析到的錯誤碼字串（如 '-2015'），或 None
        """
        m = re.search(r'"?code"?\s*:\s*(-?\d+)', error_str)
        if not m:
            m = re.search(r'(?<!\d)(-\d{4})(?!\d)', error_str)
        code = str(m.group(1)) if m else None

        if code == '-2015':
            cls._adapter_last_error_code = code
            cls._adapter_circuit_until = float('inf')
            if logger:
                logger.critical(
                    "🚨 [BinanceRest] API Key/IP 白名單無效 (-2015)，"
                    "已永久熔斷所有 REST 呼叫！請至 Binance 後台更新 IP 白名單後重啟。"
                )
            # 持久化到磁碟 & 同步到 GridEngineImpl
            cls._sync_circuit_to_engine(code, None)

        elif code == '-1003':
            cls._adapter_last_error_code = code
            bm = re.search(r'banned until (\d+)', error_str)
            if bm:
                until_ms = int(bm.group(1))
                new_until = until_ms / 1000.0
                # 只延長不縮短
                if new_until > cls._adapter_circuit_until:
                    cls._adapter_circuit_until = new_until
                wait_sec = max(0, cls._adapter_circuit_until - time.time())
                if logger:
                    logger.error(
                        f"🚫 [BinanceRest] IP 被 BAN (-1003)，熔斷 {wait_sec:.0f} 秒"
                        f"（直到 {datetime.fromtimestamp(cls._adapter_circuit_until).strftime('%H:%M:%S')}）"
                    )
            else:
                fallback_until = time.time() + 120.0
                if fallback_until > cls._adapter_circuit_until:
                    cls._adapter_circuit_until = fallback_until
                if logger:
                    logger.error(
                        "🚫 [BinanceRest] IP 被 BAN (-1003)，未解析到 banned until 時間戳，"
                        "預設退避 120 秒"
                    )
            cls._sync_circuit_to_engine(code, cls._adapter_circuit_until)

        return code

    @staticmethod
    def _sync_circuit_to_engine(code: str, until_ts: Optional[float]) -> None:
        """同步熔斷狀態到 GridEngineImpl 類變量和磁碟持久化檔"""
        try:
            from core.services.grid.implementations.grid_engine_impl import (
                GridEngineImpl, write_persisted_circuit_state
            )
            GridEngineImpl._shared_last_api_error_code = code
            if until_ts is None:
                GridEngineImpl._shared_api_circuit_until = float('inf')
            else:
                if until_ts > GridEngineImpl._shared_api_circuit_until:
                    GridEngineImpl._shared_api_circuit_until = until_ts
            write_persisted_circuit_state(code, until_ts)
        except Exception:
            pass  # 同步失敗不影響主流程

    @classmethod
    def clear_adapter_circuit(cls) -> None:
        """清除適配器層熔斷狀態（手動恢復後可呼叫）"""
        cls._adapter_circuit_until = 0.0
        cls._adapter_last_error_code = None

    # ------------------------------------------------------------------
    # 🔥 全局 REST 限速器方法
    # ------------------------------------------------------------------

    @classmethod
    def _get_throttle_lock(cls) -> asyncio.Lock:
        """惰性建立 asyncio.Lock（需在事件迴圈中建立）"""
        if cls._throttle_lock is None:
            cls._throttle_lock = asyncio.Lock()
        return cls._throttle_lock

    @classmethod
    async def _throttle_acquire(cls, logger=None, caller: str = '') -> None:
        """
        獲取一個 REST 調用配額。若窗口已滿，阻塞等待直到有空位。

        機制：
          1. 取得 Lock（排隊）
          2. 清理窗口外的過期時間戳
          3. 若窗口已滿 → 計算需等待的秒數 → sleep
          4. 記錄此次調用時間戳
          5. 釋放 Lock

        所有等待者按 FIFO 排隊，確保公平性。
        """
        lock = cls._get_throttle_lock()
        async with lock:
            now = time.time()

            # 清理窗口外的過期時間戳
            while (cls._throttle_timestamps
                   and cls._throttle_timestamps[0] < now - cls._throttle_window_sec):
                cls._throttle_timestamps.popleft()

            # 窗口已滿 → 等待最早的那筆過期
            if len(cls._throttle_timestamps) >= cls._throttle_max_calls:
                oldest = cls._throttle_timestamps[0]
                wait_until = oldest + cls._throttle_window_sec
                wait_sec = max(0.05, wait_until - now)  # 至少 50ms 避免忙等
                cls._throttle_total_waits += 1
                cls._throttle_total_wait_sec += wait_sec
                if logger:
                    queue_depth = 0  # Lock 內無法精確測量，但等待次數可追蹤
                    logger.debug(
                        f"⏱️ [限速] {caller or 'REST'} 排隊等待 {wait_sec:.1f}s "
                        f"(窗口 {cls._throttle_max_calls}/{cls._throttle_window_sec}s，"
                        f"累計等待 {cls._throttle_total_waits} 次)"
                    )
                await asyncio.sleep(wait_sec)
                # sleep 後重新清理
                now = time.time()
                while (cls._throttle_timestamps
                       and cls._throttle_timestamps[0] < now - cls._throttle_window_sec):
                    cls._throttle_timestamps.popleft()

            # 記錄此次調用
            cls._throttle_timestamps.append(time.time())
            cls._throttle_total_calls += 1

    @classmethod
    def get_throttle_stats(cls) -> Dict[str, Any]:
        """回傳限速器統計資訊（用於日誌 / 監控面板）"""
        now = time.time()
        recent = sum(1 for t in cls._throttle_timestamps
                     if t > now - cls._throttle_window_sec)
        return {
            'total_calls': cls._throttle_total_calls,
            'total_waits': cls._throttle_total_waits,
            'total_wait_sec': round(cls._throttle_total_wait_sec, 1),
            'window_current': recent,
            'window_max': cls._throttle_max_calls,
            'window_sec': cls._throttle_window_sec,
        }

    async def _execute_with_retry(self, func, *args, **kwargs):
        """带重试的API调用（含限速器 + 熔斷器 + 429/BAN退避）"""

        # 🔥 熔斷器前置檢查：IP 仍在 BAN 中時直接拒絕 REST 呼叫
        if BinanceRest._is_adapter_circuit_open():
            until = BinanceRest._adapter_circuit_until
            code = BinanceRest._adapter_last_error_code or '?'
            if until == float('inf'):
                msg = f"API 熔斷器永久開啟 (code={code})，拒絕 REST 呼叫"
            else:
                remaining = max(0, until - time.time())
                msg = f"API 熔斷中 (code={code})，剩餘 {remaining:.0f} 秒，拒絕 REST 呼叫"
            raise RuntimeError(f"⛔ [BinanceRest] {msg}")

        # 🔥 全局限速器：排隊取得配額（每 5 秒最多 8 次）
        func_name = getattr(func, '__name__', str(func)[:30])
        await BinanceRest._throttle_acquire(self.logger, func_name)

        last_error = None

        for attempt in range(self.max_retries):
            try:
                if kwargs:
                    call_fn = functools.partial(func, *args, **kwargs)
                    result = await asyncio.get_event_loop().run_in_executor(None, call_fn)
                else:
                    result = await asyncio.get_event_loop().run_in_executor(
                        None, func, *args
                    )
                return result
            except Exception as e:
                last_error = e
                err_str = str(e).lower()

                # 🔥 429 / BAN 特殊处理：立即停止重试 + 更新適配器熔斷器
                is_rate_limit = (
                    'ratelimit' in type(e).__name__.lower()
                    or '429' in err_str
                    or 'too many requests' in err_str
                    or 'banned until' in err_str
                    or 'ip banned' in err_str
                    or '-1021' in err_str  # TIMESTAMP_OUT_OF_WINDOW
                    or 'signature for this request is not valid' in err_str
                )
                if is_rate_limit:
                    if self.logger:
                        self.logger.error(
                            f"❌ API速率限制/BAN错误，立即停止重试: {str(e)}"
                        )
                    # 🔥 更新適配器級別熔斷器狀態（全局生效）
                    BinanceRest._update_adapter_circuit(str(e), self.logger)
                    # 同步失效所有缓存，避免返回过时数据
                    self._balances_cache = None
                    self._balances_cache_time = 0
                    self._open_orders_cache.clear()
                    self._positions_cache.clear()
                    raise  # 直接抛出，让 PositionMonitor 的 BAN 冷却逻辑处理

                # 🔥 -2015 特殊檢查（避免單一幣種權限不足誤殺全域 API Key）
                if '-2015' in err_str:
                    is_order_func = any(k in func_name.lower() for k in ('order', 'batch'))
                    should_trip_circuit = True
                    if is_order_func and self.exchange:
                        try:
                            # 輕量探針檢查帳戶權限（直接調用單次驗證，繞過重試機制）
                            await asyncio.get_event_loop().run_in_executor(None, self.exchange.fetch_balance)
                            # 若 fetch_balance 成功，代表 API Key 與 IP 均有效，僅該標的或操作權限受限
                            should_trip_circuit = False
                            if self.logger:
                                self.logger.warning(
                                    f"⚠️ [BinanceRest] 下單時收到 -2015，但帳戶驗證成功！"
                                    f"判定為特定標的或合約權限不足，不觸發全域熔斷: {str(e)}"
                                )
                        except Exception as probe_err:
                            if '-2015' in str(probe_err).lower():
                                should_trip_circuit = True
                            else:
                                should_trip_circuit = False

                    if should_trip_circuit:
                        BinanceRest._update_adapter_circuit(str(e), self.logger)
                    raise

                # 🔥 -2013 / 訂單不存在：立即停止重試（不可逆終端狀態，避免阻塞限速器隊列）
                if '-2013' in err_str or 'order does not exist' in err_str or 'ordernotfound' in type(e).__name__.lower():
                    if self.logger:
                        self.logger.debug(f"ℹ️ 訂單不存在（-2013 / OrderNotFound），停止重試: {str(e)}")
                    raise

                # 🔥 過濾器錯誤（價格不在 tick / 數量不在 step）：重試同樣的價格沒有意義，
                # 立即停止內層重試，交給 create_order 刷新過濾器後重新量化再試一次。
                if self._filter_error_code(e):
                    if self.logger:
                        self.logger.warning(f"🚫 交易所過濾器拒單，停止內層重試（等待重新量化）: {str(e)}")
                    raise

                if attempt < self.max_retries - 1:
                    # 指数退避：1s → 3s → 9s...（避免瞬断时暴力重试）
                    delay = self.retry_delay * (3 ** attempt)
                    if self.logger:
                        self.logger.warning(
                            f"⚠️ API调用失败，{delay:.1f}秒后重试"
                            f" (尝试 {attempt + 1}/{self.max_retries}): {str(e)}"
                        )
                    await asyncio.sleep(delay)
                else:
                    if self.logger:
                        self.logger.error(f"❌ API调用最终失败: {str(e)}")

        raise last_error
    
    # ==================== 市场数据接口 ====================
    
    async def get_exchange_info(self) -> ExchangeInfo:
        """获取交易所信息"""
        try:
            # 获取服务器时间
            server_time = await self._execute_with_retry(self.exchange.fetch_time)
            
            return ExchangeInfo(
                name="Binance Futures",
                id="binance",
                type=ExchangeType.FUTURES,
                supported_features=[
                    "spot_trading", "futures_trading", "websocket",
                    "orderbook", "ticker", "ohlcv", "user_stream"
                ],
                rate_limits={
                    'orders': self.rate_limit_orders,
                    'requests': self.rate_limit_requests
                },
                precision={},  # 从市场数据中获取
                fees={},  # TODO: 获取实际费率
                markets=self._market_info,
                status="operational",
                timestamp=datetime.fromtimestamp(server_time / 1000)
            )
        except Exception as e:
            if self.logger:
                self.logger.error(f"获取交易所信息失败: {str(e)}")
            raise
    
    async def get_ticker(self, symbol: str) -> TickerData:
        """获取单个行情数据"""
        try:
            mapped_symbol = self.map_symbol_to_binance(symbol)
            ticker_data = await self._execute_with_retry(
                self.exchange.fetch_ticker, mapped_symbol
            )
            return self.parse_ticker(ticker_data, symbol)
        except Exception as e:
            if self.logger:
                self.logger.error(f"获取行情失败 {symbol}: {str(e)}")
            raise
    
    async def get_tickers(self, symbols: Optional[List[str]] = None) -> List[TickerData]:
        """获取多个行情数据"""
        try:
            if symbols:
                # 并发获取指定符号的行情
                tasks = [self.get_ticker(symbol) for symbol in symbols]
                return await asyncio.gather(*tasks)
            else:
                # 获取所有行情
                tickers_data = await self._execute_with_retry(
                    self.exchange.fetch_tickers
                )
                
                result = []
                for market_symbol, ticker_data in tickers_data.items():
                    symbol = self.map_symbol_from_binance(market_symbol)
                    result.append(self.parse_ticker(ticker_data, symbol))
                
                return result
        except Exception as e:
            if self.logger:
                self.logger.error(f"获取多个行情失败: {str(e)}")
            raise
    
    async def get_orderbook(self, symbol: str, limit: Optional[int] = None) -> OrderBookData:
        """获取订单簿"""
        try:
            mapped_symbol = self.map_symbol_to_binance(symbol)
            orderbook_data = await self._execute_with_retry(
                self.exchange.fetch_order_book, mapped_symbol, limit
            )
            return self.parse_orderbook(orderbook_data, symbol)
        except Exception as e:
            if self.logger:
                self.logger.error(f"获取订单簿失败 {symbol}: {str(e)}")
            raise
    
    async def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        since: Optional[datetime] = None,
        limit: Optional[int] = None
    ) -> List[OHLCVData]:
        """获取K线数据"""
        try:
            mapped_symbol = self.map_symbol_to_binance(symbol)
            since_timestamp = int(since.timestamp() * 1000) if since else None
            
            ohlcv_data = await self._execute_with_retry(
                self.exchange.fetch_ohlcv,
                mapped_symbol,
                timeframe,
                since_timestamp,
                limit
            )
            
            result = []
            for candle in ohlcv_data:
                ohlcv = OHLCVData(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=datetime.fromtimestamp(candle[0] / 1000),
                    open=self._safe_decimal(candle[1]),
                    high=self._safe_decimal(candle[2]),
                    low=self._safe_decimal(candle[3]),
                    close=self._safe_decimal(candle[4]),
                    volume=self._safe_decimal(candle[5]),
                    quote_volume=None,
                    trades_count=None,
                    raw_data={'candle': candle}
                )
                result.append(ohlcv)
            
            return result
        except Exception as e:
            if self.logger:
                self.logger.error(f"获取K线数据失败 {symbol}: {str(e)}")
            raise
    
    async def get_trades(
        self,
        symbol: str,
        since: Optional[datetime] = None,
        limit: Optional[int] = None
    ) -> List[TradeData]:
        """获取成交数据"""
        try:
            mapped_symbol = self.map_symbol_to_binance(symbol)
            since_timestamp = int(since.timestamp() * 1000) if since else None
            
            trades_data = await self._execute_with_retry(
                self.exchange.fetch_trades,
                mapped_symbol,
                since_timestamp,
                limit
            )
            
            return [self.parse_trade(trade, symbol) for trade in trades_data]
        except Exception as e:
            if self.logger:
                self.logger.error(f"获取成交数据失败 {symbol}: {str(e)}")
            raise
    
    # ==================== 账户接口 ====================
    
    async def get_balances(self) -> List[BalanceData]:
        """获取账户余额"""
        try:
            # 🔥 新增：快取保護邏輯
            now = time.time()
            if self._balances_cache and (now - self._balances_cache_time) < self.api_cache_ttl:
                return self._balances_cache

            raw = await self._execute_with_retry(self.exchange.fetch_balance)

            result: List[BalanceData] = []

            # 非字典或空，直接返回空列表
            if not isinstance(raw, dict) or not raw:
                return result

            # ccxt 标准结构包含三层映射：free/used/total 字典，以及每币种字典
            totals = raw.get('total') if isinstance(raw.get('total'), dict) else {}
            frees = raw.get('free') if isinstance(raw.get('free'), dict) else {}
            useds = raw.get('used') if isinstance(raw.get('used'), dict) else {}

            # 收集所有币种键
            currency_keys = set()
            currency_keys.update(k for k in totals.keys() if isinstance(k, str))
            currency_keys.update(k for k in frees.keys() if isinstance(k, str))
            currency_keys.update(k for k in useds.keys() if isinstance(k, str))

            # 同时遍历顶层币种对象（某些交易所返回每币种字典）
            for k, v in raw.items():
                if isinstance(v, dict) and k not in ['free', 'used', 'total', 'info']:
                    currency_keys.add(k)

            # 构建余额对象
            for currency in sorted(currency_keys):
                try:
                    # 顶层币种对象（如 {'USDT': {'free':..., 'used':..., 'total':...}}）
                    top_obj = raw.get(currency)
                    free = None
                    used = None
                    total = None

                    if isinstance(top_obj, dict):
                        free = self._safe_decimal(top_obj.get('free'))
                        used = self._safe_decimal(top_obj.get('used'))
                        total = self._safe_decimal(top_obj.get('total'))

                    # 回退到聚合映射
                    if free is None:
                        free = self._safe_decimal(frees.get(currency))
                    if used is None:
                        used = self._safe_decimal(useds.get(currency))
                    if total is None:
                        total = self._safe_decimal(totals.get(currency))

                    # 再次保障为空时设为0，并计算 total
                    free = free or Decimal('0')
                    used = used or Decimal('0')
                    total = total or (free + used)

                    if total > 0:
                        balance = BalanceData(
                            currency=currency,
                            free=free,
                            used=used,
                            total=total,
                            usd_value=None,
                            timestamp=datetime.now(),
                            raw_data=top_obj if isinstance(top_obj, dict) else {
                                'free': str(free), 'used': str(used), 'total': str(total)
                            }
                        )
                        result.append(balance)
                except Exception:
                    # 单币种解析失败不影响整体
                    continue

            # 🔥 新增：更新快取
            self._balances_cache = result
            self._balances_cache_time = now

            return result
        except Exception as e:
            if self.logger:
                self.logger.error(f"获取账户余额失败: {str(e)}")
            raise
    
    async def get_positions(self, symbols: Optional[List[str]] = None) -> List[PositionData]:
        """获取持仓信息"""
        try:
            # 🔥 新增：快取保護邏輯
            now = time.time()
            cache_key = ','.join(sorted(symbols)) if symbols else 'all'
            if cache_key in self._positions_cache:
                cache_data, cache_time = self._positions_cache[cache_key]
                if (now - cache_time) < self.api_cache_ttl:
                    return cache_data

            positions_data = await self._execute_with_retry(
                self.exchange.fetch_positions
            )
            
            result = []
            for position_info in positions_data:
                # 只返回有持仓的合约
                if float(position_info.get('contracts', 0)) == 0:
                    continue
                
                position = self.parse_position(position_info)
                
                # 过滤指定符号
                if symbols is None or position.symbol in symbols:
                    result.append(position)
                elif symbols:
                    norm_sym = position.symbol.replace('/', '').replace(':', '').upper()
                    for s in symbols:
                        if s.replace('/', '').replace(':', '').upper() == norm_sym:
                            result.append(position)
                            break
            
            # 🔥 新增：更新快取
            self._positions_cache[cache_key] = (result, now)
            
            return result
        except Exception as e:
            if self.logger:
                self.logger.error(f"获取持仓信息失败: {str(e)}")
            raise
    
    # ==================== 交易接口 ====================
    
    # ==================== 交易所過濾器（tickSize / stepSize）量化 ====================
    #
    # 這裡是**唯一**把價格與數量轉成交易所可接受值的邊界。所有下單入口
    # （單筆、批次、反手、健康檢查補單、DCA）都經過 create_order / create_batch_orders，
    # 因此不需要在策略層各自推算 min_tick。

    @classmethod
    def _filter_error_code(cls, exc: Any) -> Optional[str]:
        text = str(exc)
        for code in cls.FILTER_ERROR_CODES:
            if code in text:
                return code
        return None

    @classmethod
    def _record_rejection(cls, symbol: str, code: str, message: str) -> None:
        entry = cls._rejection_stats.setdefault(
            str(symbol),
            {"consecutive": 0, "total": 0, "last_code": None, "last_message": "", "last_at": 0.0},
        )
        entry["consecutive"] += 1
        entry["total"] += 1
        entry["last_code"] = code
        entry["last_message"] = str(message)[:200]
        entry["last_at"] = time.time()

    @classmethod
    def _record_placement_success(cls, symbol: str) -> None:
        entry = cls._rejection_stats.get(str(symbol))
        if entry:
            entry["consecutive"] = 0

    @classmethod
    def get_rejection_stats(cls, symbol: Optional[str] = None) -> Dict[str, Any]:
        empty = {"consecutive": 0, "total": 0, "last_code": None, "last_message": "", "last_at": 0.0}
        if symbol is not None:
            entry = cls._rejection_stats.get(str(symbol))
            return dict(entry) if entry else dict(empty)
        return {key: dict(value) for key, value in cls._rejection_stats.items()}

    @classmethod
    def reset_rejection_stats(cls, symbol: Optional[str] = None) -> None:
        if symbol is None:
            cls._rejection_stats.clear()
            return
        cls._rejection_stats.pop(str(symbol), None)

    async def _market_rules(self, symbol: str, *, force_refresh: bool = False):
        """取得即時過濾器（TTL 快取；force_refresh 會 reload_markets）。"""
        if self.exchange is None:
            return None
        try:
            from core.adapters.exchanges.market_rules import market_rules_cache

            return await asyncio.get_event_loop().run_in_executor(
                None,
                functools.partial(
                    market_rules_cache.get, self.exchange, symbol, force_refresh=force_refresh
                ),
            )
        except Exception as exc:
            if self.logger:
                self.logger.debug(f"取得交易所過濾器失敗（沿用原值）: {exc}")
            return None

    def _quantize_with_rules(self, rules, symbol: str, side, amount, price):
        """依 live 過濾器量化；任何改寫都記錄（同 symbol 60 秒內只記一次）。"""
        if rules is None:
            return price, amount
        side_text = getattr(side, "value", side)
        q_price = rules.quantize_price(price, side_text) if price is not None else None
        q_amount = rules.quantize_qty(amount)

        if price is not None and q_price is not None and Decimal(str(q_price)) != Decimal(str(price)):
            self._log_quantize(symbol, "price", price, q_price, rules.price_tick)
        if amount is not None and q_amount is not None and Decimal(str(q_amount)) != Decimal(str(amount)):
            self._log_quantize(symbol, "quantity", amount, q_amount, rules.qty_step)
        return q_price, q_amount

    def _log_quantize(self, symbol: str, kind: str, requested, used, unit) -> None:
        key = f"{symbol}:{kind}"
        now = time.time()
        last = BinanceRest._quantize_log_at.get(key, 0.0)
        if now - last < 60:
            return
        BinanceRest._quantize_log_at[key] = now
        if self.logger:
            self.logger.warning(
                f"🔧 [過濾器] {symbol} {kind} {requested} → {used} "
                f"(交易所 {('tick' if kind == 'price' else 'step')}={unit})"
            )

    async def create_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        amount: Decimal,
        price: Optional[Decimal] = None,
        params: Optional[Dict[str, Any]] = None
    ) -> OrderData:
        """创建订单"""
        try:
            mapped_symbol = self.map_symbol_to_binance(symbol)
            
            # 准备订单参数
            order_params = dict(params) if params else {}
            client_id = (
                order_params.pop("client_order_id", None)
                or order_params.pop("clientOrderId", None)
                or order_params.pop("newClientOrderId", None)
            )
            if client_id:
                order_params["clientOrderId"] = str(client_id)
            if "reduce_only" in order_params:
                order_params["reduceOnly"] = bool(order_params.pop("reduce_only"))
            if "stop_price" in order_params and "stopPrice" not in order_params:
                order_params["stopPrice"] = order_params.pop("stop_price")
            
            side_str = side.value.lower() if hasattr(side, "value") else str(side).lower()
            order_type_str = self.ORDER_TYPE_MAPPING.get(order_type, getattr(order_type, "value", str(order_type)).upper())

            # 🔥 送單前一律依 live tickSize/stepSize 量化（單一委託建構邊界）
            rules = await self._market_rules(symbol)
            q_price, q_amount = self._quantize_with_rules(rules, symbol, side, amount, price)
            if rules and "stopPrice" in order_params:
                q_stop = rules.quantize_price(order_params["stopPrice"], side_str)
                if q_stop is not None:
                    order_params["stopPrice"] = float(q_stop)

            amount_float = float(q_amount) if q_amount is not None else 0.0
            try:
                order_data = await self._execute_with_retry(
                    self.exchange.create_order,
                    mapped_symbol,
                    order_type_str,
                    side_str,
                    amount_float,
                    float(q_price) if q_price else None,
                    order_params
                )
            except Exception as exc:
                filter_code = self._filter_error_code(exc)
                if not filter_code:
                    raise
                # 交易所剛調整過濾器：強制刷新後重新量化，只重試一次
                self._record_rejection(symbol, filter_code, str(exc))
                refreshed = await self._market_rules(symbol, force_refresh=True)
                retry_price, retry_amount = self._quantize_with_rules(
                    refreshed, symbol, side, amount, price
                )
                if refreshed and "stopPrice" in order_params:
                    retry_stop = refreshed.quantize_price(order_params["stopPrice"], side_str)
                    if retry_stop is not None:
                        order_params["stopPrice"] = float(retry_stop)
                if (retry_price, retry_amount) == (q_price, q_amount):
                    raise
                if self.logger:
                    self.logger.warning(
                        f"♻️ [過濾器自癒] {symbol} 以新規則重送: {q_price}@{q_amount} → {retry_price}@{retry_amount}"
                    )
                retry_amount_float = float(retry_amount) if retry_amount is not None else 0.0
                order_data = await self._execute_with_retry(
                    self.exchange.create_order,
                    mapped_symbol,
                    order_type_str,
                    side_str,
                    retry_amount_float,
                    float(retry_price) if retry_price else None,
                    order_params
                )

            self._record_placement_success(symbol)
            return self.parse_order(order_data, symbol)
        except Exception as e:
            if self.logger:
                self.logger.error(f"创建订单失败 {symbol}: {str(e)}")
            raise

    async def create_batch_orders(
        self,
        symbol: str,
        orders: List[Dict[str, Any]],
    ) -> List[Optional[OrderData]]:
        """
        批量下單 — 利用 Binance POST /fapi/v1/batchOrders（每次最多 5 張）

        Args:
            symbol: 交易對（內部格式，如 SOL_USDC）
            orders: 訂單字典列表，每個包含:
                - side: OrderSide
                - order_type: OrderType
                - amount: Decimal
                - price: Optional[Decimal]
                - params: Optional[Dict]

        Returns:
            與 orders 等長的列表，成功為 OrderData，失敗為 None
        """
        mapped_symbol = self.map_symbol_to_binance(symbol)
        batch_size = 5  # Binance 批量 API 上限
        results: List[Optional[OrderData]] = []

        def _build(batch, active_rules):
            built = []
            for o in batch:
                q_price, q_amount = self._quantize_with_rules(
                    active_rules, symbol, o['side'], o['amount'], o.get('price')
                )
                p = dict(o.get('params') or {})
                cid = (
                    p.pop("client_order_id", None)
                    or p.pop("clientOrderId", None)
                    or p.pop("newClientOrderId", None)
                )
                if cid:
                    p["clientOrderId"] = str(cid)
                if "reduce_only" in p:
                    p["reduceOnly"] = bool(p.pop("reduce_only"))
                built.append({
                    'symbol': mapped_symbol,
                    'type': self.ORDER_TYPE_MAPPING.get(o['order_type'], 'LIMIT'),
                    'side': o['side'].value.lower(),
                    'amount': float(q_amount),
                    'price': float(q_price) if q_price is not None else None,
                    'params': p,
                })
            return built

        for i in range(0, len(orders), batch_size):
            batch = orders[i:i + batch_size]
            rules = await self._market_rules(symbol)
            outcomes: List[Optional[OrderData]] = [None] * len(batch)
            pending = list(range(len(batch)))
            attempt = 0
            circuit_error: Optional[str] = None

            while pending and attempt < 2:
                attempt += 1
                ccxt_orders = _build([batch[j] for j in pending], rules)
                try:
                    # 🔥 全局限速器：排隊取得配額
                    await BinanceRest._throttle_acquire(self.logger, 'create_orders_batch')

                    # 🔥 熔斷器前置檢查
                    if BinanceRest._is_adapter_circuit_open():
                        code = BinanceRest._adapter_last_error_code or '?'
                        raise RuntimeError(f"⛔ [BinanceRest] API 熔斷中 (code={code})，拒絕批量下單")

                    batch_results = await asyncio.get_event_loop().run_in_executor(
                        None,
                        self.exchange.create_orders,
                        ccxt_orders,
                    )
                except Exception as e:
                    err_str = str(e)
                    filter_code = self._filter_error_code(e)
                    if filter_code and attempt == 1:
                        # 交易所剛調整過濾器：刷新後用新 tick/step 重建，只重試一次
                        self._record_rejection(symbol, filter_code, err_str)
                        rules = await self._market_rules(symbol, force_refresh=True)
                        if self.logger:
                            self.logger.warning(
                                f"♻️ [過濾器自癒] 批量下單以新規則重送 (batch {i // batch_size + 1}): {err_str}"
                            )
                        continue
                    circuit_error = err_str
                    break

                next_pending: List[int] = []
                for local_idx, res in enumerate(batch_results):
                    if local_idx >= len(pending):
                        break
                    j = pending[local_idx]
                    try:
                        if not res or isinstance(res, Exception):
                            if self.logger:
                                self.logger.error(f"❌ 批量下單單筆異常 (第 {j+1} 張): {res}")
                            outcomes[j] = None
                            continue
                        if isinstance(res, dict) and ('code' in res or 'msg' in res) and not ('orderId' in res or 'id' in res):
                            code_text = str(res.get('code'))
                            is_filter = code_text in BinanceRest.FILTER_ERROR_CODES
                            if is_filter:
                                self._record_rejection(symbol, code_text, str(res.get('msg')))
                            if is_filter and attempt == 1:
                                next_pending.append(j)
                                continue
                            if self.logger:
                                self.logger.error(
                                    f"❌ 批量下單單筆被交易所拒絕 (第 {j+1} 張): "
                                    f"code={res.get('code')} msg={res.get('msg')}"
                                )
                            outcomes[j] = None
                            continue

                        parsed = self.parse_order(res, symbol)
                        if not parsed or not getattr(parsed, 'id', None):
                            if self.logger:
                                self.logger.error(f"❌ 批量下單單筆回傳無有效 orderId: {res}")
                            outcomes[j] = None
                        else:
                            outcomes[j] = parsed
                    except Exception as parse_e:
                        if self.logger:
                            self.logger.error(f"❌ 批量下單結果解析異常 (第 {j+1} 張): {parse_e}")
                        outcomes[j] = None

                if next_pending:
                    rules = await self._market_rules(symbol, force_refresh=True)
                    if self.logger:
                        self.logger.warning(
                            f"♻️ [過濾器自癒] batch {i // batch_size + 1} 有 {len(next_pending)} 張被過濾器拒絕，"
                            f"以新 tick/step 重送"
                        )
                pending = next_pending

            if circuit_error is not None:
                if self.logger:
                    self.logger.error(f"❌ 批量下單失敗 (batch {i // batch_size + 1}): {circuit_error}")
                # 檢查熔斷器（若為 -2015 先探針確認是否為全域金鑰無效）
                if '-2015' in circuit_error and self.exchange:
                    try:
                        await asyncio.get_event_loop().run_in_executor(None, self.exchange.fetch_balance)
                        if self.logger:
                            self.logger.warning(f"⚠️ [BinanceRest] 批量下單收到 -2015 但帳戶資訊驗證正常，不觸發全域熔斷")
                    except Exception as probe_err:
                        if '-2015' in str(probe_err).lower():
                            BinanceRest._update_adapter_circuit(circuit_error, self.logger)
                else:
                    BinanceRest._update_adapter_circuit(circuit_error, self.logger)

            results.extend(outcomes)
            if any(item is not None for item in outcomes):
                self._record_placement_success(symbol)

            # 批次間短暫延遲
            if i + batch_size < len(orders):
                await asyncio.sleep(0.15)

        return results

    async def cancel_order(self, order_id: str, symbol: str, params: Optional[Dict[str, Any]] = None) -> OrderData:
        """取消订单（支援 numeric order_id 與 client_order_id）"""
        try:
            mapped_symbol = self.map_symbol_to_binance(symbol)
            order_params = dict(params) if params else {}
            str_id = str(order_id)
            if not str_id.isdigit() and "origClientOrderId" not in order_params and "clientOrderId" not in order_params:
                order_params["origClientOrderId"] = str_id
            
            order_data = await self._execute_with_retry(
                self.exchange.cancel_order,
                order_id,
                mapped_symbol,
                params=order_params
            )
            
            return self.parse_order(order_data, symbol)
        except Exception as e:
            if self.logger:
                self.logger.error(f"取消订单失败 {order_id}: {str(e)}")
            raise
    
    async def cancel_all_orders(self, symbol: Optional[str] = None) -> List[OrderData]:
        """取消所有订单"""
        try:
            if symbol:
                # 取消指定符号的所有订单
                mapped_symbol = self.map_symbol_to_binance(symbol)
                open_orders = await self._execute_with_retry(
                    self.exchange.fetch_open_orders, mapped_symbol
                )
            else:
                # 取消所有订单
                open_orders = await self._execute_with_retry(
                    self.exchange.fetch_open_orders
                )
            
            result = []
            for order in open_orders:
                try:
                    cancelled_order = await self._execute_with_retry(
                        self.exchange.cancel_order,
                        order['id'],
                        order['symbol']
                    )
                    result.append(self.parse_order(
                        cancelled_order, 
                        symbol or self.map_symbol_from_binance(order['symbol'])
                    ))
                except Exception as e:
                    if self.logger:
                        self.logger.warning(f"取消订单失败 {order['id']}: {str(e)}")
            
            return result
        except Exception as e:
            if self.logger:
                self.logger.error(f"取消所有订单失败: {str(e)}")
            raise
    
    async def get_order(self, order_id: str, symbol: str, params: Optional[Dict[str, Any]] = None) -> OrderData:
        """获取订单信息（支援交易所 numeric order_id 與 client_order_id 自動識別反查）"""
        try:
            mapped_symbol = self.map_symbol_to_binance(symbol)
            order_params = dict(params) if params else {}
            str_id = str(order_id)
            client_id = (
                order_params.pop("client_order_id", None)
                or order_params.get("origClientOrderId")
                or order_params.get("clientOrderId")
                or (str_id if not str_id.isdigit() else None)
            )
            if client_id and "origClientOrderId" not in order_params and "clientOrderId" not in order_params:
                order_params["origClientOrderId"] = str(client_id)
            
            data = await self._execute_with_retry(
                self.exchange.fetch_order,
                order_id,
                mapped_symbol,
                params=order_params
            )
            if isinstance(data, list) and data:
                data = data[0]
            return self.parse_order(data, symbol)
        except Exception as e:
            if self.logger:
                self.logger.error(f"获取订单信息失败 {order_id}: {str(e)}")
            raise
    
    async def get_open_orders(self, symbol: Optional[str] = None) -> List[OrderData]:
        """获取开放订单"""
        try:
            # 🔥 新增：快取保護邏輯
            now = time.time()
            cache_key = symbol or 'all'
            if cache_key in self._open_orders_cache:
                cache_data, cache_time = self._open_orders_cache[cache_key]
                if (now - cache_time) < self.api_cache_ttl:
                    return cache_data

            if symbol:
                mapped_symbol = self.map_symbol_to_binance(symbol)
                orders_data = await self._execute_with_retry(
                    self.exchange.fetch_open_orders, mapped_symbol
                )
            else:
                orders_data = await self._execute_with_retry(
                    self.exchange.fetch_open_orders
                )
            
            result = []
            for order_data in orders_data:
                order_symbol = symbol or self.map_symbol_from_binance(order_data.get('symbol', ''))
                result.append(self.parse_order(order_data, order_symbol))
            
            # 🔥 新增：更新快取
            self._open_orders_cache[cache_key] = (result, now)
            
            return result
        except Exception as e:
            if self.logger:
                self.logger.error(f"获取开放订单失败: {str(e)}")
            raise
    
    async def get_order_history(
        self,
        symbol: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: Optional[int] = None
    ) -> List[OrderData]:
        """获取历史订单"""
        try:
            mapped_symbol = self.map_symbol_to_binance(symbol) if symbol else None
            since_timestamp = int(since.timestamp() * 1000) if since else None
            
            orders_data = await self._execute_with_retry(
                self.exchange.fetch_orders,
                mapped_symbol,
                since_timestamp,
                limit
            )
            
            result = []
            for order_data in orders_data:
                order_symbol = symbol or self.map_symbol_from_binance(order_data.get('symbol', ''))
                result.append(self.parse_order(order_data, order_symbol))
            
            return result
        except Exception as e:
            if self.logger:
                self.logger.error(f"获取历史订单失败: {str(e)}")
            raise
    
    # ==================== 设置接口 ====================
    
    async def set_leverage(self, symbol: str, leverage: int) -> Dict[str, Any]:
        """设置杠杆倍数"""
        try:
            mapped_symbol = self.map_symbol_to_binance(symbol)
            
            # Binance期货设置杠杆
            result = await self._execute_with_retry(
                self.exchange.set_leverage,
                leverage,
                mapped_symbol
            )
            
            if self.logger:
                self.logger.info(f"✅ 设置杠杆成功 {symbol}: {leverage}x")
            
            return result
        except Exception as e:
            if self.logger:
                self.logger.error(f"设置杠杆失败 {symbol}: {str(e)}")
            raise
    
    async def set_margin_mode(self, symbol: str, margin_mode: str) -> Dict[str, Any]:
        """设置保证金模式"""
        try:
            mapped_symbol = self.map_symbol_to_binance(symbol)
            
            # Binance期货设置保证金模式
            result = await self._execute_with_retry(
                self.exchange.set_margin_mode,
                margin_mode,
                mapped_symbol
            )
            
            if self.logger:
                self.logger.info(f"✅ 设置保证金模式成功 {symbol}: {margin_mode}")
            
            return result
        except Exception as e:
            if self.logger:
                self.logger.error(f"设置保证金模式失败 {symbol}: {str(e)}")
            raise
    
    # ==================== 健康检查 ====================
    
    async def health_check(self) -> Dict[str, Any]:
        """健康检查"""
        health_data = {
            'api_accessible': False,
            'exchange_time': None,
            'market_count': 0,
            'server_time_diff': None
        }
        
        try:
            # 检查API访问
            start_time = time.time()
            server_time = await self._execute_with_retry(self.exchange.fetch_time)
            end_time = time.time()
            
            health_data['api_accessible'] = True
            health_data['exchange_time'] = datetime.fromtimestamp(server_time / 1000)
            health_data['server_time_diff'] = (end_time - start_time) * 1000  # 毫秒
            
            # 检查市场数据
            if self._market_info:
                health_data['market_count'] = len(self._market_info)
            
            return health_data
        except Exception as e:
            health_data['error'] = str(e)
            if self.logger:
                self.logger.error(f"健康检查失败: {str(e)}")
            return health_data
    
    async def heartbeat(self) -> bool:
        """心跳检查"""
        try:
            await self._execute_with_retry(self.exchange.fetch_time)
            return True
        except Exception as e:
            if self.logger:
                self.logger.error(f"心跳检查失败: {str(e)}")
            return False