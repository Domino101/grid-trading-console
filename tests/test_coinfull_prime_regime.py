"""
CoinFull Prime 風險閘門 (Regime Gate) 與換倉整合單元測試

對應 docs/CoinFull_Prime_03_NoTrade_Trigger_Spec.md（T1/T2/T5）
與 docs/CoinFull_Prime_04_Position_Sizing_Timing.md（倉位對照表）。
全部為離線測試：決策層為純函數，rebalancer 層以合成資料驅動，不觸網。
"""

import unittest
from datetime import datetime, date, timezone, timedelta

from core.services.coinfull_prime.models import (
    UniverseRankResult,
    RankedItem,
    SignalZScores,
)
from core.services.coinfull_prime.regime_filter import RegimeFilter
from core.services.coinfull_prime.rebalancer import PortfolioRebalancer
from core.services.coinfull_prime.service import compute_next_rebalance_run


def _gate_config(mode: str = "flatten", **over) -> dict:
    cfg = {
        "regime_gate": {
            "enabled": True,
            "log_only": False,
            "mode": mode,
            "trend_ma_days": 50,
            "breadth_threshold_pct": 30.0,
            "funding_percentile_days": 252,
            "funding_threshold_pct": 45.0,
            "risk_off_scale": 0.35,
            "scale_up": {
                "enabled": True, "min_pctile": 45.0, "max_pctile": 80.0,
                "multiplier": 1.15, "max_multiplier": 1.2
            },
            "crash_defer": {"enabled": True, "btc_ret_1d_pct": -5.0, "max_defers": 2},
            "extreme_crowding": {"no_scale_up_above_pctile": 90.0, "halve_above_pctile": 95.0},
        }
    }
    cfg["regime_gate"].update(over)
    return cfg


class TestRegimeDecision(unittest.TestCase):
    """決策層（純函數）"""

    def test_pts3_flatten_mode_no_flatten(self):
        # 三者皆正 → pts=3（過熱）：正常運作但不加碼
        f = RegimeFilter(_gate_config(mode="flatten"))
        d = f.decide(btc_above_ma50=True, breadth_pct=88.0, funding_pctile=70.0)
        self.assertEqual(d.pts, 3)
        self.assertEqual((d.trend_flag, d.breadth_flag, d.funding_flag), (1, 1, 1))
        self.assertFalse(d.force_flatten)
        self.assertTrue(d.allow_new_entries)
        self.assertEqual(d.scale, 1.0)  # pts=3 過熱 → 不加碼
        self.assertFalse(d.degraded)

    def test_pts0_flatten_mode_flatten_and_stop(self):
        # T1 風險關閉（flatten 保守版）→ 平倉並停止下單
        f = RegimeFilter(_gate_config(mode="flatten"))
        d = f.decide(btc_above_ma50=False, breadth_pct=10.0, funding_pctile=20.0)
        self.assertEqual(d.pts, 0)
        self.assertTrue(d.force_flatten)
        self.assertFalse(d.allow_new_entries)
        self.assertEqual(d.scale, 0.0)

    def test_pts0_soft_mode_partial_scale(self):
        # T1 風險關閉（soft 積極版）→ 0.35× 部分倉位，不平倉、可建倉
        f = RegimeFilter(_gate_config(mode="soft"))
        d = f.decide(btc_above_ma50=False, breadth_pct=10.0, funding_pctile=20.0)
        self.assertEqual(d.pts, 0)
        self.assertFalse(d.force_flatten)
        self.assertTrue(d.allow_new_entries)
        self.assertAlmostEqual(d.scale, 0.35)

    def test_pts2_soft_scale_up_bounds(self):
        # 加碼僅在 pts=2（三者恰兩項為正）且資金費率百分位落在 45~80
        f = RegimeFilter(_gate_config(mode="soft"))

        # pts=2（趨勢+費率，廣度不足）且百分位 60 落在加碼區間 → 1.15×
        d1 = f.decide(btc_above_ma50=True, breadth_pct=25.0, funding_pctile=60.0)
        self.assertEqual(d1.pts, 2)
        self.assertAlmostEqual(d1.scale, 1.15)

        # 2026-09-16 情境：趨勢+廣度成立但資金費率百分位僅 29（<45）→ 不加碼
        d2 = f.decide(btc_above_ma50=True, breadth_pct=88.0, funding_pctile=29.0)
        self.assertEqual(d2.pts, 2)
        self.assertAlmostEqual(d2.scale, 1.0)

        # pts=2（趨勢+費率）但百分位 > 80（擁擠）→ 不加碼
        d3 = f.decide(btc_above_ma50=True, breadth_pct=25.0, funding_pctile=92.0)
        self.assertEqual(d3.pts, 2)
        self.assertAlmostEqual(d3.scale, 1.0)

        # 過熱 pts=3（三者皆正）即使百分位在加碼區間也不加碼
        d4 = f.decide(btc_above_ma50=True, breadth_pct=88.0, funding_pctile=60.0)
        self.assertEqual(d4.pts, 3)
        self.assertAlmostEqual(d4.scale, 1.0)

    def test_t5_extreme_crowding_halves(self):
        # T5：資金費率百分位 > 95 且 pts ≤ 1 → 降至 0.5×
        f = RegimeFilter(_gate_config(mode="soft"))
        d = f.decide(btc_above_ma50=False, breadth_pct=10.0, funding_pctile=96.0)
        # 趨勢 0 + 廣度 0 + 費率 1 = pts 1
        self.assertEqual(d.pts, 1)
        self.assertAlmostEqual(d.scale, 0.5)

    def test_t2_crash_defer(self):
        f = RegimeFilter(_gate_config(mode="flatten"))
        d = f.decide(btc_above_ma50=True, breadth_pct=88.0, funding_pctile=60.0, btc_ret_1d_pct=-6.2)
        self.assertTrue(d.crash_defer)

        d2 = f.decide(btc_above_ma50=True, breadth_pct=88.0, funding_pctile=60.0, btc_ret_1d_pct=-4.0)
        self.assertFalse(d2.crash_defer)

        # 未提供 BTC 日報酬 → 不觸發
        d3 = f.decide(btc_above_ma50=True, breadth_pct=88.0, funding_pctile=60.0)
        self.assertFalse(d3.crash_defer)

    def test_degraded_inputs_conservative(self):
        # 資料缺漏（全 None）→ 各元件保守計 0、標記 degraded、flatten 版平倉
        f = RegimeFilter(_gate_config(mode="flatten"))
        d = f.decide(btc_above_ma50=None, breadth_pct=None, funding_pctile=None)
        self.assertTrue(d.degraded)
        self.assertEqual(d.pts, 0)
        self.assertTrue(d.force_flatten)
        # 3 條元件缺漏說明 + 1 條 T1 風險關閉動作
        self.assertEqual(len(d.reasons), 4)


