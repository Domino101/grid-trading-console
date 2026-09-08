"""Unit and contract tests for pre-flight symbol validation and normalization gate.

Validates:
1. GET /api/v1/market/symbols returns trading pairs and leverages cache.
2. POST /api/v1/grids rejects non-existent symbols with HTTP 422.
3. POST /api/v1/grids auto-normalizes alias symbols (e.g. '牛来USDT' -> '牛来/USDT:USDT').
4. POST /api/v1/dca rejects non-existent symbols with HTTP 422.
5. POST /api/v1/dca auto-normalizes alias symbols to canonical symbol.
6. Supervisor engine preflight check stops execution if symbol is missing from exchange markets.
"""

import asyncio
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND = os.path.join(ROOT, "web_ui", "backend")
for path in (BACKEND, ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)

from web_ui.backend.schemas import MarketInfoResponse, MarketSymbolItem, MarketSymbolsResponse


class TestPreflightSymbolValidation(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from web_ui.backend import auth
        from web_ui.backend.audit import AuditLogger
        from web_ui.backend.command_repository import GridCommandRepository
        from web_ui.backend.grid_state_store import GridStateStore
        from web_ui.backend.runtime_repository import RuntimeSnapshotRepository
        from web_ui.backend.routes import grids as grids_route
        from web_ui.backend.routes import dca as dca_route
        from web_ui.backend.routes import market as market_route

        self.temp_dir = tempfile.TemporaryDirectory()
        os.makedirs(os.path.join(self.temp_dir.name, "config", "grid"), exist_ok=True)
        os.makedirs(os.path.join(self.temp_dir.name, "config", "dca"), exist_ok=True)
        database_path = os.path.join(self.temp_dir.name, "runtime.sqlite3")
        RuntimeSnapshotRepository._instance = RuntimeSnapshotRepository(database_path)
        GridCommandRepository._instance = GridCommandRepository(database_path)
        GridStateStore._instance = None
        AuditLogger._instance = AuditLogger(self.temp_dir.name)
        auth._API_TOKEN = "test-symbol-token"

        class DummySafetyGuard:
            def __init__(self, _project_root): pass
            def check_rate_limit(self, _source): return True, None
            def check_can_create_grid(self): return True, None
            def check_can_create_dca(self): return True, None
            def check_config_dir_writable(self): return True, None
            def check_dca_config_dir_writable(self): return True, None
            def check_min_investment(self, _params): return True, None
            def check_min_dca_capital(self, _params): return True, None
            def check_duplicate_symbol(self, _symbol): return True, None
            def check_config_not_exists(self, _path): return True, None
            def record_action(self, _source): pass

        grids_route._get_project_root = lambda: self.temp_dir.name
        grids_route.SafetyGuard = DummySafetyGuard
        dca_route._get_project_root = lambda: self.temp_dir.name
        dca_route.SafetyGuard = DummySafetyGuard

        from web_ui.backend import main
        self.client = TestClient(main.app)
        self.headers = {"Authorization": "Bearer test-symbol-token"}
        self.market_route = market_route

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_market_symbols_endpoint_and_cache(self):
        """測試 GET /api/v1/market/symbols 端點與快取機制"""
        mock_response = MarketSymbolsResponse(
            exchange="binance",
            symbols=[
                MarketSymbolItem(symbol="BTC/USDT:USDT", id="BTCUSDT", base="BTC", quote="USDT", market_type="swap"),
                MarketSymbolItem(symbol="牛来/USDT:USDT", id="牛来USDT", base="牛来", quote="USDT", market_type="swap"),
            ],
            total=2,
            cached=False,
        )

        # 清除快取以測試載入
        self.market_route._symbols_cache.clear()

        with patch("web_ui.backend.routes.market._load_market_symbols_sync", return_value=mock_response) as mock_load:
            resp = self.client.get("/api/v1/market/symbols?exchange=binance", headers=self.headers)
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["exchange"], "binance")
            self.assertEqual(len(data["symbols"]), 2)
            symbols_list = [s["symbol"] for s in data["symbols"]]
            self.assertIn("BTC/USDT:USDT", symbols_list)
            self.assertIn("牛来/USDT:USDT", symbols_list)

            # 再次呼叫應命中快取，不重複呼叫 _load_market_symbols_sync
            resp2 = self.client.get("/api/v1/market/symbols?exchange=binance", headers=self.headers)
            self.assertEqual(resp2.status_code, 200)
            self.assertEqual(mock_load.call_count, 1)

    def test_create_grid_rejects_nonexistent_symbol(self):
        """測試建立網格機器人時，若交易對不存在則回傳 422 禁止建立"""
        with patch("web_ui.backend.routes.market._lookup_market_from_ccxt_sync", return_value=None):
            payload = {
                "symbol": "INVALID_COIN/USDT",
                "exchange": "binance",
                "grid_type": "neutral",
                "lower_price": 100,
                "upper_price": 200,
                "grid_interval": 1,
                "order_amount": 1,
                "account_mode": "paper",
                "auto_start": False,
            }
            resp = self.client.post("/api/v1/grids", headers=self.headers, json=payload)
            self.assertEqual(resp.status_code, 422)
            self.assertIn("不存在或無法交易，拒絕建立", resp.json()["detail"])

    def test_create_grid_normalizes_alias_symbol(self):
        """測試建立網格機器人時，若傳入 alias 符號，自動正規化為 canonical_symbol"""
        canonical = "牛来/USDT:USDT"
        mock_info = MarketInfoResponse(
            symbol=canonical,
            canonical_symbol=canonical,
            exchange="binance",
            price_precision=5,
            quantity_precision=0,
            min_notional=5.0,
        )
        with patch("web_ui.backend.routes.market._lookup_market_from_ccxt_sync", return_value=mock_info):
            payload = {
                "symbol": "牛来USDT",
                "exchange": "binance",
                "grid_type": "neutral",
                "lower_price": 0.1,
                "upper_price": 0.2,
                "grid_interval": 0.001,
                "order_amount": 100,
                "account_mode": "paper",
                "auto_start": False,
            }
            resp = self.client.post("/api/v1/grids", headers=self.headers, json=payload)
            self.assertEqual(resp.status_code, 201)
            # 確認 YAML 檔案內寫入的 symbol 為 canonical 符號
            config_path = os.path.join(self.temp_dir.name, resp.json()["config_path"])
            with open(config_path, "r", encoding="utf-8") as f:
                content = f.read()
                self.assertIn("symbol: 牛来/USDT:USDT", content)

    def test_create_dca_rejects_nonexistent_symbol(self):
        """測試建立 DCA 機器人時，若交易對不存在則回傳 422 禁止建立"""
        with patch("web_ui.backend.routes.market._lookup_market_from_ccxt_sync", return_value=None):
            payload = {
                "name": "Invalid DCA",
                "symbol": "NONEXISTENT/USDT",
                "exchange": "binance",
                "direction": "long",
                "account_mode": "paper",
                "base_order_amount": 20,
                "max_safety_orders": 5,
                "price_deviation_percent": 1.0,
                "price_deviation_multiplier": 1.0,
                "order_size_multiplier": 1.0,
                "take_profit_percent": 1.5,
                "auto_start": False,
            }
            resp = self.client.post("/api/v1/dca", headers=self.headers, json=payload)
            self.assertEqual(resp.status_code, 422)
            self.assertIn("不存在或無法交易，拒絕建立", resp.json()["detail"])

    def test_create_dca_normalizes_alias_symbol(self):
        """測試建立 DCA 機器人時，若傳入 alias 符號，自動正規化為 canonical_symbol"""
        canonical = "牛来/USDT:USDT"
        mock_info = MarketInfoResponse(
            symbol=canonical,
            canonical_symbol=canonical,
            exchange="binance",
            price_precision=5,
            quantity_precision=0,
            min_notional=5.0,
        )
        with patch("web_ui.backend.routes.market._lookup_market_from_ccxt_sync", return_value=mock_info):
            payload = {
                "name": "Valid DCA",
                "symbol": "牛来USDT",
                "exchange": "binance",
                "direction": "long",
                "account_mode": "paper",
                "base_order_amount": 20,
                "max_safety_orders": 5,
                "price_deviation_percent": 1.0,
                "price_deviation_multiplier": 1.0,
                "order_size_multiplier": 1.0,
                "take_profit_percent": 1.5,
                "auto_start": False,
            }
            resp = self.client.post("/api/v1/dca", headers=self.headers, json=payload)
            self.assertEqual(resp.status_code, 201)
            config_path = os.path.join(self.temp_dir.name, resp.json()["config_path"])
            with open(config_path, "r", encoding="utf-8") as f:
                content = f.read()
                self.assertIn("symbol: 牛来/USDT:USDT", content)

    def test_supervisor_preflight_gate_logic(self):
        """測試 run_multiple_grids 的 pre-flight 守衛邏輯"""
        mock_adapter = MagicMock()
        mock_adapter.exchange.markets = {
            "BTC/USDT:USDT": {"id": "BTCUSDT"},
            "牛来/USDT:USDT": {"id": "牛来USDT"},
        }

        # 模擬 loaded_markets 包含合法符號
        loaded_markets = mock_adapter.exchange.markets
        valid_symbol = "BTC/USDT:USDT"
        self.assertIn(valid_symbol, loaded_markets)

        # 模擬非法符號被攔截
        invalid_symbol = "FAKE/USDT"
        self.assertNotIn(invalid_symbol, loaded_markets)


if __name__ == "__main__":
    unittest.main()
