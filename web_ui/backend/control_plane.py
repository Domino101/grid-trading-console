"""Engine-facing write path for runtime snapshots and lifecycle commands.

Imported by ``run_multiple_grids.py`` and the API process. Does not import
FastAPI. Both processes share the same SQLite files under ``data/``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from command_repository import GridCommandRepository
from runtime_repository import RuntimeSnapshotRepository


def _grid_id(config_path: str) -> str:
    return Path(config_path).stem


def publish_runtime(config_path: str, stats: dict, updated_at: Optional[float] = None) -> None:
    """Persist an engine heartbeat and promote a pending start command."""
    RuntimeSnapshotRepository.instance().save(config_path, stats, updated_at=updated_at)
    repository = GridCommandRepository.instance()
    command = repository.latest_for_grid(_grid_id(config_path))
    if command and command.get("action") == "start" and command.get("status") == "pending_engine":
        repository.update_status(command["command_id"], "running", "engine runtime heartbeat")


def publish_lifecycle(config_path: str, status: str, detail: Optional[str] = None) -> None:
    """Record a machine-readable engine acknowledgement."""
    if status not in {"running", "stopped", "failed"}:
        return
    grid_id = _grid_id(config_path)
    repository = GridCommandRepository.instance()
    repository.record_event(grid_id, "engine_lifecycle", status, detail)
    command = repository.latest_for_grid(grid_id)
    if command is None or command.get("status") != "pending_engine":
        return
    expected = "start" if status == "running" else "stop"
    if status == "failed" or command.get("action") == expected:
        repository.update_status(command["command_id"], status, detail)


def backend_sys_path(project_root: str) -> str:
    return os.path.join(project_root, "web_ui", "backend")
