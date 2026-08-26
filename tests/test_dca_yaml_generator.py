"""DcaYAMLGenerator unit tests (Phase 4 audit 4.8 / 4.22 / 4.14)."""

import os
import sys
import unittest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND = os.path.join(PROJECT_ROOT, "web_ui", "backend")
for path in (BACKEND, PROJECT_ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)

import yaml

from core.services.dca.config_loader import load_dca_config
from dca_yaml_generator import DcaYAMLGenerator


def _base_params(**overrides):
    params = {
        "symbol": "ETH/USDC:USDC",
        "exchange": "binance",
        "direction": "long",
        "account_mode": "paper",
        "base_order_amount": "20",
        "max_safety_orders": 20,
        "price_deviation_percent": "0.8",
        "price_deviation_multiplier": "1.2",
        "order_size_multiplier": "1.0",
        "take_profit_percent": "1.0",
    }
    params.update(overrides)
    return params


class DcaYamlGeneratorTests(unittest.TestCase):
    def test_validate_accepts_vector_a(self):
        generator = DcaYAMLGenerator()
        self.assertEqual(generator.validate_params(_base_params()), [])

    def test_validate_collects_errors(self):
        generator = DcaYAMLGenerator()
        errors = generator.validate_params({})
        self.assertTrue(any("symbol" in e for e in errors))

        no_base = generator.validate_params({"symbol": "ETH/USDC:USDC"})
        self.assertTrue(any("base_order_amount" in e for e in no_base))

        # API 層預設 account_mode=paper（與 DcaConfig 預設一致），不強制使用者填寫。
        no_mode = generator.validate_params({
            "symbol": "ETH/USDC:USDC", "exchange": "binance", "base_order_amount": "20",
        })
        self.assertEqual(no_mode, [])

    def test_validate_requires_paper_or_live_account_mode(self):
        generator = DcaYAMLGenerator()
        errors = generator.validate_params(_base_params(account_mode="prod"))
        self.assertTrue(any("account_mode" in e for e in errors))

    def test_generate_round_trips_through_load_dca_config(self):
        generator = DcaYAMLGenerator(timestamp_factory=lambda: "20260826000000")
        yaml_content, path = generator.generate(_base_params())
        self.assertTrue(path.startswith("config/dca/dca-"))
        self.assertTrue(path.endswith(".yaml"))
        self.assertIn("dca:", yaml_content)
        config = load_dca_config(yaml.safe_load(yaml_content))
        self.assertEqual(config.symbol, "ETH/USDC:USDC")
        self.assertEqual(config.exchange, "binance")
        self.assertEqual(config.max_safety_orders, 20)
        self.assertEqual(config.account_mode, "paper")

    def test_generate_writes_indicator_config(self):
        params = _base_params()
        params["entry"] = {
            "mode": "indicator",
            "timeframe": "15m",
            "indicators": [
                {"type": "rsi", "period": 14, "oversold": 30.0, "overbought": 70.0, "condition": "level"},
                {"type": "macd", "fast": 12, "slow": 26, "signal": 9, "condition": "cross"},
                {"type": "bollinger", "period": 20, "std_dev": 2.0, "condition": "touch"},
                {"type": "price_change", "lookback_bars": 24, "long_threshold_percent": -3.0, "short_threshold_percent": 3.0},
            ],
        }
        generator = DcaYAMLGenerator(timestamp_factory=lambda: "20260826000000")
        self.assertEqual(generator.validate_params(params), [])
        yaml_content, _ = generator.generate(params)
        config = load_dca_config(yaml.safe_load(yaml_content))
        self.assertEqual(config.entry.mode.value, "indicator")
        self.assertEqual(len(config.entry.indicators), 4)

    def test_both_requires_valid_tiebreak(self):
        generator = DcaYAMLGenerator()
        self.assertEqual(generator.validate_params(_base_params(direction="both")), [])
        errors = generator.validate_params(_base_params(direction="both", both_tiebreak="sideways"))
        self.assertTrue(any("both_tiebreak" in e for e in errors))

    def test_bot_id_path_traversal_guard(self):
        self.assertTrue(DcaYAMLGenerator.is_valid_bot_id("dca-binance-long-eth-20260826"))
        self.assertFalse(DcaYAMLGenerator.is_valid_bot_id("../secret"))
        self.assertFalse(DcaYAMLGenerator.is_valid_bot_id("foo/bar"))
        self.assertFalse(DcaYAMLGenerator.is_valid_bot_id("foo\\bar"))
        self.assertEqual(DcaYAMLGenerator.bot_id_from_path("config/dca/x.yaml"), "x")
        self.assertEqual(DcaYAMLGenerator.path_from_bot_id("x"), "config/dca/x.yaml")


if __name__ == "__main__":
    unittest.main()
