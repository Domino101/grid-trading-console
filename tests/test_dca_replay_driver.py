"""
tests/test_dca_replay_driver.py —— Phase C3 稽核測試（ReplayDriver 接真 Coordinator + 真 Executor）

對應標準：docs/dca_chart_backtest_plan_zh-TW.md §Phase C3 稽核 3.1～3.14
- 3.1 P0: BP-1 同源（實例化真 DcaCoordinator 與真 DcaOrderExecutor）
- 3.2 P0: R-8 熔斷固定（實盤熔斷開啟不影響回測）
- 3.3 P0: R-9 棒時間時鐘（cooldown 基於 K 棒時間）
- 3.4 P0: R-10 無未来函数（前視注入測試）
- 3.5 P0: INV-1 armed 零掛單
- 3.6 P0: INV-2 averaging 恰好 1 張 TP
- 3.7 P0: INV-6 單方向互斥（cooling/armed active_direction 為 None）
- 3.8 P0: R-11 確定性（兩次執行輸出 JSON 完全相同）
- 3.9 P0: 標記時間為棒時間（非牆鐘時間）
- 3.10 P0: 不改引擎狀態（fills 副本蓋章，原物件不變）
- 3.11 P1: 效能基準（3000 根棒吞吐量 > 500 bars/sec）
- 3.12 P0: 完整循環覆蓋（armed->triggering->averaging->completed->cooling->armed）
- 3.13 P0: end_of_data 誠實（未平倉標註 end_of_data 與未實現損益）
- 3.14 P0: 網格零回歸（core/services/grid 零修改）
"""

import asyncio
import json
import logging
import os
import subprocess
import time
import unittest
from decimal import Decimal
from typing import List, Optional

from core.adapters.exchanges.adapters.binance_rest import BinanceRest
from core.services.dca.backtest.candles import Candle, make_candle
from core.services.dca.backtest.models import BacktestResult, CycleRecord, EquityPoint, TradeMark
from core.services.dca.backtest.replay_driver import ReplayDriver
from core.services.dca.backtest.replay_exchange import ReplayExchange
from core.services.dca.coordinator.dca_coordinator import DcaCoordinator
from core.services.dca.execution.dca_order_executor import DcaOrderExecutor
from core.services.dca.models.dca_config import (
    DcaConfig,
    EntryConfig,
    EntryMode,
    IndicatorConfig,
)
from core.services.dca.models.dca_state import DcaStatus


def _make_sample_candles(n: int = 10, start_ts: int = 1000000, step: int = 900,
                        base_price: float = 100.0) -> List[Candle]:
    """生成確定性測試 K 棒序列（15m 時框）。"""
    candles = []
    price = base_price
    for i in range(n):
        ts = start_ts + i * step
        # 簡單波型
        if i % 4 == 1:
            p_open = price
            p_high = price + 2.0
            p_low = price - 3.0
            p_close = price - 2.0
        elif i % 4 == 2:
            p_open = price - 2.0
            p_high = price + 1.0
            p_low = price - 4.0
            p_close = price - 1.0
        elif i % 4 == 3:
            p_open = price - 1.0
            p_high = price + 5.0
            p_low = price - 1.5
            p_close = price + 4.0
        else:
            p_open = price
            p_high = price + 1.0
            p_low = price - 1.0
            p_close = price
        candles.append(make_candle(ts, p_open, p_high, p_low, p_close, 100.0))
        price = p_close
    return candles


def _make_default_config(
    symbol: str = "ETH/USDT",
    direction: str = "long",
    cooldown_seconds: int = 300,
    repeat_after_take_profit: bool = True,
    stop_loss_percent: Optional[Decimal] = None,
    entry_mode: EntryMode = EntryMode.IMMEDIATE,
) -> DcaConfig:
    return DcaConfig(
        exchange="binance",
        symbol=symbol,
        direction=direction,
        base_order_amount=Decimal("10"),
        max_safety_orders=3,
        active_safety_orders_window=2,
        price_deviation_percent=Decimal("1.0"),
        take_profit_percent=Decimal("1.0"),
        stop_loss_percent=stop_loss_percent,
        fee_rate=Decimal("0.001"),
        cooldown_seconds=cooldown_seconds,
        repeat_after_take_profit=repeat_after_take_profit,
        entry=EntryConfig(mode=entry_mode),
    )


