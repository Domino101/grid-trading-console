"""
web_ui/backend/grid_backtest_service.py —— 網格完整回測服務

把 API 請求轉成 core 資金感知模擬器的輸入，取得歷史 K 線，執行回測 / 壓力 / 掃描。
全程唯讀：不下單、不寫 config、不使用交易所私鑰。
"""

from __future__ import annotations

import os
import sys
from decimal import Decimal
from typing import Any, Dict, List, Optional

_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from web_ui.backend.kline_repository import TIMEFRAME_SECONDS
from web_ui.backend.kline_service import KlineService
from core.services.grid.backtest.allocation import allocate_capital, reference_price
from core.services.grid.backtest.models import GridBacktestSpec
from core.services.grid.backtest.simulator import GridBacktestSimulator
from core.services.grid.backtest.stress import run_stress

DEFAULT_FEE_RATE = Decimal("0.0002")
DEFAULT_MAINTENANCE_MARGIN_RATE = Decimal("0.005")
DEFAULT_TIMEFRAME = "1h"
DEFAULT_BARS = 720


def _require_timeframe(timeframe: str) -> int:
    tf = str(timeframe).lower()
    if tf not in TIMEFRAME_SECONDS:
        raise ValueError(f"不支援的 timeframe '{tf}'。僅支援 {sorted(TIMEFRAME_SECONDS.keys())}")
    return TIMEFRAME_SECONDS[tf]


def _resolve_grid_count(
    grid_count: Optional[int],
    grid_interval: Optional[float],
    lower_price: Decimal,
    upper_price: Decimal,
) -> int:
    if grid_count is not None and grid_count >= 2:
        return int(grid_count)
    if grid_interval is not None and grid_interval > 0:
        return max(2, int(round(float(upper_price - lower_price) / float(grid_interval))))
    raise ValueError("必須提供 grid_count 或 grid_interval 其中一個")


