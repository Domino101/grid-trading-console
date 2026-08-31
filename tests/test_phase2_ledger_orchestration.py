"""Phase 2 orchestration tests (P2-1/P2-2 abstraction + Redis fallback)."""

import os
import tempfile
import unittest

from core.infrastructure.database.ledger_repository import LedgerRepository
from core.infrastructure.database.ledger_store import (
    PostgresLedgerStore,
    SqliteLedgerStore,
    create_ledger_store,
)
from core.infrastructure.database.models import BotRecord, TradeFill
from core.infrastructure.database.redis_support import (
    RedisEventBus,
    RedisLeaderElection,
    RedisStateCache,
    redis_enabled,
)


class TestPhase2LedgerOrchestration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_create_ledger_store_defaults_to_sqlite(self):
        store = create_ledger_store(os.path.join(self.tmp.name, "a.sqlite3"))
        self.assertIsInstance(store, SqliteLedgerStore)
        self.assertFalse(store.database_path == ":memory:")

    def test_postgres_store_requires_url(self):
        with self.assertRaises(RuntimeError):
            PostgresLedgerStore(database_url=None)

    def test_repository_exposes_sqlalchemy_store(self):
        repo = LedgerRepository(os.path.join(self.tmp.name, "b.sqlite3"))
        self.assertIsInstance(repo.store, SqliteLedgerStore)

    def test_facade_persists_through_store(self):
        repo = LedgerRepository(os.path.join(self.tmp.name, "c.sqlite3"))
        ts = 1_000_000.0
        repo.register_or_update_bot(BotRecord(
            bot_id="p2-1", strategy_type="grid", symbol="BTC/USDT", exchange="binance",
            account_mode="paper", status="running", created_at=ts,
        ))
        repo.record_fill(TradeFill(
            fill_id="p2-fill-1", bot_id="p2-1", client_order_id="cid", side="buy",
            role="grid", price=100.0, quantity=1.0, fee=0.01, executed_at=ts + 1,
        ))
        now = repo.save_snapshot("p2-1", "config/grid/p2.yaml", {"symbol": "BTC/USDT"}, ts)
        self.assertGreater(now, 0)
        self.assertEqual(len(repo.get_fills("p2-1")), 1)
        self.assertEqual(repo.load_snapshot_by_bot_id("p2-1")[0]["symbol"], "BTC/USDT")

    def test_redis_disabled_degrades(self):
        old_url = os.environ.pop("REDIS_URL", None)
        try:
            self.assertFalse(redis_enabled())
            self.assertIsNone(RedisStateCache().get("config/grid/x.yaml"))
            self.assertEqual(RedisStateCache().get_all(), {})
            # 未設定 Redis 時，leader election 視為關閉，允許引擎啟動。
            self.assertTrue(RedisLeaderElection("bot-x").acquire())
            RedisEventBus().publish("lifecycle", {"status": "running"})
        finally:
            if old_url is not None:
                os.environ["REDIS_URL"] = old_url


if __name__ == "__main__":
    unittest.main()