class TestRebalancerGate(unittest.TestCase):
    """rebalancer 層：閘門參數對訂單的影響"""

    def setUp(self):
        self.config = {
            "portfolio": {"total_capital_usdt": 1000.0, "leverage": 1.0, "dry_run": True}
        }
        self.rebalancer = PortfolioRebalancer(self.config)
        sig = SignalZScores()
        self.rank_result = UniverseRankResult(
            universe_size=4,
            long_list=[
                RankedItem(symbol="BTCUSDT", score=2.0, rank=1, signals=sig, side="long", target_notional=250.0),
                RankedItem(symbol="ETHUSDT", score=1.5, rank=2, signals=sig, side="long", target_notional=250.0)
            ],
            short_list=[
                RankedItem(symbol="SOLUSDT", score=-1.5, rank=3, signals=sig, side="short", target_notional=250.0),
                RankedItem(symbol="DOGEUSDT", score=-2.0, rank=4, signals=sig, side="short", target_notional=250.0)
            ],
            all_rankings=[]
        )
        self.prices = {
            "BTCUSDT": 50000.0, "ETHUSDT": 2500.0, "SOLUSDT": 100.0,
            "DOGEUSDT": 0.1, "OLDUSDT": 10.0, "SOLUSDC": 130.0
        }

    def test_force_flatten_closes_all_managed_and_no_entries(self):
        # T1：受管部位（含仍在名單內的 BTCUSDT）全部平倉；
        # 受保護 (SOLUSDC) 與未納管 (UNMANAGED) 絕不觸碰；不產生任何新倉單
        current_positions = {
            "BTCUSDT": 0.005,      # 在目標名單內（多頭 250 USDT）
            "OLDUSDT": 10.0,       # 不在名單（多頭 100 USDT）
            "SOLUSDC": 12.5,       # 外部機器人保護
            "UNMANAGED": 5.0,      # 未登記部位
        }
        prices = dict(self.prices)
        prices["UNMANAGED"] = 20.0

        orders, closed_syms, opened_syms = self.rebalancer.calculate_diff(
            current_positions=current_positions,
            prices=prices,
            rank_result=self.rank_result,
            managed_symbols={"BTCUSDT", "OLDUSDT"},
            protected_symbols={"SOLUSDC"},
            gross_scale=0.0,
            allow_new_entries=False,
            force_flatten=True
        )

        # 兩筆受管部位皆平倉（reduce-only，原因 regime_flatten）
        self.assertEqual(set(closed_syms), {"BTCUSDT", "OLDUSDT"})
        for sym in ("BTCUSDT", "OLDUSDT"):
            o = next(o for o in orders if o.symbol == sym)
            self.assertTrue(o.reduce_only)
            self.assertEqual(o.reason, "regime_flatten")

        # 保護 / 未納管標的不出現在任何訂單
        touched = {o.symbol for o in orders}
        self.assertNotIn("SOLUSDC", touched)
        self.assertNotIn("UNMANAGED", touched)

        # 無任何開倉單
        self.assertEqual(opened_syms, [])
        self.assertEqual(len(orders), 2)

    def test_reduce_only_when_entries_blocked(self):
        # allow_new_entries=False + scale=0.5：
        # 已持有且在名單的 BTCUSDT 僅減倉至 50%（不增倉）；新標的不進場
        current_positions = {
            "BTCUSDT": 0.005,   # 多頭 250 USDT（在名單，目標 250 USDT）
            "SOLUSDT": -2.5     # 空頭 250 USDT（在名單，目標 250 USDT）
        }

        orders, closed_syms, opened_syms = self.rebalancer.calculate_diff(
            current_positions=current_positions,
            prices=self.prices,
            rank_result=self.rank_result,
            gross_scale=0.5,
            allow_new_entries=False,
            force_flatten=False
        )

        # 新標的（ETHUSDT、DOGEUSDT）一律不進場
        self.assertEqual(opened_syms, [])
        touched = {o.symbol for o in orders}
        self.assertNotIn("ETHUSDT", touched)
        self.assertNotIn("DOGEUSDT", touched)

        # BTCUSDT：目標 250*0.5=125 USDT → 由 250 減至 125（賣 0.0025 BTC）
        btc = next(o for o in orders if o.symbol == "BTCUSDT")
        self.assertEqual(btc.side, "sell")
        self.assertAlmostEqual(btc.amount, 0.0025, places=6)
        self.assertTrue(btc.reduce_only)  # 減倉單標記 reduce-only

        # SOLUSDT（空頭）：目標 125 USDT → 由 250 減至 125（買回 1.25 SOL）
        sol = next(o for o in orders if o.symbol == "SOLUSDT")
        self.assertEqual(sol.side, "buy")
        self.assertAlmostEqual(sol.amount, 1.25, places=6)
        self.assertTrue(sol.reduce_only)

    def test_gross_scale_applies_to_new_entries(self):
        # 空倉 + 加碼 scale=1.15：新進名義 = 250*1.15 = 287.5 USDT
        orders, closed_syms, opened_syms = self.rebalancer.calculate_diff(
            current_positions={},
            prices=self.prices,
            rank_result=self.rank_result,
            gross_scale=1.15,
            allow_new_entries=True,
            force_flatten=False
        )
        self.assertEqual(len(opened_syms), 4)
        btc = next(o for o in orders if o.symbol == "BTCUSDT")
        self.assertAlmostEqual(btc.notional_usdt, 287.5, places=1)


