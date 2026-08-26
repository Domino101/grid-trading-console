"""DCA Lifecycle CRUD + Preview + Snapshot 端點（Phase 4）

路由清單（§4.7）：
    GET    /api/v1/dca                     — 列出所有 DCA
    POST   /api/v1/dca                     — 建立（寫 YAML；auto_start 觸發熱插拔）
    GET    /api/v1/dca/snapshot            — 批次快照（單次讀取，禁止 N+1）
    POST   /api/v1/dca/preview             — 階梯試算（pure；直接呼叫 build_ladder）
    GET    /api/v1/dca/{bot_id}            — 單台設定
    GET    /api/v1/dca/{bot_id}/stats      — 執行期統計
    GET    /api/v1/dca/{bot_id}/ladder     — 當前實際階梯
    GET    /api/v1/dca/{bot_id}/orders     — 掛單（引擎尚未提供時 available=false）
    GET    /api/v1/dca/{bot_id}/fills      — 成交（引擎尚未提供時 available=false）
    GET    /api/v1/dca/{bot_id}/events     — 生命週期事件
    POST   /api/v1/dca/{bot_id}/start      — 啟動
    POST   /api/v1/dca/{bot_id}/stop       — 停止（預設不平倉）
    DELETE /api/v1/dca/{bot_id}            — 刪除（僅限已停止且無未完成命令）

未知數值一律 null（稽核 4.6），不允許以 0 代替（G-5）。
preview 必須直接呼叫 core.services.dca.ladder.build_ladder()（稽核 4.2）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import sqlite3
import sys
import time
from decimal import Decimal
from pathlib import Path
from typing import Any, List, Mapping, Optional

import yaml
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

# 加入專案根目錄到 sys.path 以便匯入 core 模組
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../"))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from audit import AuditLogger
from auth import verify_token
from dca_yaml_generator import DcaYAMLGenerator
from safety_guard import SafetyGuard
from grid_state_store import GridStateStore
from command_repository import GridCommandRepository
from runtime_repository import RuntimeSnapshotRepository
from lifecycle import (
    data_status_from_runtime,
    delete_rejection,
    live_trading_enabled,
    start_rejection,
    write_json_atomic,
)
from schemas import (
    CreateDcaRequest,
    DcaActionResponse,
    DcaCreateResponse,
    DcaEvent,
    DcaEventListResponse,
    DcaFillsResponse,
    DcaInfo,
    DcaLadderResponse,
    DcaLevelView,
    DcaListResponse,
    DcaOrdersResponse,
    DcaPreviewLadder,
    DcaPreviewLevel,
    DcaPreviewRequest,
    DcaPreviewResponse,
    DcaProfit,
    DcaPosition,
    DcaLadderState,
    DcaSnapshotAggregate,
    DcaSnapshotItem,
    DcaSnapshotResponse,
    DcaStatsResponse,
)
from core.services.dca.config_loader import dca_account_mode_from_yaml
from core.services.dca.ladder import build_ladder, stop_loss_price, take_profit_price
from core.services.dca.models.dca_config import DcaDirection

logger = logging.getLogger("GridAPI.Dca")

router = APIRouter(prefix="/api/v1/dca", tags=["dca"])

_generator = DcaYAMLGenerator()

# 引擎 heartbeat 是唯一讓 DCA 變成 running 的來源；armed 等狀態都算「正在運行」。
_RUNNING_STATUSES = frozenset({
    "running", "armed", "triggering", "averaging",
    "completed", "cooling", "degraded",
})


# ── 共用工具 ────────────────────────────────────────────────
def _get_project_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../"))


def _get_active_dca_path() -> str:
    return os.path.join(_get_project_root(), "config", "active_dca.json")


def _read_active_dca() -> dict:
    path = _get_active_dca_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("讀取 active_dca.json 失敗: %s", e)
        return {}


def _write_active_dca(data: dict) -> None:
    write_json_atomic(_get_active_dca_path(), data)


def _read_dca_yaml(config_path: str) -> Optional[dict]:
    """安全地讀取 DCA YAML 配置檔（限定於 config/dca/）。"""
    project_root = _get_project_root()
    abs_path = os.path.join(project_root, config_path)
    config_dir = os.path.join(project_root, "config", "dca")
    resolved = os.path.realpath(abs_path)
    if not resolved.startswith(os.path.realpath(config_dir) + os.sep):
        return None
    if not os.path.exists(abs_path):
        return None
    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except Exception:
        return None


def _dca_section(data: Any) -> Mapping[str, Any]:
    if not isinstance(data, Mapping):
        return {}
    section = data.get("dca")
    if isinstance(section, Mapping):
        return section
    return data


def _number(value: Any) -> Optional[float]:
    """把 YAML 中的 str/int/float/Decimal 轉成 finite float；未知回傳 None。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _bot_status(
    desired_running: bool,
    runtime: Optional[dict],
    command: Optional[dict],
) -> str:
    """DCA 生命週期狀態；runtime.status 是引擎唯一權威來源。"""
    runtime_status = (runtime or {}).get("status")
    if isinstance(runtime_status, str) and runtime_status:
        return runtime_status
    if command and command.get("status") == "pending_engine":
        return "starting" if command.get("action") == "start" else "stopping"
    if desired_running:
        return "starting"
    return "stopped"


