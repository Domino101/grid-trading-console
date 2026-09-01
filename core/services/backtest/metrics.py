"""
core/services/backtest/metrics.py —— 跨策略共同績效指標（純函式、零 I/O）

設計目的：
- 讓「網格回測」與既有的 DCA 回測共用同一套總報酬 / 買入持有 / Alpha / 最大回撤 /
  勝率 / 獲利因子計算，避免兩套指標定義漂移。
- 僅使用 Python 標準型別，不依賴 FastAPI / ccxt / injector，可在無外部套件的
  環境中直接單元測試。

約束：
- 全數值以標準型別（int / float / str / Optional）序列化輸出。
- 未知數值一律 None（G-5；禁止以 0 冒充未知）。
- 純函式：不讀檔案、不呼叫交易所、不使用牆鐘時間。
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Sequence, Union


def _direction(direction: Optional[str]) -> str:
    raw = str(direction or "long").lower()
    return "short" if raw in ("short", "sell") else "long"


def buy_hold_return_pct(
    first_close: float,
    last_close: float,
    direction: str = "long",
) -> float:
    """買入持有基準報酬%。

    做多 / 中性網格：``(last - first) / first * 100``。
    做空網格：以反向走勢為基準，``-raw``。
    資料不足或 first=0 時回傳 0.0。
    """
    if not first_close or first_close == 0:
        return 0.0
    raw = (float(last_close) - float(first_close)) / float(first_close) * 100.0
    if _direction(direction) == "short":
        return -raw
    return raw


def max_drawdown_pct(equity_values: Sequence[Union[int, float, str]]) -> float:
    """最大回撤%。

    peak → 其後谷底的最大百分比跌幅，不得只用首尾兩點。
    ``[]`` 回傳 0.0。
    """
    values = [float(v) for v in equity_values if v is not None]
    if not values:
        return 0.0
    peak = values[0]
    worst = 0.0
    for value in values:
        current = float(value)
        if current > peak:
            peak = current
        if peak > 0:
            drawdown = (peak - current) / peak * 100.0
            if drawdown > worst:
                worst = drawdown
    return worst


def min_value(values: Sequence[Optional[float]]) -> Optional[float]:
    """序列最小值；無值或僅有 None 時回傳 None（G-5）。"""
    vals = [float(v) for v in values if v is not None]
    return min(vals) if vals else None


def max_value(values: Sequence[Optional[float]]) -> Optional[float]:
    """序列最大值；無值或僅有 None 時回傳 None（G-5）。"""
    vals = [float(v) for v in values if v is not None]
    return max(vals) if vals else None


def compute_metrics_from_equity(
    *,
    initial_capital: float,
    final_equity: float,
    first_close: float,
    last_close: float,
    direction: str = "long",
    realized_pnl: float,
    total_fees: float,
    cycles_completed: int,
    equity_values: Sequence[Union[int, float, str]],
    position_values: Sequence[Union[int, float, str]],
    completed_cycle_pnls: Sequence[float],
    total_bars: int,
    holding_bars: int,
) -> Dict[str, Any]:
    """由已知會計值產生通用績效指標字典。

    ``realized_pnl`` / ``total_fees`` 必須由模擬器會計值傳入，本函式不加總手續費。
    """
    initial = float(initial_capital)
    if initial <= 0:
        raise ValueError(f"initial_capital 必須 > 0，收到: {initial}")

    final = float(final_equity)
    total_return = (final - initial) / initial * 100.0 if initial else 0.0
    bh = buy_hold_return_pct(first_close, last_close, direction)
    alpha = total_return - bh
    mdd = max_drawdown_pct(equity_values)

    completed_pnls = [float(p) for p in completed_cycle_pnls]
    wins = [p for p in completed_pnls if p > 0]
    losses = [abs(p) for p in completed_pnls if p < 0]

    win_rate: Optional[float]
    if completed_pnls:
        win_rate = round(len(wins) / len(completed_pnls) * 100.0, 2)
    else:
        win_rate = None

    gross_profit = sum(wins)
    gross_loss = sum(losses)
    profit_factor: Optional[float]
    if gross_loss > 0:
        profit_factor = round(gross_profit / gross_loss, 4)
    else:
        profit_factor = None

    holding = [float(v) for v in position_values if v is not None]
    exposed_count = sum(1 for v in holding if abs(v) > 1e-12)
    exposure = round(exposed_count / total_bars * 100.0, 2) if total_bars > 0 else 0.0

    return {
        "initial_capital": round(initial, 4),
        "final_equity": round(final, 6),
        "total_return_pct": round(total_return, 4),
        "buy_hold_return_pct": round(bh, 4),
        "alpha_pct": round(alpha, 4),
        "realized_pnl": round(float(realized_pnl), 8),
        "total_fees": round(float(total_fees), 8),
        "cycles_completed": int(cycles_completed),
        "win_rate_pct": win_rate,
        "max_drawdown_pct": round(mdd, 4),
        "exposure_pct": exposure,
        "profit_factor": profit_factor,
    }
