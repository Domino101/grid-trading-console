"""Phase 5：DCA 前端純函式的 Python 鏡像契約。

比照 `test_frontend_format_contract.py` 的做法：在 Python 重現
`web_ui/frontend/src/utils/dca.ts` 的規則並斷言一致，防止前後端/前後
測試各自漂移。純函式規則（進度、距離、ROI、等待時長、狀態文案、
armed 顯示規則）必須與 `web_ui/frontend/tests/dca.test.ts` 完全對應。
"""

import re
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


_COMMENT_RE = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)


def _read_code(relpath):
    """讀取原始碼並移除註解，讓 grep 契約只看真正會執行的程式碼。

    必要性：JSDoc 的 `/**` 與說明文字中的 `localhost` 會讓樸素 grep 產生
    偽陽性；契約應該檢查行為而非文件措辭。
    """
    return _COMMENT_RE.sub("", _read(relpath))


def _read_css():
    """Phase C6：讀取設計系統樣式表（圖表面板沿用既有 token）。"""
    with open(os.path.join(_FRONTEND, "App.css"), encoding="utf-8") as f:
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


class DcaDetailSourceContractTests(unittest.TestCase):
    """Phase 7 檔案級契約（詳情工作台 + 分頁隔離延伸）。"""

    def test_detail_has_all_six_tabs(self):
        """7 六分頁：階梯 / 訂單 / 成交 / 事件 / 指標 / 設定。"""
        src = _read(os.path.join("components", "DcaDetail.tsx"))
        for tab in ("'ladder'", "'orders'", "'fills'", "'events'",
                    "'indicators'", "'specs'"):
            self.assertIn(tab, src, tab)

    def test_detail_wires_phase7_endpoints(self):
        """詳情頁使用 Phase 7 新端點（stats/ladder/orders/fills/events）。"""
        src = _read(os.path.join("components", "DcaDetail.tsx"))
        for method in ("getDcaStats", "getDcaLadder", "getDcaOrders",
                       "getDcaFills", "listDcaEvents"):
            self.assertIn(method, src, method)
        api_src = _read(os.path.join("services", "api.ts"))
        for sig in ("async getDcaStats(", "async getDcaLadder(",
                    "async getDcaOrders(", "async getDcaFills(",
                    "async listDcaEvents("):
            self.assertIn(sig, api_src, sig)

    def test_detail_isolated_from_grid_endpoints(self):
        """分頁隔離（5.2 延伸）：詳情元件不呼叫 grid/dashboard 端點。"""
        for name in ("DcaDetail.tsx", "DcaLadderTable.tsx"):
            src = _read(os.path.join("components", name))
            self.assertNotIn("/api/v1/grids", src, name)
            self.assertNotIn("getGridSummary", src, name)
            self.assertNotIn("getDashboardSnapshot", src, name)

    def test_ladder_table_does_no_ladder_math(self):
        """G-4：階梯表不自算階梯數學（不出現 multiplier / 冪運算）。"""
        src = _read(os.path.join("components", "DcaLadderTable.tsx"))
        self.assertNotIn("price_deviation_multiplier", src)
        self.assertNotIn("Math.pow", src)
        self.assertNotIn("**", src)

    def test_detail_armed_shows_zero_orders_semantics(self):
        """7.1：armed 詳情頁明示監控中且訂單分頁為空。"""
        src = _read(os.path.join("components", "DcaDetail.tsx"))
        self.assertIn("isDcaArmed", src)
        self.assertIn("零掛單", src)


