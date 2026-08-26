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


if __name__ == "__main__":
    unittest.main()
