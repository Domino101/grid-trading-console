"""FastAPI-free tests for Phase A/B safety locks and control-loop helpers."""

import json
import os
import sys
import tempfile
import unittest

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../web_ui/backend"))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)


class LifecyclePolicyTests(unittest.TestCase):
    def test_paper_start_is_allowed(self):
        from lifecycle import start_rejection

        self.assertIsNone(start_rejection("paper", False))
        self.assertIsNone(start_rejection("paper", True))

    def test_live_requires_flag_unknown_is_legacy_allowed(self):
        from lifecycle import start_rejection

        self.assertIsNotNone(start_rejection("live", False))
        self.assertIsNone(start_rejection("live", True))
        self.assertIsNone(start_rejection("unknown", False))

    def test_delete_rejects_inflight_lifecycle(self):
        from lifecycle import delete_rejection

        self.assertIsNotNone(delete_rejection("running"))
        self.assertIsNotNone(delete_rejection("starting"))
        self.assertIsNotNone(delete_rejection("stopping"))
        self.assertIsNotNone(delete_rejection("stopped", "pending_engine"))
        self.assertIsNone(delete_rejection("stopped", "stopped"))

    def test_unknown_runtime_is_not_zero(self):
        from lifecycle import build_grid_read_model, finite_number

        self.assertIsNone(finite_number(None))
        self.assertIsNone(finite_number("1.0"))
        model = build_grid_read_model(
            grid_id="x",
            config_path="config/grid/x.yaml",
            symbol="BTC/USDT",
            exchange="binance",
            grid_type="neutral",
            mode="paper",
            desired_running=True,
            runtime=None,
            command=None,
        )
        self.assertEqual(model["status"], "starting")
        self.assertEqual(model["data_status"], "unavailable")
        self.assertIsNone(model["net_pnl"])
        self.assertIsNone(model["equity"])
        self.assertIsNone(model["start_blocked_reason"])

    def test_heartbeat_not_desired_state_makes_running(self):
        from lifecycle import build_grid_read_model

        model = build_grid_read_model(
            grid_id="x",
            config_path="config/grid/x.yaml",
            symbol="BTC/USDT",
            exchange="binance",
            grid_type="neutral",
            mode="unknown",
            desired_running=True,
            runtime={"stale": False, "current_price": 100.0, "profit": {"net": 1.5}},
            command={"command_id": "c1", "action": "start", "status": "running"},
        )
        self.assertEqual(model["status"], "running")
        self.assertEqual(model["net_pnl"], 1.5)

    def test_cors_star_disables_credentials(self):
        from lifecycle import cors_settings

        origins, credentials = cors_settings("*")
        self.assertEqual(origins, ["*"])
        self.assertFalse(credentials)
        default_origins, default_credentials = cors_settings("")
        self.assertTrue(default_credentials)
        self.assertIn("http://127.0.0.1:5173", default_origins)

    def test_registered_paths_skip_archived_and_non_yaml(self):
        from lifecycle import registered_config_paths

        paths = registered_config_paths({
            "config/grid/a.yaml": True,
            "config/grid/archived/old.yaml": False,
            "archived/x.yaml": True,
            "notes.txt": True,
            "config/grid/b.yaml": False,
        })
        self.assertEqual(paths, ["config/grid/a.yaml", "config/grid/b.yaml"])

    def test_hotplug_accepts_new_yaml_outside_startup_list(self):
        from lifecycle import iter_hotplug_starts

        starts = iter_hotplug_starts(
            {"config/grid/new.yaml": True, "config/grid/paper.yaml": True},
            [],
            lambda path: True,
            lambda path: "paper" if "paper" in path else "unknown",
            live_enabled=False,
        )
        self.assertEqual(starts, ["config/grid/new.yaml", "config/grid/paper.yaml"])

    def test_live_and_paper_aggregates_are_not_mixed(self):
        from lifecycle import aggregate_read_models

        rolled = aggregate_read_models([
            {"mode": "live", "status": "running", "data_status": "live", "source": "engine_runtime", "realized_pnl": 10, "unrealized_pnl": 0, "fees": 1, "volume": 100, "completed_cycles": 1},
            {"mode": "paper", "status": "running", "data_status": "live", "source": "engine_runtime", "realized_pnl": 999, "unrealized_pnl": 0, "fees": 0, "volume": 1, "completed_cycles": 4},
        ])
        self.assertEqual(rolled["account_mode"], "mixed")
        self.assertEqual(rolled["aggregate"]["realized_pnl"], 10)
        self.assertEqual(rolled["aggregate_paper"]["realized_pnl"], 999)
        self.assertEqual(rolled["aggregate_live"]["net_pnl"], 9)

    def test_atomic_json_replace(self):
        from lifecycle import write_json_atomic

        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "active_grids.json")
            write_json_atomic(path, {"a": True})
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(json.load(handle), {"a": True})


class FileManagerSafetyTests(unittest.TestCase):
    def test_exchange_configs_are_blocked(self):
        from file_manager import FileManager

        manager = FileManager()
        with self.assertRaises(PermissionError):
            manager._safe_config_path("config/exchanges/binance_config.yaml")

    def test_path_escape_is_blocked(self):
        from file_manager import FileManager

        manager = FileManager()
        with self.assertRaises(ValueError):
            manager._safe_config_path("config/grid/../../.env")


class CommandIdempotencyTests(unittest.TestCase):
    def test_idempotency_is_scoped_to_grid(self):
        from command_repository import GridCommandRepository

        with tempfile.TemporaryDirectory() as temp_dir:
            repository = GridCommandRepository(os.path.join(temp_dir, "runtime.sqlite3"))
            first = repository.create("grid-a", "config/grid/a.yaml", "start", "dashboard", idempotency_key="same")
            second = repository.create("grid-b", "config/grid/b.yaml", "start", "dashboard", idempotency_key="same")
            self.assertNotEqual(first["command_id"], second["command_id"])
            self.assertEqual(
                repository.get_by_idempotency_key("start", "same", grid_id="grid-a")["command_id"],
                first["command_id"],
            )


class ControlPlanePersistenceTests(unittest.TestCase):
    def test_engine_publish_promotes_pending_start(self):
        from command_repository import GridCommandRepository
        from control_plane import publish_lifecycle, publish_runtime
        from runtime_repository import RuntimeSnapshotRepository

        with tempfile.TemporaryDirectory() as temp_dir:
            database = os.path.join(temp_dir, "runtime.sqlite3")
            RuntimeSnapshotRepository._instance = RuntimeSnapshotRepository(database)
            GridCommandRepository._instance = GridCommandRepository(database)
            command = GridCommandRepository.instance().create(
                "paper-btc-usdt", "config/grid/paper-btc-usdt.yaml", "start", "dashboard"
            )
            GridCommandRepository.instance().update_status(command["command_id"], "pending_engine")
            publish_lifecycle("config/grid/paper-btc-usdt.yaml", "running")
            publish_runtime("config/grid/paper-btc-usdt.yaml", {"current_price": 65000, "profit": {"net": 2.0}})
            self.assertEqual(GridCommandRepository.instance().get(command["command_id"])["status"], "running")
            restored = RuntimeSnapshotRepository.instance().load_all()
            self.assertEqual(restored["config/grid/paper-btc-usdt.yaml"][0]["profit"]["net"], 2.0)
            RuntimeSnapshotRepository._instance = None
            GridCommandRepository._instance = None


if __name__ == "__main__":
    unittest.main()
