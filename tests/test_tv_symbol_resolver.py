"""
單元測試：TradingView 符號解析與專案標準化對應模組 (tests/test_tv_symbol_resolver.py)
"""

import unittest
from core.services.symbol_manager.tv_symbol_resolver import (
    parse_tv_symbol,
    build_canonical_symbol,
    to_tv_symbol,
    resolve_to_project_symbol,
    TradingViewSymbolInfo
)


class TestTradingViewSymbolResolver(unittest.TestCase):
    """測試 TradingView 符號雙向解析功能"""

    def test_parse_binance_perp(self):
        info = parse_tv_symbol("BINANCE:SOLUSDC.P")
        self.assertEqual(info.tv_exchange, "BINANCE")
        self.assertEqual(info.ticker, "SOLUSDC.P")
        self.assertEqual(info.base, "SOL")
        self.assertEqual(info.quote, "USDC")
        self.assertTrue(info.is_perp)
        self.assertEqual(info.exchange, "binance")
        self.assertEqual(info.canonical_symbol, "SOL/USDC:USDC")

    def test_parse_binance_usdt_perp(self):
        info = parse_tv_symbol("BINANCE:BRUSDT.P")
        self.assertEqual(info.base, "BR")
        self.assertEqual(info.quote, "USDT")
        self.assertTrue(info.is_perp)
        self.assertEqual(info.canonical_symbol, "BR/USDT:USDT")

    def test_parse_binance_spot(self):
        info = parse_tv_symbol("BINANCE:BTCUSDT")
        self.assertEqual(info.base, "BTC")
        self.assertEqual(info.quote, "USDT")
        self.assertFalse(info.is_perp)
        self.assertEqual(info.canonical_symbol, "BTC/USDT")

    def test_parse_hyperliquid_perp(self):
        info = parse_tv_symbol("HYPERLIQUID:BTCUSDC.P")
        self.assertEqual(info.exchange, "hyperliquid")
        self.assertEqual(info.base, "BTC")
        self.assertEqual(info.quote, "USDC")
        self.assertTrue(info.is_perp)
        self.assertEqual(info.canonical_symbol, "BTC/USDC:USDC")

    def test_parse_backpack_perp(self):
        info = parse_tv_symbol("BACKPACK:SOLUSDC.P")
        self.assertEqual(info.exchange, "backpack")
        self.assertEqual(info.canonical_symbol, "SOL_USDC_PERP")

    def test_parse_lighter_perp(self):
        info = parse_tv_symbol("LIGHTER:BTC")
        self.assertEqual(info.exchange, "lighter")
        self.assertEqual(info.canonical_symbol, "BTC")

    def test_to_tv_symbol_roundtrip(self):
        # Binance perp
        tv1 = to_tv_symbol("binance", "SOL/USDC:USDC")
        self.assertEqual(tv1, "BINANCE:SOLUSDC.P")

        # Binance spot
        tv2 = to_tv_symbol("binance", "SOL/USDC")
        self.assertEqual(tv2, "BINANCE:SOLUSDC")

        # Binance USDT perp
        tv3 = to_tv_symbol("binance", "BR/USDT:USDT")
        self.assertEqual(tv3, "BINANCE:BRUSDT.P")

        # Hyperliquid perp
        tv4 = to_tv_symbol("hyperliquid", "BTC/USDC:USDC")
        self.assertEqual(tv4, "HYPERLIQUID:BTCUSDC.P")

        # Backpack perp
        tv5 = to_tv_symbol("backpack", "SOL_USDC_PERP")
        self.assertEqual(tv5, "BACKPACK:SOLUSDC.P")

        # Lighter perp
        tv6 = to_tv_symbol("lighter", "BTC")
        self.assertEqual(tv6, "LIGHTER:BTCUSD.P")

    def test_resolve_to_project_symbol(self):
        ex, sym = resolve_to_project_symbol("BINANCE:ETHUSDC.P")
        self.assertEqual(ex, "binance")
        self.assertEqual(sym, "ETH/USDC:USDC")

        # 強制指定 exchange
        ex2, sym2 = resolve_to_project_symbol("ETHUSDC.P", target_exchange="hyperliquid")
        self.assertEqual(ex2, "hyperliquid")
        self.assertEqual(sym2, "ETH/USDC:USDC")

    def test_empty_or_invalid_symbol(self):
        with self.assertRaises(ValueError):
            parse_tv_symbol("")
        with self.assertRaises(ValueError):
            parse_tv_symbol("   ")


if __name__ == "__main__":
    unittest.main()
