"""
DCA 指標引擎測試（Phase 1 稽核 1.5 / 1.5b / 1.5c / 1.5d / 1.5e / 1.6 / 1.7 / 1.8 / 1.9 / 1.10）

鎖定向量來源：docs/dca_bot_implementation_plan_zh-TW.md §4.3 / §4.5
獨立參考實作：scripts/verify_dca_indicators.py、scripts/verify_dca_convergence.py
"""

import math
import random
import unittest
from unittest.mock import MagicMock

from core.services.dca.indicators import (
    BollingerConfig,
    IndicatorResult,
    MacdConfig,
    PriceChangeConfig,
    RsiConfig,
    evaluate_indicator,
    first_valid_index,
    indicator_trigger_series,
)
from core.services.dca.indicators.bollinger import bollinger_series
from core.services.dca.indicators.config import (
    DEFAULT_CONFIRM_BARS,
    DEFAULT_ENTRY_LOGIC,
    DEFAULT_FETCH_LIMIT,
    DEFAULT_PRICE_SOURCE,
    DEFAULT_TIMEFRAME,
)
from core.services.dca.indicators.macd import macd_series
from core.services.dca.indicators.price_change import price_change_series
from core.services.dca.indicators.rsi import rsi_series

TOL = 1e-6

# §4.3.5 / 稽核 1.5：確定性輸入序列（與 scripts/verify_dca_indicators.py 相同）
V = [round(100 + 10 * math.sin(i / 7.0) + 0.15 * i, 4) for i in range(120)]


def _realistic(n, seed=42):
    """與 scripts/verify_dca_convergence.py 相同的 GBM 隨機遊走。"""
    rng = random.Random(seed)
    p, out = 2500.0, []
    for _ in range(n):
        p *= math.exp(rng.gauss(0, 0.004))
        out.append(round(p, 4))
    return out


class LockedVectorTests(unittest.TestCase):
    """稽核 1.5 / 1.5b：鎖定向量，容差 1e-6"""

    def test_rsi_14_locked(self):
        r = evaluate_indicator(RsiConfig(), V, "long")
        self.assertAlmostEqual(r.value, 21.977841, delta=TOL)

    def test_macd_locked(self):
        m = evaluate_indicator(MacdConfig(), V, "long")
        self.assertAlmostEqual(m.details["macd"], -2.240581, delta=TOL)
        self.assertAlmostEqual(m.details["signal"], -0.784542, delta=TOL)
        self.assertAlmostEqual(m.value, -1.456039, delta=TOL)  # histogram

    def test_bollinger_locked(self):
        b = evaluate_indicator(BollingerConfig(), V, "long")
        self.assertAlmostEqual(b.details["lower"], 105.193225, delta=TOL)
        self.assertAlmostEqual(b.details["mid"], 116.876210, delta=TOL)
        self.assertAlmostEqual(b.details["upper"], 128.559195, delta=TOL)

    def test_price_change_locked_and_off_by_one(self):
        """稽核 1.5b：lookback=24 → -11.782277；lookback=23 → -12.373066。"""
        p24 = evaluate_indicator(PriceChangeConfig(lookback_bars=24), V, "long")
        self.assertAlmostEqual(p24.value, -11.782277, delta=TOL)
        # 索引語意：close[119] vs close[95]（往回第 24 根）
        manual = (V[119] - V[95]) / V[95] * 100
        self.assertAlmostEqual(p24.value, manual, delta=TOL)

        p23 = evaluate_indicator(PriceChangeConfig(lookback_bars=23), V, "long")
        self.assertAlmostEqual(p23.value, -12.373066, delta=TOL)
        manual23 = (V[119] - V[96]) / V[96] * 100
        self.assertAlmostEqual(p23.value, manual23, delta=TOL)

    def test_rsi_reference_formula_match(self):
        """直接對齊 §4.3.1 公式（Wilder 平滑）一步步驗算。"""
        d = [V[i] - V[i - 1] for i in range(1, len(V))]
        ag = sum(max(x, 0) for x in d[:14]) / 14
        al = sum(max(-x, 0) for x in d[:14]) / 14
        for x in d[14:]:
            ag = (ag * 13 + max(x, 0)) / 14
            al = (al * 13 + max(-x, 0)) / 14
        expected = 100 - 100 / (1 + ag / al)
        r = evaluate_indicator(RsiConfig(), V, "long")
        self.assertAlmostEqual(r.value, expected, delta=1e-12)


