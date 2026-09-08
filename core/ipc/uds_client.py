"""
Unix Domain Socket (UDS) IPC 客戶端
支援 Python 控制面與 Go 高性能微引擎間的高速二進位 Protobuf 通訊。
"""

import asyncio
import os
import socket
import struct
import time
from typing import Optional, Tuple

from core.ipc.proto import trading_core_pb2 as pb
from core.logging import get_logger

logger = get_logger("core.ipc.uds_client")

DEFAULT_SOCKET_PATH = "/tmp/trading_core.sock"
MAX_IPC_PAYLOAD_BYTES = 10 * 1024 * 1024  # 10MB 單筆上限，與 Go IPCServer 嚴格對齊 (DEF-11)


class TradingCoreIPCError(Exception):
    """IPC 通訊異常"""
    pass


class TradingCoreClient:
    """同步阻塞式 UDS IPC 客戶端 (適用於 Worker 進程或單元測試)"""

    def __init__(self, socket_path: str = DEFAULT_SOCKET_PATH, timeout: float = 3.0):
        self.socket_path = socket_path
        self.timeout = timeout
        self._sock: Optional[socket.socket] = None
        self._seq = 0

    def connect(self) -> None:
        """建立 Unix Domain Socket 連線"""
        if not os.path.exists(self.socket_path):
            raise FileNotFoundError(f"Trading core socket not found at {self.socket_path}")

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self.socket_path)
        self._sock = sock
        logger.debug(f"Connected to trading core UDS: {self.socket_path}")

    def close(self) -> None:
        """關閉連線"""
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def _send_envelope(self, msg_type: pb.MessageType, payload: bytes) -> pb.IPCEnvelope:
        if self._sock is None:
            self.connect()

        self._seq += 1
        env = pb.IPCEnvelope(
            type=msg_type,
            payload=payload,
            sequence=self._seq,
            timestamp_ns=time.time_ns(),
        )
        serialized_env = env.SerializeToString()
        header = struct.pack(">I", len(serialized_env))

        # 寫入前綴與封包
        self._sock.sendall(header + serialized_env)

        # 讀取回傳封包長度
        raw_len = self._read_exact(4)
        resp_len = struct.unpack(">I", raw_len)[0]
        if resp_len == 0 or resp_len > MAX_IPC_PAYLOAD_BYTES:
            raise TradingCoreIPCError(f"Invalid IPC payload size: {resp_len} bytes")
        raw_resp = self._read_exact(resp_len)

        resp_env = pb.IPCEnvelope()
        resp_env.ParseFromString(raw_resp)
        return resp_env

    def _read_exact(self, n: int) -> bytes:
        data = bytearray()
        while len(data) < n:
            chunk = self._sock.recv(n - len(data))
            if not chunk:
                raise ConnectionResetError("Connection closed by peer")
            data.extend(chunk)
        return bytes(data)

    def submit_order(self, req: pb.OrderRequest) -> pb.OrderResponse:
        """同步提交訂單至 Go 撮合核心"""
        resp_env = self._send_envelope(pb.MSG_ORDER_REQ, req.SerializeToString())
        if resp_env.type != pb.MSG_ORDER_RESP:
            raise TradingCoreIPCError(f"Unexpected response type: {resp_env.type}")
        resp = pb.OrderResponse()
        resp.ParseFromString(resp_env.payload)
        return resp

    def cancel_order(self, req: pb.CancelRequest) -> pb.CancelResponse:
        """同步取消訂單"""
        resp_env = self._send_envelope(pb.MSG_CANCEL_REQ, req.SerializeToString())
        if resp_env.type != pb.MSG_CANCEL_RESP:
            raise TradingCoreIPCError(f"Unexpected response type: {resp_env.type}")
        resp = pb.CancelResponse()
        resp.ParseFromString(resp_env.payload)
        return resp

    def check_risk(self, req: pb.RiskCheckRequest) -> pb.RiskCheckResponse:
        """同步執行毫秒風控檢查"""
        resp_env = self._send_envelope(pb.MSG_RISK_REQ, req.SerializeToString())
        if resp_env.type != pb.MSG_RISK_RESP:
            raise TradingCoreIPCError(f"Unexpected response type: {resp_env.type}")
        resp = pb.RiskCheckResponse()
        resp.ParseFromString(resp_env.payload)
        return resp

    def get_heartbeat(self) -> pb.EngineHeartbeat:
        """取得引擎即時心跳與統計"""
        resp_env = self._send_envelope(pb.MSG_HEARTBEAT, b"")
        if resp_env.type != pb.MSG_HEARTBEAT:
            raise TradingCoreIPCError(f"Unexpected response type: {resp_env.type}")
        resp = pb.EngineHeartbeat()
        resp.ParseFromString(resp_env.payload)
        return resp


