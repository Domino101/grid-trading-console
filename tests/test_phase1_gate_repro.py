"""Phase 1 Gate：重現並驗證「小改動/重開即歸 0」已根除。

直接對應使用者回報的情境：
  1. 開一台 grid（改價格區間 → 檔名變）＋ 一台 DCA（重建 → timestamp 檔名變）。
  2. 產生成交與累計計數後「重啟」。
  3. 以穩定 bot_id（metadata.bot_id）恢復 → 計數與盈虧**不得歸 0**。
"""

import os
import tempfile
import time
import unittest

import yaml

from core.infrastructure.identity import resolve_bot_id
from web_ui.backend.dca_yaml_generator import DcaYAMLGenerator
from web_ui.backend.runtime_repository import RuntimeSnapshotRepository
from web_ui.backend.yaml_generator import GridYAMLGenerator


def _grid_params(**overrides):
    params = {
        "symbol": "SOL/USDC:USDC", "exchange": "binance", "grid_type": "neutral",
        "grid_interval": 0.5, "order_amount": 100,
        "lower_price": 10, "upper_price": 20, "account_mode": "paper",
    }
    params.update(overrides)
    return params


def _dca_params(**overrides):
    params = {
        "symbol": "ETH/USDC:USDC", "exchange": "binance", "direction": "long",
        "base_order_amount": 20, "account_mode": "paper",
        "entry": {"mode": "immediate"},
    }
    params.update(overrides)
    return params


class TestPhase1GateRepro(unittest.TestCase):
    def _write(self, root, rel_path, content):
        abs_path = os.path.join(root, rel_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(content)
        return abs_path

    def test_grid_edit_price_range_then_restart_does_not_reset(self):
        with tempfile.TemporaryDirectory() as d:
            repo = RuntimeSnapshotRepository(os.path.join(d, "r.sqlite3"))
            gen = GridYAMLGenerator()

            # 1. 建立 + 累積成交（5 循環、已實現 12.5）
            yaml1, path1 = gen.generate(_grid_params(lower_price=10, upper_price=20))
            bot_id = yaml.safe_load(yaml1)["metadata"]["bot_id"]
            self._write(d, path1, yaml1)
            repo.save(path1, {
                "bot_id": bot_id,
                "filled_orders": {"buy": 5, "sell": 5, "cycles": 5},
                "profit": {"realized": 12.5, "fees": 0.5},
            }, updated_at=1000.0)

            # 2. 小改動：改價格區間 → 檔名變，但沿用既有 bot_id（編輯流程）
            yaml2, path2 = gen.generate(
                _grid_params(lower_price=5, upper_price=25), existing_bot_id=bot_id
            )
            self.assertNotEqual(path1, path2)
            self.assertEqual(yaml.safe_load(yaml2)["metadata"]["bot_id"], bot_id)
            self._write(d, path2, yaml2)

            # 3. 重啟恢復：以穩定 bot_id 讀取 → 計數不歸 0
            recovered = repo.load_by_bot_id(bot_id)
            self.assertIsNotNone(recovered, "以 bot_id 恢復應能取回歷史快照")
            payload, _ = recovered
            self.assertEqual(payload["filled_orders"]["cycles"], 5)
            self.assertEqual(payload["profit"]["realized"], 12.5)

            # 4. 身份解析在新檔名上仍解析到同一 bot_id
            self.assertEqual(resolve_bot_id(path2, project_root=d), bot_id)

    def test_dca_rebuild_then_restart_does_not_reset(self):
        with tempfile.TemporaryDirectory() as d:
            repo = RuntimeSnapshotRepository(os.path.join(d, "r.sqlite3"))
            # 每次呼叫遞增 timestamp，模擬「重建」產生新檔名
            ticks = iter(["20260831000001", "20260831000002", "20260831000003"])
            gen = DcaYAMLGenerator(timestamp_factory=lambda: next(ticks))

            # 1. 建立 + 累積一輪（cycle=1、已實現 8.0、2 筆成交）
            yaml1, path1 = gen.generate(_dca_params())
            bot_id = yaml.safe_load(yaml1)["metadata"]["bot_id"]
            repo.save(path1, {
                "bot_id": bot_id,
                "cycles": 1,
                "profit": {"realized": 8.0, "fees": 0.2},
                "fills": [
                    {"client_order_id": "dca_1_base", "side": "buy", "role": "base",
                     "price": 3000.0, "quantity": 0.01, "fee": 0.01},
                    {"client_order_id": "dca_1_tp", "side": "sell", "role": "tp",
                     "price": 3080.0, "quantity": 0.01, "fee": 0.01},
                ],
            }, updated_at=1000.0)

            # 2. 重建（新 timestamp 檔名）但沿用既有 bot_id
            yaml2, path2 = gen.generate(_dca_params(), existing_bot_id=bot_id)
            self.assertNotEqual(path1, path2)
            self.assertEqual(yaml.safe_load(yaml2)["metadata"]["bot_id"], bot_id)

            # 3. 重啟恢復：快照 + 成交皆以 bot_id 取回
            recovered = repo.load_by_bot_id(bot_id)
            self.assertIsNotNone(recovered)
            self.assertEqual(recovered[0]["cycles"], 1)
            self.assertEqual(recovered[0]["profit"]["realized"], 8.0)
            fills = repo._ledger.get_fills(bot_id)
            self.assertEqual(len(fills), 2)

    def test_zero_value_from_failed_recovery_is_blocked(self):
        """恢復失敗的新引擎以 0 值回報 → 單調防護拒寫，累積計數不歸 0。"""
        with tempfile.TemporaryDirectory() as d:
            repo = RuntimeSnapshotRepository(os.path.join(d, "r.sqlite3"))
            repo.save("config/grid/x.yaml", {
                "bot_id": "stable-x",
                "filled_orders": {"buy": 4, "sell": 4, "cycles": 4},
                "profit": {"realized": 9.9, "fees": 0.4},
            }, updated_at=1000.0)

            repo.save("config/grid/x.yaml", {
                "bot_id": "stable-x",
                "filled_orders": {"buy": 0, "sell": 0, "cycles": 0},
                "profit": {"realized": 0.0, "fees": 0.0},
            }, updated_at=2000.0)

            payload, _ = repo.load_by_bot_id("stable-x")
            self.assertEqual(payload["filled_orders"]["cycles"], 4)
            self.assertEqual(payload["profit"]["realized"], 9.9)


if __name__ == "__main__":
    unittest.main()