class ConvergenceTests(unittest.TestCase):
    """稽核 1.5c：IIR 收斂（fetch_limit 300 vs 301 < 1e-6；50 根 > 1e-2）"""

    @classmethod
    def setUpClass(cls):
        cls.full = _realistic(3000)

    def _rsi_last(self, xs):
        return [x for x in rsi_series(xs, 14) if x is not None][-1]

    def _macd_sig_last(self, xs):
        _, sig, _ = macd_series(xs, 12, 26, 9)
        return [x for x in sig if x is not None][-1]

    def test_300_vs_301_converged(self):
        a, b = self.full[-300:], self.full[-301:]
        self.assertLess(abs(self._rsi_last(a) - self._rsi_last(b)), TOL)
        self.assertLess(abs(self._macd_sig_last(a) - self._macd_sig_last(b)), TOL)

    def test_50_bars_diverge_proves_discriminative(self):
        """反向斷言：50 根與 2500 根基準差異 > 1e-2（證明測試有鑑別力）。"""
        ref_rsi = self._rsi_last(self.full[-2500:])
        ref_sig = self._macd_sig_last(self.full[-2500:])
        short = self.full[-50:]
        self.assertGreater(abs(self._rsi_last(short) - ref_rsi), 1e-2)
        self.assertGreater(abs(self._macd_sig_last(short) - ref_sig), 1e-2)


class DefaultsTests(unittest.TestCase):
    """稽核 1.5e：§4.3 表格預設值逐欄位對上"""

    def test_rsi_defaults(self):
        cfg = RsiConfig()
        self.assertEqual((cfg.period, cfg.oversold, cfg.overbought, cfg.condition),
                         (14, 30.0, 70.0, "level"))

    def test_macd_defaults(self):
        cfg = MacdConfig()
        self.assertEqual((cfg.fast, cfg.slow, cfg.signal, cfg.condition),
                         (12, 26, 9, "cross"))

    def test_bollinger_defaults(self):
        cfg = BollingerConfig()
        self.assertEqual((cfg.period, cfg.std_dev, cfg.condition), (20, 2.0, "touch"))

    def test_price_change_defaults(self):
        cfg = PriceChangeConfig()
        self.assertEqual(cfg.lookback_bars, 24)
        self.assertEqual(cfg.long_threshold_percent, -3.0)
        self.assertEqual(cfg.short_threshold_percent, 3.0)

    def test_pipeline_defaults(self):
        self.assertEqual(DEFAULT_TIMEFRAME, "15m")
        self.assertEqual(DEFAULT_FETCH_LIMIT, 300)
        self.assertEqual(DEFAULT_PRICE_SOURCE, "close")
        self.assertEqual(DEFAULT_ENTRY_LOGIC, "all")
        self.assertEqual(DEFAULT_CONFIRM_BARS, 1)

    def test_invalid_config_rejected_in_chinese(self):
        with self.assertRaises(ValueError) as ctx:
            RsiConfig(period=200)
        self.assertIn("period", str(ctx.exception))
        with self.assertRaises(ValueError) as ctx:
            MacdConfig(fast=30, slow=10)
        self.assertIn("slow", str(ctx.exception))
        with self.assertRaises(ValueError):
            BollingerConfig(std_dev=10.0)
        with self.assertRaises(ValueError):
            PriceChangeConfig(lookback_bars=0)


class WarmupTests(unittest.TestCase):
    """稽核 1.6：暖機期首個有效索引 + 暖機期內不觸發"""

    def test_first_valid_indices(self):
        self.assertEqual(first_valid_index(rsi_series(V, 14)), 14)
        line, sig, _ = macd_series(V, 12, 26, 9)
        self.assertEqual(first_valid_index(line), 25)
        self.assertEqual(first_valid_index(sig), 33)
        self.assertEqual(first_valid_index(bollinger_series(V, 20, 2.0)), 19)
        self.assertEqual(first_valid_index(price_change_series(V, 24)), 24)

    def test_warmup_period_reports_insufficient_data(self):
        for cfg, series_fn in [
            (RsiConfig(), lambda xs: rsi_series(xs, 14)),
            (MacdConfig(), lambda xs: macd_series(xs, 12, 26, 9)[2]),
            (BollingerConfig(), lambda xs: bollinger_series(xs, 20, 2.0)),
            (PriceChangeConfig(), lambda xs: price_change_series(xs, 24)),
        ]:
            with self.subTest(cfg=type(cfg).__name__):
                result = evaluate_indicator(cfg, V[:10], "long")
                self.assertTrue(result.insufficient_data)
                self.assertFalse(result.triggered)
                self.assertIsNone(result.value)
                self.assertLess(result.warmup_have, result.warmup_need)


