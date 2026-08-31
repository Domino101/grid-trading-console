"""Unit tests for DatabaseMigrator and AsyncDatabaseWriter."""

import json
import os
import tempfile
import time
import unittest

from core.infrastructure.database.async_writer import AsyncDatabaseWriter
from core.infrastructure.database.ledger_repository import LedgerRepository
from core.infrastructure.database.migrator import DatabaseMigrator
from web_ui.backend.lifecycle import connect_sqlite


class TestDatabaseMigratorAndWriter(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp_dir.name, "migration_test.sqlite3")

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_legacy_data_migration(self):
        # 1. Create legacy table and insert mock legacy payload
        with connect_sqlite(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE grid_runtime_snapshots (
                    config_path TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)
            legacy_payload = {
                "bot_id": "legacy-grid-eth",
                "symbol": "ETH/USDT",
                "exchange": "binance",
                "account_mode": "paper",
                "position": {"quantity": 1.2, "average_cost": 2500.0, "total_cost": 3000.0},
                "profit": {"realized": 88.0, "fees": 2.5},
                "cycles": 12,
                "fills": [
                    {"client_order_id": "f_101", "side": "buy", "role": "grid", "price": 2400.0, "quantity": 0.5, "fee": 0.1},
                    {"client_order_id": "f_102", "side": "buy", "role": "grid", "price": 2600.0, "quantity": 0.7, "fee": 0.15},
                ],
            }
            conn.execute(
                "INSERT INTO grid_runtime_snapshots(config_path, payload, updated_at) VALUES (?, ?, ?)",
                ("config/grid/eth.yaml", json.dumps(legacy_payload), time.time()),
            )

        # 2. Run Migration
        stats = DatabaseMigrator.migrate(self.db_path)
        self.assertEqual(stats["bots_migrated"], 1)
        self.assertEqual(stats["positions_migrated"], 1)
        self.assertEqual(stats["fills_migrated"], 2)

        # 3. Verify Ledger State
        repo = LedgerRepository(self.db_path)
        bot = repo.get_bot("legacy-grid-eth")
        self.assertIsNotNone(bot)
        self.assertEqual(bot.symbol, "ETH/USDT")

        pos = repo.get_position("legacy-grid-eth")
        self.assertIsNotNone(pos)
        self.assertEqual(pos.quantity, 1.2)
        self.assertEqual(pos.completed_cycles, 12)
        self.assertEqual(pos.realized_profit, 88.0)

        fills = repo.get_fills("legacy-grid-eth")
        self.assertEqual(len(fills), 2)
        self.assertEqual(fills[0].price, 2400.0)

    def test_legacy_grid_payload_cycles_and_snapshot_backfill(self):
        """舊版 grid 快照：cycles 在 filled_orders.cycles（無 top-level），且無逐筆 fills。

        遷移後 bot_positions.completed_cycles 必須忠實回補，bot_snapshots 必須寫入
        （P1-2 穩定 bot_id 恢復主路徑），重跑須冪等（不重複 append history/bump version）。
        """
        with connect_sqlite(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE grid_runtime_snapshots (
                    config_path TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)
            legacy_payload = {
                "symbol": "SOL/USDC:USDC",
                "exchange": "binance",
                "account_mode": "paper",
                "status": "running",
                "grid_position": 0.5,
                "filled_orders": {"buy": 6, "sell": 4, "cycles": 4},
                "profit": {"realized": 12.3, "grid_profit": 12.3, "fees": 1.1},
            }
            conn.execute(
                "INSERT INTO grid_runtime_snapshots(config_path, payload, updated_at) VALUES (?, ?, ?)",
                ("config/grid/sol-usdc-100-160.yaml", json.dumps(legacy_payload), time.time()),
            )

        stats = DatabaseMigrator.migrate(self.db_path)
        self.assertEqual(stats["bots_migrated"], 1)
        self.assertEqual(stats["positions_migrated"], 1)
        self.assertEqual(stats["fills_migrated"], 0)

        repo = LedgerRepository(self.db_path)
        pos = repo.get_position("sol-usdc-100-160")
        self.assertIsNotNone(pos)
        self.assertEqual(pos.completed_cycles, 4)  # 由 filled_orders.cycles 回補

        snap = repo.load_snapshot_by_bot_id("sol-usdc-100-160")
        self.assertIsNotNone(snap)
        self.assertEqual(snap[0]["filled_orders"]["cycles"], 4)

        # 重跑冪等：snapshot version 不變、fills/bots 不重複
        DatabaseMigrator.migrate(self.db_path)
        snap2 = repo.load_snapshot_by_bot_id("sol-usdc-100-160")
        self.assertIsNotNone(snap2)
        self.assertEqual(snap2[0]["filled_orders"]["cycles"], 4)
        self.assertEqual(repo.get_fills("sol-usdc-100-160"), [])

    def test_async_database_writer_batching(self):
        repo = LedgerRepository(self.db_path)
        writer = AsyncDatabaseWriter(repository=repo, flush_interval=0.5, max_batch_size=5)

        # Enqueue 3 items
        writer.enqueue_snapshot("bot-1", "config/1.yaml", {"profit": {"net": 10}})
        writer.enqueue_snapshot("bot-2", "config/2.yaml", {"profit": {"net": 20}})
        writer.enqueue_snapshot("bot-1", "config/1.yaml", {"profit": {"net": 15}})

        flushed = writer.flush_sync()
        self.assertEqual(flushed, 2)  # Deduplicated bot-1 to latest

        all_snaps = repo.load_all_snapshots()
        self.assertIn("config/1.yaml", all_snaps)
        self.assertIn("config/2.yaml", all_snaps)
        self.assertEqual(all_snaps["config/1.yaml"][0]["profit"]["net"], 15)


if __name__ == "__main__":
    unittest.main()
