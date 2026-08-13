"""Contract tests for the dashboard batch read model."""

import asyncio
import importlib.util
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../web_ui/backend"))
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for path in (BACKEND_DIR, PROJECT_ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)

HAS_FASTAPI = importlib.util.find_spec("fastapi") is not None


@unittest.skipUnless(HAS_FASTAPI, "FastAPI is installed from requirements.txt in CI/release environments")
class DashboardSnapshotTests(unittest.TestCase):
    def setUp(self):
        from command_repository import GridCommandRepository
        from grid_state_store import GridStateStore
        from runtime_repository import RuntimeSnapshotRepository

        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = os.path.join(self.temp_dir.name, "runtime.sqlite3")
        RuntimeSnapshotRepository._instance = RuntimeSnapshotRepository(database_path)
        GridCommandRepository._instance = GridCommandRepository(database_path)
        GridStateStore._instance = None
        self.store = GridStateStore.instance()
        os.makedirs(os.path.join(self.temp_dir.name, "config", "grid"))
        self.config_path = "config/grid/binance-neutral-btc-usdt.yaml"
        with open(os.path.join(self.temp_dir.name, self.config_path), "w", encoding="utf-8") as handle:
            handle.write("""grid_system:\n  symbol: BTC/USDT\n  exchange: binance\n  grid_type: neutral\n  account_mode: paper\n  grid_interval: 100\n  order_amount: 0.001\n  price_range:\n    lower_price: 60000\n    upper_price: 70000\n""")

    def tearDown(self):
        self.temp_dir.cleanup()

    def _snapshot(self, active=True):
        from routes.dashboard import get_dashboard_snapshot

        with patch("routes.dashboard._get_project_root", return_value=self.temp_dir.name), patch(
            "routes.dashboard._read_active_grids", return_value={self.config_path: active}
        ), patch("routes.grids._get_project_root", return_value=self.temp_dir.name):
            return asyncio.run(get_dashboard_snapshot("test-token"))

    def test_missing_runtime_is_unavailable_not_zero(self):
        snapshot = self._snapshot()

        self.assertEqual(snapshot.data_status, "degraded")
        self.assertEqual(snapshot.aggregate.running_grids, 0)
        item = snapshot.grids[0]
        self.assertEqual(item.status, "starting")
        self.assertEqual(item.mode, "paper")
        self.assertEqual(item.data_status, "unavailable")
        self.assertIsNone(item.current_price)
        self.assertIsNone(item.net_pnl)
        self.assertIsNone(item.equity)

    def test_known_runtime_is_aggregated_in_one_snapshot(self):
        self.store.update(self.config_path, {
            "current_price": 65000.0,
            "profit": {"realized": 12.5, "unrealized": -1.0, "fees": 0.5, "net": 11.0, "rate": 1.1},
            "balance": {"total": 1011.0},
            "filled_orders": {"buy": 3, "sell": 2, "cycles": 2},
            "pending_orders": {"total": 4},
            "volume": 2500.0,
        })
        snapshot = self._snapshot()

        self.assertEqual(snapshot.data_status, "live")
        self.assertEqual(snapshot.aggregate.net_pnl, 11.0)
        self.assertEqual(snapshot.aggregate.volume, 2500.0)
        self.assertEqual(snapshot.grids[0].data_status, "live")
        self.assertEqual(snapshot.grids[0].completed_cycles, 2)


if __name__ == "__main__":
    unittest.main()