def _build_dca_read_model(
    config_path: str,
    active_dca: dict,
    runtime_map: Optional[dict] = None,
) -> dict:
    """單台 DCA 讀模型；runtime_map 為一次性讀取的批次快照（禁止 N+1）。"""
    bot_id = Path(config_path).stem
    yaml_data = _read_dca_yaml(config_path) or {}
    section = _dca_section(yaml_data)
    runtime = (runtime_map or {}).get(config_path)
    command = GridCommandRepository.instance().latest_for_grid(bot_id)
    mode = dca_account_mode_from_yaml(yaml_data)
    desired_running = bool(active_dca.get(config_path))
    data_status = data_status_from_runtime(runtime)
    status = _bot_status(desired_running, runtime, command)

    position = (runtime or {}).get("position") or {}
    ladder = (runtime or {}).get("ladder") or {}
    profit = (runtime or {}).get("profit") or {}

    # ── Phase 5 卡片欄位：入場設定（YAML）＋ 執行期導出值（純函式，前端禁止另算）──
    entry_section = section.get("entry") if isinstance(section.get("entry"), Mapping) else {}
    raw_indicators = entry_section.get("indicators") if isinstance(entry_section, Mapping) else None
    entry_indicators: List[dict] = []
    if isinstance(raw_indicators, list):
        allowed = {
            "type", "period", "oversold", "overbought", "fast", "slow", "signal",
            "std_dev", "lookback_bars", "long_threshold_percent",
            "short_threshold_percent", "condition",
        }
        for item in raw_indicators:
            if not isinstance(item, Mapping) or item.get("type") is None:
                continue
            entry_indicators.append({k: item[k] for k in allowed if k in item})

    # 方向解析：執行期鎖定方向優先；設定為單向時以設定值補位；both 且未鎖定 → None（G-5）
    yaml_direction = section.get("direction")
    effective_direction = (runtime or {}).get("active_direction")
    if effective_direction not in ("long", "short"):
        effective_direction = yaml_direction if yaml_direction in ("long", "short") else None

    average_cost = _number(position.get("average_cost"))
    take_profit_pct = _number(section.get("take_profit_percent"))
    stop_loss_pct = _number(section.get("stop_loss_percent"))
    derived_take_profit_price: Optional[float] = None
    derived_stop_loss_price: Optional[float] = None
    if effective_direction and average_cost is not None:
        try:
            if take_profit_pct is not None:
                derived_take_profit_price = float(
                    take_profit_price(average_cost, take_profit_pct, effective_direction)
                )
            if stop_loss_pct is not None:
                derived_stop_loss_price = float(
                    stop_loss_price(average_cost, stop_loss_pct, effective_direction)
                )
        except Exception as exc:  # 導出失敗絕不讓 snapshot 500；未知 → null
            logger.warning("DCA 導出止損/止盈價失敗 [%s]: %s", bot_id, exc)

    # armed 起算時間：最近一筆 start 命令的 created_at（無 → None）
    armed_since = (
        _number(command.get("created_at"))
        if command and command.get("action") == "start" else None
    )

    return {
        "bot_id": bot_id,
        "config_path": config_path,
        "symbol": str(section.get("symbol")) if section.get("symbol") else None,
        "exchange": str(section.get("exchange")) if section.get("exchange") else None,
        "direction": str(section.get("direction")) if section.get("direction") else None,
        "mode": mode,
        "status": status,
        "desired_running": desired_running,
        "command_id": command.get("command_id") if command else None,
        "command_status": command.get("status") if command else None,
        "data_status": data_status,
        "source": "engine_runtime" if runtime else None,
        "last_updated_at": (runtime or {}).get("last_update"),
        "current_price": _number((runtime or {}).get("current_price")),
        "active_direction": (runtime or {}).get("active_direction"),
        "position": {
            "quantity": _number(position.get("quantity")),
            "average_cost": _number(position.get("average_cost")),
            "total_cost": _number(position.get("total_cost")),
        },
        "ladder": {
            "max_safety_orders": _int(ladder.get("max_safety_orders")),
            "filled_safety_orders": _int(ladder.get("filled_safety_orders")),
            "active_safety_orders": _int(ladder.get("active_safety_orders")),
            "take_profit_orders": _int(ladder.get("take_profit_orders")),
        },
        "cycles": _int((runtime or {}).get("cycles")),
        "profit": {
            "realized": _number(profit.get("realized")),
            "unrealized": _number(profit.get("unrealized")),
            "fees": _number(profit.get("fees")),
        },
        "base_order_amount": _number(section.get("base_order_amount")),
        "max_safety_orders": _int(section.get("max_safety_orders")),
        "take_profit_percent": _number(section.get("take_profit_percent")),
        "stop_loss_percent": _number(section.get("stop_loss_percent")),
        "entry_mode": str(entry_section.get("mode")) if isinstance(entry_section, Mapping) and entry_section.get("mode") else "immediate",
        "entry_logic": str(entry_section.get("logic")) if isinstance(entry_section, Mapping) and entry_section.get("logic") else None,
        "entry_timeframe": str(entry_section.get("timeframe")) if isinstance(entry_section, Mapping) and entry_section.get("timeframe") else None,
        "entry_indicators": entry_indicators,
        "entry_price": _number((runtime or {}).get("entry_price")),
        "next_safety_order_price": _number((runtime or {}).get("next_safety_order_price")),
        "take_profit_price": derived_take_profit_price,
        "stop_loss_price": derived_stop_loss_price,
        "armed_since": armed_since,
        "start_blocked_reason": start_rejection(mode, live_trading_enabled()),
    }


