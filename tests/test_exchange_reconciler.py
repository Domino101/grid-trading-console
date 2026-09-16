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

        # Run Reconciler with has_snapshot=True (restoring from snapshot)
        res = asyncio.run(ExchangeReconciler.reconcile_dca(coordinator, has_snapshot=True))

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

    def test_dca_reconciler_uninitialized_bot_ignores_external_position(self):
        """New bot with no snapshot, no orders, and zero local position must NOT hijack external exchange position."""
        executor = MagicMock()
        executor._bot_id = self.bot_id
        executor._symbol = "PNUT/USDC:USDC"
        executor.fetch_open_orders = AsyncMock(return_value=[])
        executor.last_price = AsyncMock(return_value=Decimal("0.051"))
        
        mock_exchange = MagicMock()
        mock_exchange.get_positions = AsyncMock(return_value=[DummyPosition("PNUT/USDC:USDC", 100.0, 0.05)])
        executor._exchange = mock_exchange

        coordinator = DcaCoordinator(self.config, self.bot_id, executor)
        self.assertEqual(coordinator.state.position_quantity, Decimal("0"))
        self.assertEqual(coordinator.state.status, DcaStatus.DRAFT)

        res = asyncio.run(ExchangeReconciler.reconcile_dca(coordinator, has_snapshot=False))

        self.assertEqual(res["status"], "success")
        self.assertFalse(res["rehydrated"])
        self.assertFalse(res["tp_deployed"])
        self.assertEqual(coordinator.state.position_quantity, Decimal("0"))
        self.assertEqual(coordinator.state.status, DcaStatus.DRAFT)

    def test_dca_reconciler_short_direction_preserves_short_and_deploys_correct_tp(self):
        """Short bot with Enum DcaDirection.SHORT must maintain 'short' direction, not be forced to 'long'."""
        short_config = DcaConfig(
            exchange="binance",
            symbol="PNUT/USDC:USDC",
            base_order_amount=Decimal("10"),
            direction=DcaDirection.SHORT,
            max_safety_orders=5,
            take_profit_percent=Decimal("1.5"),
        )
        executor = MagicMock()
        executor._bot_id = "test-short-reconciler"
        executor._symbol = "PNUT/USDC:USDC"
        executor.fetch_open_orders = AsyncMock(return_value=[])
        executor.last_price = AsyncMock(return_value=Decimal("0.05"))

        # Position is short
        dummy_pos = DummyPosition("PNUT/USDC:USDC", 100.0, 0.05)
        dummy_pos.side = "short"
        mock_exchange = MagicMock()
        mock_exchange.get_positions = AsyncMock(return_value=[dummy_pos])
        executor._exchange = mock_exchange

        from core.services.dca.interfaces.dca_executor import OrderOutcome, OutcomeKind
        tp_order = DcaOrder(
            client_order_id=f"dca_12345678_short_tp",
            symbol="PNUT/USDC:USDC",
            side="buy",
            role=DcaOrderRole.TAKE_PROFIT,
            price=Decimal("0.04925"),
            quantity=Decimal("100"),
            status=DcaOrderStatus.PLACED,
        )
        executor.place_limit = AsyncMock(return_value=OrderOutcome(OutcomeKind.PLACED, order=tp_order))

        coordinator = DcaCoordinator(short_config, "test-short-reconciler", executor)
        res = asyncio.run(ExchangeReconciler.reconcile_dca(coordinator, has_snapshot=True))

        self.assertEqual(res["status"], "success")
        self.assertTrue(res["rehydrated"])
        self.assertEqual(coordinator.state.active_direction, "short")
        self.assertEqual(coordinator.state.status, DcaStatus.AVERAGING)

    def test_dca_reconciler_rejects_opposite_direction_position(self):
        """Short bot must refuse to rehydrate a LONG position on exchange."""
        short_config = DcaConfig(
            exchange="binance",
            symbol="PNUT/USDC:USDC",
            base_order_amount=Decimal("10"),
            direction=DcaDirection.SHORT,
            max_safety_orders=5,
            take_profit_percent=Decimal("1.5"),
        )
        executor = MagicMock()
        executor._bot_id = "test-short-reconciler"
        executor._symbol = "PNUT/USDC:USDC"
        executor.fetch_open_orders = AsyncMock(return_value=[])
        executor.last_price = AsyncMock(return_value=Decimal("0.05"))

        # Position is long
        dummy_pos = DummyPosition("PNUT/USDC:USDC", 100.0, 0.05)
        dummy_pos.side = "long"
        mock_exchange = MagicMock()
        mock_exchange.get_positions = AsyncMock(return_value=[dummy_pos])
        executor._exchange = mock_exchange

        coordinator = DcaCoordinator(short_config, "test-short-reconciler", executor)
        res = asyncio.run(ExchangeReconciler.reconcile_dca(coordinator, has_snapshot=True))

        self.assertEqual(res["status"], "success")
        self.assertFalse(res["rehydrated"])
        self.assertEqual(coordinator.state.position_quantity, Decimal("0"))

    def test_dca_reconciler_recognizes_existing_tp_without_redeploying(self):
        from core.services.dca.models.dca_order import bot_id_hash8
        executor = MagicMock()
        executor._bot_id = self.bot_id
        executor._symbol = "PNUT/USDC:USDC"
        
        my_hash = bot_id_hash8(self.bot_id)
        existing_tp = DcaOrder(
            client_order_id=f"dca_{my_hash}_long_tp",
            symbol="PNUT/USDC:USDC",
            side="sell",
            role=DcaOrderRole.TAKE_PROFIT,
            price=Decimal("0.05075"),
            quantity=Decimal("100"),
            status=DcaOrderStatus.PLACED,
        )
        executor.fetch_open_orders = AsyncMock(return_value=[existing_tp])
        executor.last_price = AsyncMock(return_value=Decimal("0.051"))
        executor.place_limit = AsyncMock()

        # Mock exchange position = 100.0 @ 0.05
        mock_exchange = MagicMock()
        mock_exchange.get_positions = AsyncMock(return_value=[DummyPosition("PNUT/USDC:USDC", 100.0, 0.05)])
        executor._exchange = mock_exchange

        coordinator = DcaCoordinator(self.config, self.bot_id, executor)
        coordinator.state.position_quantity = Decimal("100")
        coordinator.state.total_cost = Decimal("5.0")
        coordinator.state.status = DcaStatus.DEGRADED

        # Run Reconciler
        res = asyncio.run(ExchangeReconciler.reconcile_dca(coordinator))

        # Assertions: Should NOT deploy duplicate TP, should recognize existing TP and restore to AVERAGING
        self.assertEqual(res["status"], "success")
        self.assertFalse(res["tp_deployed"])
        executor.place_limit.assert_not_called()

        self.assertEqual(coordinator.state.status, DcaStatus.AVERAGING)
        self.assertEqual(coordinator._tp_client_id, existing_tp.client_order_id)
        self.assertIsNotNone(coordinator._tp_manager)
        self.assertEqual(coordinator._tp_manager.current_tp, existing_tp)


if __name__ == "__main__":
    unittest.main()
