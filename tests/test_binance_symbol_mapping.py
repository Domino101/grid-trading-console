import unittest
from core.adapters.exchanges import ExchangeConfig
from core.adapters.exchanges.adapters.binance_base import BinanceBase
from core.adapters.exchanges.models import ExchangeType
from core.services.dca.config_loader import auto_fill_dca_precision
from core.services.dca.models.dca_config import DcaConfig
from decimal import Decimal


class TestBinanceSymbolMapping(unittest.TestCase):
    def setUp(self):
        config = ExchangeConfig(
            exchange_id="binance",
            name="Binance",
            exchange_type=ExchangeType.PERPETUAL,
            api_key="mock_key",
            api_secret="mock_secret",
        )
        self.base = BinanceBase(config)

    def test_map_symbol_offline_no_markets(self):
        """測試在無 markets 快取時的解析（避免 duplicate quote 如 牛来USDTUSDT）"""
        cases = [
            ("牛来USDT", "牛来USDT"),
            ("牛来usdt", "牛来USDT"),
            ("牛来/USDT", "牛来USDT"),
            ("牛来/USDT:USDT", "牛来USDT"),
            ("牛来/usdt:usdt", "牛来USDT"),
            ("牛来-USDT", "牛来USDT"),
            ("牛来_USDT", "牛来USDT"),
            ("牛来", "牛来USDT"),
            ("BTCUSDT", "BTCUSDT"),
            ("btcusdt", "BTCUSDT"),
            ("BTC/USDT", "BTCUSDT"),
            ("BTC/USDT:USDT", "BTCUSDT"),
            ("BTC-USDT", "BTCUSDT"),
            ("btc_usdt", "BTCUSDT"),
            ("BTC", "BTCUSDT"),
            ("ETHUSDC", "ETHUSDC"),
            ("ethusdc", "ETHUSDC"),
            ("ETH/USDC", "ETHUSDC"),
            ("ETH/USDC:USDC", "ETHUSDC"),
            ("eth-usdc", "ETHUSDC"),
            ("ETH:USDC", "ETHUSDC"),
        ]
        for inp, expected in cases:
            with self.subTest(inp=inp):
                res = self.base.map_symbol_to_binance(inp)
                self.assertEqual(res, expected, f"Failed for input: {inp}")

    def test_map_symbol_with_markets_dict(self):
        """測試載入 markets 字典後的 ID 解析與反向解析"""
        mock_markets = {
            "牛来/USDT:USDT": {
                "id": "牛来USDT",
                "lowercaseId": "牛来usdt",
                "symbol": "牛来/USDT:USDT",
                "base": "牛来",
                "quote": "USDT",
                "precision": {"price": 1e-5, "amount": 1.0},
            },
            "BTC/USDT:USDT": {
                "id": "BTCUSDT",
                "lowercaseId": "btcusdt",
                "symbol": "BTC/USDT:USDT",
                "base": "BTC",
                "quote": "USDT",
                "precision": {"price": 0.1, "amount": 0.001},
            },
            "ETH/USDC:USDC": {
                "id": "ETHUSDC",
                "lowercaseId": "ethusdc",
                "symbol": "ETH/USDC:USDC",
                "base": "ETH",
                "quote": "USDC",
                "precision": {"price": 0.01, "amount": 0.01},
            },
        }
        self.base._market_info = mock_markets

        cases = [
            ("牛来USDT", "牛来USDT"),
            ("牛来usdt", "牛来USDT"),
            ("牛来/USDT", "牛来USDT"),
            ("牛来/USDT:USDT", "牛来USDT"),
            ("牛来-USDT", "牛来USDT"),
            ("BTCUSDT", "BTCUSDT"),
            ("btcusdt", "BTCUSDT"),
            ("BTC/USDT", "BTCUSDT"),
            ("BTC/USDT:USDT", "BTCUSDT"),
            ("ETHUSDC", "ETHUSDC"),
            ("ETH/USDC:USDC", "ETHUSDC"),
        ]
        for inp, expected in cases:
            with self.subTest(inp=inp):
                res = self.base.map_symbol_to_binance(inp)
                self.assertEqual(res, expected, f"Failed for input: {inp}")

        # 反向解析
        self.assertEqual(self.base.map_symbol_from_binance("牛来USDT"), "牛来/USDT:USDT")
        self.assertEqual(self.base.map_symbol_from_binance("BTCUSDT"), "BTC/USDT:USDT")
        self.assertEqual(self.base.map_symbol_from_binance("ETHUSDC"), "ETH/USDC:USDC")

    def test_auto_fill_dca_precision_with_unslashed_symbol(self):
        """測試 DCA 精度自動填入相容不帶斜線的符號（例如 牛来USDT）"""
        class FakeAdapter:
            class FakeExchange:
                markets = {
                    "牛来/USDT:USDT": {
                        "id": "牛来USDT",
                        "symbol": "牛来/USDT:USDT",
                        "precision": {"price": 1e-5, "amount": 1.0},
                    }
                }
                def market(self, sym):
                    if sym in ("牛来USDT", "牛来/USDT:USDT"):
                        return self.markets["牛来/USDT:USDT"]
                    raise ValueError(sym)
            exchange = FakeExchange()

        # 1. 傳入 牛来USDT
        cfg1 = DcaConfig(
            exchange="binance",
            symbol="牛来USDT",
            direction="short",
            base_order_amount=Decimal("20.0"),
            max_safety_orders=5,
            price_deviation_percent=Decimal("1.0"),
            take_profit_percent=Decimal("1.0"),
        )
        auto_fill_dca_precision(cfg1, FakeAdapter())
        self.assertEqual(cfg1.quantity_precision, 0)  # tick=1.0 -> 0 decimals
        self.assertEqual(cfg1.price_decimals, 5)     # tick=1e-5 -> 5 decimals

        # 2. 傳入 牛来/USDT:USDT
        cfg2 = DcaConfig(
            exchange="binance",
            symbol="牛来/USDT:USDT",
            direction="short",
            base_order_amount=Decimal("20.0"),
            max_safety_orders=5,
            price_deviation_percent=Decimal("1.0"),
            take_profit_percent=Decimal("1.0"),
        )
        auto_fill_dca_precision(cfg2, FakeAdapter())
        self.assertEqual(cfg2.quantity_precision, 0)
        self.assertEqual(cfg2.price_decimals, 5)


if __name__ == "__main__":
    unittest.main()
