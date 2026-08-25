"""Tests for GridCoordinator order fills and statistics gathering."""

import asyncio
from datetime import datetime
from decimal import Decimal
import unittest
from unittest.mock import AsyncMock, MagicMock

from core.services.grid.coordinator.grid_coordinator import GridCoordinator
from core.services.grid.implementations.position_tracker_impl import PositionTrackerImpl
from core.services.grid.models import (
    GridConfig,
    GridOrder,
    GridOrderSide,
    GridOrderStatus,
    GridState,
    GridType,
)


class TestCoordinatorStatsAndFills(unittest.TestCase):
    def setUp(self):
        self.config = GridConfig(
            exchange="binance",
            symbol="ETH/USDC:USDC",
            grid_type=GridType.NEUTRAL,
            order_amount=Decimal("0.01"),
            lower_price=Decimal("1800"),
            upper_price=Decimal("2000"),
            grid_interval=Decimal("20"),
            fee_rate=Decimal("0.0005"),
        )
        self.state = GridState()
        self.state.initialize_grid_levels(self.config.grid_count, self.config.get_grid_price)
        self.tracker = PositionTrackerImpl(self.config, self.state)
        self.strategy = MagicMock()
        self.strategy.calculate_reverse_order.return_value = (
            GridOrderSide.SELL,
            Decimal("1890"),
            5,
        )

        self.engine = MagicMock()
        self.engine.get_current_price = AsyncMock(return_value=Decimal("1870"))
        self.engine.place_batch_orders = AsyncMock(return_value=[])
        self.engine.place_order = AsyncMock(
            return_value=GridOrder(
                order_id="rev_1",
                grid_id=5,
                side=GridOrderSide.SELL,
                price=Decimal("1890"),
                amount=Decimal("0.01"),
                status=GridOrderStatus.PENDING,
                created_at=datetime.now(),
            )
        )
        self.engine.get_pending_orders.return_value = [
            GridOrder(
                order_id="ord_1",
                grid_id=3,
                side=GridOrderSide.BUY,
                price=Decimal("1840"),
                amount=Decimal("0.01"),
                status=GridOrderStatus.PENDING,
                created_at=datetime.now(),
            ),
            GridOrder(
                order_id="ord_2",
                grid_id=4,
                side=GridOrderSide.SELL,
                price=Decimal("1880"),
                amount=Decimal("0.01"),
                status=GridOrderStatus.PENDING,
                created_at=datetime.now(),
            ),
        ]

        self.coordinator = GridCoordinator(
            config=self.config,
            strategy=self.strategy,
            engine=self.engine,
            tracker=self.tracker,
            grid_state=self.state,
        )

    def test_on_order_filled_handles_fields_without_error(self):
        """Verify _on_order_filled does not crash on keyword arguments or missing state.statistics."""
        buy_order = GridOrder(
            order_id="ord_buy_1",
            grid_id=3,
            side=GridOrderSide.BUY,
            price=Decimal("1840"),
            amount=Decimal("0.01"),
            filled_price=Decimal("1840"),
            filled_amount=Decimal("0.01"),
            status=GridOrderStatus.FILLED,
            created_at=datetime.now(),
        )

        # Execute fill callback
        asyncio.run(self.coordinator._on_order_filled(buy_order))

        self.assertEqual(self.state.filled_buy_count, 1)
        self.assertEqual(self.tracker.buy_count, 1)

        sell_order = GridOrder(
            order_id="ord_sell_1",
            grid_id=4,
            side=GridOrderSide.SELL,
            price=Decimal("1860"),
            amount=Decimal("0.01"),
            filled_price=Decimal("1860"),
            filled_amount=Decimal("0.01"),
            status=GridOrderStatus.FILLED,
            created_at=datetime.now(),
        )

        asyncio.run(self.coordinator._on_order_filled(sell_order))

        self.assertEqual(self.state.filled_sell_count, 1)
        self.assertEqual(self.state.completed_cycles, 1)
        self.assertEqual(self.tracker.completed_cycles, 1)
        # Realized PnL should be positive for completed neutral grid cycle
        self.assertGreater(self.tracker.realized_pnl, Decimal("0"))

    def test_get_statistics_injects_balance_and_pending_orders(self):
        """Verify get_statistics populates non-zero balance and pending counts from engine and balance monitor."""
        self.coordinator.balance_monitor.get_balances = MagicMock(
            return_value={
                "spot_balance": Decimal("150.0"),
                "collateral_balance": Decimal("350.0"),
                "order_locked_balance": Decimal("50.0"),
                "total_balance": Decimal("550.0"),
            }
        )

        stats = asyncio.run(self.coordinator.get_statistics())

        self.assertEqual(stats.total_balance, Decimal("550.0"))
        self.assertEqual(stats.spot_balance, Decimal("150.0"))
        self.assertEqual(stats.pending_buy_orders, 1)
        self.assertEqual(stats.pending_sell_orders, 1)
        self.assertEqual(stats.total_pending_orders, 2)

        display = stats.to_display_dict()
        self.assertEqual(display["balance"]["total"], 550.0)
        self.assertEqual(display["pending_orders"]["total"], 2)


if __name__ == "__main__":
    unittest.main()
