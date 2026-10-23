"""用戶數據流殭屍連線守門（BRUSDT WS 靜默 5 小時事故）。

守門目標：
- 連線存活但長時間無訊息 → 持久 watchdog 主動重建。
- listenKeyExpired → 立即重建。
- 重建流程不可與 ConnectionClosed 自動重連打架（避免雙重連線）。
"""

from __future__ import annotations

import asyncio
import time
import unittest
from unittest.mock import AsyncMock, MagicMock

from core.adapters.exchanges.adapters.binance_websocket import BinanceWebSocket


def _bare_ws() -> BinanceWebSocket:
    ws = object.__new__(BinanceWebSocket)
    ws.logger = None
    ws._user_connected = True
    ws._user_websocket = MagicMock()
    ws._user_websocket.close = AsyncMock()
    ws._user_reconnect_at = 0.0
    ws._suppress_user_auto_reconnect = False
    ws._last_user_message_time = 0.0
    ws.user_watchdog_threshold = 900
    ws.user_watchdog_check_interval = 0.01
    ws.user_self_check_delay = 0
    ws._user_silence_task = None
    ws._connect_user_stream = AsyncMock(return_value=True)
    return ws


class UserStreamWatchdogTests(unittest.IsolatedAsyncioTestCase):
    async def test_force_reconnect_closes_socket_and_rebuilds(self):
        ws = _bare_ws()
        socket = ws._user_websocket
        ok = await ws.force_user_stream_reconnect("test")
        self.assertTrue(ok)
        socket.close.assert_awaited()
        self.assertIsNone(ws._user_websocket)
        ws._connect_user_stream.assert_awaited_once()
        self.assertGreater(ws._last_user_message_time, 0, "重建後需重設死寂基準")

    async def test_force_reconnect_is_rate_limited(self):
        ws = _bare_ws()
        self.assertTrue(await ws.force_user_stream_reconnect("first"))
        self.assertFalse(await ws.force_user_stream_reconnect("second"))
        self.assertEqual(ws._connect_user_stream.await_count, 1)

    async def test_silence_watchdog_fires_after_threshold(self):
        ws = _bare_ws()
        ws.user_watchdog_threshold = 0
        ws._last_user_message_time = time.time() - 10
        ws.force_user_stream_reconnect = AsyncMock(return_value=True)

        task = asyncio.create_task(ws._user_silence_watchdog())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        ws.force_user_stream_reconnect.assert_awaited()
        reason = ws.force_user_stream_reconnect.await_args.kwargs.get("reason", "")
        self.assertIn("silence", reason)

    async def test_silence_watchdog_is_quiet_when_messages_flow(self):
        ws = _bare_ws()
        ws.user_watchdog_threshold = 60
        ws.force_user_stream_reconnect = AsyncMock(return_value=True)

        async def _keep_alive():
            for _ in range(5):
                ws._last_user_message_time = time.time()
                await asyncio.sleep(0.01)

        task = asyncio.create_task(ws._user_silence_watchdog())
        await _keep_alive()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        ws.force_user_stream_reconnect.assert_not_awaited()

    async def test_self_check_reconnects_only_when_socket_closed(self):
        from unittest.mock import patch

        ws = _bare_ws()
        with patch(
            "core.adapters.exchanges.adapters.binance_websocket._is_ws_closed",
            return_value=True,
        ):
            await ws._user_stream_watchdog()
        ws._connect_user_stream.assert_awaited_once()

    async def test_self_check_does_not_reconnect_on_silent_but_open_socket(self):
        """沒有訊息 ≠ 連線壞掉：舊的 30 秒「無訊息即重連」是 BRUSDT 重連風暴主因。"""
        from unittest.mock import patch

        ws = _bare_ws()
        ws._last_user_message_time = 0.0
        with patch(
            "core.adapters.exchanges.adapters.binance_websocket._is_ws_closed",
            return_value=False,
        ):
            await ws._user_stream_watchdog()
        ws._connect_user_stream.assert_not_awaited()

    async def test_listen_key_expired_triggers_reconnect(self):
        ws = _bare_ws()
        ws.force_user_stream_reconnect = AsyncMock(return_value=True)

        await ws._process_user_message({"e": "listenKeyExpired"})
        await asyncio.sleep(0)

        ws.force_user_stream_reconnect.assert_awaited()
        self.assertEqual(
            ws.force_user_stream_reconnect.await_args.kwargs.get("reason"),
            "listenKeyExpired",
        )


if __name__ == "__main__":
    unittest.main()
