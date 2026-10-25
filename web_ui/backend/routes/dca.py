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

from web_ui.backend.audit import AuditLogger
from web_ui.backend.auth import verify_token
from web_ui.backend.dca_yaml_generator import DcaYAMLGenerator
from web_ui.backend.safety_guard import SafetyGuard
from web_ui.backend.grid_state_store import GridStateStore
from web_ui.backend.command_repository import GridCommandRepository
from web_ui.backend.runtime_repository import RuntimeSnapshotRepository
from core.infrastructure.database.redis_support import RedisCommandQueue
from web_ui.backend.lifecycle import (
    data_status_from_runtime,
    delete_rejection,
    live_trading_enabled,
    start_rejection,
    write_json_atomic,
)
from web_ui.backend.schemas import (
    CreateDcaRequest,
    DcaActionResponse,
    DcaChartMark,
    DcaChartLine,
    DcaChartResponse,
    KlineCandle,
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
    BacktestCycleRecord,
    BacktestEquityPoint,
    BacktestMetrics,
    BacktestRequest,
    BacktestResponse,
    BacktestResult,
    BacktestTradeMark,
    SweepBest,
    SweepBudget,
    SweepRequest,
    SweepResponse,
    SweepRunResult,
    UpdateDcaConfigRequest,
)
from core.services.dca.config_loader import dca_account_mode_from_yaml, load_dca_config
from core.services.dca.ladder import build_ladder, stop_loss_price, take_profit_price
from core.services.dca.models.dca_config import DcaDirection
from core.services.dca.backtest import (
    ReplayDriver,
    ReplayExchange,
    compute_metrics,
    run_sweep,
    expand_grid,
)
from web_ui.backend.kline_service import KlineService

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
    runtime = runtime_map.get(config_path) if runtime_map is not None else GridStateStore.instance().get(config_path)
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

    # 合併執行期快照指標即時值（Phase 5+ 卡片與詳情頁動態呈現）
    runtime_ind_results = (runtime or {}).get("indicator_results") or []
    for idx, ind_dict in enumerate(entry_indicators):
        if idx < len(runtime_ind_results):
            rt_res = runtime_ind_results[idx]
            if isinstance(rt_res, Mapping):
                ind_dict["current_value"] = _number(rt_res.get("value"))
                ind_dict["triggered"] = bool(rt_res.get("triggered")) if rt_res.get("triggered") is not None else None
                ind_dict["triggered_long"] = bool(rt_res.get("triggered_long")) if rt_res.get("triggered_long") is not None else None
                ind_dict["triggered_short"] = bool(rt_res.get("triggered_short")) if rt_res.get("triggered_short") is not None else None
                ind_dict["insufficient_data"] = bool(rt_res.get("insufficient_data")) if rt_res.get("insufficient_data") is not None else None
                ind_dict["warmup_have"] = _int(rt_res.get("warmup_have"))
                ind_dict["warmup_need"] = _int(rt_res.get("warmup_need"))
                ind_dict["details"] = dict(rt_res.get("details")) if isinstance(rt_res.get("details"), Mapping) else None

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

    derived_next_safety_price = _number((runtime or {}).get("next_safety_order_price"))
    if derived_next_safety_price is None and status in ("averaging", "degraded") and effective_direction:
        try:
            from core.services.dca.config_loader import load_dca_config
            from core.services.dca.ladder import build_ladder
            cfg = load_dca_config(yaml_data)
            anchor = _number((runtime or {}).get("entry_price")) or average_cost
            if anchor:
                plan = build_ladder(cfg, Decimal(str(anchor)), effective_direction)
                filled_cnt = _int(ladder.get("filled_safety_orders")) or 0
                next_level = next((lvl for lvl in plan.levels if lvl.index == filled_cnt + 1), None)
                if next_level:
                    derived_next_safety_price = float(next_level.price)
        except Exception as exc:
            logger.warning("DCA 導出下一張安全單價失敗 [%s]: %s", bot_id, exc)

    resolved_symbol = str(section.get("symbol")) if section.get("symbol") else None
    resolved_exchange = str(section.get("exchange")) if section.get("exchange") else None
    resolved_direction = str(section.get("direction")) if section.get("direction") else None
    # YAML 遺失時只還原可無歧義解析的欄位（exchange/direction）。
    # symbol 在 bot_id 中以 "-" 取代 "/" 與 ":"，無法安全還原（例如
    # ZEC/USDC:USDC 會變成 ZEC-USDC-USDC），因此寧可回 None 也不偽造。
    if not yaml_data and bot_id.startswith("dca-"):
        parts = bot_id.split("-")
        if len(parts) >= 5:
            if not resolved_exchange:
                resolved_exchange = parts[1]
            if not resolved_direction and parts[2] in ("long", "short", "both"):
                resolved_direction = parts[2]

    return {
        "bot_id": bot_id,
        "config_path": config_path,
        "symbol": resolved_symbol,
        "exchange": resolved_exchange,
        "direction": resolved_direction,
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
        "total_trades": _int((runtime or {}).get("total_trades")) or (len((runtime or {}).get("fills") or []) if isinstance((runtime or {}).get("fills"), list) else None),
        "profit": {
            "realized": _number(profit.get("realized")),
            "unrealized": _number(profit.get("unrealized")),
            "fees": _number(profit.get("fees")),
        },
        "base_order_amount": _number(section.get("base_order_amount")),
        "max_safety_orders": _int(section.get("max_safety_orders")),
        "take_profit_percent": _number(section.get("take_profit_percent")),
        "stop_loss_percent": _number(section.get("stop_loss_percent")),
        "cooldown_seconds": _int(section.get("cooldown_seconds")) if section.get("cooldown_seconds") is not None else 300,
        "repeat_after_take_profit": bool(section.get("repeat_after_take_profit", True)),
        "entry_mode": str(entry_section.get("mode")) if isinstance(entry_section, Mapping) and entry_section.get("mode") else "immediate",
        "entry_logic": str(entry_section.get("logic")) if isinstance(entry_section, Mapping) and entry_section.get("logic") else None,
        "entry_timeframe": str(entry_section.get("timeframe")) if isinstance(entry_section, Mapping) and entry_section.get("timeframe") else None,
        "entry_indicators": entry_indicators,
        "entry_price": _number((runtime or {}).get("entry_price")),
        "next_safety_order_price": derived_next_safety_price,
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
        cooldown_seconds=model.get("cooldown_seconds", 300),
        repeat_after_take_profit=model.get("repeat_after_take_profit", True),
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

    # 1.5 交易對有效性預檢與自適應正規化（不論 LIVE 還是 PAPER 都執行市場存在性驗證）
    from web_ui.backend.routes.market import _lookup_market_from_ccxt_sync
    market_info = await asyncio.get_running_loop().run_in_executor(
        None, _lookup_market_from_ccxt_sync, params["symbol"], params["exchange"]
    )
    if market_info is None:
        message = f"交易對 {params['symbol']} 在交易所 {params['exchange']} 中不存在或無法交易，拒絕建立！"
        audit_logger.log(source=source, action="create_dca", grid_id=None,
                         params=_prune_params(params), result="rejected", reason=reason,
                         error_msg=message)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=message)

    if market_info.canonical_symbol:
        params["symbol"] = market_info.canonical_symbol

    # 1.6 LIVE feature flag + 市場規則校驗（稽核 4.12 / 4.13）
    if params.get("account_mode", "paper") == "live":
        if not live_trading_enabled():
            message = "LIVE 建立功能尚未啟用（需要 GRID_LIVE_TRADING_ENABLED=true）"
            audit_logger.log(source=source, action="create_dca", grid_id=None,
                             params=_prune_params(params), result="rejected", reason=reason,
                             error_msg=message)
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=message)
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
    else:
        try:
            from web_ui.backend.routes.market import _lookup_market_from_ccxt_sync
            market_info = await asyncio.get_running_loop().run_in_executor(
                None, _lookup_market_from_ccxt_sync, params["symbol"], params["exchange"]
            )
            if market_info and market_info.symbol:
                params["symbol"] = market_info.symbol
        except Exception:
            pass

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

    # 4. 生成 YAML（P1-1：編輯/重建時沿用既有 bot_id 保留交易紀錄）
    yaml_content, config_path = _generator.generate(params, existing_bot_id=params.get("bot_id"))
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
            RedisCommandQueue().publish("START", "dca", config_path, command_id=command_id)
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
        total_trades=model.get("total_trades"),
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

    # Phase 7：引擎已提供實際階梯槽位（含三態）→ 直接回傳，避免前端與重算漂移
    runtime_slots = (runtime or {}).get("ladder_slots")
    if isinstance(runtime_slots, list) and runtime_slots:
        levels = [
            DcaLevelView(
                index=_int(slot.get("index")) or 0,
                role=str(slot.get("role") or ("base" if slot.get("index") == 0 else "safety")),
                status=str(slot.get("status") or "waiting"),
                planned_price=_number(slot.get("planned_price")),
                planned_quantity=_number(slot.get("planned_quantity")),
                actual_price=_number(slot.get("actual_price")),
                filled_price=_number(slot.get("filled_price")),
                filled_quantity=_number(slot.get("filled_quantity")),
            )
            for slot in runtime_slots
            if isinstance(slot, Mapping)
        ]
        return DcaLadderResponse(
            bot_id=bot_id,
            available=True,
            data_status=model["data_status"],
            levels=levels,
        )

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
        anchor_price = _number(model.get("entry_price")) or _number(model.get("avg_price")) or current_price
        plan = build_ladder(config, Decimal(str(anchor_price)), direction)
        pos_qty = _number(model.get("position", {}).get("quantity")) or 0
        filled_count = _int(model.get("ladder", {}).get("filled_safety_orders")) or 0
        active_count = _int(model.get("ladder", {}).get("active_safety_orders")) or 0

        levels = []
        for level in plan.levels:
            if level.index == 0:
                is_filled = pos_qty > 0 or model.get("status") in ("averaging", "degraded", "completed")
                status_str = "filled" if is_filled else ("active" if model.get("status") == "triggering" else "waiting")
            else:
                if level.index <= filled_count:
                    status_str = "filled"
                elif level.index <= filled_count + active_count:
                    status_str = "active"
                else:
                    status_str = "waiting"
            levels.append(
                DcaLevelView(
                    index=level.index,
                    role="base" if level.index == 0 else "safety",
                    status=status_str,
                    planned_price=float(level.price),
                    planned_quantity=float(level.quantity),
                    filled_price=float(anchor_price) if level.index == 0 and status_str == "filled" else None,
                    filled_quantity=float(level.quantity) if level.index == 0 and status_str == "filled" else None,
                )
            )
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
    _validate_bot_id(bot_id)
    config_path = DcaYAMLGenerator.path_from_bot_id(bot_id)
    runtime = GridStateStore.instance().get(config_path)
    if runtime is None:
        return DcaOrdersResponse(
            bot_id=bot_id,
            available=False,
            message="引擎尚未提供 DCA 掛單資料",
        )
    orders = runtime.get("open_orders")
    if not isinstance(orders, list):
        # 引擎心跳存在但尚未帶掛單欄位（例如 armed 零掛單）→ 空陣列而非不可用
        orders = []
    return DcaOrdersResponse(
        bot_id=bot_id,
        available=True,
        orders=orders,
    )


