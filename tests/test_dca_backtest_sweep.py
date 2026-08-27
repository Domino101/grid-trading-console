"""
tests/test_dca_backtest_sweep.py —— Phase C4 參數掃描（稽核 4.7–4.10 / 4.8）

對應：docs/dca_chart_backtest_plan_zh-TW.md Phase C4
測試刻意使用短 K 線與少量組合，避免長時間執行。
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import threading
import time
import unittest
from decimal import Decimal

from core.services.dca.backtest.candles import make_candle
from core.services.dca.backtest.replay_driver import ReplayDriver
from core.services.dca.backtest import sweep as sweep_mod
from core.services.dca.backtest.sweep import (
    bar_should_enter_series,
    expand_grid,
    precompute_series_fns,
    run_sweep,
)
from core.services.dca.indicators.config import PriceChangeConfig, RsiConfig
from core.services.dca.models.dca_config import (
    DcaConfig,
    DcaConfigError,
    EntryConfig,
    EntryMode,
)


def _make_config(**overrides) -> DcaConfig:
    kwargs = dict(
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
    kwargs.update(overrides)
    return DcaConfig(**kwargs)


def _sample_candles(n: int = 12, base: float = 100.0):
    candles = []
    price = base
    for i in range(n):
        if i % 4 == 1:
            o, h, l, c = price, price + 2.0, price - 3.0, price - 2.0
        elif i % 4 == 2:
            o, h, l, c = price - 2.0, price + 1.0, price - 4.0, price - 1.0
        elif i % 4 == 3:
            o, h, l, c = price - 1.0, price + 5.0, price - 1.5, price + 4.0
        else:
            o, h, l, c = price, price + 1.0, price - 1.0, price
        candles.append(make_candle(1_000_000 + i * 900, o, h, l, c, 10.0))
        price = c
    return candles


def _declining_candles(n: int = 40, start: float = 100.0):
    candles = []
    price = start
    for i in range(n):
        close = price * 0.99
        high = max(price, close)
        low = min(price, close) * 0.999
        candles.append(make_candle(2_000_000 + i * 900, price, high, low, close, 10.0))
        price = close
    return candles


class ExpandGridTests(unittest.TestCase):
    def test_cartesian_product_size(self):
        base = _make_config()
        rows = expand_grid(base, {
            "take_profit_percent": ["0.8", "1.0", "1.2"],
            "max_safety_orders": [3, 5],
        })
        self.assertEqual(len(rows), 6)
        ok = [r for r in rows if r[1] is not None]
        self.assertEqual(len(ok), 6)


class WorkerLimitTests(unittest.TestCase):
    """4.7 P1: SWEEP_MAX_WORKERS=4 時同時執行中的 replay ≤ 4。"""

    def test_audit_4_7_concurrent_replays_capped(self):
        base = _make_config()
        candles = _sample_candles(8)
        active = 0
        peak = 0
        lock = threading.Lock()

        def hook(phase: str) -> None:
            nonlocal active, peak
            if phase == "start":
                with lock:
                    active += 1
                    peak = max(peak, active)
                time.sleep(0.12)
            else:
                with lock:
                    active -= 1

        original = sweep_mod._run_hook
        sweep_mod._run_hook = hook
        try:
            result = run_sweep(
                base, candles,
                axes={"take_profit_percent": ["0.8", "1.0", "1.2", "1.4", "1.6", "1.8"]},
                initial_capital="1000",
                max_workers=4,
                max_total_bars=300000,
                use_precomputed_indicators=False,
            )
        finally:
            sweep_mod._run_hook = original

        self.assertEqual(len(result.runs), 6)
        self.assertLessEqual(peak, 4)
        self.assertGreaterEqual(peak, min(4, 6))
        self.assertFalse(result.truncated)


class EquivalenceTests(unittest.TestCase):
    """4.8 P0: 預計算觸發序列與逐棒重算，每一棒 should_enter 與最終結果相同。"""

    def _indicator_config(self) -> DcaConfig:
        return _make_config(
            entry=EntryConfig(
                mode=EntryMode.INDICATOR,
                indicators=(PriceChangeConfig(lookback_bars=24),),
            ),
        )

    def test_audit_4_8_per_bar_verdict_identical(self):
        cfg = self._indicator_config()
        candles = _declining_candles(40)
        closes = [float(c.close) for c in candles]
        precomputed = precompute_series_fns(cfg.entry, closes, "long")
        live = bar_should_enter_series(cfg, candles, direction="long", series_fns=None)
        cached = bar_should_enter_series(cfg, candles, direction="long", series_fns=precomputed)
        self.assertEqual(len(live), 40)
        self.assertEqual(live, cached)
        self.assertTrue(any(live), "測試資料必須實際觸發至少一次入場，否則等價性無鑑別力")

    def test_audit_4_8_backtest_result_fields_identical(self):
        cfg = self._indicator_config()
        candles = _declining_candles(40)
        closes = [float(c.close) for c in candles]
        precomputed = precompute_series_fns(cfg.entry, closes, "long")

        live_res = asyncio.run(ReplayDriver(cfg, candles, initial_capital="1000").run())
        cached_res = asyncio.run(
            ReplayDriver(cfg, candles, initial_capital="1000", series_fns=precomputed).run()
        )

        d1 = live_res.to_dict()
        d2 = cached_res.to_dict()
        d1.pop("bars_per_second", None)
        d2.pop("bars_per_second", None)
        d1["metrics"].pop("bars_per_second", None)
        d2["metrics"].pop("bars_per_second", None)
        self.assertEqual(json.dumps(d1, sort_keys=True), json.dumps(d2, sort_keys=True))


class BudgetTests(unittest.TestCase):
    """4.9 P0: 小預算 → truncated=true、bars_used ≤ bars_limit、未完成 run 無指標。"""

    def test_audit_4_9_truncated_does_not_report_incomplete_metrics(self):
        base = _make_config()
        candles = _sample_candles(10)  # 每 run 10 棒
        result = run_sweep(
            base, candles,
            axes={"take_profit_percent": ["0.8", "1.0", "1.2"]},
            initial_capital="1000",
            max_workers=2,
            max_total_bars=25,  # 只夠 2 組（20 棒），第 3 組截斷
            use_precomputed_indicators=False,
        )
        self.assertTrue(result.truncated)
        self.assertLessEqual(result.budget.bars_used, 25)
        self.assertEqual(result.budget.bars_used, 20)
        self.assertEqual(len(result.runs), 2)
        for run in result.runs:
            self.assertIsNotNone(run.metrics)
            self.assertIsNone(run.error)
        self.assertEqual(result.budget.runs_total, 3)
        self.assertEqual(result.budget.runs_completed, 2)


class IllegalParamTests(unittest.TestCase):
    """4.10 P1: 非法參數組合標記 error + 中文原因，整體不崩潰。"""

    def test_audit_4_10_illegal_max_safety_orders_marked_error(self):
        base = _make_config()
        candles = _sample_candles(8)
        result = run_sweep(
            base, candles,
            axes={"max_safety_orders": [0, 3]},
            initial_capital="1000",
            max_workers=2,
            use_precomputed_indicators=False,
        )
        self.assertEqual(len(result.runs), 2)
        errors = [r for r in result.runs if r.error]
        oks = [r for r in result.runs if r.metrics is not None]
        self.assertEqual(len(errors), 1)
        self.assertEqual(len(oks), 1)
        self.assertIsNone(errors[0].metrics)
        self.assertIn("max_safety_orders", errors[0].error)
        self.assertTrue(any(ord(ch) > 127 for ch in errors[0].error), "錯誤訊息必須為中文")

    def test_expand_grid_illegal_does_not_raise(self):
        base = _make_config()
        rows = expand_grid(base, {"max_safety_orders": [0, 2]})
        self.assertEqual(len(rows), 2)
        self.assertIsNone(rows[0][1])
        self.assertIsNotNone(rows[0][2])
        self.assertIsNotNone(rows[1][1])
        with self.assertRaises(DcaConfigError):
            DcaConfig(
                exchange="binance", symbol="ETH/USDT",
                base_order_amount=Decimal("10"), max_safety_orders=0,
            )


class RsiPrecomputeSmokeTests(unittest.TestCase):
    """補充：RSI 路徑的預計算等價（短序列、不跑完整 ReplayDriver）。"""

    def test_rsi_precompute_matches_live_verdicts(self):
        cfg = _make_config(
            entry=EntryConfig(
                mode=EntryMode.INDICATOR,
                indicators=(RsiConfig(),),
            ),
        )
        candles = _declining_candles(50)
        closes = [float(c.close) for c in candles]
        fns = precompute_series_fns(cfg.entry, closes, "long")
        live = bar_should_enter_series(cfg, candles, series_fns=None)
        cached = bar_should_enter_series(cfg, candles, series_fns=fns)
        self.assertEqual(live, cached)


class GridZeroRegressionTests(unittest.TestCase):
    def test_audit_4_11_grid_zero_regression(self):
        proc = subprocess.run(
            ["git", "diff", "--stat", "core/services/grid/", "run_grid_trading.py"],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
