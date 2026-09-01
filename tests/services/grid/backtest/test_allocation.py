"""Tests for grid backtest capital allocation (pure functions)."""

from decimal import Decimal
import unittest

from core.services.grid.backtest.allocation import allocate_capital, reference_price


class AllocateCapitalTests(unittest.TestCase):
    def test_reference_price(self):
        self.assertEqual(reference_price(Decimal("1800"), Decimal("2000")), Decimal("1900"))

    def test_per_grid_notional_allocates_order_amount(self):
        alloc = allocate_capital(
            initial_capital=Decimal("10000"),
            grid_count=10,
            lower_price=Decimal("1800"),
            upper_price=Decimal("2000"),
            per_grid_notional=Decimal("1000"),
            leverage=10,
        )
        self.assertEqual(alloc.max_fillable_cells, 10)
        self.assertFalse(alloc.insufficient_capital)
        # order_amount = 1000 / 1900
        self.assertAlmostEqual(float(alloc.order_amount), 1000 / 1900, places=6)
        self.assertEqual(alloc.per_grid_notional, Decimal("1000"))

    def test_capital_percent_allocation(self):
        alloc = allocate_capital(
            initial_capital=Decimal("10000"),
            grid_count=10,
            lower_price=Decimal("1800"),
            upper_price=Decimal("2000"),
            per_grid_capital_percent=Decimal("10"),
            leverage=10,
        )
        self.assertEqual(alloc.per_grid_notional, Decimal("1000"))

    def test_insufficient_capital_reports_max_cells(self):
        alloc = allocate_capital(
            initial_capital=Decimal("200"),
            grid_count=15,
            lower_price=Decimal("50"),
            upper_price=Decimal("200"),
            per_grid_notional=Decimal("500"),
            leverage=20,
        )
        self.assertTrue(alloc.insufficient_capital)
        # margin per cell = 500/20 = 25 => 200/25 = 8
        self.assertEqual(alloc.max_fillable_cells, 8)
        self.assertIn("資金不足", alloc.message)

    def test_requires_parameter(self):
        with self.assertRaises(ValueError):
            allocate_capital(
                initial_capital=Decimal("10000"),
                grid_count=10,
                lower_price=Decimal("1800"),
                upper_price=Decimal("2000"),
            )

    def test_rejects_non_positive_capital(self):
        with self.assertRaises(ValueError):
            allocate_capital(
                initial_capital=Decimal("0"),
                grid_count=10,
                lower_price=Decimal("1800"),
                upper_price=Decimal("2000"),
                per_grid_notional=Decimal("1000"),
            )


if __name__ == "__main__":
    unittest.main()
