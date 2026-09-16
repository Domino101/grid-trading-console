"""
Hermes Grid API — 階段一離線測試

不需要啟動 FastAPI 伺服器，直接測試各模組的邏輯。

使用方式：
    python3 tests/test_hermes_api.py
"""

import sys
import os
import json
import tempfile
import shutil
import unittest

# 確保 web_ui/backend 在 sys.path 中
_backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../web_ui/backend"))
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


class TestYAMLGenerator(unittest.TestCase):
    """測試 YAML 配置自動生成器"""

    def setUp(self):
        from web_ui.backend.yaml_generator import GridYAMLGenerator
        self.gen = GridYAMLGenerator()

    # ── validate_params ──

    def test_valid_neutral_params(self):
        """完整合法中性網格參數 → 無錯誤"""
        errors = self.gen.validate_params({
            "symbol": "SOL/USDC:USDC",
            "exchange": "binance",
            "grid_type": "neutral",
            "grid_interval": 0.2,
            "order_amount": 0.1,
            "lower_price": 100.0,
            "upper_price": 200.0,
        })
        self.assertEqual(errors, [])

    def test_missing_required_fields(self):
        """缺少必填欄位 → 報錯"""
        errors = self.gen.validate_params({})
        required = ["symbol", "exchange", "grid_type", "grid_interval", "order_amount"]
        for field in required:
            self.assertTrue(any(field in e for e in errors), f"應報錯缺少 {field}")

    def test_invalid_grid_type(self):
        """無效 grid_type → 報錯"""
        errors = self.gen.validate_params({
            "symbol": "SOL/USDC:USDC",
            "exchange": "binance",
            "grid_type": "invalid_type",
            "grid_interval": 0.2,
            "order_amount": 0.1,
        })
        self.assertTrue(any("grid_type" in e for e in errors))

    def test_neutral_requires_price_range(self):
        """neutral 模式缺少價格區間 → 報錯"""
        errors = self.gen.validate_params({
            "symbol": "SOL/USDC:USDC",
            "exchange": "binance",
            "grid_type": "neutral",
            "grid_interval": 0.2,
            "order_amount": 0.1,
            # 缺少 lower_price, upper_price
        })
        self.assertTrue(any("lower_price" in e for e in errors))

    def test_follow_requires_grid_count(self):
        """follow 模式缺少 follow_grid_count → 報錯"""
        errors = self.gen.validate_params({
            "symbol": "SOL/USDC:USDC",
            "exchange": "binance",
            "grid_type": "follow_long",
            "grid_interval": 0.2,
            "order_amount": 0.1,
        })
        self.assertTrue(any("follow_grid_count" in e for e in errors))

    def test_lower_price_gte_upper_price(self):
        """lower_price >= upper_price → 報錯"""
        errors = self.gen.validate_params({
            "symbol": "SOL/USDC:USDC",
            "exchange": "binance",
            "grid_type": "neutral",
            "grid_interval": 0.2,
            "order_amount": 0.1,
            "lower_price": 200.0,
            "upper_price": 100.0,
        })
        self.assertTrue(any("lower_price" in e and "upper_price" in e for e in errors))

    def test_leverage_out_of_range(self):
        """leverage 超出範圍 → 報錯"""
        errors = self.gen.validate_params({
            "symbol": "SOL/USDC:USDC",
            "exchange": "binance",
            "grid_type": "neutral",
            "grid_interval": 0.2,
            "order_amount": 0.1,
            "lower_price": 100.0,
            "upper_price": 200.0,
            "leverage": 200,
        })
        self.assertTrue(any("leverage" in e for e in errors))

    def test_invalid_exchange(self):
        """無效 exchange → 報錯"""
        errors = self.gen.validate_params({
            "symbol": "SOL/USDC:USDC",
            "exchange": "fake_exchange",
            "grid_type": "neutral",
            "grid_interval": 0.2,
            "order_amount": 0.1,
            "lower_price": 100.0,
            "upper_price": 200.0,
        })
        self.assertTrue(any("exchange" in e for e in errors))

    # ── generate ──

    def test_generate_neutral_yaml(self):
        """生成中性網格 YAML — 驗證結構與 Binance 安全預設值"""
        import yaml

        yaml_content, file_path = self.gen.generate({
            "symbol": "ETH/USDC:USDC",
            "exchange": "binance",
            "grid_type": "neutral",
            "grid_interval": 1.0,
            "order_amount": 0.01,
            "lower_price": 1800.0,
            "upper_price": 3200.0,
            "leverage": 50,
            "margin_mode": "cross",
        })

        # 驗證檔案路徑格式
        self.assertTrue(file_path.startswith("config/grid/"))
        self.assertTrue(file_path.endswith(".yaml"))
        self.assertIn("binance", file_path)
        self.assertIn("neutral", file_path)

        # 驗證 YAML 結構
        data = yaml.safe_load(yaml_content)
        self.assertIn("grid_system", data)
        gs = data["grid_system"]

        # 必填欄位
        self.assertEqual(gs["exchange"], "binance")
        self.assertEqual(gs["symbol"], "ETH/USDC:USDC")
        self.assertEqual(gs["grid_type"], "neutral")
        self.assertEqual(gs["grid_interval"], 1.0)
        self.assertEqual(gs["order_amount"], 0.01)
        self.assertEqual(gs["leverage"], 50)

        # 價格區間
        self.assertIn("price_range", gs)
        self.assertEqual(gs["price_range"]["lower_price"], 1800.0)
        self.assertEqual(gs["price_range"]["upper_price"], 3200.0)

        # Binance 安全預設值
        self.assertTrue(gs["limit_only"])
        self.assertTrue(gs["ws_only_mode"])
        self.assertFalse(gs["rest_backup_enabled"])
        self.assertEqual(gs["active_window_each_side"], 2)

    def test_generate_follow_yaml(self):
        """生成 follow 模式 YAML — 無 price_range，有 follow_grid_count"""
        import yaml

        yaml_content, file_path = self.gen.generate({
            "symbol": "SOL/USDC:USDC",
            "exchange": "binance",
            "grid_type": "follow_long",
            "grid_interval": 0.2,
            "order_amount": 0.1,
            "follow_grid_count": 200,
        })

        data = yaml.safe_load(yaml_content)
        gs = data["grid_system"]

        self.assertNotIn("price_range", gs)
        self.assertEqual(gs["follow_grid_count"], 200)
        self.assertIn("follow-long", file_path)

    def test_generate_non_binance_no_safe_defaults(self):
        """非 Binance 交易所 → 不填入 Binance 安全預設值"""
        import yaml

        yaml_content, _ = self.gen.generate({
            "symbol": "BTC/USD",
            "exchange": "hyperliquid",
            "grid_type": "long",
            "grid_interval": 10.0,
            "order_amount": 0.001,
            "lower_price": 60000.0,
            "upper_price": 80000.0,
        })

        data = yaml.safe_load(yaml_content)
        gs = data["grid_system"]

        self.assertNotIn("limit_only", gs)
        self.assertNotIn("ws_only_mode", gs)

    def test_generate_optional_params(self):
        """可選高級參數在設定時寫入 YAML"""
        import yaml

        yaml_content, _ = self.gen.generate({
            "symbol": "SOL/USDC:USDC",
            "exchange": "binance",
            "grid_type": "neutral",
            "grid_interval": 0.2,
            "order_amount": 0.1,
            "lower_price": 100.0,
            "upper_price": 200.0,
            "scalping_enabled": True,
            "scalping_trigger_percent": 80.0,
            "stop_loss_protection_enabled": True,
            "stop_loss_trigger_percent": 50.0,
        })

        data = yaml.safe_load(yaml_content)
        gs = data["grid_system"]

        self.assertTrue(gs["scalping_enabled"])
        self.assertEqual(gs["scalping_trigger_percent"], 80.0)
        self.assertTrue(gs["stop_loss_protection_enabled"])
        self.assertEqual(gs["stop_loss_trigger_percent"], 50.0)

    # ── grid_id / path 轉換 ──

    def test_grid_id_from_path(self):
        """config_path → grid_id"""
        grid_id = self.gen.grid_id_from_path("config/grid/binance-neutral-perp-sol-usdc-100-200.yaml")
        self.assertEqual(grid_id, "binance-neutral-perp-sol-usdc-100-200")

    def test_path_from_grid_id(self):
        """grid_id → config_path"""
        path = self.gen.path_from_grid_id("binance-neutral-perp-sol-usdc-100-200")
        self.assertEqual(path, "config/grid/binance-neutral-perp-sol-usdc-100-200.yaml")

    def test_roundtrip_id_path(self):
        """grid_id ↔ config_path 來回轉換一致"""
        original = "config/grid/binance-neutral-perp-eth-usdc-1800-3200.yaml"
        grid_id = self.gen.grid_id_from_path(original)
        reconstructed = self.gen.path_from_grid_id(grid_id)
        self.assertEqual(original, reconstructed)


