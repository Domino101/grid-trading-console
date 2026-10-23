"""BinanceRest 下單邊界：tick/step 量化與 -4014 自癒（BRUSDT 事故回歸）。

守門目標：
1. 送出的價格必須是 live tick 的整數倍（不是 config 的 price_decimals）。
2. 交易所調整 tick 後第一次被 -4014 拒絕，必須刷新過濾器並以新 tick 重送成功。
3. 連續拒單計數可被 supervisor 查詢（觀測層）。
"""

from __future__ import annotations

import asyncio
import functools
import unittest
from decimal import Decimal
from types import SimpleNamespace

from core.adapters.exchanges.adapters.binance_rest import BinanceRest
from core.adapters.exchanges.interface import ExchangeConfig
from core.adapters.exchanges.market_rules import market_rules_cache
from core.adapters.exchanges.models import ExchangeType, OrderSide, OrderType


class FakeCCXT:
    """最小 ccxt 替身：以 filters 為唯一真相，off-tick 一律回 -4014。"""

    id = "binance"

    def __init__(self, reported_tick: str = "0.00001000", enforce_tick: str = "0.0001000"):
        self.reported_tick = reported_tick
        self.enforce_tick = enforce_tick
        self.load_calls = 0
        self.sent = []
        self.markets = {}
        self._rebuild()

    def _rebuild(self):
        self.markets = {
            "BR/USDT:USDT": {
                "id": "BRUSDT",
                "symbol": "BR/USDT:USDT",
                "precision": {"price": float(self.reported_tick), "amount": 1.0},
                "limits": {"cost": {"min": 5.0}},
                "info": {
                    "filters": [
                        {"filterType": "PRICE_FILTER", "tickSize": self.reported_tick},
                        {"filterType": "LOT_SIZE", "stepSize": "1"},
                        {"filterType": "MIN_NOTIONAL", "notional": "5"},
                    ]
                },
            }
        }

    def load_markets(self, reload=False):  # noqa: ARG002
        self.load_calls += 1
        # 模擬「交易所已改 tick，但進程快取還是舊值」：第二次載入才看到新 tick
        if self.load_calls >= 2 and self.reported_tick != self.enforce_tick:
            self.reported_tick = self.enforce_tick
            self._rebuild()
        return self.markets

    def market(self, symbol):
        return self.markets.get(symbol)

    def _validate(self, price):
        if price is None:
            return
        steps = Decimal(str(price)) / Decimal(self.enforce_tick)
        if steps != steps.to_integral_value():
            raise Exception('binance {"code":-4014,"msg":"Price not increased by tick size."}')

    def create_order(self, symbol, order_type, side, amount, price, params):
        self._validate(price)
        self.sent.append((side, Decimal(str(price)), Decimal(str(amount))))
        return {"id": str(len(self.sent)), "symbol": symbol, "price": price, "amount": amount}

    def create_orders(self, orders):
        results = []
        for order in orders:
            try:
                self._validate(order["price"])
            except Exception as exc:
                results.append({"code": -4014, "msg": str(exc)})
                continue
            self.sent.append((order["side"], Decimal(str(order["price"])), Decimal(str(order["amount"]))))
            results.append({"id": str(len(self.sent)), "symbol": order["symbol"], "price": order["price"], "amount": order["amount"]})
        return results


def _config() -> ExchangeConfig:
    return ExchangeConfig(
        exchange_id="binance",
        name="Binance",
        exchange_type=ExchangeType.FUTURES,
        api_key="test",
        api_secret="test",
    )


class BinanceOrderQuantizationTests(unittest.TestCase):
    def setUp(self):
        BinanceRest._adapter_circuit_initialized = True
        BinanceRest.reset_rejection_stats()
        market_rules_cache.clear()
        self.rest = BinanceRest(_config(), logger=None)
        self.rest.max_retries = 1
        self.rest.map_symbol_to_binance = lambda symbol: "BRUSDT"
        self.rest.parse_order = lambda data, symbol: SimpleNamespace(
            id=str(data.get("id")) if isinstance(data, dict) else None, symbol=symbol
        )

        async def _direct(func, *args, **kwargs):
            call = functools.partial(func, *args, **kwargs) if kwargs else functools.partial(func, *args)
            return await asyncio.get_event_loop().run_in_executor(None, call)

        self.rest._execute_with_retry = _direct

    def test_off_tick_price_is_quantized_before_sending(self):
        fake = FakeCCXT(reported_tick="0.0001000")
        self.rest.exchange = fake

        asyncio.run(self.rest.create_order(
            "BR/USDT:USDT", OrderSide.BUY, OrderType.LIMIT,
            Decimal("11.4"), Decimal("0.53995"), {"postOnly": True},
        ))

        self.assertEqual(len(fake.sent), 1)
        side, price, amount = fake.sent[0]
        self.assertEqual(price, Decimal("0.5399"), "買單必須向下對齊 tick")
        self.assertEqual(amount, Decimal("11"), "數量必須向下對齊 step")

    def test_stale_tick_is_refreshed_and_retried_after_4014(self):
        fake = FakeCCXT(reported_tick="0.00001000", enforce_tick="0.0001000")
        self.rest.exchange = fake

        asyncio.run(self.rest.create_order(
            "BR/USDT:USDT", OrderSide.SELL, OrderType.LIMIT,
            Decimal("11.4"), Decimal("0.52798"), {"postOnly": True},
        ))

        self.assertGreaterEqual(fake.load_calls, 2, "被 -4014 拒絕後必須 reload_markets")
        self.assertEqual(fake.sent[-1][1], Decimal("0.528"), "必須以新 tick 重新量化")
        stats = BinanceRest.get_rejection_stats("BR/USDT:USDT")
        self.assertGreaterEqual(stats["total"], 1)

    def test_batch_orders_resend_with_refreshed_rules(self):
        fake = FakeCCXT(reported_tick="0.00001000", enforce_tick="0.0001000")
        self.rest.exchange = fake

        results = asyncio.run(self.rest.create_batch_orders("BR/USDT:USDT", [
            {"side": OrderSide.SELL, "order_type": OrderType.LIMIT,
             "amount": Decimal("11.4"), "price": Decimal("0.52798"), "params": {}},
            {"side": OrderSide.BUY, "order_type": OrderType.LIMIT,
             "amount": Decimal("11.4"), "price": Decimal("0.52000"), "params": {}},
        ]))

        self.assertEqual(len(results), 2)
        self.assertTrue(all(r is not None for r in results))
        self.assertIn(Decimal("0.528"), [price for _, price, _ in fake.sent])

    def test_success_resets_consecutive_rejection_counter(self):
        fake = FakeCCXT(reported_tick="0.0001000")
        self.rest.exchange = fake
        BinanceRest._record_rejection("BR/USDT:USDT", "-4014", "x")
        BinanceRest._record_rejection("BR/USDT:USDT", "-4014", "x")
        self.assertEqual(BinanceRest.get_rejection_stats("BR/USDT:USDT")["consecutive"], 2)

        asyncio.run(self.rest.create_order(
            "BR/USDT:USDT", OrderSide.BUY, OrderType.LIMIT,
            Decimal("11"), Decimal("0.52"), {},
        ))
        self.assertEqual(BinanceRest.get_rejection_stats("BR/USDT:USDT")["consecutive"], 0)


if __name__ == "__main__":
    unittest.main()
