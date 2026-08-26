#!/usr/bin/env python3
"""
多網格交易系統啟動腳本 (單進程多工併發 - 支援動態熱插拔控制)

此腳本用於在一單個 Python 進程中併發運行多個網格機器人，
共享同一個交易所適配器 (Exchange Adapter)，實現：
1. 共享 CCXT 緩存與限速器 (Rate Limiter)，防止觸發 Binance API 限流和 Ban。
2. 共享同一個 WebSocket 帳戶流和行情流訂閱，極大節省連接數與頻寬。
3. 支援動態熱插拔：讀取 `config/active_grids.json`，在運行時隨時開啟/停止特定網格，而不影響其他網格。
"""

import sys
import os
import asyncio
import yaml
import logging
import json
import hashlib
from pathlib import Path
from decimal import Decimal
import argparse

# 添加項目根目錄到 sys.path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

try:
    from dotenv import load_dotenv
    load_dotenv(project_root / ".env", override=False)
except ImportError:
    pass

from core.adapters.exchanges.utils import setup_optimized_logging
from core.adapters.exchanges.models import ExchangeType
from core.adapters.exchanges import ExchangeFactory, ExchangeConfig
from core.services.dca.config_loader import (
    auto_fill_dca_precision,
    dca_account_mode_from_yaml,
    load_dca_config,
)
from core.services.dca.coordinator import DcaCoordinator
from core.services.dca.execution import DcaOrderExecutor
from core.services.dca.market_data.ohlcv_cache import OhlcvCache
from core.adapters.exchanges.adapters.binance_rest import BinanceRest
from core.services.grid.coordinator import GridCoordinator
from core.services.grid.implementations import (
    GridStrategyImpl,
    GridEngineImpl,
    PositionTrackerImpl
)
from core.services.grid.models import GridConfig, GridType, GridState
from core.services.grid.reserve import (
    SpotReserveManager,
    ReserveMonitor,
    check_spot_reserve_on_startup
)
from core.services.grid.implementations.grid_engine_impl import (
    read_persisted_circuit_state, clear_persisted_circuit_state
)
from core.logging import get_system_logger

# 導入單網格啟動腳本中的配置解析和適配器創建函數
from run_grid_trading import create_grid_config, detect_market_type, create_exchange_adapter, auto_fill_precision

_backend_dir = str(project_root / "web_ui" / "backend")
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)
from control_plane import publish_lifecycle, publish_runtime
from lifecycle import account_mode_from_yaml, iter_hotplug_starts, live_trading_enabled, start_rejection

# 優化的日誌系統
setup_optimized_logging(use_colored=True)

def _pid_alive(pid: int) -> bool:
    """檢查 PID 是否仍存活(不送信號,僅探活)"""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _kill_existing_runners():
    """
    用 pgrep 掃描所有 run_multiple_grids.py 進程並逐一清理(排除自己)。

    此為 acquire_multi_lock() 的補充機制:即使 /tmp/grid_multiple_runners.pid
    鎖檔丟失、過期,或被其他進程覆寫,仍可避免多個併發啟動造成交易所
    連線 / 限速器互相競爭。
    """
    import subprocess
    import time as _time
    import signal

    current_pid = os.getpid()
    try:
        result = subprocess.run(
            ["pgrep", "-f", "run_multiple_grids.py"],
            capture_output=True, text=True, timeout=5,
        )
    except FileNotFoundError:
        print("⚠️ pgrep 不存在,跳過重複進程掃描")
        return
    except subprocess.TimeoutExpired:
        print("⚠️ pgrep 掃描逾時,跳過重複進程掃描")
        return

    if result.returncode != 0 or not result.stdout.strip():
        return

    other_pids = [
        int(p) for p in result.stdout.strip().split()
        if p.isdigit() and int(p) != current_pid
    ]
    if not other_pids:
        return

    print(f"🔍 偵測到 {len(other_pids)} 個其他 run_multiple_grids.py 進程: {other_pids}")

    for pid in other_pids:
        try:
            os.kill(pid, signal.SIGTERM)
            print(f"   → 已送 SIGTERM 給 PID {pid}")
        except ProcessLookupError:
            continue
        except PermissionError:
            print(f"   ❌ 無權限終止 PID {pid},跳過")
            continue

    # 等待最多 5 秒讓 SIGTERM 起作用
    for _ in range(50):
        if not any(_pid_alive(p) for p in other_pids):
            break
        _time.sleep(0.1)

    # 對仍存活的進程升級為 SIGKILL
    for pid in other_pids:
        if _pid_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
                print(f"   → 已送 SIGKILL 給 PID {pid}")
            except (ProcessLookupError, PermissionError):
                pass
    _time.sleep(0.3)
    print("✅ 重複進程清理完成")


