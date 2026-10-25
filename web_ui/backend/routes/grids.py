"""
Grid Lifecycle CRUD 端點

POST   /api/v1/grids              — 建立新網格
GET    /api/v1/grids              — 列出所有網格
GET    /api/v1/grids/{grid_id}    — 單一網格詳細資訊
POST   /api/v1/grids/{grid_id}/start  — 啟動網格
POST   /api/v1/grids/{grid_id}/stop   — 停止網格
DELETE /api/v1/grids/{grid_id}    — 停止+刪除
"""

import os
import sys
import json
import logging
import sqlite3
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

import yaml
from fastapi import APIRouter, Depends, Header, HTTPException, status, Query
from web_ui.backend.audit import AuditLogger

# 加入專案根目錄到 sys.path 以便匯入 core 模組
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../"))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from web_ui.backend.auth import verify_token
from web_ui.backend.schemas import (
    CreateGridRequest,
    GridCreateResponse,
    GridActionResponse,
    GridInfo,
    GridListResponse,
    GridStatsResponse,
    GridHistoryResponse,
    GridOrdersResponse,
    GridFillsResponse,
    GridSummaryResponse,
)
from web_ui.backend.yaml_generator import GridYAMLGenerator
from web_ui.backend.safety_guard import SafetyGuard
from web_ui.backend.grid_state_store import GridStateStore
from web_ui.backend.command_repository import GridCommandRepository
from web_ui.backend.runtime_repository import RuntimeSnapshotRepository
from core.infrastructure.database.redis_support import RedisCommandQueue
from web_ui.backend.lifecycle import (
    account_mode_from_yaml,
    build_grid_read_model,
    data_status_from_runtime,
    delete_rejection,
    finite_number,
    live_trading_enabled,
    optional_int,
    registered_config_paths,
    start_rejection,
    write_json_atomic,
)

logger = logging.getLogger("GridAPI.Grids")

router = APIRouter(prefix="/api/v1/grids", tags=["grids"])

# ── 共用工具 ──

_generator = GridYAMLGenerator()


def _get_project_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../"))


def _get_active_grids_path() -> str:
    return os.path.join(_get_project_root(), "config", "active_grids.json")


def _read_active_grids() -> dict:
    path = _get_active_grids_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"讀取 active_grids.json 失敗: {e}")
        return {}


def _write_active_grids(data: dict) -> None:
    write_json_atomic(_get_active_grids_path(), data)


def _read_yaml_config(config_path: str) -> Optional[dict]:
    """安全地讀取 YAML 配置檔"""
    project_root = _get_project_root()
    abs_path = os.path.join(project_root, config_path)

    # 路徑安全檢查：確保在 config/grid/ 目錄下
    config_grid_dir = os.path.join(project_root, "config", "grid")
    resolved = os.path.realpath(abs_path)
    if not resolved.startswith(os.path.realpath(config_grid_dir) + os.sep):
        return None

    if not os.path.exists(abs_path):
        return None
    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except Exception:
        return None


def _decimal_places(value: object) -> int:
    """Count decimal places without accepting a binary float representation."""
    try:
        exponent = Decimal(str(value)).as_tuple().exponent
        return max(0, -exponent)
    except (InvalidOperation, ValueError):
        return 99


def _validate_market_rules(params: dict, market_info) -> list[str]:
    """Server-side final check; client validation is only a convenience."""
    errors: list[str] = []
    lower, upper = params.get("lower_price"), params.get("upper_price")
    amount = params.get("order_amount")
    if lower is not None and upper is not None and amount is not None:
        notional = ((float(lower) + float(upper)) / 2) * float(amount)
        if market_info.min_notional is not None and notional < market_info.min_notional:
            errors.append(f"每格名義額 {notional:.8g} 低於交易所最低要求 {market_info.min_notional}")
    if market_info.price_precision is not None:
        for field in ("lower_price", "upper_price", "grid_interval"):
            if params.get(field) is not None and _decimal_places(params[field]) > market_info.price_precision:
                errors.append(f"{field} 小數位超過交易所價格精度 {market_info.price_precision}")
    if market_info.quantity_precision is not None and amount is not None and _decimal_places(amount) > market_info.quantity_precision:
        errors.append(f"order_amount 小數位超過交易所數量精度 {market_info.quantity_precision}")
    return errors


def _grid_read_model(config_path: str, active_grids: dict) -> dict:
    """Single lifecycle read model used by list/stats/detail/delete."""
    grid_id = GridYAMLGenerator.grid_id_from_path(config_path)
    yaml_data = _read_yaml_config(config_path) or {}
    grid_system = yaml_data.get("grid_system") or {}
    price_range = grid_system.get("price_range") or {}
    runtime = GridStateStore.instance().get(config_path)
    command = GridCommandRepository.instance().latest_for_grid(grid_id)
    leverage = optional_int(grid_system.get("leverage"))
    if leverage is None:
        raw_leverage = finite_number(grid_system.get("leverage"))
        leverage = int(raw_leverage) if raw_leverage is not None else None
    return build_grid_read_model(
        grid_id=grid_id,
        config_path=config_path,
        symbol=grid_system.get("symbol"),
        exchange=grid_system.get("exchange"),
        grid_type=grid_system.get("grid_type"),
        mode=account_mode_from_yaml(yaml_data),
        desired_running=bool(active_grids.get(config_path)),
        runtime=runtime,
        command=command,
        lower_price=finite_number(price_range.get("lower_price")),
        upper_price=finite_number(price_range.get("upper_price")),
        grid_interval=finite_number(grid_system.get("grid_interval")),
        order_amount=finite_number(grid_system.get("order_amount")),
        leverage=leverage,
        live_enabled=live_trading_enabled(),
    )


