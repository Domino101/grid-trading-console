"""Dashboard read model endpoints.

This module exposes a single snapshot endpoint so the browser does not need to
issue one request per grid.  It intentionally preserves data availability:
unknown runtime values remain ``null`` instead of being represented as zero.
"""

from __future__ import annotations

import os
import time
from typing import Any, Optional

from fastapi import APIRouter, Depends

from auth import verify_token
from grid_state_store import GridStateStore
from schemas import DashboardAggregate, DashboardGridItem, DashboardSnapshotResponse
from yaml_generator import GridYAMLGenerator
from routes.grids import _build_grid_info, _get_project_root, _read_active_grids

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])


def _grid_mode(config_path: str) -> str:
    """Read an explicitly configured account mode without guessing from status."""
    # Account mode is not present in legacy YAML files.  Returning unknown is
    # safer than labelling a strategy as paper or live based on an assumption.
    import yaml

    path = os.path.join(_get_project_root(), config_path)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        grid_system = data.get("grid_system", {})
        mode = grid_system.get("account_mode") or grid_system.get("trading_mode")
        return mode.lower() if isinstance(mode, str) and mode.lower() in {"live", "paper"} else "unknown"
    except (OSError, yaml.YAMLError):
        return "unknown"


def _number(value: Any) -> Optional[float]:
    """Return finite numeric data only; malformed/unknown values remain null."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


@router.get("/snapshot", response_model=DashboardSnapshotResponse)
async def get_dashboard_snapshot(_token: str = Depends(verify_token)):
    """Return all overview data from one coherent, local read pass.

    Runtime data is fed by the grid engine into :class:`GridStateStore`.
    Configured grids without a runtime heartbeat are labelled ``unavailable``;
    stale values are retained for diagnostics but never presented as live.
    """
    active_grids = _read_active_grids()
    config_dir = os.path.join(_get_project_root(), "config", "grid")
    known_paths = set(active_grids)
    if os.path.isdir(config_dir):
        known_paths.update(
            f"config/grid/{name}"
            for name in os.listdir(config_dir)
            if name.endswith(".yaml") and not name.startswith("README")
        )

    store = GridStateStore.instance()
    items: list[DashboardGridItem] = []
    realized_total = 0.0
    unrealized_total = 0.0
    fees_total = 0.0
    volume_total = 0.0
    completed_cycles = 0
    available_count = 0
    stale_count = 0

    for config_path in sorted(known_paths):
        info = _build_grid_info(config_path, active_grids)
        runtime = store.get(config_path)
        desired_running = info.status == "running"

        if runtime is None:
            data_status = "unavailable"
        elif runtime.get("stale", False):
            data_status = "stale"
            stale_count += 1
        else:
            data_status = "live"
            available_count += 1

        # active_grids.json expresses desired state only. A strategy becomes
        # running only after an engine runtime heartbeat is observed.
        lifecycle_status = "running" if desired_running and data_status == "live" else (
            "starting" if desired_running else "stopped"
        )

        profit = runtime.get("profit", {}) if runtime else {}
        filled = runtime.get("filled_orders", {}) if runtime else {}
        balance = runtime.get("balance", {}) if runtime else {}
        time_info = runtime.get("time", {}) if runtime else {}
        realized = _number(profit.get("realized"))
        unrealized = _number(profit.get("unrealized"))
        fees = _number(profit.get("fees"))
        volume = _number(runtime.get("volume")) if runtime else None
        cycles = filled.get("cycles") if isinstance(filled.get("cycles"), int) else None

        # Totals include only a known runtime record, never fabricated values.
        if runtime is not None:
            realized_total += realized or 0.0
            unrealized_total += unrealized or 0.0
            fees_total += fees or 0.0
            volume_total += volume or 0.0
            completed_cycles += cycles or 0

        items.append(DashboardGridItem(
            grid_id=info.grid_id,
            config_path=config_path,
            symbol=info.symbol,
            exchange=info.exchange,
            grid_type=info.grid_type,
            mode=_grid_mode(config_path),
            status=lifecycle_status,
            data_status=data_status,
            last_updated_at=runtime.get("last_update") if runtime else None,
            current_price=_number(runtime.get("current_price")) if runtime else None,
            current_position=_number(runtime.get("current_position")) if runtime else None,
            equity=_number(balance.get("total")),
            realized_pnl=realized,
            unrealized_pnl=unrealized,
            fees=fees,
            net_pnl=_number(profit.get("net")),
            pnl_rate=_number(profit.get("rate")),
            volume=volume,
            completed_cycles=cycles,
            filled_buy=filled.get("buy") if isinstance(filled.get("buy"), int) else None,
            filled_sell=filled.get("sell") if isinstance(filled.get("sell"), int) else None,
            pending_orders=(runtime.get("pending_orders") or {}).get("total") if runtime else None,
            lower_price=info.lower_price,
            upper_price=info.upper_price,
            grid_interval=info.grid_interval,
            order_amount=info.order_amount,
            leverage=info.leverage,
            last_trade=time_info.get("last_trade") if isinstance(time_info, dict) else None,
        ))

    aggregate = DashboardAggregate(
        total_grids=len(items),
        running_grids=sum(1 for item in items if item.status == "running"),
        available_grids=available_count,
        stale_grids=stale_count,
        realized_pnl=realized_total,
        unrealized_pnl=unrealized_total,
        fees=fees_total,
        net_pnl=realized_total + unrealized_total - fees_total,
        volume=volume_total,
        completed_cycles=completed_cycles,
    )
    overall_status = "live" if items and stale_count == 0 and available_count == len(items) else "degraded"
    if not items:
        overall_status = "unavailable"

    return DashboardSnapshotResponse(
        generated_at=time.time(),
        data_status=overall_status,
        aggregate=aggregate,
        grids=items,
    )
