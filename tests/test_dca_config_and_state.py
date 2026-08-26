"""
DCA 設定驗證與狀態機測試（Phase 1 稽核 1.9 / 1.10 / 1.11 / 1.12 / 1.13）
"""

import unittest
from unittest.mock import MagicMock

from core.services.dca.entry import EntryEvaluator, EntryVerdict
from core.services.dca.indicators.config import RsiConfig
from core.services.dca.models import (
    DcaConfig,
    DcaConfigError,
    DcaDirection,
    DcaState,
    DcaStatus,
    EntryConfig,
    EntryMode,
    InvalidDcaTransition,
)


BASE_CFG = dict(
    exchange="binance",
    symbol="ETH/USDC:USDC",
    base_order_amount="20",
)


class ConfigValidationTests(unittest.TestCase):
    """稽核 1.13：設定驗證邊界，全部產生明確中文錯誤訊息"""

    def test_valid_minimal_config_passes(self):
        cfg = DcaConfig(**BASE_CFG)
        self.assertEqual(cfg.direction, DcaDirection.LONG)
        self.assertEqual(cfg.max_safety_orders, 20)
        self.assertEqual(cfg.active_safety_orders_window, 5)
        self.assertTrue(cfg.repeat_after_take_profit)
        self.assertEqual(cfg.cooldown_seconds, 300)

    def test_max_safety_orders_zero_rejected(self):
        with self.assertRaises(DcaConfigError) as ctx:
            DcaConfig(**BASE_CFG, max_safety_orders=0)
        self.assertIn("max_safety_orders", str(ctx.exception))

    def test_max_safety_orders_over_50_rejected(self):
        with self.assertRaises(DcaConfigError) as ctx:
            DcaConfig(**BASE_CFG, max_safety_orders=51)
        self.assertIn("max_safety_orders", str(ctx.exception))

    def test_zero_deviation_rejected(self):
        with self.assertRaises(DcaConfigError) as ctx:
            DcaConfig(**BASE_CFG, price_deviation_percent=0)
        self.assertIn("price_deviation_percent", str(ctx.exception))

    def test_order_size_multiplier_below_one_rejected(self):
        with self.assertRaises(DcaConfigError) as ctx:
            DcaConfig(**BASE_CFG, order_size_multiplier="0.9")
        self.assertIn("order_size_multiplier", str(ctx.exception))

    def test_zero_take_profit_rejected(self):
        with self.assertRaises(DcaConfigError) as ctx:
            DcaConfig(**BASE_CFG, take_profit_percent=0)
        self.assertIn("take_profit_percent", str(ctx.exception))

    def test_leverage_200_rejected(self):
        with self.assertRaises(DcaConfigError) as ctx:
            DcaConfig(**BASE_CFG, leverage=200)
        self.assertIn("leverage", str(ctx.exception))

    def test_both_direction_on_short_incapable_exchange_rejected(self):
        """direction=both 但交易所不支援做空 → 明確中文錯誤。"""
        with self.assertRaises(DcaConfigError) as ctx:
            DcaConfig(**{**BASE_CFG, "exchange": "unknown_dex"}, direction="both")
        self.assertIn("不支援做空", str(ctx.exception))

    def test_invalid_exchange_rejected_with_choices(self):
        with self.assertRaises(DcaConfigError) as ctx:
            DcaConfig(**{**BASE_CFG, "exchange": "o1exchange"})
        self.assertIn("exchange", str(ctx.exception))
        self.assertIn("binance", str(ctx.exception))

    def test_bool_numeric_fields_rejected(self):
        with self.assertRaises(DcaConfigError) as ctx:
            DcaConfig(**BASE_CFG, leverage=True)
        self.assertIn("leverage", str(ctx.exception))

    def test_indicator_mode_without_indicators_rejected(self):
        """稽核 1.9：空指標清單 + mode=indicator → 設定驗證即報錯。"""
        with self.assertRaises(DcaConfigError) as ctx:
            EntryConfig(mode="indicator", indicators=())
        self.assertIn("至少需要 1 個指標", str(ctx.exception))

    def test_entry_defaults_match_plan(self):
        e = EntryConfig()
        self.assertEqual(e.mode, EntryMode.IMMEDIATE)
        self.assertEqual(e.logic, "all")
        self.assertEqual(e.timeframe, "15m")
        self.assertEqual(e.fetch_limit, 300)
        self.assertEqual(e.price_source, "close")
        self.assertEqual(e.confirm_bars, 1)
        self.assertIsNone(e.max_wait_seconds)

    def test_invalid_timeframe_rejected(self):
        with self.assertRaises(DcaConfigError):
            EntryConfig(timeframe="2h")

    def test_confirm_bars_bounds(self):
        with self.assertRaises(DcaConfigError):
            EntryConfig(confirm_bars=0)
        with self.assertRaises(DcaConfigError):
            EntryConfig(confirm_bars=6)


