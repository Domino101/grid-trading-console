"""
DcaOrderExecutor 單元測試（Phase 2 稽核 2.14 / 2.15 的執行器層）

涵蓋：精度 quantize、最小名目拒絕、熔斷延後、client order id 命名空間、
撤單語意、命名空間過濾的 open orders、最新價解析、市價單行為。
全部使用 FakeExchange，不接真實交易所。
"""

import unittest
from decimal import Decimal as D

from core.services.dca.execution import DcaOrderExecutor
from core.services.dca.interfaces import OutcomeKind
from core.services.dca.models.dca_config import DcaConfig
from core.services.dca.models.dca_order import DcaOrderRole, DcaOrderStatus, bot_id_hash8
from tests.services.dca.fake_exchange import FakeExchange

BOT_ID = "bot-executor-test"
SYMBOL = "ETH/USDC:USDC"


def _config(**kw) -> DcaConfig:
    base = dict(
        exchange="binance", symbol=SYMBOL,
        base_order_amount=D("20"),
        quantity_precision=6, price_decimals=2,
        active_safety_orders_window=2, max_safety_orders=3,
        repeat_after_take_profit=False, monitoring_interval=5,
    )
    base.update(kw)
    return DcaConfig(**base)


def _executor(config=None, exchange=None, **kw) -> tuple:
    config = config or _config()
    exchange = exchange or FakeExchange("2500")
    ex = DcaOrderExecutor(config, BOT_ID, exchange, **kw)
    return ex, exchange


class ExecutorPlaceTests(unittest.IsolatedAsyncioTestCase):
    """下單 / 精度 / client id 命名空間（稽核 2.15 前段）。"""

    async def test_place_limit_quantizes_price_half_even_and_qty_down(self):
        config = _config(quantity_precision=3, price_decimals=2)
        ex, _ = _executor(config)
        # 價格 2480.005 → ROUND_HALF_EVEN → 2480.00；數量 ROUND_DOWN
        outcome = await ex.place_limit(
            DcaOrderRole.SAFETY, "buy", D("2480.005"), D("0.123456789"),
            slot_index=1, leg="long",
        )
        self.assertEqual(outcome.kind, OutcomeKind.PLACED)
        self.assertEqual(outcome.order.price, D("2480.00"))
        self.assertEqual(outcome.order.quantity, D("0.123"))

    async def test_client_order_id_namespace_and_slot_format(self):
        ex, _ = _executor()
        outcome = await ex.place_limit(
            DcaOrderRole.SAFETY, "buy", D("2480"), D("0.008"),
            slot_index=3, leg="long",
        )
        cid = outcome.order.client_order_id
        self.assertEqual(cid, f"dca_{bot_id_hash8(BOT_ID)}_long_so03")
        self.assertTrue(cid.startswith(f"dca_{bot_id_hash8(BOT_ID)}_"))

    async def test_base_and_tp_slot_naming(self):
        ex, _ = _executor()
        base = await ex.place_limit(DcaOrderRole.BASE, "buy", D("2500"), D("0.008"))
        self.assertTrue(base.order.client_order_id.endswith("_long_base"))
        tp = await ex.place_limit(DcaOrderRole.TAKE_PROFIT, "sell", D("2525"), D("0.008"))
        self.assertTrue(tp.order.client_order_id.endswith("_long_tp"))

    async def test_min_notional_rejects_without_exchange_call(self):
        ex, fake = _executor(min_notional=D("100"))
        outcome = await ex.place_limit(
            DcaOrderRole.SAFETY, "buy", D("2480"), D("0.008"),
            slot_index=1, leg="long",
        )
        self.assertEqual(outcome.kind, OutcomeKind.REJECTED_MIN_NOTIONAL)
        self.assertEqual(fake.create_call_count(), 0)
        self.assertEqual(fake.open_order_records(), [])

    async def test_quantized_quantity_zero_rejected(self):
        config = _config(quantity_precision=0)
        ex, fake = _executor(config)
        outcome = await ex.place_limit(
            DcaOrderRole.SAFETY, "buy", D("60000"), D("0.0004"),
            slot_index=1, leg="long",
        )
        self.assertEqual(outcome.kind, OutcomeKind.REJECTED_MIN_NOTIONAL)
        self.assertEqual(fake.create_call_count(), 0)

    async def test_place_market_fills_immediately(self):
        ex, fake = _executor()
        outcome = await ex.place_market(DcaOrderRole.STOP_LOSS, "sell", D("0.008"))
        self.assertEqual(outcome.kind, OutcomeKind.PLACED)
        self.assertEqual(outcome.order.status, DcaOrderStatus.FILLED)
        self.assertEqual(fake.orders[outcome.order.order_id].status, "filled")