def _build_grid_info(config_path: str, active_grids: dict) -> GridInfo:
    """從配置路徑構建 GridInfo"""
    grid_id = GridYAMLGenerator.grid_id_from_path(config_path)
    is_active = active_grids.get(config_path, False)

    model = _grid_read_model(config_path, active_grids)
    info = GridInfo(
        grid_id=grid_id,
        config_path=config_path,
        status=model["status"],
        mode=model["mode"],
        data_status=model["data_status"],
        command_id=model["command_id"],
        command_status=model["command_status"],
    )
    info.symbol = model["symbol"]
    info.exchange = model["exchange"]
    info.grid_type = model["grid_type"]
    info.grid_interval = model["grid_interval"]
    info.order_amount = model["order_amount"]
    info.leverage = model["leverage"]
    info.lower_price = model["lower_price"]
    info.upper_price = model["upper_price"]
    # is_active is desired state only; status already comes from the read model.
    _ = is_active
    return info


# ────────────────────────────────────────────────────────
# GET /api/v1/grids — 列出所有網格
# ────────────────────────────────────────────────────────

@router.get("", response_model=GridListResponse)
async def list_grids(_token: str = Depends(verify_token)):
    """列出已註冊策略（active_grids.json），不含 archived 與歷史 YAML 掃描。"""
    active_grids = _read_active_grids()
    grids = [_build_grid_info(path, active_grids) for path in registered_config_paths(active_grids)]
    active_count = sum(1 for item in grids if item.status == "running")
    return GridListResponse(total=len(grids), active=active_count, grids=grids)


# ────────────────────────────────────────────────────────
# GET /api/v1/grids/summary — 全局 PnL 聚合
# ────────────────────────────────────────────────────────

@router.get("/summary", response_model=GridSummaryResponse)
async def get_grids_summary(_token: str = Depends(verify_token)):
    """取得所有運行中網格的全局 PnL 與統計數據聚合"""
    store = GridStateStore.instance()
    summary = store.get_summary()
    
    # 我們需要更新 grids 的 status，因為 store 只知道 grid 丟過來的資訊。
    # status 應該跟 active_grids.json 的運行狀態對齊
    active_grids = _read_active_grids()
    
    store_grids_paths = {g["config_path"] for g in summary["grids"]}
    
    updated_grids = []
    active_count = 0
    
    for g in summary["grids"]:
        config_path = g["config_path"]
        is_active = active_grids.get(config_path, False)
        g["status"] = "running" if is_active else "stopped"
        if is_active:
            active_count += 1
        updated_grids.append(g)
        
    # 補上那些在 active_grids 裡是 True，但是 state store 裡還沒有數據的網格
    for config_path, is_active in active_grids.items():
        if is_active and config_path not in store_grids_paths:
            # 讀取 YAML 獲得 symbol
            yaml_data = _read_yaml_config(config_path)
            symbol = "unknown"
            if yaml_data:
                symbol = yaml_data.get("grid_system", {}).get("symbol", "unknown")
            grid_id = GridYAMLGenerator.grid_id_from_path(config_path)
            updated_grids.append({
                "grid_id": grid_id,
                "config_path": config_path,
                "symbol": symbol,
                "realized_pnl": 0.0,
                "unrealized_pnl": 0.0,
                "total_pnl": 0.0,
                "current_price": 0.0,
                "status": "running",
                "stale": True,
            })
            active_count += 1
            
    summary["grids"] = updated_grids
    summary["active_grids"] = active_count
    summary["total_grids"] = len(updated_grids)
    
    return summary


# ────────────────────────────────────────────────────────
# GET /api/v1/grids/{grid_id}/stats — 單一網格即時統計
# ────────────────────────────────────────────────────────