class StateMachineTests(unittest.TestCase):
    """稽核 1.12：合法轉移全覆蓋 + 非法轉移拋 InvalidDcaTransition（INV-5）"""

    def _state(self, status: DcaStatus, direction=None) -> DcaState:
        s = DcaState(bot_id="dca-binance-long-eth-20260825120000", status=status)
        s.active_direction = direction
        return s

    def test_happy_path_full_cycle(self):
        """draft → armed → triggering → averaging → completed → cooling → armed"""
        s = self._state(DcaStatus.DRAFT)
        s.transition_to(DcaStatus.ARMED)
        s.active_direction = "long"  # 條件成立，鎖定方向（§4.4）
        s.transition_to(DcaStatus.TRIGGERING)
        s.transition_to(DcaStatus.AVERAGING)
        s.transition_to(DcaStatus.COMPLETED)
        self.assertEqual(s.cycle_count, 1)
        s.transition_to(DcaStatus.COOLING)
        self.assertIsNone(s.active_direction)  # cooling 解除鎖定
        s.transition_to(DcaStatus.ARMED)

    def test_safety_fill_self_loop(self):
        s = self._state(DcaStatus.AVERAGING, "long")
        s.transition_to(DcaStatus.AVERAGING)  # 安全單成交 → 重掛 TP 自迴圈
        self.assertEqual(s.status, DcaStatus.AVERAGING)

    def test_completed_to_stopped_when_repeat_disabled(self):
        s = self._state(DcaStatus.COMPLETED)
        s.transition_to(DcaStatus.STOPPED)

    def test_stop_path(self):
        s = self._state(DcaStatus.ARMED)
        s.transition_to(DcaStatus.STOPPING)
        s.transition_to(DcaStatus.STOPPED)

    def test_triggering_back_to_armed_when_condition_fades(self):
        s = self._state(DcaStatus.ARMED)
        s.active_direction = "long"
        s.transition_to(DcaStatus.TRIGGERING)
        s.transition_to(DcaStatus.ARMED)  # 條件在成交前失效
        self.assertIsNone(s.active_direction)

    def test_failed_reachable_from_every_active_state(self):
        for status in (DcaStatus.DRAFT, DcaStatus.ARMED, DcaStatus.COMPLETED,
                       DcaStatus.COOLING, DcaStatus.STOPPING):
            with self.subTest(from_status=status):
                self._state(status).transition_to(DcaStatus.FAILED)
        self._state(DcaStatus.TRIGGERING, "long").transition_to(DcaStatus.FAILED)
        self._state(DcaStatus.AVERAGING, "short").transition_to(DcaStatus.FAILED)

    def test_illegal_transitions_raise(self):
        illegal = [
            (DcaStatus.ARMED, DcaStatus.COMPLETED),
            (DcaStatus.STOPPED, DcaStatus.AVERAGING),
            (DcaStatus.DRAFT, DcaStatus.AVERAGING),
            (DcaStatus.DRAFT, DcaStatus.TRIGGERING),
            (DcaStatus.STOPPED, DcaStatus.ARMED),
            (DcaStatus.FAILED, DcaStatus.ARMED),
            (DcaStatus.COOLING, DcaStatus.AVERAGING),
            (DcaStatus.COMPLETED, DcaStatus.AVERAGING),
            (DcaStatus.TRIGGERING, DcaStatus.COOLING),
            (DcaStatus.STOPPING, DcaStatus.ARMED),
        ]
        for src, dst in illegal:
            with self.subTest(transition=f"{src.value} -> {dst.value}"):
                with self.assertRaises(InvalidDcaTransition):
                    self._state(src, "long").transition_to(dst)

    def test_triggering_requires_locked_direction(self):
        """進入 triggering 前必須鎖定方向（INV-6 程式錯誤防護）。"""
        s = self._state(DcaStatus.ARMED)  # active_direction = None
        with self.assertRaises(InvalidDcaTransition):
            s.transition_to(DcaStatus.TRIGGERING)

    def test_terminal_states_have_no_outgoing(self):
        for terminal in (DcaStatus.STOPPED, DcaStatus.FAILED):
            with self.subTest(terminal=terminal):
                for dst in DcaStatus:
                    with self.assertRaises(InvalidDcaTransition):
                        self._state(terminal).transition_to(dst)

    def test_average_cost_unknown_is_none_not_zero(self):
        """G-5：無部位時均價為 None，不得為 0。"""
        s = self._state(DcaStatus.ARMED)
        self.assertIsNone(s.average_cost)


def _series_mock(values):
    mock = MagicMock()
    mock.return_value = list(values)
    return mock


