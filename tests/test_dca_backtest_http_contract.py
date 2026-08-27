"""
DCA 回測 HTTP 契約測試（Phase C5 稽核 5.1-5.13，C7 修復並延伸）

依據：docs/dca_chart_backtest_plan_zh-TW.md §5.1-5.13

═══════════════════════════════════════════════════════════════════
本檔在 C7 經過一次結構性修復。原版存在「恆綠」盲區，紀錄於此以免重蹈：

1. fixture 用 `exchange: "paper"` 建立配置，但合法值只有
   binance/lighter/backpack/hyperliquid → `setUpClass` 直接 DcaConfigError，
   9 個測試全部 ERROR，等於整個檔案沒在跑。
   （`paper` 是 *account_mode* 的值，不是 exchange。）
2. 只把專案根加入 sys.path，但 `web_ui/backend/main.py` 內部有
   `from process_manager import ...` 這種同層匯入 → ModuleNotFoundError。
   必須同時把 `web_ui/backend` 加入 sys.path。
3. token 取自 `GRID_API_TOKEN`，但 `web_ui.backend.auth` 在 **import 當下**
   就把 `_API_TOKEN` 定死。環境變數必須在 import app 之前設好，
   否則 auth 會自行亂數生成 token，「有 token」的測試永遠 401。
4. 三個 5.8 契約測試把**所有斷言包在 `if response.status_code == 200:` 內**。
   端點恆 500 時測試恆綠 —— 這正是 C7 發現三個 500 缺陷前沒被擋下的原因。
   → 現在一律硬斷言 200，且不得有任何條件式包住斷言。
5. `test_5_4_bars_upper_limit` 斷言 400，實際是 **422**：pydantic 的
   `le=20000` 先於 route 的 400 守衛觸發。
6. `budget` 斷言 `runs_used`，實際欄位是 `runs_completed`
   （C5 曾在 schema 改名導致 AttributeError 500，之後已對齊 core）。
7. 5.9 的 grep 契約只掃 C6 的兩個檔，未涵蓋 C7 新增的三個回測元件。
═══════════════════════════════════════════════════════════════════
"""

import json
import os
import re
import subprocess
import sys
import unittest

# ── sys.path：專案根 + web_ui/backend（後者供 main.py 的同層匯入使用）──
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_backend_root = os.path.join(_project_root, "web_ui", "backend")
for _p in (_project_root, _backend_root):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ── Token 解析 ──
# `web_ui.backend.auth` 在 **import 當下** 就把 `_API_TOKEN` 解析並凍結。
# 若先設 GRID_API_TOKEN 再 import，單獨跑本檔可行；但整包 CI 跑時，
# 其他測試模組可能已先 import 過 auth，環境變數就來不及生效 → 全部 401。
# 因此改為「若尚未凍結才設環境變數，之後一律回讀 auth 真正在用的 token」。
os.environ.setdefault("GRID_API_TOKEN", "dca-backtest-contract-token")

from fastapi.testclient import TestClient  # noqa: E402
from web_ui.backend import auth as _auth  # noqa: E402
from web_ui.backend.main import app  # noqa: E402
from web_ui.backend.dca_yaml_generator import DcaYAMLGenerator  # noqa: E402

#: auth 模組實際生效中的 token（不受本檔與其他模組的 import 先後影響）
TEST_API_TOKEN = _auth._API_TOKEN

TEST_BOT_ID = "test-dca-backtest"
TEST_CONFIG_REL = f"config/dca/{TEST_BOT_ID}.yaml"


def _auth_headers():
    return {"Authorization": f"Bearer {TEST_API_TOKEN}"}


