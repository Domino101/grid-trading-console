"""Pure lifecycle and safety helpers shared by the API and the grid engine.

This module must stay free of FastAPI so the engine process and unit tests can
import it in a minimal environment.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from typing import Any, Callable, Iterable, Optional


DEFAULT_CORS_ORIGINS = (
    "http://127.0.0.1:8001",
    "http://localhost:8001",
    "http://127.0.0.1:5173",
    "http://localhost:5173",
)

LIVE_ENABLED_VALUES = {"1", "true", "yes"}
STARTABLE_STATUSES = {"starting", "running", "stopping"}
# 只有真正停止的策略可無 force 刪除。DCA 的 armed/averaging/cooling/completed 等
# runtime 狀態代表任務仍活著（可能持有倉位或隨時再入場），必須比照 running 擋下。
DELETABLE_STATUSES = {"stopped"}


def live_trading_enabled(raw: Optional[str] = None) -> bool:
    value = raw if raw is not None else os.getenv("GRID_LIVE_TRADING_ENABLED", "false")
    return str(value).strip().lower() in LIVE_ENABLED_VALUES


def account_mode_from_yaml(data: Any) -> str:
    if not isinstance(data, dict):
        return "unknown"
    grid_system = data.get("grid_system") or {}
    mode = grid_system.get("account_mode") or grid_system.get("trading_mode")
    if isinstance(mode, str) and mode.lower() in {"live", "paper"}:
        return mode.lower()
    return "unknown"


def finite_number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def optional_int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def start_rejection(account_mode: str, live_enabled: bool) -> Optional[str]:
    """PAPER uses the in-process simulator. LIVE still needs an explicit flag."""
    if account_mode == "live" and not live_enabled:
        return "LIVE 啟動需要 GRID_LIVE_TRADING_ENABLED=true"
    return None


def delete_rejection(lifecycle_status: str, command_status: Optional[str] = None) -> Optional[str]:
    """未帶 force 的刪除守門：只有 stopped 且無 pending 命令可刪。

    未知狀態一律拒絕（fail-closed）：DCA runtime 會回傳 armed/averaging/
    cooling/completed/degraded 等狀態，任何一個都代表任務仍由引擎管理，
    刪除會停止策略並取消掛單。
    """
    if lifecycle_status not in DELETABLE_STATUSES:
        return "只能刪除已停止且無未完成命令的策略"
    if command_status == "pending_engine":
        return "尚有未完成的引擎命令，請先等待或停止後再刪除"
    return None


def data_status_from_runtime(runtime: Optional[dict]) -> str:
    if not runtime:
        return "unavailable"
    if runtime.get("stale"):
        return "stale"
    return "live"


def lifecycle_status(
    desired_running: bool,
    data_status: str,
    command: Optional[dict] = None,
) -> str:
    """Engine heartbeat is the only way a strategy becomes running."""
    if command and command.get("status") == "pending_engine":
        action = command.get("action")
        if action == "start":
            return "starting"
        if action == "stop":
            return "stopping"
    if desired_running and data_status == "live":
        return "running"
    if desired_running:
        return "starting"
    return "stopped"


def cors_settings(env_value: Optional[str]) -> tuple[list[str], bool]:
    raw = (env_value or "").strip()
    if not raw:
        return list(DEFAULT_CORS_ORIGINS), True
    origins = [part.strip() for part in raw.split(",") if part.strip()]
    if origins == ["*"]:
        return ["*"], False
    return origins, True


def connect_sqlite(path: str) -> sqlite3.Connection:
    from core.infrastructure.database.guard import EnvironmentGuard
    safe_path = EnvironmentGuard.get_safe_database_path(path)

    directory = os.path.dirname(safe_path)
    if directory and safe_path != ":memory:":
        os.makedirs(directory, exist_ok=True)
    connection = sqlite3.connect(safe_path, timeout=5)
    connection.row_factory = sqlite3.Row
    if safe_path != ":memory:":
        connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("PRAGMA synchronous=NORMAL")
    return connection


def write_json_atomic(path: str, payload: Any) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".active_grids.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=4, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def registered_config_paths(active_grids: dict) -> list[str]:
    """Only strategies recorded in active_grids; never archived dumps."""
    paths: list[str] = []
    for path in active_grids:
        normalized = str(path).replace("\\", "/")
        if "/archived/" in normalized or normalized.startswith("archived/"):
            continue
        if not normalized.endswith(".yaml"):
            continue
        paths.append(path)
    return sorted(paths)


def iter_hotplug_starts(
    active_status: dict,
    running_paths: Iterable[str],
    exists: Callable[[str], bool],
    account_mode_of: Callable[[str], str],
    live_enabled: bool,
) -> list[str]:
    running = set(running_paths)
    starts: list[str] = []
    for path, enabled in active_status.items():
        if not enabled or path in running or not exists(path):
            continue
        if start_rejection(account_mode_of(path), live_enabled):
            continue
        starts.append(path)
    return starts


def build_grid_read_model(
    *,
    grid_id: str,
    config_path: str,
    symbol: Optional[str],
    exchange: Optional[str],
    grid_type: Optional[str],
    mode: str,
    desired_running: bool,
    runtime: Optional[dict],
    command: Optional[dict],
    lower_price: Optional[float] = None,
    upper_price: Optional[float] = None,
    grid_interval: Optional[float] = None,
    order_amount: Optional[float] = None,
    leverage: Optional[int] = None,
    live_enabled: bool = False,
) -> dict[str, Any]:
    data_status = data_status_from_runtime(runtime)
    status = lifecycle_status(desired_running, data_status, command)
    profit = (runtime or {}).get("profit") or {}
    filled = (runtime or {}).get("filled_orders") or {}
    balance = (runtime or {}).get("balance") or {}
    time_info = (runtime or {}).get("time") or {}
    pending = (runtime or {}).get("pending_orders") or {}
    blocked = start_rejection(mode, live_enabled)

    grid_profit = finite_number(profit.get("grid_profit") if "grid_profit" in profit else profit.get("realized"))
    realized_pnl = finite_number(profit.get("realized"))
    unrealized_pnl = finite_number(profit.get("unrealized"))
    account_unrealized_pnl = finite_number(profit.get("account_unrealized"))
    fees = finite_number(profit.get("fees"))
    raw_net = finite_number(profit.get("net"))
    raw_rate = finite_number(profit.get("rate"))

    has_pnl_data = (grid_profit is not None) or (unrealized_pnl is not None) or (raw_net is not None)
    if has_pnl_data:
        # 總收益 (Total PnL) = 網格毛利 + 浮動損益 (完全對齊幣安與交易所對帳口徑)
        total_pnl = (grid_profit or 0.0) + (unrealized_pnl or 0.0)
        # 純淨收益 (Pure Net PnL) = 總收益 - 手續費 (優先以 raw_net 為準，若無則依公式計算)
        net_pnl = raw_net if raw_net is not None else (total_pnl - (fees or 0.0))
    else:
        total_pnl = None
        net_pnl = None

    pnl_rate = raw_rate
    equity_val = finite_number(balance.get("total"))
    if (pnl_rate is None or pnl_rate == 0.0) and total_pnl and equity_val and equity_val > 0:
        pnl_rate = round((total_pnl / equity_val) * 100, 4)

    return {
        "grid_id": grid_id,
        "config_path": config_path,
        "symbol": symbol,
        "exchange": exchange,
        "grid_type": grid_type,
        "mode": mode,
        "status": status,
        "desired_running": desired_running,
        "command_id": command.get("command_id") if command else None,
        "command_status": command.get("status") if command else None,
        "data_status": data_status,
        "source": "engine_runtime" if runtime else None,
        "last_updated_at": (runtime or {}).get("last_update"),
        "current_price": finite_number((runtime or {}).get("current_price")),
        "current_position": finite_number((runtime or {}).get("current_position")),
        "grid_position": finite_number((runtime or {}).get("grid_position") if "grid_position" in (runtime or {}) else (runtime or {}).get("current_position")),
        "account_position": finite_number((runtime or {}).get("account_position")),
        "equity": equity_val,
        "grid_profit": grid_profit,
        "realized_pnl": realized_pnl,
        "unrealized_pnl": unrealized_pnl,
        "account_unrealized_pnl": account_unrealized_pnl,
        "total_pnl": total_pnl,
        "fees": fees,
        "net_pnl": net_pnl,
        "pnl_rate": pnl_rate,
        "volume": finite_number((runtime or {}).get("volume")),
        "completed_cycles": optional_int(filled.get("cycles")),
        "filled_buy": optional_int(filled.get("buy")),
        "filled_sell": optional_int(filled.get("sell")),
        "pending_orders": optional_int(pending.get("total")),
        "lower_price": lower_price,
        "upper_price": upper_price,
        "grid_interval": grid_interval,
        "order_amount": order_amount,
        "leverage": leverage,
        "last_trade": time_info.get("last_trade") if isinstance(time_info, dict) else None,
        "start_blocked_reason": blocked,
    }


def _roll_aggregate(items: list[dict[str, Any]]) -> dict[str, Any]:
    grid_profit = realized = unrealized = fees = volume = 0.0
    cycles = available = stale = 0
    for item in items:
        if item.get("data_status") == "live":
            available += 1
        elif item.get("data_status") == "stale":
            stale += 1
        if item.get("source") != "engine_runtime":
            continue
        grid_profit += item.get("grid_profit") or item.get("realized_pnl") or 0.0
        realized += item.get("realized_pnl") or 0.0
        unrealized += item.get("unrealized_pnl") or 0.0
        fees += item.get("fees") or 0.0
        volume += item.get("volume") or 0.0
        cycles += item.get("completed_cycles") or 0
    total_pnl = grid_profit + unrealized
    return {
        "total_grids": len(items),
        "running_grids": sum(1 for item in items if item.get("status") == "running"),
        "available_grids": available,
        "stale_grids": stale,
        "grid_profit": grid_profit,
        "realized_pnl": realized,
        "unrealized_pnl": unrealized,
        "total_pnl": total_pnl,
        "fees": fees,
        "net_pnl": total_pnl - fees,
        "volume": volume,
        "completed_cycles": cycles,
    }


def aggregate_read_models(items: list[dict[str, Any]]) -> dict[str, Any]:
    live_items = [item for item in items if item.get("mode") == "live"]
    paper_items = [item for item in items if item.get("mode") == "paper"]
    # Headline KPI is LIVE-only when any LIVE strategy exists, so paper PNL
    # cannot be mistaken for real account profit.
    headline_items = live_items if live_items else [item for item in items if item.get("mode") != "paper"]
    if not headline_items and paper_items:
        headline_items = paper_items
    headline = _roll_aggregate(headline_items)
    overall = "degraded"
    if not items:
        overall = "unavailable"
    elif headline["stale_grids"] == 0 and headline["available_grids"] == headline["total_grids"] and headline["total_grids"]:
        overall = "live"
    return {
        "data_status": overall,
        "aggregate": headline,
        "aggregate_live": _roll_aggregate(live_items),
        "aggregate_paper": _roll_aggregate(paper_items),
        "account_mode": "mixed" if live_items and paper_items else ("live" if live_items else ("paper" if paper_items else "unknown")),
    }