class EntryEvaluatorTests(unittest.TestCase):
    """稽核 1.9 / 1.10 / 1.11"""

    def _evaluator(self, mode="indicator", logic="all", confirm_bars=1,
                   indicators=(RsiConfig(),), series_map=None):
        entry = EntryConfig(mode=mode, logic=logic,
                            confirm_bars=confirm_bars, indicators=indicators)
        return EntryEvaluator(entry, series_fns=series_map or {})

    def test_immediate_always_true_and_calls_no_indicator(self):
        """稽核 1.11：immediate 恆 True 且完全不呼叫任何指標函式。"""
        spy = _series_mock([True] * 10)
        ev = self._evaluator(mode="immediate",
                             indicators=(RsiConfig(),),
                             series_map={RsiConfig: spy})
        for closes in ([1.0, 2.0, 3.0], []):
            verdict = ev.should_enter(closes, "long")
            self.assertTrue(verdict.should_enter)
        self.assertEqual(spy.call_count, 0)
        # evaluate_indicators 同樣不得觸碰指標
        self.assertEqual(ev.evaluate_indicators([1.0, 2.0]), ())

    def test_logic_all_and_any_semantics(self):
        """以兩種指標型別精確驗證 AND / OR。"""
        from core.services.dca.indicators.config import PriceChangeConfig
        rsi_cfg, pc_cfg = RsiConfig(), PriceChangeConfig()

        both_true = {RsiConfig: _series_mock([False, True, True]),
                     PriceChangeConfig: _series_mock([True, True, True])}
        ev_all = self._evaluator(logic="all", indicators=(rsi_cfg, pc_cfg),
                                 series_map=both_true)
        self.assertTrue(ev_all.should_enter([1.0, 2.0, 3.0], "long").should_enter)

        one_false = {RsiConfig: _series_mock([False, False, True]),
                     PriceChangeConfig: _series_mock([True, True, False])}
        ev_all2 = self._evaluator(logic="all", indicators=(rsi_cfg, pc_cfg),
                                  series_map=one_false)
        self.assertFalse(ev_all2.should_enter([1.0, 2.0, 3.0], "long").should_enter)

        ev_any = self._evaluator(logic="any", indicators=(rsi_cfg, pc_cfg),
                                 series_map=one_false)
        self.assertTrue(ev_any.should_enter([1.0, 2.0, 3.0], "long").should_enter)

        all_false = {RsiConfig: _series_mock([False, False, False]),
                     PriceChangeConfig: _series_mock([False, False, False])}
        ev_any2 = self._evaluator(logic="any", indicators=(rsi_cfg, pc_cfg),
                                  series_map=all_false)
        self.assertFalse(ev_any2.should_enter([1.0, 2.0, 3.0], "long").should_enter)

    def test_confirm_bars_requires_consecutive_holds(self):
        """稽核 1.10：抗插針——僅成立 2 根不得觸發，連續 3 根才觸發。"""
        cfg = RsiConfig()
        two_bars = _series_mock([False, False, True, True])
        ev3 = self._evaluator(confirm_bars=3, indicators=(cfg,),
                              series_map={RsiConfig: two_bars})
        verdict = ev3.should_enter([1.0, 2.0, 3.0, 4.0], "long")
        self.assertFalse(verdict.should_enter)
        self.assertEqual(verdict.consecutive_true_bars, 2)

        three_bars = _series_mock([False, True, True, True])
        ev3b = self._evaluator(confirm_bars=3, indicators=(cfg,),
                               series_map={RsiConfig: three_bars})
        self.assertTrue(ev3b.should_enter([1.0, 2.0, 3.0, 4.0], "long").should_enter)

        # 毛刺型（間斷成立）也不得觸發
        spiky = _series_mock([True, False, True, True])
        ev3c = self._evaluator(confirm_bars=3, indicators=(cfg,),
                               series_map={RsiConfig: spiky})
        self.assertFalse(ev3c.should_enter([1.0, 2.0, 3.0, 4.0], "long").should_enter)

    def test_confirm_bars_one_accepts_single_bar(self):
        cfg = RsiConfig()
        one_bar = _series_mock([False, True])
        ev1 = self._evaluator(confirm_bars=1, indicators=(cfg,),
                              series_map={RsiConfig: one_bar})
        self.assertTrue(ev1.should_enter([1.0, 2.0], "long").should_enter)

    def test_indicator_mode_empty_list_raises(self):
        """mode=indicator 但指標為空：縱深防禦（設定層已攔截）。"""
        entry = EntryConfig.__new__(EntryConfig)
        entry.mode = EntryMode.INDICATOR
        entry.logic = "all"
        entry.confirm_bars = 1
        entry.indicators = ()
        ev = EntryEvaluator(entry)
        with self.assertRaises(ValueError):
            ev.should_enter([1.0, 2.0, 3.0], "long")

    def test_unclosed_tail_ignored_by_evaluator(self):
        """未收線尾棒不參與判定（運算約定 1）。"""
        cfg = RsiConfig()
        # 若尾棒被算進去，序列末端為 True；剔除後只有前 3 根全 False+True...
        series_map = {RsiConfig: lambda closes, c, d: [True if x > 3 else False
                                                        for x in closes]}
        ev = self._evaluator(confirm_bars=1, indicators=(cfg,), series_map=series_map)
        # 尾棒（4.0）未收線 → 評估序列應為 [1, 2] → 全 False
        verdict = ev.should_enter([1.0, 2.0, 4.0], "long", last_bar_closed=False)
        self.assertFalse(verdict.should_enter)
        # 全部收線時末端 True → 觸發
        verdict2 = ev.should_enter([1.0, 2.0, 4.0], "long", last_bar_closed=True)
        self.assertTrue(verdict2.should_enter)


if __name__ == "__main__":
    unittest.main()
