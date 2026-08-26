"""
DCA 階梯數學測試（Phase 1 稽核 1.1 / 1.2 / 1.3 / 1.4 / 1.14）

鎖定測試向量與凍結公式：docs/dca_bot_implementation_plan_zh-TW.md §4.5
獨立參考實作：scripts/verify_dca_vectors.py（兩者必須一致）
"""

import inspect
import unittest
from decimal import Decimal as D

from core.services.dca import DcaConfig, build_ladder, required_capital
from core.services.dca.ladder import (
    DECIMAL_PRECISION,
    quantize_price,
    quantize_quantity,
    stop_loss_price,
    take_profit_price,
)

TOL = D("1e-6")
TOL_MIRROR = D("1e-9")


def _config_vector_a() -> DcaConfig:
    """向量 A：E=2500, b=0.8%, m=1.2, s=1.0, N=20, V₀=20 USD, order + fixed_value"""
    return DcaConfig(
        exchange="binance",
        symbol="ETH/USDC:USDC",
        base_order_amount=D("20"),
        max_safety_orders=20,
        price_deviation_percent=D("0.8"),
        price_deviation_multiplier=D("1.2"),
        order_size_multiplier=D("1.0"),
    )


def _assert_close(testcase: unittest.TestCase, got: D, want: str):
    testcase.assertLess(
        abs(got - D(want)), TOL, f"期望 {want}，實得 {got}（容差 {TOL}）"
    )


class LadderVectorTests(unittest.TestCase):
    """稽核 1.1 / 1.2：鎖定向量 A/B 九個 + 四個值，容差 1e-6"""

    def test_vector_a_all_nine_values(self):
        plan = build_ladder(_config_vector_a(), D("2500"), "long")
        levels = {lv.index: lv for lv in plan.levels}
        self.assertEqual(len(plan.levels), 21)  # 基礎單 + 20 張安全單

        _assert_close(self, levels[1].deviation_percent, "0.800000")
        _assert_close(self, levels[20].deviation_percent, "25.558400")
        _assert_close(self, levels[1].price, "2480.000000")
        _assert_close(self, levels[5].price, "2354.614306")
        _assert_close(self, levels[20].price, "496.706826")
        _assert_close(self, levels[20].avg_price, "1468.987729")
        _assert_close(self, take_profit_price(levels[20].avg_price, D("1.0"), "long"),
                      "1483.677606")
        _assert_close(self, plan.required_capital, "420.00")
        _assert_close(self, plan.max_drawdown_percent, "80.131727")

    def test_vector_b_all_four_values(self):
        """向量 B：E=2500, b=1.0%, m=1.0, s=1.5, N=5, V₀=20 USD"""
        cfg = DcaConfig(
            exchange="binance", symbol="ETH/USDC:USDC",
            base_order_amount=D("20"), max_safety_orders=5,
            price_deviation_percent=D("1.0"),
            price_deviation_multiplier=D("1.0"),
            order_size_multiplier=D("1.5"),
        )
        plan = build_ladder(cfg, D("2500"), "long")
        l5 = plan.levels[5]
        _assert_close(self, l5.price, "2377.475125")
        _assert_close(self, l5.avg_price, "2411.443413")
        _assert_close(self, plan.required_capital, "415.625")
        _assert_close(self, plan.max_drawdown_percent, "4.900995")

    def test_short_ladder_is_step_mirror_of_long(self):
        """稽核 1.4：做空階梯相同參數下，每層步進偏移與做多互為鏡像（1e-9）。"""
        cfg = _config_vector_a()
        plan_l = build_ladder(cfg, D("2500"), "long")
        plan_s = build_ladder(cfg, D("2500"), "short")

        # 首層絕對價為精確鏡像因子（1 ∓ dev₁）
        _assert_close(self, plan_s.levels[1].price, "2520.000000")
        for i in range(1, 21):
            pl, ps = plan_l.levels[i], plan_s.levels[i]
            prev_l = plan_l.levels[i - 1].price
            prev_s = plan_s.levels[i - 1].price
            with self.subTest(level=i):
                # 每層步進偏移量：做空 == −做多（凍結公式 (1 ∓ dev_i) 的直接推論）
                step_l = (pl.price - prev_l) / prev_l
                step_s = (ps.price - prev_s) / prev_s
                self.assertLess(abs(step_l + step_s), TOL_MIRROR,
                                f"第 {i} 層步進偏移未鏡像: long {step_l} vs short {step_s}")
                # 每層標稱偏移百分比完全相等
                self.assertEqual(pl.deviation_percent, ps.deviation_percent)
                # 做空每層價格高於做多（方向相反）
                self.assertGreater(ps.price, pl.price)

    def test_take_profit_directions_and_stop_loss(self):
        avg = D("1000")
        _assert_close(self, take_profit_price(avg, D("1.0"), "long"), "1010")
        _assert_close(self, take_profit_price(avg, D("1.0"), "short"), "990")
        _assert_close(self, stop_loss_price(avg, D("5"), "long"), "950")
        _assert_close(self, stop_loss_price(avg, D("5"), "short"), "1050")
        with self.assertRaises(ValueError):
            take_profit_price(D("0"), D("1.0"), "long")  # 未知均價不得回傳 0（G-5）
        with self.assertRaises(ValueError):
            take_profit_price(avg, D("1.0"), "sideways")


