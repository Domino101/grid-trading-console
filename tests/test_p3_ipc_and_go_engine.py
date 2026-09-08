"""
Unit & Integration Tests for P3 Polyglot High-Performance Core.
Tests Python-to-Go IPC over Unix Domain Socket and verifies matching engine behaviors.
"""

import asyncio
import os
import subprocess
import time
import unittest

from core.ipc.proto import trading_core_pb2 as pb
from core.ipc.uds_client import TradingCoreClient, AsyncTradingCoreClient, TradingCoreIPCError

TEST_SOCKET_PATH = "/tmp/test_trading_core.sock"


class TestP3PolyglotTradingCore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 移除舊 socket 檔案
        if os.path.exists(TEST_SOCKET_PATH):
            try:
                os.remove(TEST_SOCKET_PATH)
            except OSError:
                pass

        # 啟動 Go 撮合微引擎子進程
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        go_dir = os.path.join(project_root, "trading-core-go")
        cmd = ["go", "run", ".", "--socket", TEST_SOCKET_PATH]

        cls.engine_proc = subprocess.Popen(
            cmd,
            cwd=go_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        # 等待 socket 檔案建立並就緒 (最多等待 5 秒)
        ready = False
        t0 = time.time()
        while time.time() - t0 < 5.0:
            if os.path.exists(TEST_SOCKET_PATH):
                ready = True
                break
            time.sleep(0.05)

        if not ready:
            cls.engine_proc.kill()
            stdout, stderr = cls.engine_proc.communicate()
            raise RuntimeError(f"Go engine failed to create socket in time: {stderr}\n{stdout}")

    @classmethod
    def tearDownClass(cls):
        if cls.engine_proc and cls.engine_proc.poll() is None:
            cls.engine_proc.terminate()
            try:
                cls.engine_proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                cls.engine_proc.kill()
        if os.path.exists(TEST_SOCKET_PATH):
            try:
                os.remove(TEST_SOCKET_PATH)
            except OSError:
                pass

    def test_01_sync_heartbeat(self):
        """測試同步 UDS 客戶端連接與心跳擷取"""
        with TradingCoreClient(socket_path=TEST_SOCKET_PATH) as client:
            hb = client.get_heartbeat()
            self.assertIsInstance(hb.timestamp_ns, int)
            self.assertGreater(hb.timestamp_ns, 0)
            self.assertGreaterEqual(hb.cpu_pct, 0.0)

    def test_02_limit_order_placement_and_matching(self):
        """測試限價單掛單與對手單高速撮合"""
        with TradingCoreClient(socket_path=TEST_SOCKET_PATH) as client:
            symbol = "BTC/USDT"
            # 1. 放置 Maker 賣單 (Sell at 60,000)
            maker_req = pb.OrderRequest(
                request_id="req-maker-1",
                bot_id="bot-maker",
                symbol=symbol,
                side=pb.OrderSide.SELL,
                order_type=pb.OrderType.LIMIT,
                price=60000.0,
                quantity=1.0,
                client_order_id="m-101",
            )
            maker_resp = client.submit_order(maker_req)
            self.assertEqual(maker_resp.status, pb.OrderStatus.OPEN)
            self.assertEqual(maker_resp.executed_quantity, 0.0)
            self.assertEqual(maker_resp.remaining_quantity, 1.0)
            self.assertGreater(len(maker_resp.order_id), 0)

            # 2. 放置 Taker 買單 (Buy at 60,000) 吃掉賣單
            taker_req = pb.OrderRequest(
                request_id="req-taker-1",
                bot_id="bot-taker",
                symbol=symbol,
                side=pb.OrderSide.BUY,
                order_type=pb.OrderType.LIMIT,
                price=60000.0,
                quantity=0.6,
                client_order_id="t-101",
            )
            taker_resp = client.submit_order(taker_req)
            self.assertEqual(taker_resp.status, pb.OrderStatus.FILLED)
            self.assertAlmostEqual(taker_resp.executed_price, 60000.0, places=2)
            self.assertAlmostEqual(taker_resp.executed_quantity, 0.6, places=4)
            self.assertAlmostEqual(taker_resp.remaining_quantity, 0.0, places=4)
            # 撮合延遲必須小於 500 微秒 (500,000 ns)
            self.assertLess(taker_resp.latency_ns, 500000, f"Latency was {taker_resp.latency_ns} ns")

    def test_03_order_cancellation(self):
        """測試在簿掛單撤銷"""
        with TradingCoreClient(socket_path=TEST_SOCKET_PATH) as client:
            req = pb.OrderRequest(
                request_id="req-cancel-test",
                bot_id="bot-cancel",
                symbol="BTC/USDT",
                side=pb.OrderSide.BUY,
                order_type=pb.OrderType.LIMIT,
                price=40000.0,
                quantity=0.5,
                client_order_id="c-to-cancel",
            )
            resp = client.submit_order(req)
            self.assertEqual(resp.status, pb.OrderStatus.OPEN)
            order_id = resp.order_id

            # 撤銷此訂單
            cancel_req = pb.CancelRequest(
                request_id="req-cancel-action",
                bot_id="bot-cancel",
                symbol="BTC/USDT",
                order_id=order_id,
            )
            cancel_resp = client.cancel_order(cancel_req)
            self.assertTrue(cancel_resp.success)

    def test_04_microsecond_risk_guard(self):
        """測試 Go 側毫秒級風控衛哨與熔斷阻斷"""
        with TradingCoreClient(socket_path=TEST_SOCKET_PATH) as client:
            # 1. 正常風控查詢 (新標的無偏離，或貼近市價)
            normal_req = pb.RiskCheckRequest(
                bot_id="bot-risk-1",
                symbol="SOL/USDT",
                side=pb.OrderSide.BUY,
                price=150.0,
                quantity=1.0,
            )
            resp = client.check_risk(normal_req)
            self.assertTrue(resp.approved, f"Failed with reason: {resp.reason}")
            self.assertEqual(resp.reason, "APPROVED")

            # 2. 胖手指異常偏離 (> 5% deviation) 風控攔截
            # 當前 BTC/USDT 盤口 best_ask 為 60,000，買單出價 70,000 (+16.7%) 應被攔截
            fat_finger_req = pb.RiskCheckRequest(
                bot_id="bot-risk-1",
                symbol="BTC/USDT",
                side=pb.OrderSide.BUY,
                price=70000.0,
                quantity=1.0,
            )
            resp_fat = client.check_risk(fat_finger_req)
            self.assertFalse(resp_fat.approved)
            self.assertIn("FAT_FINGER_BUY_PRICE_TOO_HIGH", resp_fat.reason)

            # 3. 超過單筆最大下單量 (> 100 BTC) 風控拒絕
            oversized_req = pb.RiskCheckRequest(
                bot_id="bot-risk-1",
                symbol="SOL/USDT",
                side=pb.OrderSide.BUY,
                price=150.0,
                quantity=500.0,
            )
            resp_over = client.check_risk(oversized_req)
            self.assertFalse(resp_over.approved)
            self.assertIn("ORDER_QTY_LIMIT_EXCEEDED", resp_over.reason)

    def test_05_async_client_operations(self):
        """測試 asyncio 協程環境下的非同步 IPC 客戶端"""
        async def run_async_test():
            async with AsyncTradingCoreClient(socket_path=TEST_SOCKET_PATH) as aclient:
                hb = await aclient.get_heartbeat()
                self.assertGreater(hb.timestamp_ns, 0)

                order_req = pb.OrderRequest(
                    request_id="async-req-1",
                    bot_id="bot-async",
                    symbol="ETH/USDT",
                    side=pb.OrderSide.BUY,
                    order_type=pb.OrderType.LIMIT,
                    price=3500.0,
                    quantity=2.0,
                    client_order_id="async-101",
                )
                order_resp = await aclient.submit_order(order_req)
                self.assertEqual(order_resp.status, pb.OrderStatus.OPEN)
                self.assertEqual(order_resp.remaining_quantity, 2.0)

        asyncio.run(run_async_test())


if __name__ == "__main__":
    unittest.main()
