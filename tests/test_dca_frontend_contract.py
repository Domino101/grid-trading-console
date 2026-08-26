"""Phase 5：DCA 前端純函式的 Python 鏡像契約。

比照 `test_frontend_format_contract.py` 的做法：在 Python 重現
`web_ui/frontend/src/utils/dca.ts` 的規則並斷言一致，防止前後端/前後
測試各自漂移。純函式規則（進度、距離、ROI、等待時長、狀態文案、
armed 顯示規則）必須與 `web_ui/frontend/tests/dca.test.ts` 完全對應。
"""

import unittest

# ── utils/dca.ts 的 Python 鏡像 ────────────────────────────────

DCA_STATUS_LABELS = {
    "armed": "監控中",
    "triggering": "進場中",
    "averaging": "攤平中",
    "completed": "本輪完成",
    "cooling": "冷卻中",
    "stopping": "停止中",
    "stopped": "已停止",
    "failed": "失敗",
    "degraded": "降級",
    "starting": "啟動中",
}

# 與後端 routes/dca.py 的 _RUNNING_STATUSES 對齊（另含命令 pending 期的 starting）
DCA_RUNNING_STATUSES = frozenset({
    "running", "armed", "triggering", "averaging",
    "completed", "cooling", "degraded", "starting",
})


def dca_status_text(status):
    return DCA_STATUS_LABELS.get(status, status or "未知")


def is_dca_running_status(status):
    return isinstance(status, str) and status in DCA_RUNNING_STATUSES


def is_dca_armed(status):
    return status == "armed"


def dca_ladder_progress(filled, max_orders):
    """(filled, max) → (filled, max, percent)；未知一律 None（G-5）。"""
    percent = None
    if filled is not None and max_orders not in (None, 0) and max_orders > 0:
        percent = min(100, round((filled / max_orders) * 1000) / 10)
    return filled, max_orders, percent


def distance_percent(value_from, value_to):
    if value_from is None or value_to is None or value_from == 0:
        return None
    return (value_to - value_from) / value_from * 100


def distance_text(percent):
    if percent is None:
        return "—"
    return f"{percent:+.2f}%"


def dca_roi_percent(total_cost, unrealized):
    if total_cost is None or unrealized is None or total_cost == 0:
        return None
    return unrealized / total_cost * 100


def format_wait_seconds(total_seconds):
    if total_seconds != total_seconds or total_seconds < 0:  # NaN 或負值
        return "—"
    total = int(total_seconds)
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    if h > 0:
        return f"{h}h {m}m"
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


def armed_wait_text(armed_since, now_ms):
    if armed_since is None:
        return None
    return f"已等待 {format_wait_seconds((now_ms - armed_since * 1000) / 1000)}"


