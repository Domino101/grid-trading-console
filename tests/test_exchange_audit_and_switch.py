"""
Unit tests verifying multi-exchange audit fixes:
1. ExchangeFactory registration and instantiation of okx adapter.
2. YAML generator support for okx, backpack, binance, hyperliquid, lighter.
3. DCA config validation for okx.
4. Grid backtest fee rate resolution per venue.
5. KlineService CCXT handling (rejecting silent fallback to Binance for unknown exchanges).
"""

import unittest
from core.adapters.exchanges.factory import ExchangeFactory
from core.services.dca.models.dca_config import DcaConfig, VALID_EXCHANGES
from web_ui.backend.yaml_generator import GridYAMLGenerator
from decimal import Decimal
from web_ui.backend.grid_backtest_service import DEFAULT_EXCHANGE_FEE_RATES
from web_ui.backend.kline_service import _default_fetch_ccxt_ohlcv


class TestExchangeAuditAndSwitch(unittest.TestCase):

    def test_factory_registers_and_creates_okx(self):
        factory = ExchangeFactory()
        registered = factory.get_registered_exchanges()
        self.assertIn("okx", registered)
        self.assertIn("binance", registered)
        self.assertIn("hyperliquid", registered)
        self.assertIn("lighter", registered)
        self.assertIn("backpack", registered)

        # Create OKX adapter instance
        adapter = factory.create_adapter(
            "okx",
            api_key="test_key",
            api_secret="test_secret",
            api_passphrase="test_passphrase",
            testnet=True,
        )
        self.assertIsNotNone(adapter)
        self.assertEqual(adapter.config.exchange_id, "okx")

    def test_yaml_generator_accepts_okx_and_backpack(self):
        generator = GridYAMLGenerator()
        yaml_str, _ = generator.generate({
            "exchange": "okx",
            "symbol": "BTC-USDT-SWAP",
            "grid_type": "neutral",
            "grid_interval": 100.0,
            "order_amount": 0.01,
            "lower_price": 50000.0,
            "upper_price": 70000.0,
        })
        self.assertIn("exchange: okx", yaml_str)
        self.assertIn("symbol: BTC-USDT-SWAP", yaml_str)

        yaml_bp, _ = generator.generate({
            "exchange": "backpack",
            "symbol": "SOL_USDC_PERP",
            "grid_type": "neutral",
            "grid_interval": 1.0,
            "order_amount": 0.1,
            "lower_price": 100.0,
            "upper_price": 200.0,
        })
        self.assertIn("exchange: backpack", yaml_bp)

    def test_dca_config_accepts_okx(self):
        self.assertIn("okx", VALID_EXCHANGES)
        config = DcaConfig(
            exchange="okx",
            symbol="BTC-USDT-SWAP",
            base_order_amount=10,
            account_mode="paper",
            direction="long",
        )
        self.assertEqual(config.exchange, "okx")

    def test_grid_backtest_venue_fee_rates(self):
        self.assertEqual(DEFAULT_EXCHANGE_FEE_RATES.get("binance"), Decimal("0.0002"))
        self.assertEqual(DEFAULT_EXCHANGE_FEE_RATES.get("okx"), Decimal("0.0002"))
        self.assertEqual(DEFAULT_EXCHANGE_FEE_RATES.get("hyperliquid"), Decimal("0.0001"))
        self.assertEqual(DEFAULT_EXCHANGE_FEE_RATES.get("lighter"), Decimal("0.0000"))
        self.assertEqual(DEFAULT_EXCHANGE_FEE_RATES.get("backpack"), Decimal("0.0002"))
        self.assertEqual(DEFAULT_EXCHANGE_FEE_RATES.get("paper"), Decimal("0.0002"))

    def test_kline_service_rejects_unsupported_ccxt_exchange(self):
        # Should return empty list rather than silently returning Binance candles
        candles = _default_fetch_ccxt_ohlcv("nonexistent_dex_xyz", "BTC/USDT", "1h", 10)
        self.assertEqual(candles, [])


if __name__ == "__main__":
    unittest.main()