def _roll_dca_aggregate(items: List[dict]) -> dict:
    realized = unrealized = fees = 0.0
    filled = cycles = available = stale = running = 0
    for item in items:
        data_status = item.get("data_status")
        if data_status == "live":
            available += 1
        elif data_status == "stale":
            stale += 1
        if item.get("status") in _RUNNING_STATUSES:
            running += 1
        if item.get("source") != "engine_runtime":
            continue
        realized += item.get("profit", {}).get("realized") or 0.0
        unrealized += item.get("profit", {}).get("unrealized") or 0.0
        fees += item.get("profit", {}).get("fees") or 0.0
        filled += item.get("ladder", {}).get("filled_safety_orders") or 0
        cycles += item.get("cycles") or 0
    return {
        "total_bots": len(items),
        "running_bots": running,
        "available_bots": available,
        "stale_bots": stale,
        "realized_pnl": realized,
        "unrealized_pnl": unrealized,
        "fees": fees,
        "net_pnl": realized + unrealized - fees,
        "filled_safety_orders": filled,
        "completed_cycles": cycles,
    }


def _aggregate_dca_models(items: List[dict]) -> dict:
    live_items = [item for item in items if item.get("mode") == "live"]
    paper_items = [item for item in items if item.get("mode") == "paper"]
    # Headline KPI 以 LIVE 優先，避免 PAPER 被誤認為真實帳戶損益（比照 grids）。
    headline_items = live_items if live_items else [item for item in items if item.get("mode") != "paper"]
    if not headline_items and paper_items:
        headline_items = paper_items
    headline = _roll_dca_aggregate(headline_items)
    overall = "unavailable"
    if items and headline["stale_bots"] == 0 and headline["available_bots"] == headline["total_bots"] and headline["total_bots"]:
        overall = "live"
    elif items and (headline["stale_bots"] or headline["available_bots"]):
        overall = "degraded"
    return {
        "data_status": overall,
        "aggregate": headline,
        "aggregate_live": _roll_dca_aggregate(live_items),
        "aggregate_paper": _roll_dca_aggregate(paper_items),
        "account_mode": "mixed" if live_items and paper_items else ("live" if live_items else ("paper" if paper_items else "unknown")),
    }


def _build_dca_info(config_path: str, active_dca: dict) -> DcaInfo:
    model = _build_dca_read_model(config_path, active_dca)
    return DcaInfo(
        bot_id=model["bot_id"],
        config_path=model["config_path"],
        symbol=model["symbol"],
        exchange=model["exchange"],
        direction=model["direction"],
        status=model["status"],
        mode=model["mode"],
        data_status=model["data_status"],
        command_id=model["command_id"],
        command_status=model["command_status"],
        current_price=model["current_price"],
        avg_price=model.get("position", {}).get("average_cost"),
        position_quantity=model.get("position", {}).get("quantity"),
        filled_safety_orders=model.get("ladder", {}).get("filled_safety_orders"),
        active_safety_orders=model.get("ladder", {}).get("active_safety_orders"),
        cycle_count=model["cycles"],
        unrealized_pnl=model.get("profit", {}).get("unrealized"),
        base_order_amount=model["base_order_amount"],
        max_safety_orders=model["max_safety_orders"],
        take_profit_percent=model["take_profit_percent"],
    )


def _validate_bot_id(bot_id: str) -> None:
    if not DcaYAMLGenerator.is_valid_bot_id(bot_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"無效的 bot_id: {bot_id!r}",
        )


def _validate_dca_market_rules(config, market_info, entry_price) -> List[str]:
    """LIVE 建立：min_notional 對基礎單與每一張安全單都檢查（稽核 4.13）。"""
    if market_info is None or getattr(market_info, "min_notional", None) is None:
        return []
    min_notional = float(market_info.min_notional)
    if config.direction is DcaDirection.BOTH:
        directions = ["long", "short"]
    else:
        directions = [config.direction.value]
    errors: List[str] = []
    for direction in directions:
        plan = build_ladder(config, entry_price, direction)
        for level in plan.levels:
            notional = float(level.notional)
            if notional < min_notional:
                slot = "base" if level.index == 0 else f"safety_{level.index}"
                errors.append(
                    f"{direction} slot {slot} 名目額 {notional:.8g} "
                    f"低於交易所最低要求 {min_notional}"
                )
    return errors


def _fetch_ticker_price_sync(symbol: str, exchange: str) -> Optional[float]:
    """LIVE 建立時取得目前價（僅在未提供 entry_price 時使用）。"""
    try:
        import ccxt
        exchange_class = getattr(ccxt, exchange.lower(), None)
        if exchange_class is None:
            return None
        ex = exchange_class({"enableRateLimit": True, "options": {"defaultType": "swap"}})
        ticker = ex.fetch_ticker(symbol)
        price = ticker.get("last")
        return float(price) if price else None
    except Exception as exc:
        logger.warning("LIVE 取得 {exchange}:{symbol} 即時價失敗: %s", exc)
        return None


