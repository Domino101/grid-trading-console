"""
core/services/grid/backtest/allocation.py —— 初始資金 → 每格下單量分配

純函式、零 I/O、零交易所呼叫。

規則（v1）：
- 使用者提供總資金（initial_capital）與「每格金額（USDT 名目）」或「每格佔比 %（pct）」。
- 每格名目 notional 優先取 per_grid_notional；未提供時用 per_grid_capital_percent
  （整數如 1 表示 1%）。
- order_amount（base 幣數量）= per_grid_notional / 參考價（上下限平均）。
- 保證金需求 = per_grid_notional / leverage（isolated 逐倉近似）。
- max_fillable_cells = floor(initial_capital * leverage / per_grid_notional)。
- 資金不足時以 insufficient_capital=True 標示，並提供可讀訊息。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from .models import GridAllocation


def reference_price(lower_price: Decimal, upper_price: Decimal) -> Decimal:
    if upper_price <= lower_price:
        raise ValueError(f"upper_price 必須大於 lower_price: {upper_price} <= {lower_price}")
    return (lower_price + upper_price) / Decimal("2")


def allocate_capital(
    *,
    initial_capital: Decimal,
    grid_count: int,
    lower_price: Decimal,
    upper_price: Decimal,
    per_grid_notional: Optional[Decimal] = None,
    per_grid_capital_percent: Optional[Decimal] = None,
    leverage: int = 10,
    minimum_per_grid_notional: Decimal = Decimal("1"),
) -> GridAllocation:
    """
    計算每格 base 數量與可掛滿格數。

    ``per_grid_capital_percent`` 為整數百分比（如 Decimal("1") 代表 1%）。
    """
    if grid_count <= 0:
        raise ValueError("grid_count 必須 > 0")
    if initial_capital <= 0:
        raise ValueError("initial_capital 必須 > 0")
    if leverage <= 0:
        raise ValueError("leverage 必須 > 0")

    ref = reference_price(lower_price, upper_price)

    if per_grid_notional is None:
        if per_grid_capital_percent is None:
            raise ValueError("必須提供 per_grid_notional 或 per_grid_capital_percent 其中一個")
        if per_grid_capital_percent <= 0:
            raise ValueError("per_grid_capital_percent 必須 > 0")
        per_grid_notional = initial_capital * per_grid_capital_percent / Decimal("100")
    if per_grid_notional <= 0:
        raise ValueError("per_grid_notional 必須 > 0")

    notional = max(per_grid_notional, minimum_per_grid_notional)
    order_amount = notional / ref

    margin_per_cell = notional / Decimal(leverage)
    required_capital = margin_per_cell * Decimal(grid_count)
    if margin_per_cell > 0:
        max_cells = int(initial_capital / margin_per_cell)
    else:
        max_cells = grid_count
    max_fillable_cells = min(max_cells, grid_count)

    insufficient = max_fillable_cells < grid_count
    message = ""
    if insufficient:
        message = (
            f"資金不足：需 ${required_capital:,.2f} 才能掛滿 {grid_count} 格，"
            f"目前 ${initial_capital:,.2f} 只能掛 {max_fillable_cells} 格。"
            f"建議提高初始資金或降低每格金額。"
        )
    else:
        message = (
            f"可掛滿 {grid_count} 格（每格約 ${notional:,.2f}，"
            f"需求 ${required_capital:,.2f}）。"
        )

    return GridAllocation(
        order_amount=order_amount,
        per_grid_notional=notional,
        max_fillable_cells=max_fillable_cells,
        required_capital=required_capital,
        supplied_capital=initial_capital,
        insufficient_capital=insufficient,
        message=message,
    )