class LadderCombosTests(unittest.TestCase):
    """稽核 1.3：四種 dca_mode × averaging_method 組合皆有測試"""

    def _build(self, mode: str, method: str):
        cfg = DcaConfig(
            exchange="binance", symbol="ETH/USDC:USDC",
            base_order_amount=D("20"), max_safety_orders=6,
            dca_mode=mode, averaging_method=method,
            price_deviation_percent=D("1.0"),
            price_deviation_multiplier=D("1.1"),
            order_size_multiplier=D("1.3"),
        )
        return build_ladder(cfg, D("2500"), "long")

    def _cumulative_monotonic_and_required(self, plan):
        cumulative = D("0")
        for lv in plan.levels:
            self.assertGreater(lv.price, D("0"))
            self.assertGreater(lv.quantity, D("0"))
            cumulative += lv.notional
            # 累計名目為單調不減（遞增）
            self.assertLessEqual(cumulative, plan.required_capital)
        self.assertEqual(cumulative, plan.required_capital)

    def test_order_fixed_value(self):
        plan = self._build("order", "fixed_value")
        # fixed_value：V_i = V₀ × s^i → 第 1 層名目 = 26
        _assert_close(self, plan.levels[1].notional, "26.0")
        _assert_close(self, plan.levels[1].price, "2475.0")  # 2500 × 0.99
        self._cumulative_monotonic_and_required(plan)

    def test_order_fixed_coin(self):
        plan = self._build("order", "fixed_coin")
        # fixed_coin：Q_i = (V₀/E) × s^i → Q₁ = 0.008 × 1.3 = 0.0104
        _assert_close(self, plan.levels[1].quantity, "0.0104")
        self._cumulative_monotonic_and_required(plan)

    def test_position_fixed_value(self):
        plan = self._build("position", "fixed_value")
        # position mode：以攤平後均價為錨，第 1 層與 order mode 相同（avg₀ = E）
        _assert_close(self, plan.levels[1].price, "2475.0")
        self._cumulative_monotonic_and_required(plan)

    def test_position_fixed_coin(self):
        plan = self._build("position", "fixed_coin")
        _assert_close(self, plan.levels[1].quantity, "0.0104")
        self._cumulative_monotonic_and_required(plan)

    def test_position_mode_diverges_from_order_mode_at_deeper_levels(self):
        """position 與 order 模式在深層應產生不同價格（錨點不同）。"""
        plan_o = self._build("order", "fixed_value")
        plan_p = self._build("position", "fixed_value")
        self.assertNotEqual(plan_o.levels[6].price, plan_p.levels[6].price)

    def test_amount_unit_base_equals_converted_quote(self):
        """amount_unit=base 時換算後的階梯與等值 quote 完全一致。"""
        cfg_quote = DcaConfig(
            exchange="binance", symbol="ETH/USDC:USDC",
            base_order_amount=D("20"), max_safety_orders=3, amount_unit="quote",
        )
        cfg_base = DcaConfig(
            exchange="binance", symbol="ETH/USDC:USDC",
            base_order_amount=D("0.008"), max_safety_orders=3, amount_unit="base",
        )
        plan_q = build_ladder(cfg_quote, D("2500"), "long")
        plan_b = build_ladder(cfg_base, D("2500"), "long")
        for lq, lb in zip(plan_q.levels, plan_b.levels):
            self.assertEqual(lq.price, lb.price)
            self.assertEqual(lq.notional, lb.notional)
            self.assertEqual(lq.quantity, lb.quantity)

    def test_both_direction_required_is_max_of_two_legs(self):
        """決策 D-2：資金試算為 max(long, short) 而非相加。"""
        cfg = DcaConfig(
            exchange="binance", symbol="ETH/USDC:USDC",
            base_order_amount=D("20"), max_safety_orders=6, direction="both",
        )
        plan_l = build_ladder(cfg, D("2500"), "long")
        plan_s = build_ladder(cfg, D("2500"), "short")
        expected = max(plan_l.required_capital, plan_s.required_capital)
        self.assertEqual(required_capital(cfg, D("2500")), expected)
        # 分別計算時不為兩者相加
        self.assertNotEqual(
            required_capital(cfg, D("2500")),
            plan_l.required_capital + plan_s.required_capital,
        )


