"""DCA batch snapshot tests (Phase 4 audit 4.5 / 4.6 / 4.7)."""

import importlib.util
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND = os.path.join(PROJECT_ROOT, "web_ui", "backend")
for path in (BACKEND, PROJECT_ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)

HAS_FASTAPI = importlib.util.find_spec("fastapi") is not None


def _yaml(symbol, direction="long", account_mode="paper", base_amount="20", max_safety=20):
    return f"""dca:
  exchange: binance
  symbol: {symbol}
  direction: {direction}
  account_mode: {account_mode}
  base_order_amount: "{base_amount}"
  max_safety_orders: {max_safety}
  price_deviation_percent: "0.8"
  price_deviation_multiplier: "1.2"
  order_size_multiplier: "1.0"
  take_profit_percent: "1.0"
  entry:
    mode: immediate
    indicators: []
"""


@unittest.skipUnless(HAS_FASTAPI, "FastAPI installed from requirements.txt in CI/release environments")
class DcaSnapshotTests(unittest.TestCase):
    def setUp(self):
        import asyncio
        from web_ui.backend.command_repository import GridCommandRepository
        from web_ui.backend.grid_state_store import GridStateStore
        from web_ui.backend.runtime_repository import RuntimeSnapshotRepository
        from web_ui.backend.routes import dca as dca_route

        self.temp_dir = tempfile.TemporaryDirectory()
        os.makedirs(os.path.join(self.temp_dir.name, "config", "dca"))
        database_path = os.path.join(self.temp_dir.name, "runtime.sqlite3")
        self.repository = RuntimeSnapshotRepository(database_path)
        RuntimeSnapshotRepository._instance = self.repository
        GridCommandRepository._instance = GridCommandRepository(database_path)
        GridStateStore._instance = None
        self.store = GridStateStore.instance()
        self.dca_route = dca_route
        dca_route._get_project_root = lambda: self.temp_dir.name
        self.asyncio = asyncio

    def tearDown(self):
        self.temp_dir.cleanup()

    def _write_yaml(self, name: str, content: str) -> str:
        path = os.path.join(self.temp_dir.name, "config", "dca", name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        return f"config/dca/{name}"

    def _snapshot(self, active: dict):
        from web_ui.backend.routes.dca import get_dca_snapshot

        with patch("web_ui.backend.routes.dca._read_active_dca", return_value=active), \
             patch("web_ui.backend.routes.dca._read_dca_yaml", autospec=True, side_effect=lambda p: self._read_yaml(p)):
            return self.asyncio.run(get_dca_snapshot("test-token"))

    def _read_yaml(self, config_path: str):
        import yaml
        path = os.path.join(self.temp_dir.name, config_path)
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as handle:
            return yaml.safe_load(handle)

    def test_snapshot_loads_repository_at_most_once_per_pass(self):
        paper = self._write_yaml("dca-paper.yaml", _yaml("PAPER/USDT"))
        self.store.update(paper, {
            "status": "averaging",
            "current_price": 10.0,
            "position": {"quantity": 1.0, "average_cost": 9.0, "total_cost": 9.0},
            "ladder": {"max_safety_orders": 20, "filled_safety_orders": 3, "active_safety_orders": 2, "take_profit_orders": 1},
            "cycles": 1,
            "profit": {"realized": 1.0, "unrealized": 0.5, "fees": 0.1},
        })
        original = self.store._repository.load_all
        calls = {"n": 0}

        def counting_load_all():
            calls["n"] += 1
            return original()

        self.store._repository.load_all = counting_load_all
        self.store._last_refresh = 0.0
        snapshot = self._snapshot({paper: True})
        # 稽核 4.5：一次批次快照不得逐台 bot 重複 load_all。
        self.assertLessEqual(calls["n"], 2)
        self.assertEqual(len(snapshot.bots), 1)
        self.assertEqual(snapshot.bots[0].bot_id, "dca-paper")

    def test_missing_runtime_is_unavailable_not_zero(self):
        paper = self._write_yaml("dca-norruntime.yaml", _yaml("NORUN/USDT"))
        snapshot = self._snapshot({paper: True})

        self.assertEqual(snapshot.data_status, "unavailable")
        item = snapshot.bots[0]
        self.assertEqual(item.data_status, "unavailable")
        self.assertIsNone(item.current_price)
        self.assertIsNone(item.position.quantity)
        self.assertIsNone(item.position.average_cost)
        self.assertIsNone(item.profit.unrealized)
        self.assertTrue(item.desired_running)

    def test_live_and_paper_not_mixed_in_headline_aggregate(self):
        live = self._write_yaml("dca-live.yaml", _yaml("LIVE/USDT", account_mode="live"))
        paper = self._write_yaml("dca-paper2.yaml", _yaml("PAPER2/USDT", account_mode="paper"))
        self.store.update(live, {
            "status": "averaging",
            "current_price": 100.0,
            "profit": {"realized": 10.0, "unrealized": 1.0, "fees": 0.1},
        })
        self.store.update(paper, {
            "status": "averaging",
            "current_price": 20.0,
            "profit": {"realized": 99.0, "unrealized": 2.0, "fees": 0.2},
        })
        snapshot = self._snapshot({live: True, paper: True})

        self.assertEqual(snapshot.account_mode, "mixed")
        # headline KPI 只含 LIVE，PAPER 不混入
        self.assertEqual(snapshot.aggregate.realized_pnl, 10.0)
        self.assertEqual(snapshot.aggregate_paper.realized_pnl, 99.0)
        self.assertEqual(snapshot.aggregate_live.realized_pnl, 10.0)


if __name__ == "__main__":
    unittest.main()