class InsufficientDataTests(unittest.TestCase):
    """稽核 1.7：資料不足不得誤判（triggered=False，不拋例外）"""

    def test_short_sequences_never_trigger(self):
        cfgs = [RsiConfig(), MacdConfig(), BollingerConfig(), PriceChangeConfig()]
        for cfg in cfgs:
            for seq in ([], [100.0], [100.0, 101.0, 99.0]):
                for direction in ("long", "short"):
                    with self.subTest(cfg=type(cfg).__name__, n=len(seq), dir=direction):
                        result = evaluate_indicator(cfg, seq, direction)
                        self.assertFalse(result.triggered)
                        self.assertTrue(result.insufficient_data)

    def test_trigger_series_tolerates_short_input(self):
        for cfg in [RsiConfig(), MacdConfig(), BollingerConfig(), PriceChangeConfig()]:
            series = indicator_trigger_series(cfg, [100.0, 99.0], "long")
            self.assertEqual(series, [False, False])


class UnclosedBarTests(unittest.TestCase):
    """稽核 1.8：未收線 K 棒被丟棄（n 根 vs n+1 根尾棒未收線結果一致）"""

    def test_all_indicators_drop_unclosed_tail(self):
        closed = V[:]
        with_unclosed = V[:] + [9999.0]  # 極端尾棒，應被丟棄
        for cfg in [RsiConfig(), MacdConfig(), BollingerConfig(), PriceChangeConfig()]:
            with self.subTest(cfg=type(cfg).__name__):
                a = evaluate_indicator(cfg, closed, "long", last_bar_closed=True)
                b = evaluate_indicator(cfg, with_unclosed, "long", last_bar_closed=False)
                self.assertEqual(a.value, b.value)
                self.assertEqual(a.triggered, b.triggered)
                self.assertEqual(a.details, b.details)


def _sawtooth_down(n):
    """持續陰跌序列（RSI 低檔、hist < 0）。"""
    return [200 - 0.5 * i for i in range(n)]


def _sawtooth_up(n):
    return [100 + 0.5 * i for i in range(n)]


class RsiConditionTests(unittest.TestCase):
    """稽核 1.5d：RSI 三種 condition × long/short 全覆蓋（測試數列已用掃描法定標）"""

    def test_level_long_and_short(self):
        down = _sawtooth_down(60)
        r_long = evaluate_indicator(RsiConfig(condition="level"), down, "long")
        self.assertLess(r_long.value, 30)
        self.assertTrue(r_long.triggered)

        up = _sawtooth_up(60)
        r_short = evaluate_indicator(RsiConfig(condition="level"), up, "short")
        self.assertGreater(r_short.value, 70)
        self.assertTrue(r_short.triggered)
        # 反向不觸發
        self.assertFalse(evaluate_indicator(RsiConfig(condition="level"), down, "short").triggered)
        self.assertFalse(evaluate_indicator(RsiConfig(condition="level"), up, "long").triggered)

    def test_cross_in_long_and_short(self):
        cfg = RsiConfig(condition="cross_in")
        up40 = _sawtooth_up(40)
        plunge = up40 + [up40[-1] - 16]  # RSI 100 → 28.9（由上向下穿越 30）
        r_long = evaluate_indicator(cfg, plunge, "long")
        self.assertGreater(evaluate_indicator(cfg, plunge[:-1], "long").value, 30)
        self.assertLessEqual(r_long.value, 30)
        self.assertTrue(r_long.triggered)
        self.assertFalse(evaluate_indicator(cfg, plunge, "short").triggered)

        dn40 = _sawtooth_down(40)
        spike = dn40 + [dn40[-1] + 16]    # RSI 0 → 71.1（由下向上穿越 70）
        r_short = evaluate_indicator(cfg, spike, "short")
        self.assertLess(evaluate_indicator(cfg, spike[:-1], "short").value, 70)
        self.assertGreaterEqual(r_short.value, 70)
        self.assertTrue(r_short.triggered)
        self.assertFalse(evaluate_indicator(cfg, spike, "long").triggered)

    def test_cross_out_long_and_short(self):
        cfg = RsiConfig(condition="cross_out")
        dn40 = _sawtooth_down(40)
        dip_recover = dn40 + [dn40[-1] - 20, dn40[-1] - 20 + 15]  # RSI 0 → 37.9 穿回 30
        r_long = evaluate_indicator(cfg, dip_recover, "long")
        self.assertLessEqual(evaluate_indicator(cfg, dip_recover[:-1], "long").value, 30)
        self.assertGreater(r_long.value, 30)
        self.assertTrue(r_long.triggered)
        self.assertFalse(evaluate_indicator(cfg, dip_recover, "short").triggered)

        up40 = _sawtooth_up(40)
        fade = up40 + [up40[-1] + 20, up40[-1] + 20 - 12]          # RSI 100 → 67.2 穿回 70
        r_short = evaluate_indicator(cfg, fade, "short")
        self.assertGreaterEqual(evaluate_indicator(cfg, fade[:-1], "short").value, 70)
        self.assertLess(r_short.value, 70)
        self.assertTrue(r_short.triggered)
        self.assertFalse(evaluate_indicator(cfg, fade, "long").triggered)