@router.get("/{grid_id}/stats", response_model=GridStatsResponse)
async def get_grid_stats(grid_id: str, _token: str = Depends(verify_token)):
    """取得單一網格的即時統計（PnL、掛單、持倉）"""
    config_path = GridYAMLGenerator.path_from_grid_id(grid_id)
    project_root = _get_project_root()
    abs_path = os.path.join(project_root, config_path)

    if not os.path.exists(abs_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"網格 {grid_id} 不存在",
        )

    active_grids = _read_active_grids()
    model = _grid_read_model(config_path, active_grids)
    runtime = GridStateStore.instance().get(config_path) or {}
    pending = runtime.get("pending_orders") or {}
    filled = runtime.get("filled_orders") or {}
    profit = runtime.get("profit") or {}
    balance = runtime.get("balance") or {}
    time_info = runtime.get("time") or {}
    return GridStatsResponse(
        grid_id=grid_id,
        symbol=model["symbol"] or "unknown",
        status=model["status"],
        data_status=model["data_status"],
        stale=model["data_status"] != "live",
        current_price=model["current_price"],
        current_position=model["current_position"],
        grid_position=model.get("grid_position"),
        account_position=model.get("account_position"),
        average_cost=finite_number(runtime.get("average_cost")),
        pending_orders={
            "buy": pending.get("buy") if isinstance(pending.get("buy"), int) else None,
            "sell": pending.get("sell") if isinstance(pending.get("sell"), int) else None,
            "total": pending.get("total") if isinstance(pending.get("total"), int) else None,
        } if runtime else None,
        filled_orders={
            "buy": filled.get("buy") if isinstance(filled.get("buy"), int) else None,
            "sell": filled.get("sell") if isinstance(filled.get("sell"), int) else None,
            "cycles": filled.get("cycles") if isinstance(filled.get("cycles"), int) else None,
        } if runtime else None,
        profit={
            "grid_profit": finite_number(profit.get("grid_profit") if "grid_profit" in profit else profit.get("realized")),
            "realized": finite_number(profit.get("realized")),
            "unrealized": finite_number(profit.get("unrealized")),
            "total": finite_number(profit.get("total")),
            "fees": finite_number(profit.get("fees")),
            "net": finite_number(profit.get("net")),
            "rate": finite_number(profit.get("rate")),
            "account_unrealized": finite_number(profit.get("account_unrealized")),
        } if runtime else None,
        grid_utilization=finite_number(runtime.get("grid_utilization")),
        balance={
            "spot": finite_number(balance.get("spot")),
            "collateral": finite_number(balance.get("collateral")),
            "total": finite_number(balance.get("total")),
            "utilization": finite_number(balance.get("utilization")),
        } if runtime else None,
        time={
            "running_time": time_info.get("running_time") or "",
            "last_trade": time_info.get("last_trade"),
        } if runtime else None,
        last_update=model["last_updated_at"],
    )


# ────────────────────────────────────────────────────────
# GET /api/v1/grids/{grid_id}/history — 可繪製的本地快照歷史
# ────────────────────────────────────────────────────────

@router.get("/{grid_id}/history", response_model=GridHistoryResponse)
async def get_grid_history(
    grid_id: str,
    hours: int = Query(default=24, ge=1, le=168, description="查詢區間（小時，最長 7 天）"),
    _token: str = Depends(verify_token),
):
    config_path = GridYAMLGenerator.path_from_grid_id(grid_id)
    if not os.path.exists(os.path.join(_get_project_root(), config_path)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"網格 {grid_id} 不存在")
    import time
    history = RuntimeSnapshotRepository.instance().history(config_path, time.time() - hours * 3600)
    points = []
    for payload, recorded_at in history:
        profit = payload.get("profit") or {}
        balance = payload.get("balance") or {}
        gp = profit.get("grid_profit") if "grid_profit" in profit else profit.get("realized")
        up = profit.get("unrealized") or 0.0
        fees = profit.get("fees") or 0.0
        total_pnl = (float(gp) if gp is not None else 0.0) + float(up)
        net_pnl = profit.get("net")
        if net_pnl is None:
            net_pnl = total_pnl - float(fees)
        points.append({
            "recorded_at": recorded_at,
            "current_price": payload.get("current_price"),
            "total_pnl": total_pnl,
            "net_pnl": net_pnl,
            "realized_pnl": profit.get("realized"),
            "unrealized_pnl": profit.get("unrealized"),
            "fees": fees,
            "equity": balance.get("total"),
        })
    return {"grid_id": grid_id, "range_hours": hours, "points": points}


@router.get("/{grid_id}/orders", response_model=GridOrdersResponse)
async def get_grid_orders(grid_id: str, _token: str = Depends(verify_token)):
    config_path = GridYAMLGenerator.path_from_grid_id(grid_id)
    if not os.path.exists(os.path.join(_get_project_root(), config_path)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"網格 {grid_id} 不存在")
    runtime = GridStateStore.instance().get(config_path) or {}
    orders = runtime.get("pending_order_list")
    if not isinstance(orders, list):
        return {"grid_id": grid_id, "available": False, "orders": [], "message": "引擎尚未提供掛單明細"}
    return {"grid_id": grid_id, "available": True, "orders": orders, "message": None}


