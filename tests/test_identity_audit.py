"""P1-6: identity audit + no 'unknown' placeholder in the ledger."""

import os
import tempfile
import time
import unittest

from core.infrastructure.database.ledger_repository import (
    LedgerIntegrityError,
    LedgerRepository,
)
from core.infrastructure.database.models import BotPosition, BotRecord, TradeFill


class TestIdentityAudit(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp_dir.name, "identity.sqlite3")
        self.repo = LedgerRepository(self.db_path)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def _register(self, bot_id="bot-a", config_path=None):
        self.repo.register_or_update_bot(BotRecord(
            bot_id=bot_id, strategy_type="grid", symbol="A/USDT", exchange="binance",
            account_mode="paper", status="running", created_at=time.time(),
            config_path=config_path,
        ))

    def test_record_fill_raises_when_bot_missing(self):
        fill = TradeFill(
            fill_id="f-orphan", bot_id="ghost-bot", client_order_id="cid",
            side="buy", role="grid", price=1.0, quantity=1.0, executed_at=time.time(),
        )
        with self.assertRaises(LedgerIntegrityError):
            self.repo.record_fill(fill)

    def test_update_position_occ_raises_when_bot_missing(self):
        pos = BotPosition(bot_id="ghost-bot", quantity=1.0, updated_at=time.time())
        with self.assertRaises(LedgerIntegrityError):
            self.repo.update_position_occ("ghost-bot", pos, expected_version=1)

    def test_created_event_recorded(self):
        self._register(bot_id="bot-a", config_path="config/grid/a.yaml")
        events = self.repo.get_identity_events("bot-a")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "created")
        self.assertEqual(events[0].config_path, "config/grid/a.yaml")

    def test_config_linked_event_on_path_change(self):
        self._register(bot_id="bot-a", config_path="config/grid/a.yaml")
        # 檔名漂移（改區間）後，同一 bot_id 指向新 config_path → config_linked 審計
        self._register(bot_id="bot-a", config_path="config/grid/a-renamed.yaml")
        events = self.repo.get_identity_events("bot-a")
        types = [e.event_type for e in events]
        self.assertEqual(types, ["created", "config_linked"])
        self.assertEqual(events[-1].config_path, "config/grid/a-renamed.yaml")

    def test_no_unknown_placeholder_rows(self):
        self._register(bot_id="bot-a")
        # 正常註冊後不會有任何 strategy_type='unknown' 的列
        with self.repo._connect() as conn:
            unknown = conn.execute(
                "SELECT COUNT(*) AS c FROM bots WHERE strategy_type = 'unknown'"
            ).fetchone()["c"]
        self.assertEqual(unknown, 0)


if __name__ == "__main__":
    unittest.main()
