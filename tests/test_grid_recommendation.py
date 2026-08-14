"""
Unit tests for AI Grid Recommendation and Backtest Service.
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../web_ui/backend"))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from web_ui.backend.grid_recommendation import (
    _simulate_grid_backtest,
    _normalize_symbol_for_ccxt,
    generate_recommendation_sync,
)


class TestGridRecommendation(unittest.TestCase):
    def test_normalize_symbol_for_ccxt(self):
        self.assertEqual(_normalize_symbol_for_ccxt("BTC/USDT", "binance"), "BTC/USDT")
        self.assertEqual(_normalize_symbol_for_ccxt("BTCUSDT", "binance"), "BTC/USDT")
        self.assertEqual(_normalize_symbol_for_ccxt("ETH/USDC:USDC", "binance"), "ETH/USDC:USDC")

    def test_simulate_grid_backtest(self):
        # 建立一個上下震盪的模擬 K 線序列
        # [timestamp, open, high, low, close, volume]
        candles = [
            [1000, 100.0, 105.0, 95.0, 100.0, 1000],
            [2000, 100.0, 110.0, 90.0, 105.0, 1000],
            [3000, 105.0, 115.0, 85.0, 95.0, 1000],
            [4000, 95.0, 105.0, 90.0, 100.0, 1000],
            [5000, 100.0, 110.0, 95.0, 105.0, 1000],
        ]
        cycles, apr = _simulate_grid_backtest(
            ohlcv_list=candles,
            lower_price=80.0,
            upper_price=120.0,
            grid_count=10,
            fee_rate_pct=0.04
        )
        self.assertGreaterEqual(cycles, 1)
        self.assertGreater(apr, 0.0)

    @patch("web_ui.backend.grid_recommendation._fetch_ohlcv_sync")
    def test_generate_recommendation_sync(self, mock_fetch):
        # 產生 100 根模擬 1h K 線
        mock_candles = []
        base = 50000.0
        for i in range(100):
            mock_candles.append([
                i * 3600000,
                base,
                base + 500 + (i % 10) * 100,
                base - 500 - (i % 10) * 100,
                base + 100,
                100.0
            ])
        mock_fetch.return_value = mock_candles

        result = generate_recommendation_sync(
            symbol="BTC/USDT",
            exchange_id="binance",
            target_days=7,
            price_precision=2,
            quantity_precision=4,
            min_notional=5.0
        )

        self.assertEqual(result["symbol"], "BTC/USDT")
        self.assertEqual(result["exchange"], "binance")
        self.assertIn("7d", result["periods"])
        self.assertIn("30d", result["periods"])
        self.assertIn("180d", result["periods"])
        self.assertIn("recommended", result)
        self.assertGreater(result["recommended"]["estimated_apr"], 0.0)
        self.assertGreater(result["recommended"]["grid_count"], 0)
        self.assertGreater(result["recommended"]["upper_price"], result["recommended"]["lower_price"])

    @patch("web_ui.backend.grid_recommendation._fetch_ohlcv_sync")
    @patch("web_ui.backend.grid_recommendation._get_top_market_tickers_sync")
    def test_get_market_rankings_sync(self, mock_top_tickers, mock_fetch_ohlcv):
        mock_top_tickers.return_value = [
            {"symbol": "SOL/USDT", "last": 150.0, "quoteVolume": 500000000.0, "percentage": 5.2},
            {"symbol": "BTC/USDT", "last": 60000.0, "quoteVolume": 1000000000.0, "percentage": 1.5},
        ]
        mock_fetch_ohlcv.return_value = [
            [1000, 100.0, 110.0, 90.0, 100.0, 1000],
            [2000, 100.0, 115.0, 85.0, 105.0, 1000],
            [3000, 105.0, 120.0, 95.0, 100.0, 1000],
            [4000, 100.0, 110.0, 90.0, 95.0, 1000],
            [5000, 95.0, 105.0, 85.0, 100.0, 1000],
        ]

        from web_ui.backend.grid_recommendation import get_market_rankings_sync, _rankings_cache
        _rankings_cache.clear()

        rankings = get_market_rankings_sync(exchange_id="binance", period="7d", limit=10)

        self.assertEqual(rankings["exchange"], "binance")
        self.assertEqual(rankings["period"], "7d")
        self.assertIsInstance(rankings["items"], list)
        self.assertGreaterEqual(len(rankings["items"]), 1)
        self.assertEqual(rankings["items"][0]["rank"], 1)
        self.assertIn("rating", rankings["items"][0])

    @patch("web_ui.backend.grid_recommendation._fetch_ohlcv_sync")
    @patch("web_ui.backend.grid_recommendation._get_top_market_tickers_sync")
    def test_rankings_disk_persistence_and_restart_recovery(self, mock_top_tickers, mock_fetch_ohlcv):
        """測試排行榜在記憶體清空（模擬重啟）後，可直接從 SQLite 磁碟載入，無需呼叫交易所 API"""
        import tempfile
        from web_ui.backend.grid_recommendation import get_market_rankings_sync, _rankings_cache

        mock_top_tickers.return_value = [
            {"symbol": "SOL/USDT", "last": 150.0, "quoteVolume": 500000000.0, "percentage": 5.2},
        ]
        mock_fetch_ohlcv.return_value = [
            [1000, 100.0, 110.0, 90.0, 100.0, 1000],
            [2000, 100.0, 115.0, 85.0, 105.0, 1000],
            [3000, 105.0, 120.0, 95.0, 100.0, 1000],
            [4000, 100.0, 110.0, 90.0, 95.0, 1000],
            [5000, 95.0, 105.0, 85.0, 100.0, 1000],
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_db = os.path.join(temp_dir, "test_runtime.sqlite3")
            with patch("web_ui.backend.grid_recommendation._get_db_path", return_value=temp_db):
                _rankings_cache.clear()

                # 1. 首次呼叫：會計算並存入磁碟與記憶體
                first_res = get_market_rankings_sync(exchange_id="binance", period="30d", limit=5)
                self.assertEqual(first_res["cached"], False)
                self.assertEqual(mock_top_tickers.call_count, 1)

                # 2. 模擬後端進程重啟：清空 Python 記憶體快取
                _rankings_cache.clear()
                mock_top_tickers.reset_mock()
                mock_fetch_ohlcv.reset_mock()

                # 3. 重啟後再次呼叫：應直接從 SQLite 載入，不觸發交易所 API
                restarted_res = get_market_rankings_sync(exchange_id="binance", period="30d", limit=5)
                self.assertEqual(restarted_res["cached"], True)
                self.assertEqual(mock_top_tickers.call_count, 0)
                self.assertEqual(mock_fetch_ohlcv.call_count, 0)
                self.assertEqual(len(restarted_res["items"]), len(first_res["items"]))
                self.assertEqual(restarted_res["items"][0]["symbol"], first_res["items"][0]["symbol"])



if __name__ == "__main__":
    unittest.main()