class TestSchedulerNextRun(unittest.TestCase):
    """排程：每週四 UTC 00:05（Python weekday: Mon=0 ... Thu=3）"""

    def test_next_thursday_from_wednesday(self):
        now = datetime(2026, 9, 16, 3, 0, tzinfo=timezone.utc)  # 週三
        nxt = compute_next_rebalance_run(now, rebalance_days=7, execution_weekday=3, execution_minute=5)
        self.assertEqual(nxt.weekday(), 3)  # 週四
        self.assertEqual((nxt.hour, nxt.minute), (0, 5))
        self.assertGreater(nxt, now)
        self.assertLessEqual(nxt - now, timedelta(days=7))
        self.assertEqual(nxt, datetime(2026, 9, 17, 0, 5, tzinfo=timezone.utc))

    def test_thursday_before_cutoff_runs_today(self):
        now = datetime(2026, 9, 17, 0, 4, tzinfo=timezone.utc)  # 週四 00:04
        nxt = compute_next_rebalance_run(now, rebalance_days=7, execution_weekday=3, execution_minute=5)
        self.assertEqual(nxt, datetime(2026, 9, 17, 0, 5, tzinfo=timezone.utc))

    def test_thursday_after_cutoff_runs_next_week(self):
        now = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)  # 週四中午
        nxt = compute_next_rebalance_run(now, rebalance_days=7, execution_weekday=3, execution_minute=5)
        self.assertEqual(nxt, datetime(2026, 9, 24, 0, 5, tzinfo=timezone.utc))

    def test_non_weekly_n_day_phase_lock(self):
        # 14 日週期（低頻替代方案）：自錨點起每 14 日一次
        anchor = date(2026, 1, 1)
        now = datetime(2026, 1, 3, 0, 0, tzinfo=timezone.utc)
        nxt = compute_next_rebalance_run(now, rebalance_days=14, execution_weekday=3, execution_minute=5, anchor_date=anchor)
        self.assertEqual(nxt, datetime(2026, 1, 15, 0, 5, tzinfo=timezone.utc))


if __name__ == "__main__":
    unittest.main()
