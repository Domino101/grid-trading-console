"""P3-1: bot_events append-only stream — append/get/dedupe/bridge semantics."""

import os
import tempfile
import time
import unittest

from core.infrastructure.database.ledger_repository import LedgerRepository
from core.infrastructure.database.models import BotRecord, TradeFill


class TestBotEventStream(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = os.path.join(self.tmp.name, "events.sqlite3")
        self.repo = LedgerRepository(self.db_path)
        # emit_* 經由 singleton → 測試指向暫存 DB
        LedgerRepository._instance = self.repo
        self.addCleanup(self._reset_singleton)

    def _reset_singleton(self):
        LedgerRepository._instance = None

    def _register(self, bot_id="evt-bot", config_path=None):
        self.repo.register_or_update_bot(BotRecord(
            bot_id=bot_id, strategy_type="grid", symbol="ETH/USDC", exchange="binance",
            account_mode="paper", status="running", created_at=time.time(),
            config_path=config_path,
        ))

    def test_append_assigns_monotonic_seq_and_reads_in_order(self):
        self._register()
        # bot_created 橋接事件佔 seq=1；後續 append 自 seq=2 遞增
        s1 = self.repo.append_event("evt-bot", "state_transition", {"status": "running"})
        s2 = self.repo.append_event("evt-bot", "reset", {"reset_type": "止盈"})
        s3 = self.repo.append_event("evt-bot", "fill", {"side": "buy"})
        self.assertEqual([s1, s2, s3], [2, 3, 4])
        events = self.repo.get_events("evt-bot")
        self.assertEqual([e.seq for e in events], [1, 2, 3, 4])
        self.assertEqual(
            [e.event_type for e in events],
            ["bot_created", "state_transition", "reset", "fill"],
        )

    def test_dedupe_key_is_idempotent(self):
        self._register()
        first = self.repo.append_event("evt-bot", "fill", {"x": 1}, dedupe_key="fill:f-1")
        again = self.repo.append_event("evt-bot", "fill", {"x": 1}, dedupe_key="fill:f-1")
        self.assertEqual(first, again)
        self.assertEqual(len([e for e in self.repo.get_events("evt-bot") if e.event_type == "fill"]), 1)
        # 不同 dedupe_key 正常追加
        self.repo.append_event("evt-bot", "fill", {"x": 2}, dedupe_key="fill:f-2")
        self.assertEqual(len(self.repo.get_events("evt-bot")), 3)  # bot_created + 2 fills

    def test_registration_bridges_bot_created_event(self):
        self._register("bridge-bot", config_path="config/grid/bridge.yaml")
        events = self.repo.get_events("bridge-bot")
        self.assertEqual([e.event_type for e in events], ["bot_created"])
        self.assertEqual(events[0].payload.get("config_path"), "config/grid/bridge.yaml")
        # 路徑變更 → bot_renamed 橋接
        self._register("bridge-bot", config_path="config/grid/bridge-renamed.yaml")
        events = self.repo.get_events("bridge-bot")
        self.assertEqual([e.event_type for e in events], ["bot_created", "bot_renamed"])

    def test_streams_are_isolated_per_bot(self):
        self._register("b1")
        self._register("b2")
        self.repo.append_event("b1", "reset", {})
        self.assertEqual(len(self.repo.get_events("b1")), 1 + 1)  # + bot_created
        self.assertEqual(len(self.repo.get_events("b2")), 1)      # 只有 bot_created

    def test_fill_event_payload_roundtrip(self):
        from core.infrastructure.database.bot_events import emit_fill_event

        self._register("fill-bot")
        fill = TradeFill(
            fill_id="f-77", bot_id="fill-bot", client_order_id="c-77", side="buy",
            role="grid", price=101.5, quantity=0.25, fee=0.01, realized_pnl=0.0,
            executed_at=1756600000.0,
        )
        seq = emit_fill_event(fill)
        self.assertIsNotNone(seq)
        # 重送同一 fill 冪等
        self.assertEqual(emit_fill_event(fill), seq)
        events = self.repo.get_events("fill-bot")
        fill_events = [e for e in events if e.event_type == "fill"]
        self.assertEqual(len(fill_events), 1)
        self.assertEqual(fill_events[0].payload["price"], 101.5)
        self.assertEqual(fill_events[0].payload["quantity"], 0.25)

    def test_reset_checkpoint_type_mapping(self):
        from core.infrastructure.database.bot_events import emit_reset_checkpoint

        self._register("reset-bot")
        checkpoint = {"cycles": 0, "buy": 0, "sell": 0, "fees": 0.0, "realized": 0.0}
        emit_reset_checkpoint("reset-bot", "止盈", checkpoint=checkpoint)
        emit_reset_checkpoint("reset-bot", "本金保护", checkpoint=checkpoint)
        emit_reset_checkpoint("reset-bot", "价格脱离", checkpoint=checkpoint)
        types = [e.event_type for e in self.repo.get_events("reset-bot") if e.event_type != "bot_created"]
        self.assertEqual(types, ["take_profit", "stop_loss", "reset"])

    def test_events_table_exists_on_raw_schema_path(self):
        # schema.sql（原生 sqlite 路徑）也必須含 bot_events
        import sqlite3

        con = sqlite3.connect(self.db_path)
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("bot_events", tables)
        con.close()


if __name__ == "__main__":
    unittest.main()