def acquire_multi_lock() -> str:
    """
    獲取多網格啟動器的進程鎖，防止重複啟動。
    """
    import time
    import signal

    # 🔥 先用 pgrep 掃並清理所有重複進程(不限於鎖檔記錄的那一個)
    _kill_existing_runners()

    lock_file = "/tmp/grid_multiple_runners.pid"

    if os.path.exists(lock_file):
        with open(lock_file, 'r') as f:
            old_pid = f.read().strip()
        if old_pid.isdigit():
            old_pid_int = int(old_pid)
            try:
                os.kill(old_pid_int, 0)
                print(f"⚠️ 發現多網格系統舊進程 (PID: {old_pid}) 正在運行")
                print(f"🔄 正在嘗試自動結束舊進程...")
                try:
                    os.kill(old_pid_int, signal.SIGTERM)
                    for _ in range(50):
                        try:
                            os.kill(old_pid_int, 0)
                            time.sleep(0.1)
                        except OSError:
                            break
                    else:
                        print(f"⏳ 舊進程未響應，強制結束 (SIGKILL)...")
                        os.kill(old_pid_int, signal.SIGKILL)
                        time.sleep(0.5)
                except PermissionError:
                    print("❌ 啟動失敗：沒有權限結束舊進程，請使用 sudo 或手動結束！")
                    sys.exit(1)
                except OSError as e:
                    print(f"⚠️ 結束舊進程時發生異常: {e}")
                print(f"✅ 旧进程已成功結束")
            except OSError:
                pass

    with open(lock_file, 'w') as f:
        f.write(str(os.getpid()))
    return lock_file


