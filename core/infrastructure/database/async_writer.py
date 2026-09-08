"""Asynchronous Batched Database Writer.

Buffers high-frequency runtime snapshots and writes them in batched transactions
to drastically reduce disk I/O and prevent SQLite database lock contention.

P1-5 changes:
- The default save target performs the FULL ``RuntimeSnapshotRepository.save``
  (bot registration + fills + OCC position + snapshot), so routing the engine
  heartbeat through this writer does not drop the DCA fill extraction.
- A bounded retry queue re-attempts failed writes before dropping them.
- ``enqueue_snapshot`` only queues; callers that want a synchronous fallback
  should check ``is_running()`` (the engine starts the writer in ``main()``,
  so tests/fallback paths that never start it can stay synchronous).
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.logging import get_logger

logger = get_logger(__name__)

# Queue item: (bot_id, config_path, stats, ts, retries)
_QueueItem = Tuple[str, str, dict, float, int]


def async_write_enabled() -> bool:
    """P1-5: env gate for the async batched write path (default enabled)."""
    return os.getenv("GRID_DB_ASYNC_WRITE", "1").strip().lower() not in {"0", "false", "off", "no"}


class AsyncDatabaseWriter:
    """Async writer queue that aggregates write operations into periodic batched transactions."""

    _instance: Optional["AsyncDatabaseWriter"] = None
    _lock = threading.Lock()

    def __init__(
        self,
        repository: Optional[Any] = None,
        save_fn: Optional[Callable[[str, dict, float], None]] = None,
        flush_interval: float = 2.0,
        max_batch_size: int = 50,
        max_retries: int = 3,
    ):
        # ``repository`` may be a LedgerRepository (save_snapshot) or a
        # RuntimeSnapshotRepository (save). ``save_fn(config_path, stats, ts)``
        # overrides both when provided.
        self._repository = repository
        self._save_fn = save_fn
        self.flush_interval = flush_interval
        self.max_batch_size = max_batch_size
        self.max_retries = max_retries
        self._queue: List[_QueueItem] = []
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

    def is_running(self) -> bool:
        """True when the background flush loop has been started."""
        return self._running

    def enqueue_snapshot(self, bot_id: str, config_path: str, stats: dict, timestamp: Optional[float] = None) -> None:
        """Enqueue a snapshot for batched writing (deduplicated on flush)."""
        ts = timestamp if timestamp is not None else time.time()
        with self._queue_lock:
            self._queue.append((bot_id, config_path, stats, ts, 0))
            if len(self._queue) >= self.max_batch_size:
                self.flush_sync()

    def flush_sync(self) -> int:
        """Synchronously flush all queued snapshots; bounded retry on failure."""
        with self._queue_lock:
            if not self._queue:
                return 0
            items = list(self._queue)
            self._queue.clear()

        # Deduplicate to keep latest snapshot per bot_id
        latest_per_bot: Dict[str, _QueueItem] = {}
        for item in items:
            latest_per_bot[item[0]] = item

        flushed = 0
        for item in latest_per_bot.values():
            bot_id, config_path, stats, ts, retries = item
            try:
                self._flush_one(bot_id, config_path, stats, ts)
                flushed += 1
            except Exception as exc:
                if retries < self.max_retries:
                    logger.warning(
                        f"⚠️ [AsyncWriter] 寫入快照失敗，稍後重試 ({bot_id}, retry={retries + 1}): {exc}"
                    )
                    with self._queue_lock:
                        self._queue.append((bot_id, config_path, stats, ts, retries + 1))
                else:
                    logger.error(
                        f"❌ [AsyncWriter] 寫入快照失敗且已達重試上限，捨棄 ({bot_id}): {exc}"
                    )
        return flushed

    def _flush_one(self, bot_id: str, config_path: str, stats: dict, ts: float) -> None:
        """Perform one full write (full save path preferred)."""
        if self._save_fn is not None:
            self._save_fn(config_path, stats, ts)
            return

        repo = self._repository
        if repo is None:
            # Default: full RuntimeSnapshotRepository.save (lazy import avoids cycle)
            from web_ui.backend.runtime_repository import RuntimeSnapshotRepository
            repo = RuntimeSnapshotRepository.instance()

        save = getattr(repo, "save", None)
        if callable(save):
            save(config_path, stats, updated_at=ts)
        else:
            repo.save_snapshot(bot_id, config_path, stats, ts)

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
                await asyncio.to_thread(self.flush_sync)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"⚠️ [AsyncWriter] 批量寫入迴圈異常: {e}")