@router.get("/{grid_id}/fills", response_model=GridFillsResponse)
async def get_grid_fills(grid_id: str, _token: str = Depends(verify_token)):
    config_path = GridYAMLGenerator.path_from_grid_id(grid_id)
    if not os.path.exists(os.path.join(_get_project_root(), config_path)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"網格 {grid_id} 不存在")
    from core.infrastructure.identity import resolve_bot_id
    from datetime import datetime, timezone
    bot_id = resolve_bot_id(config_path)

    # 優先從持久化真實帳本 trade_fills 讀取（支援歷史與回填成交）
    fills_records = RuntimeSnapshotRepository.instance().get_fills(bot_id, limit=300, order_desc=True)
    if fills_records:
        fills = [
            {
                "side": f.side,
                "price": f.price,
                "amount": f.quantity,
                "fee": f.fee,
                "time": datetime.fromtimestamp(f.executed_at, tz=timezone.utc).astimezone().isoformat() if f.executed_at else None,
                "profit": f.realized_pnl,
            }
            for f in fills_records
        ]
        return {"grid_id": grid_id, "available": True, "fills": fills, "message": None}

    runtime = GridStateStore.instance().get(config_path) or {}
    fills = runtime.get("recent_fills")
    if not isinstance(fills, list):
        return {"grid_id": grid_id, "available": False, "fills": [], "message": "引擎尚未提供成交明細"}
    return {"grid_id": grid_id, "available": True, "fills": fills, "message": None}


# ────────────────────────────────────────────────────────
# GET /api/v1/grids/{grid_id} — 單一網格詳細資訊
# ────────────────────────────────────────────────────────

@router.get("/{grid_id}", response_model=GridInfo)
async def get_grid(grid_id: str, _token: str = Depends(verify_token)):
    """取得單一網格詳細資訊"""
    config_path = GridYAMLGenerator.path_from_grid_id(grid_id)
    project_root = _get_project_root()
    abs_path = os.path.join(project_root, config_path)

    if not os.path.exists(abs_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"網格 {grid_id} 不存在",
        )

    active_grids = _read_active_grids()
    return _build_grid_info(config_path, active_grids)


# ────────────────────────────────────────────────────────
# POST /api/v1/grids — 建立新網格
# ────────────────────────────────────────────────────────