def _abs_config(path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else project_root / path


def _load_grid_yaml(path: str) -> dict:
    with open(_abs_config(path), "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _config_account_mode(path: str) -> str:
    try:
        return account_mode_from_yaml(_load_grid_yaml(path))
    except Exception:
        return "unknown"


def _load_dca_yaml(path: str) -> dict:
    with open(_abs_config(path), "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _dca_config_account_mode(path: str) -> str:
    """DCA 檔案的 account_mode；未標註時為 unknown（稽核 3.4）。"""
    try:
        return dca_account_mode_from_yaml(_load_dca_yaml(path))
    except Exception:
        return "unknown"


def _grid_style_from_dca(data: dict) -> dict:
    """把 DCA 設定轉成 create_exchange_adapter 需要的 grid_system 外觀。"""
    section = data.get("dca") if isinstance(data.get("dca"), dict) else data
    return {
        "grid_system": {
            "exchange": str(section.get("exchange") or "").lower(),
            "symbol": str(section.get("symbol") or ""),
            "account_mode": str(section.get("account_mode") or "unknown").lower(),
        }
    }


class ExchangeAdapterPool:
    """One connected adapter per exchange. Different venues must not share a client."""

    def __init__(self):
        self._adapters = {}
        self._lock = asyncio.Lock()

    async def get(self, config_path: str):
        data = _load_grid_yaml(config_path)
        is_dca = isinstance(data.get("dca"), dict)
        if is_dca:
            section = data["dca"]
            exchange = str(section.get("exchange") or "").lower()
            if not exchange:
                raise ValueError(f"{config_path} 缺少 dca.exchange")
            mode = dca_account_mode_from_yaml(data)
            # 同一交易所的網格與 DCA 共用同一個 adapter（稽核 3.16），
            # 避免第二套 WS 連線與限速器。
            key = f"paper:{exchange}" if mode == "paper" else f"live:{exchange}"
        else:
            exchange = str((data.get("grid_system") or {}).get("exchange") or "").lower()
            if not exchange:
                raise ValueError(f"{config_path} 缺少 grid_system.exchange")
            mode = account_mode_from_yaml(data)
            key = f"paper:{exchange}" if mode == "paper" else f"live:{exchange}"
        async with self._lock:
            if key not in self._adapters:
                self._adapters[key] = await create_exchange_adapter(
                    _grid_style_from_dca(data) if is_dca else data
                )
            return self._adapters[key]

    async def disconnect_all(self):
        async with self._lock:
            adapters = list(self._adapters.values())
            self._adapters.clear()
        for adapter in adapters:
            try:
                await adapter.disconnect()
            except Exception:
                pass


def _emit_lifecycle(config_path: str, status: str, detail=None, symbol=None):
    payload = {"event": "grid_lifecycle", "config_path": config_path, "status": status}
    if detail:
        payload["detail"] = detail
    if symbol:
        payload["symbol"] = symbol
    print("[GRID_LIFECYCLE] " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)
    try:
        publish_lifecycle(config_path, status, detail)
    except Exception as exc:
        logging.getLogger("system").warning("寫入 lifecycle journal 失敗: %s", exc)


def _emit_dca_lifecycle(config_path: str, status: str, detail=None, symbol=None):
    payload = {"event": "dca_lifecycle", "config_path": config_path, "status": status}
    if detail:
        payload["detail"] = detail
    if symbol:
        payload["symbol"] = symbol
    print("[DCA_LIFECYCLE] " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)
    try:
        publish_lifecycle(config_path, status, detail)
    except Exception as exc:
        logging.getLogger("system").warning("寫入 DCA lifecycle journal 失敗: %s", exc)


async def run_single_grid_task(config_path: str, adapter_pool, logger, running_grids_ref):
    """
    併發啟動並運行單個網格任務
    """
    logger.info(f"🟢 [初始化] 正在讀取配置檔案: {config_path}")
    try:
        blocked = start_rejection(_config_account_mode(config_path), live_trading_enabled())
        if blocked:
            logger.error(f"❌ [{config_path}] {blocked}")
            _emit_lifecycle(config_path, "failed", blocked)
            return

        # 1. 載入並解析配置
        config_data = _load_grid_yaml(config_path)
        grid_config = create_grid_config(config_data)
        shared_adapter = await adapter_pool.get(config_path)

        # 🔥 自動獲取精度信息（若 YAML 中省略了 quantity_precision / price_decimals）
        await auto_fill_precision(grid_config, shared_adapter, logger)

        # 2. 建立該網格專有的網格組件，注入共享適配器
        strategy = GridStrategyImpl()
        
        # 關鍵點：同一交易所共用 adapter，不同交易所分池，避免串單。
        engine = GridEngineImpl(shared_adapter)
        
        grid_state = GridState()
        tracker = PositionTrackerImpl(grid_config, grid_state)
        
        # 處理現貨預留管理（如有）
        reserve_manager = None
        reserve_monitor = None
        if shared_adapter.config.exchange_type == ExchangeType.SPOT:
            spot_reserve_config = getattr(grid_config, 'spot_reserve', None)
            if spot_reserve_config and spot_reserve_config.get('enabled', False):
                logger.info(f"💼 [{grid_config.symbol}] 現貨預留管理已啟用")
                reserve_manager = SpotReserveManager(
                    reserve_config=spot_reserve_config,
                    exchange_adapter=shared_adapter,
                    symbol=grid_config.symbol,
                    quantity_precision=grid_config.quantity_precision
                )
                reserve_monitor = ReserveMonitor(
                    reserve_manager=reserve_manager,
                    exchange_adapter=shared_adapter,
                    symbol=grid_config.symbol,
                    check_interval=60
                )

        # 3. 實例化協調器 (Coordinator)
        coordinator = GridCoordinator(
            config=grid_config,
            strategy=strategy,
            engine=engine,
            tracker=tracker,
            grid_state=grid_state,
            config_path=config_path
        )

        # 註冊到全域運行字典，以便動態調度停止與監控
        running_grids_ref[config_path] = {
            "coordinator": coordinator,
            "task": asyncio.current_task()
        }

        # 4. 現貨啟動前檢查
        if reserve_manager:
            logger.info(f"🔍 [{grid_config.symbol}] 正在執行啟動前資金預留檢查...")
            if not await check_spot_reserve_on_startup(grid_config, shared_adapter, reserve_manager):
                err_msg = "現貨資金預留檢查未通過"
                logger.error(f"❌ [{grid_config.symbol}] 啟動檢查未通過，跳過此網格！")
                _emit_lifecycle(config_path, "failed", err_msg)
                return

        # 5. 啟動協調器
        logger.info(f"🚀 [{grid_config.symbol}] 正在掛出網格訂單...")
        await coordinator.start()
        
        if reserve_monitor:
            await reserve_monitor.start()
            logger.info(f"✅ [{grid_config.symbol}] 預留監控器已啟動")

        logger.info(f"✨ [{grid_config.symbol}] 網格系統完全就位！開始監聽交易對...")
        # Machine-readable acknowledgement consumed by the control API. The
        # config path, rather than a symbol, is used as the lifecycle identity.
        _emit_lifecycle(config_path, "running", symbol=grid_config.symbol)

        # 6. 週期性統計日誌彙報
        import decimal
        from datetime import timedelta, datetime

        class JSONEncoder(json.JSONEncoder):
            def default(self, obj):
                if isinstance(obj, decimal.Decimal):
                    return float(obj)
                if isinstance(obj, timedelta):
                    return str(obj)
                if isinstance(obj, datetime):
                    return obj.isoformat()
                return super().default(obj)

        while True:
            try:
                # 🔥 熔斷守衛：API BAN 期間跳過統計查詢（避免 REST 呼叫加劇 BAN）
                if hasattr(engine, '_is_circuit_open') and engine._is_circuit_open():
                    code = getattr(engine, '_last_api_error_code', '?')
                    logger.warning(
                        f"⛔ [{grid_config.symbol}] API 熔斷中 (code={code})，跳過本輪統計查詢"
                    )
                else:
                    stats = await coordinator.get_statistics()
                    try:
                        stats_dict = stats.to_display_dict()
                    except Exception:
                        stats_dict = {
                            "current_price": float(stats.current_price),
                            "current_position": float(stats.current_position),
                            "grid_position": float(getattr(stats, "grid_position", stats.current_position)),
                            "account_position": float(stats.account_position) if getattr(stats, "account_position", None) is not None else None,
                            "profit": {
                                "grid_profit": float(getattr(stats, "grid_profit", stats.realized_profit)),
                                "realized": float(stats.realized_profit),
                                "unrealized": float(stats.unrealized_profit),
                                "fees": float(stats.total_fees),
                                "net": float(stats.net_profit),
                                "rate": float(stats.profit_rate),
                            },
                        }
                    stats_dict["symbol"] = grid_config.symbol
                    stats_dict["source"] = "engine_runtime"
                    try:
                        pending = engine.get_pending_orders()
                        stats_dict["pending_order_list"] = [
                            {
                                "side": getattr(order.side, "value", str(order.side)),
                                "price": float(order.price) if order.price is not None else None,
                                "amount": float(order.amount) if order.amount is not None else None,
                                "grid_id": getattr(order, "grid_id", None),
                                "status": getattr(order.status, "value", str(order.status)),
                            }
                            for order in pending[:50]
                        ]
                    except Exception:
                        pass
                    try:
                        trades = coordinator.tracker.get_trade_history(limit=50)
                        stats_dict["recent_fills"] = [
                            {
                                "side": t.get("side"),
                                "price": float(t["price"]) if t.get("price") is not None else None,
                                "amount": float(t["amount"]) if t.get("amount") is not None else None,
                                "fee": float(t.get("fee") or 0.0),
                                "time": t.get("time").isoformat() if hasattr(t.get("time"), "isoformat") else str(t.get("time")),
                                "profit": float(t["profit"]) if t.get("profit") is not None else None,
                            }
                            for t in trades
                        ]
                    except Exception:
                        pass
                    print(f"[STATS_JSON][{grid_config.symbol}] {json.dumps(stats_dict, cls=JSONEncoder)}")
                    try:
                        publish_runtime(config_path, stats_dict)
                    except Exception as persist_err:
                        logger.warning(f"寫入 runtime snapshot 失敗: {persist_err}")
                    logger.info(
                        f"📊 [{grid_config.symbol}] 當前價格: {stats.current_price} | "
                        f"持倉: {stats.current_position} | "
                        f"實現盈虧: {stats.realized_pnl} | "
                        f"活躍掛單: {stats.total_pending_orders}"
                    )
                    # 🔥 限速器統計（每分鐘彙報一次）
                    if True:
                        ts = BinanceRest.get_throttle_stats()
                        logger.info(
                            f"⏱️ [限速器] REST累計={ts['total_calls']}次 | "
                            f"排隊={ts['total_waits']}次 | "
                            f"等待={ts['total_wait_sec']}s | "
                            f"窗口={ts['window_current']}/{ts['window_max']}"
                        )
            except Exception as stats_err:
                logger.error(f"⚠️ [{grid_config.symbol}] 獲取統計資訊失敗: {stats_err}")
                # 🔥 統計查詢失敗也嘗試同步至熔斷器（防止 BAN 錯誤被吞噬）
                if hasattr(engine, '_parse_and_set_circuit'):
                    try:
                        engine._parse_and_set_circuit(str(stats_err))
                    except Exception:
                        pass
            await asyncio.sleep(60)

    except asyncio.CancelledError:
        logger.info(f"🛑 [{grid_config.symbol if 'grid_config' in locals() else config_path}] 網格任務已被取消/停用。")
        _emit_lifecycle(config_path, "stopped")
        raise
    except Exception as e:
        logger.error(f"💥 運行網格任務時發生崩潰 [{config_path}]: {e}", exc_info=True)
        _emit_lifecycle(config_path, "failed", str(e))
    finally:
        if config_path in running_grids_ref:
            del running_grids_ref[config_path]


async def run_single_dca_task(config_path: str, adapter_pool, logger,
                              running_dca_ref, dca_ohlcv_caches):
    """
    併發啟動並運行單個 DCA 機器人（Phase 3）。

    R-1 task 隔離（稽核 3.15）：頂層 except 捕捉所有非 CancelledError 例外，
    發 failed lifecycle 後**只結束自己這條 task**，絕不向上拋——DCA 不得拖垮
    網格引擎進程。
    """
    coordinator = None
    executor = None
    bot_id = Path(config_path).stem
    try:
        logger.info(f"🔵 [DCA 初始化] 讀取設定檔: {config_path}")
        data = _load_dca_yaml(config_path)
        account_mode = dca_account_mode_from_yaml(data)
        blocked = start_rejection(account_mode, live_trading_enabled())
        if blocked:
            logger.error(f"❌ [DCA {config_path}] {blocked}")
            _emit_dca_lifecycle(config_path, "failed", blocked)
            return

        config = load_dca_config(data)
        symbol = config.symbol
        exchange = config.exchange

        # 共享 Adapter 池（稽核 3.16）：與網格同 exchange 共用實例
        adapter = await adapter_pool.get(config_path)
        await auto_fill_dca_precision(config, adapter, logger)

        # 每 exchange 一個共享 OhlcvCache：同 symbol+timeframe 去重（稽核 3.6）
        cache = dca_ohlcv_caches.get(exchange)
        if cache is None:
            cache = OhlcvCache(
                lambda _ex, sym, tf, limit, _adapter=adapter:
                    _adapter.get_ohlcv(sym, tf, limit=limit)
            )
            dca_ohlcv_caches[exchange] = cache

        executor = DcaOrderExecutor(config, bot_id, adapter)
        coordinator = DcaCoordinator(
            config,
            bot_id,
            executor,
            ohlcv_provider=cache.provider(exchange),
            lifecycle_publisher=lambda status: _emit_dca_lifecycle(
                config_path, status, symbol=symbol
            ),
        )

        running_dca_ref[config_path] = {
            "coordinator": coordinator,
            "task": asyncio.current_task(),
        }
        coordinator.start()
        _emit_dca_lifecycle(config_path, "running", symbol=symbol)
        logger.info(f"✨ [DCA {symbol}] 已進入 armed（監控中，交易所零掛單）")

        interval = max(int(config.monitoring_interval), 5)
        while True:
            await asyncio.sleep(interval)
            if coordinator.state.status.value == "failed":
                # 已由 coordinator 轉 failed 並發布，supervisor 只結束自己
                return
            try:
                last_price = await executor.last_price()
            except Exception:
                last_price = None
            try:
                stats = coordinator.build_statistics(last_price)
                stats_dict = stats.to_display_dict()
            except Exception as stats_err:
                logger.warning(f"⚠️ [DCA {symbol}] 統計建構失敗: {stats_err}")
                continue
            stats_dict["symbol"] = symbol
            stats_dict["exchange"] = exchange
            stats_dict["source"] = "dca_engine_runtime"
            stats_dict["synthetic"] = (getattr(adapter, "exchange", None) is None)
            try:
                publish_runtime(config_path, stats_dict)
            except Exception as persist_err:
                logger.warning(f"⚠️ 寫入 DCA runtime snapshot 失敗: {persist_err}")

    except asyncio.CancelledError:
        logger.info(f"🛑 [DCA {bot_id}] 任務已被取消/停用。")
        if coordinator is not None:
            try:
                await coordinator.stop()
            except Exception as stop_err:
                logger.warning(f"⚠️ [DCA {bot_id}] 取消關閉時出錯: {stop_err}")
        _emit_dca_lifecycle(config_path, "stopped")
        raise
    except Exception as e:
        # R-1 / 稽核 3.15：吞掉所有非 CancelledError 例外，只結束自己
        logger.error(f"💥 DCA 任務異常終止 [{config_path}]: {e}", exc_info=True)
        try:
            _emit_dca_lifecycle(config_path, "failed", str(e))
        except Exception:
            pass
    finally:
        if coordinator is not None:
            try:
                await coordinator.stop()
            except Exception:
                pass
        if config_path in running_dca_ref:
            del running_dca_ref[config_path]


async def main():
    parser = argparse.ArgumentParser(description="多網格交易系統啟動器 (單進程多工併發 - 支援熱插拔)")
    parser.add_argument('configs', metavar='CONFIG_PATH', type=str, nargs='*',
                        help='網格配置檔案路徑（例如：config/grid/binance-neutral-perp-sol.yaml）')
    parser.add_argument('--debug', action='store_true', help='啟用 DEBUG 級別日誌輸出')
    args = parser.parse_args()

    # 設置日誌級別
    logger = get_system_logger()
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
        for module in ['core.services.grid', 'core.adapters.exchanges', 'ExchangeAdapter']:
            logging.getLogger(module).setLevel(logging.DEBUG)
        logger.info("🔧 調試模式 (DEBUG) 已啟用")

    logger.info("🎬 多網格併發啟動器運行中...")

    # CLI 明確指定的配置才加入 desired state。未指定時進入熱插拔待命，
    # 不再偷偷啟動內建的五個實盤 YAML。
    config_paths = list(args.configs)
    valid_paths = []
    for cp in config_paths:
        if _abs_config(cp).exists():
            valid_paths.append(cp)
        else:
            logger.warning(f"⚠️ 找不到配置檔案: {cp}，跳過此網格。")

    lock_file = acquire_multi_lock()
    adapter_pool = ExchangeAdapterPool()

    control_file = project_root / "config" / "active_grids.json"
    control_file.parent.mkdir(parents=True, exist_ok=True)

    active_status = {}
    if control_file.exists():
        try:
            with open(control_file, "r", encoding="utf-8") as cf:
                loaded = json.load(cf)
            if isinstance(loaded, dict):
                active_status = loaded
        except Exception as e:
            logger.warning(f"⚠️ 讀取 active_grids.json 失敗: {e}，將重建控制檔。")

    modified = False
    for path in valid_paths:
        if path not in active_status:
            active_status[path] = True
            modified = True

    if modified or not control_file.exists():
        try:
            tmp = control_file.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as cf:
                json.dump(active_status, cf, indent=4, ensure_ascii=False)
                cf.flush()
                os.fsync(cf.fileno())
            os.replace(tmp, control_file)
            logger.info(f"📝 已建立/更新網格控制檔: {control_file}")
        except Exception as e:
            logger.error(f"❌ 寫入 active_grids.json 失敗: {e}")

    enabled_existing = [
        path for path, enabled in active_status.items()
        if enabled and _abs_config(path).exists()
    ]
    if enabled_existing:
        logger.info(f"📋 控制檔中有 {len(enabled_existing)} 個啟用中的網格；適配器將按交易所分池建立。")
    else:
        logger.info("📋 沒有待啟動的網格，進入熱插拔待命。UI 新建的 YAML 將在啟用後被接手。")

    # ── DCA 熱插拔控制檔（Phase 3）──
    dca_control_file = project_root / "config" / "active_dca.json"
    dca_control_file.parent.mkdir(parents=True, exist_ok=True)
    active_dca_status = {}
    if dca_control_file.exists():
        try:
            with open(dca_control_file, "r", encoding="utf-8") as cf:
                loaded = json.load(cf)
            if isinstance(loaded, dict):
                active_dca_status = loaded
        except Exception as e:
            logger.warning(f"⚠️ 讀取 active_dca.json 失敗: {e}，將重建控制檔。")
    if not dca_control_file.exists():
        try:
            tmp = dca_control_file.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as cf:
                json.dump(active_dca_status, cf, indent=4, ensure_ascii=False)
                cf.flush()
                os.fsync(cf.fileno())
            os.replace(tmp, dca_control_file)
            logger.info(f"📝 已建立 DCA 控制檔: {dca_control_file}")
        except Exception as e:
            logger.error(f"❌ 寫入 active_dca.json 失敗: {e}")

    # 🔥 啟動前檢查：若 IP 仍在 BAN 中，等待解封後再啟動網格任務
    if BinanceRest._is_adapter_circuit_open():
        import time as _t
        from datetime import datetime as _dt
        until = BinanceRest._adapter_circuit_until
        code = BinanceRest._adapter_last_error_code or '?'
        if until == float('inf'):
            logger.error(
                f"⛔ 偵測到永久熔斷狀態 (code={code}): API Key 或 IP 白名單失效"
            )
            logger.error("請至 Binance 後台更新 IP 白名單或 API Key 後，刪除 data/.circuit_breaker_state.json，再重啟")
            if os.path.exists(lock_file):
                os.remove(lock_file)
            sys.exit(1)
        else:
            wait_total = until - _t.time()
            until_str = _dt.fromtimestamp(until).strftime("%Y-%m-%d %H:%M:%S")
            logger.warning(
                f"⛔ IP 仍在 BAN 中 (code={code})，等待解封後再啟動網格任務"
                f"（直到 {until_str}，剩餘 {wait_total:.0f} 秒）"
            )
            while BinanceRest._is_adapter_circuit_open():
                remaining = BinanceRest._adapter_circuit_until - _t.time()
                if remaining <= 0:
                    break
                sleep_for = min(30.0, remaining)
                logger.info(f"   ⏳ 等待解封：剩餘 {remaining:.0f} 秒...")
                await asyncio.sleep(sleep_for)
            # 解封後清除熔斷狀態
            BinanceRest.clear_adapter_circuit()
            GridEngineImpl._shared_api_circuit_until = 0.0
            GridEngineImpl._shared_last_api_error_code = None
            clear_persisted_circuit_state()
            logger.info("✅ BAN 已解封，繼續啟動網格任務")

    # 4. 併發運行字典與監控循環
    running_grids_ref = {}
    running_dca_ref = {}
    dca_ohlcv_caches = {}

    # 定義熱插拔控制監控協程
    async def active_grids_monitor():
        logger.info("👀 網格動態控制監控器 (Hot-reload Monitor) 已啟動")
        reported_rejections = set()
        while True:
            await asyncio.sleep(5)
            if not control_file.exists():
                continue
            try:
                with open(control_file, 'r', encoding='utf-8') as cf:
                    current_status = json.load(cf)
            except Exception:
                continue

            for running_path in list(running_grids_ref.keys()):
                if not current_status.get(running_path, False):
                    logger.info(f"🛑 [動態控制] 檢測到 {running_path} 被設為停用或已刪除，正在優雅取消所有掛單並停用任務...")
                    grid_info = running_grids_ref.get(running_path)
                    if grid_info:
                        try:
                            await grid_info["coordinator"].stop()
                        except Exception as stop_err:
                            logger.error(f"⚠️ [動態控制] 停止 {running_path} 失敗: {stop_err}")
                        grid_info["task"].cancel()
                    logger.info(f"✅ [動態控制] {running_path} 已成功停止並清空掛單。")
                    reported_rejections.discard(running_path)

            def _exists(path: str) -> bool:
                return _abs_config(path).exists()

            # 檢查是否有啟用但被阻擋或不存在的配置，即時回報失敗而非靜默逾時
            for path, enabled in current_status.items():
                if enabled and path not in running_grids_ref:
                    if not _exists(path):
                        if path not in reported_rejections:
                            reported_rejections.add(path)
                            logger.error(f"❌ [動態控制] 找不到配置檔案: {path}")
                            _emit_lifecycle(path, "failed", f"找不到配置檔案: {path}")
                    else:
                        blocked = start_rejection(_config_account_mode(path), live_trading_enabled())
                        if blocked:
                            if path not in reported_rejections:
                                reported_rejections.add(path)
                                logger.error(f"❌ [動態控制] {path} 啟動被拒絕: {blocked}")
                                _emit_lifecycle(path, "failed", blocked)

            for path in iter_hotplug_starts(
                current_status,
                running_grids_ref.keys(),
                _exists,
                _config_account_mode,
                live_trading_enabled(),
            ):
                reported_rejections.discard(path)
                logger.info(f"▶️ [動態控制] 檢測到 {path} 被設為啟用，正在異步啟動網格...")
                asyncio.create_task(run_single_grid_task(path, adapter_pool, logger, running_grids_ref))

    # DCA 熱插拔監控協程（與 active_grids_monitor 對稱，稽核 3.12/3.13/3.14）
    async def active_dca_monitor():
        logger.info("👀 DCA 動態控制監控器 (Hot-reload Monitor) 已啟動")
        reported_dca_rejections = set()
        while True:
            await asyncio.sleep(5)
            if not dca_control_file.exists():
                continue
            try:
                with open(dca_control_file, 'r', encoding='utf-8') as cf:
                    current_status = json.load(cf)
            except Exception:
                continue

            for running_path in list(running_dca_ref.keys()):
                if not current_status.get(running_path, False):
                    logger.info(f"🛑 [DCA 動態控制] 檢測到 {running_path} 被設為停用，正在停止 DCA 任務...")
                    dca_info = running_dca_ref.get(running_path)
                    if dca_info:
                        try:
                            await dca_info["coordinator"].stop()
                        except Exception as stop_err:
                            logger.error(f"⚠️ [DCA 動態控制] 停止 {running_path} 失敗: {stop_err}")
                        dca_info["task"].cancel()
                    logger.info(f"✅ [DCA 動態控制] {running_path} 已成功停止（持倉保留）。")
                    reported_dca_rejections.discard(running_path)

            def _dca_exists(path: str) -> bool:
                return _abs_config(path).exists()

            # 找不到設定檔 / start_rejection 即時發 failed（稽核 3.14）
            for path, enabled in current_status.items():
                if enabled and path not in running_dca_ref:
                    if not _dca_exists(path):
                        if path not in reported_dca_rejections:
                            reported_dca_rejections.add(path)
                            logger.error(f"❌ [DCA 動態控制] 找不到設定檔: {path}")
                            _emit_dca_lifecycle(path, "failed", f"找不到設定檔: {path}")
                    else:
                        blocked = start_rejection(
                            _dca_config_account_mode(path), live_trading_enabled()
                        )
                        if blocked:
                            if path not in reported_dca_rejections:
                                reported_dca_rejections.add(path)
                                logger.error(f"❌ [DCA 動態控制] {path} 啟動被拒絕: {blocked}")
                                _emit_dca_lifecycle(path, "failed", blocked)

            for path in iter_hotplug_starts(
                current_status,
                running_dca_ref.keys(),
                _dca_exists,
                _dca_config_account_mode,
                live_trading_enabled(),
            ):
                reported_dca_rejections.discard(path)
                logger.info(f"▶️ [DCA 動態控制] 檢測到 {path} 被設為啟用，正在異步啟動 DCA...")
                asyncio.create_task(
                    run_single_dca_task(
                        path, adapter_pool, logger, running_dca_ref, dca_ohlcv_caches
                    )
                )

    stagger_delay = 5
    initial_paths = [
        path for path, enabled in active_status.items()
        if enabled and _abs_config(path).exists()
        and not start_rejection(_config_account_mode(path), live_trading_enabled())
    ]
    for idx, path in enumerate(initial_paths):
        if idx > 0:
            logger.info(f"⏳ 等待 {stagger_delay} 秒後啟動下一個網格...")
            await asyncio.sleep(stagger_delay)
        asyncio.create_task(run_single_grid_task(path, adapter_pool, logger, running_grids_ref))

    initial_dca_paths = [
        path for path, enabled in active_dca_status.items()
        if enabled and _abs_config(path).exists()
        and not start_rejection(_dca_config_account_mode(path), live_trading_enabled())
    ]
    for idx, path in enumerate(initial_dca_paths):
        if idx > 0:
            logger.info(f"⏳ 等待 {stagger_delay} 秒後啟動下一個 DCA...")
            await asyncio.sleep(stagger_delay)
        asyncio.create_task(
            run_single_dca_task(path, adapter_pool, logger, running_dca_ref, dca_ohlcv_caches)
        )

    # 啟動監控協程
    monitor_task = asyncio.create_task(active_grids_monitor())
    dca_monitor_task = asyncio.create_task(active_dca_monitor())

    try:
        # 保持主循環持續運行
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        logger.info("🛑 主進程任務已被取消，系統正在關閉...")
    finally:
        # 取消監控
        monitor_task.cancel()
        dca_monitor_task.cancel()
        # 清理所有運行中的網格
        logger.info("🛑 正在關閉所有運行中的網格...")
        for path, grid_info in list(running_grids_ref.items()):
            try:
                logger.info(f"🧹 正在清理網格訂單: {path}")
                await grid_info["coordinator"].stop()
                grid_info["task"].cancel()
            except Exception as close_err:
                logger.error(f"⚠️ 清理 {path} 時出錯: {close_err}")
        # 清理所有運行中的 DCA（持倉保留）
        logger.info("🛑 正在關閉所有運行中的 DCA...")
        for path, dca_info in list(running_dca_ref.items()):
            try:
                logger.info(f"🧹 正在停止 DCA: {path}")
                await dca_info["coordinator"].stop()
                dca_info["task"].cancel()
            except Exception as close_err:
                logger.error(f"⚠️ 清理 DCA {path} 時出錯: {close_err}")

        logger.info("🔌 正在斷開交易所適配器連接並清理鎖檔案...")
        try:
            await adapter_pool.disconnect_all()
        except Exception as close_err:
            logger.error(f"⚠️ 斷開適配器時出錯: {close_err}")
        if os.path.exists(lock_file):
            os.remove(lock_file)
        logger.info("👋 多網格併發系統已安全退出。")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 收到鍵盤終止訊號，正在安全退出多網格系統...")