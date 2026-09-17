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

    def test_tradfi_fallback_symbols_present(self):
        from core.services.coinfull_prime.quant_engine import DEFAULT_FALLBACK_TRADFI_SYMBOLS
        # 檢驗已知主要美股與大宗商品在兜底名單中
        for sym in ["NVDAUSDT", "TSLAUSDT", "SPYUSDT", "QQQUSDT", "AAPLUSDT", "XAUUSDT", "CLUSDT"]:
            self.assertIn(sym, DEFAULT_FALLBACK_TRADFI_SYMBOLS)

    def test_fetch_universe_tradfi_filtering(self):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        quant = CoinFullPrimeQuant({
            "universe": {"top_n": 10, "exclude_tradfi": True}
        })
        self.assertTrue(quant.exclude_tradfi)

        # 模擬 ticker 數據包含加密貨幣與美股/商品
        mock_tickers = [
            {"symbol": "BTCUSDT", "quoteVolume": "1000000000"},
            {"symbol": "NVDAUSDT", "quoteVolume": "900000000"},  # 美股 TradFi
            {"symbol": "ETHUSDT", "quoteVolume": "800000000"},
            {"symbol": "TSLAUSDT", "quoteVolume": "700000000"},  # 美股 TradFi
            {"symbol": "SOLUSDT", "quoteVolume": "600000000"},
            {"symbol": "XAUUSDT", "quoteVolume": "500000000"},   # 黃金 TradFi
            {"symbol": "DOGEUSDT", "quoteVolume": "400000000"},
            {"symbol": "USDCUSDT", "quoteVolume": "300000000"},  # 穩定幣
        ]

        # 模擬 session 與 response
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=mock_tickers)

        mock_session = MagicMock()
        mock_session.get.return_value.__aenter__.return_value = mock_resp

        # 1. 啟用 exclude_tradfi (預設)
        universe_pure = asyncio.run(quant.fetch_universe(mock_session, exclude_tradfi=True))
        self.assertIn("BTCUSDT", universe_pure)
        self.assertIn("ETHUSDT", universe_pure)
        self.assertIn("SOLUSDT", universe_pure)
        self.assertIn("DOGEUSDT", universe_pure)
        self.assertNotIn("NVDAUSDT", universe_pure)
        self.assertNotIn("TSLAUSDT", universe_pure)
        self.assertNotIn("XAUUSDT", universe_pure)
        self.assertNotIn("USDCUSDT", universe_pure)

        # 2. 停用 exclude_tradfi (包含美股與商品)
        universe_all = asyncio.run(quant.fetch_universe(mock_session, exclude_tradfi=False))
        self.assertIn("BTCUSDT", universe_all)
        self.assertIn("NVDAUSDT", universe_all)
        self.assertIn("TSLAUSDT", universe_all)
        self.assertIn("XAUUSDT", universe_all)
        self.assertNotIn("USDCUSDT", universe_all)  # 穩定幣仍應被排除

    def test_fetch_tradfi_symbols_from_exchange_info(self):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        quant = CoinFullPrimeQuant()

        mock_exchange_info = {
            "symbols": [
                {"symbol": "BTCUSDT", "contractType": "PERPETUAL", "underlyingType": "COIN"},
                {"symbol": "CUSTOM_STOCK_USDT", "contractType": "TRADIFI_PERPETUAL", "underlyingType": "EQUITY"},
                {"symbol": "CUSTOM_COMMODITY_USDT", "contractType": "PERPETUAL", "underlyingType": "COMMODITY"},
                {"symbol": "CUSTOM_INDEX_USDT", "contractType": "PERPETUAL", "underlyingType": "INDEX"},
            ]
        }

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=mock_exchange_info)

        mock_session = MagicMock()
        mock_session.get.return_value.__aenter__.return_value = mock_resp

        tradfi_set = asyncio.run(quant.fetch_tradfi_symbols(mock_session))
        self.assertIn("CUSTOM_STOCK_USDT", tradfi_set)
        self.assertIn("CUSTOM_COMMODITY_USDT", tradfi_set)
        self.assertIn("CUSTOM_INDEX_USDT", tradfi_set)
        self.assertNotIn("BTCUSDT", tradfi_set)


if __name__ == "__main__":
    unittest.main()
