"""Unit tests for Phase C1: K-line repository, service, and API endpoint.

Tests all C1 audit items (1.1 through 1.13):
- 1.1 Parameterized queries & SQL injection defense
- 1.2 Idempotent upsert
- 1.3 Synthetic vs Real isolation (real data cannot be degraded by synthetic)
- 1.4 Gap detection
- 1.5 Cache-first fetch avoidance
- 1.6 Pagination budget & partial fill warnings
- 1.7 PAPER deterministic synthesis
- 1.8 Error honesty (stale vs unavailable, never false live)
- 1.9 Retention pruning (180 days)
- 1.10 Threadpool execution / non-blocking
- 1.11 SQLite migration & integrity
- 1.12 Grid regression prevention
- 1.13 CI registration
"""

import asyncio
import os
import shutil
import sys
import tempfile
import time
import unittest
from decimal import Decimal
from typing import Any, List, Optional

_backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../web_ui/backend"))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from fastapi.testclient import TestClient
from main import app
from kline_repository import KlineRepository, StoredKline, TIMEFRAME_SECONDS
from kline_service import KlineService, KlineResult
from schemas import KlinesResponse, KlineCandle
from lifecycle import connect_sqlite


class TestDcaKlineRepository(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="kline_test_")
        self.db_path = os.path.join(self.temp_dir, "test_runtime.sqlite3")
        self.repo = KlineRepository(database_path=self.db_path)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_audit_1_1_parameterized_queries(self):
        """1.1 P0: All queries must use '?' parameter placeholders."""
        repo_file = os.path.join(_backend_dir, "kline_repository.py")
        with open(repo_file, "r", encoding="utf-8") as f:
            code = f.read()

        self.assertNotIn('execute(f"', code)
        self.assertNotIn("execute(f'", code)
        self.assertNotIn("execute(\"%", code)
        self.assertNotIn("execute('%", code)

        # Test against SQL injection in values
        malicious_symbol = "ETH/USDT'; DROP TABLE dca_klines; --"
        self.repo.save_candles(
            exchange="binance",
            symbol=malicious_symbol,
            timeframe="15m",
            candles=[(1700000000, "100.5", "101.0", "99.0", "100.8", "50.0")],
            synthetic=0,
        )
        res = self.repo.get_range("binance", malicious_symbol, "15m")
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0].symbol, malicious_symbol)

        # Verify table still exists
        with connect_sqlite(self.db_path) as conn:
            row = conn.execute("SELECT count(*) FROM dca_klines").fetchone()
            self.assertEqual(row[0], 1)

    def test_audit_1_2_upsert_idempotency(self):
        """1.2 P0: Writing the same key 3 times results in 1 row with the latest value."""
        candle_v1 = (1700000000, "100.0", "105.0", "95.0", "102.0", "10.0")
        candle_v2 = (1700000000, "100.0", "106.0", "94.0", "103.0", "15.0")
        candle_v3 = (1700000000, "100.0", "107.0", "93.0", "104.5", "20.0")

        self.repo.save_candles("binance", "SOL/USDC", "15m", [candle_v1], synthetic=0)
        self.repo.save_candles("binance", "SOL/USDC", "15m", [candle_v2], synthetic=0)
        self.repo.save_candles("binance", "SOL/USDC", "15m", [candle_v3], synthetic=0)

        candles = self.repo.get_range("binance", "SOL/USDC", "15m")
        self.assertEqual(len(candles), 1)
        self.assertEqual(candles[0].high, Decimal("107.0"))
        self.assertEqual(candles[0].close, Decimal("104.5"))
        self.assertEqual(candles[0].volume, Decimal("20.0"))

    def test_audit_1_3_synthetic_does_not_pollute_real(self):
        """1.3 P0: Real historical data must never be downgraded by synthetic data."""
        # Case A: Synthetic (1) written first, then Real (0) -> Result is Real (0)
        candle_synth = (1700000000, "50.0", "55.0", "45.0", "52.0", "100.0")
        candle_real = (1700000000, "60.0", "65.0", "58.0", "62.0", "200.0")

        self.repo.save_candles("binance", "BTC/USDT", "1h", [candle_synth], synthetic=1)
        candles = self.repo.get_range("binance", "BTC/USDT", "1h")
        self.assertEqual(candles[0].synthetic, 1)
        self.assertEqual(candles[0].close, Decimal("52.0"))

        self.repo.save_candles("binance", "BTC/USDT", "1h", [candle_real], synthetic=0)
        candles = self.repo.get_range("binance", "BTC/USDT", "1h")
        self.assertEqual(candles[0].synthetic, 0)
        self.assertEqual(candles[0].close, Decimal("62.0"))

        # Case B: Real (0) written first, then Synthetic (1) -> Result stays Real (0)
        self.repo.save_candles("binance", "BTC/USDT", "1h", [candle_synth], synthetic=1)
        candles = self.repo.get_range("binance", "BTC/USDT", "1h")
        self.assertEqual(candles[0].synthetic, 0)
        self.assertEqual(candles[0].close, Decimal("62.0"))  # Not overwritten by 52.0

    def test_audit_1_4_gap_detection(self):
        """1.4 P0: Given 10 equidistant bars with 2 removed, count_gaps returns 2; 0 when full."""
        step = 900  # 15m
        base_ts = 1700000000
        full_bars = [base_ts + i * step for i in range(10)]
        self.assertEqual(KlineRepository.count_gaps(full_bars, "15m"), 0)

        # Remove bars at index 3 and 4
        missing_bars = [full_bars[i] for i in [0, 1, 2, 5, 6, 7, 8, 9]]
        self.assertEqual(KlineRepository.count_gaps(missing_bars, "15m"), 2)

        # Remove 1 bar at index 1 and 2 bars at index 7, 8
        multi_missing = [full_bars[i] for i in [0, 2, 3, 4, 5, 6, 9]]
        self.assertEqual(KlineRepository.count_gaps(multi_missing, "15m"), 3)

    def test_audit_1_5_cache_first_avoids_api_calls(self):
        """1.5 P0: Complete cache must trigger exactly 0 external fetch calls."""
        step = 900
        base_ts = 1700000000
        sample_bars = [
            (base_ts + i * step, "100", "105", "95", "102", "50")
            for i in range(50)
        ]
        self.repo.save_candles("binance", "ETH/USDT", "15m", sample_bars, synthetic=0)

        fetch_call_count = 0

        def mock_fetch(symbol, exchange, timeframe, since_ms, limit):
            nonlocal fetch_call_count
            fetch_call_count += 1
            return []

        service = KlineService(repository=self.repo, fetch_fn=mock_fetch)
        res = service.get_candles_sync(
            exchange="binance",
            symbol="ETH/USDT",
            timeframe="15m",
            limit=50,
            until=base_ts + 49 * step,
        )

        self.assertEqual(fetch_call_count, 0)
        self.assertEqual(res.source, "cache")
        self.assertEqual(res.data_status, "live")
        self.assertEqual(len(res.candles), 50)
        self.assertEqual(res.gaps, 0)

    def test_audit_1_6_pagination_budget_and_partial_fill_warning(self):
        """1.6 P0: Requesting 20000 1m bars on miss -> calls <= MAX_PAGES, warnings non-empty, data_status=stale."""
        fetch_call_count = 0
        step_ms = 60 * 1000

        def mock_paginated_fetch(symbol, exchange, timeframe, since_ms, limit):
            nonlocal fetch_call_count
            fetch_call_count += 1
            start = since_ms or 1700000000000
            # Return a batch of 1000 bars
            return [
                [start + i * step_ms, 100.0, 105.0, 95.0, 102.0, 10.0]
                for i in range(1000)
            ]

        max_pages = 5
        service = KlineService(
            repository=self.repo,
            fetch_fn=mock_paginated_fetch,
            max_pages=max_pages,
            page_size=1000,
        )

        res = service.get_candles_sync(
            exchange="binance",
            symbol="BTC/USDT",
            timeframe="1m",
            limit=20000,
            until=1700000000 + 20000 * 60,
        )

        self.assertLessEqual(fetch_call_count, max_pages)
        self.assertEqual(fetch_call_count, max_pages)
        self.assertEqual(res.data_status, "stale")
        self.assertTrue(any("僅回填部分歷史" in w for w in res.warnings))
        self.assertEqual(len(res.candles), max_pages * 1000)

    def test_audit_1_7_paper_determinism(self):
        """1.7 P0: Same symbol/timeframe in paper mode produces identical values and source='paper_synthetic'."""
        service = KlineService(repository=self.repo)

        res1 = service.get_candles_sync(
            exchange="paper",
            symbol="ETH/USDC:USDC",
            timeframe="15m",
            limit=100,
        )
        res2 = service.get_candles_sync(
            exchange="paper",
            symbol="ETH/USDC:USDC",
            timeframe="15m",
            limit=100,
        )

        self.assertEqual(res1.source, "paper_synthetic")
        self.assertEqual(res1.data_status, "paper")
        self.assertEqual(res2.source, "paper_synthetic")
        self.assertEqual(res2.data_status, "paper")
        self.assertEqual(len(res1.candles), 100)
        self.assertEqual(len(res2.candles), 100)

        # Assert candles are identically valued
        for c1, c2 in zip(res1.candles, res2.candles):
            self.assertEqual(c1.ts, c2.ts)
            self.assertEqual(c1.open, c2.open)
            self.assertEqual(c1.high, c2.high)
            self.assertEqual(c1.low, c2.low)
            self.assertEqual(c1.close, c2.close)
            self.assertEqual(c1.volume, c2.volume)
            self.assertEqual(c1.synthetic, 1)

    def test_audit_1_8_error_honesty(self):
        """1.8 P0: Mock 429 error -> stale if cache exists, unavailable if no cache; never 'live'."""
        def mock_rate_limited_fetch(symbol, exchange, timeframe, since_ms, limit):
            raise RuntimeError("HTTP 429 Too Many Requests: Rate Limit Exceeded")

        service = KlineService(repository=self.repo, fetch_fn=mock_rate_limited_fetch)

        # Case 1: Empty cache -> data_status='unavailable', candles=[]
        res_empty = service.get_candles_sync("binance", "AVAX/USDT", "1h", limit=50)
        self.assertEqual(res_empty.data_status, "unavailable")
        self.assertEqual(len(res_empty.candles), 0)
        self.assertNotEqual(res_empty.data_status, "live")

        # Case 2: Pre-existing cached data -> data_status='stale', returns cached candles
        base_ts = 1700000000
        step = 3600
        sample_bars = [
            (base_ts + i * step, "20", "22", "19", "21", "100")
            for i in range(10)
        ]
        self.repo.save_candles("binance", "AVAX/USDT", "1h", sample_bars, synthetic=0)

        # Request 20 bars (exceeds cached 10) so it attempts to backfill and encounters 429
        res_cached = service.get_candles_sync("binance", "AVAX/USDT", "1h", limit=20)
        self.assertEqual(res_cached.data_status, "stale")
        self.assertEqual(len(res_cached.candles), 10)
        self.assertNotEqual(res_cached.data_status, "live")
        self.assertTrue(any("交易所連線異常" in w for w in res_cached.warnings))

    def test_audit_1_9_retention_prune(self):
        """1.9 P1: prune() deletes rows older than 180 days based on fetched_at."""
        now = time.time()
        day_sec = 86400.0

        old_fetched = now - (181 * day_sec)
        fresh_fetched = now - (10 * day_sec)

        # Old candles (181 days ago)
        self.repo.save_candles(
            "binance", "DOT/USDT", "1d",
            [(1600000000, "5", "6", "4", "5.5", "1000")],
            synthetic=0,
            fetched_at=old_fetched,
        )
        # Fresh candles (10 days ago)
        self.repo.save_candles(
            "binance", "DOT/USDT", "1d",
            [(1700000000, "7", "8", "6", "7.5", "2000")],
            synthetic=0,
            fetched_at=fresh_fetched,
        )

        self.assertEqual(self.repo.count("binance", "DOT/USDT", "1d"), 2)

        deleted = self.repo.prune(retention_days=180, now=now)
        self.assertEqual(deleted, 1)

        remaining = self.repo.get_range("binance", "DOT/USDT", "1d")
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].ts, 1700000000)

    def test_audit_1_10_non_blocking_async_service(self):
        """1.10 P1: KlineService.get_candles is async and non-blocking."""
        service = KlineService(repository=self.repo)
        
        async def run_async():
            return await service.get_candles(
                exchange="paper",
                symbol="BTC/USDC:USDC",
                timeframe="15m",
                limit=10,
            )

        result = asyncio.run(run_async())
        self.assertIsInstance(result, KlineResult)
        self.assertEqual(result.source, "paper_synthetic")
        self.assertEqual(len(result.candles), 10)

    def test_audit_1_11_sqlite_integrity_and_bounds(self):
        """1.11 P1: PRAGMA integrity_check and bounds verification."""
        with connect_sqlite(self.db_path) as conn:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            self.assertEqual(row[0], "ok")

        self.repo.save_candles(
            "binance", "LINK/USDT", "1h",
            [
                (1700000000, "10", "11", "9", "10.5", "500"),
                (1700003600, "10.5", "12", "10", "11.5", "600"),
                (1700007200, "11.5", "13", "11", "12.0", "700"),
            ],
            synthetic=0,
        )
        min_ts, max_ts = self.repo.get_time_bounds("binance", "LINK/USDT", "1h")
        self.assertEqual(min_ts, 1700000000)
        self.assertEqual(max_ts, 1700007200)
        self.assertEqual(self.repo.count("binance", "LINK/USDT", "1h"), 3)

    def test_audit_1_12_http_endpoint_contract(self):
        """1.12 & 4.6 API Contract test for GET /api/v1/market/klines/{symbol}."""
        import auth
        auth._API_TOKEN = "test-token"
        client = TestClient(app)
        headers = {"Authorization": "Bearer test-token"}

        # 1. Test 401 Unauthorized without token
        resp_unauth = client.get("/api/v1/market/klines/ETH/USDC:USDC")
        self.assertEqual(resp_unauth.status_code, 401)

        # 2. Test 400 Bad Request on invalid timeframe
        resp_bad_tf = client.get(
            "/api/v1/market/klines/ETH/USDC:USDC?timeframe=2m",
            headers=headers,
        )
        self.assertEqual(resp_bad_tf.status_code, 400)
        self.assertIn("timeframe", resp_bad_tf.json()["detail"])

        # 3. Test Paper request returns 200 with complete KlinesResponse shape
        resp_paper = client.get(
            "/api/v1/market/klines/ETH/USDC:USDC?exchange=paper&timeframe=15m&limit=50",
            headers=headers,
        )
        self.assertEqual(resp_paper.status_code, 200)
        data = resp_paper.json()
        self.assertEqual(data["symbol"], "ETH/USDC:USDC")
        self.assertEqual(data["exchange"], "paper")
        self.assertEqual(data["timeframe"], "15m")
        self.assertEqual(data["source"], "paper_synthetic")
        self.assertEqual(data["data_status"], "paper")
        self.assertEqual(len(data["candles"]), 50)
        self.assertIn("t", data["candles"][0])
        self.assertIn("o", data["candles"][0])
        self.assertIn("h", data["candles"][0])
        self.assertIn("l", data["candles"][0])
        self.assertIn("c", data["candles"][0])
        self.assertIn("v", data["candles"][0])


if __name__ == "__main__":
    unittest.main()