class DcaFrontendContractTests(unittest.TestCase):
    def test_all_state_machine_states_have_distinct_labels(self):
        core = ["armed", "triggering", "averaging", "completed",
                "cooling", "stopping", "stopped", "failed"]
        labels = [DCA_STATUS_LABELS[s] for s in core]
        self.assertEqual(len(set(labels)), len(core))
        self.assertEqual(dca_status_text("armed"), "監控中")
        self.assertEqual(dca_status_text("unknown_x"), "unknown_x")
        self.assertEqual(dca_status_text(None), "未知")

    def test_running_statuses_match_backend(self):
        for status in ("running", "armed", "triggering", "averaging",
                       "completed", "cooling", "degraded", "starting"):
            self.assertTrue(is_dca_running_status(status), status)
        for status in ("stopped", "stopping", "failed", None, ""):
            self.assertFalse(is_dca_running_status(status), repr(status))

    def test_armed_is_unique_status_hiding_orders(self):
        self.assertTrue(is_dca_armed("armed"))
        for status in ("triggering", "averaging", "completed", None):
            self.assertFalse(is_dca_armed(status), status)

    def test_ladder_progress_null_safe_and_capped(self):
        self.assertEqual(dca_ladder_progress(None, 20), (None, 20, None))
        self.assertEqual(dca_ladder_progress(3, 20), (3, 20, 15.0))
        self.assertEqual(dca_ladder_progress(9, 2)[2], 100)
        self.assertEqual(dca_ladder_progress(3, None)[2], None)

    def test_distance_signed_and_null_safe(self):
        self.assertIsNone(distance_percent(None, 2500))
        self.assertIsNone(distance_percent(2500, None))
        self.assertIsNone(distance_percent(0, 2500))
        self.assertAlmostEqual(distance_percent(2500, 2480), -0.8, places=9)
        self.assertAlmostEqual(distance_percent(2500, 2520), 0.8, places=9)
        self.assertEqual(distance_text(None), "—")
        self.assertEqual(distance_text(0.8), "+0.80%")
        self.assertEqual(distance_text(-0.8), "-0.80%")

    def test_roi_null_safe_no_zero_substitution(self):
        self.assertIsNone(dca_roi_percent(None, 1.0))
        self.assertIsNone(dca_roi_percent(0, 1.0))
        self.assertAlmostEqual(dca_roi_percent(4960, 49.6), 1.0, places=9)

    def test_wait_time_buckets(self):
        self.assertEqual(format_wait_seconds(5), "5s")
        self.assertEqual(format_wait_seconds(95), "1m 35s")
        self.assertEqual(format_wait_seconds(3723), "1h 2m")
        self.assertEqual(format_wait_seconds(-1), "—")

    def test_armed_wait_text(self):
        self.assertIsNone(armed_wait_text(None, 1_750_000_000_000))
        self.assertEqual(
            armed_wait_text(1_750_000_000 - 125, 1_750_000_000_000),
            "已等待 2m 5s",
        )


# ── Phase 6：建立 DCA 精靈鏡像 ────────────────────────────────

import os

_FRONTEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "web_ui", "frontend", "src")
_TESTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "web_ui", "frontend", "tests")


def _read(relpath):
    with open(os.path.join(_FRONTEND, relpath), encoding="utf-8") as f:
        return f.read()


# §4.3 凍結預設值（與 utils/dca.ts 的 DCA_*_DEFAULTS 對應，稽核 6.8）
RSI_DEFAULTS = {"period": 14, "oversold": 30, "overbought": 70, "condition": "level"}
MACD_DEFAULTS = {"fast": 12, "slow": 26, "signal": 9, "condition": "cross"}
BOLLINGER_DEFAULTS = {"period": 20, "std_dev": 2.0, "condition": "touch"}
PRICE_CHANGE_DEFAULTS = {"lookback_bars": 24, "long_threshold_percent": -3.0, "short_threshold_percent": 3.0}
ENTRY_DEFAULTS = {"mode": "immediate", "logic": "all", "timeframe": "15m",
                  "fetch_limit": 300, "price_source": "close", "confirm_bars": 1}


def _in_range(value, lo, hi):
    return value is not None and value == value and lo <= value <= hi  # 第二個 value==value 檢查 NaN


