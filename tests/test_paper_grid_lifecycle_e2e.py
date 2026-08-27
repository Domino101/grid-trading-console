"""Paper lifecycle acceptance test without a live exchange or FastAPI server.

It exercises the durable boundary used by the API/engine integration:
create audit -> start command -> engine confirmation -> runtime persistence ->
stop confirmation -> audit history.  A real deployment additionally runs the
same flow through HTTP and the exchange adapter as documented in the release
checklist.
"""

import os
import sys
import tempfile
import unittest

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../web_ui/backend"))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)


class PaperGridLifecycleE2E(unittest.TestCase):
    def test_paper_create_start_stop_and_restart_recovery(self):
        from web_ui.backend.audit import AuditLogger
        from web_ui.backend.command_repository import GridCommandRepository
        from web_ui.backend.runtime_repository import RuntimeSnapshotRepository

        grid_id = "paper-btc-usdt"
        config_path = f"config/grid/{grid_id}.yaml"
        with tempfile.TemporaryDirectory() as root:
            database_path = os.path.join(root, "data", "runtime.sqlite3")
            runtime = RuntimeSnapshotRepository(database_path)
            commands = GridCommandRepository(database_path)
            audit = AuditLogger(root)

            audit.log("dashboard", "create_grid", grid_id, {"account_mode": "paper"}, "success")
            start = commands.create(grid_id, config_path, "start", "dashboard", "paper acceptance")
            commands.update_status(start["command_id"], "pending_engine")
            commands.record_event(grid_id, "engine_lifecycle", "running")
            commands.update_status(start["command_id"], "running")
            runtime.save(config_path, {"current_price": 65000, "profit": {"net": 4.2}}, updated_at=100.0)
            audit.log("dashboard", "start", grid_id, {"command_id": start["command_id"]}, "success")

            # Simulate a Web UI/API restart: a fresh repository reads the same durable data.
            recovered = RuntimeSnapshotRepository(database_path)
            self.assertEqual(recovered.load_all()[config_path][0]["profit"]["net"], 4.2)
            self.assertEqual(commands.get(start["command_id"])["status"], "running")

            stop = commands.create(grid_id, config_path, "stop", "dashboard", "paper acceptance")
            commands.update_status(stop["command_id"], "pending_engine")
            commands.record_event(grid_id, "engine_lifecycle", "stopped")
            commands.update_status(stop["command_id"], "stopped")
            audit.log("dashboard", "stop", grid_id, {"command_id": stop["command_id"]}, "success")

            self.assertEqual(commands.get(stop["command_id"])["status"], "stopped")
            self.assertEqual(commands.list_events(grid_id)[0]["status"], "stopped")
            self.assertEqual([entry["action"] for entry in audit.get_logs(limit=10)], ["stop", "start", "create_grid"])


if __name__ == "__main__":
    unittest.main()
