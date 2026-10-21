"""CoinFull Prime legacy ledger repair methods (L5/L6) regression tests."""

import tempfile
import time
import unittest
from pathlib import Path

from core.infrastructure.database.ledger_repository import LedgerRepository
from core.infrastructure.database.models import BasketEntry, TradeFill
from core.infrastructure.database.strategy_ledger import StrategyLedger


class TestLegacyLedgerRepair(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = LedgerRepository(str(Path(self.tmp.name) / "ledger.sqlite3"))
        self.ledger = StrategyLedger(repository=self.repo)
        self.ledger.ensure_bot(
            bot_id="coinfull_prime",
            strategy_type="coinfull_prime",
            symbol="BASKET_16",
            exchange="legacy",
            account_mode="paper",
            status="archived",
        )
        self.ledger.ensure_bot(
            bot_id="coinfull_prime:paper",
            strategy_type="coinfull_prime",
            symbol="BASKET_16",
            exchange="paper",
            account_mode="paper",
            status="running",
        )

    def test_backfill_dedupe_keys_is_idempotent(self):
        self.ledger.append_event("coinfull_prime", "daily_rebalance", {"n": 1}, created_at=1.0)
        self.ledger.append_event("coinfull_prime", "regime_gate", {"n": 2}, created_at=2.0)
        self.ledger.append_event("coinfull_prime", "reset", {"n": 3}, created_at=3.0)

        self.assertEqual(self.ledger.count_legacy_event_dedupe_gaps("coinfull_prime"), 2)
        self.assertEqual(self.ledger.backfill_legacy_event_dedupe_keys("coinfull_prime"), 2)
        self.assertEqual(self.ledger.count_legacy_event_dedupe_gaps("coinfull_prime"), 0)
        self.assertEqual(self.ledger.backfill_legacy_event_dedupe_keys("coinfull_prime"), 0)

        events = self.ledger.get_events("coinfull_prime")
        keyed = [e for e in events if e.event_type in ("daily_rebalance", "regime_gate")]
        self.assertTrue(all(str(e.dedupe_key).startswith("legacy:") for e in keyed))

    def test_close_basket_entry_preserves_realized_and_is_idempotent(self):
        self.ledger.save_basket(
            "coinfull_prime:paper",
            "paper",
            [BasketEntry(
                bot_id="coinfull_prime:paper",
                account_id="paper",
                symbol="AAAUSDT",
                side="long",
                quantity=10.0,
                entry_price=100.0,
                notional_usdt=1000.0,
                realized_pnl=5.0,
                status="active",
            )],
            close_missing=False,
        )
        info = self.ledger.close_basket_entry(
            "coinfull_prime:paper", "paper", "AAAUSDT", reason="missing_on_exchange"
        )
        self.assertEqual(info["quantity"], 10.0)
        self.assertEqual(info["realized_pnl"], 5.0)
        self.assertEqual(self.ledger.load_basket("coinfull_prime:paper", "paper", active_only=True), [])
        closed = self.ledger.load_basket("coinfull_prime:paper", "paper", active_only=False)
        self.assertEqual(closed[0].status, "closed")
        self.assertEqual(closed[0].quantity, 0.0)
        self.assertEqual(closed[0].realized_pnl, 5.0)
        self.assertIsNone(self.ledger.close_basket_entry(
            "coinfull_prime:paper", "paper", "AAAUSDT", reason="missing_on_exchange"
        ))

    def test_backfill_missing_fill_events_is_idempotent(self):
        fill = TradeFill(
            fill_id="legacy-fill-1",
            bot_id="coinfull_prime:paper",
            client_order_id="legacy-fill-1",
            side="buy",
            role="rebalance_entry",
            price=100.0,
            quantity=1.0,
            executed_at=time.time(),
        )
        # 模擬舊資料：只有 trade_fills，沒有 fill 事件
        self.ledger.store.record_fill(fill)
        self.assertEqual(self.ledger.backfill_missing_fill_events("coinfull_prime:paper"), 1)
        self.assertEqual(self.ledger.backfill_missing_fill_events("coinfull_prime:paper"), 0)

        fill_events = [
            e for e in self.ledger.get_events("coinfull_prime:paper") if e.event_type == "fill"
        ]
        self.assertEqual(len(fill_events), 1)
        self.assertEqual(fill_events[0].payload.get("fill_id"), "legacy-fill-1")


if __name__ == "__main__":
    unittest.main()
