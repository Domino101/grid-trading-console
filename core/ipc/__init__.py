"""
High-Performance IPC Module for Polyglot Core Evolution (Task P3).
Provides Protobuf message definitions and Unix Domain Socket (UDS) clients.
"""

from core.ipc.uds_client import (
    TradingCoreClient,
    AsyncTradingCoreClient,
    TradingCoreIPCError,
    DEFAULT_SOCKET_PATH,
)

__all__ = [
    "TradingCoreClient",
    "AsyncTradingCoreClient",
    "TradingCoreIPCError",
    "DEFAULT_SOCKET_PATH",
]