def _ladder_to_preview(plan, take_profit_percent: Decimal) -> DcaPreviewLadder:
    levels: List[DcaPreviewLevel] = []
    for level in plan.levels:
        # 每個 level 的 take_profit_price 以該層均價為基準（與引擎一致）
        tp_price = level.take_profit_price(take_profit_percent, plan.direction)
        levels.append(
            DcaPreviewLevel(
                index=level.index,
                deviation_percent=float(level.deviation_percent),
                price=float(level.price),
                notional=float(level.notional),
                quantity=float(level.quantity),
                avg_price=float(level.avg_price),
                take_profit_price=float(tp_price),
            )
        )
    summary = plan.summarize(take_profit_percent)
    return DcaPreviewLadder(
        direction=plan.direction,
        entry_price=float(plan.entry_price),
        required_capital=float(plan.required_capital),
        max_drawdown_percent=float(plan.max_drawdown_percent),
        final_average_price=float(plan.final_average_price),
        final_take_profit_price=float(summary["final_take_profit_price"]),
        levels=levels,
    )


# ────────────────────────────────────────────────────────
# GET /api/v1/dca — 列出所有 DCA
# ────────────────────────────────────────────────────────
@router.get("", response_model=DcaListResponse)
async def list_dca(_token: str = Depends(verify_token)):
    active_dca = _read_active_dca()
    bots = [_build_dca_info(path, active_dca) for path in sorted(active_dca)]
    active = sum(1 for item in bots if item.status in _RUNNING_STATUSES)
    return DcaListResponse(total=len(bots), active=active, bots=bots)


# ────────────────────────────────────────────────────────
# GET /api/v1/dca/snapshot — 批次快照（禁止 N+1）
# ────────────────────────────────────────────────────────
@router.get("/snapshot", response_model=DcaSnapshotResponse)
async def get_dca_snapshot(_token: str = Depends(verify_token)):
    active_dca = _read_active_dca()
    # 一次讀取 active_dca → 逐檔 YAML → 單次 RuntimeSnapshotRepository.load_all()
    runtime_map = GridStateStore.instance().get_all()
    items = [
        _build_dca_read_model(path, active_dca, runtime_map)
        for path in sorted(active_dca)
    ]
    rolled = _aggregate_dca_models(items)
    return DcaSnapshotResponse(
        generated_at=time.time(),
        data_status=rolled["data_status"],
        account_mode=rolled["account_mode"],
        aggregate=DcaSnapshotAggregate(**rolled["aggregate"]),
        aggregate_live=DcaSnapshotAggregate(**rolled["aggregate_live"]),
        aggregate_paper=DcaSnapshotAggregate(**rolled["aggregate_paper"]),
        bots=[DcaSnapshotItem(**item) for item in items],
    )


# ────────────────────────────────────────────────────────
# POST /api/v1/dca/preview — 階梯試算（純函式）
# ────────────────────────────────────────────────────────
@router.post("/preview", response_model=DcaPreviewResponse)
async def preview_dca(payload: DcaPreviewRequest, _token: str = Depends(verify_token)):
    params = payload.model_dump()
    errors = _generator.validate_params(params)
    if errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": "參數驗證失敗", "errors": errors},
        )
    config = _generator.build_config(params)
    take_profit_percent = config.take_profit_percent
    entry = Decimal(str(payload.entry_price))

    if config.direction is DcaDirection.BOTH:
        plan_long = build_ladder(config, entry, "long")
        plan_short = build_ladder(config, entry, "short")
        ladder_long = _ladder_to_preview(plan_long, take_profit_percent)
        ladder_short = _ladder_to_preview(plan_short, take_profit_percent)
        required = max(ladder_long.required_capital, ladder_short.required_capital)
        return DcaPreviewResponse(
            direction="both",
            required_capital=required,
            ladder_long=ladder_long,
            ladder_short=ladder_short,
            data_status="live",
        )

    direction = payload.direction
    plan = build_ladder(config, entry, direction)
    ladder = _ladder_to_preview(plan, take_profit_percent)
    return DcaPreviewResponse(
        direction=direction,
        required_capital=ladder.required_capital,
        ladder=ladder,
        data_status="live",
    )


