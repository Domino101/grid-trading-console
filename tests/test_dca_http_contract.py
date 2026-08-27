"""DCA HTTP contract tests (Phase 4 audit 4.1-4.24)."""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from decimal import Decimal as D
from unittest.mock import patch

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND = os.path.join(PROJECT_ROOT, "web_ui", "backend")
for path in (BACKEND, PROJECT_ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)

HAS_FASTAPI = importlib.util.find_spec("fastapi") is not None


def _create_payload(**overrides):
    payload = {
        "symbol": "ETH/USDC:USDC",
        "exchange": "binance",
        "direction": "long",
        "account_mode": "paper",
        "base_order_amount": 20,
        "max_safety_orders": 20,
        "price_deviation_percent": 0.8,
        "price_deviation_multiplier": 1.2,
        "order_size_multiplier": 1.0,
        "take_profit_percent": 1.0,
        "entry_price": 2500.0,
    }
    payload.update(overrides)
    return payload


def _preview_payload(**overrides):
    payload = _create_payload()
    payload.pop("account_mode", None)
    payload.pop("entry_price", None)
    payload["direction"] = "long"
    payload["entry_price"] = 2500.0
    payload.update(overrides)
    return payload


class PaperDcaGuard:
    def __init__(self, _project_root):
        pass

    def check_rate_limit(self, _source):
        return True, None

    def check_can_create_dca(self):
        return True, None

    def check_dca_config_dir_writable(self):
        return True, None

    def check_config_not_exists(self, _path):
        return True, None

    def check_max_combined_bots(self):
        return True, None

    def record_action(self, _source):
        pass


