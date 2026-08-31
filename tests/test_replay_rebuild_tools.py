"""P3-3/P3-4: replay tool consistency + projection rebuild (DR drill core)."""

import os
import tempfile
import time
import unittest

from sqlalchemy import text

from core.infrastructure.database.event_fold import rebuild_position
from core.infrastructure.database.ledger_repository import LedgerRepository
from core.infrastructure.database.models import BotRecord, TradeFill
from scripts.tools.replay_bot import replay as replay_bot
from scripts.tools.rebuild_projections import rebuild as rebuild_bot


class TestReplayAndRebuild(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = os.path.join(self.tmp.name, "tools.sqlite3")
        self.repo = LedgerRepository(self.db_path)
        # emit_* 經由 singleton → 測試指向暫存 DB
        LedgerRepository._instance = self.repo
        self.addCleanup(self._reset_singleton)
        self._seed()

    def _reset_singleton(self):
        LedgerRepository._instance = None

    def _delete_projection_rows(self):
        """方言無關地清空投影表（SQLite 檔案或 PostgreSQL schema 皆可）。"""
        with self.repo.store._engine.begin() as conn:
            conn.execute(text("DELETE FROM bot_positions"))

    def _seed(self):
        repo = self.repo
        repo.register_or_update_bot(BotRecord(
            bot_id="tool-bot", strategy_type="grid", symbol="ETH/USDC", exchange="binance",
            account_mode="paper", status="running", created_at=time.time(),
            config_path="config/grid/tool.yaml",
        ))
        # 3 筆成交（1 個 TP cycle）+ 1 次止盈重置檢查點 + 2 筆成交
        for i in range(3):
            repo.record_fill(TradeFill(
                fill_id=f"t-{i}", bot_id="tool-bot", client_order_id=f"c-{i}",
                side="buy" if i % 2 == 0 else "sell", role="tp" if i == 1 else "grid",
                price=100 + i, quantity=0.5, fee=0.01,
                realized_pnl=2.0 if i == 1 else 0.0, executed_at=1756600000.0 + i,
            ))
            from core.infrastructure.database.bot_events import emit_fill_event
            emit_fill_event(repo.get_fills("tool-bot")[-1])
        repo.append_event("tool-bot", "take_profit", {
            "reset_type": "止盈",
            "checkpoint": {"cycles": 0, "buy": 0, "sell": 0, "fees": 0.0, "realized": 0.0},
        })
        for i in range(3, 5):
            repo.record_fill(TradeFill(
                fill_id=f"t-{i}", bot_id="tool-bot", client_order_id=f"c-{i}",
                side="buy" if i % 2 == 0 else "sell", role="grid",
                price=100 + i, quantity=0.5, fee=0.01, realized_pnl=1.0,
                executed_at=1756600000.0 + i,
            ))
            from core.infrastructure.database.bot_events import emit_fill_event
            emit_fill_event(repo.get_fills("tool-bot")[-1])

    def _sync_projection_to_rebuild(self):
        """把 stored projection 對齊 rebuild 結果（模擬一致的健康狀態）。"""
        rebuilt = rebuild_position(self.repo.store, "tool-bot")
        existing = self.repo.store.get_position("tool-bot")
        expected = existing.version if existing else 1
        self.assertTrue(self.repo.store.update_position_occ("tool-bot", rebuilt, expected_version=expected))

    def test_replay_reports_consistent_when_projection_matches(self):
        self._sync_projection_to_rebuild()
        report = replay_bot("tool-bot", self.db_path)
        self.assertEqual(report["status"], "consistent")
        self.assertEqual(report["events_by_type"].get("fill"), 5)
        self.assertEqual(report["events_by_type"].get("take_profit"), 1)

    def test_replay_detects_divergence(self):
        report = replay_bot("tool-bot", self.db_path)
        self.assertEqual(report["status"], "divergent")
        # 重置檢查點把 cycles 歸零 → stored(空) 與 rebuilt 差異在 realized/fees
        self.assertIn("realized_profit", report["diffs"])

    def test_replay_missing_bot(self):
        self.assertEqual(replay_bot("no-such-bot", self.db_path)["status"], "bot_not_found")

    def test_rebuild_restores_deleted_projection(self):
        # P3-4 核心：刪除投影 → 由 facts+events 完整重建
        self._sync_projection_to_rebuild()
        expected = self.repo.store.get_position("tool-bot")

        self._delete_projection_rows()
        self.assertIsNone(self.repo.store.get_position("tool-bot"))

        result = rebuild_bot(self.repo, "tool-bot", execute=True)
        self.assertEqual(result["status"], "rebuilt")
        restored = self.repo.store.get_position("tool-bot")
        self.assertIsNotNone(restored)
        self.assertAlmostEqual(restored.quantity, expected.quantity, places=7)
        self.assertEqual(restored.completed_cycles, expected.completed_cycles)
        self.assertAlmostEqual(restored.realized_profit, expected.realized_profit, places=7)
        self.assertAlmostEqual(restored.total_fees, expected.total_fees, places=7)

    def test_rebuild_dry_run_does_not_write(self):
        self._delete_projection_rows()
        result = rebuild_bot(self.repo, "tool-bot", execute=False)
        self.assertEqual(result["status"], "dry_run")
        self.assertIsNone(self.repo.store.get_position("tool-bot"))


if __name__ == "__main__":
    unittest.main()