# ────────────────────────────────────────────────────────
# POST /api/v1/dca — 建立
# ────────────────────────────────────────────────────────
@router.post("", response_model=DcaCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_dca(
    payload: CreateDcaRequest,
    source: str = Query(default="hermes", description="來源"),
    reason: Optional[str] = Query(default=None, description="原因"),
    _token: str = Depends(verify_token),
):
    project_root = _get_project_root()
    guard = SafetyGuard(project_root)
    audit_logger = AuditLogger.get_instance(project_root)
    params = payload.model_dump()

    # 0. 寫操作頻率限制
    can_proceed, limit_msg = guard.check_rate_limit(source)
    if not can_proceed:
        audit_logger.log(source=source, action="create_dca", grid_id=None,
                         params=_prune_params(params), result="rejected", reason=reason,
                         error_msg=limit_msg)
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=limit_msg)

    # 1. 參數驗證
    errors = _generator.validate_params(params)
    if errors:
        audit_logger.log(source=source, action="create_dca", grid_id=None,
                         params=_prune_params(params), result="rejected", reason=reason,
                         error_msg=f"參數驗證失敗: {errors}")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": "參數驗證失敗", "errors": errors},
        )

    # 1.5 LIVE feature flag + 市場規則校驗（稽核 4.12 / 4.13）
    if params.get("account_mode", "paper") == "live":
        if not live_trading_enabled():
            message = "LIVE 建立功能尚未啟用（需要 GRID_LIVE_TRADING_ENABLED=true）"
            audit_logger.log(source=source, action="create_dca", grid_id=None,
                             params=_prune_params(params), result="rejected", reason=reason,
                             error_msg=message)
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=message)
        from routes.market import _lookup_market_from_ccxt_sync
        market_info = await asyncio.get_running_loop().run_in_executor(
            None, _lookup_market_from_ccxt_sync, params["symbol"], params["exchange"]
        )
        if market_info is None:
            message = "LIVE 建立前無法確認交易對市場規則"
            audit_logger.log(source=source, action="create_dca", grid_id=None,
                             params=_prune_params(params), result="rejected", reason=reason,
                             error_msg=message)
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=message)
        entry_price = params.get("entry_price")
        if entry_price is None:
            entry_price = await asyncio.get_running_loop().run_in_executor(
                None, _fetch_ticker_price_sync, params["symbol"], params["exchange"]
            )
        if entry_price is None or float(entry_price) <= 0:
            message = "LIVE 建立前無法取得入場價以驗證市場規則"
            audit_logger.log(source=source, action="create_dca", grid_id=None,
                             params=_prune_params(params), result="rejected", reason=reason,
                             error_msg=message)
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=message)
        config = _generator.build_config(params)
        market_errors = _validate_dca_market_rules(config, market_info, Decimal(str(entry_price)))
        params["market_validation"] = {
            "symbol": market_info.symbol, "exchange": market_info.exchange,
            "price_precision": market_info.price_precision,
            "quantity_precision": market_info.quantity_precision,
            "min_notional": market_info.min_notional,
        }
        if market_errors:
            audit_logger.log(source=source, action="create_dca", grid_id=None,
                             params=_prune_params(params), result="rejected", reason=reason,
                             error_msg=f"市場規則驗證失敗: {market_errors}")
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"message": "市場規則驗證失敗", "errors": market_errors},
            )

    # 2. 安全護欄（僅寫 YAML 時不要求引擎存活；auto_start 才走完整啟動檢查）
    wants_start = bool(params.get("auto_start", False))
    if wants_start:
        can_create, err_msg = guard.check_can_create_dca()
    else:
        can_create, err_msg = guard.check_dca_config_dir_writable()
    if not can_create:
        audit_logger.log(source=source, action="create_dca", grid_id=None,
                         params=_prune_params(params), result="rejected", reason=reason,
                         error_msg=err_msg)
        if "熔斷" in (err_msg or ""):
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=err_msg)
        elif "最大" in (err_msg or ""):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err_msg)
        else:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err_msg)

    # 4. 生成 YAML
    yaml_content, config_path = _generator.generate(params)
    bot_id = DcaYAMLGenerator.bot_id_from_path(config_path)

    # 檢查配置檔案是否已存在
    can_write, exist_msg = guard.check_config_not_exists(config_path)
    if not can_write:
        audit_logger.log(source=source, action="create_dca", grid_id=bot_id,
                         params=_prune_params(params), result="rejected", reason=reason,
                         error_msg=exist_msg)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exist_msg)

    # 寫入 YAML
    abs_yaml_path = os.path.join(project_root, config_path)
    try:
        with open(abs_yaml_path, "w", encoding="utf-8") as f:
            f.write(yaml_content)
        logger.info("✅ 已建立 DCA 配置: %s", config_path)
    except Exception as e:
        audit_logger.log(source=source, action="create_dca", grid_id=bot_id,
                         params=_prune_params(params), result="error", reason=reason,
                         error_msg=f"寫入 YAML 配置檔失敗: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail=f"寫入 YAML 配置檔失敗: {e}")

    dca_status = "created"
    command_id = None
    start_block = start_rejection(params.get("account_mode", "paper"), live_trading_enabled())
    if wants_start and start_block:
        logger.info("建立後未啟動 %s：%s", bot_id, start_block)
    elif wants_start:
        command = GridCommandRepository.instance().create(
            grid_id=bot_id, config_path=config_path, action="start",
            source=source, reason=reason or "auto_start after create",
        )
        command_id = command["command_id"]
        try:
            active_dca = _read_active_dca()
            active_dca[config_path] = True
            _write_active_dca(active_dca)
            GridCommandRepository.instance().update_status(command_id, "pending_engine")
            dca_status = "pending_engine"
            logger.info("已接受建立後啟動命令: %s command=%s", bot_id, command_id)
        except Exception as exc:
            GridCommandRepository.instance().update_status(command_id, "failed", str(exc))
            logger.error("更新 active_dca.json 失敗: %s", exc)
            dca_status = "created"

    guard.record_action(source)
    audit_logger.log(source=source, action="create_dca", grid_id=bot_id,
                     params=_prune_params(params), result="success", reason=reason)

    if dca_status != "pending_engine":
        active_dca = _read_active_dca()
        if config_path not in active_dca:
            active_dca[config_path] = False
            _write_active_dca(active_dca)

    if dca_status == "pending_engine":
        message = f"DCA {bot_id} 已建立，啟動命令已接受，等待引擎 heartbeat 確認"
    elif wants_start and start_block:
        message = f"DCA {bot_id} 已建立為設定檔。{start_block}"
    else:
        message = f"DCA {bot_id} 已成功建立"

    return DcaCreateResponse(
        bot_id=bot_id,
        config_path=config_path,
        status=dca_status,
        yaml_content=yaml_content,
        message=message,
        command_id=command_id,
    )


# ────────────────────────────────────────────────────────
# GET /api/v1/dca/{bot_id} — 單台設定
# ────────────────────────────────────────────────────────
@router.get("/{bot_id}", response_model=DcaInfo)
async def get_dca(bot_id: str, _token: str = Depends(verify_token)):
    _validate_bot_id(bot_id)
    config_path = DcaYAMLGenerator.path_from_bot_id(bot_id)
    active_dca = _read_active_dca()
    if config_path not in active_dca and not os.path.exists(
        os.path.join(_get_project_root(), config_path)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"DCA 配置 {bot_id} 不存在")
    return _build_dca_info(config_path, active_dca)


# ────────────────────────────────────────────────────────
# GET /api/v1/dca/{bot_id}/stats — 執行期統計
# ────────────────────────────────────────────────────────
@router.get("/{bot_id}/stats", response_model=DcaStatsResponse)
async def get_dca_stats(bot_id: str, _token: str = Depends(verify_token)):
    _validate_bot_id(bot_id)
    config_path = DcaYAMLGenerator.path_from_bot_id(bot_id)
    active_dca = _read_active_dca()
    model = _build_dca_read_model(config_path, active_dca)
    if model["bot_id"] != bot_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"DCA 配置 {bot_id} 不存在")
    return DcaStatsResponse(
        bot_id=model["bot_id"],
        status=model["status"],
        data_status=model["data_status"],
        last_updated_at=model["last_updated_at"],
        current_price=model["current_price"],
        active_direction=model["active_direction"],
        position=DcaPosition(**model["position"]),
        ladder=DcaLadderState(**model["ladder"]),
        cycles=model["cycles"],
        profit=DcaProfit(**model["profit"]),
    )


