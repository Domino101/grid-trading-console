"""Tests for grid backtest risk/liquidation pure functions."""

from decimal import Decimal
import unittest

from core.services.grid.backtest.risk import (
    additional_capital_for_coverage,
    build_risk_metrics,
    liquidation_price_long,
    liquidation_price_short,
    margin_state,
)
from core.services.grid.backtest.models import GridEquityPoint


class RiskFunctionTests(unittest.TestCase):
    def test_liquidation_price_long_known(self):
        # wallet=50, qty=10, avg=100, mmr=0.005
        # p = (50 - 10*100) / (10*(0.005-1)) = -950 / -9.95 = 95.477...
        price = liquidation_price_long(
            Decimal("50"), Decimal("10"), Decimal("100"), Decimal("0.005")
        )
        self.assertAlmostEqual(float(price), 95.4773869, places=4)

    def test_liquidation_price_short_known(self):
        # wallet=50, qty=-10, avg=100, mmr=0.005
        # p = (50 + 10*100) / (10*(1+0.005)) = 1050 / 10.05 = 104.477...
        price = liquidation_price_short(
            Decimal("50"), Decimal("-10"), Decimal("100"), Decimal("0.005")
        )
        self.assertAlmostEqual(float(price), 104.4776119, places=4)

    def test_margin_state_negative_equity_is_liquidated(self):
        state = margin_state(
            wallet_balance=Decimal("50"),
            position_quantity=Decimal("10"),
            average_cost=Decimal("100"),
            price=Decimal("80"),
            leverage=10,
            maintenance_margin_rate=Decimal("0.005"),
        )
        # unrealized = (80-100)*10 = -200; equity = 50-200 = -150; coverage negative.
        self.assertEqual(state["wallet_balance"], Decimal("50"))
        self.assertEqual(state["unrealized_pnl"], Decimal("-200"))
        self.assertEqual(state["equity"], Decimal("-150"))
        self.assertTrue(state["equity"] < 0)
        self.assertTrue(state["liquidated"])

    def test_additional_capital_for_negative_equity(self):
        needed = additional_capital_for_coverage(
            wallet_balance=Decimal("50"),
            position_quantity=Decimal("10"),
            average_cost=Decimal("100"),
            price=Decimal("80"),
            maintenance_margin_rate=Decimal("0.005"),
        )
        # maintenance = 10*80*0.005 = 4; required equity = 4; current = -150 => needed 154
        self.assertGreater(float(needed), 0)
        self.assertAlmostEqual(float(needed), 154.0, places=4)

    def test_margin_state_zero_position_no_liquidation(self):
        state = margin_state(
            wallet_balance=Decimal("10000"),
            position_quantity=Decimal("0"),
            average_cost=Decimal("0"),
            price=Decimal("100"),
            leverage=10,
            maintenance_margin_rate=Decimal("0.005"),
        )
        self.assertFalse(state["liquidated"])
        self.assertIsNone(state["liquidation_price"])
        self.assertIsNone(state["margin_ratio"])


class BuildRiskMetricsTests(unittest.TestCase):
    def test_no_equity_returns_default(self):
        metrics = build_risk_metrics([])
        self.assertFalse(metrics.liquidation_occurred)
        self.assertIsNone(metrics.min_equity)
        self.assertIsNone(metrics.additional_capital_to_prevent_liquidation)

    def test_flags_liquidated_point(self):
        points = [
            GridEquityPoint(
                ts=100,
                equity=1000.0,
                wallet_balance=1000.0,
                position_quantity=0.0,
                margin_used=0.0,
                maintenance_margin=0.0,
                margin_ratio=None,
                liquidation_price=None,
                status="open",
            ),
            GridEquityPoint(
                ts=200,
                equity=-100.0,
                wallet_balance=0.0,
                position_quantity=10.0,
                average_cost=100.0,
                margin_used=80.0,
                maintenance_margin=4.0,
                margin_ratio=-25.0,
                liquidation_price=95.0,
                status="liquidated",
            ),
        ]
        metrics = build_risk_metrics(points)
        self.assertTrue(metrics.liquidation_occurred)
        self.assertEqual(metrics.liquidation_ts, 200)
        self.assertEqual(metrics.liquidation_price, 95.0)
        self.assertGreater(metrics.additional_capital_to_prevent_liquidation or 0.0, 0)


if __name__ == "__main__":
    unittest.main()
