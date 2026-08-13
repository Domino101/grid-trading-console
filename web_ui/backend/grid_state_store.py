import threading
import time
from typing import Optional, Dict

class GridStateStore:
    """進程內共享的網格狀態儲存（零 DB 開銷）"""

    _instance = None
    _lock = threading.Lock()
    STALE_THRESHOLD = 180  # 秒

    def __init__(self):
        self._states: Dict[str, dict] = {}
        self._updated_at: Dict[str, float] = {}
        # Retain the last engine snapshots across a FastAPI restart. Runtime
        # freshness is still calculated from the original engine timestamp.
        from runtime_repository import RuntimeSnapshotRepository
        self._repository = RuntimeSnapshotRepository.instance()
        for config_path, (stats, updated_at) in self._repository.load_all().items():
            self._states[config_path] = stats
            self._updated_at[config_path] = updated_at

    @classmethod
    def instance(cls) -> "GridStateStore":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def update(self, config_path: str, stats: dict):
        updated_at = time.time()
        with self._lock:
            self._states[config_path] = stats
            self._updated_at[config_path] = updated_at
        self._repository.save(config_path, stats, updated_at)

    def get(self, config_path: str) -> Optional[dict]:
        with self._lock:
            stats = self._states.get(config_path)
            if stats is None:
                return None
            updated = self._updated_at.get(config_path, 0)
            
            # 動態補上 grid_id
            import os
            grid_id = stats.get("grid_id")
            if not grid_id:
                base = os.path.basename(config_path)
                grid_id = os.path.splitext(base)[0]
                
            return {
                **stats,
                "grid_id": grid_id,
                "last_update": updated,
                "stale": (time.time() - updated) > self.STALE_THRESHOLD,
            }

    def get_all(self) -> dict:
        with self._lock:
            now = time.time()
            import os
            ret = {}
            for path, stats in self._states.items():
                updated = self._updated_at.get(path, 0)
                grid_id = stats.get("grid_id")
                if not grid_id:
                    base = os.path.basename(path)
                    grid_id = os.path.splitext(base)[0]
                ret[path] = {
                    **stats,
                    "grid_id": grid_id,
                    "last_update": updated,
                    "stale": (now - updated) > self.STALE_THRESHOLD,
                }
            return ret

    def remove(self, config_path: str):
        with self._lock:
            self._states.pop(config_path, None)
            self._updated_at.pop(config_path, None)
        self._repository.remove(config_path)

    def get_summary(self) -> dict:
        with self._lock:
            total_realized = 0.0
            total_unrealized = 0.0
            grids = []
            for path, stats in self._states.items():
                profit = stats.get("profit", {})
                realized = profit.get("realized", 0.0)
                unrealized = profit.get("unrealized", 0.0)
                total_realized += realized
                total_unrealized += unrealized
                
                # grid_id 可以從 path 推導，也可以從 stats 取出
                # 為了避免沒有 grid_id，這裡嘗試推導
                grid_id = stats.get("grid_id")
                if not grid_id:
                    # config/grid/binance-neutral-sol-usdc.yaml -> binance-neutral-sol-usdc
                    import os
                    base = os.path.basename(path)
                    grid_id = os.path.splitext(base)[0]

                grids.append({
                    "grid_id": grid_id,
                    "config_path": path,
                    "symbol": stats.get("symbol", "unknown"),
                    "realized_pnl": realized,
                    "unrealized_pnl": unrealized,
                    "total_pnl": realized + unrealized,
                    "current_price": stats.get("current_price", 0.0),
                    "current_position": stats.get("current_position", 0.0),
                    "status": stats.get("status", "running"),
                    "stale": (time.time() - self._updated_at.get(path, 0)) > self.STALE_THRESHOLD,
                })
            return {
                "total_grids": len(self._states),
                "total_realized_pnl": total_realized,
                "total_unrealized_pnl": total_unrealized,
                "total_pnl": total_realized + total_unrealized,
                "grids": grids,
            }