@router.post("", response_model=GridCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_grid(
    request: CreateGridRequest,
    _token: str = Depends(verify_token),
):
    """
    建立新網格。

    流程：
    1. 參數驗證
    2. 安全護欄檢查（頻率限制/熔斷/上限/引擎存活/目錄可寫/最小投資）
    3. 重複交易對檢查
    4. 生成 YAML 寫入磁碟
    5. 若 auto_start=true，寫入 active_grids.json 觸發 5 秒熱插拔
    """
    project_root = _get_project_root()
    guard = SafetyGuard(project_root)
    audit_logger = AuditLogger.get_instance(project_root)
    params = request.model_dump(exclude_none=True)

    metadata = params.get("metadata", {})
    source = metadata.get("source", "hermes")
    reason = metadata.get("reason")

    # 0. 寫操作頻率限制檢查
    can_proceed, limit_msg = guard.check_rate_limit(source)
    if not can_proceed:
        audit_logger.log(
            source=source,
            action="create_grid",
            grid_id=None,
            params=params,
            result="rejected",
            reason=reason,
            error_msg=limit_msg,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=limit_msg,
        )

    # 1. 參數驗證
    errors = _generator.validate_params(params)
    if errors:
        audit_logger.log(
            source=source,
            action="create_grid",
            grid_id=None,
            params=params,
            result="rejected",
            reason=reason,
            error_msg=f"參數驗證失敗: {errors}",
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": "參數驗證失敗", "errors": errors},
        )

    # 1.5 交易對有效性預檢與自適應正規化（不論 LIVE 還是 PAPER 都執行市場存在性驗證）
    import asyncio
    from web_ui.backend.routes.market import _lookup_market_from_ccxt_sync
    market_info = await asyncio.get_running_loop().run_in_executor(
        None, _lookup_market_from_ccxt_sync, params["symbol"], params["exchange"]
    )
    if market_info is None:
        message = f"交易對 {params['symbol']} 在交易所 {params['exchange']} 中不存在或無法交易，拒絕建立！"
        audit_logger.log(source=source, action="create_grid", grid_id=None, params=params, result="rejected", reason=reason, error_msg=message)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=message)

    # 🔥 自適應正規化為交易所標準符號（例如 牛来USDT -> 牛来/USDT:USDT）
    if market_info.canonical_symbol:
        params["symbol"] = market_info.canonical_symbol

    # 1.6 LIVE server-side final gate.
    if params.get("account_mode", "paper") == "live":
        if os.getenv("GRID_LIVE_TRADING_ENABLED", "false").lower() not in {"1", "true", "yes"}:
            message = "LIVE 建立功能尚未啟用（需要 GRID_LIVE_TRADING_ENABLED=true）"
            audit_logger.log(source=source, action="create_grid", grid_id=None, params=params, result="rejected", reason=reason, error_msg=message)
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=message)
        market_errors = _validate_market_rules(params, market_info)
        params["market_validation"] = {
            "symbol": market_info.symbol, "exchange": market_info.exchange,
            "price_precision": market_info.price_precision, "quantity_precision": market_info.quantity_precision,
            "min_notional": market_info.min_notional,
        }
        if market_errors:
            audit_logger.log(source=source, action="create_grid", grid_id=None, params=params, result="rejected", reason=reason, error_msg=f"市場規則驗證失敗: {market_errors}")
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"message": "市場規則驗證失敗", "errors": market_errors})

    # 2. 安全護欄。僅寫入 YAML 時不要求引擎存活；auto_start 才走完整啟動檢查。
    wants_start = bool(params.get("auto_start", False))
    if wants_start:
        can_create, err_msg = guard.check_can_create_grid()
    else:
        can_create, err_msg = guard.check_config_dir_writable()
    if not can_create:
        audit_logger.log(
            source=source,
            action="create_grid",
            grid_id=None,
            params=params,
            result="rejected",
            reason=reason,
            error_msg=err_msg,
        )
        if "熔斷" in (err_msg or ""):
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=err_msg)
        elif "最大" in (err_msg or ""):
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=err_msg)
        else:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err_msg)

    # 2.5 最小投資額驗證
    can_invest, invest_msg = guard.check_min_investment(params)
    if not can_invest:
        audit_logger.log(
            source=source,
            action="create_grid",
            grid_id=None,
            params=params,
            result="rejected",
            reason=reason,
            error_msg=invest_msg,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=invest_msg,
        )

    # 3. 重複交易對檢查
    can_proceed, dup_msg = guard.check_duplicate_symbol(params["symbol"])
    if not can_proceed:
        audit_logger.log(
            source=source,
            action="create_grid",
            grid_id=None,
            params=params,
            result="rejected",
            reason=reason,
            error_msg=dup_msg,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=dup_msg,
        )

    # 4. 生成 YAML（P1-1：編輯/重建時沿用既有 bot_id 保留交易紀錄）
    yaml_content, config_path = _generator.generate(params, existing_bot_id=params.get("bot_id"))
    grid_id = GridYAMLGenerator.grid_id_from_path(config_path)

    # 檢查配置檔案是否已存在
    can_write, exist_msg = guard.check_config_not_exists(config_path)
    if not can_write:
        audit_logger.log(
            source=source,
            action="create_grid",
            grid_id=grid_id,
            params=params,
            result="rejected",
            reason=reason,
            error_msg=exist_msg,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=exist_msg,
        )

    # 寫入 YAML 檔案
    abs_yaml_path = os.path.join(project_root, config_path)
    try:
        with open(abs_yaml_path, "w", encoding="utf-8") as f:
            f.write(yaml_content)
        logger.info(f"✅ 已建立網格配置: {config_path}")
    except Exception as e:
        audit_logger.log(
            source=source,
            action="create_grid",
            grid_id=grid_id,
            params=params,
            result="error",
            reason=reason,
            error_msg=f"寫入 YAML 配置檔失敗: {e}",
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"寫入 YAML 配置檔失敗: {e}",
        )

    grid_status = "created"
    command_id = None
    start_block = start_rejection(params.get("account_mode", "paper"), live_trading_enabled())
    if wants_start and start_block:
        logger.info("建立後未啟動 %s：%s", grid_id, start_block)
    elif wants_start:
        command = GridCommandRepository.instance().create(
            grid_id=grid_id,
            config_path=config_path,
            action="start",
            source=source,
            reason=reason or "auto_start after create",
        )
        command_id = command["command_id"]
        try:
            active_grids = _read_active_grids()
            active_grids[config_path] = True
            _write_active_grids(active_grids)
            RedisCommandQueue().publish("START", "grid", config_path, command_id=command_id)
            GridCommandRepository.instance().update_status(command_id, "pending_engine")
            grid_status = "pending_engine"
            logger.info("已接受建立後啟動命令: %s command=%s", grid_id, command_id)
        except Exception as exc:
            GridCommandRepository.instance().update_status(command_id, "failed", str(exc))
            logger.error("更新 active_grids.json 失敗: %s", exc)
            grid_status = "created"
            command_id = command_id

    guard.record_action(source)
    audit_logger.log(
        source=source,
        action="create_grid",
        grid_id=grid_id,
        params=params,
        result="success",
        reason=reason,
    )

    if metadata:
        logger.info(
            "📝 [Hermes] 建立網格 %s | source=%s | reason=%s | confidence=%s",
            grid_id,
            source,
            reason or "N/A",
            metadata.get("confidence", "N/A"),
        )

    if grid_status != "pending_engine":
        active_grids = _read_active_grids()
        if config_path not in active_grids:
            active_grids[config_path] = False
            _write_active_grids(active_grids)

    if grid_status == "pending_engine":
        message = f"網格 {grid_id} 已建立，啟動命令已接受，等待引擎 heartbeat 確認"
    elif wants_start and start_block:
        message = f"網格 {grid_id} 已建立為設定檔。{start_block}"
    else:
        message = f"網格 {grid_id} 已成功建立"

    return GridCreateResponse(
        grid_id=grid_id,
        config_path=config_path,
        status=grid_status,
        yaml_content=yaml_content,
        message=message,
        command_id=command_id,
    )


# ────────────────────────────────────────────────────────
# POST /api/v1/grids/{grid_id}/start — 啟動網格
# ────────────────────────────────────────────────────────