def _truncate_at_flip(closes, pick, mode):
    """在 MACD 序列中尋找最後一次穿越（帶邊際），回傳截斷到穿越當棒的 closes。"""
    line, _, hist = macd_series(closes, 12, 26, 9)
    seq = line if pick == "line" else hist
    for i in range(len(seq) - 1, 0, -1):
        a, b = seq[i - 1], seq[i]
        if a is None or b is None:
            continue
        if mode == "neg2pos" and a <= 0 < b and abs(a) > 1e-3:
            return closes[:i + 1]
        if mode == "pos2neg" and a >= 0 > b and abs(a) > 1e-3:
            return closes[:i + 1]
    raise AssertionError(f"測試資料中找不到 {pick} {mode} 穿越點")


class MacdConditionTests(unittest.TestCase):
    """稽核 1.5d：MACD 三種 condition × long/short 全覆蓋

    雙速數列（慢趨勢 40 棒 → 急趨勢 10 棒）確保 hist/line 遠離 0 的
    穩態收斂，使穿越點明確可判定。
    """

    def _v_shape(self):
        pre = [200 - 0.3 * i for i in range(40)] + \
              [188.0 - 1.2 * i for i in range(1, 11)]
        return pre, pre + [176.0 + 1.5 * i for i in range(1, 20)]

    def _inverse_v_shape(self):
        pre = [100 + 0.3 * i for i in range(40)] + \
              [112.0 + 1.2 * i for i in range(1, 11)]
        return pre, pre + [124.0 - 1.5 * i for i in range(1, 20)]

    def test_cross_golden_and_death(self):
        cfg = MacdConfig(condition="cross")
        _, v = self._v_shape()
        golden = _truncate_at_flip(v, "hist", "neg2pos")
        prev = evaluate_indicator(cfg, golden[:-1], "long")
        r_long = evaluate_indicator(cfg, golden, "long")
        self.assertLessEqual(prev.value, 0)
        self.assertGreater(r_long.value, 0)
        self.assertTrue(r_long.triggered)
        self.assertFalse(evaluate_indicator(cfg, golden, "short").triggered)

        _, inv = self._inverse_v_shape()
        death = _truncate_at_flip(inv, "hist", "pos2neg")
        prev_s = evaluate_indicator(cfg, death[:-1], "short")
        r_short = evaluate_indicator(cfg, death, "short")
        self.assertGreaterEqual(prev_s.value, 0)
        self.assertLess(r_short.value, 0)
        self.assertTrue(r_short.triggered)
        self.assertFalse(evaluate_indicator(cfg, death, "long").triggered)

    def test_hist_sign(self):
        cfg = MacdConfig(condition="hist_sign")
        pre_up, _ = self._inverse_v_shape()
        r_long = evaluate_indicator(cfg, pre_up, "long")
        self.assertGreater(r_long.value, 0)
        self.assertTrue(r_long.triggered)

        pre_dn, _ = self._v_shape()
        r_short = evaluate_indicator(cfg, pre_dn, "short")
        self.assertLess(r_short.value, 0)
        self.assertTrue(r_short.triggered)
        # 反向不觸發
        self.assertFalse(evaluate_indicator(cfg, pre_dn, "long").triggered)
        self.assertFalse(evaluate_indicator(cfg, pre_up, "short").triggered)

    def test_zero_cross(self):
        cfg = MacdConfig(condition="zero_cross")
        recover = [200 - 0.7 * i for i in range(90)] + \
                  [137.7 + 1.2 * i for i in range(1, 30)]
        up_flip = _truncate_at_flip(recover, "line", "neg2pos")
        line, _, _ = macd_series(up_flip, 12, 26, 9)
        self.assertLess(line[-2], 0)
        self.assertGreater(line[-1], 0)
        self.assertTrue(evaluate_indicator(cfg, up_flip, "long").triggered)
        self.assertFalse(evaluate_indicator(cfg, up_flip, "short").triggered)

        crash = [100 + 0.7 * i for i in range(90)] + \
                [162.3 - 1.2 * i for i in range(1, 30)]
        down_flip = _truncate_at_flip(crash, "line", "pos2neg")
        line2, _, _ = macd_series(down_flip, 12, 26, 9)
        self.assertGreater(line2[-2], 0)
        self.assertLess(line2[-1], 0)
        self.assertTrue(evaluate_indicator(cfg, down_flip, "short").triggered)
        self.assertFalse(evaluate_indicator(cfg, down_flip, "long").triggered)