class TestDcaBacktestHttpContract(unittest.TestCase):
    """DCA 回測 HTTP 契約測試（走完整成功路徑，不用條件式包住斷言）。"""

    @classmethod
    def setUpClass(cls):
        cls.test_client = TestClient(app)
        cls.project_root = _project_root
        cls.generator = DcaYAMLGenerator()

        active_dca_path = os.path.join(cls.project_root, "config", "active_dca.json")
        if os.path.exists(active_dca_path):
            with open(active_dca_path, "rb") as f:
                cls.initial_active_dca = f.read()
        else:
            cls.initial_active_dca = b"{}"

        cls._ensure_test_dca_exists()

    def setUp(self):
        """每個測試前修復全域狀態，使本檔不依賴測試模組的執行順序。

        `tests/test_dca_http_contract.py` 會把 `routes.dca._get_project_root`
        永久指向一個 TemporaryDirectory 且**從不還原**，也會覆寫
        `auth._API_TOKEN`。整包 CI 跑時這些污染會外溢到後續模組，
        讓本檔的端點在真實 config/ 之外尋找設定 → 一律 404。
        （根因在該檔，這裡先自我防護，避免順序性假失敗。）
        """
        self._restore_global_state()
        type(self)._ensure_test_dca_exists()

    @staticmethod
    def _restore_global_state():
        """把被其他測試污染的模組級狀態指回真實專案。"""
        real_root = _project_root

        # routes.dca 可能同時以 `routes.dca` 與 `web_ui.backend.routes.dca`
        # 兩個模組物件存在（sys.path 有兩個入口），兩份都要修。
        for name in ("routes.dca", "web_ui.backend.routes.dca"):
            module = sys.modules.get(name)
            if module is not None:
                module._get_project_root = lambda: real_root

        # verify_token 實際讀的是它自身模組的 _API_TOKEN
        for name in ("auth", "web_ui.backend.auth"):
            module = sys.modules.get(name)
            if module is not None:
                module._API_TOKEN = TEST_API_TOKEN

    @classmethod
    def tearDownClass(cls):
        active_dca_path = os.path.join(cls.project_root, "config", "active_dca.json")
        if hasattr(cls, "initial_active_dca"):
            with open(active_dca_path, "wb") as f:
                f.write(cls.initial_active_dca)
        cls._cleanup_test_dca()

    @classmethod
    def _ensure_test_dca_exists(cls):
        """建立測試用 DCA 配置。

        注意：`exchange` 必須是真實交易所（binance/lighter/backpack/hyperliquid）；
        「模擬」是靠 `account_mode: paper` 表達，不是 `exchange: paper`。
        """
        config_dir = os.path.join(cls.project_root, "config", "dca")
        os.makedirs(config_dir, exist_ok=True)

        # 可重入：檔案還在就直接沿用（generator 檔名帶時間戳，重複產生會留下垃圾）
        existing = getattr(cls, "generated_config_rel", None)
        if existing and os.path.exists(os.path.join(cls.project_root, existing)):
            return

        test_params = {
            "symbol": "ETH/USDC:USDC",
            "exchange": "binance",
            "direction": "long",
            "account_mode": "paper",
            "base_order_amount": 100,
            "amount_unit": "quote",
            "dca_mode": "order",
            "averaging_method": "fixed_value",
            "max_safety_orders": 5,
            "price_deviation_percent": 1.0,
            "price_deviation_multiplier": 1.5,
            "order_size_multiplier": 1.0,
            "take_profit_percent": 2.0,
            "take_profit_order_type": "limit",
            "stop_loss_percent": None,
            "max_position_notional": None,
            "fee_rate": 0.001,
            "leverage": 10,
            "margin_mode": "isolated",
            "quantity_precision": None,
            "price_decimals": None,
            "repeat_after_take_profit": True,
            "cooldown_seconds": 300,
            "max_cycles": None,
            "both_tiebreak": "long_first",
            "active_safety_orders_window": 5,
            "monitoring_interval": 15,
            "indicator_refresh_interval": 60,
            "order_health_check_interval": 300,
            "entry": {
                "mode": "immediate",
                "logic": "all",
                "timeframe": "15m",
                "fetch_limit": 300,
                "price_source": "close",
                "confirm_bars": 1,
                "max_wait_seconds": None,
                "indicators": [],
            },
            "auto_start": False,
        }

        yaml_content, config_path = cls.generator.generate(test_params)
        abs_config_path = os.path.join(cls.project_root, config_path)
        with open(abs_config_path, "w", encoding="utf-8") as f:
            f.write(yaml_content)
        cls.generated_config_rel = config_path

        active_dca_path = os.path.join(cls.project_root, "config", "active_dca.json")
        active_dca = {}
        if os.path.exists(active_dca_path):
            with open(active_dca_path, "r", encoding="utf-8") as f:
                active_dca = json.load(f)
        active_dca[config_path] = False
        with open(active_dca_path, "w", encoding="utf-8") as f:
            json.dump(active_dca, f, indent=2, ensure_ascii=False)

    @classmethod
    def _cleanup_test_dca(cls):
        rel = getattr(cls, "generated_config_rel", TEST_CONFIG_REL)
        abs_path = os.path.join(cls.project_root, rel)
        if os.path.exists(abs_path):
            os.remove(abs_path)

        active_dca_path = os.path.join(cls.project_root, "config", "active_dca.json")
        if os.path.exists(active_dca_path):
            with open(active_dca_path, "r", encoding="utf-8") as f:
                active_dca = json.load(f)
            active_dca.pop(rel, None)
            with open(active_dca_path, "w", encoding="utf-8") as f:
                json.dump(active_dca, f, indent=2, ensure_ascii=False)

    @property
    def bot_id(self):
        rel = getattr(type(self), "generated_config_rel", TEST_CONFIG_REL)
        return os.path.splitext(os.path.basename(rel))[0]

    # ── 5.1 認證 ──

    def test_5_1_authentication_required(self):
        """5.1 P0: 端點無 token → 401。"""
        self.assertEqual(
            self.test_client.get(f"/api/v1/dca/{self.bot_id}/chart").status_code, 401
        )
        self.assertEqual(self.test_client.post("/api/v1/dca/backtest").status_code, 401)
        self.assertEqual(
            self.test_client.post("/api/v1/dca/backtest/sweep").status_code, 401
        )

    def test_5_1_with_token_access_granted(self):
        """5.1 P0: 有 token → 200（硬斷言，不接受 401/500）。"""
        response = self.test_client.get(
            f"/api/v1/dca/{self.bot_id}/chart",
            headers=_auth_headers(),
            params={"timeframe": "15m", "limit": 100},
        )
        self.assertEqual(
            response.status_code, 200, f"預期 200，實得 {response.status_code}: {response.text[:300]}"
        )

    # ── 5.4 上限守衛 ──

    def test_5_4_bars_upper_limit(self):
        """5.4 P0: bars=20001 被拒。

        pydantic `le=20000` 先於 route 的 400 守衛觸發，故為 422。
        兩者都是「明確拒絕」，重點是不得放行、也不得 500。
        """
        response = self.test_client.post(
            "/api/v1/dca/backtest",
            headers=_auth_headers(),
            json={"bot_id": self.bot_id, "bars": 20001, "timeframe": "15m"},
        )
        self.assertEqual(response.status_code, 422, response.text[:300])

    def test_5_4_bars_upper_bound_is_accepted(self):
        """5.4 邊界：bars=20000 恰好合法，不得被誤拒（off-by-one 防護）。"""
        response = self.test_client.post(
            "/api/v1/dca/backtest",
            headers=_auth_headers(),
            json={"bot_id": self.bot_id, "bars": 20000, "timeframe": "15m"},
        )
        self.assertNotEqual(response.status_code, 422, "20000 應在允許範圍內")

    def test_5_4_invalid_timeframe(self):
        """5.4 P0: timeframe="2m" → 400 且訊息為中文。"""
        response = self.test_client.post(
            "/api/v1/dca/backtest",
            headers=_auth_headers(),
            json={"bot_id": self.bot_id, "bars": 100, "timeframe": "2m"},
        )
        self.assertEqual(response.status_code, 400, response.text[:300])
        detail = str(response.json().get("detail", ""))
        self.assertIn("不支援", detail)

    def test_5_4_unknown_bot_returns_404(self):
        """5.4: 不存在的 bot_id → 404，不得 500。"""
        response = self.test_client.post(
            "/api/v1/dca/backtest",
            headers=_auth_headers(),
            json={"bot_id": "no-such-bot-xyz", "bars": 50, "timeframe": "15m"},
        )
        self.assertEqual(response.status_code, 404, response.text[:300])

    # ── 5.8 回應契約（硬斷言 200；禁止條件式包住斷言）──

    def test_5_8_chart_response_contract(self):
        """5.8 P0: GET /chart 回應欄位契約。"""
        response = self.test_client.get(
            f"/api/v1/dca/{self.bot_id}/chart",
            headers=_auth_headers(),
            params={"timeframe": "15m", "limit": 100},
        )
        self.assertEqual(response.status_code, 200, response.text[:300])
        data = response.json()

        for field in ("bot_id", "candles", "marks", "lines",
                      "current_price", "data_status", "source", "warnings"):
            self.assertIn(field, data, f"缺少必需欄位: {field}")

        for field in ("candles", "marks", "lines", "warnings"):
            self.assertIsInstance(data[field], list, f"{field} 應為陣列")

    def test_5_8_backtest_response_contract(self):
        """5.8 P0: POST /backtest 回應欄位契約（前端 C7 直讀的全部欄位）。"""
        response = self.test_client.post(
            "/api/v1/dca/backtest",
            headers=_auth_headers(),
            json={"bot_id": self.bot_id, "bars": 50, "timeframe": "15m"},
        )
        self.assertEqual(response.status_code, 200, response.text[:300])
        data = response.json()
        self.assertIn("result", data)
        result = data["result"]

        for field in ("bars", "timeframe", "first_ts", "last_ts", "metrics",
                      "equity", "marks", "cycles", "data_status", "source",
                      "warnings", "bars_per_second"):
            self.assertIn(field, result, f"缺少必需欄位: {field}")

        # 前端 DcaBacktestResult.tsx 的 8 張指標卡逐一直讀這些鍵
        metrics = result["metrics"]
        for field in ("initial_capital", "final_equity", "total_return_pct",
                      "buy_hold_return_pct", "alpha_pct", "realized_pnl",
                      "total_fees", "cycles_completed", "win_rate_pct",
                      "max_drawdown_pct", "exposure_pct", "avg_cycle_bars",
                      "mfe_pct", "mae_pct", "profit_factor"):
            self.assertIn(field, metrics, f"metrics 缺少必需欄位: {field}")

        # 稽核 7.7：profit_factor 允許為 null，前端須顯示「—」而非 0。
        # 契約要求「鍵存在」，型別為 float 或 None，不得是字串。
        self.assertTrue(
            metrics["profit_factor"] is None or isinstance(metrics["profit_factor"], (int, float)),
            "profit_factor 必須是數字或 null",
        )

        self.assertIsInstance(result["equity"], list)
        self.assertIsInstance(result["marks"], list)
        self.assertIsInstance(result["cycles"], list)

        # equity 點位契約（前端第二窗格直讀）
        if result["equity"]:
            point = result["equity"][0]
            for field in ("ts", "equity", "position_quantity", "average_cost", "status"):
                self.assertIn(field, point, f"equity 點缺少欄位: {field}")

    def test_5_8_sweep_response_contract(self):
        """5.8 P0: POST /backtest/sweep 回應欄位契約。"""
        response = self.test_client.post(
            "/api/v1/dca/backtest/sweep",
            headers=_auth_headers(),
            json={
                "bot_id": self.bot_id,
                "bars": 50,
                "timeframe": "15m",
                "base_order_amount_range": [100, 200],
                "max_total_bars": 1000,
                "max_workers": 2,
            },
        )
        self.assertEqual(response.status_code, 200, response.text[:300])
        data = response.json()

        for field in ("runs", "best_by", "truncated", "budget", "best"):
            self.assertIn(field, data, f"缺少必需欄位: {field}")

        self.assertIsInstance(data["runs"], list)
        self.assertIsInstance(data["truncated"], bool)

        # budget 欄位與 core.services.dca.backtest.sweep.SweepBudget 一對一對齊。
        # 稽核 7.4 的「已完成 N/M 組」直接讀 runs_completed / runs_total。
        budget = data["budget"]
        for field in ("bars_used", "bars_limit", "runs_completed", "runs_total", "runs_limit"):
            self.assertIn(field, budget, f"budget 缺少必需欄位: {field}")
        self.assertLessEqual(
            budget["runs_completed"], budget["runs_total"],
            "已完成組數不得超過總組數",
        )

        # 每個 run 的形狀（前端 DcaSweepTable 直讀）
        for run in data["runs"]:
            for field in ("params", "metrics", "error"):
                self.assertIn(field, run, f"run 缺少欄位: {field}")

        # best 可能所有指標皆 null 而無法比較，此時 value/params 為 None（G-5）。
        best = data["best"]
        if best is not None:
            for field in ("metric", "value", "params"):
                self.assertIn(field, best, f"best 缺少欄位: {field}")

    # ── 5.7 warnings 揭露 ──

    def test_5_7_backtest_warnings_disclosure(self):
        """5.7 P0: 回測回應 warnings 必含 §A-6 三條簡化事項。"""
        response = self.test_client.post(
            "/api/v1/dca/backtest",
            headers=_auth_headers(),
            json={"bot_id": self.bot_id, "bars": 50, "timeframe": "15m"},
        )
        self.assertEqual(response.status_code, 200, response.text[:300])
        warnings = response.json()["result"]["warnings"]

        for required in ("回測不模擬部分成交",
                         "未套用交易所最小名目限制",
                         "歷史績效不代表未來"):
            self.assertIn(required, warnings, f"缺少必需警告: {required}")

    def test_5_7_warnings_have_no_duplicates(self):
        """5.7: warnings 不得有重複條目（前端全量渲染，重複即是可見瑕疵）。"""
        response = self.test_client.post(
            "/api/v1/dca/backtest",
            headers=_auth_headers(),
            json={"bot_id": self.bot_id, "bars": 50, "timeframe": "15m"},
        )
        self.assertEqual(response.status_code, 200, response.text[:300])
        warnings = response.json()["result"]["warnings"]
        self.assertEqual(
            len(warnings), len(set(warnings)),
            f"warnings 含重複條目: {warnings}",
        )

    # ── 5.13 網格零回歸 ──

    def test_5_13_grid_zero_regression(self):
        """5.13 P0: 網格核心檔案零變更（工作區 + 已提交，皆對照基準分支）。"""
        base = os.environ.get("GRID_REGRESSION_BASE", "").strip()
        cmd = ["git", "diff", "--stat"]
        if base:
            cmd.append(base)
        cmd += ["--", "core/services/grid/", "run_grid_trading.py"]
        result = subprocess.run(cmd, cwd=self.project_root, capture_output=True, text=True)
        self.assertEqual(result.stdout.strip(), "", "網格核心檔案應該零變更")


