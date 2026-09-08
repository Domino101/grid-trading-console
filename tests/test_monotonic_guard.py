"""P1-4: monotonic guard for runtime snapshots.

Ensures a freshly (re)started engine that failed recovery cannot overwrite the
accumulated cumulative counters (cycles / buy / sell / fees) with zero values.
Legitimate resets (take-profit / capital protection / price escape) are
exempted via a one-shot ``_monotonic_bypass`` marker.
"""

import os
import tempfile
import unittest

from web_ui.backend.runtime_repository import RuntimeSnapshotRepository


def _snapshot(cycles, buy=0, sell=0, fees=0.0, realized=0.0, **extra):
    payload = {
        "filled_orders": {"buy": buy, "sell": sell, "cycles": cycles},
        "profit": {"realized": realized, "fees": fees},
    }
    payload.update(extra)
    return payload


class TestMonotonicGuard(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp_dir.name, "runtime.sqlite3")
        self.repo = RuntimeSnapshotRepository(self.db_path)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def _save(self, payload, updated_at):
        payload = dict(payload)
        payload.setdefault("bot_id", "bot-1")
        return self.repo.save("config/grid/test.yaml", payload, updated_at=updated_at)

    def test_counter_regression_is_blocked(self):
        self._save(_snapshot(cycles=5, buy=5, sell=5, fees=1.0, realized=10.0), updated_at=1.0)
        # 模擬恢復失敗的新引擎以 0 值回報 → 應被拒寫
        self._save(_snapshot(cycles=0, buy=0, sell=0, fees=0.0, realized=0.0), updated_at=2.0)

        payload, ts = self.repo.load_by_bot_id("bot-1")
        self.assertEqual(payload["filled_orders"]["cycles"], 5)
        self.assertEqual(payload["filled_orders"]["buy"], 5)
        self.assertEqual(payload["profit"]["fees"], 1.0)

    def test_loss_realized_is_not_blocked(self):
        # realized 會因虧損循環/停損合法下降，單調防護不得誤擋
        self._save(_snapshot(cycles=2, fees=1.0, realized=10.0), updated_at=1.0)
        self._save(_snapshot(cycles=2, fees=1.1, realized=5.0), updated_at=2.0)

        payload, _ = self.repo.load_by_bot_id("bot-1")
        self.assertEqual(payload["profit"]["realized"], 5.0)
        self.assertEqual(payload["profit"]["fees"], 1.1)

    def test_bypass_allows_legitimate_reset(self):
        self._save(_snapshot(cycles=5, buy=5, sell=5, fees=1.0), updated_at=1.0)
        # 合法重置（止盈/本金保護/價格脫離）帶 bypass 標記 → 允許歸零
        self._save(
            _snapshot(cycles=0, buy=0, sell=0, fees=0.0, _monotonic_bypass=True),
            updated_at=2.0,
        )

        payload, _ = self.repo.load_by_bot_id("bot-1")
        self.assertEqual(payload["filled_orders"]["cycles"], 0)

    def test_increase_is_allowed(self):
        self._save(_snapshot(cycles=5, buy=5, sell=5, fees=1.0), updated_at=1.0)
        self._save(_snapshot(cycles=6, buy=6, sell=6, fees=1.2), updated_at=2.0)

        payload, _ = self.repo.load_by_bot_id("bot-1")
        self.assertEqual(payload["filled_orders"]["cycles"], 6)

    def test_first_write_is_never_blocked(self):
        # 首次寫入無既有快照可比較，應直接寫入
        self._save(_snapshot(cycles=0, buy=0, sell=0, fees=0.0), updated_at=1.0)
        payload, _ = self.repo.load_by_bot_id("bot-1")
        self.assertEqual(payload["filled_orders"]["cycles"], 0)

    def test_corrupted_restart_realized_wipe_is_blocked(self):
        # DEF-01: 模擬引擎重啟後記憶體未恢復，若無新交易發生 (fees 未增)，試圖將累積獲利抹除為 0 → 必須拒絕
        self._save(_snapshot(cycles=5, buy=5, sell=5, fees=1.0, realized=1250.0), updated_at=1.0)
        self._save(_snapshot(cycles=5, buy=5, sell=5, fees=1.0, realized=0.0), updated_at=2.0)

        payload, _ = self.repo.load_by_bot_id("bot-1")
        self.assertEqual(payload["profit"]["realized"], 1250.0)

    def test_none_injection_is_blocked(self):
        # DEF-02: 傳入 None 或損毀型別，不得繞過單調檢查抹除原有計數
        self._save(_snapshot(cycles=5, buy=5, sell=5, fees=1.0), updated_at=1.0)
        corrupted_payload = {
            "filled_orders": {"buy": None, "sell": None, "cycles": None},
            "profit": {"fees": None},
        }
        self._save(corrupted_payload, updated_at=2.0)

        payload, _ = self.repo.load_by_bot_id("bot-1")
        self.assertEqual(payload["filled_orders"]["cycles"], 5)
        self.assertEqual(payload["filled_orders"]["buy"], 5)


if __name__ == "__main__":
    unittest.main()