class TestSafetyGuard(unittest.TestCase):
    """測試安全護欄"""

    def setUp(self):
        # 建立臨時目錄模擬專案結構
        self.tmpdir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmpdir, "config", "grid"), exist_ok=True)

        from web_ui.backend.safety_guard import SafetyGuard
        self.guard = SafetyGuard(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_config_dir_writable(self):
        """config/grid/ 存在且可寫 → 通過"""
        ok, msg = self.guard.check_config_dir_writable()
        self.assertTrue(ok)
        self.assertIsNone(msg)

    def test_config_dir_not_exist(self):
        """config/grid/ 不存在 → 失敗"""
        shutil.rmtree(os.path.join(self.tmpdir, "config", "grid"))
        ok, msg = self.guard.check_config_dir_writable()
        self.assertFalse(ok)
        self.assertIn("不存在", msg)

    def test_config_not_exists_pass(self):
        """配置檔案不存在 → 通過"""
        ok, msg = self.guard.check_config_not_exists("config/grid/new-grid.yaml")
        self.assertTrue(ok)

    def test_config_already_exists(self):
        """配置檔案已存在 → 失敗"""
        yaml_path = os.path.join(self.tmpdir, "config", "grid", "existing.yaml")
        with open(yaml_path, "w") as f:
            f.write("grid_system: {}\n")

        ok, msg = self.guard.check_config_not_exists("config/grid/existing.yaml")
        self.assertFalse(ok)
        self.assertIn("已存在", msg)

    def test_max_grids_under_limit(self):
        """運行中網格數未達上限 → 通過"""
        # 空的 active_grids.json
        ag_path = os.path.join(self.tmpdir, "config", "active_grids.json")
        with open(ag_path, "w") as f:
            json.dump({}, f)

        ok, msg = self.guard.check_max_grids()
        self.assertTrue(ok)

    def test_max_grids_at_limit(self):
        """運行中網格數已達上限 → 失敗"""
        from web_ui.backend.safety_guard import MAX_CONCURRENT_GRIDS

        ag_path = os.path.join(self.tmpdir, "config", "active_grids.json")
        data = {f"config/grid/grid-{i}.yaml": True for i in range(MAX_CONCURRENT_GRIDS)}
        with open(ag_path, "w") as f:
            json.dump(data, f)

        ok, msg = self.guard.check_max_grids()
        self.assertFalse(ok)
        self.assertIn("最大", msg)

    def test_duplicate_symbol_detection(self):
        """同 symbol 已有 active grid → 失敗"""
        import yaml as _yaml

        # 建立一個 YAML 配置
        yaml_path = os.path.join(self.tmpdir, "config", "grid", "test-grid.yaml")
        with open(yaml_path, "w") as f:
            _yaml.dump({"grid_system": {"symbol": "SOL/USDC:USDC"}}, f)

        # 設為 active
        ag_path = os.path.join(self.tmpdir, "config", "active_grids.json")
        with open(ag_path, "w") as f:
            json.dump({"config/grid/test-grid.yaml": True}, f)

        ok, msg = self.guard.check_duplicate_symbol("SOL/USDC:USDC")
        self.assertFalse(ok)
        self.assertIn("SOL/USDC:USDC", msg)

    def test_no_duplicate_if_stopped(self):
        """同 symbol 已有 grid 但已停止 (enabled=false) → 通過"""
        import yaml as _yaml

        yaml_path = os.path.join(self.tmpdir, "config", "grid", "test-grid.yaml")
        with open(yaml_path, "w") as f:
            _yaml.dump({"grid_system": {"symbol": "SOL/USDC:USDC"}}, f)

        ag_path = os.path.join(self.tmpdir, "config", "active_grids.json")
        with open(ag_path, "w") as f:
            json.dump({"config/grid/test-grid.yaml": False}, f)

        ok, msg = self.guard.check_duplicate_symbol("SOL/USDC:USDC")
        self.assertTrue(ok)


class TestSchemas(unittest.TestCase):
    """測試 Pydantic 模型"""

    def test_create_grid_request_minimal(self):
        """最小合法請求 → 成功建立"""
        from web_ui.backend.schemas import CreateGridRequest

        req = CreateGridRequest(
            symbol="SOL/USDC:USDC",
            exchange="binance",
            grid_type="neutral",
            grid_interval=0.2,
            order_amount=0.1,
            lower_price=100.0,
            upper_price=200.0,
        )
        self.assertEqual(req.symbol, "SOL/USDC:USDC")
        self.assertFalse(req.auto_start)  # 預設值：建立與啟動分離
        self.assertIsNone(req.metadata)

    def test_create_grid_request_full(self):
        """完整請求（含所有可選欄位）→ 成功建立"""
        from web_ui.backend.schemas import CreateGridRequest, GridMetadata

        req = CreateGridRequest(
            symbol="SOL/USDC:USDC",
            exchange="binance",
            grid_type="neutral",
            grid_interval=0.2,
            order_amount=0.1,
            lower_price=100.0,
            upper_price=200.0,
            leverage=50,
            margin_mode="cross",
            scalping_enabled=True,
            auto_start=False,
            metadata=GridMetadata(
                source="hermes",
                reason="測試",
                confidence=0.85,
            ),
        )
        self.assertEqual(req.leverage, 50)
        self.assertFalse(req.auto_start)
        self.assertEqual(req.metadata.source, "hermes")
        self.assertAlmostEqual(req.metadata.confidence, 0.85)

    def test_create_grid_request_validation_error(self):
        """grid_interval <= 0 → ValidationError"""
        from web_ui.backend.schemas import CreateGridRequest
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            CreateGridRequest(
                symbol="SOL/USDC:USDC",
                exchange="binance",
                grid_type="neutral",
                grid_interval=-0.1,  # 無效
                order_amount=0.1,
            )

    def test_model_dump_exclude_none(self):
        """model_dump(exclude_none=True) 排除 None 欄位"""
        from web_ui.backend.schemas import CreateGridRequest

        req = CreateGridRequest(
            symbol="SOL/USDC:USDC",
            exchange="binance",
            grid_type="neutral",
            grid_interval=0.2,
            order_amount=0.1,
        )
        dumped = req.model_dump(exclude_none=True)
        self.assertNotIn("scalping_enabled", dumped)
        self.assertNotIn("lower_price", dumped)
        self.assertIn("symbol", dumped)

    def test_system_health_response(self):
        """SystemHealthResponse 序列化"""
        from web_ui.backend.schemas import (
            SystemHealthResponse,
            CircuitBreakerInfo,
            ApiThrottleInfo,
            EngineInfo,
        )

        resp = SystemHealthResponse(
            status="healthy",
            circuit_breaker=CircuitBreakerInfo(is_open=False),
            api_throttle=ApiThrottleInfo(total_calls=142, window_current=23, window_max=2400),
            engine=EngineInfo(process_running=True, pid=12345),
            running_grids=2,
            running_grid_configs=["config/grid/a.yaml", "config/grid/b.yaml"],
            max_recommended_grids=5,
            can_accept_new_grid=True,
        )
        data = resp.model_dump()
        self.assertEqual(data["status"], "healthy")
        self.assertTrue(data["can_accept_new_grid"])
        self.assertEqual(data["api_throttle"]["window_max"], 2400)


class TestAuth(unittest.TestCase):
    """測試認證模組"""

    def test_token_resolution_from_env(self):
        """GRID_API_TOKEN 環境變數優先"""
        original = os.environ.get("GRID_API_TOKEN")
        try:
            os.environ["GRID_API_TOKEN"] = "test-token-12345"
            from web_ui.backend.auth import _resolve_api_token
            token = _resolve_api_token()
            self.assertEqual(token, "test-token-12345")
        finally:
            if original is None:
                os.environ.pop("GRID_API_TOKEN", None)
            else:
                os.environ["GRID_API_TOKEN"] = original

    def test_token_constant_time_comparison(self):
        """驗證 verify_token / token_matches 使用 secrets.compare_digest"""
        import secrets
        import inspect
        from web_ui.backend import auth
        source = inspect.getsource(auth.token_matches)
        self.assertIn("compare_digest", source)


class TestGridStateStore(unittest.TestCase):
    """測試 GridStateStore 共享儲存層"""

    def setUp(self):
        import time
        import tempfile
        from web_ui.backend.grid_state_store import GridStateStore
        from web_ui.backend.runtime_repository import RuntimeSnapshotRepository
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = os.path.join(self.temp_dir.name, "test_runtime.sqlite3")
        self.repository = RuntimeSnapshotRepository(db_path)
        RuntimeSnapshotRepository._instance = self.repository
        GridStateStore._instance = None
        self.store = GridStateStore.instance()

    def tearDown(self):
        from web_ui.backend.grid_state_store import GridStateStore
        from web_ui.backend.runtime_repository import RuntimeSnapshotRepository
        self.temp_dir.cleanup()
        RuntimeSnapshotRepository.reset_instance()
        GridStateStore.reset_instance()

    def test_basic_crud(self):
        """測試狀態更新、獲取與刪除"""
        stats = {
            "symbol": "SOL/USDC:USDC",
            "current_price": 167.42,
            "current_position": 0.3,
            "profit": {
                "realized": 12.35,
                "unrealized": -2.10,
            }
        }
        config_path = "config/grid/test-sol.yaml"
        self.store.update(config_path, stats)

        # 讀取單一網格
        ret = self.store.get(config_path)
        self.assertIsNotNone(ret)
        self.assertEqual(ret["current_price"], 167.42)
        self.assertFalse(ret["stale"])

        # 讀取全部網格
        all_states = self.store.get_all()
        self.assertIn(config_path, all_states)
        self.assertEqual(all_states[config_path]["current_position"], 0.3)

        # 刪除網格
        self.store.remove(config_path)
        self.assertIsNone(self.store.get(config_path))

    def test_stale_detection(self):
        """測試 TTL 快取過期判定"""
        import time
        stats = {"symbol": "BTC/USDC"}
        config_path = "config/grid/test-btc.yaml"
        self.store.update(config_path, stats)

        # 手動修改時間戳，模擬 200 秒前更新（超過門檻 180 秒）
        with self.store._lock:
            self.store._updated_at[config_path] = self.store._updated_at[config_path] - 200

        ret = self.store.get(config_path)
        self.assertTrue(ret["stale"])

        all_states = self.store.get_all()
        self.assertTrue(all_states[config_path]["stale"])

    def test_get_summary(self):
        """測試全局數據聚合計算"""
        stats1 = {
            "symbol": "SOL/USDC:USDC",
            "current_price": 100.0,
            "profit": {"realized": 10.0, "unrealized": 2.0}
        }
        stats2 = {
            "symbol": "ETH/USDC:USDC",
            "current_price": 2000.0,
            "profit": {"realized": 20.0, "unrealized": -5.0}
        }
        self.store.update("config/grid/sol.yaml", stats1)
        self.store.update("config/grid/eth.yaml", stats2)

        summary = self.store.get_summary()
        self.assertEqual(summary["total_grids"], 2)
        self.assertEqual(summary["total_realized_pnl"], 30.0)
        self.assertEqual(summary["total_unrealized_pnl"], -3.0)
        self.assertEqual(summary["total_pnl"], 27.0)


class TestProcessManagerParseStats(unittest.TestCase):
    """測試 ProcessManager stdout 攔截與解析 [STATS_JSON]"""

    def setUp(self):
        from web_ui.backend.process_manager import ProcessManager
        from web_ui.backend.grid_state_store import GridStateStore
        from web_ui.backend.runtime_repository import RuntimeSnapshotRepository
        
        self.tmpdir = tempfile.mkdtemp()
        db_path = os.path.join(self.tmpdir, "test_runtime.sqlite3")
        self.repository = RuntimeSnapshotRepository(db_path)
        RuntimeSnapshotRepository._instance = self.repository
        GridStateStore._instance = None
        self.store = GridStateStore.instance()
        self.pm = ProcessManager()

        # 模擬建立 active_grids.json 以便解析時的 mapping
        os.makedirs(os.path.join(self.tmpdir, "config", "grid"), exist_ok=True)
        self.original_project_root = self.pm.project_root
        self.pm.project_root = self.tmpdir
        
        # 建立 active_grids.json
        self.ag_path = os.path.join(self.tmpdir, "config", "active_grids.json")
        with open(self.ag_path, "w") as f:
            json.dump({"config/grid/test-sol.yaml": True}, f)
            
        # 建立 YAML
        self.yaml_path = os.path.join(self.tmpdir, "config", "grid", "test-sol.yaml")
        with open(self.yaml_path, "w") as f:
            import yaml
            yaml.dump({"grid_system": {"symbol": "SOL/USDC:USDC"}}, f)

    def tearDown(self):
        from web_ui.backend.grid_state_store import GridStateStore
        from web_ui.backend.runtime_repository import RuntimeSnapshotRepository
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        self.pm.project_root = self.original_project_root
        RuntimeSnapshotRepository.reset_instance()
        GridStateStore.reset_instance()

    def test_parse_stats_with_symbol_in_brackets(self):
        """測試 [STATS_JSON][SYMBOL] {...} 格式"""
        line = '[STATS_JSON][SOL/USDC:USDC] {"current_price": 150.0, "profit": {"realized": 5.0, "unrealized": 1.0}}'
        self.pm._parse_stats_line(line)

        # 驗證是否寫入快取
        stats = self.store.get("config/grid/test-sol.yaml")
        self.assertIsNotNone(stats)
        self.assertEqual(stats["current_price"], 150.0)
        self.assertEqual(stats["symbol"], "SOL/USDC:USDC")

    def test_parse_stats_with_only_stats_json(self):
        """測試 [STATS_JSON] {...} 格式（帶有 default_config_path）"""
        line = '[STATS_JSON] {"current_price": 160.0, "profit": {"realized": 10.0, "unrealized": 0.0}}'
        self.pm._parse_stats_line(line, "config/grid/test-sol.yaml")

        stats = self.store.get("config/grid/test-sol.yaml")
        self.assertIsNotNone(stats)
        self.assertEqual(stats["current_price"], 160.0)

    def test_parse_stats_ignored_lines(self):
        """測試非統計資料行會被忽略且無害"""
        line = '[INFO] Just a normal log line'
        self.pm._parse_stats_line(line)
        self.assertEqual(len(self.store._states), 0)

    def test_parse_stats_invalid_json(self):
        """測試無效 JSON 不崩潰"""
        line = '[STATS_JSON][SOL/USDC:USDC] {invalid json}'
        # 應被優雅忽略，不拋出 exception
        try:
            self.pm._parse_stats_line(line)
        except Exception as e:
            self.fail(f"_parse_stats_line raised an exception: {e}")
        self.assertEqual(len(self.store._states), 0)


class TestGridStatsEndpoints(unittest.TestCase):
    """測試 API /stats 與 /summary 端點"""

    def setUp(self):
        import tempfile
        from web_ui.backend import auth
        from web_ui.backend.runtime_repository import RuntimeSnapshotRepository
        from web_ui.backend.grid_state_store import GridStateStore

        self.original_api_token = auth._API_TOKEN
        auth._API_TOKEN = "test-token"

        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = os.path.join(self.temp_dir.name, "test_runtime.sqlite3")
        self.repository = RuntimeSnapshotRepository(db_path)
        RuntimeSnapshotRepository._instance = self.repository
        GridStateStore._instance = None
        self.store = GridStateStore.instance()

        from fastapi.testclient import TestClient
        from web_ui.backend.main import app
        self.client = TestClient(app)
        self.headers = {"Authorization": "Bearer test-token"}

        # 模擬 active_grids 檔案讀寫
        self.original_read = app.dependency_overrides.get("read_active_grids")
        # 建立模擬
        from web_ui.backend.routes import grids as grids_route
        self.original_read_active = grids_route._read_active_grids
        grids_route._read_active_grids = lambda: {"config/grid/test-sol.yaml": True}
        
        self.original_read_yaml = grids_route._read_yaml_config
        grids_route._read_yaml_config = lambda p: {"grid_system": {"symbol": "SOL/USDC:USDC"}} if "test-sol.yaml" in p else None

    def tearDown(self):
        from web_ui.backend import auth
        from web_ui.backend.runtime_repository import RuntimeSnapshotRepository
        from web_ui.backend.grid_state_store import GridStateStore

        auth._API_TOKEN = self.original_api_token
        
        from web_ui.backend.routes import grids as grids_route
        grids_route._read_active_grids = self.original_read_active
        grids_route._read_yaml_config = self.original_read_yaml
        os.environ.pop("GRID_API_TOKEN", None)

        self.temp_dir.cleanup()
        RuntimeSnapshotRepository.reset_instance()
        GridStateStore.reset_instance()

    def test_stats_not_found(self):
        """測試查詢不存在的網格 → 404"""
        resp = self.client.get("/api/v1/grids/non-exist/stats", headers=self.headers)
        self.assertEqual(resp.status_code, 404)

    def test_stats_running_but_empty(self):
        """測試查詢運行中但尚未有統計數據的網格 → 回傳 stale: True 預設結構"""
        # test-sol.yaml 是存在且運行的，但 store 裡無資料
        from web_ui.backend.routes import grids as grids_route

        # 清除快取與暫存庫快照
        with self.store._lock:
            self.store._states.clear()
            self.store._updated_at.clear()
        with self.store._repository._connect() as conn:
            conn.execute("DELETE FROM grid_runtime_snapshots")

        # Mock 檔案存在
        original_exists = os.path.exists
        os.path.exists = lambda p: True if "test-sol.yaml" in str(p) else original_exists(p)
        
        try:
            resp = self.client.get("/api/v1/grids/test-sol/stats", headers=self.headers)
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["grid_id"], "test-sol")
            self.assertTrue(data["stale"])
            self.assertIn(data["status"], ["starting", "running"])
        finally:
            os.path.exists = original_exists

    def test_stats_success(self):
        """測試查詢有數據的網格 → 200 成功"""
        from web_ui.backend.routes import grids as grids_route
        original_exists = os.path.exists
        os.path.exists = lambda p: True if "test-sol.yaml" in str(p) else original_exists(p)

        # 寫入 mock 統計
        self.store.update("config/grid/test-sol.yaml", {
            "symbol": "SOL/USDC:USDC",
            "current_price": 167.42,
            "profit": {
                "realized": 10.0,
                "unrealized": 1.0,
                "total": 11.0,
                "fees": 0.5,
                "net": 10.5,
                "rate": 1.5
            }
        })

        try:
            resp = self.client.get("/api/v1/grids/test-sol/stats", headers=self.headers)
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["grid_id"], "test-sol")
            self.assertEqual(data["current_price"], 167.42)
            self.assertFalse(data["stale"])
        finally:
            os.path.exists = original_exists

    def test_summary_success(self):
        """測試全局摘要 → 200 成功"""
        from web_ui.backend.routes import grids as grids_route
        original_exists = os.path.exists
        os.path.exists = lambda p: True if "test-sol.yaml" in str(p) else original_exists(p)

        self.store.update("config/grid/test-sol.yaml", {
            "symbol": "SOL/USDC:USDC",
            "current_price": 167.42,
            "profit": {"realized": 10.0, "unrealized": 1.0}
        })

        try:
            resp = self.client.get("/api/v1/grids/summary", headers=self.headers)
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["total_grids"], 1)
            self.assertEqual(data["total_realized_pnl"], 10.0)
            self.assertEqual(data["grids"][0]["grid_id"], "test-sol")
        finally:
            os.path.exists = original_exists