@router.post("/{grid_id}/start", response_model=GridActionResponse)
async def start_grid(
    grid_id: str,
    source: str = Query(default="hermes", description="來源"),
    reason: Optional[str] = Query(default=None, description="原因"),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    _token: str = Depends(verify_token),
):
    """
    啟動指定網格。

    更新 active_grids.json 中該配置為 true，
    run_multiple_grids.py 的 active_grids_monitor() 會在 5 秒內偵測到並啟動。
    """
    project_root = _get_project_root()
    guard = SafetyGuard(project_root)
    audit_logger = AuditLogger.get_instance(project_root)

    # 0. 寫操作頻率限制檢查
    can_proceed, limit_msg = guard.check_rate_limit(source)
    if not can_proceed:
        audit_logger.log(
            source=source,
            action="start",
            grid_id=grid_id,
            params={"grid_id": grid_id},
            result="rejected",
            reason=reason,
            error_msg=limit_msg,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=limit_msg,
        )

    config_path = GridYAMLGenerator.path_from_grid_id(grid_id)
    abs_path = os.path.join(project_root, config_path)

    if not os.path.exists(abs_path):
        audit_logger.log(
            source=source,
            action="start",
            grid_id=grid_id,
            params={"grid_id": grid_id},
            result="rejected",
            reason=reason,
            error_msg=f"網格配置 {grid_id} 不存在",
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"網格配置 {grid_id} 不存在",
        )

    yaml_data = _read_yaml_config(config_path) or {}
    blocked = start_rejection(account_mode_from_yaml(yaml_data), live_trading_enabled())
    if blocked:
        audit_logger.log(
            source=source,
            action="start",
            grid_id=grid_id,
            params={"grid_id": grid_id},
            result="rejected",
            reason=reason,
            error_msg=blocked,
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=blocked)

    # 啟動前交易對市場檢驗門禁
    cfg_symbol = yaml_data.get("grid_system", {}).get("symbol")
    cfg_exchange = yaml_data.get("grid_system", {}).get("exchange", "binance")
    if cfg_symbol:
        import asyncio
        from web_ui.backend.routes.market import _lookup_market_from_ccxt_sync
        m_info = await asyncio.get_running_loop().run_in_executor(
            None, _lookup_market_from_ccxt_sync, cfg_symbol, cfg_exchange
        )
        if m_info is None:
            err_msg = f"配置中交易對 {cfg_symbol} 在交易所 {cfg_exchange} 中無效或不存在，禁止啟動！"
            audit_logger.log(
                source=source, action="start", grid_id=grid_id,
                params={"grid_id": grid_id}, result="rejected", reason=reason,
                error_msg=err_msg
            )
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=err_msg)

    command_repo = GridCommandRepository.instance()
    if idempotency_key:
        existing = command_repo.get_by_idempotency_key("start", idempotency_key, grid_id=grid_id)
        if existing:
            return GridActionResponse(
                grid_id=existing["grid_id"], action="start", status=existing["status"],
                command_id=existing["command_id"], message="重複請求已回傳原始命令",
            )

    # 安全護欄
    can_create, err_msg = guard.check_can_create_grid()
    if not can_create:
        audit_logger.log(
            source=source,
            action="start",
            grid_id=grid_id,
            params={"grid_id": grid_id},
            result="rejected",
            reason=reason,
            error_msg=err_msg,
        )
        if "熔斷" in (err_msg or ""):
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=err_msg)
        else:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err_msg)

    active_grids = _read_active_grids()

    # 已經在運行中
    if active_grids.get(config_path, False):
        return GridActionResponse(
            grid_id=grid_id,
            action="start",
            status="already_running",
            message=f"網格 {grid_id} 已在運行中",
        )

    try:
        command = GridCommandRepository.instance().create(
            grid_id=grid_id, config_path=config_path, action="start", source=source, reason=reason,
            idempotency_key=idempotency_key,
        )
    except sqlite3.IntegrityError:
        existing = command_repo.get_by_idempotency_key("start", idempotency_key or "", grid_id=grid_id)
        if existing:
            return GridActionResponse(
                grid_id=existing["grid_id"], action="start", status=existing["status"],
                command_id=existing["command_id"], message="重複請求已回傳原始命令",
            )
        raise
    try:
        active_grids[config_path] = True
        _write_active_grids(active_grids)
        RedisCommandQueue().publish("START", "grid", config_path, command_id=command["command_id"])
        GridCommandRepository.instance().update_status(command["command_id"], "pending_engine")
        logger.info(f"▶️ 已接受啟動網格命令: {grid_id}, command={command['command_id']}")

        # 記錄寫操作時間戳
        guard.record_action(source)

        # 寫入審計日誌
        audit_logger.log(
            source=source,
            action="start",
            grid_id=grid_id,
            params={"grid_id": grid_id},
            result="success",
            reason=reason,
        )

        return GridActionResponse(
            grid_id=grid_id,
            action="start",
            status="pending_engine",
            command_id=command["command_id"],
            message=f"網格 {grid_id} 的啟動命令已接受，等待引擎 heartbeat 確認",
        )
    except Exception as e:
        GridCommandRepository.instance().update_status(command["command_id"], "failed", str(e))
        audit_logger.log(
            source=source,
            action="start",
            grid_id=grid_id,
            params={"grid_id": grid_id},
            result="error",
            reason=reason,
            error_msg=f"啟動網格異常: {e}",
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"啟動網格異常: {e}",
        )