def can_advance_dca_step(step, form, live_confirmation):
    """Python 鏡像：utils/dca.ts canAdvanceDcaStep（稽核 6.13）。"""
    if step == 1:
        return bool(form.get("symbol", "").strip()) and bool(form.get("exchange")) and form.get("marketInfoOk")
    if step == 2:
        entry = form["entry"]
        if not _in_range(entry.get("fetch_limit"), 100, 500):
            return False
        if not _in_range(entry.get("confirm_bars"), 1, 5):
            return False
        if not str(entry.get("timeframe", "")).strip():
            return False
        if entry.get("mode") == "indicator":
            if not any(v.get("enabled") for v in entry.get("indicators", {}).values()):
                return False
            rsi, macd = entry["indicators"]["rsi"], entry["indicators"]["macd"]
            bb, pc = entry["indicators"]["bollinger"], entry["indicators"]["price_change"]
            if not _in_range(rsi["period"], 2, 100) or not _in_range(rsi["oversold"], 1, 50):
                return False
            if not _in_range(rsi["overbought"], 50, 99):
                return False
            if not (_in_range(macd["fast"], 2, 100) and _in_range(macd["slow"], 3, 200)
                    and _in_range(macd["signal"], 2, 50)):
                return False
            if _in_range(macd["fast"], -9e9, 9e9) and _in_range(macd["slow"], -9e9, 9e9) and macd["slow"] <= macd["fast"]:
                return False
            if not _in_range(bb["period"], 2, 200) or not _in_range(bb["std_dev"], 0.5, 5.0):
                return False
            if not _in_range(pc["lookback_bars"], 1, 500):
                return False
            if not (_in_range(pc["long_threshold_percent"], -100, 100)
                    and _in_range(pc["short_threshold_percent"], -100, 100)):
                return False
        return True
    if step == 3:
        f = form
        if not _in_range(f.get("base_order_amount"), 0, 9e15):
            return False  # 須 > 0
        if f.get("base_order_amount") is not None and f["base_order_amount"] <= 0:
            return False
        n = f.get("max_safety_orders")
        if not (isinstance(n, int) and 1 <= n <= 50):
            return False
        if not _in_range(f.get("price_deviation_percent"), 0, 9e9) or f["price_deviation_percent"] <= 0:
            return False
        if not _in_range(f.get("take_profit_percent"), 0, 9e9) or f["take_profit_percent"] <= 0:
            return False
        sl = f.get("stop_loss_percent")
        if sl is not None and (not _in_range(sl, 0, 9e9) or sl <= 0):
            return False
        if not (isinstance(f.get("leverage"), int) and 1 <= f["leverage"] <= 125):
            return False
        mi = f.get("monitoring_interval")
        if not (isinstance(mi, int) and 5 <= mi <= 300):
            return False
        return True
    # step 4
    return form.get("account_mode") != "live" or live_confirmation == "CONFIRM LIVE"


def _js_num(x):
    """模擬 JS 模板字串的數字轉字串：整數不帶小數點（6 而非 6.0）。"""
    r = round(x * 10) / 10
    return str(int(r)) if float(r).is_integer() else str(r)


def lookback_time(lookback_bars, timeframe):
    """Python 鏡像：utils/dca.ts formatLookbackTime（稽核 6.8c）。"""
    import re
    if not (isinstance(lookback_bars, (int, float)) and lookback_bars == lookback_bars and lookback_bars > 0):
        return None
    m = re.fullmatch(r"(\d+)([smhd])", timeframe.strip())
    if not m:
        return None
    minutes = int(m.group(1)) * {"s": 1 / 60, "m": 1, "h": 60, "d": 1440}[m.group(2)]
    total = lookback_bars * minutes
    if total >= 1440:
        return f"{int(lookback_bars)} 根 × {timeframe} = {_js_num(total / 1440)} 天"
    if total >= 60:
        return f"{int(lookback_bars)} 根 × {timeframe} = {_js_num(total / 60)} 小時"
    return f"{int(lookback_bars)} 根 × {timeframe} = {_js_num(total)} 分"


def dca_funds_status(required, available):
    """Python 鏡像：utils/dca.ts dcaFundsStatus（稽核 6.15）。"""
    if required is None or available is None:
        return "unknown"
    return "insufficient" if required > available else "ok"


