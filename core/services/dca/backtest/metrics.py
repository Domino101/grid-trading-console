"""
core/services/dca/backtest/metrics.py —— DCA 回測績效指標（純函式）

依據：docs/dca_chart_backtest_plan_zh-TW.md §4.5 凍結定義與 Phase C4 稽核 4.1–4.6

約束：
- 純函式、零 I/O、零牆鐘時間戳、零交易所呼叫
- realized_pnl / total_fees 必須由呼叫端傳入引擎會計值（build_statistics），
  本模組絕不加總 fills[].fee（dual-stage M-1 / 稽核 4.5）
- 未知數值一律 None（禁止以 0 代替，G-5）：無輪次時 win_rate / avg_cycle_bars /
  mfe_pct / mae_pct 為 None；profit_factor 在虧損輪總虧損為 0 時為 None（不是 inf）
- 不重寫階梯 / 均價 / 止盈 / 指標數學（G-13）
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Sequence, Union

from .candles import Candle
from .models import CycleRecord, EquityPoint

_WIN_EXIT = "take_profit"
_LOSS_EXITS = frozenset({"stop_loss", "max_cycles", "end_of_data"})
_COMPLETED_EXITS = frozenset({_WIN_EXIT, *_LOSS_EXITS})


def _as_direction(direction: Any) -> str:
    if hasattr(direction, "value"):
        return str(direction.value)
    return str(direction or "long")


def _close_of(candle: Union[Candle, Any]) -> float:
    return float(candle.close)


def buy_hold_return_pct(
    first_close: float,
    last_close: float,
    direction: str = "long",
) -> float:
    """買入持有基準報酬%。做空為反向基準（§4.5 / 稽核 4.2）。"""
    if first_close == 0:
        return 0.0
    raw = (last_close - first_close) / first_close * 100.0
    if _as_direction(direction) == "short":
        return -raw
    return raw


def max_drawdown_pct(equity_values: Sequence[float]) -> float:
    """
    權益曲線最大回撤%：歷史峰值 → 其後谷底，不得用首尾兩點（稽核 4.4）。

    例：``[100, 120, 90, 110]`` → 25.0（120 → 90）。
    """
    if not equity_values:
        return 0.0
    peak = float(equity_values[0])
    worst = 0.0
    for value in equity_values:
        current = float(value)
        if current > peak:
            peak = current
        if peak > 0:
            drawdown = (peak - current) / peak * 100.0
            if drawdown > worst:
                worst = drawdown
    return worst


def compute_metrics(
    *,
    equity: Sequence[EquityPoint],
    cycles: Sequence[CycleRecord],
    candles: Sequence[Any],
    initial_capital: float,
    realized_pnl: float,
    total_fees: float,
    cycles_completed: int,
    holding_bars: int,
    direction: str = "long",
    bars_per_second: Optional[float] = None,
) -> Dict[str, Any]:
    """
    計算 §4.5 全部績效指標。

    ``realized_pnl`` / ``total_fees`` 必須是引擎 ``build_statistics()`` 的會計值，
    呼叫端不得傳入 fills[].fee 加總。
    """
    initial = float(initial_capital)
    if initial <= 0:
        raise ValueError(f"initial_capital 必須 > 0，收到: {initial}")

    final_eq = float(equity[-1].equity) if equity else initial
    total_return = (final_eq - initial) / initial * 100.0

    if candles:
        first_close = _close_of(candles[0])
        last_close = _close_of(candles[-1])
    else:
        first_close = 0.0
        last_close = 0.0
    bh = buy_hold_return_pct(first_close, last_close, direction)
    alpha = total_return - bh

    # 未實現 = 權益恆等式反推，與 driver 的 equity 口徑一致
    unrealized = final_eq - initial - float(realized_pnl) + float(total_fees)

    completed = [c for c in cycles if c.exit_reason in _COMPLETED_EXITS]
    completed_count = len(completed)
    wins = [c for c in completed if c.exit_reason == _WIN_EXIT]

    win_rate: Optional[float]
    if completed_count > 0:
        win_rate = round(len(wins) / completed_count * 100.0, 2)
    else:
        win_rate = None

    gross_profit = sum(
        float(c.realized_pnl) for c in completed
        if c.realized_pnl is not None and c.realized_pnl > 0
    )
    gross_loss = sum(
        abs(float(c.realized_pnl)) for c in completed
        if c.realized_pnl is not None and c.realized_pnl < 0
    )
    if gross_loss > 0:
        profit_factor: Optional[float] = round(gross_profit / gross_loss, 4)
    else:
        profit_factor = None

    mdd = round(max_drawdown_pct([pt.equity for pt in equity]), 4)

    total_bars = len(candles)
    exposure = round(holding_bars / total_bars * 100.0, 2) if total_bars > 0 else 0.0

    avg_cycle_bars: Optional[float]
    if completed_count > 0:
        avg_cycle_bars = round(sum(c.bars for c in completed) / completed_count, 2)
    else:
        avg_cycle_bars = None

    mfe_list = [c.mfe_pct for c in completed if c.mfe_pct is not None]
    mae_list = [c.mae_pct for c in completed if c.mae_pct is not None]
    avg_mfe = round(sum(mfe_list) / len(mfe_list), 4) if mfe_list else None
    avg_mae = round(sum(mae_list) / len(mae_list), 4) if mae_list else None

    return {
        "initial_capital": initial,
        "final_equity": round(final_eq, 4),
        "total_return_pct": round(total_return, 4),
        "buy_hold_return_pct": round(bh, 4),
        "alpha_pct": round(alpha, 4),
        "realized_pnl": round(float(realized_pnl), 8),
        "total_fees": round(float(total_fees), 8),
        "unrealized_pnl": round(unrealized, 8),
        "cycles_completed": int(cycles_completed),
        "win_rate_pct": win_rate,
        "max_drawdown_pct": mdd,
        "exposure_pct": exposure,
        "avg_cycle_bars": avg_cycle_bars,
        "mfe_pct": avg_mfe,
        "mae_pct": avg_mae,
        "profit_factor": profit_factor,
        "bars_per_second": bars_per_second,
    }
