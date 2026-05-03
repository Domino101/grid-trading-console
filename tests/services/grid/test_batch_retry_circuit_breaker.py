"""
單元測試：API 熔斷器（BAN / Invalid API Key 短路保護）

測試範圍：
1. _parse_and_set_circuit：正確解析 -2015 / -1003 / 其他碼
2. _is_circuit_open：時間前後回傳正確布林值
3. place_batch_orders 重試迴圈：-2015 出現後不進入第 2 輪重試
4. place_batch_orders 重試迴圈：-1003 出現後熔斷並跳過重試
"""

import asyncio
import time
import unittest
from decimal import Decimal
from typing import List
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# 最小化 Mock：模擬 GridEngineImpl 的必要依賴，讓 __init__ 可以執行
# ---------------------------------------------------------------------------

def _make_engine():
    """建立最小化的 GridEngineImpl 實例（mock exchange adapter）"""
    from core.services.grid.implementations.grid_engine_impl import GridEngineImpl

    mock_exchange = MagicMock()
    mock_exchange.config = MagicMock()
    mock_exchange.config.exchange_id = "binance"
    mock_exchange.is_connected = MagicMock(return_value=True)

    engine = GridEngineImpl.__new__(GridEngineImpl)
    # 手動呼叫 __init__ 所需的最小設定
    import re
    import collections
    engine.logger = MagicMock()
    engine.exchange = mock_exchange
    engine.config = None
    engine.coordinator = None
    engine._order_callbacks = []
    engine._pending_orders = {}
    engine._expected_cancellations = set()
    engine._current_price = None
    engine._last_price_update_time = 0
    engine._price_ws_enabled = False
    engine._expected_total_orders = 0
    engine._health_check_task = None
    engine._last_health_check_time = 0
    engine._last_order_fill_time = 0
    engine._last_health_repair_count = 0
    engine._last_health_repair_time = 0
    engine._health_checker = None
    engine._last_position_warning_time = 0
    engine._position_warning_interval = 60
    engine._lighter_order_lock = asyncio.Lock()
    engine._pending_orders_by_client_id = {}
    engine._running = False
    engine._order_rate_window_seconds = 0
    engine._order_rate_max_orders = 0
    engine._order_rate_timestamps = collections.deque()
    engine._order_rate_lock = asyncio.Lock()
    engine._symbol_open_orders_cache = {}
    engine._symbol_open_orders_cache_lock = asyncio.Lock()
    engine._price_fetch_lock = asyncio.Lock()
    # 🔥 熔斷器狀態
    engine._last_api_error_code = None
    engine._api_circuit_until = 0.0
    return engine


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------

class TestParseAndSetCircuit(unittest.TestCase):
    """測試 _parse_and_set_circuit 解析邏輯"""

    def setUp(self):
        self.engine = _make_engine()

    def test_2015_sets_infinite_circuit(self):
        """-2015 應設定永久熔斷（inf），回傳 '-2015'"""
        code = self.engine._parse_and_set_circuit('{"code":-2015,"msg":"Invalid API-key"}')
        self.assertEqual(code, '-2015')
        self.assertEqual(self.engine._last_api_error_code, '-2015')
        self.assertEqual(self.engine._api_circuit_until, float('inf'))
        self.assertTrue(self.engine._is_circuit_open())

    def test_1003_with_timestamp_sets_circuit(self):
        """-1003 含 banned until 時間戳，應設定定時熔斷"""
        future_ms = int((time.time() + 300) * 1000)  # 300 秒後
        err = f'{{"code":-1003,"msg":"Way too many requests; IP banned until {future_ms}."}}'
        code = self.engine._parse_and_set_circuit(err)
        self.assertEqual(code, '-1003')
        self.assertEqual(self.engine._last_api_error_code, '-1003')
        expected_until = future_ms / 1000.0
        self.assertAlmostEqual(self.engine._api_circuit_until, expected_until, delta=1)
        self.assertTrue(self.engine._is_circuit_open())

    def test_1003_without_timestamp_defaults_120s(self):
        """-1003 未含時間戳，應預設 120 秒熔斷"""
        before = time.time()
        self.engine._parse_and_set_circuit('{"code":-1003,"msg":"Too many requests"}')
        self.assertAlmostEqual(
            self.engine._api_circuit_until, before + 120, delta=2
        )
        self.assertTrue(self.engine._is_circuit_open())

    def test_other_code_no_circuit(self):
        """其他錯誤碼（如 -2011）不應設定熔斷"""
        code = self.engine._parse_and_set_circuit('{"code":-2011,"msg":"Unknown order sent"}')
        self.assertEqual(code, '-2011')
        self.assertEqual(self.engine._api_circuit_until, 0.0)
        self.assertFalse(self.engine._is_circuit_open())

    def test_no_code_returns_none(self):
        """無法解析錯誤碼時回傳 None"""
        code = self.engine._parse_and_set_circuit("connection timeout")
        self.assertIsNone(code)
        self.assertFalse(self.engine._is_circuit_open())