class TestDcaBacktestStaticChecks(unittest.TestCase):
    """靜態檢查（檔案級 grep 契約）。"""

    #: 前端不得自行重算的階梯/指標欄位（G-4）
    BANNED_TOKENS = ("required_capital", "price_deviation", "take_profit_price")

    #: 受管制的前端檔案：C6 圖表 + C7 回測工作台
    GUARDED_FILES = (
        "web_ui/frontend/src/components/DcaChart.tsx",
        "web_ui/frontend/src/utils/dcaChart.ts",
        "web_ui/frontend/src/components/DcaBacktestPanel.tsx",
        "web_ui/frontend/src/components/DcaBacktestResult.tsx",
        "web_ui/frontend/src/components/DcaSweepTable.tsx",
    )

    @staticmethod
    def _read_code(rel_path):
        """讀檔並剝除註解，避免說明文字誤觸 grep 契約。

        必須依語言選擇註解語法：`.py` 用 `#`，`.ts`/`.tsx` 用 `//` 與 `/* */`。
        （曾因對 Python 檔套用 JS 規則，把描述地雷的 `#` 註解誤判為真實違規。）
        """
        with open(os.path.join(_project_root, rel_path), "r", encoding="utf-8") as f:
            src = f.read()
        if rel_path.endswith(".py"):
            return re.sub(r"#.*$", "", src, flags=re.MULTILINE)
        src = re.sub(r"/\*[\s\S]*?\*/", "", src)
        src = re.sub(r"(^|[^:])//.*$", r"\1", src, flags=re.MULTILINE)
        return src

    def test_5_9_frontend_zero_calculation_grep(self):
        """5.9 P0 / 7.1: 前端圖表與回測元件零金融計算。"""
        for rel_path in self.GUARDED_FILES:
            self.assertTrue(
                os.path.exists(os.path.join(_project_root, rel_path)),
                f"受管制檔案不存在（是否被改名？）: {rel_path}",
            )
            code = self._read_code(rel_path)
            for token in self.BANNED_TOKENS:
                self.assertNotIn(token, code, f"{rel_path} 不應包含階梯/指標數學: {token}")

    def test_5_9_frontend_uses_same_origin_urls(self):
        """5.9: 前端 API 層不得寫死 localhost / 127.0.0.1（預覽環境會壞）。"""
        code = self._read_code("web_ui/frontend/src/services/api.ts")
        for token in ("localhost", "127.0.0.1"):
            self.assertNotIn(token, code, f"api.ts 不得寫死 {token}")

    def test_5_11_non_blocking_grep(self):
        """5.11 P1: 回測以 threadpool + 逾時守衛執行，不阻塞事件迴圈。"""
        code = self._read_code("web_ui/backend/routes/dca.py")
        self.assertIn("asyncio.wait_for", code, "應使用 asyncio.wait_for 逾時守衛")

    def test_backend_routes_use_absolute_imports(self):
        """回歸防護：routes/dca.py 不得用 `from schemas import`。

        同層匯入會建立與 response_model 不同的 pydantic 類別物件，
        導致驗證失敗、端點 100% 500。C6/C7 已三度踩到此地雷。
        """
        code = self._read_code("web_ui/backend/routes/dca.py")
        self.assertNotIn(
            "from schemas import", code,
            "必須使用 `from web_ui.backend.schemas import`（絕對匯入）",
        )


if __name__ == "__main__":
    unittest.main()