class TestAuditLogger(unittest.TestCase):
    """測試 SQLite 審計日誌讀寫"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        from web_ui.backend.audit import AuditLogger
        self.audit_logger = AuditLogger(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_log_and_get_logs(self):
        # 寫入成功日誌
        self.audit_logger.log(
            source="hermes",
            action="create_grid",
            grid_id="test-sol",
            params={"symbol": "SOL/USDC:USDC", "order_amount": 0.1},
            result="success",
            reason="SOL 價格回檔",
            error_msg=None
        )

        # 寫入拒絕日誌
        self.audit_logger.log(
            source="dashboard",
            action="start",
            grid_id="test-eth",
            params=None,
            result="rejected",
            reason="手動啟動",
            error_msg="熔斷器開啟，拒絕操作"
        )

        # 查詢所有日誌
        logs = self.audit_logger.get_logs()
        self.assertEqual(len(logs), 2)

        # 驗證順序為 id 倒序
        self.assertEqual(logs[0]["source"], "dashboard")
        self.assertEqual(logs[0]["result"], "rejected")
        self.assertEqual(logs[0]["error_msg"], "熔斷器開啟，拒絕操作")
        self.assertIsNone(logs[0]["params"])

        self.assertEqual(logs[1]["source"], "hermes")
        self.assertEqual(logs[1]["action"], "create_grid")
        self.assertEqual(logs[1]["grid_id"], "test-sol")
        self.assertEqual(logs[1]["params"]["symbol"], "SOL/USDC:USDC")

        # 依來源過濾
        hermes_logs = self.audit_logger.get_logs(source="hermes")
        self.assertEqual(len(hermes_logs), 1)
        self.assertEqual(hermes_logs[0]["grid_id"], "test-sol")


class TestSafetyGuardPhase3(unittest.TestCase):
    """測試階段三新增的安全護欄（頻率限制與最小投資額）"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmpdir, "config", "grid"), exist_ok=True)
        from web_ui.backend.safety_guard import SafetyGuard
        self.guard = SafetyGuard(self.tmpdir)

        # 重置 rate limiter 單例狀態
        from web_ui.backend.safety_guard import rate_limiter
        with rate_limiter._lock:
            rate_limiter._history.clear()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_write_rate_limiter(self):
        source = "hermes-test"
        # 前 3 次應允許
        for _ in range(3):
            ok, msg = self.guard.check_rate_limit(source)
            self.assertTrue(ok)
            self.guard.record_action(source)

        # 第 4 次應拒絕
        ok, msg = self.guard.check_rate_limit(source)
        self.assertFalse(ok)
        self.assertIn("已達寫操作頻率限制", msg)

        # 另一個來源應不受影響
        ok_other, msg_other = self.guard.check_rate_limit("dashboard-test")
        self.assertTrue(ok_other)

    def test_dashboard_rate_limiter(self):
        source = "dashboard"
        # 前 10 次應允許
        for _ in range(10):
            ok, msg = self.guard.check_rate_limit(source)
            self.assertTrue(ok)
            self.guard.record_action(source)

        # 第 11 次應拒絕並帶有剩餘秒數
        ok, msg = self.guard.check_rate_limit(source)
        self.assertFalse(ok)
        self.assertIn("已達寫操作頻率限制", msg)
        self.assertIn("最多 10 次", msg)
        self.assertIn("秒後再試", msg)

    def test_check_min_investment_pass(self):
        # 總投資額 = 0.1 * 10 (100-90 / 1) * 100.0 = 100.0 >= 10.0
        params = {
            "symbol": "SOL/USDC:USDC",
            "exchange": "binance",
            "grid_type": "neutral",
            "grid_interval": 1.0,
            "order_amount": 0.1,
            "lower_price": 90.0,
            "upper_price": 100.0
        }
        ok, msg = self.guard.check_min_investment(params)
        self.assertTrue(ok)
        self.assertIsNone(msg)

    def test_check_min_investment_fail(self):
        # 總投資額 = 0.01 * 5 * 100.0 = 5.0 < 10.0
        params = {
            "symbol": "SOL/USDC:USDC",
            "exchange": "binance",
            "grid_type": "neutral",
            "grid_interval": 2.0,
            "order_amount": 0.01,
            "lower_price": 90.0,
            "upper_price": 100.0
        }
        ok, msg = self.guard.check_min_investment(params)
        self.assertFalse(ok)
        self.assertIn("網格總投資額估算不足", msg)


