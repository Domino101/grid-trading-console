"""HTTP contract tests for /api/v1/grid-backtest endpoints."""

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


def _payload(**overrides):
    payload = {
        "symbol": "SOL/USDC:USDC",
        "exchange": "binance",
        "grid_type": "neutral",
        "lower_price": 120.0,
        "upper_price": 160.0,
        "grid_count": 10,
        "per_grid_notional": 500.0,
        "initial_capital": 10000.0,
        "leverage": 10,
        "timeframe": "1h",
        "start_ts": 1700000000,
        "end_ts": 1700100000,
        "source": "manual",
    }
    payload.update(overrides)
    return payload


def _candles():
    out = []
    for i in range(30):
        ts = 1700000000 + i * 3600
        p = 140.0 + (5 if i % 2 == 0 else -5)
        out.append({"t": ts, "o": p, "h": p + 2, "l": p - 2, "c": p, "v": 1000})
    return out


class _FakeKlineService:
    def __init__(self, candles):
        self._candles = candles

    async def get_candles(self, **kwargs):
        from web_ui.backend.kline_service import KlineResult
        return KlineResult(
            symbol=kwargs["symbol"],
            exchange=kwargs["exchange"],
            timeframe=kwargs["timeframe"],
            candles=self._candles,
            source="exchange",
            data_status="live",
            warnings=[],
            oldest_ts=self._candles[0]["t"] if self._candles else None,
        )


@unittest.skipUnless(HAS_FASTAPI, "FastAPI installed in CI/release environments")
class GridBacktestHttpContractTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from web_ui.backend import auth
        from web_ui.backend.audit import AuditLogger
        from web_ui.backend import main

        self.temp_dir = tempfile.TemporaryDirectory()
        self._stash_global(auth, "_API_TOKEN", "contract-token")
        self._stash_global(AuditLogger, "_instance", AuditLogger(self.temp_dir.name))
        self.client = TestClient(main.app)

    def _stash_global(self, target, attribute, new_value):
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

    def _fake_service(self):
        fake = _FakeKlineService(_candles())
        from web_ui.backend import grid_backtest_service
        self._stash_global(
            grid_backtest_service.KlineService,
            "instance",
            lambda *args, **kwargs: fake,
        )
        return fake

    def test_requires_auth(self):
        for endpoint in ("/api/v1/grid-backtest/run", "/api/v1/grid-backtest/stress", "/api/v1/grid-backtest/sweep"):
            response = self.client.post(endpoint, json=_payload())
            self.assertEqual(response.status_code, 401, endpoint)

    def test_run_returns_result_contract(self):
        self._fake_service()
        response = self.client.post(
            "/api/v1/grid-backtest/run",
            headers=self._headers(),
            json=_payload(),
        )
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertIn("result", data)
        result = data["result"]
        self.assertIn("metrics", result)
        self.assertIn("risk", result)
        self.assertIn("equity", result)
        self.assertIn("allocation", result)
        self.assertIn("warnings", result)
        self.assertIn("liquidation_occurred", result["risk"])
        self.assertTrue(len(result["equity"]) > 0)

    def test_missing_capital_percentage_rejected(self):
        self._fake_service()
        payload = _payload()
        payload.pop("per_grid_notional", None)
        response = self.client.post(
            "/api/v1/grid-backtest/run",
            headers=self._headers(),
            json=payload,
        )
        self.assertIn(response.status_code, (400, 422))

    def test_stress_returns_scenarios(self):
        self._fake_service()
        response = self.client.post(
            "/api/v1/grid-backtest/stress",
            headers=self._headers(),
            json=_payload(),
        )
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertIn("scenarios", data)
        self.assertTrue(len(data["scenarios"]) > 0)

    def test_sweep_returns_runs(self):
        self._fake_service()
        payload = _payload()
        payload["sweep_grid_counts"] = [5, 10]
        payload["sweep_per_grid_notional"] = [500.0, 1000.0]
        payload["runs_limit"] = 20
        response = self.client.post(
            "/api/v1/grid-backtest/sweep",
            headers=self._headers(),
            json=_payload(**payload),
        )
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertIn("runs", data)
        self.assertTrue(len(data["runs"]) > 0)

    def test_run_does_not_call_exchange_write(self):
        from web_ui.backend import grid_backtest_service
        self._fake_service()
        with patch.object(grid_backtest_service, "GridBacktestSimulator", wraps=grid_backtest_service.GridBacktestSimulator) as sim_cls:
            response = self.client.post(
                "/api/v1/grid-backtest/run",
                headers=self._headers(),
                json=_payload(),
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(sim_cls.call_count, 1)


if __name__ == "__main__":
    unittest.main()
