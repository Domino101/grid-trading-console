"""PAPER 端到端測試（Phase 3 稽核 3.10 / 3.11 / 3.19）。

用真實 `PaperExchangeAdapter`（合成 OHLCV + 本機撮合）加上共享 `OhlcvCache`
跑完整的 DCA 狀態機：armed(0 掛單) → 指標觸發 → 基礎單成交 → ≥2 張安全單成交 →
TP 成交 → completed，全程斷言 INV-1..INV-4。
"""

import asyncio
import logging
import os
import sys
import tempfile
import unittest
from decimal import Decimal as D

from core.adapters.exchanges.adapters.paper import create_paper_adapter
from core.services.dca.coordinator import DcaCoordinator, EntryMonitor
from core.services.dca.execution import DcaOrderExecutor
from core.services.dca.indicators.config import PriceChangeConfig
from core.services.dca.indicators.price_change import price_change_series
from core.services.dca.ladder import build_ladder, quantize_price, take_profit_price
from core.services.dca.market_data.ohlcv_cache import OhlcvCache
from core.services.dca.models.dca_config import DcaConfig, EntryConfig, EntryMode
from core.services.dca.models.dca_state import DcaStatus

SYMBOL = "ETH/USDC:USDC"
BOT_ID = "bot-phase3-e2e"


def _build_config(threshold: float) -> DcaConfig:
    return DcaConfig(
        exchange="binance",
        symbol=SYMBOL,
        base_order_amount=D("20"),
        direction="long",
        account_mode="paper",
        quantity_precision=6,
        price_decimals=2,
        max_safety_orders=3,
        price_deviation_percent=D("0.8"),
        price_deviation_multiplier=D("1.2"),
        order_size_multiplier=D("1.0"),
        active_safety_orders_window=2,
        take_profit_percent=D("1.0"),
        repeat_after_take_profit=False,
        cooldown_seconds=0,
        entry=EntryConfig(
            mode=EntryMode.INDICATOR,
            indicators=(PriceChangeConfig(
                lookback_bars=24,
                long_threshold_percent=threshold,
            ),),
            confirm_bars=1,
        ),
    )


class PaperOhlcvTests(unittest.IsolatedAsyncioTestCase):
    async def test_ohlcv_deterministic_and_synthetic(self):
        """稽核 3.10/3.11：同 seed 兩次呼叫相同，且含 synthetic 標記。"""
        paper = create_paper_adapter(
            {"grid_system": {"exchange": "binance", "symbol": SYMBOL,
                             "account_mode": "paper"}},
            ticker_provider=lambda _s: D("2500"),
        )
        await paper.connect()
        try:
            first = await paper.get_ohlcv(SYMBOL, "15m", limit=100)
            second = await paper.get_ohlcv(SYMBOL, "15m", limit=100)
            self.assertEqual(len(first), 100)
            self.assertEqual(len(second), 100)
            self.assertEqual(
                [(c.close, c.high, c.low, c.volume) for c in first],
                [(c.close, c.high, c.low, c.volume) for c in second],
            )
            self.assertTrue(all(c.raw_data.get("synthetic") is True for c in first))
        finally:
            await paper.disconnect()


