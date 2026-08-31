"""Phase 2 orchestration tests (P2-1/P2-2 abstraction + Redis fallback).

Covers the audit fixes as regression tests:
* F1 — a bare ``postgresql://`` DATABASE_URL must select the PostgreSQL store
  (driver-normalized to the pinned psycopg v3), never silently fall back to
  SQLite; ``postgresql+psycopg://`` must be recognized as a postgres URL too.
* F2 — distinct database paths map to distinct PostgreSQL schemas (same
  isolation semantics as SQLite files).
* F3 — the raw sqlite3 back-door (``_connect``) is rejected in PG mode.
"""

import os
import tempfile
import unittest

from core.infrastructure.database.ledger_repository import LedgerRepository
from core.infrastructure.database.ledger_store import (
    PostgresLedgerStore,
    SqliteLedgerStore,
    create_ledger_store,
    is_postgres_url,
    normalize_postgres_url,
)
from core.infrastructure.database.models import BotRecord, TradeFill
from core.infrastructure.database.redis_support import (
    RedisEventBus,
    RedisLeaderElection,
    RedisStateCache,
    env_int,
    redis_enabled,
)


def _pg_test_url() -> str:
    """Optional live-PostgreSQL regression URL (set TEST_PG_URL to enable)."""
    return os.getenv("TEST_PG_URL", "")


class TestPhase2LedgerOrchestration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # These tests assert SQLite-vs-Postgres *selection*; a leftover
        # DATABASE_URL from the surrounding environment used to flip the
        # factory and fail the suite (audit F2).  Sanitize per test.
        self._old_url = os.environ.pop("DATABASE_URL", None)
        self.addCleanup(self._restore_url)

    def _restore_url(self):
        if self._old_url is not None:
            os.environ["DATABASE_URL"] = self._old_url

    # ── factory selection ───────────────────────────────────────────────────
    def test_create_ledger_store_defaults_to_sqlite(self):
        store = create_ledger_store(os.path.join(self.tmp.name, "a.sqlite3"))
        self.assertIsInstance(store, SqliteLedgerStore)
        self.assertFalse(store.database_path == ":memory:")

    def test_postgres_store_requires_url(self):
        with self.assertRaises(RuntimeError):
            PostgresLedgerStore(database_url=None)

    def test_is_postgres_url_accepts_all_documented_forms(self):
        # F1 regression: the driver-qualified psycopg v3 form was previously
        # rejected, silently routing production to SQLite.
        self.assertTrue(is_postgres_url("postgresql://grid:grid@localhost:5432/grid"))
        self.assertTrue(is_postgres_url("postgres://grid:grid@localhost:5432/grid"))
        self.assertTrue(is_postgres_url("postgresql+psycopg://grid:grid@localhost:5432/grid"))
        self.assertTrue(is_postgres_url("postgresql+psycopg2://grid:grid@localhost:5432/grid"))
        self.assertFalse(is_postgres_url("sqlite:///data/x.sqlite3"))
        self.assertFalse(is_postgres_url(None))
        self.assertFalse(is_postgres_url(""))

    def test_normalize_postgres_url_targets_pinned_driver(self):
        self.assertEqual(
            normalize_postgres_url("postgresql://grid:grid@db:5432/grid"),
            "postgresql+psycopg://grid:grid@db:5432/grid",
        )
        self.assertEqual(
            normalize_postgres_url("postgres://grid:grid@db:5432/grid"),
            "postgresql+psycopg://grid:grid@db:5432/grid",
        )
        self.assertEqual(
            normalize_postgres_url("postgresql+psycopg://grid:grid@db:5432/grid"),
            "postgresql+psycopg://grid:grid@db:5432/grid",
        )

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

    # ── F3: raw sqlite3 back-door must not read a ghost source in PG mode ──
    def test_raw_sqlite_connect_rejected_in_postgres_mode(self):
        repo = LedgerRepository(os.path.join(self.tmp.name, "f3.sqlite3"))
        repo._store = PostgresLedgerStore.__new__(PostgresLedgerStore)  # bypass __init__
        try:
            with self.assertRaises(RuntimeError):
                repo._connect()
            with self.assertRaises(RuntimeError):
                repo._ensure_schema(object())
        finally:
            from core.infrastructure.database.ledger_store import SqliteLedgerStore

            repo._store = SqliteLedgerStore(os.path.join(self.tmp.name, "f3.sqlite3"))

    # ── Redis degrade semantics ────────────────────────────────────────────
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

    def test_ttl_env_vars_are_wired(self):
        # F4 regression: documented TTL envs now feed cache TTL / leader lease.
        old_state = os.environ.pop("GRID_REDIS_STATE_TTL", None)
        old_leader = os.environ.pop("GRID_REDIS_LEADER_TTL", None)
        try:
            os.environ["GRID_REDIS_STATE_TTL"] = "61"
            os.environ["GRID_REDIS_LEADER_TTL"] = "16"
            self.assertEqual(RedisStateCache().ttl, 61)
            self.assertEqual(RedisLeaderElection("bot-ttl").ttl, 16)
            self.assertEqual(RedisStateCache(ttl=77).ttl, 77)  # explicit wins
            os.environ["GRID_REDIS_STATE_TTL"] = "not-a-number"
            self.assertEqual(RedisStateCache().ttl, 300)  # garbage -> default
        finally:
            for key, old in (("GRID_REDIS_STATE_TTL", old_state), ("GRID_REDIS_LEADER_TTL", old_leader)):
                if old is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = old

    def test_env_int_defaults(self):
        self.assertEqual(env_int("GRID_TEST_MISSING_VAR_XYZ", 42), 42)


