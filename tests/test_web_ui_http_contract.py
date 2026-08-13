"""HTTP contract smoke tests for the authenticated Web UI API.

The test uses only a temporary SQLite repository and does not touch exchanges.
It is skipped in minimal tooling environments that have not installed
requirements.txt; CI/release environments must run it with FastAPI available.
"""

import importlib.util
import os
import sys
import tempfile
import unittest

HAS_FASTAPI = importlib.util.find_spec("fastapi") is not None
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND = os.path.join(ROOT, "web_ui", "backend")
for path in (BACKEND, ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)


@unittest.skipUnless(HAS_FASTAPI, "FastAPI is installed from requirements.txt in CI/release environments")
class WebUiHttpContractTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        import auth
        from audit import AuditLogger
        from command_repository import GridCommandRepository
        from grid_state_store import GridStateStore
        from runtime_repository import RuntimeSnapshotRepository
        import routes.dashboard as dashboard_route
        import routes.grids as grids_route

        self.temp_dir = tempfile.TemporaryDirectory()
        os.makedirs(os.path.join(self.temp_dir.name, "config", "grid"))
        database_path = os.path.join(self.temp_dir.name, "runtime.sqlite3")
        RuntimeSnapshotRepository._instance = RuntimeSnapshotRepository(database_path)
        GridCommandRepository._instance = GridCommandRepository(database_path)
        GridStateStore._instance = None
        AuditLogger._instance = AuditLogger(self.temp_dir.name)
        auth._API_TOKEN = "contract-token"
        self.grids_route = grids_route
        self.dashboard_route = dashboard_route

        class PaperSafetyGuard:
            def __init__(self, _project_root): pass
            def check_rate_limit(self, _source): return True, None
            def check_can_create_grid(self): return True, None
            def check_config_dir_writable(self): return True, None
            def check_min_investment(self, _params): return True, None
            def check_duplicate_symbol(self, _symbol): return True, None
            def check_config_not_exists(self, _path): return True, None
            def record_action(self, _source): pass

        # This contract test deliberately isolates filesystem/engine guards;
        # their production behavior is covered by safety-guard unit tests.
        grids_route._get_project_root = lambda: self.temp_dir.name
        grids_route.SafetyGuard = PaperSafetyGuard
        dashboard_route._get_project_root = lambda: self.temp_dir.name
        dashboard_route._read_active_grids = grids_route._read_active_grids
        dashboard_route._read_yaml_config = grids_route._read_yaml_config
        dashboard_route._build_grid_info = grids_route._build_grid_info

        # Import after dependency singletons have been isolated for this test.
        import main
        self.client = TestClient(main.app)
        self.repository = GridCommandRepository.instance()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_readiness_probe_is_public_but_commands_require_auth(self):
        health = self.client.get("/healthz")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json(), {"status": "ok"})

        denied = self.client.get("/api/v1/commands?grid_id=paper-grid")
        self.assertIn(denied.status_code, (401, 403))

    def test_legacy_process_and_config_routes_require_auth(self):
        denied = self.client.get("/api/processes")
        self.assertIn(denied.status_code, (401, 403))
        secret = self.client.get("/api/config/config/exchanges/binance_config.yaml")
        self.assertIn(secret.status_code, (401, 403))

    def test_paper_start_is_accepted_as_pending_engine(self):
        headers = {"Authorization": "Bearer contract-token"}
        created = self.client.post("/api/v1/grids", headers=headers, json={
            "symbol": "ETH/USDT", "exchange": "binance", "grid_type": "neutral",
            "lower_price": 2000, "upper_price": 3000, "grid_interval": 50,
            "order_amount": 0.01, "account_mode": "paper", "auto_start": False,
        })
        self.assertEqual(created.status_code, 201, created.text)
        grid_id = created.json()["grid_id"]
        started = self.client.post(f"/api/v1/grids/{grid_id}/start", headers=headers)
        self.assertEqual(started.status_code, 200)
        self.assertEqual(started.json()["status"], "pending_engine")

    def test_paper_create_contract_writes_mode_tagged_yaml(self):
        response = self.client.post("/api/v1/grids", headers={"Authorization": "Bearer contract-token"}, json={
            "symbol": "BTC/USDT", "exchange": "binance", "grid_type": "neutral",
            "lower_price": 60000, "upper_price": 70000, "grid_interval": 100,
            "order_amount": 0.001, "account_mode": "paper", "auto_start": False,
        })
        self.assertEqual(response.status_code, 201, response.text)
        payload = response.json()
        with open(os.path.join(self.temp_dir.name, payload["config_path"]), encoding="utf-8") as handle:
            self.assertIn("account_mode: paper", handle.read())

    def test_command_lookup_and_event_timeline_contract(self):
        command = self.repository.create("paper-grid", "config/grid/paper-grid.yaml", "start", "dashboard")
        self.repository.update_status(command["command_id"], "running")
        self.repository.record_event("paper-grid", "engine_lifecycle", "running")
        headers = {"Authorization": "Bearer contract-token"}

        detail = self.client.get(f"/api/v1/commands/{command['command_id']}", headers=headers)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["status"], "running")

        history = self.client.get("/api/v1/commands?grid_id=paper-grid", headers=headers)
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.json()["commands"][0]["command_id"], command["command_id"])

        events = self.client.get("/api/v1/commands/events?grid_id=paper-grid", headers=headers)
        self.assertEqual(events.status_code, 200)
        self.assertEqual(events.json()["events"][0]["status"], "running")

    def test_snapshot_nulls_orders_unavailable_and_hides_unregistered(self):
        headers = {"Authorization": "Bearer contract-token"}
        created = self.client.post("/api/v1/grids", headers=headers, json={
            "symbol": "SOL/USDT", "exchange": "binance", "grid_type": "neutral",
            "lower_price": 100, "upper_price": 200, "grid_interval": 5,
            "order_amount": 0.1, "account_mode": "paper", "auto_start": False,
        })
        self.assertEqual(created.status_code, 201, created.text)
        grid_id = created.json()["grid_id"]
        with open(os.path.join(self.temp_dir.name, "config", "grid", "orphan.yaml"), "w", encoding="utf-8") as handle:
            handle.write("grid_system:\n  symbol: ORPHAN/USDT\n  exchange: binance\n")

        snapshot = self.client.get("/api/v1/dashboard/snapshot", headers=headers)
        self.assertEqual(snapshot.status_code, 200)
        payload = snapshot.json()
        self.assertEqual([item["grid_id"] for item in payload["grids"]], [grid_id])
        item = payload["grids"][0]
        self.assertIsNone(item["net_pnl"])
        self.assertIsNone(item["equity"])
        self.assertEqual(item["data_status"], "unavailable")

        orders = self.client.get(f"/api/v1/grids/{grid_id}/orders", headers=headers)
        self.assertEqual(orders.status_code, 200)
        self.assertFalse(orders.json()["available"])
        self.assertIn("引擎尚未提供", orders.json()["message"])

        fills = self.client.get(f"/api/v1/grids/{grid_id}/fills", headers=headers)
        self.assertEqual(fills.status_code, 200)
        self.assertFalse(fills.json()["available"])
        self.assertIn("引擎尚未提供", fills.json()["message"])

    def test_delete_starting_strategy_is_conflict(self):
        headers = {"Authorization": "Bearer contract-token"}
        created = self.client.post("/api/v1/grids", headers=headers, json={
            "symbol": "ADA/USDT", "exchange": "binance", "grid_type": "neutral",
            "lower_price": 0.4, "upper_price": 0.8, "grid_interval": 0.01,
            "order_amount": 10, "account_mode": "paper", "auto_start": False,
        })
        self.assertEqual(created.status_code, 201, created.text)
        grid_id = created.json()["grid_id"]
        started = self.client.post(f"/api/v1/grids/{grid_id}/start", headers=headers)
        self.assertEqual(started.status_code, 200)
        deleted = self.client.delete(f"/api/v1/grids/{grid_id}", headers=headers)
        self.assertEqual(deleted.status_code, 409)

    def test_live_start_without_flag_is_rejected(self):
        config_path = os.path.join(self.temp_dir.name, "config", "grid", "live-btc.yaml")
        with open(config_path, "w", encoding="utf-8") as handle:
            handle.write(
                "grid_system:\n  symbol: BTC/USDT\n  exchange: binance\n"
                "  account_mode: live\n  grid_type: neutral\n"
            )
        started = self.client.post(
            "/api/v1/grids/live-btc/start",
            headers={"Authorization": "Bearer contract-token"},
        )
        self.assertEqual(started.status_code, 409)
        self.assertIn("GRID_LIVE_TRADING_ENABLED", started.json()["detail"])


if __name__ == "__main__":
    unittest.main()