class LadderPrecisionTests(unittest.TestCase):
    """稽核 1.14：Decimal 純度 + 量化規則"""

    def test_all_money_fields_are_decimal(self):
        plan = build_ladder(_config_vector_a(), D("2500"), "long")
        for lv in plan.levels:
            with self.subTest(level=lv.index):
                for value in (lv.price, lv.notional, lv.quantity,
                              lv.avg_price, lv.deviation_percent):
                    self.assertIsInstance(value, D)
        self.assertIsInstance(plan.required_capital, D)
        self.assertIsInstance(plan.max_drawdown_percent, D)

    def test_no_float_calls_in_ladder_source(self):
        """計算路徑不得出現 float 呼叫（binary float 會在 1e-6 容差前分岔）。"""
        import core.services.dca.ladder as ladder_mod
        source = inspect.getsource(ladder_mod)
        self.assertNotIn("float(", source)

    def test_precision_context_is_at_least_28(self):
        self.assertGreaterEqual(DECIMAL_PRECISION, 28)

    def test_quantize_price_half_even(self):
        # ROUND_HALF_EVEN：0.005 → 偶數側
        self.assertEqual(quantize_price(D("2480.005"), 2), D("2480.00"))
        self.assertEqual(quantize_price(D("2480.015"), 2), D("2480.02"))
        self.assertEqual(quantize_price(D("2480.0049"), 2), D("2480.00"))
        # 未設定精度時原樣回傳
        self.assertEqual(quantize_price(D("2480.123456"), None), D("2480.123456"))

    def test_quantize_quantity_round_down(self):
        # ROUND_DOWN：絕不向上增購數量
        self.assertEqual(quantize_quantity(D("1.23459"), 4), D("1.2345"))
        self.assertEqual(quantize_quantity(D("0.009999"), 4), D("0.0099"))
        self.assertEqual(quantize_quantity(D("1.23459"), None), D("1.23459"))

    def test_negative_entry_price_rejected(self):
        with self.assertRaises(ValueError):
            build_ladder(_config_vector_a(), D("-1"), "long")

    def test_both_direction_requires_explicit_leg(self):
        cfg = DcaConfig(
            exchange="binance", symbol="ETH/USDC:USDC",
            base_order_amount=D("20"), direction="both",
        )
        with self.assertRaises(ValueError):
            build_ladder(cfg, D("2500"))  # 未指定方向


if __name__ == "__main__":
    unittest.main()