# ────────────────────────────────────────────────────────
# POST /api/v1/grids/{grid_id}/stop — 停止網格
# ────────────────────────────────────────────────────────

@router.post("/{grid_id}/stop", response_model=GridActionResponse)
async def stop_grid(
    grid_id: str,
    source: str = Query(default="hermes", description="來源"),
    reason: Optional[str] = Query(default=None, description="原因"),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    _token: str = Depends(verify_token),
):
    """
    停止指定網格。

    更新 active_grids.json 中該配置為 false，
    run_multiple_grids.py 的 active_grids_monitor() 會在 5 秒內偵測到並優雅停止。
    """
    project_root = _get_project_root()
    guard = SafetyGuard(project_root)
    audit_logger = AuditLogger.get_instance(project_root)

    # 0. 寫操作頻率限制檢查
    can_proceed, limit_msg = guard.check_rate_limit(source)
    if not can_proceed:
        audit_logger.log(
            source=source,
            action="stop",
            grid_id=grid_id,
            params={"grid_id": grid_id},
            result="rejected",
            reason=reason,
            error_msg=limit_msg,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=limit_msg,
        )

    config_path = GridYAMLGenerator.path_from_grid_id(grid_id)
    active_grids = _read_active_grids()

    if config_path not in active_grids:
        audit_logger.log(
            source=source,
            action="stop",
            grid_id=grid_id,
            params={"grid_id": grid_id},
            result="rejected",
            reason=reason,
            error_msg=f"網格 {grid_id} 不在 active_grids 控制中",
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"網格 {grid_id} 不在 active_grids 控制中",
        )

    command_repo = GridCommandRepository.instance()
    if idempotency_key:
        existing = command_repo.get_by_idempotency_key("stop", idempotency_key, grid_id=grid_id)
        if existing:
            return GridActionResponse(
                grid_id=existing["grid_id"], action="stop", status=existing["status"],
                command_id=existing["command_id"], message="重複請求已回傳原始命令",
            )

    if not active_grids.get(config_path, False):
        return GridActionResponse(
            grid_id=grid_id,
            action="stop",
            status="already_stopped",
            message=f"網格 {grid_id} 已處於停止狀態",
        )

    command = GridCommandRepository.instance().create(
        grid_id=grid_id, config_path=config_path, action="stop", source=source, reason=reason,
        idempotency_key=idempotency_key,
    )
    try:
        active_grids[config_path] = False
        _write_active_grids(active_grids)
        RedisCommandQueue().publish("STOP", "grid", config_path, command_id=command["command_id"])
        GridCommandRepository.instance().update_status(command["command_id"], "pending_engine")
        logger.info(f"🛑 已接受停止網格命令: {grid_id}, command={command['command_id']}")

        # 記錄寫操作時間戳
        guard.record_action(source)

        # 寫入審計日誌
        audit_logger.log(
            source=source,
            action="stop",
            grid_id=grid_id,
            params={"grid_id": grid_id},
            result="success",
            reason=reason,
        )

        return GridActionResponse(
            grid_id=grid_id,
            action="stop",
            status="pending_engine",
            command_id=command["command_id"],
            message=f"網格 {grid_id} 的停止命令已接受，等待引擎取消掛單並確認",
        )
    except Exception as e:
        GridCommandRepository.instance().update_status(command["command_id"], "failed", str(e))
        audit_logger.log(
            source=source,
            action="stop",
            grid_id=grid_id,
            params={"grid_id": grid_id},
            result="error",
            reason=reason,
            error_msg=f"停止網格異常: {e}",
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"停止網格異常: {e}",
        )


# ────────────────────────────────────────────────────────
# DELETE /api/v1/grids/{grid_id} — 停止+刪除
# ────────────────────────────────────────────────────────