class SupervisorPoolTests(unittest.IsolatedAsyncioTestCase):
    """稽核 3.16：1 個網格 + 1 個 DCA 同交易所 → 共用單一 adapter 實例。"""

    async def test_single_dca_task_isolates_exception(self):
        """稽核 3.15（R-1）：run_single_dca_task 內部錯誤不得向上拋、不得拖垮呼叫方。"""
        import run_multiple_grids as mg

        missing_path = "/tmp/_dca_missing_config.yaml"
        running_ref = {}
        logger = logging.getLogger("test_dca_supervisor")
        # 頂層 except 必須吞掉所有非 CancelledError；若向上拋此 await 會 raise。
        await mg.run_single_dca_task(
            missing_path, mg.ExchangeAdapterPool(), logger, running_ref, {}
        )
        self.assertNotIn(missing_path, running_ref)

    async def test_pool_shares_adapter_between_grid_and_dca(self):
        import run_multiple_grids as mg

        created = []

        async def fake_create_adapter(config_data):
            adapter = object()
            created.append(adapter)
            return adapter

        original = mg.create_exchange_adapter
        mg.create_exchange_adapter = fake_create_adapter
        try:
            with tempfile.TemporaryDirectory() as root:
                grid_dir = os.path.join(root, "grid")
                dca_dir = os.path.join(root, "dca")
                os.makedirs(grid_dir, exist_ok=True)
                os.makedirs(dca_dir, exist_ok=True)
                grid_path = os.path.join(grid_dir, "g.yaml")
                dca_path = os.path.join(dca_dir, "d.yaml")
                with open(grid_path, "w", encoding="utf-8") as f:
                    f.write(
                        "grid_system:\n"
                        "  exchange: binance\n"
                        "  symbol: BTC/USDT\n"
                        "  account_mode: paper\n"
                    )
                with open(dca_path, "w", encoding="utf-8") as f:
                    f.write(
                        "dca:\n"
                        "  exchange: binance\n"
                        "  symbol: ETH/USDC:USDC\n"
                        "  account_mode: paper\n"
                    )
                pool = mg.ExchangeAdapterPool()
                grid_adapter = await pool.get(grid_path)
                dca_adapter = await pool.get(dca_path)
                self.assertIs(grid_adapter, dca_adapter)
                self.assertEqual(len(created), 1)
                await pool.disconnect_all()
        finally:
            mg.create_exchange_adapter = original


    async def test_run_single_dca_task_reaches_averaging_without_await_none(self):
        """Phase 5 回歸：run_single_dca_task 對同步函式 auto_fill_dca_precision
        誤用 await（await None → TypeError）曾使每台 PAPER DCA 一啟動即 failed。

        以真實 PaperExchangeAdapter + 真實 YAML 跑完整 task：
        必須註冊進 running_ref 且狀態機進入運行狀態（immediate 入場 →
        基礎單即成交 → averaging），而非在 auto_fill 行異常終止。
        """
        import run_multiple_grids as mg

        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        rel_path = "config/dca/dca-test-phase5-supervisor.yaml"
        abs_path = os.path.join(project_root, rel_path)
        yaml_text = (
            "dca:\n"
            "  exchange: binance\n"
            f"  symbol: {SYMBOL}\n"
            "  direction: long\n"
            "  account_mode: paper\n"
            "  quantity_precision: 6\n"
            "  price_decimals: 2\n"
            "  base_order_amount: '20'\n"
            "  max_safety_orders: 5\n"
            "  price_deviation_percent: '1.0'\n"
            "  price_deviation_multiplier: '1.1'\n"
            "  order_size_multiplier: '1.0'\n"
            "  take_profit_percent: '1.0'\n"
            "  active_safety_orders_window: 2\n"
            "  monitoring_interval: 5\n"
            "  repeat_after_take_profit: false\n"
            "  entry:\n"
            "    mode: immediate\n"
        )

        paper = create_paper_adapter(
            {"grid_system": {"exchange": "binance", "symbol": SYMBOL,
                             "account_mode": "paper"}},
            ticker_provider=lambda _s: D("2500"),
        )
        await paper.connect()

        class _FakePool:
            async def get(self, _config_path):
                return paper

        running_ref = {}
        logger = logging.getLogger("test_dca_supervisor_p5")
        task = None
        try:
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(yaml_text)
            task = asyncio.create_task(
                mg.run_single_dca_task(rel_path, _FakePool(), logger, running_ref, {})
            )
            deadline = asyncio.get_event_loop().time() + 15
            while rel_path not in running_ref and asyncio.get_event_loop().time() < deadline:
                await asyncio.sleep(0.05)
            self.assertIn(rel_path, running_ref, "DCA task 未註冊（疑在 auto_fill 行異常終止）")

            coordinator = running_ref[rel_path]["coordinator"]
            # immediate 入場 + PAPER 即成交 → 應在數個監控週期內進入 averaging
            deadline = asyncio.get_event_loop().time() + 25
            while coordinator.state.status is not DcaStatus.AVERAGING \
                    and asyncio.get_event_loop().time() < deadline:
                if task.done():
                    break
                await asyncio.sleep(0.05)
            self.assertEqual(
                coordinator.state.status, DcaStatus.AVERAGING,
                f"狀態機未進入 averaging: {coordinator.state.status} "
                f"（last_error={coordinator.state.last_error!r}）",
            )
            # INV-2：averaging 中恰好 1 筆 TP；基礎單已成交（部位 > 0）
            tp_count = sum(
                1 for o in coordinator._active.values()
                if o.role.name == "TAKE_PROFIT"
            )
            self.assertEqual(tp_count, 1)
            self.assertGreater(coordinator.state.position_quantity, 0)
        finally:
            if rel_path in running_ref:
                try:
                    await running_ref[rel_path]["coordinator"].stop()
                except Exception:
                    pass
            if task is not None:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            if os.path.exists(abs_path):
                os.remove(abs_path)
            await paper.disconnect()


