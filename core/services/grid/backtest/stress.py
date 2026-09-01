"""
core/services/grid/backtest/stress.py —— 壓力情境爆倉檢查

對同一組「歷史開局價格」合成單向壓力 K 線（-15/-30/-50% 與 +15/+30/+50%），
用與真實回測相同的 `GridBacktestSimulator` 重播，輸出該情境下是否爆倉、
最低權益、最後權益以及「需追加多少資金才能防止爆倉」。

誠實揭露：壓力 K 線為「線性插值單向移動」的合成情境，非真實歷史。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from .allocation import GridAllocation
from .models import GridBacktestSpec
from .simulator import GridBacktestSimulator

STRESS_MOVEMENTS_DEFAULT = (-50.0, -30.0, -15.0, 15.0, 30.0, 50.0)


def _normalize_candles(candles: Sequence[Any]) -> List[Dict[str, Any]]:
    """正規化為 simulator 使用的 ``{ts, open, high, low, close, volume}`` 格式。"""
    out: List[Dict[str, Any]] = []
    for c in candles:
        if isinstance(c, dict):
            ts = c.get("ts") or c.get("t") or 0
            ts = int(ts // 1000 if ts > 10_000_000_000 else ts)
            out.append({
                "ts": ts,
                "open": float(c.get("open") or c.get("o") or 0),
                "high": float(c.get("high") or c.get("h") or 0),
                "low": float(c.get("low") or c.get("l") or 0),
                "close": float(c.get("close") or c.get("c") or 0),
                "volume": float(c.get("volume") or c.get("v") or 0),
            })
        elif isinstance(c, (list, tuple)) and len(c) >= 5:
            ts = int(c[0] // 1000 if c[0] > 10_000_000_000 else c[0])
            out.append({
                "ts": ts,
                "open": float(c[1]),
                "high": float(c[2]),
                "low": float(c[3]),
                "close": float(c[4]),
                "volume": float(c[5] if len(c) > 5 else 0),
            })
        elif hasattr(c, "close"):
            dt = getattr(c, "timestamp", None)
            ts = int(dt.timestamp()) if hasattr(dt, "timestamp") else int(getattr(c, "ts", 0))
            out.append({
                "ts": ts,
                "open": float(getattr(c, "open", 0)),
                "high": float(getattr(c, "high", 0)),
                "low": float(getattr(c, "low", 0)),
                "close": float(getattr(c, "close", 0)),
                "volume": float(getattr(c, "volume", 0)),
            })
    return out


def _build_stress_candles(
    candles: Sequence[Any],
    start_price: float,
    movement_pct: float,
) -> List[Dict[str, Any]]:
    """把 (已正規化) K 線轉成線性單向移動的合成 K 線。"""
    n = len(candles)
    if n == 0:
        return []
    target = start_price * (1.0 + movement_pct / 100.0)
    out: List[Dict[str, Any]] = []
    for i, c in enumerate(candles):
        ratio = (i + 1) / n
        close = start_price + (target - start_price) * ratio
        out.append({
            "ts": int(c["ts"]),
            "open": float(close),
            "high": float(close * 1.001),
            "low": float(close * 0.999),
            "close": float(close),
            "volume": float(c["volume"]),
        })
    return out


def run_stress(
    spec: GridBacktestSpec,
    candles: Sequence[Any],
    allocation: Optional[GridAllocation] = None,
    movements: Optional[Sequence[float]] = None,
    source: str = "stress",
    data_status: str = "live",
) -> Dict[str, Any]:
    """執行壓力情境掃描（唯讀、deterministic）。"""
    if not candles:
        raise ValueError("candles 不得為空序列")
    movements = movements or STRESS_MOVEMENTS_DEFAULT
    candles = _normalize_candles(candles)
    if not candles:
        raise ValueError("candles 無法解析")
    start_price = float(candles[0]["close"])

    scenarios: List[Dict[str, Any]] = []
    for mv in movements:
        stress_candles = _build_stress_candles(candles, start_price, mv)
        result = GridBacktestSimulator(
            spec=spec,
            candles=stress_candles,
            allocation=allocation,
            source=source,
            data_status=data_status,
        ).run()
        scenarios.append({
            "movement_pct": float(mv),
            "liquidation_occurred": bool(result.risk.liquidation_occurred),
            "liquidation_ts": result.risk.liquidation_ts,
            "liquidation_price": result.risk.liquidation_price,
            "final_equity": result.metrics["final_equity"],
            "min_equity": result.risk.min_equity,
            "additional_capital_to_prevent_liquidation": result.risk.additional_capital_to_prevent_liquidation,
            "max_drawdown_pct": result.metrics["max_drawdown_pct"],
        })

    return {
        "start_price": float(start_price),
        "scenarios": scenarios,
        "warnings": [
            "壓力情境使用線性單向移動的合成 K 線，非真實歷史",
            "壓力情境沿用與主回測相同的近似逐倉保證金/清算模型",
        ],
    }
