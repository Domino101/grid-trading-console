import os
import threading
import time
from typing import Optional, Dict


class GridStateStore:
    """In-process cache over the durable runtime repository."""

    _instance = None
    _lock = threading.Lock()
    STALE_THRESHOLD = 180
    REFRESH_TTL = 0.5

    def __init__(self):
        self._states: Dict[str, dict] = {}
        self._updated_at: Dict[str, float] = {}
        self._last_refresh = 0.0
        from web_ui.backend.runtime_repository import RuntimeSnapshotRepository
        self._repository = RuntimeSnapshotRepository.instance()
        self._refresh_from_repository(force=True)

    @classmethod
    def instance(cls) -> "GridStateStore":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._lock:
            cls._instance = None

    def _refresh_from_repository(self, force: bool = False) -> None:
        now = time.time()
        if not force and (now - self._last_refresh) < self.REFRESH_TTL:
            return
        for config_path, (stats, updated_at) in self._repository.load_all().items():
            current = self._updated_at.get(config_path, 0)
            if updated_at >= current:
                self._states[config_path] = stats
                self._updated_at[config_path] = updated_at
        self._last_refresh = now

    def update(self, config_path: str, stats: dict):
        updated_at = time.time()
        with self._lock:
            self._states[config_path] = stats
            self._updated_at[config_path] = updated_at
        self._repository.save(config_path, stats, updated_at)
        try:
            from web_ui.backend.command_repository import GridCommandRepository
            grid_id = os.path.splitext(os.path.basename(config_path))[0]
            command = GridCommandRepository.instance().latest_for_grid(grid_id)
            if command and command["action"] == "start" and command["status"] == "pending_engine":
                GridCommandRepository.instance().update_status(command["command_id"], "running")
        except Exception:
            pass

    def _decorate(self, config_path: str, stats: dict, updated: float) -> dict:
        grid_id = stats.get("grid_id")
        if not grid_id:
            grid_id = os.path.splitext(os.path.basename(config_path))[0]
        return {
            **stats,
            "grid_id": grid_id,
            "last_update": updated,
            "stale": (time.time() - updated) > self.STALE_THRESHOLD,
        }

    def get(self, config_path: str) -> Optional[dict]:
        with self._lock:
            self._refresh_from_repository()
            stats = self._states.get(config_path)
            if stats is None:
                return None
            return self._decorate(config_path, stats, self._updated_at.get(config_path, 0))

    def get_all(self) -> dict:
        with self._lock:
            self._refresh_from_repository()
            return {
                path: self._decorate(path, stats, self._updated_at.get(path, 0))
                for path, stats in self._states.items()
            }

    def remove(self, config_path: str):
        with self._lock:
            self._states.pop(config_path, None)
            self._updated_at.pop(config_path, None)
        self._repository.remove(config_path)

    def get_summary(self) -> dict:
        all_stats = self.get_all()
        total_realized = 0.0
        total_unrealized = 0.0
        grids = []
        for path, stats in all_stats.items():
            profit = stats.get("profit") or {}
            realized = profit.get("realized")
            unrealized = profit.get("unrealized")
            if isinstance(realized, (int, float)) and not isinstance(realized, bool):
                total_realized += float(realized)
            if isinstance(unrealized, (int, float)) and not isinstance(unrealized, bool):
                total_unrealized += float(unrealized)
            grids.append({
                "grid_id": stats.get("grid_id"),
                "config_path": path,
                "symbol": stats.get("symbol"),
                "realized_pnl": realized if isinstance(realized, (int, float)) else None,
                "unrealized_pnl": unrealized if isinstance(unrealized, (int, float)) else None,
                "total_pnl": (
                    (float(realized) if isinstance(realized, (int, float)) else 0.0)
                    + (float(unrealized) if isinstance(unrealized, (int, float)) else 0.0)
                    if isinstance(realized, (int, float)) or isinstance(unrealized, (int, float))
                    else None
                ),
                "current_price": stats.get("current_price"),
                "current_position": stats.get("current_position"),
                "status": stats.get("status"),
                "stale": stats.get("stale", False),
            })
        return {
            "total_grids": len(all_stats),
            "total_realized_pnl": total_realized,
            "total_unrealized_pnl": total_unrealized,
            "total_pnl": total_realized + total_unrealized,
            "grids": grids,
        }