@unittest.skipUnless(_pg_test_url(), "Set TEST_PG_URL (live PostgreSQL) to run PG regression tests")
class TestPhase2PostgresFactory(unittest.TestCase):
    """Live-PG regressions for audit fixes F1/F2 (factory + schema isolation)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._old_url = os.environ.pop("DATABASE_URL", None)
        self.addCleanup(self._restore_url)

    def _restore_url(self):
        if self._old_url is not None:
            os.environ["DATABASE_URL"] = self._old_url

    def test_bare_postgres_url_selects_pg_store(self):
        # F1: documented URL form must yield the PG store (psycopg v3 driver).
        os.environ["DATABASE_URL"] = _pg_test_url()
        self.assertTrue(_pg_test_url().startswith("postgresql"))
        store = create_ledger_store(os.path.join(self.tmp.name, "pg-a.sqlite3"))
        try:
            self.assertIsInstance(store, PostgresLedgerStore)
            self.assertIsNotNone(store.schema)
        finally:
            store._engine.dispose()

    def test_pg_schema_isolation_between_paths(self):
        # F2: two isolated database paths behave like two SQLite files.
        os.environ["DATABASE_URL"] = _pg_test_url()
        from core.infrastructure.database.models import BotRecord

        path_a = os.path.join(self.tmp.name, "iso-a.sqlite3")
        path_b = os.path.join(self.tmp.name, "iso-b.sqlite3")
        store_a = create_ledger_store(path_a)
        store_b = create_ledger_store(path_b)
        try:
            self.assertNotEqual(store_a.schema, store_b.schema)
            ts = 1_234_567.0
            store_a.register_or_update_bot(BotRecord(
                bot_id="iso-a-bot", strategy_type="grid", symbol="A/USDT",
                exchange="binance", account_mode="paper", status="running", created_at=ts,
            ))
            # B must not see A's bot (separate schema), A must see its own.
            self.assertIsNotNone(store_a.get_bot("iso-a-bot"))
            self.assertIsNone(store_b.get_bot("iso-a-bot"))
        finally:
            store_a._engine.dispose()
            store_b._engine.dispose()


if __name__ == "__main__":
    unittest.main()