class DcaFormWizardContractTests(unittest.TestCase):
    """Phase 6 步驟推進規則 + §4.3 預設值的 Python 鏡像（稽核 6.7/6.8/6.13/6.14）。"""

    @staticmethod
    def _form(**overrides):
        base = {
            "symbol": "ETH/USDC:USDC", "exchange": "binance", "direction": "long",
            "account_mode": "paper", "marketInfoOk": True,
            "entry": {**ENTRY_DEFAULTS, "indicators": {
                "rsi": {"enabled": False, **RSI_DEFAULTS},
                "macd": {"enabled": False, **MACD_DEFAULTS},
                "bollinger": {"enabled": False, **BOLLINGER_DEFAULTS},
                "price_change": {"enabled": False, **PRICE_CHANGE_DEFAULTS},
            }},
            "base_order_amount": 20, "max_safety_orders": 20,
            "price_deviation_percent": 0.8, "order_size_multiplier": 1.0,
            "price_deviation_multiplier": 1.2, "take_profit_percent": 1.0,
            "stop_loss_percent": None, "leverage": 10, "monitoring_interval": 15,
        }
        base.update(overrides)
        return base

    def test_step1_requires_confirmed_market_info(self):
        self.assertTrue(can_advance_dca_step(1, self._form(), ""))
        self.assertFalse(can_advance_dca_step(1, self._form(symbol=""), ""))
        self.assertFalse(can_advance_dca_step(1, self._form(exchange=""), ""))
        self.assertFalse(can_advance_dca_step(1, self._form(marketInfoOk=False), ""))

    def test_step2_indicator_needs_enabled_indicator(self):
        self.assertTrue(can_advance_dca_step(2, self._form(), ""))  # immediate
        entry = dict(self._form()["entry"])
        entry = {**entry, "mode": "indicator"}
        self.assertFalse(can_advance_dca_step(2, self._form(entry=entry), ""))
        rsi = {**RSI_DEFAULTS, "enabled": True}
        entry = {**entry, "indicators": {**entry["indicators"], "rsi": rsi}}
        self.assertTrue(can_advance_dca_step(2, self._form(entry=entry), ""))

    def test_step2_rejects_out_of_range_params(self):
        entry = {**self._form()["entry"], "mode": "indicator",
                 "indicators": {**self._form()["entry"]["indicators"],
                                "rsi": {**RSI_DEFAULTS, "enabled": True, "oversold": 80}}}
        self.assertFalse(can_advance_dca_step(2, self._form(entry=entry), ""))
        entry = {**self._form()["entry"], "mode": "indicator",
                 "indicators": {**self._form()["entry"]["indicators"],
                                "macd": {**MACD_DEFAULTS, "enabled": True, "fast": 30, "slow": 20}}}
        self.assertFalse(can_advance_dca_step(2, self._form(entry=entry), ""))

    def test_step3_core_params(self):
        self.assertTrue(can_advance_dca_step(3, self._form(), ""))
        self.assertFalse(can_advance_dca_step(3, self._form(base_order_amount=0), ""))
        self.assertFalse(can_advance_dca_step(3, self._form(max_safety_orders=0), ""))
        self.assertFalse(can_advance_dca_step(3, self._form(price_deviation_percent=0), ""))
        self.assertFalse(can_advance_dca_step(3, self._form(take_profit_percent=0), ""))
        self.assertFalse(can_advance_dca_step(3, self._form(stop_loss_percent=-1), ""))

    def test_step4_live_confirmation(self):
        self.assertTrue(can_advance_dca_step(4, self._form(), ""))  # paper
        live = self._form(account_mode="live")
        self.assertFalse(can_advance_dca_step(4, live, ""))
        self.assertFalse(can_advance_dca_step(4, live, "CONFIRM"))
        self.assertTrue(can_advance_dca_step(4, live, "CONFIRM LIVE"))

    def test_frozen_defaults_match_plan_43(self):
        self.assertEqual(RSI_DEFAULTS, {"period": 14, "oversold": 30, "overbought": 70, "condition": "level"})
        self.assertEqual(MACD_DEFAULTS, {"fast": 12, "slow": 26, "signal": 9, "condition": "cross"})
        self.assertEqual(BOLLINGER_DEFAULTS, {"period": 20, "std_dev": 2.0, "condition": "touch"})
        self.assertEqual(PRICE_CHANGE_DEFAULTS,
                         {"lookback_bars": 24, "long_threshold_percent": -3.0, "short_threshold_percent": 3.0})
        self.assertEqual(ENTRY_DEFAULTS["timeframe"], "15m")
        self.assertEqual(ENTRY_DEFAULTS["fetch_limit"], 300)
        self.assertEqual(ENTRY_DEFAULTS["price_source"], "close")
        self.assertEqual(ENTRY_DEFAULTS["logic"], "all")
        self.assertEqual(ENTRY_DEFAULTS["confirm_bars"], 1)

    def test_lookback_time_conversion(self):
        self.assertEqual(lookback_time(24, "15m"), "24 根 × 15m = 6 小時")
        self.assertEqual(lookback_time(48, "1h"), "48 根 × 1h = 2 天")
        self.assertEqual(lookback_time(1, "1h"), "1 根 × 1h = 1 小時")
        self.assertEqual(lookback_time(30, "1m"), "30 根 × 1m = 30 分")
        self.assertIsNone(lookback_time(24, "7w"))
        self.assertIsNone(lookback_time(0, "15m"))

    def test_funds_status_null_is_unknown(self):
        self.assertEqual(dca_funds_status(420, None), "unknown")
        self.assertEqual(dca_funds_status(None, 1000), "unknown")
        self.assertEqual(dca_funds_status(420, 1000), "ok")
        self.assertEqual(dca_funds_status(420, 400), "insufficient")
        self.assertEqual(dca_funds_status(420, 420), "ok")


