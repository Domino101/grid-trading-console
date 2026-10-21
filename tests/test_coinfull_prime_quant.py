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
            },
            # 這組測試驗證 v2 basket 對稱空頭；v3 預設是 benchmark，需顯式指定。
            "hedge": {"mode": "basket"},
            "universe": {"min_24h_volume_usdt": 0.0, "max_7d_atr_pct": 0.0},
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


    def test_compute_ranks_benchmark_hedge(self):
        # 驗證 v3.0 Opt C: 50% BTC 基準指數對沖
        cfg = {
            "portfolio": {
                "long_count": 3,
                "short_count": 1,
                "total_capital_usdt": 1000.0,
                "leverage": 1.0
            },
            "hedge": {
                "mode": "benchmark",
                "symbol": "BTCUSDT",
                "weight": 0.50
            },
            "universe": {"min_24h_volume_usdt": 0.0, "max_7d_atr_pct": 0.0},
        }
        quant = CoinFullPrimeQuant(cfg)
        features = [
            RawFeature(symbol="BTCUSDT", momentum=20.0, flow=3.0, funding=0.01, liquidation=2.0), # 即使評分最高
            RawFeature(symbol="SOLUSDT", momentum=15.0, flow=2.5, funding=0.01, liquidation=1.8),
            RawFeature(symbol="AVAXUSDT", momentum=10.0, flow=2.0, funding=0.005, liquidation=1.5),
            RawFeature(symbol="LINKUSDT", momentum=8.0, flow=1.5, funding=0.0, liquidation=1.2),
            RawFeature(symbol="DOGEUSDT", momentum=-5.0, flow=0.8, funding=-0.01, liquidation=0.8),
        ]
        result = quant.compute_ranks(features)
        self.assertEqual(result.status, "success")
        # 1. BTCUSDT 作為對沖標的，不得進入做多清單
        long_symbols = [x.symbol for x in result.long_list]
        self.assertNotIn("BTCUSDT", long_symbols)
        self.assertEqual(len(result.long_list), 3)
        self.assertEqual(result.long_list[0].symbol, "SOLUSDT")
        # 多頭每檔名義資金: 1000 * 0.5 / 3 = 166.67
        self.assertAlmostEqual(result.long_list[0].target_notional, 166.67, places=1)

        # 2. 空頭清單必須唯一為 BTCUSDT，佔 50% 總名義本金 (500 USDT)
        self.assertEqual(len(result.short_list), 1)
        self.assertEqual(result.short_list[0].symbol, "BTCUSDT")
        self.assertEqual(result.short_list[0].side, "short")
        self.assertAlmostEqual(result.short_list[0].target_weight, 0.50, places=2)
        self.assertAlmostEqual(result.short_list[0].target_notional, 500.0, places=1)

    def test_compute_ranks_dual_benchmark_hedge(self):
        # 驗證 v3.0 Opt B: 雙主流 BTC + ETH 基準對沖 (各 25%)
        cfg = {
            "portfolio": {
                "long_count": 2,
                "short_count": 2,
                "total_capital_usdt": 1000.0,
                "leverage": 1.0
            },
            "hedge": {
                "mode": "dual_benchmark",
                "dual_weights": {
                    "BTCUSDT": 0.25,
                    "ETHUSDT": 0.25
                }
            },
            "universe": {"min_24h_volume_usdt": 0.0, "max_7d_atr_pct": 0.0},
        }
        quant = CoinFullPrimeQuant(cfg)
        features = [
            RawFeature(symbol="BTCUSDT", momentum=20.0, flow=3.0, funding=0.01, liquidation=2.0),
            RawFeature(symbol="ETHUSDT", momentum=18.0, flow=2.8, funding=0.01, liquidation=1.9),
            RawFeature(symbol="SOLUSDT", momentum=15.0, flow=2.5, funding=0.01, liquidation=1.8),
            RawFeature(symbol="AVAXUSDT", momentum=10.0, flow=2.0, funding=0.005, liquidation=1.5),
        ]
        result = quant.compute_ranks(features)
        self.assertEqual(len(result.short_list), 2)
        short_map = {x.symbol: x for x in result.short_list}
        self.assertIn("BTCUSDT", short_map)
        self.assertIn("ETHUSDT", short_map)
        self.assertAlmostEqual(short_map["BTCUSDT"].target_weight, 0.25, places=2)
        self.assertAlmostEqual(short_map["BTCUSDT"].target_notional, 250.0, places=1)
        self.assertAlmostEqual(short_map["ETHUSDT"].target_weight, 0.25, places=2)
        self.assertAlmostEqual(short_map["ETHUSDT"].target_notional, 250.0, places=1)

    def test_compute_ranks_volume_filter(self):
        # 驗證 24h 成交額門檻初篩
        cfg = {
            "portfolio": {"long_count": 2, "short_count": 1, "total_capital_usdt": 1000.0},
            "universe": {"min_24h_volume_usdt": 50000000.0, "max_7d_atr_pct": 0.0},
            "hedge": {"mode": "benchmark", "symbol": "BTCUSDT", "weight": 0.50}
        }
        quant = CoinFullPrimeQuant(cfg)
        quant.quote_volumes = {
            "BTCUSDT": 1000000000.0,
            "HIGH_MOM_MICRO_CAP": 5000000.0,  # 成交額僅 500 萬，未達 5000 萬門檻
            "GOOD_MID_CAP": 80000000.0,       # 達標
            "GOOD_LARGE_CAP": 200000000.0,    # 達標
        }
        features = [
            RawFeature(symbol="BTCUSDT", momentum=20.0, flow=3.0, funding=0.01, liquidation=2.0),
            RawFeature(symbol="HIGH_MOM_MICRO_CAP", momentum=50.0, flow=4.0, funding=0.01, liquidation=2.0), # 分數最高但流動性不足
            RawFeature(symbol="GOOD_MID_CAP", momentum=15.0, flow=2.5, funding=0.01, liquidation=1.8),
            RawFeature(symbol="GOOD_LARGE_CAP", momentum=10.0, flow=2.0, funding=0.005, liquidation=1.5),
        ]
        result = quant.compute_ranks(features)
        long_symbols = [x.symbol for x in result.long_list]
        self.assertNotIn("HIGH_MOM_MICRO_CAP", long_symbols)
        self.assertIn("GOOD_MID_CAP", long_symbols)
        self.assertIn("GOOD_LARGE_CAP", long_symbols)


if __name__ == "__main__":
    unittest.main()