class ExecutorCircuitTests(unittest.IsolatedAsyncioTestCase):
    """熔斷守衛（稽核 2.14 執行器層）：熔斷中不下單、延後不失敗。"""

    async def test_circuit_open_defers_limit_and_market(self):
        flag = [True]
        ex, fake = _executor(circuit_probe=lambda: flag[0])
        limit = await ex.place_limit(
            DcaOrderRole.BASE, "buy", D("2500"), D("0.008"), leg="long",
        )
        self.assertEqual(limit.kind, OutcomeKind.DEFERRED_CIRCUIT_OPEN)
        market = await ex.place_market(DcaOrderRole.STOP_LOSS, "sell", D("0.008"))
        self.assertEqual(market.kind, OutcomeKind.DEFERRED_CIRCUIT_OPEN)
        self.assertEqual(fake.create_call_count(), 0)

    async def test_circuit_close_then_places(self):
        flag = [True]
        ex, fake = _executor(circuit_probe=lambda: flag[0])
        first = await ex.place_limit(DcaOrderRole.BASE, "buy", D("2500"), D("0.008"))
        self.assertEqual(first.kind, OutcomeKind.DEFERRED_CIRCUIT_OPEN)
        flag[0] = False
        second = await ex.place_limit(DcaOrderRole.BASE, "buy", D("2500"), D("0.008"))
        self.assertEqual(second.kind, OutcomeKind.PLACED)
        self.assertEqual(fake.create_call_count(), 1)

    def test_default_circuit_probe_fail_open_no_raise(self):
        # 預設探針讀 BinanceRest/GridEngineImpl 共用熔斷；任何失敗都不得傳播
        result = DcaOrderExecutor._default_circuit_probe()
        self.assertIsInstance(result, bool)


