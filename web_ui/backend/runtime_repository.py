"""Small durable repository for dashboard runtime snapshots.

SQLite is deliberately used as the Phase 1 single-host baseline.  The API can
restart without losing the last engine heartbeat, while the repository interface
can later be replaced by Redis/PostgreSQL for a multi-instance deployment.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Optional


class RuntimeSnapshotRepository:
    _instance: Optional["RuntimeSnapshotRepository"] = None
    _instance_lock = threading.Lock()

    def __init__(self, database_path: Optional[str] = None):
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
        self.database_path = database_path or os.path.join(root, "data", "web_ui_runtime.sqlite3")
        os.makedirs(os.path.dirname(self.database_path), exist_ok=True)
        self._lock = threading.Lock()
        self._initialize()

    @classmethod
    def instance(cls) -> "RuntimeSnapshotRepository":
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
                CREATE TABLE IF NOT EXISTS grid_runtime_snapshots (
                    config_path TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)

    def save(self, config_path: str, stats: dict, updated_at: Optional[float] = None) -> float:
        timestamp = updated_at if updated_at is not None else time.time()
        payload = json.dumps(stats, ensure_ascii=False, separators=(",", ":"), default=str)
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO grid_runtime_snapshots(config_path, payload, updated_at)
                   VALUES(?, ?, ?)
                   ON CONFLICT(config_path) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at""",
                (config_path, payload, timestamp),
            )
        return timestamp

    def load_all(self) -> dict[str, tuple[dict, float]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute("SELECT config_path, payload, updated_at FROM grid_runtime_snapshots").fetchall()
        snapshots: dict[str, tuple[dict, float]] = {}
        for row in rows:
            try:
                payload = json.loads(row["payload"])
                if isinstance(payload, dict):
                    snapshots[row["config_path"]] = (payload, float(row["updated_at"]))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        return snapshots

    def remove(self, config_path: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM grid_runtime_snapshots WHERE config_path = ?", (config_path,))