class DcaChartSourceContractTests(unittest.TestCase):
    """Phase C6 檔案級契約（圖表基礎：依賴隔離 / 零計算 / 記憶體 / 輪詢）。"""

    _LIB = "lightweight-charts"

    def test_c6_1_chart_tab_registered_in_detail(self):
        """C6：詳情頁新增第七個分頁 'chart'，且六個既有分頁未被移除。"""
        src = _read(os.path.join("components", "DcaDetail.tsx"))
        for tab in ("'chart'", "'ladder'", "'orders'", "'fills'",
                    "'events'", "'indicators'", "'specs'"):
            self.assertIn(tab, src, tab)

    def test_c6_2_chart_wires_new_endpoints(self):
        """C5 端點接線：getDcaChart / getKlines 存在於 api.ts，詳情頁使用 getDcaChart。"""
        api_src = _read_code(os.path.join("services", "api.ts"))
        for sig in ("async getDcaChart(", "async getKlines("):
            self.assertIn(sig, api_src, sig)
        # 走同源相對路徑（禁止 localhost / 127.0.0.1）
        self.assertIn("/v1/dca/${encodeURIComponent(botId)}/chart", api_src)
        self.assertIn("/v1/market/klines/", api_src)
        self.assertNotIn("localhost", api_src)
        self.assertNotIn("127.0.0.1", api_src)

        detail_src = _read(os.path.join("components", "DcaDetail.tsx"))
        self.assertIn("getDcaChart", detail_src)

    def test_c6_7_chart_library_isolated_to_single_file(self):
        """稽核 6.7 / E-6.2 / G-17：全前端只有 DcaChart.tsx 可觸及圖表函式庫。"""
        hits = []
        for dirpath, _dirnames, filenames in os.walk(_FRONTEND):
            for name in filenames:
                if not name.endswith((".ts", ".tsx")):
                    continue
                path = os.path.join(dirpath, name)
                with open(path, "r", encoding="utf-8") as handle:
                    if self._LIB in handle.read():
                        hits.append(os.path.relpath(path, _FRONTEND))
        self.assertEqual(
            sorted(hits), [os.path.join("components", "DcaChart.tsx")],
            f"圖表函式庫外洩到其他檔案: {hits}",
        )

    def test_c6_7_grid_components_untouched_by_c6(self):
        """稽核 6.7 / E-6.1：不得回頭改寫運行中的網格實盤 UI。"""
        grid_src = _read(os.path.join("components", "GridDetail.tsx"))
        self.assertNotIn(self._LIB, grid_src)
        self.assertNotIn("DcaChart", grid_src)
        self.assertIn("recharts", grid_src, "網格頁應維持既有 recharts 實作")

    def test_c6_2_chart_files_do_no_math(self):
        """稽核 6.2 / G-4：圖表元件與純函式不得含階梯 / 均價 / ROI / 止盈價數學。"""
        forbidden = (
            "price_deviation_multiplier",
            "price_deviation_percent",
            "required_capital",
            "take_profit_price",
            "Math.pow",
            "**",
        )
        for rel in (os.path.join("components", "DcaChart.tsx"),
                    os.path.join("utils", "dcaChart.ts")):
            src = _read_code(rel)
            for token in forbidden:
                self.assertNotIn(token, src, f"{rel} 含禁止的計算符號 {token}")

    def test_c6_4_chart_cleanup_removes_chart(self):
        """稽核 6.4 / RC-12：卸載必須 chart.remove()，價格線必須 removePriceLine()。"""
        src = _read(os.path.join("components", "DcaChart.tsx"))
        self.assertIn("chart.remove()", src)
        self.assertIn("removePriceLine(", src)
        self.assertIn("observer.disconnect()", src)

    def test_c6_5_polling_is_scoped_and_cancellable(self):
        """稽核 6.5：切離分頁後無新增請求（clearInterval + AbortController）。"""
        src = _read(os.path.join("components", "DcaDetail.tsx"))
        self.assertIn("CHART_REFRESH_MS", src)
        self.assertIn("window.clearInterval(timer)", src)
        self.assertIn("chartAbortRef.current?.abort()", src)
        self.assertIn("if (activeTab !== 'chart') return;", src)

    def test_c6_5_poll_interval_matches_dashboard_cadence(self):
        """輪詢節奏對齊 DcaDashboard.tsx 的 REFRESH_MS（15 秒）。"""
        detail_src = _read(os.path.join("components", "DcaDetail.tsx"))
        dash_src = _read(os.path.join("components", "DcaDashboard.tsx"))
        self.assertIn("const CHART_REFRESH_MS = 15_000;", detail_src)
        self.assertIn("15_000", dash_src)

    def test_c6_6_armed_semantics_on_chart(self):
        """稽核 6.6：armed → 監控中提示，且 order_placed 標記被過濾為 0。"""
        detail_src = _read(os.path.join("components", "DcaDetail.tsx"))
        self.assertIn("filterMarksForStatus", detail_src)
        util_src = _read(os.path.join("utils", "dcaChart.ts"))
        self.assertIn("order_placed", util_src)
        self.assertIn("filterMarksForStatus", util_src)

    def test_c6_8_and_6_9_honest_data_status(self):
        """稽核 6.8 / 6.9：資料不足要明講，合成資料要角標。"""
        util_src = _read(os.path.join("utils", "dcaChart.ts"))
        self.assertIn("chartDataNotice", util_src)
        self.assertIn("paper_synthetic", util_src)
        self.assertIn("合成資料", util_src)
        self.assertIn("資料不足", util_src)
        detail_src = _read(os.path.join("components", "DcaDetail.tsx"))
        self.assertIn("chartDataNotice", detail_src)
        self.assertIn("chartNotice.unavailable", detail_src)

    def test_c6_10_chart_lazy_loaded_into_its_own_chunk(self):
        """稽核 6.10：DcaChart 必須動態匯入（獨立 chunk），不得灌進 index-*.js。"""
        src = _read(os.path.join("components", "DcaDetail.tsx"))
        self.assertIn("lazy(() => import('./DcaChart'))", src)
        self.assertIn("Suspense", src)

    def test_c6_9_semantics_table_mirrors_plan_4_7(self):
        """稽核 6.1 的 Python 鏡像：kind → shape / position 與 §4.7 凍結表逐行相符。"""
        src = _read(os.path.join("utils", "dcaChart.ts"))
        expected = {
            "base_filled": ("arrowUp", "arrowDown", "belowBar", "aboveBar"),
            "safety_filled": ("circle", "circle", "belowBar", "aboveBar"),
            "take_profit_filled": ("arrowUp", None, "belowBar", None),
            "stop_loss_filled": ("arrowDown", None, "aboveBar", None),
            "order_placed": ("square", None, "inBar", None),
            "skipped": ("cross", None, "inBar", None),
        }
        for kind in expected:
            self.assertIn(f"{kind}: {{", src, kind)
        # 表內出現的每一種形狀都必須是 §4.7 允許值
        for shape in ("arrowUp", "arrowDown", "circle", "square", "cross"):
            self.assertIn(f"'{shape}'", src, shape)

    def test_c6_12_accessibility_hooks_present(self):
        """稽核 6.12：圖表容器有 aria-label；時框切換有 aria-pressed。"""
        chart_src = _read(os.path.join("components", "DcaChart.tsx"))
        self.assertIn("aria-label", chart_src)
        self.assertIn('role="img"', chart_src)
        detail_src = _read(os.path.join("components", "DcaDetail.tsx"))
        self.assertIn("aria-pressed", detail_src)
        self.assertIn('role="tab"', detail_src)

    def test_c6_theme_uses_existing_css_tokens(self):
        """圖表色票一律由既有 token 衍生，不得硬編碼新色票。"""
        css = _read_css()
        for token in ("--dca-long", "--dca-short", "--dca-safety-long",
                      "--dca-take-profit", "--dca-stop-loss",
                      "--dca-order-placed", "--dca-skipped",
                      "--dca-line-entry", "--dca-line-average",
                      "--dca-line-take-profit", "--dca-line-next-safety",
                      "--dca-line-stop-loss"):
            self.assertIn(token, css, token)
        # 每個新 token 都指向既有語意色，不是新的十六進位色碼
        self.assertIn("--dca-long: var(--trading-green);", css)
        self.assertIn("--dca-short: var(--trading-red);", css)


if __name__ == "__main__":
    unittest.main()
