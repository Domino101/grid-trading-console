"""
CoinFull Prime 量化計算單元測試
"""

import unittest
import numpy as np
from core.services.coinfull_prime.models import RawFeature
from core.services.coinfull_prime.quant_engine import CoinFullPrimeQuant


class TestCoinFullPrimeQuant(unittest.TestCase):

    def setUp(self):
        self.config = {
            "weights": {
                "momentum": 0.4,
                "flow": 0.3,
                "funding": 0.2,
                "liquidation": 0.1
            },
            "portfolio": {
                "long_count": 2,
                "short_count": 2,
                "total_capital_usdt": 1000.0,
                "leverage": 1.0
            }
        }
        self.quant = CoinFullPrimeQuant(self.config)

    def test_winsorize_and_zscore_normal(self):
        # 構造帶有顯著極值的分佈
        data = [1.0, 2.0, 2.5, 3.0, 3.5, 4.0, 1000.0, -1000.0]
        z = self.quant.winsorize_and_zscore(data, limits=(0.1, 0.9))
        self.assertEqual(len(z), len(data))
        # 去極值後，標準化數值應介於合理區間（例如不會超過 ±5）
        self.assertTrue(np.all(np.abs(z) < 5.0))
        # 均值應趨近 0
        self.assertAlmostEqual(float(np.mean(z)), 0.0, places=5)
        # 標準差應趨近 1
        self.assertAlmostEqual(float(np.std(z)), 1.0, places=5)

    def test_winsorize_and_zscore_constant_or_empty(self):
        # 全常數邊界條件
        const_data = [5.0, 5.0, 5.0, 5.0]
        z_const = self.quant.winsorize_and_zscore(const_data)
        self.assertTrue(np.all(z_const == 0.0))

        # 空陣列
        z_empty = self.quant.winsorize_and_zscore([])
        self.assertEqual(len(z_empty), 0)

        # 單元素
        z_single = self.quant.winsorize_and_zscore([42.0])
        self.assertEqual(len(z_single), 1)
        self.assertEqual(z_single[0], 0.0)

    def test_compute_ranks_correctness(self):
        # 構造 6 個幣種特徵
        features = [
            RawFeature(symbol="AAA", momentum=10.0, flow=2.5, funding=0.01, liquidation=2.0),
            RawFeature(symbol="BBB", momentum=5.0, flow=1.8, funding=0.005, liquidation=1.5),
            RawFeature(symbol="CCC", momentum=1.0, flow=1.0, funding=0.0, liquidation=1.0),
            RawFeature(symbol="DDD", momentum=-1.0, flow=0.9, funding=-0.005, liquidation=0.9),
            RawFeature(symbol="EEE", momentum=-5.0, flow=0.5, funding=-0.01, liquidation=0.6),
            RawFeature(symbol="FFF", momentum=-10.0, flow=0.2, funding=-0.02, liquidation=0.4),
        ]

        result = self.quant.compute_ranks(features)
        self.assertEqual(result.status, "success")
        self.assertEqual(result.universe_size, 6)

        # 最強幣種 AAA 應排第 1 名
        self.assertEqual(result.long_list[0].symbol, "AAA")
        self.assertEqual(result.long_list[0].rank, 1)
        self.assertEqual(result.long_list[0].side, "long")

        # 最弱幣種 FFF 應在 short_list 最後一名
        self.assertEqual(result.short_list[-1].symbol, "FFF")
        self.assertEqual(result.short_list[-1].rank, 6)
        self.assertEqual(result.short_list[-1].side, "short")

        # 檢驗多空個數
        self.assertEqual(len(result.long_list), 2)
        self.assertEqual(len(result.short_list), 2)

        # 檢驗權重與目標名義本金 (總資金 1000 * 1.0 = 1000, 多頭 500 / 2 = 250)
        self.assertAlmostEqual(result.long_list[0].target_notional, 250.0, places=1)
        self.assertAlmostEqual(result.short_list[0].target_notional, 250.0, places=1)

    def test_compute_ranks_with_capital_and_leverage_overrides(self):
        features = [
            RawFeature(symbol="AAA", momentum=10.0, flow=2.5, funding=0.01, liquidation=2.0),
            RawFeature(symbol="BBB", momentum=5.0, flow=1.8, funding=0.005, liquidation=1.5),
            RawFeature(symbol="CCC", momentum=1.0, flow=1.0, funding=0.0, liquidation=1.0),
            RawFeature(symbol="DDD", momentum=-1.0, flow=0.9, funding=-0.005, liquidation=0.9),
            RawFeature(symbol="EEE", momentum=-5.0, flow=0.5, funding=-0.01, liquidation=0.6),
            RawFeature(symbol="FFF", momentum=-10.0, flow=0.2, funding=-0.02, liquidation=0.4),
        ]
        # 傳入本金 2000.0，槓桿 1.5x (總曝險 = 3000.0, 多頭總資金 = 1500.0, 2 檔多頭 -> 單幣 750.0 USDT)
        result = self.quant.compute_ranks(features, capital_override=2000.0, leverage_override=1.5)
        self.assertEqual(result.status, "success")
        self.assertAlmostEqual(result.long_list[0].target_notional, 750.0, places=1)
        self.assertAlmostEqual(result.short_list[0].target_notional, 750.0, places=1)


if __name__ == "__main__":
    unittest.main()