@unittest.skipUnless(HAS_FASTAPI, "FastAPI installed from requirements.txt in CI/release environments")
class DcaHttpContractTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        import auth
        from audit import AuditLogger
        from command_repository import GridCommandRepository
        from grid_state_store import GridStateStore
        from runtime_repository import RuntimeSnapshotRepository
        import routes.dca as dca_route
        import routes.market as market_route

        self.temp_dir = tempfile.TemporaryDirectory()
        os.makedirs(os.path.join(self.temp_dir.name, "config", "dca"))
        database_path = os.path.join(self.temp_dir.name, "runtime.sqlite3")

        # 這些都是**模組級全域**。C7 的工作紀錄已指出本檔從不還原它們，
        # 污染會外溢到後續測試模組（回測契約檔因此一律 404）。
        # 現在一律以 addCleanup 還原，讓本檔不再影響任何其他模組。
        self._stash_global(RuntimeSnapshotRepository, "_instance", RuntimeSnapshotRepository(database_path))
        self._stash_global(GridCommandRepository, "_instance", GridCommandRepository(database_path))
        self._stash_global(GridStateStore, "_instance", None)
        self._stash_global(AuditLogger, "_instance", AuditLogger(self.temp_dir.name))
        self._stash_global(auth, "_API_TOKEN", "contract-token")
        self.dca_route = dca_route
        self.market_route = market_route
        self._stash_global(dca_route, "_get_project_root", lambda: self.temp_dir.name)
        self._stash_global(dca_route, "SafetyGuard", PaperDcaGuard)

        import main
        self.client = TestClient(main.app)
        self.repository = GridCommandRepository.instance()

    def _stash_global(self, target, attribute, new_value):
        """設定模組/類別級全域，並登記測試結束後自動還原。"""
        sentinel = object()
        previous = getattr(target, attribute, sentinel)

        def _restore():
            if previous is sentinel:
                delattr(target, attribute)
            else:
                setattr(target, attribute, previous)

        self.addCleanup(_restore)
        setattr(target, attribute, new_value)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _headers(self):
        return {"Authorization": "Bearer contract-token"}

    def _create(self, headers=None, **overrides):
        headers = headers or self._headers()
        response = self.client.post("/api/v1/dca", headers=headers, json=_create_payload(**overrides))
        return response

    # ── 4.1 全端點需認證 ──
    def test_all_dca_endpoints_require_auth(self):
        endpoints = [
            ("GET", "/api/v1/dca"),
            ("POST", "/api/v1/dca"),
            ("GET", "/api/v1/dca/snapshot"),
            ("POST", "/api/v1/dca/preview"),
            ("GET", "/api/v1/dca/x"),
            ("GET", "/api/v1/dca/x/stats"),
            ("GET", "/api/v1/dca/x/ladder"),
            ("GET", "/api/v1/dca/x/orders"),
            ("GET", "/api/v1/dca/x/fills"),
            ("GET", "/api/v1/dca/x/events"),
            ("POST", "/api/v1/dca/x/start"),
            ("POST", "/api/v1/dca/x/stop"),
            ("DELETE", "/api/v1/dca/x"),
            ("GET", "/api/v1/market/indicators/ETH/USDC:USDC"),
        ]
        for method, url in endpoints:
            with self.subTest(url=url, method=method):
                response = self.client.request(method, url)
                self.assertIn(response.status_code, (401, 403), response.text)
                wrong = self.client.request(
                    method, url, headers={"Authorization": "Bearer wrong-token"}
                )
                self.assertIn(wrong.status_code, (401, 403), wrong.text)

    # ── 4.2 / 4.3 / 4.4 preview 與引擎同源、零外部呼叫、鎖定向量 ──
    def test_preview_matches_build_ladder_directly_and_vector_a(self):
        payload = _preview_payload(max_safety_orders=20)
        # ccxt 全掛也不影響 preview（純函式，零外部呼叫）
        with patch("routes.market._lookup_market_from_ccxt_sync", side_effect=Exception("network down")), \
             patch("routes.dca._fetch_ticker_price_sync", side_effect=Exception("network down")):
            response = self.client.post("/api/v1/dca/preview", headers=self._headers(), json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertAlmostEqual(body["required_capital"], 420.00, places=6)
        self.assertAlmostEqual(body["ladder"]["max_drawdown_percent"], 80.131727, places=6)
        self.assertAlmostEqual(body["ladder"]["final_take_profit_price"], 1483.677606, places=6)

        config = self.dca_route._generator.build_config(payload)
        plan = __import__("core.services.dca.ladder", fromlist=["build_ladder"]).build_ladder(
            config, D("2500"), "long"
        )
        levels = body["ladder"]["levels"]
        self.assertEqual(len(levels), len(plan.levels))
        for got, want in zip(levels, plan.levels):
            self.assertAlmostEqual(got["price"], float(want.price), places=6)
            self.assertAlmostEqual(got["notional"], float(want.notional), places=6)
            self.assertAlmostEqual(got["quantity"], float(want.quantity), places=6)

    def test_preview_does_not_rewrite_ladder_formula(self):
        with open(os.path.join(BACKEND, "routes", "dca.py"), encoding="utf-8") as handle:
            source = handle.read()
        # 禁止在 routes/dca.py 複製階梯公式（稽核 4.2）；build_ladder 必須被直接呼叫。
        self.assertIn("build_ladder", source)
        self.assertLess(source.count("price_deviation_multiplier"), 3)

    # ── 4.14 both 資金試算取 max ──
    def test_preview_both_returns_max_not_sum(self):
        response = self.client.post(
            "/api/v1/dca/preview", headers=self._headers(),
            json=_preview_payload(direction="both", max_safety_orders=5),
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertIsNotNone(body["ladder_long"])
        self.assertIsNotNone(body["ladder_short"])
        self.assertEqual(
            body["required_capital"],
            max(body["ladder_long"]["required_capital"], body["ladder_short"]["required_capital"]),
        )
        self.assertLess(
            body["required_capital"],
            body["ladder_long"]["required_capital"] + body["ladder_short"]["required_capital"],
        )

    # ── 4.8 / 4.9 建立→YAML、auto_start=false ──
    def test_create_writes_parseable_yaml_and_auto_start_false(self):
        response = self._create()
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertTrue(body["config_path"].startswith("config/dca/"))
        yaml_path = os.path.join(self.temp_dir.name, body["config_path"])
        self.assertTrue(os.path.exists(yaml_path))

        import yaml
        from core.services.dca.config_loader import load_dca_config
        with open(yaml_path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
        config = load_dca_config(data)
        self.assertEqual(config.symbol, "ETH/USDC:USDC")
        self.assertEqual(config.account_mode, "paper")

        active_path = os.path.join(self.temp_dir.name, "config", "active_dca.json")
        self.assertTrue(os.path.exists(active_path))
        with open(active_path, encoding="utf-8") as handle:
            active = json.load(handle)
        self.assertFalse(active[body["config_path"]])

    # ── 4.10 冪等鍵 ──
    def test_start_idempotency_returns_same_command(self):
        created = self._create()
        self.assertEqual(created.status_code, 201, created.text)
        bot_id = created.json()["bot_id"]
        headers = dict(self._headers())
        headers["Idempotency-Key"] = "same-key"

        first = self.client.post(f"/api/v1/dca/{bot_id}/start", headers=headers)
        self.assertEqual(first.status_code, 200, first.text)
        second = self.client.post(f"/api/v1/dca/{bot_id}/start", headers=headers)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(first.json()["command_id"], second.json()["command_id"])
        commands = self.repository.list_for_grid(bot_id)
        self.assertEqual(len(commands), 1)
        with open(os.path.join(self.temp_dir.name, "config", "active_dca.json"), encoding="utf-8") as handle:
            active = json.load(handle)
        self.assertTrue(active[created.json()["config_path"]])

    # ── 4.11 刪除限制 ──
    def test_delete_pending_start_is_conflict(self):
        created = self._create()
        bot_id = created.json()["bot_id"]
        start = self.client.post(f"/api/v1/dca/{bot_id}/start", headers=self._headers())
        self.assertEqual(start.status_code, 200, start.text)
        deleted = self.client.delete(f"/api/v1/dca/{bot_id}", headers=self._headers())
        self.assertEqual(deleted.status_code, 409, deleted.text)
        self.assertIn("只能刪除", deleted.json()["detail"])

    # ── 4.12 / 4.13 LIVE feature flag / min_notional 全階梯 ──
    def test_live_create_requires_feature_flag(self):
        with patch("routes.dca.live_trading_enabled", return_value=False):
            response = self._create(account_mode="live")
        self.assertEqual(response.status_code, 403, response.text)

    def test_live_create_checks_min_notional_every_safety_order(self):
        from schemas import MarketInfoResponse
        fake_market = MarketInfoResponse(
            symbol="ETH/USDC:USDC", exchange="binance",
            price_precision=2, quantity_precision=6,
            min_order_amount=0.001, min_notional=1000.0,
        )
        with patch("routes.dca.live_trading_enabled", return_value=True), \
             patch("routes.market._lookup_market_from_ccxt_sync", return_value=fake_market):
            response = self._create(account_mode="live", entry_price=2500.0, base_order_amount=20)
        self.assertEqual(response.status_code, 422, response.text)
        errors = response.json()["detail"]["errors"]
        self.assertTrue(any("safety_1" in e for e in errors), errors)
        self.assertTrue(any("safety_20" in e for e in errors), errors)
        self.assertTrue(any("base" in e for e in errors), errors)

    # ── 4.15 併發總量上限（網格 + DCA） ──
    def test_combined_cap_includes_grids_and_dca(self):
        from safety_guard import SafetyGuard
        os.makedirs(os.path.join(self.temp_dir.name, "config", "grid"), exist_ok=True)
        os.makedirs(os.path.join(self.temp_dir.name, "config", "dca"), exist_ok=True)
        with open(os.path.join(self.temp_dir.name, "config", "active_grids.json"), "w", encoding="utf-8") as handle:
            json.dump({f"config/grid/g{i}.yaml": True for i in range(6)}, handle)
        with open(os.path.join(self.temp_dir.name, "config", "active_dca.json"), "w", encoding="utf-8") as handle:
            json.dump({f"config/dca/d{i}.yaml": True for i in range(5)}, handle)
        guard = SafetyGuard(self.temp_dir.name)
        ok, message = guard.check_max_combined_bots()
        self.assertFalse(ok)
        self.assertIn("11", message)

        class BlockingGuard(PaperDcaGuard):
            def check_can_create_dca(self):
                return False, "已達最大同時運行策略數 (6 網格 + 5 DCA = 11/10)"

        self.dca_route.SafetyGuard = BlockingGuard
        response = self._create(auto_start=True)
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("最大", response.json()["detail"])

    # ── 4.16 寫入頻率限制 ──
    def test_create_respects_write_rate_limit(self):
        class LimitingGuard(PaperDcaGuard):
            calls = 0

            def __init__(self, _root):
                pass

            def check_rate_limit(self, _source):
                LimitingGuard.calls += 1
                return (True, None) if LimitingGuard.calls <= 1 else (False, "來源 'hermes' 已達寫操作頻率限制")

        self.dca_route.SafetyGuard = LimitingGuard
        first = self._create()
        self.assertEqual(first.status_code, 201, first.text)
        second = self._create()
        self.assertEqual(second.status_code, 429, second.text)

    # ── 4.17 / 4.18 稽核日誌與不洩密 ──
    def test_audit_logs_create_start_stop_and_no_secrets(self):
        from audit import AuditLogger
        created = self._create(metadata={"source": "hermes", "reason": "test", "token": "SECRET_VALUE"})
        self.assertEqual(created.status_code, 201, created.text)
        bot_id = created.json()["bot_id"]
        self.client.post(f"/api/v1/dca/{bot_id}/start", headers=self._headers())
        self.client.post(f"/api/v1/dca/{bot_id}/stop", headers=self._headers())

        logs = AuditLogger.get_instance(self.temp_dir.name).get_logs(limit=100)
        actions = {log["action"] for log in logs}
        self.assertIn("create_dca", actions)
        self.assertIn("start", actions)
        self.assertIn("stop", actions)
        serialized = json.dumps(logs, ensure_ascii=False)
        self.assertNotIn("SECRET_VALUE", serialized)
        self.assertNotIn("api_secret", serialized)
        self.assertNotIn("api_key", serialized)

    # ── 4.21 Pydantic 契約完整 ──
    def test_openapi_contains_dca_schemas(self):
        response = self.client.get("/openapi.json")
        self.assertEqual(response.status_code, 200, response.text)
        schemas = response.json()["components"]["schemas"]
        for name in (
            "CreateDcaRequest",
            "DcaPreviewRequest",
            "DcaPreviewResponse",
            "DcaSnapshotResponse",
            "DcaCreateResponse",
            "DcaActionResponse",
            "MarketIndicatorsResponse",
        ):
            self.assertIn(name, schemas)

    # ── 4.22 路徑穿越防護 ──
    def test_path_traversal_rejected(self):
        for bad in ("..%2Fevil", "..%5Cevil", "foo%2Fbar"):
            with self.subTest(bad=bad):
                response = self.client.get(f"/api/v1/dca/{bad}/stats", headers=self._headers())
                self.assertIn(response.status_code, (400, 404), response.text)
        from dca_yaml_generator import DcaYAMLGenerator
        self.assertFalse(DcaYAMLGenerator.is_valid_bot_id("../evil"))

    # ── 4.19 指標端點 60 秒快取 ──
    def test_indicators_endpoint_caches_fetch_ohlcv(self):
        from schemas import MarketIndicatorsResponse
        calls = {"count": 0}

        def fake_compute(symbol, exchange, timeframe, limit):
            calls["count"] += 1
            return MarketIndicatorsResponse(
                symbol=symbol, exchange=exchange, timeframe=timeframe, fetch_limit=limit,
                current_price=2500.0, rsi={"value": 30.0}, macd={"value": 0.1},
                bollinger={"value": 0.5}, price_change={"value": -3.0},
                data_status="live", cached=False,
            )

        self.market_route._indicators_cache.clear()
        with patch("routes.market._compute_indicators_sync", side_effect=fake_compute):
            first = self.client.get("/api/v1/market/indicators/ETH/USDC:USDC", headers=self._headers())
            second = self.client.get("/api/v1/market/indicators/ETH/USDC:USDC", headers=self._headers())
            third = self.client.get("/api/v1/market/indicators/ETH/USDC:USDC", headers=self._headers())
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(calls["count"], 1)
        self.assertFalse(first.json()["cached"])
        self.assertTrue(second.json()["cached"])
        self.assertTrue(third.json()["cached"])

    # ── Phase 7：詳情端點讀取引擎快照（orders / fills / ladder） ──
    def test_detail_endpoints_read_engine_runtime_snapshot(self):
        from grid_state_store import GridStateStore
        created = self._create()
        self.assertEqual(created.status_code, 201, created.text)
        bot_id = created.json()["bot_id"]
        config_path = created.json()["config_path"]

        # 引擎心跳寫入含詳情快照的 runtime
        GridStateStore.instance().update(config_path, {
            "status": "averaging",
            "current_price": 2450.0,
            "active_direction": "long",
            "position": {"quantity": 0.016, "average_cost": 2460.0, "total_cost": 39.36},
            "ladder": {"max_safety_orders": 20, "filled_safety_orders": 1,
                       "active_safety_orders": 2, "take_profit_orders": 1},
            "profit": {"realized": 0.0, "unrealized": -0.16, "fees": 0.02},
            "open_orders": [
                {"client_order_id": "dca_x_long_tp", "role": "take_profit",
                 "side": "sell", "price": 2484.6, "quantity": 0.016, "notional": 39.75},
                {"client_order_id": "dca_x_long_so02", "role": "safety",
                 "side": "buy", "price": 2420.0, "quantity": 0.008, "notional": 19.36},
            ],
            "fills": [
                {"client_order_id": "dca_x_long_base", "role": "base", "side": "buy",
                 "price": 2500.0, "quantity": 0.008, "fee": 0.008,
                 "cumulative_avg_price": 2500.0, "occurred_at": 1_700_000_000.0},
            ],
            "ladder_slots": [
                {"index": 0, "role": "base", "status": "filled",
                 "planned_price": 2500.0, "planned_quantity": 0.008},
                {"index": 1, "role": "safety", "status": "filled",
                 "planned_price": 2480.0, "planned_quantity": 0.008},
                {"index": 2, "role": "safety", "status": "active",
                 "planned_price": 2420.0, "planned_quantity": 0.008},
            ],
        })

        orders = self.client.get(f"/api/v1/dca/{bot_id}/orders", headers=self._headers())
        self.assertEqual(orders.status_code, 200, orders.text)
        obody = orders.json()
        self.assertTrue(obody["available"])
        self.assertEqual(len(obody["orders"]), 2)
        self.assertEqual(sum(1 for o in obody["orders"] if o["role"] == "take_profit"), 1)

        fills = self.client.get(f"/api/v1/dca/{bot_id}/fills", headers=self._headers())
        self.assertEqual(fills.status_code, 200, fills.text)
        fbody = fills.json()
        self.assertTrue(fbody["available"])
        self.assertEqual(fbody["fills"][0]["role"], "base")
        self.assertIn("cumulative_avg_price", fbody["fills"][0])

        ladder = self.client.get(f"/api/v1/dca/{bot_id}/ladder", headers=self._headers())
        self.assertEqual(ladder.status_code, 200, ladder.text)
        lbody = ladder.json()
        self.assertTrue(lbody["available"])
        statuses = {lv["index"]: lv["status"] for lv in lbody["levels"]}
        self.assertEqual(statuses[0], "filled")
        self.assertEqual(statuses[2], "active")

    def test_orders_endpoint_unavailable_without_runtime(self):
        created = self._create()
        bot_id = created.json()["bot_id"]
        orders = self.client.get(f"/api/v1/dca/{bot_id}/orders", headers=self._headers())
        self.assertEqual(orders.status_code, 200, orders.text)
        body = orders.json()
        self.assertFalse(body["available"])
        self.assertEqual(body["orders"], [])


if __name__ == "__main__":
    unittest.main()