# ────────────────────────────────────────────────────────
# GET /api/v1/dca/{bot_id}/ladder — 當前實際階梯
# ────────────────────────────────────────────────────────
@router.get("/{bot_id}/ladder", response_model=DcaLadderResponse)
async def get_dca_ladder(bot_id: str, _token: str = Depends(verify_token)):
    _validate_bot_id(bot_id)
    config_path = DcaYAMLGenerator.path_from_bot_id(bot_id)
    active_dca = _read_active_dca()
    model = _build_dca_read_model(config_path, active_dca)
    runtime = GridStateStore.instance().get(config_path)
    current_price = _number((runtime or {}).get("current_price"))
    if current_price is None:
        return DcaLadderResponse(
            bot_id=bot_id,
            available=False,
            data_status=model["data_status"],
            message="引擎尚未提供價格，無法重算當前階梯",
        )
    yaml_data = _read_dca_yaml(config_path)
    if yaml_data is None:
        return DcaLadderResponse(
            bot_id=bot_id,
            available=False,
            data_status="unavailable",
            message="找不到 DCA 配置檔",
        )
    from core.services.dca.config_loader import load_dca_config
    config = load_dca_config(yaml_data)
    direction = model.get("active_direction") or (runtime or {}).get("active_direction")
    if direction in ("long", "short"):
        plan = build_ladder(config, Decimal(str(current_price)), direction)
        levels = [
            DcaLevelView(
                index=level.index,
                role="base" if level.index == 0 else "safety",
                planned_price=float(level.price),
                planned_quantity=float(level.quantity),
            )
            for level in plan.levels
        ]
        return DcaLadderResponse(
            bot_id=bot_id,
            available=True,
            data_status=model["data_status"],
            levels=levels,
        )
    return DcaLadderResponse(
        bot_id=bot_id,
        available=False,
        data_status=model["data_status"],
        message="目前為 armed/cooling 狀態，無鎖定方向的實際階梯",
    )


# ────────────────────────────────────────────────────────
# GET /api/v1/dca/{bot_id}/orders, /fills, /events
# ────────────────────────────────────────────────────────
@router.get("/{bot_id}/orders", response_model=DcaOrdersResponse)
async def get_dca_orders(bot_id: str, _token: str = Depends(verify_token)):
    return DcaOrdersResponse(
        bot_id=bot_id,
        available=False,
        message="引擎尚未提供 DCA 掛單資料",
    )


@router.get("/{bot_id}/fills", response_model=DcaFillsResponse)
async def get_dca_fills(bot_id: str, _token: str = Depends(verify_token)):
    return DcaFillsResponse(
        bot_id=bot_id,
        available=False,
        message="引擎尚未提供 DCA 成交資料",
    )


@router.get("/{bot_id}/events", response_model=DcaEventListResponse)
async def get_dca_events(bot_id: str, _token: str = Depends(verify_token)):
    _validate_bot_id(bot_id)
    events = GridCommandRepository.instance().list_events(bot_id)
    return DcaEventListResponse(
        bot_id=bot_id,
        events=[
            DcaEvent(
                bot_id=bot_id,
                event_type=item.get("event_type", ""),
                status=item.get("status", ""),
                detail=item.get("detail"),
                occurred_at=float(item.get("occurred_at", 0.0)),
            )
            for item in events
        ],
    )


