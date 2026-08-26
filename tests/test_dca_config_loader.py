"""DCA YAML 設定載入器測試（Phase 3 稽核 3.1–3.4、3.11）。"""

import os
import sys
import unittest
from decimal import Decimal as D

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.services.dca.config_loader import (
    _warn_unknown_keys,
    auto_fill_dca_precision,
    dca_account_mode_from_yaml,
    dca_config_to_dict,
    load_dca_config,
)
from core.services.dca.models.dca_config import DcaConfigError


class DcaConfigLoaderTests(unittest.TestCase):
    def test_example_yaml_loads_and_matches_plan(self):
        """範例檔可載入，且基礎欄位符合 §4.2 預設。"""
        import yaml
        with open("config/dca/example-dca-paper-eth.yaml", "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        config = load_dca_config(data)
        self.assertEqual(config.symbol, "ETH/USDC:USDC")
        self.assertEqual(config.direction.value, "long")
        self.assertEqual(config.max_safety_orders, 20)
        self.assertEqual(config.account_mode, "paper")
        self.assertEqual(config.entry.indicators[0].period, 14)

    def test_round_trip_yaml_equality(self):
        """稽核 3.1：DcaConfig → dict → load_dca_config 所有欄位相等。"""
        import yaml
        with open("config/dca/example-dca-paper-eth.yaml", "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        original = load_dca_config(data)
        dumped = yaml.safe_dump(dca_config_to_dict(original), sort_keys=False,
                                allow_unicode=True)
        reloaded = load_dca_config(yaml.safe_load(dumped))
        self.assertEqual(original, reloaded)
        # Decimal 精度逐欄斷言
        self.assertEqual(reloaded.base_order_amount, D("20"))
        self.assertEqual(reloaded.fee_rate, D("0.0004"))
        self.assertEqual(reloaded.price_deviation_percent, D("0.8"))

    def test_unknown_key_is_reported_not_silently_swallowed(self):
        """稽核 3.2：未知鍵以 warning 日誌明確列出，不靜默吞掉。"""
        unknown = _warn_unknown_keys(
            {"symbol": "ETH/USDC:USDC", "mystery": 1},
            frozenset({"symbol"}),
            "dca",
        )
        self.assertEqual(unknown, ["mystery"])

    def test_unknown_key_does_not_break_load(self):
        import yaml
        with open("config/dca/example-dca-paper-eth.yaml", "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        data["dca"]["future_option"] = "not-a-real-field"
        config = load_dca_config(data)
        self.assertEqual(config.symbol, "ETH/USDC:USDC")

    def test_missing_required_symbol_errors(self):
        """稽核 3.3：缺 symbol → 明確中文錯誤。"""
        with self.assertRaises(DcaConfigError) as ctx:
            load_dca_config({
                "dca": {
                    "exchange": "binance", "base_order_amount": "20",
                    "account_mode": "paper",
                }
            })
        self.assertIn("symbol", str(ctx.exception))

    def test_missing_required_base_order_amount_errors(self):
        """稽核 3.3：缺 base_order_amount → 明確中文錯誤，不用 0。"""
        with self.assertRaises(DcaConfigError) as ctx:
            load_dca_config({
                "dca": {
                    "exchange": "binance", "symbol": "ETH/USDC:USDC",
                    "account_mode": "paper",
                }
            })
        self.assertIn("base_order_amount", str(ctx.exception))

    def test_missing_account_mode_is_unknown_not_paper(self):
        """稽核 3.4：未標 account_mode → unknown，且載入拒絕自動視為 paper。"""
        data = {"dca": {"exchange": "binance", "symbol": "ETH/USDC:USDC",
                        "base_order_amount": "20"}}
        self.assertEqual(dca_account_mode_from_yaml(data), "unknown")
        with self.assertRaises(DcaConfigError) as ctx:
            load_dca_config(data)
        self.assertIn("account_mode", str(ctx.exception))
        self.assertIn("unknown", str(ctx.exception))

    def test_flat_dict_is_accepted(self):
        """扁平 dict 相容（無 dca: 外層鍵）。"""
        config = load_dca_config({
            "exchange": "binance", "symbol": "ETH/USDC:USDC",
            "base_order_amount": "20", "account_mode": "paper",
        })
        self.assertEqual(config.exchange, "binance")

    def test_auto_fill_dca_precision(self):
        """執行期精度自動填入：adapter 提供 ccxt markets 時填入 None 欄位。"""
        class _Exchange:
            def __init__(self):
                self.markets = {
                    "ETH/USDC:USDC": {"precision": {"amount": 0.001, "price": 0.01}}
                }
        class _Adapter:
            def __init__(self):
                self.exchange = _Exchange()

        config = load_dca_config({
            "dca": {
                "exchange": "binance", "symbol": "ETH/USDC:USDC",
                "base_order_amount": "20", "account_mode": "paper",
            }
        })
        self.assertIsNone(config.quantity_precision)
        self.assertIsNone(config.price_decimals)
        auto_fill_dca_precision(config, _Adapter())
        self.assertEqual(config.quantity_precision, 3)
        self.assertEqual(config.price_decimals, 2)


if __name__ == "__main__":
    unittest.main()