class TestAuditRoute(unittest.TestCase):
    """測試 /api/v1/system/audit API 路由"""

    def setUp(self):
        from web_ui.backend import auth
        self.original_api_token = auth._API_TOKEN
        auth._API_TOKEN = "test-token"

        from fastapi.testclient import TestClient
        from web_ui.backend.main import app
        self.client = TestClient(app)
        self.headers = {"Authorization": "Bearer test-token"}

        # 模擬 AuditLogger
        self.tmpdir = tempfile.mkdtemp()
        from web_ui.backend.audit import AuditLogger
        self.mock_audit = AuditLogger(self.tmpdir)
        self.original_get_instance = AuditLogger.get_instance
        AuditLogger.get_instance = lambda *args, **kwargs: self.mock_audit

        # 寫入一條測試日誌
        self.mock_audit.log(
            source="hermes",
            action="stop",
            grid_id="test-sol",
            params=None,
            result="success",
            reason="止損觸發"
        )

    def tearDown(self):
        from web_ui.backend import auth
        auth._API_TOKEN = self.original_api_token

        from web_ui.backend.audit import AuditLogger
        AuditLogger.get_instance = self.original_get_instance
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_audit_endpoint_unauthorized(self):
        # 無 Token 請求 → 403 (HTTPBearer auto_error) 或 401
        resp = self.client.get("/api/v1/system/audit")
        self.assertIn(resp.status_code, (401, 403))


    def test_audit_endpoint_success(self):
        # 有 Token 請求 → 200 且返回日誌
        resp = self.client.get("/api/v1/system/audit", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["logs"][0]["source"], "hermes")
        self.assertEqual(data["logs"][0]["action"], "stop")
        self.assertEqual(data["logs"][0]["reason"], "止損觸發")


if __name__ == "__main__":
    # 設定工作目錄為專案根目錄
    os.chdir(_project_root)
    unittest.main(verbosity=2)