# ────────────────────────────────────────────────────────
# POST /api/v1/dca/{bot_id}/start
# ────────────────────────────────────────────────────────
@router.post("/{bot_id}/start", response_model=DcaActionResponse)
async def start_dca(
    bot_id: str,
    source: str = Query(default="hermes", description="來源"),
    reason: Optional[str] = Query(default=None, description="原因"),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    _token: str = Depends(verify_token),
):
    _validate_bot_id(bot_id)
    project_root = _get_project_root()
    guard = SafetyGuard(project_root)
    audit_logger = AuditLogger.get_instance(project_root)

    can_proceed, limit_msg = guard.check_rate_limit(source)
    if not can_proceed:
        audit_logger.log(source=source, action="start", grid_id=bot_id,
                         params={"bot_id": bot_id}, result="rejected", reason=reason,
                         error_msg=limit_msg)
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=limit_msg)

    config_path = DcaYAMLGenerator.path_from_bot_id(bot_id)
    abs_path = os.path.join(project_root, config_path)
    if not os.path.exists(abs_path):
        audit_logger.log(source=source, action="start", grid_id=bot_id,
                         params={"bot_id": bot_id}, result="rejected", reason=reason,
                         error_msg=f"DCA 配置 {bot_id} 不存在")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"DCA 配置 {bot_id} 不存在")

    yaml_data = _read_dca_yaml(config_path) or {}
    blocked = start_rejection(dca_account_mode_from_yaml(yaml_data), live_trading_enabled())
    if blocked:
        audit_logger.log(source=source, action="start", grid_id=bot_id,
                         params={"bot_id": bot_id}, result="rejected", reason=reason,
                         error_msg=blocked)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=blocked)

    command_repo = GridCommandRepository.instance()
    if idempotency_key:
        existing = command_repo.get_by_idempotency_key("start", idempotency_key, grid_id=bot_id)
        if existing:
            return DcaActionResponse(
                bot_id=existing["grid_id"], action="start", status=existing["status"],
                command_id=existing["command_id"], message="重複請求已回傳原始命令",
            )

    can_create, err_msg = guard.check_can_create_dca()
    if not can_create:
        audit_logger.log(source=source, action="start", grid_id=bot_id,
                         params={"bot_id": bot_id}, result="rejected", reason=reason,
                         error_msg=err_msg)
        if "熔斷" in (err_msg or ""):
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=err_msg)
        else:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err_msg)

    active_dca = _read_active_dca()
    if active_dca.get(config_path, False):
        return DcaActionResponse(
            bot_id=bot_id, action="start", status="already_running",
            message=f"DCA {bot_id} 已在運行中",
        )

    try:
        command = GridCommandRepository.instance().create(
            grid_id=bot_id, config_path=config_path, action="start",
            source=source, reason=reason, idempotency_key=idempotency_key,
        )
    except sqlite3.IntegrityError:
        existing = command_repo.get_by_idempotency_key("start", idempotency_key or "", grid_id=bot_id)
        if existing:
            return DcaActionResponse(
                bot_id=existing["grid_id"], action="start", status=existing["status"],
                command_id=existing["command_id"], message="重複請求已回傳原始命令",
            )
        raise
    try:
        active_dca[config_path] = True
        _write_active_dca(active_dca)
        GridCommandRepository.instance().update_status(command["command_id"], "pending_engine")
        guard.record_action(source)
        audit_logger.log(source=source, action="start", grid_id=bot_id,
                         params={"bot_id": bot_id}, result="success", reason=reason)
        return DcaActionResponse(
            bot_id=bot_id, action="start", status="pending_engine",
            command_id=command["command_id"],
            message=f"DCA {bot_id} 的啟動命令已接受，等待引擎 heartbeat 確認",
        )
    except Exception as e:
        GridCommandRepository.instance().update_status(command["command_id"], "failed", str(e))
        audit_logger.log(source=source, action="start", grid_id=bot_id,
                         params={"bot_id": bot_id}, result="error", reason=reason,
                         error_msg=f"啟動 DCA 異常: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail=f"啟動 DCA 異常: {e}")


# ────────────────────────────────────────────────────────
# POST /api/v1/dca/{bot_id}/stop
# ────────────────────────────────────────────────────────
@router.post("/{bot_id}/stop", response_model=DcaActionResponse)
async def stop_dca(
    bot_id: str,
    source: str = Query(default="hermes", description="來源"),
    reason: Optional[str] = Query(default=None, description="原因"),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    _token: str = Depends(verify_token),
):
    _validate_bot_id(bot_id)
    project_root = _get_project_root()
    guard = SafetyGuard(project_root)
    audit_logger = AuditLogger.get_instance(project_root)

    can_proceed, limit_msg = guard.check_rate_limit(source)
    if not can_proceed:
        audit_logger.log(source=source, action="stop", grid_id=bot_id,
                         params={"bot_id": bot_id}, result="rejected", reason=reason,
                         error_msg=limit_msg)
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=limit_msg)

    config_path = DcaYAMLGenerator.path_from_bot_id(bot_id)
    active_dca = _read_active_dca()
    if config_path not in active_dca:
        audit_logger.log(source=source, action="stop", grid_id=bot_id,
                         params={"bot_id": bot_id}, result="rejected", reason=reason,
                         error_msg=f"DCA {bot_id} 不在 active_dca 控制中")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"DCA {bot_id} 不在 active_dca 控制中")

    command_repo = GridCommandRepository.instance()
    if idempotency_key:
        existing = command_repo.get_by_idempotency_key("stop", idempotency_key, grid_id=bot_id)
        if existing:
            return DcaActionResponse(
                bot_id=existing["grid_id"], action="stop", status=existing["status"],
                command_id=existing["command_id"], message="重複請求已回傳原始命令",
            )

    if not active_dca.get(config_path, False):
        return DcaActionResponse(
            bot_id=bot_id, action="stop", status="already_stopped",
            message=f"DCA {bot_id} 已處於停止狀態",
        )

    command = GridCommandRepository.instance().create(
        grid_id=bot_id, config_path=config_path, action="stop",
        source=source, reason=reason, idempotency_key=idempotency_key,
    )
    try:
        active_dca[config_path] = False
        _write_active_dca(active_dca)
        GridCommandRepository.instance().update_status(command["command_id"], "pending_engine")
        guard.record_action(source)
        audit_logger.log(source=source, action="stop", grid_id=bot_id,
                         params={"bot_id": bot_id}, result="success", reason=reason)
        return DcaActionResponse(
            bot_id=bot_id, action="stop", status="pending_engine",
            command_id=command["command_id"],
            message=f"DCA {bot_id} 的停止命令已接受，等待引擎取消掛單並確認",
        )
    except Exception as e:
        GridCommandRepository.instance().update_status(command["command_id"], "failed", str(e))
        audit_logger.log(source=source, action="stop", grid_id=bot_id,
                         params={"bot_id": bot_id}, result="error", reason=reason,
                         error_msg=f"停止 DCA 異常: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail=f"停止 DCA 異常: {e}")


# ────────────────────────────────────────────────────────
# DELETE /api/v1/dca/{bot_id}
# ────────────────────────────────────────────────────────
@router.delete("/{bot_id}", response_model=DcaActionResponse)
async def delete_dca(
    bot_id: str,
    source: str = Query(default="hermes", description="來源"),
    reason: Optional[str] = Query(default=None, description="原因"),
    force: bool = Query(default=False, description="是否強制停止並刪除運行中的策略"),
    _token: str = Depends(verify_token),
):
    _validate_bot_id(bot_id)
    project_root = _get_project_root()
    guard = SafetyGuard(project_root)
    audit_logger = AuditLogger.get_instance(project_root)

    can_proceed, limit_msg = guard.check_rate_limit(source)
    if not can_proceed:
        audit_logger.log(source=source, action="delete", grid_id=bot_id,
                         params={"bot_id": bot_id}, result="rejected", reason=reason,
                         error_msg=limit_msg)
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=limit_msg)

    config_path = DcaYAMLGenerator.path_from_bot_id(bot_id)
    abs_yaml = os.path.join(project_root, config_path)
    if not os.path.exists(abs_yaml):
        audit_logger.log(source=source, action="delete", grid_id=bot_id,
                         params={"bot_id": bot_id}, result="rejected", reason=reason,
                         error_msg=f"DCA 配置 {bot_id} 不存在")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"DCA 配置 {bot_id} 不存在")

    try:
        active_dca = _read_active_dca()
        model = _build_dca_read_model(config_path, active_dca)
        if not force:
            blocked = delete_rejection(model["status"], model.get("command_status"))
            if blocked:
                audit_logger.log(source=source, action="delete", grid_id=bot_id,
                                 params={"bot_id": bot_id, "status": model["status"]},
                                 result="rejected", reason=reason, error_msg=blocked)
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=blocked)

        was_running = active_dca.get(config_path, False)
        if was_running:
            try:
                GridCommandRepository.instance().create(
                    grid_id=bot_id, config_path=config_path, action="stop",
                    source=source, reason=reason or "force_delete",
                )
            except Exception:
                pass
            active_dca[config_path] = False
            _write_active_dca(active_dca)
            logger.info("刪除前已停止 DCA: %s", bot_id)

        if config_path in active_dca:
            del active_dca[config_path]
            _write_active_dca(active_dca)

        GridStateStore.instance().remove(config_path)

        archive_dir = os.path.join(project_root, "config", "dca", "archived")
        os.makedirs(archive_dir, exist_ok=True)
        archived_path = os.path.join(archive_dir, Path(config_path).name)
        archive_success = False
        try:
            os.rename(abs_yaml, archived_path)
            archive_success = True
        except Exception as e:
            logger.error("歸檔失敗: %s，嘗試直接刪除", e)
            try:
                os.remove(abs_yaml)
                archive_success = True
            except Exception as e2:
                logger.error("刪除也失敗: %s", e2)

        if not archive_success:
            raise Exception("無法移動或刪除 DCA 配置文件")

        guard.record_action(source)
        audit_logger.log(source=source, action="delete", grid_id=bot_id,
                         params={"bot_id": bot_id}, result="success", reason=reason)
        return DcaActionResponse(
            bot_id=bot_id, action="delete", status="deleted",
            message=f"DCA {bot_id} 已{'停止並' if was_running else ''}刪除（YAML 已歸檔）",
        )
    except HTTPException:
        raise
    except Exception as e:
        audit_logger.log(source=source, action="delete", grid_id=bot_id,
                         params={"bot_id": bot_id}, result="error", reason=reason,
                         error_msg=f"刪除 DCA 異常: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail=f"刪除 DCA 異常: {e}")


def _prune_params(params: Mapping[str, Any]) -> dict:
    """審計時移除可能包含秘密的控制鍵（稽核 4.18）。"""
    return {
        k: v for k, v in params.items()
        if k not in {"api_key", "api_secret", "secret", "token", "entry_price", "Idempotency-Key"}
    }
