"""跨策略共同回測工具（與 DCA 網格共用）。"""
from .metrics import (
    buy_hold_return_pct,
    compute_metrics_from_equity,
    max_drawdown_pct,
    min_value,
    max_value,
)

__all__ = [
    "buy_hold_return_pct",
    "compute_metrics_from_equity",
    "max_drawdown_pct",
    "min_value",
    "max_value",
]
