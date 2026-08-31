"""Asynchronous Batched Database Writer.

Buffers high-frequency runtime snapshots and writes them in batched transactions
to drastically reduce disk I/O and prevent SQLite database lock contention.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from core.infrastructure.database.ledger_repository import LedgerRepository
from core.logging import get_logger

logger = get_logger(__name__)


class AsyncDatabaseWriter:
    """Async writer queue that aggregates write operations into periodic batched transactions."""

    _instance: Optional["AsyncDatabaseWriter"] = None
    _lock = threading.Lock()

    def __init__(
        self,
        repository: Optional[LedgerRepository] = None,
        flush_interval: float = 2.0,
        max_batch_size: int = 50,
    ):
        self.repository = repository or LedgerRepository.instance()
        self.flush_interval = flush_interval
        self.max_batch_size = max_batch_size
        self._queue: List[Tuple[str, str, dict, float]] = []
        self._queue_lock = threading.Lock()
        self._running = False
        self._task: Optional[asyncio.Task] = None

    @classmethod
    def instance(cls) -> "AsyncDatabaseWriter":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._lock:
            cls._instance = None

    def enqueue_snapshot(self, bot_id: str, config_path: str, stats: dict, timestamp: Optional[float] = None) -> None:
        """Enqueue a snapshot for batched writing."""
        ts = timestamp if timestamp is not None else time.time()
        with self._queue_lock:
            self._queue.append((bot_id, config_path, stats, ts))
            if len(self._queue) >= self.max_batch_size:
                self.flush_sync()

    def flush_sync(self) -> int:
        """Synchronously flush all queued snapshots in a single transaction."""
        with self._queue_lock:
            if not self._queue:
                return 0
            items = list(self._queue)
            self._queue.clear()

        # Deduplicate to keep latest snapshot per bot_id
        latest_per_bot: Dict[str, Tuple[str, str, dict, float]] = {}
        for item in items:
            bot_id, config_path, stats, ts = item
            latest_per_bot[bot_id] = item

        flushed = 0
        for bot_id, config_path, stats, ts in latest_per_bot.values():
            try:
                self.repository.save_snapshot(bot_id, config_path, stats, ts)
                flushed += 1
            except Exception as e:
                logger.warning(f"⚠️ [AsyncWriter] 寫入快照失敗 ({bot_id}): {e}")

        return flushed

    async def start(self) -> None:
        """Start the background async flush loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._flush_loop(), name="async-db-writer")

    async def stop(self) -> None:
        """Stop writer and perform final flush."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.flush_sync()

    async def _flush_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self.flush_interval)
                self.flush_sync()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"⚠️ [AsyncWriter] 批量寫入迴圈異常: {e}")
