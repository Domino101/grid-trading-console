"""Dashboard read model endpoints.

This module exposes a single snapshot endpoint so the browser does not need to
issue one request per grid.  It intentionally preserves data availability:
unknown runtime values remain ``null`` instead of being represented as zero.
"""

from __future__ import annotations

import os
import time

from fastapi import APIRouter, Depends

from auth import verify_token
from grid_state_store import GridStateStore
from command_repository import GridCommandRepository
from lifecycle import (
    account_mode_from_yaml,
    aggregate_read_models,
    build_grid_read_model,
    live_trading_enabled,
)
from schemas import DashboardAggregate, DashboardGridItem, DashboardSnapshotResponse
from routes.grids import _build_grid_info, _get_project_root, _read_active_grids, _read_yaml_config

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])


@router.get("/snapshot", response_model=DashboardSnapshotResponse)
async def get_dashboard_snapshot(_token: str = Depends(verify_token)):
    """Return all overview data from one coherent, local read pass."""
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
    command_repository = GridCommandRepository.instance()
    command_repository.mark_timeouts()
    live_enabled = live_trading_enabled()
    items: list[DashboardGridItem] = []

    for config_path in sorted(known_paths):
        info = _build_grid_info(config_path, active_grids)
        yaml_data = _read_yaml_config(config_path)
        runtime = store.get(config_path)
        command = command_repository.latest_for_grid(info.grid_id)
        model = build_grid_read_model(
            grid_id=info.grid_id,
            config_path=config_path,
            symbol=info.symbol,
            exchange=info.exchange,
            grid_type=info.grid_type,
            mode=account_mode_from_yaml(yaml_data),
            desired_running=bool(active_grids.get(config_path)),
            runtime=runtime,
            command=command,
            lower_price=info.lower_price,
            upper_price=info.upper_price,
            grid_interval=info.grid_interval,
            order_amount=info.order_amount,
            leverage=info.leverage,
            live_enabled=live_enabled,
        )
        items.append(DashboardGridItem(**model))

    rolled = aggregate_read_models([item.model_dump() for item in items])
    return DashboardSnapshotResponse(
        generated_at=time.time(),
        data_status=rolled["data_status"],
        account_mode=rolled["account_mode"],
        aggregate=DashboardAggregate(**rolled["aggregate"]),
        aggregate_live=DashboardAggregate(**rolled["aggregate_live"]),
        aggregate_paper=DashboardAggregate(**rolled["aggregate_paper"]),
        grids=items,
    )
