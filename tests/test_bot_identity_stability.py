"""P1-1 / P1-2: bot identity stability and bot_id-keyed recovery tests.

Ensures the durable ledger key (bot_id) no longer drifts with the config
filename (grid price range / DCA timestamp), which was the root cause of
trade records resetting to zero on edit + restart.
"""

import os
import tempfile
import unittest

import yaml

from core.infrastructure.identity import (
    generate_bot_id,
    resolve_bot_id,
)
from web_ui.backend.dca_yaml_generator import DcaYAMLGenerator
from web_ui.backend.runtime_repository import RuntimeSnapshotRepository
from web_ui.backend.yaml_generator import GridYAMLGenerator


def _grid_params(**overrides):
    params = {
        "symbol": "SOL/USDC:USDC",
        "exchange": "binance",
        "grid_type": "neutral",
        "grid_interval": 0.5,
        "order_amount": 100,
        "lower_price": 10,
        "upper_price": 20,
        "account_mode": "paper",
    }
    params.update(overrides)
    return params


def _dca_params(**overrides):
    params = {
        "symbol": "ETH/USDC:USDC",
        "exchange": "binance",
        "direction": "long",
        "base_order_amount": 20,
        "account_mode": "paper",
        "entry": {"mode": "immediate"},
    }
    params.update(overrides)
    return params


class TestBotIdentityStability(unittest.TestCase):
    def test_generate_bot_id_is_hex_and_unique(self):
        a = generate_bot_id()
        b = generate_bot_id()
        self.assertRegex(a, r"^[0-9a-f]{32}$")
        self.assertNotEqual(a, b)

    def test_grid_generator_embeds_and_preserves_bot_id(self):
        gen = GridYAMLGenerator()
        yaml1, _ = gen.generate(_grid_params())
        meta1 = yaml.safe_load(yaml1)["metadata"]["bot_id"]
        self.assertRegex(meta1, r"^[0-9a-f]{32}$")

        # 編輯時傳入既有 bot_id → 身份不變（即使價格區間改變）
        yaml2, path2 = gen.generate(
            _grid_params(lower_price=5, upper_price=25), existing_bot_id=meta1
        )
        meta2 = yaml.safe_load(yaml2)["metadata"]["bot_id"]
        self.assertEqual(meta2, meta1)
        # 檔名仍隨區間改變，但身份已解耦
        self.assertIn("5-25", path2)

    def test_dca_generator_embeds_and_preserves_bot_id(self):
        gen = DcaYAMLGenerator()
        yaml1, _ = gen.generate(_dca_params())
        meta1 = yaml.safe_load(yaml1)["metadata"]["bot_id"]
        self.assertRegex(meta1, r"^[0-9a-f]{32}$")

        # 重建（新 timestamp）時傳入既有 bot_id → 身份不變
        yaml2, path2 = gen.generate(_dca_params(), existing_bot_id=meta1)
        meta2 = yaml.safe_load(yaml2)["metadata"]["bot_id"]
        self.assertEqual(meta2, meta1)

    def test_resolve_bot_id_prefers_metadata_then_falls_back_to_stem(self):
        with tempfile.TemporaryDirectory() as d:
            p1 = os.path.join(d, "with-meta.yaml")
            with open(p1, "w", encoding="utf-8") as f:
                f.write("metadata:\n  bot_id: stable-abc\n")
            self.assertEqual(resolve_bot_id(p1), "stable-abc")

            p2 = os.path.join(d, "legacy-name.yaml")
            with open(p2, "w", encoding="utf-8") as f:
                f.write("dca:\n  symbol: X\n")
            self.assertEqual(resolve_bot_id(p2), "legacy-name")

    def test_resolve_bot_id_missing_file_falls_back_to_stem(self):
        self.assertEqual(resolve_bot_id("config/grid/does-not-exist.yaml"), "does-not-exist")

    def test_snapshot_survives_config_rename_via_bot_id(self):
        """核心回歸：config_path 改名（檔名漂移）後，仍能以穩定 bot_id 讀回快照。"""
        with tempfile.TemporaryDirectory() as d:
            repo = RuntimeSnapshotRepository(os.path.join(d, "runtime.sqlite3"))
            repo.save(
                "config/grid/old-name.yaml",
                {"bot_id": "stable-1", "profit": {"realized": 42.0}},
                updated_at=1.0,
            )
            # 模擬改動後檔名變了，但 bot_id 相同
            by_id = repo.load_by_bot_id("stable-1")
            self.assertIsNotNone(by_id)
            payload, ts = by_id
            self.assertEqual(payload["profit"]["realized"], 42.0)

            # 未註冊的 bot_id 回傳 None
            self.assertIsNone(repo.load_by_bot_id("no-such-bot"))


if __name__ == "__main__":
    unittest.main()