@router.get("/{bot_id}/fills", response_model=DcaFillsResponse)
async def get_dca_fills(bot_id: str, _token: str = Depends(verify_token)):
    _validate_bot_id(bot_id)
    config_path = DcaYAMLGenerator.path_from_bot_id(bot_id)
    runtime = GridStateStore.instance().get(config_path)
    if runtime is None:
        return DcaFillsResponse(
            bot_id=bot_id,
            available=False,
            message="引擎尚未提供 DCA 成交資料",
        )
    fills = runtime.get("fills")
    if not isinstance(fills, list):
        fills = []
    return DcaFillsResponse(
        bot_id=bot_id,
        available=True,
        fills=fills,
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
    cooldown_seconds: Optional[int] = Query(default=None, ge=0, description="開啟時可自訂每輪冷卻秒數"),
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

    # 開啟時若傳入冷卻秒數，寫回配置 YAML
    if cooldown_seconds is not None:
        if "dca" in yaml_data and isinstance(yaml_data["dca"], dict):
            yaml_data["dca"]["cooldown_seconds"] = cooldown_seconds
            with open(abs_path, "w", encoding="utf-8") as f:
                yaml.dump(yaml_data, f, allow_unicode=True, sort_keys=False)

    blocked = start_rejection(dca_account_mode_from_yaml(yaml_data), live_trading_enabled())
    if blocked:
        audit_logger.log(source=source, action="start", grid_id=bot_id,
                         params={"bot_id": bot_id}, result="rejected", reason=reason,
                         error_msg=blocked)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=blocked)

    # 啟動前交易對市場檢驗門禁
    cfg_symbol = yaml_data.get("dca", {}).get("symbol")
    cfg_exchange = yaml_data.get("dca", {}).get("exchange", "binance")
    if cfg_symbol:
        from web_ui.backend.routes.market import _lookup_market_from_ccxt_sync
        m_info = await asyncio.get_running_loop().run_in_executor(
            None, _lookup_market_from_ccxt_sync, cfg_symbol, cfg_exchange
        )
        if m_info is None:
            err_msg = f"配置中交易對 {cfg_symbol} 在交易所 {cfg_exchange} 中無效或不存在，禁止啟動！"
            audit_logger.log(
                source=source, action="start", grid_id=bot_id,
                params={"bot_id": bot_id}, result="rejected", reason=reason,
                error_msg=err_msg
            )
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=err_msg)

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
        RedisCommandQueue().publish("START", "dca", config_path, command_id=command["command_id"])
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
# PATCH /api/v1/dca/{bot_id}/config — 修改冷卻時間等參數
# ────────────────────────────────────────────────────────
@router.patch("/{bot_id}/config", response_model=DcaInfo)
async def update_dca_config(
    bot_id: str,
    payload: UpdateDcaConfigRequest,
    source: str = Query(default="hermes", description="來源"),
    reason: Optional[str] = Query(default=None, description="原因"),
    _token: str = Depends(verify_token),
):
    _validate_bot_id(bot_id)
    project_root = _get_project_root()
    audit_logger = AuditLogger.get_instance(project_root)

    config_path = DcaYAMLGenerator.path_from_bot_id(bot_id)
    abs_path = os.path.join(project_root, config_path)
    if not os.path.exists(abs_path):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"DCA 配置 {bot_id} 不存在")

    active_dca = _read_active_dca()
    if active_dca.get(config_path, False):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"DCA {bot_id} 正在運行中，請先停止機器人再修改參數",
        )

    yaml_data = _read_dca_yaml(config_path) or {}
    if "dca" not in yaml_data or not isinstance(yaml_data["dca"], dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="無效的 DCA 配置結構")

    changed = False
    if payload.cooldown_seconds is not None:
        yaml_data["dca"]["cooldown_seconds"] = payload.cooldown_seconds
        changed = True
    if payload.repeat_after_take_profit is not None:
        yaml_data["dca"]["repeat_after_take_profit"] = payload.repeat_after_take_profit
        changed = True

    if changed:
        with open(abs_path, "w", encoding="utf-8") as f:
            yaml.dump(yaml_data, f, allow_unicode=True, sort_keys=False)
        audit_logger.log(
            source=source, action="update_config", grid_id=bot_id,
            params=payload.model_dump(exclude_unset=True), result="success", reason=reason
        )

    return _build_dca_info(config_path, active_dca)


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
        RedisCommandQueue().publish("STOP", "dca", config_path, command_id=command["command_id"])
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
# GET /api/v1/dca/{bot_id}/chart — 即時圖表（K線 + runtime 快照標記）
# ────────────────────────────────────────────────────────
@router.get("/{bot_id}/chart", response_model=DcaChartResponse)
async def get_dca_chart(
    bot_id: str,
    timeframe: str = Query(default="15m", description="K 線時框 (1m/5m/15m/30m/1h/4h/1d)"),
    limit: int = Query(default=300, ge=1, le=20000, description="K 棒數量 (上限 20000)"),
    _token: str = Depends(verify_token),
):
    """
    即時圖表：K 線 + 本機器人實際下單/成交/止盈標記 + 階梯/均價/止盈價格線（§4.7）。
    
    引擎零變更：下單/收益標記的資料已在心跳裡（§2.2 既有 _fills / _orders / ladder_slots）
    此端點僅做投影，不做任何計算（G-4）。
    """
    _validate_bot_id(bot_id)
    config_path = DcaYAMLGenerator.path_from_bot_id(bot_id)
    active_dca = _read_active_dca()
    
    # 檢查 bot 存在
    if config_path not in active_dca and not os.path.exists(
        os.path.join(_get_project_root(), config_path)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"DCA 配置 {bot_id} 不存在")
    
    yaml_data = _read_dca_yaml(config_path) or {}
    section = _dca_section(yaml_data)
    runtime = GridStateStore.instance().get(config_path)
    
    symbol = str(section.get("symbol")) if section.get("symbol") else None
    exchange = str(section.get("exchange")) if section.get("exchange") else None
    account_mode = dca_account_mode_from_yaml(yaml_data)
    
    if symbol is None or exchange is None:
        return DcaChartResponse(
            bot_id=bot_id,
            candles=[],
            marks=[],
            lines=[],
            current_price=None,
            data_status="unavailable",
            source="yaml",
            warnings=["找不到交易對符號或交易所資訊"],
        )
    
    # 取得 K 線資料
    kline_service = KlineService.instance()
    kline_result = await kline_service.get_candles(
        exchange=exchange,
        symbol=symbol,
        timeframe=timeframe,
        limit=limit,
        account_mode=account_mode,
    )
    
    # 取得 runtime 快照資料
    fills = (runtime or {}).get("fills") or []
    open_orders = (runtime or {}).get("open_orders") or []
    ladder_slots = (runtime or {}).get("ladder_slots") or []
    position = (runtime or {}).get("position") or {}
    current_price = _number((runtime or {}).get("current_price"))
    active_direction = (runtime or {}).get("active_direction")
    
    # 方向解析
    yaml_direction = section.get("direction")
    effective_direction = active_direction
    if effective_direction not in ("long", "short"):
        effective_direction = yaml_direction if yaml_direction in ("long", "short") else None
    
    # 從 YAML 取得設定值
    take_profit_pct = _number(section.get("take_profit_percent"))
    stop_loss_pct = _number(section.get("stop_loss_percent"))
    base_order_amount = _number(section.get("base_order_amount"))
    
    # 建立價格線
    lines: List[DcaChartLine] = []
    
    # entry_price 線（本輪基礎單成交價）
    entry_price = _number((runtime or {}).get("entry_price"))
    if entry_price is not None:
        lines.append(DcaChartLine(kind="entry", price=entry_price))
    else:
        lines.append(DcaChartLine(kind="entry", price=None))
    
    # average_cost 線（攤平均價）
    avg_cost = _number(position.get("average_cost")) if position else None
    if avg_cost is not None:
        lines.append(DcaChartLine(kind="average_cost", price=avg_cost))
    else:
        lines.append(DcaChartLine(kind="average_cost", price=None))
    
    # take_profit 線（當前 TP 價）
    if effective_direction and avg_cost is not None and take_profit_pct is not None:
        try:
            tp_price = float(take_profit_price(Decimal(str(avg_cost)), take_profit_pct, effective_direction))
            lines.append(DcaChartLine(kind="take_profit", price=tp_price))
        except Exception:
            lines.append(DcaChartLine(kind="take_profit", price=None))
    else:
        lines.append(DcaChartLine(kind="take_profit", price=None))
    
    # next_safety 線（下一張安全單價）
    next_safety_price = _number((runtime or {}).get("next_safety_order_price"))
    if next_safety_price is not None:
        lines.append(DcaChartLine(kind="next_safety", price=next_safety_price))
    else:
        lines.append(DcaChartLine(kind="next_safety", price=None))
    
    # stop_loss 線
    if effective_direction and avg_cost is not None and stop_loss_pct is not None:
        try:
            sl_price = float(stop_loss_price(Decimal(str(avg_cost)), stop_loss_pct, effective_direction))
            lines.append(DcaChartLine(kind="stop_loss", price=sl_price))
        except Exception:
            lines.append(DcaChartLine(kind="stop_loss", price=None))
    else:
        lines.append(DcaChartLine(kind="stop_loss", price=None))
    
    # 建立交易標記（從 fills）
    marks: List[DcaChartMark] = []
    for fill in fills:
        if not isinstance(fill, Mapping):
            continue
        
        ts = fill.get("occurred_at")
        if ts is None:
            continue
        
        # 嘗試把 timestamp 轉為 int（可能是 float 或 string）
        try:
            ts_int = int(float(ts))
        except (ValueError, TypeError):
            continue
        
        price = _number(fill.get("price"))
        role = str(fill.get("role")) if fill.get("role") else "base"
        side = str(fill.get("side")) if fill.get("side") else "buy"
        quantity = _number(fill.get("quantity"))
        notional = _number(fill.get("notional"))
        slot_index = _int(fill.get("slot_index"))
        cycle_index = _int(fill.get("cycle_index")) or 1
        
        # 判斷 kind
        if role == "base":
            kind = "base_filled"
        elif role == "safety":
            kind = "safety_filled"
        elif role == "take_profit":
            kind = "take_profit_filled"
        elif role == "stop_loss":
            kind = "stop_loss_filled"
        else:
            kind = "base_filled"
        
        marks.append(DcaChartMark(
            ts=ts_int,
            price=price if price is not None else 0.0,
            kind=kind,
            role=role,
            side=side,
            quantity=quantity,
            notional=notional,
            slot_index=slot_index,
            cycle_index=cycle_index,
            realized_pct=None,  # 即時圖表不顯示收益標記
        ))
    
    # 從 open_orders 新增 order_placed 標記
    for order in open_orders:
        if not isinstance(order, Mapping):
            continue
        
        ts = order.get("created_at") or order.get("occurred_at")
        if ts is None:
            continue
        
        try:
            ts_int = int(float(ts))
        except (ValueError, TypeError):
            continue
        
        price = _number(order.get("price"))
        role = str(order.get("role")) if order.get("role") else "base"
        side = str(order.get("side")) if order.get("side") else "buy"
        quantity = _number(order.get("quantity"))
        
        marks.append(DcaChartMark(
            ts=ts_int,
            price=price if price is not None else 0.0,
            kind="order_placed",
            role=role,
            side=side,
            quantity=quantity,
            notional=None,
            slot_index=None,
            cycle_index=1,
            realized_pct=None,
        ))
    
    # 從 ladder_slots 新增 skipped 標記（急跌穿透）
    for slot in ladder_slots:
        if not isinstance(slot, Mapping):
            continue
        
        slot_status = slot.get("status")
        if slot_status != "skipped":
            continue
        
        ts = slot.get("occurred_at")
        if ts is None:
            continue
        
        try:
            ts_int = int(float(ts))
        except (ValueError, TypeError):
            continue
        
        price = _number(slot.get("planned_price"))
        slot_index = _int(slot.get("index"))
        
        marks.append(DcaChartMark(
            ts=ts_int,
            price=price if price is not None else 0.0,
            kind="skipped",
            role="safety",
            side="buy",
            quantity=None,
            notional=None,
            slot_index=slot_index,
            cycle_index=1,
            realized_pct=None,
        ))
    
    # 建立 K 線回應（KlineCandle 由模組頂端統一匯入；此處不得再做函式內 import，
    # 否則 `from schemas import ...` 會建立與 response_model 不同的類別實例而觸發
    # pydantic model_type 驗證錯誤 → 500）。
    chart_candles = [
        KlineCandle(
            t=int(c.ts),
            o=float(c.open),
            h=float(c.high),
            l=float(c.low),
            c=float(c.close),
            v=float(c.volume),
        )
        for c in kline_result.candles
    ]
    
    # 處理 warnings。
    # KlineService 在 PAPER 路徑已經放入「使用合成 K 線，非真實歷史」，
    # 這裡若無條件再 append 就會出現重複條目；前端是全量渲染 warnings，
    # 重複會直接顯示成兩行（C8 live 驗證實際觀察到）。因此改為去重後附加。
    warnings = list(kline_result.warnings)
    if kline_result.data_status == "paper" and "使用合成 K 線，非真實歷史" not in warnings:
        warnings.append("使用合成 K 線，非真實歷史")
    
    effective_current_price = current_price
    if effective_current_price is None and chart_candles:
        effective_current_price = chart_candles[-1].c

    return DcaChartResponse(
        bot_id=bot_id,
        candles=chart_candles,
        marks=marks,
        lines=lines,
        current_price=effective_current_price,
        data_status=kline_result.data_status,
        source=kline_result.source,
        warnings=warnings,
    )


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
    active_dca = _read_active_dca()
    in_active = config_path in active_dca
    has_state = GridStateStore.instance().get(config_path) is not None

    if not os.path.exists(abs_yaml):
        if in_active or has_state:
            # 幽靈/殘留清理只移除「無即時心跳」的登記殘留。
            # 不建立 STOP、不取消掛單、不動用任何交易所資源，因此不會改變持倉。
            runtime = GridStateStore.instance().get(config_path)
            data_status = data_status_from_runtime(runtime)
            if data_status == "live":
                audit_logger.log(source=source, action="delete", grid_id=bot_id,
                                 params={"bot_id": bot_id, "data_status": data_status},
                                 result="rejected", reason=reason,
                                 error_msg="殘留 DCA 仍有即時心跳")
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"DCA {bot_id} 的 YAML 已不存在，但仍有即時心跳。"
                        "為避免誤停執行中的策略或動到持倉，請先停止該 DCA，"
                        "待心跳過期（停止後約 3 分鐘）再刪除"
                    ),
                )
            previous_desired = active_dca.get(config_path, False) if in_active else False
            if in_active:
                del active_dca[config_path]
                _write_active_dca(active_dca)
            if has_state:
                GridStateStore.instance().remove(config_path)
            guard.record_action(source)
            audit_logger.log(source=source, action="delete", grid_id=bot_id,
                             params={
                                 "bot_id": bot_id,
                                 "data_status": data_status,
                                 "had_state": has_state,
                                 "previous_desired_running": previous_desired,
                                 "ghost_cleanup": True,
                             },
                             result="success", reason=reason or "ghost_cleanup")
            return DcaActionResponse(
                bot_id=bot_id, action="delete", status="deleted",
                message=(
                    f"DCA {bot_id} 配置文件已不存在，已清理殘留登記（心跳狀態: {data_status}）；"
                    "未變更任何持倉或掛單"
                ),
            )
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
            RedisCommandQueue().publish("STOP", "dca", config_path)
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


