"""Unit tests for LedgerRepository and normalized trade fills / OCC."""

import os
import tempfile
import time
import unittest

from core.infrastructure.database.ledger_repository import LedgerRepository
from core.infrastructure.database.models import BotPosition, BotRecord, TradeFill
from web_ui.backend.runtime_repository import RuntimeSnapshotRepository


class TestLedgerRepository(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp_dir.name, "test_ledger.sqlite3")
        self.repo = LedgerRepository(self.db_path)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_register_and_get_bot(self):
        bot = BotRecord(
            bot_id="bot-sol-01",
            strategy_type="grid",
            symbol="SOL/USDC:USDC",
            exchange="binance",
            account_mode="paper",
            status="running",
            created_at=time.time(),
            config_path="config/grid/sol.yaml",
        )
        self.repo.register_or_update_bot(bot)

        retrieved = self.repo.get_bot("bot-sol-01")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.symbol, "SOL/USDC:USDC")
        self.assertEqual(retrieved.config_path, "config/grid/sol.yaml")

        by_config = self.repo.get_bot_by_config("config/grid/sol.yaml")
        self.assertIsNotNone(by_config)
        self.assertEqual(by_config.bot_id, "bot-sol-01")

    def test_immutable_fills_and_rehydration(self):
        bot_id = "bot-dca-pnut"
        now = time.time()

        # 1. Base order fill: Buy 100 PNUT at 0.05
        f1 = TradeFill(
            fill_id="f1",
            bot_id=bot_id,
            client_order_id="dca_123_base",
            side="buy",
            role="base",
            price=0.05,
            quantity=100.0,
            executed_at=now,
            fee=0.001,
        )
        # 2. Safety order fill: Buy 200 PNUT at 0.04
        f2 = TradeFill(
            fill_id="f2",
            bot_id=bot_id,
            client_order_id="dca_123_so01",
            side="buy",
            role="safety",
            price=0.04,
            quantity=200.0,
            executed_at=now + 10,
            fee=0.002,
        )
        self.repo.record_fill(f1)
        self.repo.record_fill(f2)

        fills = self.repo.get_fills(bot_id)
        self.assertEqual(len(fills), 2)
        self.assertEqual(fills[0].fill_id, "f1")
        self.assertEqual(fills[1].fill_id, "f2")

        # 3. Rehydrate position from fills
        # Total cost = 100*0.05 + 200*0.04 = 5 + 8 = 13. Qty = 300. Avg cost = 13 / 300 = 0.043333333333333335
        rehydrated = self.repo.rehydrate_position_from_fills(bot_id)
        self.assertAlmostEqual(rehydrated.quantity, 300.0)
        self.assertAlmostEqual(rehydrated.total_cost, 13.0)
        self.assertAlmostEqual(rehydrated.average_cost, 13.0 / 300.0)
        self.assertAlmostEqual(rehydrated.total_fees, 0.003)
        self.assertEqual(rehydrated.active_direction, "long")

    def test_optimistic_concurrency_control_occ(self):
        bot_id = "bot-occ-test"
        pos = BotPosition(
            bot_id=bot_id,
            active_direction="long",
            quantity=10.0,
            average_cost=100.0,
            total_cost=1000.0,
            completed_cycles=1,
            version=1,
            updated_at=time.time(),
        )
        # First write (insert version 1)
        res = self.repo.update_position_occ(bot_id, pos, expected_version=1)
        self.assertTrue(res)

        stored = self.repo.get_position(bot_id)
        self.assertEqual(stored.version, 1)
        self.assertEqual(stored.quantity, 10.0)

        # Successful OCC update with expected_version=1 -> becomes version 2
        pos.quantity = 20.0
        res2 = self.repo.update_position_occ(bot_id, pos, expected_version=1)
        self.assertTrue(res2)
        stored2 = self.repo.get_position(bot_id)
        self.assertEqual(stored2.version, 2)
        self.assertEqual(stored2.quantity, 20.0)

        # Conflicting OCC update with stale version 1 -> rejected!
        pos_stale = BotPosition(
            bot_id=bot_id,
            quantity=999.0,
        )
        res_conflict = self.repo.update_position_occ(bot_id, pos_stale, expected_version=1)
        self.assertFalse(res_conflict)

        # Confirm data was not overwritten
        stored_unmodified = self.repo.get_position(bot_id)
        self.assertEqual(stored_unmodified.quantity, 20.0)
        self.assertEqual(stored_unmodified.version, 2)

    def test_runtime_repository_facade_backward_compatibility(self):
        facade = RuntimeSnapshotRepository(self.db_path)
        stats = {
            "bot_id": "facade-bot",
            "symbol": "BTC/USDT",
            "profit": {"realized": 45.6, "fees": 1.2},
            "position": {"quantity": 0.5, "average_cost": 50000.0, "total_cost": 25000.0},
            "cycles": 3,
            "fills": [
                {"price": 50000.0, "quantity": 0.5, "side": "buy", "role": "base", "client_order_id": "c1"}
            ],
        }
        ts = facade.save("config/grid/btc.yaml", stats)
        self.assertIsInstance(ts, float)

        all_snaps = facade.load_all()
        self.assertIn("config/grid/btc.yaml", all_snaps)
        snap, snap_ts = all_snaps["config/grid/btc.yaml"]
        self.assertEqual(snap["symbol"], "BTC/USDT")
        self.assertEqual(snap["profit"]["realized"], 45.6)

        history = facade.history("config/grid/btc.yaml", since=0)
        self.assertEqual(len(history), 1)

        # Check underlying ledger table also received data
        bot = self.repo.get_bot("facade-bot")
        self.assertIsNotNone(bot)
        self.assertEqual(bot.symbol, "BTC/USDT")
        fills = self.repo.get_fills("facade-bot")
        self.assertEqual(len(fills), 1)
        self.assertEqual(fills[0].price, 50000.0)


if __name__ == "__main__":
    unittest.main()