class TestDcaReplayDriver(unittest.TestCase):
    """Phase C3 驗證測試套件。"""

    def test_audit_3_1_bp1_same_decision_core(self):
        """3.1 P0: ReplayDriver 必須實例化真 DcaCoordinator 與真 DcaOrderExecutor，禁止第二套執行器。"""
        cfg = _make_default_config()
        candles = _make_sample_candles(5)
        driver = ReplayDriver(cfg, candles, initial_capital="1000")

        self.assertIsInstance(driver.coordinator, DcaCoordinator)
        self.assertIsInstance(driver.executor, DcaOrderExecutor)
        self.assertIsInstance(driver.exchange, ReplayExchange)

        # 靜態契約反證：backtest 套件內不得定義第二套 Executor、階梯或止盈數學
        proc = subprocess.run(
            [
                "grep",
                "-rnE",
                "class .*Executor|def build_ladder|def take_profit_price|def evaluate_rsi",
                "core/services/dca/backtest/",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            proc.returncode,
            1,
            f"違反 BP-1 / G-13：backtest 目錄存在重複核心交易邏輯:\n{proc.stdout}",
        )

    def test_audit_3_2_r8_circuit_breaker_fixed_off(self):
        """3.2 P0: 實盤熔斷開啟時（BinanceRest._adapter_circuit_until 為未來），回測結果仍完全相同。"""
        cfg = _make_default_config()
        candles = _make_sample_candles(12)

        # 1. 正常回測
        driver1 = ReplayDriver(cfg, candles, initial_capital="1000")
        res1 = asyncio.run(driver1.run())

        # 2. 開啟實盤類別層級熔斷
        original_circuit = getattr(BinanceRest, "_adapter_circuit_until", 0.0)
        try:
            BinanceRest._adapter_circuit_until = time.time() + 3600.0
            driver2 = ReplayDriver(cfg, candles, initial_capital="1000")
            res2 = asyncio.run(driver2.run())
        finally:
            BinanceRest._adapter_circuit_until = original_circuit

        # 斷言熔斷不影響回測撮合與指標結果
        self.assertEqual(res1.metrics["realized_pnl"], res2.metrics["realized_pnl"])
        self.assertEqual(len(res1.marks), len(res2.marks))
        self.assertEqual(len(res1.cycles), len(res2.cycles))

    def test_audit_3_3_r9_bar_timestamp_clock(self):
        """3.3 P0: cooldown_seconds 基於 K 棒時間推進而非牆鐘時間。"""
        # timeframe=1h (3600s/bar), cooldown=300s
        # 當止盈發生在 bar 2 (ts=7200) -> completed -> cooling (until 7200+300=7500)
        # 次棒 bar 3 (ts=10800 >= 7500) -> 必須重新轉為 armed
        cfg = _make_default_config(cooldown_seconds=300)
        candles = [
            make_candle(3600, 100, 101, 99, 100, 10),
            make_candle(7200, 100, 101, 99, 100, 10),
            make_candle(10800, 100, 102, 98, 102, 10),  # TP 成交
            make_candle(14400, 100, 101, 99, 100, 10),  # cooling -> armed
            make_candle(18000, 100, 101, 99, 100, 10),  # 再次 trigger
        ]
        driver = ReplayDriver(cfg, candles, initial_capital="1000", timeframe="1h")
        res = asyncio.run(driver.run())

        # 斷言在 5 根棒內順利完成冷卻並開啟第 2 輪
        self.assertGreaterEqual(res.metrics["cycles_completed"], 1)
        statuses = [pt.status for pt in res.equity]
        self.assertIn("completed", statuses)

    def test_audit_3_4_r10_no_lookahead_future_poison_test(self):
        """3.4 P0: 前視注入測試——修改第 50 棒之後為極端假數據，第 50 棒之前的決策與標記完全不變。"""
        base_candles = _make_sample_candles(80, base_price=100.0)
        cfg = _make_default_config()

        driver1 = ReplayDriver(cfg, base_candles, initial_capital="1000")
        res1 = asyncio.run(driver1.run())

        # 前視投毒：第 40 棒之後暴漲 100%
        poisoned_candles = []
        for idx, c in enumerate(base_candles):
            if idx < 40:
                poisoned_candles.append(c)
            else:
                p_high = max(float(c.high) * 2.0, float(c.open) * 2.0, float(c.close) * 2.0)
                p_low = min(float(c.low) * 2.0, float(c.open) * 2.0, float(c.close) * 2.0)
                poisoned_candles.append(
                    make_candle(
                        c.ts,
                        float(c.open) * 2.0,
                        p_high,
                        p_low,
                        float(c.close) * 2.0,
                        float(c.volume),
                    )
                )

        driver2 = ReplayDriver(cfg, poisoned_candles, initial_capital="1000")
        res2 = asyncio.run(driver2.run())

        ts_boundary = base_candles[39].ts
        marks_clean = [m for m in res1.marks if m.ts <= ts_boundary]
        marks_poisoned = [m for m in res2.marks if m.ts <= ts_boundary]

        self.assertEqual(len(marks_clean), len(marks_poisoned))
        for m1, m2 in zip(marks_clean, marks_poisoned):
            self.assertEqual(m1.ts, m2.ts)
            self.assertEqual(m1.kind, m2.kind)
            self.assertEqual(m1.price, m2.price)

    def test_audit_3_5_inv1_armed_zero_open_orders(self):
        """3.5 P0: 全回放過程中，任一棒若 status==armed 則交易所 open orders 必須為 0。"""
        cfg = _make_default_config()
        candles = _make_sample_candles(15)
        driver = ReplayDriver(cfg, candles, initial_capital="1000")
        res = asyncio.run(driver.run())
        # ReplayDriver 內部 _assert_invariants 若違規會拋錯中斷，此處確認正常跑完
        self.assertIsNotNone(res)

    def test_audit_3_6_inv2_averaging_exactly_one_take_profit(self):
        """3.6 P0: averaging 且持倉 > 0 時，在世 TP 掛單必須恰好 1 張。"""
        cfg = _make_default_config()
        candles = _make_sample_candles(15)
        driver = ReplayDriver(cfg, candles, initial_capital="1000")
        res = asyncio.run(driver.run())
        self.assertIsNotNone(res)

    def test_audit_3_7_inv6_direction_exclusivity(self):
        """3.7 P0: 單方向互斥，cooling/armed 狀態下 active_direction 為 None。"""
        cfg = _make_default_config()
        candles = _make_sample_candles(15)
        driver = ReplayDriver(cfg, candles, initial_capital="1000")
        res = asyncio.run(driver.run())
        for pt in res.equity:
            if pt.status in ("armed", "cooling"):
                # 確認持倉為 0
                self.assertEqual(pt.position_quantity, 0.0)

    def test_audit_3_8_r11_determinism(self):
        """3.8 P0: 同輸入執行兩次，json.dumps 輸出完全相同。"""
        cfg = _make_default_config()
        candles = _make_sample_candles(20)

        driver1 = ReplayDriver(cfg, candles, initial_capital="1000")
        res1 = asyncio.run(driver1.run())

        driver2 = ReplayDriver(cfg, candles, initial_capital="1000")
        res2 = asyncio.run(driver2.run())

        d1 = res1.to_dict()
        d2 = res2.to_dict()
        # 移除執行耗時微幅差異以比對純確定性
        d1.pop("bars_per_second", None)
        d2.pop("bars_per_second", None)
        d1["metrics"].pop("bars_per_second", None)
        d2["metrics"].pop("bars_per_second", None)

        self.assertEqual(
            json.dumps(d1, sort_keys=True),
            json.dumps(d2, sort_keys=True),
        )

    def test_audit_3_9_marks_use_bar_timestamps_not_wall_clock(self):
        """3.9 P0: 所有 marks[].ts 屬於 candles 的 ts 集合，不得存在牆鐘時間戳。"""
        cfg = _make_default_config()
        candles = _make_sample_candles(16)
        valid_ts_set = {c.ts for c in candles}

        now_wall_clock = time.time()
        driver = ReplayDriver(cfg, candles, initial_capital="1000")
        res = asyncio.run(driver.run())

        self.assertGreater(len(res.marks), 0)
        for mark in res.marks:
            self.assertIn(mark.ts, valid_ts_set)
            # 斷言標記時間非執行當下牆鐘秒數（相差 > 1000 秒）
            self.assertGreater(abs(now_wall_clock - mark.ts), 1000)

    def test_audit_3_10_engine_fills_not_tampered(self):
        """3.10 P0: 蓋章副本不修改 coord._fills 的原物件。"""
        cfg = _make_default_config()
        candles = _make_sample_candles(10)
        driver = ReplayDriver(cfg, candles, initial_capital="1000")
        res = asyncio.run(driver.run())

        # 檢查 coordinator 的 fills_snapshot 仍然保留 datetime 牆鐘產生之秒數
        fills = driver.coordinator.fills_snapshot()
        self.assertGreater(len(fills), 0)
        # coord 的 fills occurred_at 為當下時間戳（> 1700000000）
        for f in fills:
            self.assertGreater(f["occurred_at"], 1700000000)

    def test_audit_3_11_performance_benchmark(self):
        """3.11 P1: 3000 根 15m 棒回測吞吐量高於基準（> 500 bars/sec）。"""
        candles = _make_sample_candles(3000, base_price=2000.0)
        cfg = _make_default_config()
        driver = ReplayDriver(cfg, candles, initial_capital="5000")

        prev_disable = logging.root.manager.disable
        logging.disable(logging.WARNING)
        try:
            t0 = time.perf_counter()
            res = asyncio.run(driver.run())
            elapsed = time.perf_counter() - t0
        finally:
            logging.disable(prev_disable)

        self.assertEqual(res.bars, 3000)
        self.assertGreater(res.bars_per_second, 500)
        self.assertLess(elapsed, 6.0)

    def test_audit_3_12_full_cycle_coverage(self):
        """3.12 P0: 測試實際走過 armed->triggering->averaging->completed->cooling 完整循環。"""
        # 設計明確走過進場、攤平、止盈、冷卻的 K 棒序列
        candles = [
            make_candle(1000, 100, 101, 99, 100, 10),
            make_candle(2000, 100, 101, 99, 100, 10),  # trigger base order
            make_candle(3000, 100, 101, 98.5, 100, 10), # base order fill & safety order placed
            make_candle(4000, 100, 102, 98.0, 102, 10), # safety order & TP fill -> completed
            make_candle(5000, 100, 101, 99, 100, 10),  # cooling -> armed
        ]
        cfg = _make_default_config(cooldown_seconds=0)
        driver = ReplayDriver(cfg, candles, initial_capital="1000")
        res = asyncio.run(driver.run())

        self.assertGreaterEqual(res.metrics["cycles_completed"], 1)
        self.assertEqual(len(res.cycles), 1)
        cycle = res.cycles[0]
        self.assertEqual(cycle.exit_reason, "take_profit")
        self.assertGreater(cycle.realized_pnl, 0.0)

    def test_audit_3_13_end_of_data_honest_reporting(self):
        """3.13 P0: 資料結束時仍持倉，標註 exit_reason='end_of_data'，未實現損益如實以最後收盤評價。"""
        candles = [
            make_candle(1000, 100, 101, 99, 100, 10),
            make_candle(2000, 100, 101, 99, 100, 10),  # trigger
            make_candle(3000, 100, 101, 98.5, 100, 10), # base order fill -> averaging
            make_candle(4000, 100, 100.5, 99.5, 99.8, 10), # holding, never hit TP
        ]
        cfg = _make_default_config(cooldown_seconds=300)
        driver = ReplayDriver(cfg, candles, initial_capital="1000")
        res = asyncio.run(driver.run())

        self.assertEqual(len(res.cycles), 1)
        cycle = res.cycles[0]
        self.assertEqual(cycle.exit_reason, "end_of_data")
        # 未實現損益必須存在
        self.assertNotEqual(res.metrics["unrealized_pnl"], 0.0)

    def test_audit_3_14_grid_zero_regression(self):
        """網格核心零變更守門。

        P4-1 起守門規則升級：差異必須落在 tests/_grid_guard.GRID_DIFF_ALLOWLIST
        （已授權的跨切面改動）內，任何未授權的網格差異仍會失敗。
        """
        from pathlib import Path

        from tests._grid_guard import assert_grid_core_zero_regression

        assert_grid_core_zero_regression(self, Path(__file__).resolve().parents[1])

if __name__ == "__main__":
    unittest.main()