# ────────────────────────────────────────────────────────
# POST /api/v1/dca/backtest — 單一設定回放（§4.6）
# ────────────────────────────────────────────────────────
@router.post("/backtest", response_model=BacktestResponse)
async def run_dca_backtest(
    payload: BacktestRequest,
    source: str = Query(default="dashboard", description="來源"),
    _token: str = Depends(verify_token),
):
    """
    回測：對「既有 DCA 設定」或「建立精靈中尚未送出的參數」跑歷史回放（§4.6）。
    
    BP-1：回測路徑必須實例化真正的 DcaCoordinator 與真正的 DcaOrderExecutor
    BP-2：回測請求處理期間，交易所寫入方法呼叫次數必須為 0；config/active_dca.json 內容不得變動
    A-4：上限守衛（bars ≤ 20000, timeframe ∈ {1m,5m,15m,30m,1h,4h,1d}）
    A-5：執行時間守衛（wall-clock 上限 60s）
    A-6：warnings 必含簡化事項揭露
    A-7：稽核日誌
    """
    project_root = _get_project_root()
    audit_logger = AuditLogger.get_instance(project_root)
    start_time = time.time()
    
    # A-4 上限守衛
    if payload.bars > 20000:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="回測棒數上限為 20000，請調整參數",
        )
    
    valid_timeframes = {"1m", "5m", "15m", "30m", "1h", "4h", "1d"}
    if payload.timeframe not in valid_timeframes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不支援的 timeframe '{payload.timeframe}'。僅支援 {valid_timeframes}",
        )
    
    # 取得 DCA 設定
    config = None
    bot_id = None
    exchange = None
    symbol = None
    
    if payload.bot_id:
        # 讀既有 YAML
        bot_id = payload.bot_id
        _validate_bot_id(bot_id)
        config_path = DcaYAMLGenerator.path_from_bot_id(bot_id)
        yaml_data = _read_dca_yaml(config_path)
        if yaml_data is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"DCA 配置 {bot_id} 不存在",
            )
        config = load_dca_config(yaml_data)
        exchange = str(yaml_data.get("dca", {}).get("exchange")) if yaml_data.get("dca") else None
        symbol = str(yaml_data.get("dca", {}).get("symbol")) if yaml_data.get("dca") else None
        
    elif payload.draft:
        # 使用精靈參數（走與建立機器人同一套驗證函式）
        draft_params = payload.draft
        errors = _generator.validate_params(draft_params)
        if errors:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"message": "draft 參數驗證失敗", "errors": errors},
            )
        config = _generator.build_config(draft_params)
        exchange = str(draft_params.get("exchange"))
        symbol = str(draft_params.get("symbol"))
        bot_id = "draft"
        
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="必須提供 bot_id 或 draft 其中一個",
        )
    
    if exchange is None or symbol is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="無法取得交易所或交易對符號",
        )
    
    # 取得 K 線資料
    account_mode = (
        dca_account_mode_from_yaml(yaml_data)
        if yaml_data
        else (draft_params.get("account_mode") if isinstance(draft_params, dict) else "paper")
    )
    kline_service = KlineService.instance()
    kline_result = await kline_service.get_candles(
        exchange=exchange,
        symbol=symbol,
        timeframe=payload.timeframe,
        limit=payload.bars,
        account_mode=account_mode,
    )
    
    if not kline_result.candles:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"無法取得足夠的 K 線資料（取得 {len(kline_result.candles)} 棒）",
        )
    
    # 取得初始資本
    from core.services.dca.ladder import required_capital
    initial_capital_decimal = None
    if payload.initial_capital:
        try:
            initial_capital_decimal = Decimal(payload.initial_capital)
        except Exception:
            initial_capital_decimal = None
    
    if initial_capital_decimal is None:
        # 使用 required_capital
        try:
            first_candle = kline_result.candles[0]
            entry_price = Decimal(str(first_candle.close))
            initial_capital_decimal = required_capital(config, entry_price)
        except Exception:
            initial_capital_decimal = Decimal("1000")
    
    # 轉換 K 線資料格式
    from core.services.dca.backtest.candles import to_candles
    candles = to_candles(kline_result.candles)
    
    # A-5 執行時間守衛：直接 await ReplayDriver.run()，使用 asyncio.wait_for
    try:
        driver = ReplayDriver(
            config=config,
            candles=candles,
            initial_capital=str(initial_capital_decimal),
            timeframe=payload.timeframe,
            source=kline_result.source,
            data_status=kline_result.data_status,
            bot_id=bot_id,
        )
        result = await asyncio.wait_for(driver.run(), timeout=60)
    except asyncio.TimeoutError:
        audit_logger.log(source=source, action="run_dca_backtest", grid_id=bot_id,
                         params={"bot_id": bot_id, "bars": payload.bars, "timeframe": payload.timeframe},
                         result="error", error_msg="回測執行逾時（>60秒）")
        raise HTTPException(
            status_code=status.HTTP_408_REQUEST_TIMEOUT,
            detail="回測執行逾時，請減少棒數或使用更大的時框",
        )
    except Exception as e:
        audit_logger.log(source=source, action="run_dca_backtest", grid_id=bot_id,
                         params={"bot_id": bot_id, "bars": payload.bars, "timeframe": payload.timeframe},
                         result="error", error_msg=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"回測執行失敗: {str(e)}",
        )
    
    # 記錄執行時間
    elapsed = time.time() - start_time
    
    # 確保 warnings 包含簡化事項揭露（A-6 / D-11）
    warnings = list(result.warnings)
    required_warnings = [
        "回測不模擬部分成交",
        "未套用交易所最小名目限制",
        "歷史績效不代表未來",
    ]
    for req_warning in required_warnings:
        if req_warning not in warnings:
            warnings.append(req_warning)
    
    if kline_result.data_status == "paper" or kline_result.source == "paper_synthetic":
        if "使用合成 K 線，非真實歷史" not in warnings:
            warnings.append("使用合成 K 線，非真實歷史")
    
    # 記錄稽核日誌（A-7）
    audit_logger.log(source=source, action="run_dca_backtest", grid_id=bot_id,
                     params={"bot_id": bot_id, "bars": payload.bars, "timeframe": payload.timeframe, "elapsed_seconds": round(elapsed, 2)},
                     result="success")
    
    # 建立回應（轉換 core BacktestResult 到 API BacktestResult）。
    # 注意：schema 類別一律由模組頂端的 `web_ui.backend.schemas` 匯入。
    # 此處若改用函式內 `from schemas import ...`，會建立與 response_model
    # 不同的類別物件，pydantic 對每一筆 equity/marks/cycles 回報 model_type
    # 驗證錯誤而導致整個端點 500（與 /chart 端點同一類缺陷）。
    backtest_response = BacktestResult(
        bars=result.bars,
        timeframe=result.timeframe,
        first_ts=result.first_ts,
        last_ts=result.last_ts,
        metrics=BacktestMetrics(**result.metrics),
        equity=[
            BacktestEquityPoint(**ep.to_dict()) for ep in result.equity
        ],
        marks=[
            BacktestTradeMark(**mark.to_dict()) for mark in result.marks
        ],
        cycles=[
            BacktestCycleRecord(**cycle.to_dict()) for cycle in result.cycles
        ],
        data_status=result.data_status,
        source=result.source,
        warnings=warnings,
        bars_per_second=result.bars_per_second,
    )
    
    return BacktestResponse(result=backtest_response)


