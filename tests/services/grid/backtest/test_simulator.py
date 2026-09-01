"""Tests for GridBacktestSimulator (capital-aware grid playback)."""

from decimal import Decimal
import unittest

from core.services.grid.backtest.models import GridBacktestSpec
from core.services.grid.backtest.allocation import allocate_capital
from core.services.grid.backtest.simulator import GridBacktestSimulator


def _spec(**kwargs) -> GridBacktestSpec:
    base = dict(
        symbol="ETH/USDC:USDC",
        exchange="binance",
        grid_type="neutral",
        lower_price=Decimal("1800"),
        upper_price=Decimal("2000"),
        grid_count=10,
        order_amount=Decimal("0.1"),
        initial_capital=Decimal("10000"),
        leverage=10,
        fee_rate=Decimal("0.0002"),
    )
    base.update(kwargs)
    return GridBacktestSpec(**base)


class SimulatorTests(unittest.TestCase):
    def test_completed_cycle_pnl_and_equity(self):
        # Grid buys at 1880 then sells at 1900. qty = 1000/1900.
        alloc = allocate_capital(
            initial_capital=Decimal("10000"),
            grid_count=10,
            lower_price=Decimal("1800"),
            upper_price=Decimal("2000"),
            per_grid_notional=Decimal("1000"),
            leverage=10,
        )
        candles = [
            {"ts": 100, "o": 1900, "h": 1901, "l": 1899, "c": 1900, "v": 1},
            {"ts": 101, "o": 1900, "h": 1901, "l": 1880, "c": 1885, "v": 1},
            {"ts": 102, "o": 1885, "h": 1905, "l": 1884, "c": 1900, "v": 1},
        ]
        result = GridBacktestSimulator(
            spec=_spec(order_amount=alloc.order_amount),
            candles=candles,
            allocation=alloc,
        ).run()

        self.assertEqual(result.metrics["cycles_completed"], 1)
        qty = float(alloc.order_amount)
        expected_realized = (1900 - 1880) * qty
        self.assertAlmostEqual(result.metrics["realized_pnl"], expected_realized, places=6)
        self.assertGreater(result.metrics["total_fees"], 0)
        self.assertEqual(len([m for m in result.marks if m.side == "buy"]), 1)
        self.assertEqual(len([m for m in result.marks if m.side == "sell"]), 1)

    def test_crossing_detection_does_not_fill_levels_above_start(self):
        alloc = allocate_capital(
            initial_capital=Decimal("10000"),
            grid_count=10,
            lower_price=Decimal("1800"),
            upper_price=Decimal("2000"),
            per_grid_notional=Decimal("1000"),
            leverage=10,
        )
        candles = [
            {"ts": 100, "o": 1900, "h": 1901, "l": 1899, "c": 1900, "v": 1},
            {"ts": 101, "o": 1900, "h": 1901, "l": 1880, "c": 1885, "v": 1},
            {"ts": 102, "o": 1885, "h": 1905, "l": 1884, "c": 1900, "v": 1},
        ]
        result = GridBacktestSimulator(
            spec=_spec(order_amount=alloc.order_amount),
            candles=candles,
            allocation=alloc,
        ).run()
        prices = [m.price for m in result.marks if m.side == "buy"]
        # Only the 1880 buy should fill (and then sell at 1900); no 1900/1920 instant fill.
        self.assertIn(1880.0, prices)
        self.assertNotIn(1900.0, prices)

    def test_insufficient_capital_still_runs_and_warns(self):
        alloc = allocate_capital(
            initial_capital=Decimal("200"),
            grid_count=15,
            lower_price=Decimal("50"),
            upper_price=Decimal("200"),
            per_grid_notional=Decimal("500"),
            leverage=20,
        )
        # explicit order_amount; no reliance on allocation order_amount
        candles = [
            {"ts": 100, "o": 150, "h": 151, "l": 149, "c": 150, "v": 1},
            {"ts": 101, "o": 150, "h": 151, "l": 140, "c": 142, "v": 1},
        ]
        result = GridBacktestSimulator(
            spec=_spec(grid_type="long", order_amount=Decimal("3.0"), initial_capital=Decimal("200")),
            candles=candles,
            allocation=alloc,
        ).run()
        self.assertTrue(result.allocation.insufficient_capital)
        self.assertTrue(any("資金不足" in w for w in result.warnings))

    def test_liquidation_scenario_flags_and_suggests_capital(self):
        alloc = allocate_capital(
            initial_capital=Decimal("200"),
            grid_count=15,
            lower_price=Decimal("50"),
            upper_price=Decimal("200"),
            per_grid_notional=Decimal("500"),
            leverage=20,
        )
        spec = _spec(
            grid_type="long",
            lower_price=Decimal("50"),
            upper_price=Decimal("200"),
            grid_count=15,
            order_amount=Decimal("3.3333"),
            initial_capital=Decimal("200"),
            leverage=20,
            fee_rate=Decimal("0.0004"),
        )
        candles = []
        for i in range(40):
            p = 150.0 - i * 2.5
            candles.append({"ts": 100 + i * 60, "o": p + 1, "h": p + 1, "l": p - 1, "c": p, "v": 1})

        result = GridBacktestSimulator(spec=spec, candles=candles, allocation=alloc).run()
        self.assertTrue(result.risk.liquidation_occurred)
        self.assertIsNotNone(result.risk.liquidation_ts)
        self.assertGreater(result.risk.additional_capital_to_prevent_liquidation or 0.0, 0)

    def test_deterministic(self):
        alloc = allocate_capital(
            initial_capital=Decimal("10000"),
            grid_count=10,
            lower_price=Decimal("1800"),
            upper_price=Decimal("2000"),
            per_grid_notional=Decimal("1000"),
            leverage=10,
        )
        candles = [
            {"ts": 100, "o": 1900, "h": 1901, "l": 1899, "c": 1900, "v": 1},
            {"ts": 101, "o": 1900, "h": 1901, "l": 1880, "c": 1885, "v": 1},
            {"ts": 102, "o": 1885, "h": 1905, "l": 1884, "c": 1900, "v": 1},
        ]
        r1 = GridBacktestSimulator(spec=_spec(order_amount=alloc.order_amount), candles=candles, allocation=alloc).run()
        r2 = GridBacktestSimulator(spec=_spec(order_amount=alloc.order_amount), candles=candles, allocation=alloc).run()
        self.assertEqual(r1.to_dict(), r2.to_dict())


if __name__ == "__main__":
    unittest.main()