class ExecutorCancelAndQueryTests(unittest.IsolatedAsyncioTestCase):
    """撤單語意與命名空間過濾查詢。"""

    async def test_cancel_marks_order_and_returns_true(self):
        ex, fake = _executor()
        outcome = await ex.place_limit(DcaOrderRole.SAFETY, "sell", D("2600"), D("0.008"), slot_index=1, leg="long")
        ok = await ex.cancel(outcome.order)
        self.assertTrue(ok)
        self.assertEqual(outcome.order.status, DcaOrderStatus.CANCELLED)
        self.assertEqual(fake.cancel_call_count(), 1)

    async def test_cancel_failure_returns_false(self):
        ex, fake = _executor()
        outcome = await ex.place_limit(DcaOrderRole.SAFETY, "sell", D("2600"), D("0.008"), slot_index=1, leg="long")
        fake.fail_next_cancel = True
        ok = await ex.cancel(outcome.order)
        self.assertFalse(ok)
        self.assertEqual(outcome.order.status, DcaOrderStatus.PLACED)

    async def test_cancel_unplaced_order_is_noop_true(self):
        ex, fake = _executor()
        flag = [True]
        ex2 = DcaOrderExecutor(_config(), BOT_ID, fake, circuit_probe=lambda: flag[0])
        deferred = await ex2.place_limit(DcaOrderRole.BASE, "buy", D("2500"), D("0.008"))
        self.assertEqual(deferred.kind, OutcomeKind.DEFERRED_CIRCUIT_OPEN)
        self.assertIsNone(deferred.order)
        del ex  # 未使用
        # 直接構造未送出去的訂單
        from core.services.dca.models.dca_order import DcaOrder
        loose = DcaOrder(client_order_id="dca_x_long_base", symbol=SYMBOL, side="buy",
                         role=DcaOrderRole.BASE, price=D("2500"), quantity=D("0.008"))
        ok = await ex2.cancel(loose)
        self.assertTrue(ok)
        self.assertEqual(fake.cancel_call_count(), 0)

    async def test_fetch_open_orders_filters_foreign_namespace(self):
        ex, fake = _executor()
        await ex.place_limit(DcaOrderRole.SAFETY, "sell", D("2600"), D("0.008"),
                             slot_index=1, leg="long")
        # 外來單（其他 bot / 手動單）不帶本 bot 命名空間
        await fake.create_order(SYMBOL, "sell", "limit", D("0.5"), D("3000"),
                                params={"client_order_id": "manual-order-001"})
        open_orders = await ex.fetch_open_orders()
        self.assertEqual(len(open_orders), 1)
        self.assertIn(f"dca_{bot_id_hash8(BOT_ID)}_", open_orders[0].client_order_id)

    async def test_fetch_order_round_trip_filled(self):
        ex, fake = _executor()
        outcome = await ex.place_limit(DcaOrderRole.BASE, "buy", D("2500"), D("0.008"))
        # 2500 買單在市價 2500 立即成交
        latest = await ex.fetch_order(outcome.order.client_order_id)
        self.assertIsNotNone(latest)
        self.assertEqual(latest.status, DcaOrderStatus.FILLED)
        self.assertEqual(latest.filled_quantity, D("0.008"))

    async def test_fetch_order_passes_orig_client_order_id_and_parses_order_data(self):
        from unittest.mock import AsyncMock
        from core.adapters.exchanges.models import OrderData, OrderSide, OrderType, OrderStatus
        from datetime import datetime

        mock_exchange = AsyncMock()
        mock_exchange.get_order.return_value = OrderData(
            id="123456789",
            client_id=None,
            symbol=SYMBOL,
            side=OrderSide.SELL,
            type=OrderType.LIMIT,
            amount=D("0.039"),
            price=D("2520.80"),
            filled=D("0.039"),
            remaining=D("0"),
            cost=D("98.3112"),
            average=D("2520.80"),
            status=OrderStatus.FILLED,
            timestamp=datetime.now(),
            updated=datetime.now(),
            fee=None,
            trades=[],
            params={"origClientOrderId": "dca_7ebd0816_short_base"},
            raw_data={},
        )
        mock_exchange.get_ticker.return_value = {"last": 2520.80}

        ex = DcaOrderExecutor(_config(), BOT_ID, mock_exchange)
        latest = await ex.fetch_order("dca_7ebd0816_short_base")

        self.assertIsNotNone(latest)
        self.assertEqual(latest.client_order_id, "dca_7ebd0816_short_base")
        self.assertEqual(latest.order_id, "123456789")
        self.assertEqual(latest.status, DcaOrderStatus.FILLED)
        self.assertEqual(latest.filled_quantity, D("0.039"))
        self.assertEqual(latest.average_fill_price, D("2520.80"))

        mock_exchange.get_order.assert_called_once_with(
            "dca_7ebd0816_short_base",
            SYMBOL,
            params={
                "origClientOrderId": "dca_7ebd0816_short_base",
                "clientOrderId": "dca_7ebd0816_short_base",
                "clOrdId": "dca_7ebd0816_short_base",
            },
        )

    async def test_last_price_prefers_last_attribute(self):
        ex, fake = _executor()
        fake.set_last_price("2499.5")
        self.assertEqual(await ex.last_price(), D("2499.5"))


if __name__ == "__main__":
    unittest.main()