# ────────────────────────────────────────────────────────
# POST /api/v1/dca/backtest/sweep — 參數掃描（§4.6）
# ────────────────────────────────────────────────────────
@router.post("/backtest/sweep", response_model=SweepResponse)
async def run_dca_sweep(
    payload: SweepRequest,
    source: str = Query(default="dashboard", description="來源"),
    _token: str = Depends(verify_token),
):
    """
    參數掃描：對多個參數組合跑回測（§4.6）。
    
    BP-1：回測路徑必須實例化真正的 DcaCoordinator 與真正的 DcaOrderExecutor
    BP-2：回測請求處理期間，交易所寫入方法呼叫次數必須為 0；config/active_dca.json 內容不得變動
    A-4：上限守衛
    A-6：warnings 必含簡化事項揭露
    A-7：稽核日誌
    """
    project_root = _get_project_root()
    audit_logger = AuditLogger.get_instance(project_root)
    start_time = time.time()
    
    # A-4 上限守衛
    if payload.bars > 20000:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="回測棒數上限為 20000，請調整參數",
        )
    
    valid_timeframes = {"1m", "5m", "15m", "30m", "1h", "4h", "1d"}
    if payload.timeframe not in valid_timeframes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不支援的 timeframe '{payload.timeframe}'。僅支援 {valid_timeframes}",
        )
    
    # 取得基礎 DCA 設定
    base_config = None
    bot_id = None
    exchange = None
    symbol = None
    
    if payload.bot_id:
        # 讀既有 YAML
        bot_id = payload.bot_id
        _validate_bot_id(bot_id)
        config_path = DcaYAMLGenerator.path_from_bot_id(bot_id)
        yaml_data = _read_dca_yaml(config_path)
        if yaml_data is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"DCA 配置 {bot_id} 不存在",
            )
        base_config = load_dca_config(yaml_data)
        exchange = str(yaml_data.get("dca", {}).get("exchange")) if yaml_data.get("dca") else None
        symbol = str(yaml_data.get("dca", {}).get("symbol")) if yaml_data.get("dca") else None
        
    elif payload.draft:
        # 使用精靈參數（走與建立機器人同一套驗證函式）
        draft_params = payload.draft
        errors = _generator.validate_params(draft_params)
        if errors:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"message": "draft 參數驗證失敗", "errors": errors},
            )
        base_config = _generator.build_config(draft_params)
        exchange = str(draft_params.get("exchange"))
        symbol = str(draft_params.get("symbol"))
        bot_id = "draft"
        
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="必須提供 bot_id 或 draft 其中一個",
        )
    
    if exchange is None or symbol is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="無法取得交易所或交易對符號",
        )
    
    # 取得 K 線資料
    account_mode = (
        dca_account_mode_from_yaml(yaml_data)
        if yaml_data
        else (draft_params.get("account_mode") if isinstance(draft_params, dict) else "paper")
    )
    kline_service = KlineService.instance()
    kline_result = await kline_service.get_candles(
        exchange=exchange,
        symbol=symbol,
        timeframe=payload.timeframe,
        limit=payload.bars,
        account_mode=account_mode,
    )
    
    if not kline_result.candles:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"無法取得足夠的 K 線資料（取得 {len(kline_result.candles)} 棒）",
        )
    
    # 取得初始資本
    from core.services.dca.ladder import required_capital
    initial_capital_decimal = None
    if payload.initial_capital:
        try:
            initial_capital_decimal = Decimal(payload.initial_capital)
        except Exception:
            initial_capital_decimal = None
    
    if initial_capital_decimal is None:
        try:
            first_candle = kline_result.candles[0]
            entry_price = Decimal(str(first_candle.close))
            initial_capital_decimal = required_capital(base_config, entry_price)
        except Exception:
            initial_capital_decimal = Decimal("1000")
    
    # 轉換 K 線資料格式
    from core.services.dca.backtest.candles import to_candles
    candles = to_candles(kline_result.candles)
    
    # 准備掃描參數 axes（run_sweep 期望的格式）
    from core.services.dca.backtest.sweep import run_sweep
    
    candles = to_candles(kline_result.candles)
    
    # 准備掃描參數 axes（run_sweep 期望的格式）
    sweep_axes = {}
    if payload.base_order_amount_range:
        sweep_axes["base_order_amount"] = payload.base_order_amount_range
    if payload.price_deviation_percent_range:
        sweep_axes["price_deviation_percent"] = payload.price_deviation_percent_range
    if payload.take_profit_percent_range:
        sweep_axes["take_profit_percent"] = payload.take_profit_percent_range
    if payload.max_safety_orders_range:
        sweep_axes["max_safety_orders"] = payload.max_safety_orders_range
    
    if not sweep_axes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="至少需要指定一個掃描參數範圍",
        )
    
    # A-5 執行時間守衛：run_sweep 是同步函式，使用 ThreadPoolExecutor
    import concurrent.futures
    
    def run_sweep_sync():
        """同步執行掃描。"""
        try:
            return run_sweep(
                base_config=base_config,
                candles=candles,
                axes=sweep_axes,
                initial_capital=str(initial_capital_decimal),
                timeframe=payload.timeframe,
                max_workers=payload.max_workers,
                max_total_bars=payload.max_total_bars,
                best_metric=payload.best_by,
            )
        except Exception as e:
            logger.error(f"掃描執行失敗: {e}")
            raise
    
    try:
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(run_sweep_sync)
            
            # 等待最多 120 秒
            try:
                sweep_result = future.result(timeout=120)
            except concurrent.futures.TimeoutError:
                future.cancel()
                audit_logger.log(source=source, action="run_dca_sweep", grid_id=bot_id,
                                 params={"bot_id": bot_id, "bars": payload.bars, "timeframe": payload.timeframe},
                                 result="error", error_msg="掃描執行逾時（>120秒）")
                raise HTTPException(
                    status_code=status.HTTP_408_REQUEST_TIMEOUT,
                    detail="掃描執行逾時，請減少參數組合數或棒數",
                )
            except Exception as e:
                audit_logger.log(source=source, action="run_dca_sweep", grid_id=bot_id,
                                 params={"bot_id": bot_id, "bars": payload.bars, "timeframe": payload.timeframe},
                                 result="error", error_msg=str(e))
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"掃描執行失敗: {str(e)}",
                )
    except Exception as e:
        audit_logger.log(source=source, action="run_dca_sweep", grid_id=bot_id,
                         params={"bot_id": bot_id, "bars": payload.bars, "timeframe": payload.timeframe},
                         result="error", error_msg=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"掃描執行失敗: {str(e)}",
        )
    
    # 記錄執行時間
    elapsed = time.time() - start_time
    
    # 記錄稽核日誌（A-7）
    audit_logger.log(source=source, action="run_dca_sweep", grid_id=bot_id,
                     params={"bot_id": bot_id, "bars": payload.bars, "timeframe": payload.timeframe, "elapsed_seconds": round(elapsed, 2)},
                     result="success")
    
    # 建立回應
    # schema 類別一律由模組頂端匯入；函式內 `from schemas import ...` 會建立
    # 與 response_model 不同的類別物件而導致 pydantic model_type 500。
    
    runs = []
    for run in sweep_result.runs:
        if hasattr(run, 'to_dict'):
            run_dict = run.to_dict()
        else:
            run_dict = {
                "params": run.params,
                "metrics": run.metrics,
                "error": run.error,
            }
        
        # 確保 metrics 有值
        metrics_dict = run_dict.get("metrics", {})
        if not metrics_dict:
            metrics_dict = {}
        
        runs.append(SweepRunResult(
            params=run_dict.get("params", {}),
            metrics=BacktestMetrics(**metrics_dict) if metrics_dict else BacktestMetrics(),
            error=run_dict.get("error"),
        ))
    
    budget = SweepBudget(
        bars_used=sweep_result.budget.bars_used,
        bars_limit=sweep_result.budget.bars_limit,
        runs_completed=sweep_result.budget.runs_completed,
        runs_total=sweep_result.budget.runs_total,
        runs_limit=sweep_result.budget.runs_limit,
    )
    
    # 最優組合由 core 的 _pick_best 判定並原樣透出，前端不得自行排序挑選（G-4）。
    best_raw = sweep_result.best_by if isinstance(sweep_result.best_by, Mapping) else {}
    best = SweepBest(
        metric=str(best_raw.get("metric") or payload.best_by),
        value=best_raw.get("value"),
        params=best_raw.get("params"),
    )

    return SweepResponse(
        runs=runs,
        best_by=payload.best_by,
        best=best,
        truncated=sweep_result.truncated,
        budget=budget,
    )


def _prune_params(params: Mapping[str, Any]) -> dict:
    """審計時移除可能包含秘密的控制鍵（稽核 4.18）。"""
    return {
        k: v for k, v in params.items()
        if k not in {"api_key", "api_secret", "secret", "token", "entry_price", "Idempotency-Key"}
    }