def resolve_bars(
    *,
    timeframe: str,
    bars: Optional[int],
    start_ts: Optional[int],
    end_ts: Optional[int],
) -> int:
    step = _require_timeframe(timeframe)
    if bars is not None:
        return max(10, min(int(bars), 20000))
    if start_ts is not None and end_ts is not None:
        if end_ts <= start_ts:
            raise ValueError("end_ts 必須大於 start_ts")
        limit = int((end_ts - start_ts) // step)
        return max(10, min(limit, 20000))
    return DEFAULT_BARS


def build_spec_from_request(payload: Dict[str, Any]) -> GridBacktestSpec:
    """將 API 請求 payload 轉為 GridBacktestSpec（含 allocation 計算後的 order_amount）。"""
    exchange = str(payload.get("exchange") or "").lower()
    symbol = str(payload.get("symbol") or "").strip()
    grid_type = str(payload.get("grid_type") or "neutral").lower()
    lower = Decimal(str(payload.get("lower_price")))
    upper = Decimal(str(payload.get("upper_price")))
    grid_count = _resolve_grid_count(
        payload.get("grid_count"),
        payload.get("grid_interval"),
        lower,
        upper,
    )

    per_grid_notional = (
        Decimal(str(payload["per_grid_notional"]))
        if payload.get("per_grid_notional") is not None
        else None
    )
    per_grid_capital_percent = (
        Decimal(str(payload["per_grid_capital_percent"]))
        if payload.get("per_grid_capital_percent") is not None
        else None
    )
    if per_grid_notional is None and per_grid_capital_percent is None:
        raise ValueError("必須提供 per_grid_notional 或 per_grid_capital_percent 其中一個")

    initial_capital = Decimal(str(payload.get("initial_capital")))
    leverage = max(1, int(payload.get("leverage") or 10))
    fee_rate = (
        Decimal(str(payload["fee_rate"]))
        if payload.get("fee_rate") is not None
        else DEFAULT_FEE_RATE
    )
    mm_rate = (
        Decimal(str(payload["maintenance_margin_rate"]))
        if payload.get("maintenance_margin_rate") is not None
        else DEFAULT_MAINTENANCE_MARGIN_RATE
    )
    timeframe = str(payload.get("timeframe") or DEFAULT_TIMEFRAME)
    if timeframe not in TIMEFRAME_SECONDS:
        raise ValueError(f"不支援的 timeframe '{timeframe}'")

    allocation = allocate_capital(
        initial_capital=initial_capital,
        grid_count=grid_count,
        lower_price=lower,
        upper_price=upper,
        per_grid_notional=per_grid_notional,
        per_grid_capital_percent=per_grid_capital_percent,
        leverage=leverage,
    )

    return GridBacktestSpec(
        symbol=symbol,
        exchange=exchange,
        lower_price=lower,
        upper_price=upper,
        grid_count=grid_count,
        order_amount=allocation.order_amount,
        grid_type=grid_type,
        account_mode=str(payload.get("account_mode") or "unknown"),
        fee_rate=fee_rate,
        leverage=leverage,
        margin_mode=str(payload.get("margin_mode") or "isolated"),
        maintenance_margin_rate=mm_rate,
        initial_capital=initial_capital,
        timeframe=timeframe,
    )


async def run_grid_backtest(payload: Dict[str, Any]) -> Dict[str, Any]:
    """單次網格回測。"""
    spec = build_spec_from_request(payload)
    timeframe = spec.timeframe
    limit = resolve_bars(
        timeframe=timeframe,
        bars=payload.get("bars"),
        start_ts=payload.get("start_ts"),
        end_ts=payload.get("end_ts"),
    )
    kline_result = await KlineService.instance().get_candles(
        exchange=spec.exchange,
        symbol=spec.symbol,
        timeframe=timeframe,
        limit=limit,
        until=payload.get("end_ts"),
    )
    if not kline_result.candles:
        raise ValueError(
            f"無法取得足夠歷史 K 線（{spec.exchange}:{spec.symbol} {timeframe}，"
            f"取得 {len(kline_result.candles)} 棒）"
        )

    allocation = allocate_capital(
        initial_capital=spec.initial_capital,
        grid_count=spec.grid_count,
        lower_price=spec.lower_price,
        upper_price=spec.upper_price,
        per_grid_notional=spec.order_amount * reference_price(spec.lower_price, spec.upper_price),
        leverage=spec.leverage,
    )
    # 重建一個帶既有 allocation order_amount 的 spec
    spec = GridBacktestSpec(
        symbol=spec.symbol,
        exchange=spec.exchange,
        lower_price=spec.lower_price,
        upper_price=spec.upper_price,
        grid_count=spec.grid_count,
        order_amount=allocation.order_amount,
        grid_type=spec.grid_type,
        account_mode=spec.account_mode,
        fee_rate=spec.fee_rate,
        leverage=spec.leverage,
        margin_mode=spec.margin_mode,
        maintenance_margin_rate=spec.maintenance_margin_rate,
        initial_capital=spec.initial_capital,
        timeframe=spec.timeframe,
    )

    result = GridBacktestSimulator(
        spec=spec,
        candles=kline_result.candles,
        allocation=allocation,
        source=kline_result.source,
        data_status=kline_result.data_status,
    ).run()
    out = result.to_dict()
    out["warnings"] = list(dict.fromkeys([*result.warnings, *kline_result.warnings]))
    return out


async def run_grid_stress(payload: Dict[str, Any]) -> Dict[str, Any]:
    """壓力情境掃描。"""
    spec = build_spec_from_request(payload)
    limit = resolve_bars(
        timeframe=spec.timeframe,
        bars=payload.get("bars"),
        start_ts=payload.get("start_ts"),
        end_ts=payload.get("end_ts"),
    )
    kline_result = await KlineService.instance().get_candles(
        exchange=spec.exchange,
        symbol=spec.symbol,
        timeframe=spec.timeframe,
        limit=limit,
        until=payload.get("end_ts"),
    )
    if not kline_result.candles:
        raise ValueError("無法取得足夠歷史 K 線執行壓力情境")
    allocation = allocate_capital(
        initial_capital=spec.initial_capital,
        grid_count=spec.grid_count,
        lower_price=spec.lower_price,
        upper_price=spec.upper_price,
        per_grid_notional=spec.order_amount * reference_price(spec.lower_price, spec.upper_price),
        leverage=spec.leverage,
    )
    spec = GridBacktestSpec(
        symbol=spec.symbol,
        exchange=spec.exchange,
        lower_price=spec.lower_price,
        upper_price=spec.upper_price,
        grid_count=spec.grid_count,
        order_amount=allocation.order_amount,
        grid_type=spec.grid_type,
        account_mode=spec.account_mode,
        fee_rate=spec.fee_rate,
        leverage=spec.leverage,
        margin_mode=spec.margin_mode,
        maintenance_margin_rate=spec.maintenance_margin_rate,
        initial_capital=spec.initial_capital,
        timeframe=spec.timeframe,
    )
    stress = run_stress(
        spec,
        kline_result.candles,
        allocation=allocation,
        source="stress",
        data_status=kline_result.data_status,
    )
    stress["warnings"] = list(dict.fromkeys([*stress["warnings"], *kline_result.warnings]))
    return stress


async def run_grid_sweep(payload: Dict[str, Any]) -> Dict[str, Any]:
    """參數掃描（v1：掃 grid_count / per_grid_notional / leverage）。"""
    spec = build_spec_from_request(payload)
    limit = resolve_bars(
        timeframe=spec.timeframe,
        bars=payload.get("bars"),
        start_ts=payload.get("start_ts"),
        end_ts=payload.get("end_ts"),
    )
    kline_result = await KlineService.instance().get_candles(
        exchange=spec.exchange,
        symbol=spec.symbol,
        timeframe=spec.timeframe,
        limit=limit,
        until=payload.get("end_ts"),
    )
    if not kline_result.candles:
        raise ValueError("無法取得足夠歷史 K 線執行參數掃描")

    grid_counts = payload.get("sweep_grid_counts") or [spec.grid_count]
    notional_values = payload.get("sweep_per_grid_notional") or [
        float(spec.initial_capital) / max(1, spec.grid_count)
    ]
    leverages = payload.get("sweep_leverages") or [spec.leverage]

    runs: List[Dict[str, Any]] = []
    warnings: List[str] = []
    total_bars = 0
    budget_limit = int(payload.get("max_total_bars") or 300000)
    runs_limit = int(payload.get("runs_limit") or 500)
    for gc in grid_counts:
        for notional in notional_values:
            for lev in leverages:
                if total_bars + limit > budget_limit or len(runs) >= runs_limit:
                    warnings.append("掃描達預算限制，結果僅為已執行組合")
                    break
                try:
                    alloc = allocate_capital(
                        initial_capital=spec.initial_capital,
                        grid_count=int(gc),
                        lower_price=spec.lower_price,
                        upper_price=spec.upper_price,
                        per_grid_notional=Decimal(str(notional)),
                        leverage=int(lev),
                    )
                    sub_spec = GridBacktestSpec(
                        symbol=spec.symbol,
                        exchange=spec.exchange,
                        lower_price=spec.lower_price,
                        upper_price=spec.upper_price,
                        grid_count=int(gc),
                        order_amount=alloc.order_amount,
                        grid_type=spec.grid_type,
                        account_mode=spec.account_mode,
                        fee_rate=spec.fee_rate,
                        leverage=int(lev),
                        margin_mode=spec.margin_mode,
                        maintenance_margin_rate=spec.maintenance_margin_rate,
                        initial_capital=spec.initial_capital,
                        timeframe=spec.timeframe,
                    )
                    result = GridBacktestSimulator(
                        spec=sub_spec,
                        candles=kline_result.candles,
                        allocation=alloc,
                        source=kline_result.source,
                        data_status=kline_result.data_status,
                    ).run()
                    runs.append({
                        "params": {"grid_count": int(gc), "per_grid_notional": float(notional), "leverage": int(lev)},
                        "metrics": result.metrics,
                        "risk": result.risk.to_dict(),
                        "error": None,
                    })
                    total_bars += limit
                except Exception as exc:  # pragma: no cover - defensive
                    runs.append({
                        "params": {"grid_count": int(gc), "per_grid_notional": float(notional), "leverage": int(lev)},
                        "metrics": {},
                        "risk": {},
                        "error": str(exc),
                    })

    best: Optional[Dict[str, Any]] = None
    completed = [r for r in runs if r["metrics"].get("final_equity") is not None]
    if completed:
        def key(r: Dict[str, Any]) -> float:
            m = r["metrics"]
            return float(m.get("total_return_pct") or 0.0)
        best = max(completed, key=key)

    return {
        "runs": runs,
        "budget": {"bars_used": total_bars, "bars_limit": budget_limit, "runs_completed": len(completed), "runs_total": int(len(grid_counts) * len(notional_values) * len(leverages)), "runs_limit": runs_limit},
        "best": best,
        "warnings": list(dict.fromkeys([*warnings, *kline_result.warnings])),
    }
