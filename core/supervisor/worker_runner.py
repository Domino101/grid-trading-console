#!/usr/bin/env python3
"""
Worker Runner — 獨立子進程策略執行器 (P1-3)

每個 Worker 為獨立的作業系統進程，擁有自己的 Python 直譯器與 Asyncio Loop，
徹底解決單進程多網格的 GIL 瓶頸與 Fate Sharing（連坐崩潰）風險。

用法:
    python3 -m core.supervisor.worker_runner --config config/grid/binance-neutral-perp-sol.yaml
    python3 -m core.supervisor.worker_runner --config config/dca/dca-binance-btc.yaml --type dca
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
import time
from pathlib import Path

# 添加專案根目錄至 sys.path
project_root = Path(__file__).resolve().parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

try:
    from dotenv import load_dotenv
    load_dotenv(project_root / ".env", override=False)
except ImportError:
    pass

from core.adapters.exchanges.utils import setup_optimized_logging
from core.adapters.exchanges import ExchangeFactory, ExchangeConfig
from core.logging import get_system_logger
from core.infrastructure.database.async_writer import AsyncDatabaseWriter
from core.infrastructure.database.redis_support import RedisLeaderElection, leader_election_enabled
from core.infrastructure.identity import resolve_bot_id
from core.security.vault import decrypt_dict_secrets
from web_ui.backend.control_plane import publish_lifecycle, publish_runtime
from web_ui.backend.lifecycle import account_mode_from_yaml, live_trading_enabled, start_rejection

# 導入網格與 DCA 協調器及配置解析函式
from run_grid_trading import (
    create_grid_config,
    detect_market_type,
    create_exchange_adapter,
    auto_fill_precision,
)
from core.services.grid.coordinator import GridCoordinator
from core.services.grid.implementations import (
    GridStrategyImpl,
    GridEngineImpl,
    PositionTrackerImpl,
)
from core.services.grid.reserve import SpotReserveManager, check_spot_reserve_on_startup

from core.services.dca.config_loader import (
    auto_fill_dca_precision,
    dca_account_mode_from_yaml,
    load_dca_config,
)
from core.services.dca.coordinator import DcaCoordinator
from core.services.dca.execution import DcaOrderExecutor
from core.services.dca.market_data.ohlcv_cache import OhlcvCache
import yaml
import json

logger = get_system_logger()


def _infer_bot_type(config_path: str, explicit_type: str | None) -> str:
    if explicit_type and explicit_type.lower() in ("grid", "dca"):
        return explicit_type.lower()
    norm = config_path.replace("\\", "/").lower()
    if "/dca/" in norm or norm.startswith("dca"):
        return "dca"
    return "grid"


def _load_yaml(path: str) -> dict:
    p = Path(path)
    if not p.is_absolute():
        p = project_root / p
    with open(p, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return decrypt_dict_secrets(data or {})


class WorkerRunner:
    def __init__(self, config_path: str, bot_type: str):
        self.config_path = config_path
        self.bot_type = bot_type
        self.bot_id = Path(config_path).stem
        self.coordinator = None
        self.leader = None
        self.adapter = None
        self.shutdown_event = asyncio.Event()

    def _setup_signal_handlers(self, loop: asyncio.AbstractEventLoop):
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, lambda s=sig: self._on_signal(s))
            except (NotImplementedError, RuntimeError):
                # Windows 或特異線程環境 fallback
                signal.signal(sig, lambda s, f: loop.call_soon_threadsafe(self.shutdown_event.set))

    def _on_signal(self, sig):
        logger.info(f"🛑 [Worker {self.bot_id}] 收到信號 {signal.Signals(sig).name}，啟動優雅停機...")
        self.shutdown_event.set()

    async def run(self):
        loop = asyncio.get_running_loop()
        self._setup_signal_handlers(loop)

        # 啟動非同步資料庫寫入器
        await AsyncDatabaseWriter.instance().start()

        # Redis 領導者選舉防雙開
        if leader_election_enabled():
            self.leader = RedisLeaderElection(resolve_bot_id(self.config_path))
            if not self.leader.acquire():
                logger.error(f"❌ [Worker {self.bot_id}] 領導權已被另一進程持有，退出")
                publish_lifecycle(self.config_path, "failed", detail="leader election conflict")
                sys.exit(1)

        try:
            if self.bot_type == "dca":
                await self._run_dca()
            else:
                await self._run_grid()
        finally:
            await self._cleanup()

    async def _run_grid(self):
        logger.info(f"🚀 [Worker {self.bot_id}] 啟動網格交易子進程: {self.config_path}")
        data = _load_yaml(self.config_path)
        account_mode = account_mode_from_yaml(data)
        blocked = start_rejection(account_mode, live_trading_enabled())
        if blocked:
            logger.error(f"❌ [Worker {self.bot_id}] 啟動被拒絕: {blocked}")
            publish_lifecycle(self.config_path, "failed", detail=blocked)
            sys.exit(1)

        grid_config = create_grid_config(data)
        exchange_config = ExchangeConfig.from_yaml(self.config_path)
        market_type = detect_market_type(grid_config.symbol, exchange_config)
        self.adapter = create_exchange_adapter(exchange_config, market_type)

        await self.adapter.connect()
        auto_fill_precision(grid_config, self.adapter)

        strategy = GridStrategyImpl(grid_config)
        engine = GridEngineImpl(self.adapter, grid_config)
        tracker = PositionTrackerImpl()
        reserve_mgr = SpotReserveManager(grid_config)

        self.coordinator = GridCoordinator(
            grid_config,
            strategy,
            engine,
            tracker,
            reserve_manager=reserve_mgr,
            lifecycle_publisher=lambda st, **kw: publish_lifecycle(self.config_path, st, detail=kw.get("reason") or kw.get("symbol")),
        )

        try:
            await self.coordinator.recover_state_from_snapshot(self.config_path)
        except Exception as rec_err:
            logger.warning(f"⚠️ [Worker {self.bot_id}] 快照恢復異常 (繼續運行): {rec_err}")

        await self.coordinator.start()
        publish_lifecycle(self.config_path, "running", detail=grid_config.symbol)
        logger.info(f"✅ [Worker {self.bot_id}] 網格協調器啟動完成")

        # 心跳與統計落盤循環
        while not self.shutdown_event.is_set():
            try:
                await asyncio.wait_for(self.shutdown_event.wait(), timeout=5.0)
                break
            except asyncio.TimeoutError:
                pass

            try:
                stats = self.coordinator.get_statistics()
                stats_dict = {
                    "current_price": float(stats.current_price) if stats.current_price else None,
                    "target_position": float(stats.target_position),
                    "current_position": float(stats.current_position),
                    "total_grids": stats.total_grids,
                    "active_grids": stats.active_grids,
                    "total_orders": stats.total_orders,
                    "pending_buy_orders": stats.pending_buy_orders,
                    "pending_sell_orders": stats.pending_sell_orders,
                    "total_pending_orders": stats.total_pending_orders,
                    "filled_buy_orders": stats.filled_buy_orders,
                    "filled_sell_orders": stats.filled_sell_orders,
                    "completed_cycles": stats.completed_cycles,
                    "realized_pnl": float(stats.realized_pnl),
                    "unrealized_pnl": float(stats.unrealized_pnl),
                    "total_fees": float(stats.total_fees),
                    "net_pnl": float(stats.net_pnl),
                    "profit": {
                        "grid_profit": float(getattr(stats, "grid_profit", stats.realized_profit)),
                        "realized": float(stats.realized_profit),
                        "unrealized": float(stats.unrealized_profit),
                        "fees": float(stats.total_fees),
                        "net": float(stats.net_profit),
                        "rate": float(stats.profit_rate),
                    },
                    "symbol": grid_config.symbol,
                    "bot_id": self.bot_id,
                    "source": "worker_runtime",
                }
                publish_runtime(self.config_path, stats_dict)
            except Exception as st_err:
                logger.warning(f"⚠️ [Worker {self.bot_id}] 統計建構或快照發布異常: {st_err}")

    async def _run_dca(self):
        logger.info(f"🚀 [Worker {self.bot_id}] 啟動 DCA 交易子進程: {self.config_path}")
        data = _load_yaml(self.config_path)
        account_mode = dca_account_mode_from_yaml(data)
        blocked = start_rejection(account_mode, live_trading_enabled())
        if blocked:
            logger.error(f"❌ [Worker {self.bot_id}] 啟動被拒絕: {blocked}")
            publish_lifecycle(self.config_path, "failed", detail=blocked)
            sys.exit(1)

        dca_config = load_dca_config(data)
        symbol = dca_config.symbol
        exchange = dca_config.exchange

        exchange_config = ExchangeConfig.from_yaml(self.config_path)
        market_type = detect_market_type(symbol, exchange_config)
        self.adapter = create_exchange_adapter(exchange_config, market_type)
        await self.adapter.connect()
        auto_fill_dca_precision(dca_config, self.adapter, logger)

        cache = OhlcvCache(
            lambda _ex, sym, tf, limit, _ad=self.adapter: _ad.get_ohlcv(sym, tf, limit=limit)
        )
        executor = DcaOrderExecutor(dca_config, self.bot_id, self.adapter)
        self.coordinator = DcaCoordinator(
            dca_config,
            self.bot_id,
            executor,
            ohlcv_provider=cache.provider(exchange),
            lifecycle_publisher=lambda st: publish_lifecycle(self.config_path, st, detail=symbol),
        )

        try:
            await self.coordinator.recover_state_from_snapshot(self.config_path)
        except Exception as rec_err:
            logger.warning(f"⚠️ [Worker {self.bot_id}] DCA 快照恢復異常: {rec_err}")

        self.coordinator.start()
        publish_lifecycle(self.config_path, "running", detail=symbol)
        logger.info(f"✅ [Worker {self.bot_id}] DCA 協調器已就緒")

        interval = max(int(dca_config.monitoring_interval), 5)
        while not self.shutdown_event.is_set():
            try:
                await asyncio.wait_for(self.shutdown_event.wait(), timeout=float(interval))
                break
            except asyncio.TimeoutError:
                pass

            if self.coordinator.state.status.value == "failed":
                logger.error(f"❌ [Worker {self.bot_id}] DCA 狀態已轉為 failed，準備退出")
                break

            try:
                last_price = await executor.last_price()
            except Exception:
                last_price = None

            try:
                stats = self.coordinator.build_statistics(last_price)
                stats_dict = stats.to_display_dict()
                stats_dict["symbol"] = symbol
                stats_dict["exchange"] = exchange
                stats_dict["source"] = "worker_runtime"
                publish_runtime(self.config_path, stats_dict)
            except Exception as st_err:
                logger.warning(f"⚠️ [Worker {self.bot_id}] DCA 統計建構或快照發布異常: {st_err}")

    async def _cleanup(self):
        logger.info(f"🧹 [Worker {self.bot_id}] 執行資源清理...")
        if self.coordinator:
            try:
                await self.coordinator.stop()
            except Exception as stop_err:
                logger.warning(f"⚠️ [Worker {self.bot_id}] 停止協調器異常: {stop_err}")

        if self.adapter:
            try:
                await self.adapter.disconnect()
            except Exception:
                pass

        if self.leader:
            try:
                self.leader.release()
            except Exception:
                pass

        try:
            await AsyncDatabaseWriter.instance().stop()
        except Exception:
            pass

        publish_lifecycle(self.config_path, "stopped")
        logger.info(f"🏁 [Worker {self.bot_id}] 清理完畢，退出進程")


def main():
    parser = argparse.ArgumentParser(description="獨立策略 Worker 執行器 (P1-3)")
    parser.add_argument("--config", required=True, help="策略配置檔案路徑 (YAML)")
    parser.add_argument("--type", choices=["grid", "dca"], default=None, help="策略類型")
    parser.add_argument("--debug", action="store_true", help="啟用 DEBUG 日誌")
    args = parser.parse_args()

    setup_optimized_logging(use_colored=True)
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    bot_type = _infer_bot_type(args.config, args.type)
    runner = WorkerRunner(args.config, bot_type)

    try:
        asyncio.run(runner.run())
    except KeyboardInterrupt:
        logger.info("🛑 收到鍵盤中斷，結束 Worker")
    except Exception as e:
        logger.critical(f"💥 Worker 發生致命崩潰: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
