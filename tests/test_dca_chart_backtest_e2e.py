"""
DCA 圖表與回測 端到端驗收測試（Phase C8 稽核 8.1-8.6）

依據：docs/dca_chart_backtest_plan_zh-TW.md §5 Phase C8

本檔跑的是**整條鏈路**，不是單一端點：

    POST /api/v1/dca            建立 PAPER 機器人（真 SafetyGuard + 真 YAML 產生器）
      → GET  /api/v1/market/klines/{symbol}   合成 K 線（離線、確定性）
      → GET  /api/v1/dca/{bot_id}/chart       K 線 + 標記 + 價格線
      → POST /api/v1/dca/backtest             單一設定回放（真 DcaCoordinator）
      → POST /api/v1/dca/backtest/sweep       參數掃描
      → DELETE /api/v1/dca/{bot_id}           清理

與既有 `tests/test_dca_backtest_http_contract.py`（逐端點契約）的分工：
本檔只驗證「串起來會不會壞」與計畫 §5 C8 的六條稽核（8.1-8.6），
不重複逐欄位契約。

離線保證：`account_mode=paper` → `KlineService._handle_paper_mode()` 走
`PaperExchangeAdapter` 的確定性合成器，全程零網路。為了讓「不小心連網」
變成**紅燈而不是慢**，本檔把 `KlineService` 的 ccxt 抓取函式換成會拋例外的
stub（`_ForbiddenFetch`），任何真實抓取都會立刻失敗。
"""

import importlib.util
import json
import os
import re
import subprocess
import sys
import unittest

_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_backend_root = os.path.join(_project_root, "web_ui", "backend")
for _p in (_project_root, _backend_root):
    if _p not in sys.path:
        sys.path.insert(0, _p)

HAS_FASTAPI = importlib.util.find_spec("fastapi") is not None

# auth 在 import 當下就凍結 token；先設環境變數，之後一律回讀 auth 真正在用的值。
os.environ.setdefault("GRID_API_TOKEN", "dca-c8-e2e-token")


def _skip_reason():
    return "FastAPI is installed from requirements.txt in CI/release environments"


class _ForbiddenFetch:
    """任何真實交易所抓取都必須失敗（本檔應該 100% 離線）。"""

    def __init__(self):
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("C8 E2E 不得對交易所發出網路請求（應走 PAPER 合成 K 線）")


