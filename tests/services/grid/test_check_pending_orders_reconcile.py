import asyncio
from datetime import datetime
from decimal import Decimal
import unittest
from unittest.mock import AsyncMock, MagicMock

from core.adapters.exchanges.models import OrderData, OrderSide, OrderStatus, OrderType
from core.services.grid.implementations.grid_engine_impl import GridEngineImpl
from core.services.grid.models import GridConfig, GridOrder, GridOrderSide, GridOrderStatus, GridType


class TestCheckPendingOrdersReconcile(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = GridConfig(
            symbol="ETH/USDC:USDC",
            exchange="binance",
            grid_type=GridType.NEUTRAL,
            lower_price=Decimal("2000"),
            upper_price=Decimal("3000"),
            order_amount=Decimal("0.02"),
            grid_interval=Decimal("20"),
        )
        self.mock_adapter = MagicMock()
        self.mock_adapter.config.exchange_id = "binance"
        self.mock_adapter.config.exchange_type = MagicMock()
        self.mock_adapter.get_open_orders = AsyncMock(return_value=[])
        self.mock_adapter.get_order = AsyncMock()
        self.mock_adapter.cancel_order = AsyncMock()
        self.mock_adapter.create_order = AsyncMock(return_value=MagicMock(id="new_mock_ord", client_id="cli_mock"))
        self.mock_adapter.get_account_balance = AsyncMock(return_value=MagicMock(free=Decimal("10000")))

        try:
            from core.adapters.exchanges.adapters.binance_rest import BinanceRest
            BinanceRest.clear_adapter_circuit()
        except Exception:
            pass

        GridEngineImpl._shared_api_circuit_until = 0.0
        GridEngineImpl._shared_last_api_error_code = None

        self.engine = GridEngineImpl(self.mock_adapter)
        self.engine.config = self.config

    async def test_expected_cancellations_are_not_treated_as_filled(self):
        """測試預期撤單（如窗口調整撤單）在消失於 open_orders 時，絕不當成成交"""
        order = GridOrder(
            order_id="ord_cancelled_1",
            client_id="cli_1",
            grid_id=1,
            side=GridOrderSide.BUY,
            price=Decimal("2400"),
            amount=Decimal("0.02"),
            status=GridOrderStatus.PENDING,
            created_at=datetime.now(),
        )
        self.engine._pending_orders["ord_cancelled_1"] = order
        self.engine._pending_orders_by_client_id["cli_1"] = order
        self.engine._expected_cancellations.add("ord_cancelled_1")

        callback_mock = AsyncMock()
        self.engine.subscribe_order_updates(callback_mock)

        # 交易所 open_orders 為空（訂單已被撤銷）
        self.mock_adapter.get_open_orders.return_value = []

        filled_count = await self.engine._check_pending_orders()

        self.assertEqual(filled_count, 0)
        self.assertNotIn("ord_cancelled_1", self.engine._pending_orders)
        self.assertNotIn("cli_1", self.engine._pending_orders_by_client_id)
        self.assertEqual(order.status, GridOrderStatus.CANCELLED)
        callback_mock.assert_not_called()
        self.mock_adapter.get_order.assert_not_called()

    async def test_missing_order_confirmed_canceled_via_get_order(self):
        """測試非預期撤單（如外部撤單）反查為 CANCELED 時，正確移除且不計為成交"""
        order = GridOrder(
            order_id="ord_external_cancel",
            client_id="cli_2",
            grid_id=2,
            side=GridOrderSide.SELL,
            price=Decimal("2600"),
            amount=Decimal("0.02"),
            status=GridOrderStatus.PENDING,
            created_at=datetime.now(),
        )
        self.engine._pending_orders["ord_external_cancel"] = order

        # 交易所 open_orders 為空，get_order 回傳已撤單狀態
        self.mock_adapter.get_open_orders.return_value = []
        self.mock_adapter.get_order.return_value = OrderData(
            id="ord_external_cancel",
            client_id="cli_2",
            symbol="ETH/USDC:USDC",
            side=OrderSide.SELL,
            type=OrderType.LIMIT,
            amount=Decimal("0.02"),
            price=Decimal("2600"),
            filled=Decimal("0"),
            remaining=Decimal("0.02"),
            cost=Decimal("0"),
            average=None,
            status=OrderStatus.CANCELED,
            timestamp=datetime.now(),
            updated=datetime.now(),
            fee=None,
            trades=[],
            params={},
            raw_data={},
        )

        callback_mock = AsyncMock()
        self.engine.subscribe_order_updates(callback_mock)

        filled_count = await self.engine._check_pending_orders()

        self.assertEqual(filled_count, 0)
        self.assertNotIn("ord_external_cancel", self.engine._pending_orders)
        self.assertEqual(order.status, GridOrderStatus.CANCELLED)
        callback_mock.assert_not_called()
        self.mock_adapter.get_order.assert_awaited_once_with("ord_external_cancel", "ETH/USDC:USDC")

    async def test_missing_order_confirmed_filled_via_get_order(self):
        """測試不在 open_orders 的訂單經反查確認為 FILLED 時，正確執行成交回調"""
        order = GridOrder(
            order_id="ord_real_fill",
            client_id="cli_3",
            grid_id=3,
            side=GridOrderSide.BUY,
            price=Decimal("2420"),
            amount=Decimal("0.02"),
            status=GridOrderStatus.PENDING,
            created_at=datetime.now(),
        )
        self.engine._pending_orders["ord_real_fill"] = order

        # get_order 回傳 FILLED
        self.mock_adapter.get_open_orders.return_value = []
        self.mock_adapter.get_order.return_value = OrderData(
            id="ord_real_fill",
            client_id="cli_3",
            symbol="ETH/USDC:USDC",
            side=OrderSide.BUY,
            type=OrderType.LIMIT,
            amount=Decimal("0.02"),
            price=Decimal("2420"),
            filled=Decimal("0.02"),
            remaining=Decimal("0"),
            cost=Decimal("48.4"),
            average=Decimal("2420"),
            status=OrderStatus.FILLED,
            timestamp=datetime.now(),
            updated=datetime.now(),
            fee=None,
            trades=[],
            params={},
            raw_data={},
        )

        callback_mock = AsyncMock()
        self.engine.subscribe_order_updates(callback_mock)

        filled_count = await self.engine._check_pending_orders()

        self.assertEqual(filled_count, 1)
        self.assertNotIn("ord_real_fill", self.engine._pending_orders)
        self.assertEqual(order.status, GridOrderStatus.FILLED)
        callback_mock.assert_awaited_once_with(order)

    async def test_get_order_transient_failure_keeps_pending(self):
        """測試反查暫態失敗時，保留在 pending 緩存待下輪重試，不武斷認定成交"""
        order = GridOrder(
            order_id="ord_retry",
            client_id="cli_4",
            grid_id=4,
            side=GridOrderSide.SELL,
            price=Decimal("2500"),
            amount=Decimal("0.02"),
            status=GridOrderStatus.PENDING,
            created_at=datetime.now(),
        )
        self.engine._pending_orders["ord_retry"] = order

        self.mock_adapter.get_open_orders.return_value = []
        self.mock_adapter.get_order.side_effect = RuntimeError("Network timeout")

        callback_mock = AsyncMock()
        self.engine.subscribe_order_updates(callback_mock)

        filled_count = await self.engine._check_pending_orders()

        self.assertEqual(filled_count, 0)
        self.assertIn("ord_retry", self.engine._pending_orders)
        callback_mock.assert_not_called()

    async def test_refresh_active_window_cancels_and_removes_from_pending(self):
        """測試活躍窗口刷新時，被撤單子確實自 _pending_orders 移除"""
        order_to_cancel = GridOrder(
            order_id="ord_out_of_window",
            client_id="cli_out",
            grid_id=1,  # 遠離現價（現價設為 2500，Grid 1 約 2000）
            side=GridOrderSide.BUY,
            price=Decimal("2000"),
            amount=Decimal("0.02"),
            status=GridOrderStatus.PENDING,
            created_at=datetime.now(),
        )
        self.engine._pending_orders["ord_out_of_window"] = order_to_cancel
        self.engine._pending_orders_by_client_id["cli_out"] = order_to_cancel

        # 模擬交易所返回目前的掛單包含這張訂單
        ex_order = OrderData(
            id="ord_out_of_window",
            client_id="cli_out",
            symbol="ETH/USDC:USDC",
            side=OrderSide.BUY,
            type=OrderType.LIMIT,
            amount=Decimal("0.02"),
            price=Decimal("2000"),
            filled=Decimal("0"),
            remaining=Decimal("0.02"),
            cost=Decimal("0"),
            average=None,
            status=OrderStatus.OPEN,
            timestamp=datetime.now(),
            updated=datetime.now(),
            fee=None,
            trades=[],
            params={},
            raw_data={},
        )
        self.mock_adapter.get_open_orders.return_value = [ex_order]
        self.mock_adapter.cancel_order.return_value = ex_order
        self.mock_adapter.get_ticker.return_value = MagicMock(last=Decimal("2500"), close=Decimal("2500"))

        # 現價 2500，center grid 約在中間 (2500 附近)，Grid 1 (2000) 必定超出窗口
        await self.engine._refresh_active_window_once(window_each_side=2)

        # 驗證該訂單已調用 cancel_order 並從本地 pending 徹底移除
        self.mock_adapter.cancel_order.assert_awaited_with("ord_out_of_window", "ETH/USDC:USDC")
        self.assertNotIn("ord_out_of_window", self.engine._pending_orders)
        self.assertNotIn("cli_out", self.engine._pending_orders_by_client_id)


class TestPaperExchangeAdapter(unittest.IsolatedAsyncioTestCase):
    async def test_paper_cancel_order_emits_event(self):
        """測試 PaperExchangeAdapter 在撤單時透過 user_callbacks 發送取消通知"""
        from core.adapters.exchanges.adapters.paper import PaperExchangeAdapter
        from core.adapters.exchanges.interface import ExchangeConfig, ExchangeType

        config = ExchangeConfig(
            exchange_id="binance",
            name="Binance",
            exchange_type=ExchangeType.PERPETUAL,
            api_key="mock_key",
            api_secret="mock_secret",
        )
        adapter = PaperExchangeAdapter(config, starting_equity=Decimal("10000"))
        adapter.set_last_price("ETH/USDT", Decimal("2500"))

        # 掛一張單
        order = await adapter.create_order(
            symbol="ETH/USDT",
            side="buy",
            order_type="limit",
            amount=Decimal("0.02"),
            price=Decimal("2400"),  # 低於現價不成交
        )
        self.assertEqual(order.status, OrderStatus.OPEN)

        received_updates = []
        await adapter.subscribe_user_data(lambda o: received_updates.append(o))

        # 撤單
        cancelled = await adapter.cancel_order(order.id, "ETH/USDT")
        self.assertEqual(cancelled.status, OrderStatus.CANCELED)
        self.assertEqual(len(received_updates), 1)
        self.assertEqual(received_updates[0].id, order.id)
        self.assertEqual(received_updates[0].status, OrderStatus.CANCELED)


if __name__ == "__main__":
    unittest.main()

