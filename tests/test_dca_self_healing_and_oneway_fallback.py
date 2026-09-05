"""
單元測試：DCA 持倉歸零自癒與單向持倉反向 TP 降級限價單
"""

import asyncio
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.services.dca.coordinator.dca_coordinator import DcaCoordinator
from core.services.dca.coordinator.take_profit_manager import (
    TakeProfitManager,
    TpPositionClosedExternallyError,
)
from core.services.dca.execution.dca_order_executor import DcaOrderExecutor
from core.services.dca.interfaces.dca_executor import (
    IDcaOrderExecutor,
    OrderOutcome,
    OutcomeKind,
)
from core.services.dca.models.dca_config import DcaConfig, DcaDirection
from core.services.dca.models.dca_order import DcaOrder, DcaOrderRole
from core.services.dca.models.dca_state import DcaState, DcaStatus


class TestDcaSelfHealingAndFallback(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.config = DcaConfig(
            exchange="binance",
            symbol="ETH/USDC:USDC",
            direction=DcaDirection.LONG,
            base_order_amount=Decimal("40.0"),
            max_safety_orders=5,
            price_deviation_percent=Decimal("1.0"),
            take_profit_percent=Decimal("1.0"),
            active_safety_orders_window=2,
            cooldown_seconds=300,
        )

    async def test_zero_position_self_healing_in_degraded(self):
        """測試在 degraded 狀態下，若交易所持倉為 0，能自主收輪轉入 COMPLETED"""
        mock_executor = AsyncMock(spec=IDcaOrderExecutor)
        mock_executor.fetch_open_orders.return_value = []
        mock_executor.fetch_position.return_value = {
            "size": Decimal("0"),
            "side": "none",
            "raw_amount": Decimal("0"),
            "entry_price": Decimal("0"),
        }

        coord = DcaCoordinator(self.config, "test-bot-01", mock_executor)
        coord.state.status = DcaStatus.DEGRADED
        coord.state.position_quantity = Decimal("1000.0")
        coord.state.total_cost = Decimal("100.0")

        # 模擬殘留掛單
        order = DcaOrder(
            client_order_id="test_so01", symbol="ETH/USDC:USDC", side="buy",
            role=DcaOrderRole.SAFETY, price=Decimal("2400"), quantity=Decimal("1"),
        )
        coord._active["test_so01"] = order

        await coord._step_degraded()

        # 驗證狀態已切至 COMPLETED
        self.assertEqual(coord.state.status, DcaStatus.COMPLETED)
        # 驗證撤單已被調用
        mock_executor.cancel.assert_awaited_once_with(order)

    async def test_zero_position_self_healing_in_reconcile(self):
        """測試在 _reconcile 對帳中，若交易所持倉為 0，能自動標記 _flag_round_finished"""
        mock_executor = AsyncMock(spec=IDcaOrderExecutor)
        mock_executor.fetch_open_orders.return_value = []
        mock_executor.fetch_position.return_value = {
            "size": Decimal("0"),
            "side": "none",
            "raw_amount": Decimal("0"),
            "entry_price": Decimal("0"),
        }

        coord = DcaCoordinator(self.config, "test-bot-02", mock_executor)
        coord.state.status = DcaStatus.AVERAGING
        coord.state.position_quantity = Decimal("500.0")

        await coord._reconcile()

        self.assertTrue(coord._flag_round_finished)

    async def test_oneway_opposing_tp_fallback_to_normal_limit(self):
        """測試多單在單向空頭持倉衝突下，ReduceOnly 被拒能自動降級為普通限價單成功送出"""
        mock_exchange = AsyncMock()

        # 第一次 create_order (帶 reduceOnly) 拋出 -2022
        # 第二次 create_order (不帶 reduceOnly) 成功
        def side_effect_create(*args, **kwargs):
            params = kwargs.get("params", {})
            if params.get("reduceOnly") or params.get("reduce_only"):
                raise RuntimeError('binance {"code":-2022,"msg":"ReduceOnly Order is rejected."}')
            return SimpleNamespace(id="order-12345", status="open")

        mock_exchange.create_order.side_effect = side_effect_create
        # 模擬交易所實盤為反向持倉（空頭 -0.180）
        pos_data = SimpleNamespace(
            symbol="ETH/USDC:USDC",
            amount=Decimal("-0.180"),
            size=Decimal("0.180"),
            side=SimpleNamespace(value="short"),
            entry_price=Decimal("2500"),
        )
        mock_exchange.get_positions.return_value = [pos_data]

        executor = DcaOrderExecutor(self.config, "test-bot-03", mock_exchange)

        outcome = await executor.place_limit(
            role=DcaOrderRole.TAKE_PROFIT,
            side="sell",
            price=Decimal("2520"),
            quantity=Decimal("0.016"),
        )

        self.assertEqual(outcome.kind, OutcomeKind.PLACED)
        self.assertEqual(outcome.order.order_id, "order-12345")
        # 驗證 create_order 被呼叫了兩次（第一次被拒，第二次降級成功）
        self.assertEqual(mock_exchange.create_order.await_count, 2)
        # 驗證第二次呼叫的 params 中沒有 reduceOnly
        second_call_params = mock_exchange.create_order.await_args_list[1].kwargs["params"]
        self.assertNotIn("reduce_only", second_call_params)
        self.assertNotIn("reduceOnly", second_call_params)

    async def test_tp_position_closed_externally_when_exchange_pos_zero(self):
        """測試在持倉為 0 時，ReduceOnly 被拒會回傳 POSITION_CLOSED_EXTERNALLY 而不重試"""
        mock_exchange = AsyncMock()
        mock_exchange.create_order.side_effect = RuntimeError('binance {"code":-2022,"msg":"ReduceOnly Order is rejected."}')
        # 模擬交易所實盤持倉已為 0
        mock_exchange.get_positions.return_value = []

        executor = DcaOrderExecutor(self.config, "test-bot-04", mock_exchange)

        outcome = await executor.place_limit(
            role=DcaOrderRole.TAKE_PROFIT,
            side="buy",
            price=Decimal("0.092"),
            quantity=Decimal("1253.0"),
        )

        self.assertEqual(outcome.kind, OutcomeKind.POSITION_CLOSED_EXTERNALLY)
        # 驗證沒有進行第二次降級呼叫（因為持倉為 0 不能亂開新倉）
        self.assertEqual(mock_exchange.create_order.await_count, 1)


if __name__ == "__main__":
    unittest.main()
