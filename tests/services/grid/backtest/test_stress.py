"""Tests for stress scenario runner."""

from decimal import Decimal
import unittest

from core.services.grid.backtest.models import GridBacktestSpec
from core.services.grid.backtest.allocation import allocate_capital
from core.services.grid.backtest.stress import run_stress


def _spec(**kwargs) -> GridBacktestSpec:
    base = dict(
        symbol="X/USDT",
        exchange="binance",
        grid_type="long",
        lower_price=Decimal("50"),
        upper_price=Decimal("200"),
        grid_count=15,
        order_amount=Decimal("3.3333"),
        initial_capital=Decimal("200"),
        leverage=20,
        fee_rate=Decimal("0.0004"),
        maintenance_margin_rate=Decimal("0.005"),
    )
    base.update(kwargs)
    return GridBacktestSpec(**base)


class StressTests(unittest.TestCase):
    def test_stress_reports_liquidation_for_drop(self):
        candles = []
        for i in range(40):
            p = 150.0 + i * 0.5
            candles.append({"ts": 100 + i * 60, "o": p + 1, "h": p + 1, "l": p - 1, "c": p, "v": 1})
        alloc = allocate_capital(
            initial_capital=Decimal("200"),
            grid_count=15,
            lower_price=Decimal("50"),
            upper_price=Decimal("200"),
            per_grid_notional=Decimal("500"),
            leverage=20,
        )
        result = run_stress(_spec(), candles, allocation=alloc, movements=(-50.0,))
        self.assertEqual(len(result["scenarios"]), 1)
        scenario = result["scenarios"][0]
        self.assertEqual(scenario["movement_pct"], -50.0)
        self.assertTrue(scenario["liquidation_occurred"])
        self.assertGreater(scenario["additional_capital_to_prevent_liquidation"], 0)

    def test_stress_returns_warnings(self):
        candles = [{"ts": 100 + i * 60, "o": 150, "h": 151, "l": 149, "c": 150, "v": 1} for i in range(10)]
        result = run_stress(_spec(), candles, movements=(15.0,))
        self.assertTrue(any("非真實歷史" in w for w in result["warnings"]))

    def test_stress_rejects_empty_candles(self):
        with self.assertRaises(ValueError):
            run_stress(_spec(), [])

    def test_stress_handles_candle_objects(self):
        class DummyCandle:
            def __init__(self, ts, o, h, l, c, v):
                self.ts = ts
                self.open = o
                self.high = h
                self.low = l
                self.close = c
                self.volume = v

        candles = [DummyCandle(100 + i * 60, 150, 151, 149, 150, 1) for i in range(10)]
        result = run_stress(_spec(), candles, movements=(15.0,))
        self.assertEqual(len(result["scenarios"]), 1)


if __name__ == "__main__":
    unittest.main()
