"""Durable command journal for grid lifecycle requests."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from typing import Any, Optional

from runtime_repository import RuntimeSnapshotRepository


class GridCommandRepository:
    _instance: Optional["GridCommandRepository"] = None
    _instance_lock = threading.Lock()

    def __init__(self, database_path: Optional[str] = None):
        self.database_path = database_path or RuntimeSnapshotRepository.instance().database_path
        self._lock = threading.Lock()
        self._initialize()

    @classmethod
    def instance(cls) -> "GridCommandRepository":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS grid_commands (
                    command_id TEXT PRIMARY KEY,
                    grid_id TEXT NOT NULL,
                    config_path TEXT NOT NULL,
                    action TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source TEXT NOT NULL,
                    reason TEXT,
                    detail TEXT,
                    idempotency_key TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)
            # Existing Phase 1 databases are migrated in place.
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(grid_commands)")}
            if "idempotency_key" not in columns:
                connection.execute("ALTER TABLE grid_commands ADD COLUMN idempotency_key TEXT")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_grid_commands_grid_created ON grid_commands(grid_id, created_at DESC)")
            connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_grid_commands_idempotency ON grid_commands(action, idempotency_key) WHERE idempotency_key IS NOT NULL")

    def create(self, grid_id: str, config_path: str, action: str, source: str, reason: Optional[str] = None,
               idempotency_key: Optional[str] = None) -> dict[str, Any]:
        command_id = str(uuid.uuid4())
        now = time.time()
        command = {
            "command_id": command_id, "grid_id": grid_id, "config_path": config_path,
            "action": action, "status": "accepted", "source": source, "reason": reason,
            "detail": None, "idempotency_key": idempotency_key, "created_at": now, "updated_at": now,
        }
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO grid_commands(command_id, grid_id, config_path, action, status, source, reason, detail, idempotency_key, created_at, updated_at)
                   VALUES(:command_id, :grid_id, :config_path, :action, :status, :source, :reason, :detail, :idempotency_key, :created_at, :updated_at)""",
                command,
            )
        return command

    def update_status(self, command_id: str, status: str, detail: Optional[str] = None) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE grid_commands SET status = ?, detail = ?, updated_at = ? WHERE command_id = ?",
                (status, detail, time.time(), command_id),
            )

    def get(self, command_id: str) -> Optional[dict[str, Any]]:
        self.mark_timeouts()
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT * FROM grid_commands WHERE command_id = ?", (command_id,)).fetchone()
        return self._row_to_dict(row)

    def get_by_idempotency_key(self, action: str, key: str) -> Optional[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM grid_commands WHERE action = ? AND idempotency_key = ?", (action, key)
            ).fetchone()
        return self._row_to_dict(row)

    def mark_timeouts(self, timeout_seconds: int = 90) -> None:
        """Do not claim an engine operation succeeded when no acknowledgement arrives."""
        deadline = time.time() - timeout_seconds
        with self._lock, self._connect() as connection:
            connection.execute(
                """UPDATE grid_commands SET status = 'timed_out',
                   detail = '等待引擎確認逾時', updated_at = ?
                   WHERE status = 'pending_engine' AND updated_at < ?""",
                (time.time(), deadline),
            )

    def latest_for_grid(self, grid_id: str) -> Optional[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT * FROM grid_commands WHERE grid_id = ? ORDER BY created_at DESC LIMIT 1", (grid_id,)).fetchone()
        return self._row_to_dict(row)

    @staticmethod
    def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict[str, Any]]:
        return dict(row) if row is not None else None
