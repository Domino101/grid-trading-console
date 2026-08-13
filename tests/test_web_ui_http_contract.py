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
        from command_repository import GridCommandRepository
        from runtime_repository import RuntimeSnapshotRepository

        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = os.path.join(self.temp_dir.name, "runtime.sqlite3")
        RuntimeSnapshotRepository._instance = RuntimeSnapshotRepository(database_path)
        GridCommandRepository._instance = GridCommandRepository(database_path)
        auth._API_TOKEN = "contract-token"

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


if __name__ == "__main__":
    unittest.main()
