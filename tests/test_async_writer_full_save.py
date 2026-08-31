"""P1-5: AsyncDatabaseWriter full-save path and bounded retry queue."""

import os
import tempfile
import unittest

from core.infrastructure.database.async_writer import AsyncDatabaseWriter
from web_ui.backend.runtime_repository import RuntimeSnapshotRepository


class TestAsyncWriterFullSave(unittest.TestCase):
    def test_full_save_preserves_bot_fills_position_and_snapshot(self):
        """Routing through the writer must keep the FULL save semantics."""
        with tempfile.TemporaryDirectory() as d:
            repo = RuntimeSnapshotRepository(os.path.join(d, "r.sqlite3"))
            writer = AsyncDatabaseWriter(repository=repo)

            stats = {
                "bot_id": "writer-bot",
                "symbol": "BTC/USDT",
                "profit": {"realized": 10.0, "fees": 0.5},
                "position": {"quantity": 0.2, "average_cost": 60000.0, "total_cost": 12000.0},
                "cycles": 2,
                "fills": [
                    {"client_order_id": "c1", "side": "buy", "role": "base",
                     "price": 60000.0, "quantity": 0.2, "fee": 0.1},
                ],
            }
            writer.enqueue_snapshot("writer-bot", "config/grid/w.yaml", stats, 123.0)
            self.assertEqual(writer.flush_sync(), 1)

            # 完整 save：bot 註冊 + 成交入流水 + 快照落盤
            bot = repo._ledger.get_bot("writer-bot")
            self.assertIsNotNone(bot)
            self.assertEqual(bot.symbol, "BTC/USDT")
            self.assertEqual(len(repo._ledger.get_fills("writer-bot")), 1)
            by_id = repo.load_by_bot_id("writer-bot")
            self.assertIsNotNone(by_id)
            self.assertEqual(by_id[0]["profit"]["realized"], 10.0)

    def test_retry_queue_recovers_after_transient_failure(self):
        with tempfile.TemporaryDirectory() as d:
            repo = RuntimeSnapshotRepository(os.path.join(d, "r.sqlite3"))
            calls = {"n": 0}

            def flaky(config_path, stats, ts):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("transient boom")
                repo.save(config_path, stats, updated_at=ts)

            writer = AsyncDatabaseWriter(save_fn=flaky, max_retries=2)
            writer.enqueue_snapshot("b1", "config/1.yaml", {"profit": {"net": 1}}, 1.0)
            self.assertEqual(writer.flush_sync(), 0)  # 首次失敗 → 重新入隊
            self.assertEqual(writer.flush_sync(), 1)  # 重試成功
            self.assertEqual(calls["n"], 2)
            self.assertIsNotNone(repo.load_all()["config/1.yaml"])

    def test_gives_up_after_max_retries(self):
        def always_fail(config_path, stats, ts):
            raise RuntimeError("permanent")

        writer = AsyncDatabaseWriter(save_fn=always_fail, max_retries=1)
        writer.enqueue_snapshot("b1", "config/1.yaml", {"profit": {"net": 1}}, 1.0)
        self.assertEqual(writer.flush_sync(), 0)  # retry=0 失敗 → 入隊 retry=1
        self.assertEqual(writer.flush_sync(), 0)  # retry=1 失敗 → 達上限捨棄
        self.assertEqual(writer.flush_sync(), 0)  # 佇列已空


if __name__ == "__main__":
    unittest.main()
