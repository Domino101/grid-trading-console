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

from core.adapters.exchanges.utils import setup_optimized_logging
from core.adapters.exchanges.models import ExchangeType
from core.adapters.exchanges import ExchangeFactory, ExchangeConfig
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

async def run_single_grid_task(config_path: str, shared_adapter, logger, running_grids_ref):
    """
    併發啟動並運行單個網格任務
    """
    logger.info(f"🟢 [初始化] 正在讀取配置檔案: {config_path}")
    try:
        # 1. 載入並解析配置
        with open(config_path, 'r', encoding='utf-8') as f:
            config_data = yaml.safe_load(f)
        grid_config = create_grid_config(config_data)

        # 🔥 自動獲取精度信息（若 YAML 中省略了 quantity_precision / price_decimals）
        await auto_fill_precision(grid_config, shared_adapter, logger)

        # 2. 建立該網格專有的網格組件，注入共享適配器
        strategy = GridStrategyImpl()
        
        # 關鍵點：傳入同一個 shared_adapter，引擎會複用這個適配器的緩存、限速器和訂閱管理器
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
            grid_state=grid_state
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
                logger.error(f"❌ [{grid_config.symbol}] 啟動檢查未通過，跳過此網格！")
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
        print("[GRID_LIFECYCLE] " + json.dumps({
            "event": "grid_lifecycle", "config_path": config_path,
            "status": "running", "symbol": grid_config.symbol,
        }, ensure_ascii=False, separators=(",", ":")), flush=True)

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
                    stats_dict = stats.to_display_dict()
                    print(f"[STATS_JSON][{grid_config.symbol}] {json.dumps(stats_dict, cls=JSONEncoder)}")
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
        print("[GRID_LIFECYCLE] " + json.dumps({
            "event": "grid_lifecycle", "config_path": config_path, "status": "stopped",
        }, ensure_ascii=False, separators=(",", ":")), flush=True)
        raise
    except Exception as e:
        logger.error(f"💥 運行網格任務時發生崩潰 [{config_path}]: {e}", exc_info=True)
        print("[GRID_LIFECYCLE] " + json.dumps({
            "event": "grid_lifecycle", "config_path": config_path, "status": "failed", "detail": str(e),
        }, ensure_ascii=False, separators=(",", ":")), flush=True)
    finally:
        if config_path in running_grids_ref:
            del running_grids_ref[config_path]

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

    # 獲取要運行的配置檔案列表
    config_paths = args.configs
    if not config_paths:
        # 如果未指定，則運行預設的 5 個幣種網格
        config_paths = [
            "config/grid/binance-neutral-perp-sol-usdc-100-160.yaml",
            "config/grid/binance-long-perp-sol-usdc.yaml",
            "config/grid/binance-long-perp-doge-usdc.yaml",
            "config/grid/binance-neutral-perp-tradoor-usdt.yaml",
            "config/grid/binance-neutral-perp-paxg-usdt.yaml"
        ]

    # 過濾掉不存在的配置檔案
    valid_paths = []
    for cp in config_paths:
        if Path(cp).exists():
            valid_paths.append(cp)
        else:
            logger.warning(f"⚠️ 找不到配置檔案: {cp}，跳過此網格。")

    if not valid_paths:
        logger.error("❌ 沒有可執行的有效網格配置檔案！程序退出。")
        sys.exit(1)

    logger.info(f"📋 共加載了 {len(valid_paths)} 個網格配置任務，準備共享同一個交易所適配器。")

    # 鎖文件註冊
    lock_file = acquire_multi_lock()

    # 1. 創建全域唯一的共享交易所適配器
    logger.info(f"🔗 正在基於首個配置初始化全域共享適配器...")
    try:
        with open(valid_paths[0], 'r', encoding='utf-8') as f:
            first_config_data = yaml.safe_load(f)
        shared_adapter = await create_exchange_adapter(first_config_data)
        logger.info("🔌 全域共享交易所適配器已成功連接並快取！")
    except Exception as init_err:
        logger.error(f"❌ 初始化共享交易所適配器失敗: {init_err}")
        if os.path.exists(lock_file):
            os.remove(lock_file)
        sys.exit(1)

    # 2. 初始化 active_grids.json 控制檔
    control_file = project_root / "config" / "active_grids.json"
    control_file.parent.mkdir(parents=True, exist_ok=True)
    
    active_status = {}
    if control_file.exists():
        try:
            with open(control_file, 'r', encoding='utf-8') as cf:
                active_status = json.load(cf)
        except Exception as e:
            logger.warning(f"⚠️ 讀取 active_grids.json 失敗: {e}，將重建控制檔。")
    
    # 確保所有 valid_paths 在 active_status 中都有初始記錄
    modified = False
    for path in valid_paths:
        if path not in active_status:
            active_status[path] = True
            modified = True
            
    if modified or not control_file.exists():
        try:
            with open(control_file, 'w', encoding='utf-8') as cf:
                json.dump(active_status, cf, indent=4, ensure_ascii=False)
            logger.info(f"📝 已建立/更新網格控制檔: {control_file}")
        except Exception as e:
            logger.error(f"❌ 寫入 active_grids.json 失敗: {e}")

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

    # 定義熱插拔控制監控協程
    async def active_grids_monitor():
        logger.info("👀 網格動態控制監控器 (Hot-reload Monitor) 已啟動")
        while True:
            await asyncio.sleep(5)
            if not control_file.exists():
                continue
            try:
                with open(control_file, 'r', encoding='utf-8') as cf:
                    current_status = json.load(cf)
            except Exception:
                continue

            for path in list(current_status.keys()):
                is_enabled = current_status[path]
                
                # 情況 1：控制檔設為 false，且該網格正處於運行狀態 -> 停止它
                if not is_enabled and path in running_grids_ref:
                    logger.info(f"🛑 [動態控制] 檢測到 {path} 被設為停用，正在優雅取消所有掛單並停用任務...")
                    grid_info = running_grids_ref.get(path)
                    if grid_info:
                        try:
                            # 1. 呼叫 stop() 優雅取消 Binance 掛單與清場
                            await grid_info["coordinator"].stop()
                        except Exception as stop_err:
                            logger.error(f"⚠️ [動態控制] 停止 {path} 失敗: {stop_err}")
                        # 2. 取消協程
                        grid_info["task"].cancel()
                    logger.info(f"✅ [動態控制] {path} 已成功停止並清空掛單。")
                    
                # 情況 2：控制檔設為 true，且該網格目前未在運行狀態 -> 啟動 it
                elif is_enabled and path not in running_grids_ref and path in valid_paths:
                    logger.info(f"▶️ [動態控制] 檢測到 {path} 被設為啟用，正在異步啟動網格...")
                    asyncio.create_task(run_single_grid_task(path, shared_adapter, logger, running_grids_ref))

    # 🔥 併發運行所有初始啟用的網格（錯開 2 秒避免 API burst）
    stagger_delay = 5  # 秒（🔥 Fix #5: 從2秒加大至5秒，避免5個網格啟動時 API burst）
    for idx, path in enumerate(valid_paths):
        if active_status.get(path, True):
            if idx > 0:
                logger.info(f"⏳ 等待 {stagger_delay} 秒後啟動下一個網格...")
                await asyncio.sleep(stagger_delay)
            asyncio.create_task(run_single_grid_task(path, shared_adapter, logger, running_grids_ref))

    # 啟動監控協程
    monitor_task = asyncio.create_task(active_grids_monitor())

    try:
        # 保持主循環持續運行
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        logger.info("🛑 主進程任務已被取消，系統正在關閉...")
    finally:
        # 取消監控
        monitor_task.cancel()
        # 清理所有運行中的網格
        logger.info("🛑 正在關閉所有運行中的網格...")
        for path, grid_info in list(running_grids_ref.items()):
            try:
                logger.info(f"🧹 正在清理網格訂單: {path}")
                await grid_info["coordinator"].stop()
                grid_info["task"].cancel()
            except Exception as close_err:
                logger.error(f"⚠️ 清理 {path} 時出錯: {close_err}")
        
        # 斷開適配器連接並清理鎖檔案
        logger.info("🔌 正在斷開全域共享適配器連接並清理鎖檔案...")
        try:
            await shared_adapter.disconnect()
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