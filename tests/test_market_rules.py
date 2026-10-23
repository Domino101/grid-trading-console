"""MarketRules：交易所過濾器快取與量化（BRUSDT tick 事故回歸）。

守門目標（Level 3 行為測試）：
- tickSize 由 0.00001 改為 0.0001 後，舊快取必須可被強制刷新。
- 任何送單價格都必須落在 live tick 上（含 grid_interval 不是 tick 倍數的情境）。
"""

from __future__ import annotations

import time
import unittest
from decimal import Decimal

from core.adapters.exchanges.market_rules import (
    MarketRulesCache,
    normalize_grid_config,
    resolve_ccxt_exchange,
    rules_from_market,
)
from core.services.grid.models.grid_config import GridConfig, GridType


def _market(tick: str = "0.0001000", step: str = "1", notional: str = "5") -> dict:
    return {
        "id": "BRUSDT",
        "symbol": "BR/USDT:USDT",
        "precision": {"price": float(tick), "amount": float(step)},
        "limits": {"cost": {"min": float(notional)}},
        "info": {
            "filters": [
                {"filterType": "PRICE_FILTER", "tickSize": tick},
                {"filterType": "LOT_SIZE", "stepSize": step},
                {"filterType": "MIN_NOTIONAL", "notional": notional},
            ]
        },
    }


class FakeExchange:
    id = "binance"

    def __init__(self, tick: str = "0.0001000"):
        self.tick = tick
        self.markets = {}
        self.load_calls = 0
        self._build()

    def _build(self):
        self.markets = {"BR/USDT:USDT": _market(self.tick)}

    def load_markets(self, reload=False):  # noqa: ARG002 - ccxt signature
        self.load_calls += 1
        self._build()
        return self.markets

    def market(self, symbol):
        return self.markets.get(symbol)


class MarketRulesExtractionTests(unittest.TestCase):
    def test_extracts_exact_tick_step_and_notional(self):
        rules = rules_from_market("BR/USDT:USDT", _market())
        self.assertEqual(rules.price_tick, Decimal("0.0001"))
        self.assertEqual(rules.qty_step, Decimal("1"))
        self.assertEqual(rules.min_notional, Decimal("5"))
        self.assertEqual(rules.price_decimals, 4)
        self.assertEqual(rules.qty_decimals, 0)

    def test_quantize_price_buy_floors_sell_ceils(self):
        rules = rules_from_market("BR/USDT:USDT", _market())
        self.assertEqual(rules.quantize_price(Decimal("0.53995"), "buy"), Decimal("0.5399"))
        self.assertEqual(rules.quantize_price(Decimal("0.53995"), "sell"), Decimal("0.5400"))
        self.assertEqual(rules.quantize_price(Decimal("0.52798"), "sell"), Decimal("0.5280"))

    def test_quantize_quantity_floors_to_step(self):
        rules = rules_from_market("BR/USDT:USDT", _market())
        self.assertEqual(rules.quantize_qty(Decimal("11.4")), Decimal("11"))
        self.assertEqual(rules.quantize_qty(Decimal("0.4")), Decimal("0"))

    def test_is_on_price_tick(self):
        rules = rules_from_market("BR/USDT:USDT", _market())
        self.assertTrue(rules.is_on_price_tick(Decimal("0.5200")))
        self.assertFalse(rules.is_on_price_tick(Decimal("0.52798")))


class ResolveCCXTExchangeTests(unittest.TestCase):
    def test_resolves_adapter_rest_exchange_wrapper(self):
        """BinanceAdapter 的 ccxt 在 adapter.rest.exchange（事故根因之一）。"""
        inner = FakeExchange()

        class Rest:
            exchange = inner

        class Adapter:
            rest = Rest()

        self.assertIs(resolve_ccxt_exchange(Adapter()), inner)
        self.assertIs(resolve_ccxt_exchange(inner), inner)
        self.assertIsNone(resolve_ccxt_exchange(object()))


class MarketRulesCacheTests(unittest.TestCase):
    def test_cache_hits_without_reload_and_force_refresh_reloads(self):
        exchange = FakeExchange()
        cache = MarketRulesCache(ttl_seconds=300)
        first = cache.get(exchange, "BR/USDT:USDT")
        self.assertIsNotNone(first)
        calls_after_first = exchange.load_calls
        cache.get(exchange, "BR/USDT:USDT")
        self.assertEqual(exchange.load_calls, calls_after_first, "TTL 內不應重複 load_markets")
        cache.get(exchange, "BR/USDT:USDT", force_refresh=True)
        self.assertGreater(exchange.load_calls, calls_after_first)

    def test_tick_change_is_picked_up_by_force_refresh(self):
        exchange = FakeExchange(tick="0.00001000")
        cache = MarketRulesCache(ttl_seconds=300)
        old = cache.get(exchange, "BR/USDT:USDT")
        self.assertEqual(old.price_decimals, 5)

        exchange.tick = "0.0001000"  # 交易所調整 tick
        stale = cache.get(exchange, "BR/USDT:USDT")
        self.assertEqual(stale.price_decimals, 5, "TTL 內允許短暫沿用舊值")

        fresh = cache.get(exchange, "BR/USDT:USDT", force_refresh=True)
        self.assertEqual(fresh.price_decimals, 4, "強制刷新必須拿到新 tick")

    def test_invalidate_forces_reload(self):
        exchange = FakeExchange()
        cache = MarketRulesCache(ttl_seconds=300)
        cache.get(exchange, "BR/USDT:USDT")
        calls = exchange.load_calls
        cache.invalidate(exchange, "BR/USDT:USDT")
        cache.get(exchange, "BR/USDT:USDT")
        self.assertGreater(exchange.load_calls, calls)


class NormalizeGridConfigTests(unittest.TestCase):
    def _config(self) -> GridConfig:
        return GridConfig(
            exchange="binance",
            symbol="BR/USDT:USDT",
            grid_type=GridType.NEUTRAL,
            order_amount=Decimal("11.4"),
            grid_interval=Decimal("0.00399"),
            lower_price=Decimal("0.52"),
            upper_price=Decimal("1.1"),
            quantity_precision=1,
            price_decimals=5,
        )

    def test_snaps_config_to_live_tick_and_step(self):
        rules = rules_from_market("BR/USDT:USDT", _market())
        config = self._config()

        warnings = normalize_grid_config(config, rules)

        self.assertTrue(warnings, "任何改寫都必須留下訊息")
        self.assertEqual(config.price_decimals, 4)
        self.assertEqual(config.quantity_precision, 0)
        self.assertEqual(config.grid_interval, Decimal("0.0039"))
        self.assertEqual(config.order_amount, Decimal("11"))

        for index in range(1, config.grid_count + 1):
            price = config.get_grid_price(index)
            self.assertTrue(
                rules.is_on_price_tick(price),
                f"grid {index} price {price} 不在 tick {rules.price_tick} 上",
            )

    def test_idempotent_when_config_already_aligned(self):
        rules = rules_from_market("BR/USDT:USDT", _market())
        config = GridConfig(
            exchange="binance",
            symbol="BR/USDT:USDT",
            grid_type=GridType.NEUTRAL,
            order_amount=Decimal("11"),
            grid_interval=Decimal("0.004"),
            lower_price=Decimal("0.52"),
            upper_price=Decimal("1.1"),
            quantity_precision=0,
            price_decimals=4,
        )
        warnings = normalize_grid_config(config, rules)
        self.assertEqual(warnings, [])
        self.assertEqual(config.grid_interval, Decimal("0.004"))


if __name__ == "__main__":
    unittest.main()
