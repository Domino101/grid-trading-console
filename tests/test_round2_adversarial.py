"""Round 2 Adversarial Red-Team Test Suite (2026-09-06).

Independent, adversarial destructive tests probing edge cases, type injection,
concurrency regressions, and boundary conditions across P0-P3 refactored components.
"""

import asyncio
import math
import os
import subprocess
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from core.infrastructure.database.ledger_store import (
    _monotonic_regression,
    _num,
    create_ledger_store,
)
from core.security.vault import derive_key, encrypt_secret, decrypt_secret
from core.ipc.uds_client import TradingCoreClient, AsyncTradingCoreClient, TradingCoreIPCError, MAX_IPC_PAYLOAD_BYTES
from web_ui.backend.runtime_repository import RuntimeSnapshotRepository
from core.supervisor.master_daemon import MasterSupervisor, WorkerInfo


class TestRound2AdversarialSuite(unittest.TestCase):
    """第二輪對抗性獨立破壞性測試套件"""

    def test_r2_01_nan_and_inf_monotonic_bypass_blocked(self):
        """DEF-06: 驗證 NaN 與 Inf 數值注入無法穿透單調計數防線"""
        # 1. 驗證 _num 攔截
        self.assertIsNone(_num(float("nan")))
        self.assertIsNone(_num("nan"))
        self.assertIsNone(_num("NaN"))
        self.assertIsNone(_num(float("inf")))
        self.assertIsNone(_num(float("-inf")))

        # 2. 驗證 _monotonic_regression 阻斷
        old = {"completed_cycles": 100, "filled_buy_count": 50, "realized_pnl": 500.0}
        new_nan = {"completed_cycles": float("nan"), "filled_buy_count": "nan", "realized_pnl": "NaN"}
        regressed = _monotonic_regression(old, new_nan)
        self.assertIn("cycles", regressed)
        self.assertIn("buy", regressed)
        self.assertIn("realized", regressed)

    def test_r2_02_negative_counter_injection_blocked(self):
        """DEF-06: 驗證負數計數器無法寫入單調計數器"""
        old = {"completed_cycles": 10, "filled_buy_count": 5, "total_fees": 1.0}
        new_neg = {"completed_cycles": -1, "filled_buy_count": -5, "total_fees": -0.5}
        regressed = _monotonic_regression(old, new_neg)
        self.assertIn("cycles", regressed)
        self.assertIn("buy", regressed)
        self.assertIn("fees", regressed)

    def test_r2_03_create_ledger_store_url_as_path_safety(self):
        """DEF-07: 驗證以第一個位置參數傳入 PostgreSQL URL 時，不會建立意外目錄或污染 SQLite 路徑"""
        test_url = "postgresql+psycopg://blackhole_user:blackhole_pass@127.0.0.1:54329/non_existent_db"
        store = create_ledger_store(test_url)
        db_path = getattr(store, "database_path", "")
        # 絕不得在工作目錄建立名為 postgresql+psycopg: 的檔案或目錄
        self.assertFalse(os.path.exists("postgresql+psycopg:/"))
        self.assertNotIn("postgresql+", db_path)

    def test_r2_04_l1_cache_disjoint_set_no_bot_loss(self):
        """DEF-08: 驗證 Redis 快取集合與 DB 不完全重合時，Web UI 讀模型絕不遺漏 DB 機器人"""
        class FakeLedger:
            def load_all_snapshots(self):
                return {
                    "config/grid/bot_A.yaml": ({"id": "A", "pnl": 10}, 1.0),
                    "config/grid/bot_B.yaml": ({"id": "B", "pnl": 20}, 1.0),
                }

        class FakeRedisCache:
            def get_all(self):
                # 包含 bot_A 與幽靈 bot_GHOST，但缺少 bot_B
                return {
                    "config/grid/bot_A.yaml": ({"id": "A", "pnl": 15}, 2.0),
                    "config/grid/bot_GHOST.yaml": ({"id": "GHOST", "pnl": 0}, 2.0),
                }

        repo = RuntimeSnapshotRepository(":memory:")
        repo._ledger = FakeLedger()
        repo._redis_cache = FakeRedisCache()

        result = repo.load_all()
        # 驗證 bot_B 必須存在，且 bot_A 更新至 Redis 最新數值
        self.assertIn("config/grid/bot_A.yaml", result)
        self.assertIn("config/grid/bot_B.yaml", result)
        self.assertEqual(result["config/grid/bot_A.yaml"][0]["pnl"], 15)
        self.assertEqual(result["config/grid/bot_B.yaml"][0]["pnl"], 20)

    def test_r2_05_vault_master_key_whitespace_and_newline(self):
        """FRG-03: 驗證 Master Key 包含換行符或前後空白時，衍生金鑰完全一致且解密無誤"""
        key_raw = "a" * 64
        key_with_nl = ("a" * 64) + "\n"
        key_with_crlf = "  " + ("a" * 64) + " \r\n"

        k1 = derive_key(key_raw)
        k2 = derive_key(key_with_nl)
        k3 = derive_key(key_with_crlf)
        self.assertEqual(k1, k2)
        self.assertEqual(k2, k3)

        secret = "SUPER_SECURE_API_SECRET_9876"
        encrypted = encrypt_secret(secret, key_raw)
        decrypted = decrypt_secret(encrypted, key_with_crlf)
        self.assertEqual(decrypted, secret)

    def test_r2_06_async_uds_client_timeout_and_max_payload(self):
        """DEF-11: 驗證非同步 UDS 客戶端超時保護與巨型畸形封包防禦"""
        client = AsyncTradingCoreClient(timeout=0.1)
        self.assertEqual(client.timeout, 0.1)

        # 測試超額封包長度檢查
        client_sync = TradingCoreClient()
        fake_socket = MagicMock()
        fake_socket.recv.side_effect = [b"\x01\x00\x00\x00"]  # 16MB (超過 10MB)
        client_sync._sock = fake_socket

        with self.assertRaises(TradingCoreIPCError):
            client_sync._send_envelope(1, b"test")

    def test_r2_07_binance_fast_html_error_fallback(self):
        """FRG-04: 驗證 BinanceFastAdapter 安全解析非 JSON 響應 (如 502 HTML)"""
        from core.adapters.exchanges.adapters.binance_fast import BinanceFastAdapter

        adapter = BinanceFastAdapter.__new__(BinanceFastAdapter)

        # 模擬非 JSON 回應
        mock_resp = AsyncMock()
        mock_resp.status = 502
        mock_resp.json.side_effect = Exception("ContentTypeError: text/html")
        mock_resp.text.return_value = "<html>502 Bad Gateway</html>"

        async def run_test():
            parsed = await adapter._parse_json_response(mock_resp)
            self.assertEqual(parsed["code"], 502)
            self.assertIn("502 Bad Gateway", parsed["msg"])

        asyncio.run(run_test())

    def test_r2_08_master_supervisor_parallel_shutdown(self):
        """FRG-05: 驗證 MasterSupervisor 並行優雅關閉 Worker"""
        master = MasterSupervisor()
        p1 = MagicMock()
        p1.poll.side_effect = [None, 0]
        p2 = MagicMock()
        p2.poll.side_effect = [None, 0]

        master.workers["config/grid/a.yaml"] = WorkerInfo("config/grid/a.yaml", "grid", process=p1)
        master.workers["config/grid/b.yaml"] = WorkerInfo("config/grid/b.yaml", "grid", process=p2)

        t0 = time.time()
        master.shutdown_all(timeout=2.0)
        elapsed = time.time() - t0

        self.assertLess(elapsed, 2.0)
        p1.terminate.assert_called_once()
        p2.terminate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