class DcaWizardSourceContractTests(unittest.TestCase):
    """Phase 6 檔案級契約（稽核 6.2/6.4/6.18 + 分頁隔離延伸）。"""

    def test_ladder_preview_does_no_math(self):
        """6.2：DcaLadderPreview.tsx 計算路徑零命中（不自算階梯）。"""
        src = _read(os.path.join("components", "DcaLadderPreview.tsx"))
        self.assertNotIn("price_deviation_multiplier", src)
        self.assertNotIn("Math.pow", src)
        self.assertNotIn("**", src)

    def test_ladder_preview_reads_preview_response_fields(self):
        """6.2/6.3：Summary 數值取自 /preview 回應欄位。"""
        src = _read(os.path.join("components", "DcaLadderPreview.tsx"))
        for field in ("required_capital", "max_drawdown_percent", "final_take_profit_price",
                      "final_average_price", "deviation_percent"):
            self.assertIn(field, src)

    def test_wizard_form_components_isolated_from_grid_endpoints(self):
        """分頁隔離（5.2 延伸）：精靈元件不呼叫 grid/dashboard 端點。"""
        for name in ("DcaForm.tsx", "DcaLadderPreview.tsx", "IndicatorConfigPanel.tsx",
                     "DcaDashboard.tsx", "DcaCard.tsx", "DcaTable.tsx"):
            src = _read(os.path.join("components", name))
            self.assertNotIn("/api/v1/grids", src, name)
            self.assertNotIn("/api/v1/dashboard", src, name)
            self.assertNotIn("getGridSummary", src, name)
            self.assertNotIn("getDashboardSnapshot", src, name)

    def test_wizard_wires_preview_and_create_endpoints(self):
        """精靈使用 /dca/preview 與 /dca（建立），且 preview 請求可取消（6.16 基礎）。"""
        form_src = _read(os.path.join("components", "DcaForm.tsx"))
        self.assertIn("previewDca", form_src)
        self.assertIn("createDca", form_src)
        self.assertIn("AbortController", form_src)
        self.assertIn("buildDcaPreviewRequest", form_src)
        api_src = _read(os.path.join("services", "api.ts"))
        self.assertIn("async previewDca(request: DcaPreviewRequest, signal?: AbortSignal)", api_src)
        self.assertIn("async createDca(request: DcaCreateRequest)", api_src)

    def test_wizard_form_step_rules_pure_function_exported(self):
        """6.13：步驟推進規則為 utils/dca.ts 的純函式且有 TS 測試。"""
        dca_src = _read(os.path.join("utils", "dca.ts"))
        self.assertIn("export const canAdvanceDcaStep", dca_src)
        ts_test = os.path.join(_TESTS, "dca-form.test.ts")
        self.assertTrue(os.path.exists(ts_test))
        with open(ts_test, encoding="utf-8") as f:
            self.assertIn("canAdvanceDcaStep", f.read())

    def test_grid_form_untouched_by_phase6(self):
        """6.18：網格精靈零回歸（Phase 6 不修改 GridForm.tsx）。"""
        src = _read(os.path.join("components", "GridForm.tsx"))
        self.assertIn("canAdvanceCreateStep", src)
        self.assertNotIn("DcaForm", src)


if __name__ == "__main__":
    unittest.main()
