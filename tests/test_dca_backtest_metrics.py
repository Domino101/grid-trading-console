"""
tests/test_dca_backtest_metrics.py —— Phase C4 績效指標純函式（稽核 4.1–4.6 / 4.11）

對應：docs/dca_chart_backtest_plan_zh-TW.md §4.5 與 Phase C4
"""

from __future__ import annotations

import asyncio
import math
import subprocess
import unittest
from decimal import Decimal

from core.services.dca.backtest.candles import make_candle
from core.services.dca.backtest.metrics import (
    buy_hold_return_pct,
    compute_metrics,
    max_drawdown_pct,
)
from core.services.dca.backtest.models import CycleRecord, EquityPoint
from core.services.dca.backtest.replay_driver import ReplayDriver
from core.services.dca.models.dca_config import DcaConfig, EntryConfig, EntryMode

TOL = 1e-6


def _eq(ts: int, equity: float, qty: float = 0.0, avg=None, status: str = "armed") -> EquityPoint:
    return EquityPoint(
        ts=ts, equity=equity, position_quantity=qty, average_cost=avg, status=status,
    )


def _cycle(**kwargs) -> CycleRecord:
    defaults = dict(
        index=1,
        direction="long",
        exit_reason="take_profit",
        realized_pnl=0.0,
        bars=1,
    )
    defaults.update(kwargs)
    return CycleRecord(**defaults)


def _candles_from_closes(closes):
    out = []
    prev = closes[0]
    for i, close in enumerate(closes):
        high = max(prev, close)
        low = min(prev, close)
        out.append(make_candle(1_000_000 + i * 900, prev, high, low, close, 10))
        prev = close
    return out


class MetricsVectorTests(unittest.TestCase):
    """4.1 P0: 手工可驗算的小型 case。"""

    def test_audit_4_1_hand_computed_three_cycles(self):
        equity = [
            _eq(1, 1000.0),
            _eq(2, 1200.0),
            _eq(3, 900.0),
            _eq(4, 1100.0),
        ]
        cycles = [
            _cycle(index=1, exit_reason="take_profit", realized_pnl=80.0, bars=5, mfe_pct=3.0, mae_pct=1.0),
            _cycle(index=2, exit_reason="take_profit", realized_pnl=50.0, bars=3, mfe_pct=2.0, mae_pct=0.5),
            _cycle(index=3, exit_reason="stop_loss", realized_pnl=-30.0, bars=4, mfe_pct=1.0, mae_pct=4.0),
        ]
        candles = _candles_from_closes([100.0, 101.0, 99.0, 110.0])
        metrics = compute_metrics(
            equity=equity,
            cycles=cycles,
            candles=candles,
            initial_capital=1000.0,
            realized_pnl=100.0,
            total_fees=2.5,
            cycles_completed=3,
            holding_bars=2,
            direction="long",
        )
        self.assertAlmostEqual(metrics["total_return_pct"], 10.0, delta=TOL)
        self.assertAlmostEqual(metrics["win_rate_pct"], round(2 / 3 * 100.0, 2), delta=TOL)
        self.assertAlmostEqual(metrics["max_drawdown_pct"], 25.0, delta=TOL)
        self.assertAlmostEqual(metrics["buy_hold_return_pct"], 10.0, delta=TOL)
        self.assertAlmostEqual(metrics["alpha_pct"], 0.0, delta=TOL)
        self.assertAlmostEqual(metrics["profit_factor"], round(130.0 / 30.0, 4), delta=TOL)
        self.assertAlmostEqual(metrics["avg_cycle_bars"], 4.0, delta=TOL)
        self.assertEqual(metrics["cycles_completed"], 3)
        self.assertAlmostEqual(metrics["exposure_pct"], 50.0, delta=TOL)


class BuyHoldTests(unittest.TestCase):
    """4.2 P0: buy&hold 與 alpha 必存在；做多/做空各一。"""

    def test_audit_4_2_long_buy_hold(self):
        self.assertAlmostEqual(buy_hold_return_pct(100.0, 110.0, "long"), 10.0, delta=TOL)
        candles = _candles_from_closes([100.0, 110.0])
        equity = [_eq(1, 1000.0), _eq(2, 1050.0)]
        m = compute_metrics(
            equity=equity, cycles=[], candles=candles,
            initial_capital=1000.0, realized_pnl=0.0, total_fees=0.0,
            cycles_completed=0, holding_bars=0, direction="long",
        )
        self.assertIn("buy_hold_return_pct", m)
        self.assertIn("alpha_pct", m)
        self.assertAlmostEqual(m["buy_hold_return_pct"], 10.0, delta=TOL)
        self.assertAlmostEqual(m["alpha_pct"], -5.0, delta=TOL)

    def test_audit_4_2_short_buy_hold_is_inverted(self):
        self.assertAlmostEqual(buy_hold_return_pct(100.0, 110.0, "short"), -10.0, delta=TOL)
        candles = _candles_from_closes([100.0, 90.0])
        equity = [_eq(1, 1000.0), _eq(2, 1010.0)]
        m = compute_metrics(
            equity=equity, cycles=[], candles=candles,
            initial_capital=1000.0, realized_pnl=10.0, total_fees=0.0,
            cycles_completed=0, holding_bars=0, direction="short",
        )
        # 現貨跌 10%；做空基準為 +10%
        self.assertAlmostEqual(m["buy_hold_return_pct"], 10.0, delta=TOL)
        self.assertAlmostEqual(m["alpha_pct"], -9.0, delta=TOL)