class BollingerConditionTests(unittest.TestCase):
    """稽核 1.5d：BB 三種 condition × long/short 全覆蓋（數列已掃描定標）"""

    _FLAT = [100.0] * 28
    _BASE = [100 + (0.05 if i % 2 == 0 else -0.05) for i in range(40)]

    def test_touch_long_and_short(self):
        cfg = BollingerConfig(condition="touch")
        dip = self._FLAT + [100.0, 96.0]
        r_long = evaluate_indicator(cfg, dip, "long")
        self.assertLessEqual(dip[-1], r_long.details["lower"])
        self.assertTrue(r_long.triggered)
        self.assertFalse(evaluate_indicator(cfg, dip, "short").triggered)

        spike = self._FLAT + [100.0, 104.0]
        r_short = evaluate_indicator(cfg, spike, "short")
        self.assertGreaterEqual(spike[-1], r_short.details["upper"])
        self.assertTrue(r_short.triggered)
        self.assertFalse(evaluate_indicator(cfg, spike, "long").triggered)

    def test_break_long_and_short(self):
        cfg = BollingerConfig(condition="break")
        snap = self._BASE + [96.0]  # 前棒在通道內、當棒跌破下軌
        prev_band = evaluate_indicator(cfg, snap[:-1], "long").details
        self.assertGreater(snap[-2], prev_band["lower"])
        r_long = evaluate_indicator(cfg, snap, "long")
        self.assertLessEqual(snap[-1], r_long.details["lower"])
        self.assertTrue(r_long.triggered)
        self.assertFalse(evaluate_indicator(cfg, snap, "short").triggered)

        spike = self._BASE + [104.0]  # 前棒在通道內、當棒突破上軌
        prev_band_s = evaluate_indicator(cfg, spike[:-1], "short").details
        self.assertLess(spike[-2], prev_band_s["upper"])
        r_short = evaluate_indicator(cfg, spike, "short")
        self.assertGreaterEqual(spike[-1], r_short.details["upper"])
        self.assertTrue(r_short.triggered)
        self.assertFalse(evaluate_indicator(cfg, spike, "long").triggered)

    def test_return_long_and_short(self):
        cfg = BollingerConfig(condition="return")
        rec = self._FLAT + [96.0, 99.5]  # 前棒跌破下軌、當棒收回通道內
        prev_band = evaluate_indicator(cfg, rec[:-1], "long").details
        self.assertLessEqual(rec[-2], prev_band["lower"])
        r_long = evaluate_indicator(cfg, rec, "long")
        self.assertGreater(rec[-1], r_long.details["lower"])
        self.assertTrue(r_long.triggered)
        self.assertFalse(evaluate_indicator(cfg, rec, "short").triggered)

        rec_s = self._FLAT + [104.0, 100.5]  # 前棒突破上軌、當棒收回
        prev_band_s = evaluate_indicator(cfg, rec_s[:-1], "short").details
        self.assertGreaterEqual(rec_s[-2], prev_band_s["upper"])
        r_short = evaluate_indicator(cfg, rec_s, "short")
        self.assertLess(rec_s[-1], r_short.details["upper"])
        self.assertTrue(r_short.triggered)
        self.assertFalse(evaluate_indicator(cfg, rec_s, "long").triggered)

    def test_percent_b_value(self):
        b = evaluate_indicator(BollingerConfig(), V, "long")
        self.assertAlmostEqual(b.value, b.details["percent_b"], delta=1e-12)


class PriceChangeTriggerTests(unittest.TestCase):
    """稽核 1.5d：Price Change long/short 語意"""

    def test_long_trigger_on_drop_and_short_on_rally(self):
        cfg = PriceChangeConfig(lookback_bars=24)
        r_long = evaluate_indicator(cfg, V, "long")
        self.assertTrue(r_long.triggered)  # -11.78 ≤ -3.0
        r_short = evaluate_indicator(cfg, V, "short")
        self.assertFalse(r_short.triggered)

        rally = [100 * (1.005 ** i) for i in range(60)]
        r_rally_short = evaluate_indicator(cfg, rally, "short")
        self.assertTrue(r_rally_short.triggered)
        self.assertFalse(evaluate_indicator(cfg, rally, "long").triggered)