class DcaRuntimeSnapshotPublishTests(unittest.TestCase):
    """稽核 3.17：publish_runtime() 後 snapshot 含 config/dca 鍵且 JSON 可還原。"""

    def test_publish_runtime_writes_dca_config_path(self):
        backend = os.path.join(os.path.dirname(__file__), "..", "web_ui", "backend")
        if backend not in sys.path:
            sys.path.insert(0, backend)
        from web_ui.backend import control_plane
        from web_ui.backend.command_repository import GridCommandRepository
        from web_ui.backend.runtime_repository import RuntimeSnapshotRepository

        RuntimeSnapshotRepository.reset_instance()
        GridCommandRepository.reset_instance()
        try:
            with tempfile.TemporaryDirectory() as root:
                database_path = os.path.join(root, "runtime.sqlite3")
                RuntimeSnapshotRepository._instance = RuntimeSnapshotRepository(database_path)
                GridCommandRepository._instance = GridCommandRepository(database_path)
                config_path = "config/dca/dca-binance-long-eth-test.yaml"
                control_plane.publish_runtime(
                    config_path,
                    {"status": "armed", "symbol": "ETH/USDC:USDC",
                     "source": "dca_engine_runtime"},
                )
                loaded = RuntimeSnapshotRepository.instance().load_all()
                self.assertIn(config_path, loaded)
                payload, _ = loaded[config_path]
                self.assertEqual(payload["symbol"], "ETH/USDC:USDC")
                self.assertEqual(payload["status"], "armed")
                self.assertEqual(payload["source"], "dca_engine_runtime")
        finally:
            RuntimeSnapshotRepository.reset_instance()
            GridCommandRepository.reset_instance()


class PaperE2ETests(unittest.IsolatedAsyncioTestCase):
    async def test_full_round_inv1_to_inv4(self):
        """稽核 3.19：armed → 觸發 → 基礎單 → ≥2 安全單 → TP → completed。"""
        prices = {SYMBOL: D("2500")}
        paper = create_paper_adapter(
            {"grid_system": {"exchange": "binance", "symbol": SYMBOL,
                             "account_mode": "paper"}},
            ticker_provider=lambda _s: prices[SYMBOL],
        )
        await paper.connect()
        paper.set_last_price(SYMBOL, D("2500"))
        try:
            # 由 PAPER 合成 K 線計算一個「必定成立」的 PriceChange 門檻
            raw = await paper.get_ohlcv(SYMBOL, "15m", limit=100)
            closes = [float(c.close) for c in raw][:-1]  # cache 會丟掉未收線尾棒
            change = price_change_series(closes, 24)[-1]
            config = _build_config(threshold=change + 0.5)

            cache = OhlcvCache(
                lambda _ex, sym, tf, limit, adapter=paper:
                    adapter.get_ohlcv(sym, tf, limit=limit)
            )
            monitor = EntryMonitor(config, ohlcv_provider=cache.provider("binance"))
            executor = DcaOrderExecutor(config, BOT_ID, paper)
            published = []
            coord = DcaCoordinator(
                config, BOT_ID, executor,
                entry_monitor=monitor,
                lifecycle_publisher=published.append,
            )

            # ── INV-1：armed 零掛單 ──
            coord.begin()
            self.assertIs(coord.state.status, DcaStatus.ARMED)
            self.assertEqual(await paper.get_open_orders(SYMBOL), [])
            self.assertEqual(len(paper._all_orders), 0)

            # 觸發 → triggering
            await coord.step()
            self.assertIs(coord.state.status, DcaStatus.TRIGGERING)
            self.assertEqual(await paper.get_open_orders(SYMBOL), [])

            # 下基礎單（PAPER 在現價立即成交）
            await coord.step()
            # 下一輪 REST 對帳 → 進入 averaging
            await coord.step()
            self.assertIs(coord.state.status, DcaStatus.AVERAGING)

            # ── INV-2 / INV-3：進入 averaging 後 TP 恰 1 筆、安全單 ≤ 窗口 ──
            open_orders = await paper.get_open_orders(SYMBOL)
            role_of_tp = [o for o in open_orders if o.client_id.endswith("_tp")]
            safety_open = [o for o in open_orders if "_so" in (o.client_id or "")]
            self.assertEqual(len(role_of_tp), 1)
            self.assertLessEqual(len(safety_open), config.active_safety_orders_window)
            self.assertLessEqual(
                coord.state.filled_safety_count(), config.max_safety_orders)

            # 計算required（INV-4 上界用）
            plan = build_ladder(config, D("2500"), "long")
            required = plan.required_capital

            # 成交 ≥2 張安全單：先壓到 2450（會成交 so01/so02），再補第三張
            paper.set_last_price(SYMBOL, D("2450"))
            await coord.step()
            self.assertGreaterEqual(coord.state.filled_safety_count(), 2)
            # INV-4：已下單名目 ≤ required
            self.assertLessEqual(coord.state.total_cost, required)

            paper.set_last_price(SYMBOL, D("2420"))
            await coord.step()
            self.assertEqual(coord.state.filled_safety_count(),
                             config.max_safety_orders)

            # ── 止盈：以 TP 掛單的量化價格驅動成交 ──
            avg = coord.state.average_cost
            self.assertIsNotNone(avg)
            tp_price = quantize_price(
                take_profit_price(avg, config.take_profit_percent, "long"),
                config.price_decimals,
            )
            paper.set_last_price(SYMBOL, tp_price)
            await coord.step()

            self.assertIs(coord.state.status, DcaStatus.COMPLETED)
            self.assertEqual(await paper.get_open_orders(SYMBOL), [])
            self.assertEqual(coord.state.position_quantity, D("0"))
            self.assertIn(DcaStatus.COMPLETED.value, published)
            # INV-4：止盈後累計成本仍不得超過 required
            self.assertLessEqual(coord.state.total_cost, required)
        finally:
            await paper.disconnect()