@unittest.skipUnless(HAS_FASTAPI, _skip_reason())
class DcaChartBacktestE2ETests(unittest.TestCase):
    """8.1-8.6：PAPER 全鏈路 + 零回歸 + 安全旗標。"""

    BOT_SYMBOL = "ETH/USDC:USDC"
    BOT_EXCHANGE = "binance"

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from web_ui.backend import auth as auth_module
        from web_ui.backend.main import app
        from web_ui.backend import routes as _routes  # noqa: F401  (確保套件已載入)
        from web_ui.backend.kline_service import KlineService

        cls.token = auth_module._API_TOKEN
        cls.client = TestClient(app)
        cls.project_root = _project_root
        cls.active_dca_path = os.path.join(_project_root, "config", "active_dca.json")

        # BP-2 證據 A：整檔開始前的 active_dca.json 位元組
        cls.active_dca_bytes_before = cls._read_active_dca_bytes()

        # 全程離線：任何 ccxt 抓取都變成 AssertionError
        cls.forbidden_fetch = _ForbiddenFetch()
        KlineService.reset_instance()
        cls.kline_service = KlineService.instance()
        cls._original_fetch_fn = cls.kline_service.fetch_fn
        cls.kline_service.fetch_fn = cls.forbidden_fetch

        # 修復可能被其他測試模組污染的模組級狀態（見 test_dca_backtest_http_contract 註解）
        cls._restore_global_state()

        cls.bot_id = None
        cls.created_config_rel = None
        cls.created_configs = []
        cls.write_source_seq = 0

    @classmethod
    def tearDownClass(cls):
        from web_ui.backend.kline_service import KlineService

        cls.kline_service.fetch_fn = cls._original_fetch_fn
        KlineService.reset_instance()

        for rel in cls.created_configs:
            abs_path = os.path.join(cls.project_root, rel)
            if os.path.exists(abs_path):
                os.remove(abs_path)
        # 還原熱插拔登記檔（本檔建立的條目必須消失）
        if cls.active_dca_bytes_before is None:
            if os.path.exists(cls.active_dca_path):
                os.remove(cls.active_dca_path)
        else:
            with open(cls.active_dca_path, "wb") as handle:
                handle.write(cls.active_dca_bytes_before)

    # ── 共用工具 ──

    @classmethod
    def _read_active_dca_bytes(cls):
        if not os.path.exists(cls.active_dca_path):
            return None
        with open(cls.active_dca_path, "rb") as handle:
            return handle.read()

    @classmethod
    def _restore_global_state(cls):
        real_root = cls.project_root
        for name in ("routes.dca", "web_ui.backend.routes.dca"):
            module = sys.modules.get(name)
            if module is not None:
                module._get_project_root = lambda: real_root
        for name in ("auth", "web_ui.backend.auth"):
            module = sys.modules.get(name)
            if module is not None:
                module._API_TOKEN = cls.token

    def setUp(self):
        type(self)._restore_global_state()

    @classmethod
    def _next_write_source(cls):
        """每次寫操作用不同 source。

        SafetyGuard 對「同一 source 60 秒內最多 3 次寫操作」設限，
        本檔的寫操作次數超過該上限；共用 source 會讓後面的測試收到 429
        而不是它真正要驗證的狀態碼（這在 C8 第一次跑時就實際發生了）。
        """
        cls.write_source_seq += 1
        return f"c8-e2e-{cls.write_source_seq}"

    def _headers(self):
        return {"Authorization": f"Bearer {type(self).token}"}

    def _create_payload(self):
        return {
            "symbol": self.BOT_SYMBOL,
            "exchange": self.BOT_EXCHANGE,
            "direction": "long",
            "account_mode": "paper",
            "base_order_amount": 100,
            "max_safety_orders": 5,
            "price_deviation_percent": 1.0,
            "price_deviation_multiplier": 1.5,
            "order_size_multiplier": 1.0,
            "take_profit_percent": 1.0,
            "entry_price": 2500.0,
            "fee_rate": 0.001,
            "leverage": 10,
            "repeat_after_take_profit": True,
            "cooldown_seconds": 60,
            "auto_start": False,
        }

    def _ensure_bot(self):
        """步驟一：透過真實 HTTP 端點建立 PAPER 機器人（僅建立一次）。"""
        cls = type(self)
        if cls.bot_id:
            return cls.bot_id

        response = self.client.post(
            f"/api/v1/dca?source={self._next_write_source()}",
            headers=self._headers(),
            json=self._create_payload(),
        )
        self.assertEqual(response.status_code, 201, response.text[:400])
        body = response.json()
        self.assertIn("bot_id", body, body)
        cls.bot_id = body["bot_id"]
        cls.created_config_rel = body.get("config_path")
        if cls.created_config_rel:
            cls.created_configs.append(cls.created_config_rel)
        return cls.bot_id

    # ── 8.1 PAPER 全鏈路 ──

    def test_8_1_a_create_dca_writes_yaml_and_does_not_auto_start(self):
        bot_id = self._ensure_bot()
        rel = type(self).created_config_rel
        self.assertTrue(rel, "建立回應必須含 config_path")
        self.assertTrue(os.path.exists(os.path.join(self.project_root, rel)), rel)

        with open(self.active_dca_path, "r", encoding="utf-8") as handle:
            active = json.load(handle)
        self.assertIn(rel, active, "建立後必須登記於 active_dca.json")
        self.assertFalse(active[rel], "auto_start=False 時不得被登記為啟動中（G-8）")

        detail = self.client.get(f"/api/v1/dca/{bot_id}", headers=self._headers())
        self.assertEqual(detail.status_code, 200, detail.text[:300])

    def test_8_1_b_klines_endpoint_returns_offline_synthetic_candles(self):
        response = self.client.get(
            f"/api/v1/market/klines/{self.BOT_SYMBOL}",
            headers=self._headers(),
            params={
                "exchange": self.BOT_EXCHANGE,
                "timeframe": "15m",
                "limit": 300,
                "account_mode": "paper",
            },
        )
        self.assertEqual(response.status_code, 200, response.text[:300])
        body = response.json()
        self.assertEqual(body["source"], "paper_synthetic")
        self.assertEqual(body["data_status"], "paper")
        self.assertEqual(len(body["candles"]), 300)
        # S-4 / G-16：合成資料必須誠實標示
        self.assertTrue(
            any("合成" in w for w in body["warnings"]),
            f"合成 K 線必須揭露，實得 warnings={body['warnings']}",
        )
        first = body["candles"][0]
        self.assertLessEqual(first["l"], first["o"])
        self.assertGreaterEqual(first["h"], first["c"])
        self.assertEqual(self.forbidden_fetch.calls, 0, "PAPER 路徑不得觸發交易所抓取")

    def test_8_1_c_chart_endpoint_structure_and_armed_semantics(self):
        bot_id = self._ensure_bot()
        response = self.client.get(
            f"/api/v1/dca/{bot_id}/chart",
            headers=self._headers(),
            params={"timeframe": "15m", "limit": 200},
        )
        self.assertEqual(response.status_code, 200, response.text[:400])
        body = response.json()

        for field in ("bot_id", "candles", "marks", "lines", "data_status", "source", "warnings"):
            self.assertIn(field, body, f"/chart 缺少欄位 {field}")
        self.assertEqual(body["bot_id"], bot_id)
        self.assertEqual(len(body["candles"]), 200)
        self.assertEqual(body["data_status"], "paper")

        # 6.6 / 8.1：機器人尚未啟動（無 runtime）→ 不得有任何下單標記
        self.assertEqual(
            [m for m in body["marks"] if m["kind"] == "order_placed"], [],
            "未啟動的機器人不得出現 order_placed 標記（INV-1）",
        )
        # G-5：價格線未知時是 null，不得補 0
        for line in body["lines"]:
            self.assertIn("kind", line)
            self.assertNotEqual(line.get("price"), 0, f"未知價格必須為 null 而非 0：{line}")

        # 前端全量渲染 warnings，重複條目會直接變成畫面上的兩行
        # （C8 live 驗證抓到 /chart 重複附加「使用合成 K 線，非真實歷史」）。
        self.assertEqual(
            len(body["warnings"]), len(set(body["warnings"])),
            f"warnings 不得重複：{body['warnings']}",
        )
        self.assertTrue(any("合成" in w for w in body["warnings"]))

    def test_8_1_d_backtest_completes_at_least_one_cycle(self):
        bot_id = self._ensure_bot()
        response = self.client.post(
            "/api/v1/dca/backtest?source=c8-e2e-read",
            headers=self._headers(),
            json={"bot_id": bot_id, "timeframe": "15m", "bars": 600, "initial_capital": "1000"},
        )
        self.assertEqual(response.status_code, 200, response.text[:400])
        result = response.json()["result"]

        self.assertEqual(result["bars"], 600)
        self.assertEqual(result["timeframe"], "15m")
        self.assertEqual(result["data_status"], "paper")
        self.assertGreater(result["bars_per_second"], 0)

        metrics = result["metrics"]
        self.assertGreaterEqual(
            metrics["cycles_completed"] or 0, 1,
            f"600 根合成棒上應至少完成一輪，實得 metrics={metrics}",
        )
        # RC-9：buy&hold 對照是必要指標
        for field in ("total_return_pct", "buy_hold_return_pct", "alpha_pct", "max_drawdown_pct"):
            self.assertIn(field, metrics)
        self.assertIsNotNone(metrics["buy_hold_return_pct"])

        # A-6 / D-11：簡化事項必須揭露
        for required in ("回測不模擬部分成交", "未套用交易所最小名目限制", "歷史績效不代表未來"):
            self.assertIn(required, result["warnings"])
        self.assertTrue(any("合成" in w for w in result["warnings"]))

        # 標記與權益的時間邊界
        self.assertGreater(len(result["marks"]), 0, "應至少有一筆虛擬下單/成交標記")
        self.assertEqual(len(result["equity"]), 600)
        first_ts, last_ts = result["first_ts"], result["last_ts"]
        for mark in result["marks"]:
            self.assertGreaterEqual(mark["ts"], first_ts)
            self.assertLessEqual(mark["ts"], last_ts)
            self.assertGreaterEqual(mark["cycle_index"], 1)
        # G-15：標記時間必須是棒時間（對齊 15m 網格），不得是牆鐘
        for mark in result["marks"]:
            self.assertEqual(mark["ts"] % 900, first_ts % 900, f"標記未落在棒時間上：{mark}")

        # §4.7：`order_placed` 是「僅即時圖」的標記（未成交在世掛單）。
        # 回測結果只投影成交，出現 order_placed 代表語意跑掉了。
        allowed_kinds = {
            "base_filled", "safety_filled", "take_profit_filled", "stop_loss_filled", "skipped",
        }
        kinds = {m["kind"] for m in result["marks"]}
        self.assertTrue(kinds.issubset(allowed_kinds), f"回測出現非預期標記種類：{kinds}")
        self.assertIn("base_filled", kinds)
        self.assertIn("take_profit_filled", kinds)

        # 輪次紀錄與 metrics 必須自洽（前端直接顯示這兩者，分岔會被使用者看見）
        completed = [c for c in result["cycles"] if c["exit_reason"] == "take_profit"]
        self.assertEqual(
            len(completed), len([m for m in result["marks"] if m["kind"] == "take_profit_filled"]),
            "止盈輪次數與止盈標記數必須一致",
        )

        type(self).backtest_metrics = metrics

    def test_8_1_d2_backtest_is_deterministic(self):
        """同一請求跑兩次必須逐位元組相同（RC-5：結果不得隨執行環境漂移）。"""
        bot_id = self._ensure_bot()
        payload = {"bot_id": bot_id, "timeframe": "15m", "bars": 400, "initial_capital": "1000"}
        first = self.client.post(
            "/api/v1/dca/backtest?source=c8-e2e-read", headers=self._headers(), json=payload
        )
        second = self.client.post(
            "/api/v1/dca/backtest?source=c8-e2e-read", headers=self._headers(), json=payload
        )
        self.assertEqual(first.status_code, 200, first.text[:300])
        self.assertEqual(second.status_code, 200, second.text[:300])
        # `bars_per_second` 是效能量測，本來就會浮動；其餘欄位必須逐一相同。
        first_result = {k: v for k, v in first.json()["result"].items() if k != "bars_per_second"}
        second_result = {k: v for k, v in second.json()["result"].items() if k != "bars_per_second"}
        self.assertEqual(
            first_result, second_result,
            "相同輸入必須得到相同回測結果（牆鐘/亂數洩漏會在此現形）",
        )

    def test_8_1_e_sweep_completes_at_least_two_runs(self):
        bot_id = self._ensure_bot()
        response = self.client.post(
            "/api/v1/dca/backtest/sweep?source=c8-e2e-read",
            headers=self._headers(),
            json={
                "bot_id": bot_id,
                "timeframe": "15m",
                "bars": 400,
                "initial_capital": "1000",
                "take_profit_percent_range": [1.0, 2.0],
                "max_total_bars": 5000,
                "max_workers": 2,
                "best_by": "total_return_pct",
            },
        )
        self.assertEqual(response.status_code, 200, response.text[:400])
        body = response.json()

        self.assertGreaterEqual(len(body["runs"]), 2, body)
        self.assertEqual(body["best_by"], "total_return_pct")
        budget = body["budget"]
        for field in ("bars_used", "bars_limit", "runs_completed", "runs_total"):
            self.assertIn(field, budget)
        self.assertGreaterEqual(budget["runs_completed"], 2)
        self.assertEqual(budget["runs_total"], 2)
        self.assertFalse(body["truncated"], "預算充足時不得回報截斷")

        for run in body["runs"]:
            self.assertIsNone(run["error"], run)
            self.assertIn("take_profit_percent", run["params"])
        self.assertIsNotNone(body["best"])
        self.assertEqual(body["best"]["metric"], "total_return_pct")

    # ── 8.1 收尾：刪除（確認鏈路可完整拆除）──

    def test_8_1_f_delete_removes_config_and_registration(self):
        bot_id = self._ensure_bot()
        rel = type(self).created_config_rel
        response = self.client.delete(
            f"/api/v1/dca/{bot_id}?source={self._next_write_source()}", headers=self._headers()
        )
        self.assertEqual(response.status_code, 200, response.text[:300])
        self.assertFalse(os.path.exists(os.path.join(self.project_root, rel)))
        with open(self.active_dca_path, "r", encoding="utf-8") as handle:
            active = json.load(handle)
        self.assertNotIn(rel, active)
        # 讓後續（字母序在後的）測試重新建立，避免相依刪除順序
        type(self).bot_id = None
        type(self).created_config_rel = None

    # ── 8.2 BP-2：回測是唯讀功能 ──

    def test_8_2_backtest_places_no_real_orders(self):
        """回測期間真實 adapter 的寫入方法呼叫次數必須為 0（BP-2 / G-11）。"""
        from unittest.mock import patch

        bot_id = self._ensure_bot()
        from core.adapters.exchanges.adapters.paper import PaperExchangeAdapter

        with patch.object(PaperExchangeAdapter, "create_order") as create_mock, \
                patch.object(PaperExchangeAdapter, "cancel_order") as cancel_mock:
            response = self.client.post(
                "/api/v1/dca/backtest?source=c8-e2e-read",
                headers=self._headers(),
                json={"bot_id": bot_id, "timeframe": "15m", "bars": 300},
            )
            self.assertEqual(response.status_code, 200, response.text[:300])
            self.assertEqual(create_mock.call_count, 0, "回測不得呼叫真實 create_order")
            self.assertEqual(cancel_mock.call_count, 0, "回測不得呼叫真實 cancel_order")

    def test_8_2_backtest_does_not_mutate_active_dca(self):
        """回測前後 config/active_dca.json 位元組必須完全相同（BP-2 / G-12）。"""
        bot_id = self._ensure_bot()
        before = self._read_active_dca_bytes()
        response = self.client.post(
            "/api/v1/dca/backtest?source=c8-e2e-read",
            headers=self._headers(),
            json={"bot_id": bot_id, "timeframe": "15m", "bars": 300},
        )
        self.assertEqual(response.status_code, 200, response.text[:300])
        self.assertEqual(before, self._read_active_dca_bytes(), "回測不得寫入熱插拔登記檔")

    # ── 8.3 網格零回歸 ──

    def test_8_3_grid_core_has_zero_regression(self):
        """本計畫不得動到運行中的網格系統（G-1 / G-2 / G-18）。

        `GRID_REGRESSION_BASE` 可指定基準 commit；未設定時退回檢查工作區未提交改動。
        """
        base = os.getenv("GRID_REGRESSION_BASE", "")
        paths = [
            "core/services/grid/",
            "run_grid_trading.py",
            "web_ui/frontend/src/components/GridDetail.tsx",
        ]
        cmd = ["git", "diff", "--stat"] + ([base] if base else []) + ["--"] + paths
        completed = subprocess.run(
            cmd, cwd=self.project_root, capture_output=True, text=True, timeout=60
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "", f"網格檔案不得變更：\n{completed.stdout}")

    # ── 8.4 LIVE 維持關閉 ──

    def test_8_4_live_trading_remains_disabled(self):
        from web_ui.backend.lifecycle import live_trading_enabled

        self.assertFalse(
            live_trading_enabled(),
            "驗收必須在 PAPER 下進行（G-9：GRID_LIVE_TRADING_ENABLED 不得開啟）",
        )
        response = self.client.post(
            f"/api/v1/dca?source={self._next_write_source()}",
            headers=self._headers(),
            json={**self._create_payload(), "account_mode": "live"},
        )
        self.assertEqual(response.status_code, 403, response.text[:300])

    # ── 8.5 憑證掃描 ──

    def test_8_5_no_credentials_in_backtest_surface(self):
        """回測/圖表相關原始碼不得出現硬編碼憑證。"""
        secret_pattern = re.compile(
            r"(api[_-]?key|secret|private[_-]?key|token)\s*[:=]\s*[\"'][A-Za-z0-9/+_-]{16,}[\"']",
            re.IGNORECASE,
        )
        targets = [
            "core/services/dca/backtest",
            "web_ui/backend/kline_service.py",
            "web_ui/backend/kline_repository.py",
            "web_ui/frontend/src/utils/dcaChart.ts",
        ]
        offenders = []
        for target in targets:
            abs_target = os.path.join(self.project_root, target)
            files = []
            if os.path.isdir(abs_target):
                for root, _dirs, names in os.walk(abs_target):
                    files.extend(os.path.join(root, n) for n in names if n.endswith((".py", ".ts", ".tsx")))
            elif os.path.exists(abs_target):
                files.append(abs_target)
            for path in files:
                with open(path, "r", encoding="utf-8") as handle:
                    for lineno, line in enumerate(handle, 1):
                        if secret_pattern.search(line):
                            offenders.append(f"{path}:{lineno}: {line.strip()[:120]}")
        self.assertEqual(offenders, [], "偵測到疑似硬編碼憑證：\n" + "\n".join(offenders))

    # ── 8.6 單例一致性（C8 修復的根因回歸防護）──

    def test_8_6_backend_modules_are_not_imported_twice(self):
        """`auth` 與 `web_ui.backend.auth` 必須是同一個模組物件。

        兩個拼法各執行一次檔案時，模組級單例會出現兩份：
        兩個 API token、兩個 SQLite repository、兩份 GridStateStore 快取。
        這正是 C8 之前 15 個測試回報 `401 Invalid or missing API token`、
        以及 `test_dashboard_snapshot` 讀到真實 runtime 資料庫的根因。
        """
        import auth as auth_top
        import runtime_repository as repo_top
        import command_repository as cmd_top
        import grid_state_store as store_top
        import schemas as schemas_top
        from web_ui.backend import auth as auth_pkg
        from web_ui.backend import runtime_repository as repo_pkg
        from web_ui.backend import command_repository as cmd_pkg
        from web_ui.backend import grid_state_store as store_pkg
        from web_ui.backend import schemas as schemas_pkg

        for top, pkg, name in (
            (auth_top, auth_pkg, "auth"),
            (repo_top, repo_pkg, "runtime_repository"),
            (cmd_top, cmd_pkg, "command_repository"),
            (store_top, store_pkg, "grid_state_store"),
            (schemas_top, schemas_pkg, "schemas"),
        ):
            self.assertIs(top, pkg, f"{name} 被載入兩次（模組級單例會分岔）")

        import routes.dca as dca_top
        from web_ui.backend.routes import dca as dca_pkg
        self.assertIs(dca_top, dca_pkg, "routes.dca 被載入兩次")


if __name__ == "__main__":
    unittest.main()