@router.delete("/{grid_id}", response_model=GridActionResponse)
async def delete_grid(
    grid_id: str,
    source: str = Query(default="hermes", description="來源"),
    reason: Optional[str] = Query(default=None, description="原因"),
    force: bool = Query(default=False, description="是否強制停止並刪除運行中的策略"),
    _token: str = Depends(verify_token),
):
    """
    停止並刪除指定網格。

    1. 先將 active_grids.json 中設為 false（觸發引擎停止）
    2. 從 active_grids.json 中移除該條目
    3. 將 YAML 檔案移至 config/grid/archived/ 歸檔（而非直接刪除）
    """
    project_root = _get_project_root()
    guard = SafetyGuard(project_root)
    audit_logger = AuditLogger.get_instance(project_root)

    # 0. 寫操作頻率限制檢查
    can_proceed, limit_msg = guard.check_rate_limit(source)
    if not can_proceed:
        audit_logger.log(
            source=source,
            action="delete",
            grid_id=grid_id,
            params={"grid_id": grid_id},
            result="rejected",
            reason=reason,
            error_msg=limit_msg,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=limit_msg,
        )

    config_path = GridYAMLGenerator.path_from_grid_id(grid_id)
    abs_yaml = os.path.join(project_root, config_path)
    active_grids = _read_active_grids()
    in_active = config_path in active_grids
    has_state = GridStateStore.instance().get(config_path) is not None

    if not os.path.exists(abs_yaml):
        if in_active or has_state:
            # 幽靈/殘留清理只移除「無即時心跳」的登記殘留。
            # 不建立 STOP、不取消掛單、不動用任何交易所資源，因此不會改變持倉。
            runtime = GridStateStore.instance().get(config_path)
            data_status = data_status_from_runtime(runtime)
            if data_status == "live":
                audit_logger.log(
                    source=source,
                    action="delete",
                    grid_id=grid_id,
                    params={"grid_id": grid_id, "data_status": data_status},
                    result="rejected",
                    reason=reason,
                    error_msg="殘留策略仍有即時心跳",
                )
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"網格 {grid_id} 的 YAML 已不存在，但仍有即時心跳。"
                        "為避免誤停執行中的策略或動到持倉，請先停止該策略，"
                        "待心跳過期（停止後約 3 分鐘）再刪除"
                    ),
                )
            previous_desired = active_grids.get(config_path, False) if in_active else False
            if in_active:
                del active_grids[config_path]
                _write_active_grids(active_grids)
            if has_state:
                GridStateStore.instance().remove(config_path)
            guard.record_action(source)
            audit_logger.log(
                source=source,
                action="delete",
                grid_id=grid_id,
                params={
                    "grid_id": grid_id,
                    "data_status": data_status,
                    "had_state": has_state,
                    "previous_desired_running": previous_desired,
                    "ghost_cleanup": True,
                },
                result="success",
                reason=reason or "ghost_cleanup",
            )
            return GridActionResponse(
                grid_id=grid_id,
                action="delete",
                status="deleted",
                message=(
                    f"網格 {grid_id} 配置文件已不存在，已清理殘留登記（心跳狀態: {data_status}）；"
                    "未變更任何持倉或掛單"
                ),
            )
        audit_logger.log(
            source=source,
            action="delete",
            grid_id=grid_id,
            params={"grid_id": grid_id},
            result="rejected",
            reason=reason,
            error_msg=f"網格配置 {grid_id} 不存在",
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"網格配置 {grid_id} 不存在",
        )

    try:
        active_grids = _read_active_grids()
        model = _grid_read_model(config_path, active_grids)
        if not force:
            blocked = delete_rejection(model["status"], model.get("command_status"))
            if blocked:
                audit_logger.log(
                    source=source,
                    action="delete",
                    grid_id=grid_id,
                    params={"grid_id": grid_id, "status": model["status"]},
                    result="rejected",
                    reason=reason,
                    error_msg=blocked,
                )
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=blocked)

        # 1. 停止（若仍標記為 desired running）
        was_running = active_grids.get(config_path, False)
        if was_running:
            try:
                GridCommandRepository.instance().create(
                    grid_id=grid_id,
                    config_path=config_path,
                    action="stop",
                    source=source,
                    reason=reason or "force_delete",
                )
            except Exception:
                pass
            active_grids[config_path] = False
            _write_active_grids(active_grids)
            RedisCommandQueue().publish("STOP", "grid", config_path)
            logger.info(f"🛑 刪除前已停止網格: {grid_id}")

        # 2. 從 active_grids 移除
        if config_path in active_grids:
            del active_grids[config_path]
            _write_active_grids(active_grids)

        # 清理 StateStore 中的快取
        GridStateStore.instance().remove(config_path)

        # 3. 歸檔 YAML（移動到 archived/ 目錄）
        archive_dir = os.path.join(project_root, "config", "grid", "archived")
        os.makedirs(archive_dir, exist_ok=True)
        archived_path = os.path.join(archive_dir, Path(config_path).name)

        archive_success = False
        try:
            os.rename(abs_yaml, archived_path)
            logger.info(f"📦 已歸檔: {config_path} → config/grid/archived/")
            archive_success = True
        except Exception as e:
            logger.error(f"⚠️ 歸檔失敗: {e}，嘗試直接刪除")
            try:
                os.remove(abs_yaml)
                logger.info(f"🗑️ 已刪除: {config_path}")
                archive_success = True
            except Exception as e2:
                logger.error(f"❌ 刪除也失敗: {e2}")

        if not archive_success:
            raise Exception("無法移動或刪除網格配置文件")

        # 記錄寫操作時間戳
        guard.record_action(source)

        # 寫入審計日誌
        audit_logger.log(
            source=source,
            action="delete",
            grid_id=grid_id,
            params={"grid_id": grid_id},
            result="success",
            reason=reason,
        )

        return GridActionResponse(
            grid_id=grid_id,
            action="delete",
            status="deleted",
            message=f"網格 {grid_id} 已{'停止並' if was_running else ''}刪除（YAML 已歸檔）",
        )
    except HTTPException:
        raise
    except Exception as e:
        audit_logger.log(
            source=source,
            action="delete",
            grid_id=grid_id,
            params={"grid_id": grid_id},
            result="error",
            reason=reason,
            error_msg=f"刪除網格異常: {e}",
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"刪除網格異常: {e}",
        )

