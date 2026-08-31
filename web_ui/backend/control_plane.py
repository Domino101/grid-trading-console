"""Engine-facing write path for runtime snapshots and lifecycle commands.

Imported by ``run_multiple_grids.py`` and the API process. Does not import
FastAPI. Both processes share the same SQLite files under ``data/``.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional

from core.infrastructure.database.async_writer import AsyncDatabaseWriter, async_write_enabled
from core.infrastructure.database.redis_support import RedisEventBus
from core.infrastructure.identity import resolve_bot_id
from web_ui.backend.command_repository import GridCommandRepository
from web_ui.backend.runtime_repository import RuntimeSnapshotRepository

_event_bus = RedisEventBus()


def _grid_id(config_path: str) -> str:
    # UI/命令層身份使用檔名 stem；帳本 key 由 RuntimeSnapshotRepository.save 內的
    # resolve_bot_id（metadata.bot_id）負責。
    return Path(config_path).stem


def publish_runtime(config_path: str, stats: dict, updated_at: Optional[float] = None) -> None:
    """Persist an engine heartbeat and promote a pending start command."""
    ts = updated_at if updated_at is not None else time.time()
    # P1-5: 引擎啟動 writer 後走批量非同步寫入；否則（測試/停用）同步寫入。
    writer = AsyncDatabaseWriter.instance()
    if async_write_enabled() and writer.is_running():
        writer.enqueue_snapshot(resolve_bot_id(config_path), config_path, stats, ts)
    else:
        RuntimeSnapshotRepository.instance().save(config_path, stats, updated_at=ts)

    # 命令 promotion 保持同步（生命週期關鍵）
    repository = GridCommandRepository.instance()
    command = repository.latest_for_grid(_grid_id(config_path))
    if command and command.get("action") == "start" and command.get("status") in {"pending_engine", "accepted", "timed_out"}:
        repository.update_status(command["command_id"], "running", "engine runtime heartbeat")

    # P2-4: Redis Pub/Sub 事件推送（可拋棄，失敗不影響主路徑）
    _event_bus.publish("runtime", {"config_path": config_path, "bot_id": resolve_bot_id(config_path), "updated_at": ts})


def publish_lifecycle(config_path: str, status: str, detail: Optional[str] = None) -> None:
    """Record a machine-readable engine acknowledgement."""
    if status not in {"running", "stopped", "failed"}:
        return
    grid_id = _grid_id(config_path)
    repository = GridCommandRepository.instance()
    repository.record_event(grid_id, "engine_lifecycle", status, detail)

    # P3-1: 生命週期寫入 canonical 事件流（state_transition，冪等去重）
    try:
        from core.infrastructure.database.bot_events import emit_bot_event
        import time as _time
        emit_bot_event(
            resolve_bot_id(config_path),
            "state_transition",
            payload={
                "config_path": config_path,
                "grid_id": grid_id,
                "status": status,
                "detail": detail or "",
            },
            dedupe_key=f"lifecycle:{grid_id}:{status}:{int(_time.time() // 1)}:{(detail or '')[:64]}",
        )
    except Exception:
        pass  # 事件擷取不影響命令/生命週期主路徑
    command = repository.latest_for_grid(grid_id)
    if command is None:
        return
    expected = "start" if status == "running" else "stop"
    if status == "failed" or command.get("action") == expected:
        if command.get("status") in {"pending_engine", "accepted", "timed_out"}:
            repository.update_status(command["command_id"], status, detail)

    # P2-4: lifecycle events -> Redis Pub/Sub
    _event_bus.publish("lifecycle", {"config_path": config_path, "status": status, "detail": detail or ""})


def backend_sys_path(project_root: str) -> str:
    return os.path.join(project_root, "web_ui", "backend")
