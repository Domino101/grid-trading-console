"""
Hermes Grid API — Phase 4 Hermes Client & Integration Test Suite

This test file verifies:
1. Bearer Token authorization handling in HermesGridClient.
2. Under-the-hood routing to the real FastAPI server in-process.
3. System Health checks, Grid Creation, Stop actions, and P&L Summaries.
4. HermesGridController monitor loop triggers and Custom Rule decisions.

Usage:
    python3 -m unittest tests/test_hermes_client.py
"""

import os
import sys
import unittest
import asyncio
import builtins
from unittest.mock import patch, MagicMock, AsyncMock
import httpx

# Ensure project root and web_ui/backend is in sys.path
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_backend_dir = os.path.join(_project_root, "web_ui", "backend")
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from web_ui.backend.main import app
from web_ui.backend import auth
from tools.hermes_grid_client import HermesGridClient, HermesGridController, HermesDecision

# Ensure token is mock-configured
TEST_TOKEN = "test-phase4-hermes-token-abc123xyz"


class TestHermesGridClient(unittest.IsolatedAsyncioTestCase):
    """Integration tests for HermesGridClient using in-process FastAPI AsyncClient routing"""

    async def asyncSetUp(self):
        # Set mock API token on auth module
        self.original_api_token = auth._API_TOKEN
        auth._API_TOKEN = TEST_TOKEN
        
        # Setup real async client linked directly to our FastAPI app
        if hasattr(httpx, "ASGITransport"):
            self.async_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost:8001")
        else:
            self.async_client = httpx.AsyncClient(app=app, base_url="http://localhost:8001")
        
        # Patch httpx.AsyncClient inside tools.hermes_grid_client to return our in-process async_client
        self.patcher = patch("tools.hermes_grid_client.httpx.AsyncClient", return_value=self.async_client)
        self.mock_client_class = self.patcher.start()

        # Initialize our client
        self.client = HermesGridClient(base_url="http://localhost:8001", token=TEST_TOKEN)

    async def asyncTearDown(self):
        # Restore mock patch and token
        self.patcher.stop()
        await self.async_client.aclose()
        auth._API_TOKEN = self.original_api_token

    async def test_is_system_ready_healthy(self):
        """Verify is_system_ready returns True when FastAPI health endpoint is OK"""
        from web_ui.backend.safety_guard import SafetyGuard
        
        original_get_engine_pid = SafetyGuard.get_engine_pid
        SafetyGuard.get_engine_pid = lambda *args: 12345  # simulate engine running
        
        # Patch BinanceRest adapter circuit status and active grids
        with patch("core.adapters.exchanges.adapters.binance_rest.BinanceRest") as mock_binance_rest, \
             patch("web_ui.backend.safety_guard.SafetyGuard._read_active_grids", return_value={}):
            mock_binance_rest._is_adapter_circuit_open.return_value = False
            mock_binance_rest.get_throttle_stats.return_value = {}
            
            ready = await self.client.is_system_ready()
            self.assertTrue(ready)
            
        SafetyGuard.get_engine_pid = original_get_engine_pid

    async def test_create_neutral_grid_success(self):
        """Verify create_neutral_grid makes a POST to grids endpoint with valid inputs"""
        from web_ui.backend.routes import grids as grids_route
        from web_ui.backend.safety_guard import SafetyGuard
        
        # Bypass safety guards since we are running offline
        original_check_rate_limit = SafetyGuard.check_rate_limit
        original_check_can_create = SafetyGuard.check_can_create_grid
        original_check_min_invest = SafetyGuard.check_min_investment
        original_check_duplicate = SafetyGuard.check_duplicate_symbol
        original_check_config_not_exists = SafetyGuard.check_config_not_exists
        
        SafetyGuard.check_rate_limit = lambda *args: (True, None)
        SafetyGuard.check_can_create_grid = lambda *args: (True, None)
        SafetyGuard.check_min_investment = lambda *args: (True, None)
        SafetyGuard.check_duplicate_symbol = lambda *args, **kwargs: (True, None)
        SafetyGuard.check_config_not_exists = lambda *args, **kwargs: (True, None)
        
        # Mock file operations so no actual YAML or JSON files are written to disk
        original_write_yaml = grids_route.GridYAMLGenerator.generate
        original_write_active = grids_route._write_active_grids
        original_read_active = grids_route._read_active_grids
        
        grids_route.GridYAMLGenerator.generate = lambda *args, **kwargs: ("grid_system: {}", "config/grid/binance-neutral-perp-sol-usdc-120-180.yaml")
        grids_route._write_active_grids = lambda *args, **kwargs: None
        grids_route._read_active_grids = lambda: {}

        # Selectively patch open to avoid interrupting active_grids.json read
        original_open = builtins.open
        def mock_open_func(file, *args, **kwargs):
            if "config/grid" in str(file) and ("w" in args or kwargs.get("mode") == "w"):
                # Mock context manager
                mock_file = MagicMock()
                mock_context = MagicMock()
                mock_context.__enter__.return_value = mock_file
                return mock_context
            return original_open(file, *args, **kwargs)

        with patch("builtins.open", mock_open_func):
            try:
                res = await self.client.create_neutral_grid(
                    symbol="SOL/USDC:USDC",
                    lower=120.0,
                    upper=180.0,
                    interval=0.5,
                    amount=0.1
                )
                self.assertEqual(res["grid_id"], "binance-neutral-perp-sol-usdc-120-180")
                self.assertIn("status", res)
            finally:
                SafetyGuard.check_rate_limit = original_check_rate_limit
                SafetyGuard.check_can_create_grid = original_check_can_create
                SafetyGuard.check_min_investment = original_check_min_invest
                SafetyGuard.check_duplicate_symbol = original_check_duplicate
                SafetyGuard.check_config_not_exists = original_check_config_not_exists
                grids_route.GridYAMLGenerator.generate = original_write_yaml
                grids_route._write_active_grids = original_write_active
                grids_route._read_active_grids = original_read_active

    async def test_stop_grid_success(self):
        """Verify stop_grid posts request to stop endpoint"""
        from web_ui.backend.routes import grids as grids_route
        from web_ui.backend.safety_guard import SafetyGuard
        
        # Stub the active_grids.json lookup
        original_read_active = grids_route._read_active_grids
        original_write_active = grids_route._write_active_grids
        original_check_rate_limit = SafetyGuard.check_rate_limit
        
        grids_route._read_active_grids = lambda: {"config/grid/binance-neutral-perp-sol-usdc-120-180.yaml": True}
        grids_route._write_active_grids = lambda *args: None
        SafetyGuard.check_rate_limit = lambda *args: (True, None)

        try:
            res = await self.client.stop_grid("binance-neutral-perp-sol-usdc-120-180")
            self.assertIn(res["status"], ["stopped", "pending_engine"])
            self.assertEqual(res["action"], "stop")
        finally:
            grids_route._read_active_grids = original_read_active
            grids_route._write_active_grids = original_write_active
            SafetyGuard.check_rate_limit = original_check_rate_limit

    async def test_get_pnl_summary_success(self):
        """Verify get_pnl_summary aggregates active grid state correctly"""
        from web_ui.backend.routes import grids as grids_route
        from web_ui.backend.grid_state_store import GridStateStore
        from web_ui.backend.runtime_repository import RuntimeSnapshotRepository

        RuntimeSnapshotRepository.reset_instance()
        GridStateStore.reset_instance()
        store = GridStateStore.instance()
        with store._lock:
            store._states.clear()
            store._updated_at.clear()

        original_read_active = grids_route._read_active_grids
        grids_route._read_active_grids = lambda: {"config/grid/binance-neutral-perp-sol-usdc-120-180.yaml": True}

        try:
            summary = await self.client.get_pnl_summary()
            self.assertGreaterEqual(summary["total_grids"], 1)
            self.assertGreaterEqual(summary["active_grids"], 1)
            grid_ids = [g["grid_id"] for g in summary["grids"]]
            self.assertIn("binance-neutral-perp-sol-usdc-120-180", grid_ids)
        finally:
            grids_route._read_active_grids = original_read_active


