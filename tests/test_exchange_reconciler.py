"""Unit tests for ExchangeReconciler dual-stage self-healing and TP enforcement."""

import asyncio
from decimal import Decimal
import unittest
from unittest.mock import AsyncMock, MagicMock

from core.services.dca.coordinator import DcaCoordinator
from core.services.dca.models import DcaConfig, DcaDirection, DcaOrder, DcaOrderRole, DcaOrderStatus, DcaStatus
from core.services.reconciler import ExchangeReconciler


class DummyPosition:
    def __init__(self, symbol="PNUT/USDC:USDC", amount=100.0, entry_price=0.05):
        self.symbol = symbol
        self.amount = amount
        self.entry_price = entry_price


class TestExchangeReconciler(unittest.TestCase):
    def setUp(self):
        self.config = DcaConfig(
            exchange="binance",
            symbol="PNUT/USDC:USDC",
            base_order_amount=Decimal("10"),
            direction=DcaDirection.LONG,
            max_safety_orders=5,
            take_profit_percent=Decimal("1.5"),
        )
        self.bot_id = "test-pnut-reconciler"

    def test_dca_reconciler_rehydrates_position_and_deploys_tp(self):
        executor = MagicMock()
        executor._bot_id = self.bot_id
        executor._symbol = "PNUT/USDC:USDC"
        executor.fetch_open_orders = AsyncMock(return_value=[])
        executor.last_price = AsyncMock(return_value=Decimal("0.051"))
        
        # Mock exchange position = 100.0 @ 0.05
        mock_exchange = MagicMock()
        mock_exchange.get_positions = AsyncMock(return_value=[DummyPosition("PNUT/USDC:USDC", 100.0, 0.05)])
        executor._exchange = mock_exchange

        # Mock place_limit for TP placement
        from core.services.dca.interfaces.dca_executor import OrderOutcome, OutcomeKind
        tp_order = DcaOrder(
            client_order_id=f"dca_12345678_long_tp",
            symbol="PNUT/USDC:USDC",
            side="sell",
            role=DcaOrderRole.TAKE_PROFIT,
            price=Decimal("0.05075"),
            quantity=Decimal("100"),
            status=DcaOrderStatus.PLACED,
        )
        executor.place_limit = AsyncMock(return_value=OrderOutcome(OutcomeKind.PLACED, order=tp_order))

        coordinator = DcaCoordinator(self.config, self.bot_id, executor)
        self.assertEqual(coordinator.state.position_quantity, Decimal("0"))
        self.assertEqual(coordinator.state.status, DcaStatus.DRAFT)

        # Run Reconciler
        res = asyncio.run(ExchangeReconciler.reconcile_dca(coordinator))

        # Assertions
        self.assertEqual(res["status"], "success")
        self.assertTrue(res["rehydrated"])
        self.assertTrue(res["tp_deployed"])

        # Coordinator state must be recovered
        self.assertEqual(coordinator.state.position_quantity, Decimal("100"))
        self.assertEqual(coordinator.state.average_cost, Decimal("0.05"))
        self.assertEqual(coordinator.state.status, DcaStatus.AVERAGING)
        self.assertIsNotNone(coordinator._tp_client_id)
        self.assertIn(coordinator._tp_client_id, coordinator._active)


if __name__ == "__main__":
    unittest.main()