class ProfitFactorTests(unittest.TestCase):
    """4.3 P0: 無虧損輪時 profit_factor 為 None（不是 0、不是 inf）。"""

    def test_audit_4_3_no_losing_cycle_returns_none(self):
        cycles = [
            _cycle(index=1, exit_reason="take_profit", realized_pnl=10.0),
            _cycle(index=2, exit_reason="take_profit", realized_pnl=5.0),
        ]
        m = compute_metrics(
            equity=[_eq(1, 1000.0), _eq(2, 1015.0)],
            cycles=cycles,
            candles=_candles_from_closes([100.0, 101.0]),
            initial_capital=1000.0, realized_pnl=15.0, total_fees=0.0,
            cycles_completed=2, holding_bars=1, direction="long",
        )
        self.assertIsNone(m["profit_factor"])
        self.assertFalse(math.isinf(m["profit_factor"] or 0))
        self.assertIsNot(m["profit_factor"], 0)


class DrawdownTests(unittest.TestCase):
    """4.4 P0: MDD 定義為峰值到谷底，非首尾差。"""

    def test_audit_4_4_peak_to_trough_not_first_last(self):
        self.assertAlmostEqual(max_drawdown_pct([100, 120, 90, 110]), 25.0, delta=TOL)
        # 首尾差只有 10%，不得被誤用
        first_last = (100 - 110) / 100 * 100.0
        self.assertNotAlmostEqual(25.0, abs(first_last), delta=1.0)
        m = compute_metrics(
            equity=[_eq(1, 100.0), _eq(2, 120.0), _eq(3, 90.0), _eq(4, 110.0)],
            cycles=[],
            candles=_candles_from_closes([100.0, 120.0, 90.0, 110.0]),
            initial_capital=100.0, realized_pnl=0.0, total_fees=0.0,
            cycles_completed=0, holding_bars=0, direction="long",
        )
        self.assertAlmostEqual(m["max_drawdown_pct"], 25.0, delta=TOL)


class AccountingTests(unittest.TestCase):
    """4.5 P0: realized_pnl / total_fees 取自引擎會計，而非 fills[].fee 加總。"""

    def test_audit_4_5_uses_engine_statistics_not_fill_fee_sum(self):
        cfg = DcaConfig(
            exchange="binance",
            symbol="ETH/USDT",
            direction="long",
            base_order_amount=Decimal("10"),
            max_safety_orders=3,
            active_safety_orders_window=2,
            price_deviation_percent=Decimal("1.0"),
            take_profit_percent=Decimal("1.0"),
            fee_rate=Decimal("0.001"),
            cooldown_seconds=300,
            entry=EntryConfig(mode=EntryMode.IMMEDIATE),
        )
        candles = [
            make_candle(1000, 100, 101, 99, 100, 10),
            make_candle(2000, 100, 101, 99, 100, 10),
            make_candle(3000, 100, 101, 98.5, 100, 10),
            make_candle(4000, 100, 102, 98.0, 102, 10),
        ]
        driver = ReplayDriver(cfg, candles, initial_capital="1000")
        result = asyncio.run(driver.run())

        stats = driver.coordinator.build_statistics()
        fills = driver.coordinator.fills_snapshot()
        self.assertGreater(len(fills), 0)

        # 刻意汙染顯示層 fills[].fee，與引擎 _total_fees 分岔
        for row in fills:
            row["fee"] = 999.0
        fill_fee_sum = sum(float(row["fee"]) for row in fills)
        engine_fees = float(stats.total_fees)
        engine_realized = float(stats.realized_profit)
        self.assertNotAlmostEqual(engine_fees, fill_fee_sum, delta=1.0)

        self.assertAlmostEqual(result.metrics["realized_pnl"], engine_realized, delta=TOL)
        self.assertAlmostEqual(result.metrics["total_fees"], engine_fees, delta=TOL)
        self.assertNotAlmostEqual(result.metrics["total_fees"], fill_fee_sum, delta=1.0)
        # 顯示層契約：build_statistics().to_display_dict()["profit"]["realized"]
        display = stats.to_display_dict()
        self.assertAlmostEqual(
            result.metrics["realized_pnl"], display["profit"]["realized"], delta=TOL,
        )


class UnknownNotZeroTests(unittest.TestCase):
    """4.6 P0: 無任何輪次時 win_rate / avg_cycle_bars / mfe 皆為 None（G-5）。"""

    def test_audit_4_6_no_cycles_metrics_are_none(self):
        m = compute_metrics(
            equity=[_eq(1, 1000.0), _eq(2, 1000.0)],
            cycles=[],
            candles=_candles_from_closes([100.0, 100.0]),
            initial_capital=1000.0, realized_pnl=0.0, total_fees=0.0,
            cycles_completed=0, holding_bars=0, direction="long",
        )
        self.assertIsNone(m["win_rate_pct"])
        self.assertIsNone(m["avg_cycle_bars"])
        self.assertIsNone(m["mfe_pct"])
        self.assertIsNone(m["mae_pct"])
        self.assertIsNone(m["profit_factor"])
        self.assertNotEqual(m["win_rate_pct"], 0)
        self.assertNotEqual(m["avg_cycle_bars"], 0)
        self.assertNotEqual(m["mfe_pct"], 0)


class GridZeroRegressionTests(unittest.TestCase):
    """4.11 P0: 網格核心零變更。"""

    def test_audit_4_11_grid_zero_regression(self):
        """網格核心零變更守門。

        P4-1 起守門規則升級：差異必須落在 tests/_grid_guard.GRID_DIFF_ALLOWLIST
        （已授權的跨切面改動）內，任何未授權的網格差異仍會失敗。
        """
        from pathlib import Path

        from tests._grid_guard import assert_grid_core_zero_regression

        assert_grid_core_zero_regression(self, Path(__file__).resolve().parents[1])

if __name__ == "__main__":
    unittest.main()