class TestHermesGridController(unittest.IsolatedAsyncioTestCase):
    """Tests for the HermesGridController monitoring loops and AI decision routing"""

    @patch("tools.hermes_grid_client.HermesGridClient")
    async def test_monitor_loop_execution_flow(self, mock_client_class):
        """Test one complete iteration of the monitor loop under the Controller"""
        mock_client = mock_client_class.return_value
        
        # Configure mock responses
        mock_client.is_system_ready = AsyncMock(return_value=True)
        mock_client.get_pnl_summary = AsyncMock(return_value={"total_grids": 1, "active_grids": 1, "grids": []})
        mock_client.create_neutral_grid = AsyncMock(return_value={"grid_id": "mock-grid", "status": "created_and_started"})
        mock_client.stop_grid = AsyncMock(return_value={"grid_id": "stop-grid", "status": "stopped"})

        controller = HermesGridController("http://localhost:8001", "mock-token")
        
        # Override ai_analyze to mock specific decisions
        async def mock_ai_analyze(summary):
            return [
                HermesDecision(action="create", params={"symbol": "BTC/USDC", "lower": 60000.0, "upper": 70000.0, "interval": 1.0, "amount": 0.001}),
                HermesDecision(action="stop", grid_id="binance-neutral-perp-sol-usdc-120-180")
            ]
        controller.ai_analyze = mock_ai_analyze

        # We will run monitor_loop once and break out of the loop using a custom mock sleep that raises a control exception
        class StopLoopException(Exception):
            pass

        with patch("asyncio.sleep", AsyncMock(side_effect=StopLoopException("Break Loop"))):
            with self.assertRaises(StopLoopException):
                await controller.monitor_loop()

        # Assert all interactions were correctly executed programmatically
        mock_client.is_system_ready.assert_called_once()
        mock_client.get_pnl_summary.assert_called_once()
        mock_client.create_neutral_grid.assert_called_once_with(
            symbol="BTC/USDC", lower=60000.0, upper=70000.0, interval=1.0, amount=0.001
        )
        mock_client.stop_grid.assert_called_once_with("binance-neutral-perp-sol-usdc-120-180")


if __name__ == "__main__":
    unittest.main()
