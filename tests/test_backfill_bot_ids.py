"""P1-1: backfill script adds metadata.bot_id to existing grid/dca YAML."""

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

import yaml

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "migration" / "backfill_bot_ids.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("backfill_bot_ids", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestBackfillBotIds(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "config" / "grid").mkdir(parents=True)
        (self.root / "config" / "dca").mkdir(parents=True)
        self.mod = _load_module()

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, rel, content):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_backfill_adds_metadata_bot_id_and_skips_defaults(self):
        self._write("config/grid/a-bot.yaml", "grid_system:\n  symbol: A/USDT\n  exchange: binance\n")
        self._write("config/dca/dca-binance-long-b.yaml", "dca:\n  symbol: B/USDT\n")
        self._write("config/grid/defaults.yaml", "grid_interval: 1\n")

        targets = [p.name for p in self.mod._target_files(self.root)]
        self.assertEqual(sorted(targets), ["a-bot.yaml", "dca-binance-long-b.yaml"])

        self.assertTrue(self.mod._needs_backfill(self.root / "config/grid/a-bot.yaml"))
        self.mod._apply_backfill(self.root / "config/grid/a-bot.yaml")

        data = yaml.safe_load((self.root / "config/grid/a-bot.yaml").read_text(encoding="utf-8"))
        self.assertEqual(data["metadata"]["bot_id"], "a-bot")

        # 冪等：已存在 bot_id 則不再視為需 backfill
        self.assertFalse(self.mod._needs_backfill(self.root / "config/grid/a-bot.yaml"))

    def test_backfill_preserves_existing_bot_id(self):
        self._write("config/grid/keep.yaml", "metadata:\n  bot_id: keep-me\n")
        self.assertFalse(self.mod._needs_backfill(self.root / "config/grid/keep.yaml"))
        self.mod._apply_backfill(self.root / "config/grid/keep.yaml")
        data = yaml.safe_load((self.root / "config/grid/keep.yaml").read_text(encoding="utf-8"))
        self.assertEqual(data["metadata"]["bot_id"], "keep-me")


if __name__ == "__main__":
    unittest.main()