class TestIsCircuitOpen(unittest.TestCase):
    """測試 _is_circuit_open 時間邏輯"""

    def setUp(self):
        self.engine = _make_engine()

    def test_not_open_by_default(self):
        """預設不開啟"""
        self.assertFalse(self.engine._is_circuit_open())

    def test_open_when_until_in_future(self):
        """until 在未來 → 開啟"""
        self.engine._api_circuit_until = time.time() + 60
        self.assertTrue(self.engine._is_circuit_open())

    def test_closed_when_until_in_past(self):
        """until 在過去 → 關閉"""
        self.engine._api_circuit_until = time.time() - 1
        self.assertFalse(self.engine._is_circuit_open())

    def test_infinite_always_open(self):
        """float('inf') → 永遠開啟"""
        self.engine._api_circuit_until = float('inf')
        self.assertTrue(self.engine._is_circuit_open())


class TestPlaceBatchOrdersCircuitBreaker(unittest.IsolatedAsyncioTestCase):
    """測試 place_batch_orders 重試迴圈的熔斷短路行為"""

    async def _run_batch_with_error(self, error_msg: str) -> int:
        """
        模擬 place_order 一律丟出指定 error_msg 的情境，
        執行 place_batch_orders，回傳 place_order 被呼叫的總次數。
        """
        from core.services.grid.implementations.grid_engine_impl import GridEngineImpl
        from core.services.grid.models import GridOrder, GridOrderSide, GridOrderStatus

        engine = _make_engine()

        # 建立 1 個假訂單
        order = MagicMock(spec=GridOrder)
        order.grid_id = 1
        order.side = GridOrderSide.BUY
        order.amount = Decimal("0.1")
        order.price = Decimal("100")

        call_count = 0

        async def mock_place_order(o, **kwargs):
            nonlocal call_count
            call_count += 1
            raise Exception(error_msg)

        engine.place_order = mock_place_order

        # 模擬 Binance 環境的必要屬性
        engine.exchange.config.exchange_id = "binance_test"  # 非 binance，走一般路徑
        engine.config = MagicMock()
        engine.config.exchange = "binance_test"
        engine.config.symbol = "SOL/USDC:USDC"
        engine.config.grid_type = MagicMock()

        engine._last_batch_place_time = 0

        await engine.place_batch_orders([order], max_retries=2)
        return call_count

    async def test_2015_stops_after_first_failure(self):
        """
        -2015 出現後，重試迴圈在第 1 輪重試解析到錯誤後立即 break。

        呼叫次序：
          [初始批次] place_order × 1 → 失敗，記入 failed_orders
          [重試第 1 輪] place_order × 1 → 失敗，_parse_and_set_circuit 設定 -2015，break
          [重試第 2 輪] 因熔斷 break，永不執行

        預期 place_order 總計被呼叫 2 次（初始 1 + 重試 1）。
        """
        calls = await self._run_batch_with_error(
            '{"code":-2015,"msg":"Invalid API-key, IP, or permissions for action."}'
        )
        # 初始 1 次 + 重試第 1 輪 1 次（第 1 輪發現 -2015 後 break）= 2 次
        self.assertEqual(calls, 2, f"Expected 2 call total, got {calls}")

    async def test_1003_stops_retry(self):
        """
        -1003 出現後，重試迴圈在第 1 輪重試解析到 BAN 後立即 break。

        呼叫次序：
          [初始批次] place_order × 1 → 失敗，記入 failed_orders
          [重試第 1 輪前] _is_circuit_open() 檢查：此時熔斷尚未設定 → 進入重試
          [重試第 1 輪] place_order × 1 → 失敗，_parse_and_set_circuit 設定 -1003，break
          [重試第 2 輪] 因熔斷 break，永不執行

        預期 place_order 總計被呼叫 2 次（初始 1 + 重試 1）。
        """
        future_ms = int((time.time() + 300) * 1000)
        calls = await self._run_batch_with_error(
            f'{{"code":-1003,"msg":"Way too many requests; IP banned until {future_ms}."}}'  
        )
        # 初始 1 次 + 重試第 1 輪 1 次（第 1 輪發現 -1003 後 break）= 2 次
        self.assertEqual(calls, 2, f"Expected 2 call total, got {calls}")

    async def test_normal_error_allows_retry(self):
        """
        一般錯誤（非 -2015/-1003）應允許重試，place_order 被呼叫 1 + max_retries 次
        """
        calls = await self._run_batch_with_error(
            '{"code":-2011,"msg":"Unknown order sent."}'
        )
        # 初始 1 次 + 重試 2 次 = 3 次
        self.assertEqual(calls, 3, f"Expected 3 calls total, got {calls}")


if __name__ == "__main__":
    unittest.main()