class AsyncTradingCoreClient:
    """非同步協程 UDS IPC 客戶端 (適用於 asyncio 事件循環)"""

    def __init__(self, socket_path: str = DEFAULT_SOCKET_PATH, timeout: float = 3.0):
        self.socket_path = socket_path
        self.timeout = timeout
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._seq = 0
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        """非同步建立 Unix Domain Socket 連線"""
        if not os.path.exists(self.socket_path):
            raise FileNotFoundError(f"Trading core socket not found at {self.socket_path}")

        self._reader, self._writer = await asyncio.open_unix_connection(self.socket_path)
        logger.debug(f"Connected async to trading core UDS: {self.socket_path}")

    async def close(self) -> None:
        """非同步關閉連線"""
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def _send_envelope(self, msg_type: pb.MessageType, payload: bytes) -> pb.IPCEnvelope:
        async with self._lock:
            if self._writer is None:
                await self.connect()

            coro = self._send_and_recv(msg_type, payload)
            if self.timeout and self.timeout > 0:
                try:
                    return await asyncio.wait_for(coro, timeout=self.timeout)
                except asyncio.TimeoutError as exc:
                    raise TimeoutError(f"Trading core IPC timeout after {self.timeout}s") from exc
            return await coro

    async def _send_and_recv(self, msg_type: pb.MessageType, payload: bytes) -> pb.IPCEnvelope:
        self._seq += 1
        env = pb.IPCEnvelope(
            type=msg_type,
            payload=payload,
            sequence=self._seq,
            timestamp_ns=time.time_ns(),
        )
        serialized_env = env.SerializeToString()
        header = struct.pack(">I", len(serialized_env))

        self._writer.write(header + serialized_env)
        await self._writer.drain()

        raw_len = await self._reader.readexactly(4)
        resp_len = struct.unpack(">I", raw_len)[0]
        if resp_len == 0 or resp_len > MAX_IPC_PAYLOAD_BYTES:
            raise TradingCoreIPCError(f"Invalid IPC payload size: {resp_len} bytes")
        raw_resp = await self._reader.readexactly(resp_len)

        resp_env = pb.IPCEnvelope()
        resp_env.ParseFromString(raw_resp)
        return resp_env

    async def submit_order(self, req: pb.OrderRequest) -> pb.OrderResponse:
        """非同步提交訂單"""
        resp_env = await self._send_envelope(pb.MSG_ORDER_REQ, req.SerializeToString())
        if resp_env.type != pb.MSG_ORDER_RESP:
            raise TradingCoreIPCError(f"Unexpected response type: {resp_env.type}")
        resp = pb.OrderResponse()
        resp.ParseFromString(resp_env.payload)
        return resp

    async def cancel_order(self, req: pb.CancelRequest) -> pb.CancelResponse:
        """非同步取消訂單"""
        resp_env = await self._send_envelope(pb.MSG_CANCEL_REQ, req.SerializeToString())
        if resp_env.type != pb.MSG_CANCEL_RESP:
            raise TradingCoreIPCError(f"Unexpected response type: {resp_env.type}")
        resp = pb.CancelResponse()
        resp.ParseFromString(resp_env.payload)
        return resp

    async def check_risk(self, req: pb.RiskCheckRequest) -> pb.RiskCheckResponse:
        """非同步風控檢查"""
        resp_env = await self._send_envelope(pb.MSG_RISK_REQ, req.SerializeToString())
        if resp_env.type != pb.MSG_RISK_RESP:
            raise TradingCoreIPCError(f"Unexpected response type: {resp_env.type}")
        resp = pb.RiskCheckResponse()
        resp.ParseFromString(resp_env.payload)
        return resp

    async def get_heartbeat(self) -> pb.EngineHeartbeat:
        """非同步獲取心跳"""
        resp_env = await self._send_envelope(pb.MSG_HEARTBEAT, b"")
        if resp_env.type != pb.MSG_HEARTBEAT:
            raise TradingCoreIPCError(f"Unexpected response type: {resp_env.type}")
        resp = pb.EngineHeartbeat()
        resp.ParseFromString(resp_env.payload)
        return resp