class DetailWorkbenchSnapshotTests(unittest.IsolatedAsyncioTestCase):
    """Phase 7：詳情工作台快照（訂單/成交/階梯槽位）與 tp_replaced 事件。"""

    async def _drive_to_averaging(self, paper):
        raw = await paper.get_ohlcv(SYMBOL, "15m", limit=100)
        closes = [float(c.close) for c in raw][:-1]
        change = price_change_series(closes, 24)[-1]
        config = _build_config(threshold=change + 0.5)
        cache = OhlcvCache(
            lambda _ex, sym, tf, limit, adapter=paper:
                adapter.get_ohlcv(sym, tf, limit=limit)
        )
        monitor = EntryMonitor(config, ohlcv_provider=cache.provider("binance"))
        executor = DcaOrderExecutor(config, BOT_ID, paper)
        published = []
        coord = DcaCoordinator(
            config, BOT_ID, executor,
            entry_monitor=monitor,
            lifecycle_publisher=published.append,
        )
        coord.begin()
        await coord.step()  # trigger
        await coord.step()  # place base
        await coord.step()  # reconcile → averaging
        return coord

    async def test_snapshots_and_tp_replaced_event(self):
        prices = {SYMBOL: D("2500")}
        paper = create_paper_adapter(
            {"grid_system": {"exchange": "binance", "symbol": SYMBOL,
                             "account_mode": "paper"}},
            ticker_provider=lambda _s: prices[SYMBOL],
        )
        await paper.connect()
        paper.set_last_price(SYMBOL, D("2500"))
        try:
            coord = await self._drive_to_averaging(paper)
            self.assertIs(coord.state.status, DcaStatus.AVERAGING)

            # ── open_orders_snapshot：含恰好 1 筆 TP（INV-2）＋安全單 ──
            orders = coord.open_orders_snapshot()
            self.assertTrue(all("role" in o and "price" in o for o in orders))
            tp_rows = [o for o in orders if o["role"] == "take_profit"]
            self.assertEqual(len(tp_rows), 1)

            # ── ladder_slots_snapshot：基礎單 index 0 + 安全單槽位（三態）──
            slots = coord.ladder_slots_snapshot()
            self.assertTrue(any(s["index"] == 0 and s["role"] == "base" for s in slots))
            self.assertTrue(all(
                s["status"] in ("filled", "active", "waiting", "skipped")
                for s in slots
            ))

            # ── fills_snapshot：至少基礎單成交，含手續費與累積均價欄位 ──
            fills = coord.fills_snapshot()
            self.assertGreaterEqual(len(fills), 1)
            base_fill = fills[0]
            for key in ("role", "price", "quantity", "fee",
                        "cumulative_avg_price", "occurred_at"):
                self.assertIn(key, base_fill)

            # ── 成交安全單 → 觸發 tp_replaced 事件（稽核 7.3）──
            paper.set_last_price(SYMBOL, D("2450"))
            await coord.step()
            self.assertGreaterEqual(coord.state.filled_safety_count(), 1)
            event_names = {name for name, _ in coord.events}
            self.assertIn("tp_replaced", event_names)

            # 安全單成交後 fills 增加，且該筆帶累積均價
            fills_after = coord.fills_snapshot()
            self.assertGreater(len(fills_after), len(fills))
            self.assertIsNotNone(fills_after[-1]["cumulative_avg_price"])
        finally:
            await paper.disconnect()


if __name__ == "__main__":
    unittest.main()
