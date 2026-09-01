"""
core/services/grid/backtest/risk.py —— 逐倉保證金 / 清算 / 風險純函式

約束：
- 純函式、零 I/O、使用 Decimal 內部計算、回傳標準型別。
- 公式為「假設 isolated 逐倉 + 固定 maintenance margin rate」的近似值；
  交易所實際規則（資金費率、強平手續費、價格精度）不納入。
- unknown → None（G-5），禁止以 0 冒充。

參考：core/services/grid/terminal_ui.py 的爆倉推導公式，
     本模組將其一般化並加入維持保證金門檻。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional, Sequence

from .models import GridEquityPoint, GridRiskMetrics

_DEFAULT_TARGET_COVERAGE = Decimal("1.0")
_DEFAULT_MAINTENANCE_MARGIN_RATE = Decimal("0.005")


def _d(value: Decimal) -> float:
    return round(float(value), 8)


def liquidation_price_long(
    wallet_balance: Decimal,
    position_quantity: Decimal,
    average_cost: Decimal,
    maintenance_margin_rate: Decimal,
) -> Decimal:
    """
    做多逐倉清算價（近似）。

    令維持保證金 = |qty| * p * mmr，且權益 = wallet + qty*(p - avg)。
    解 ``equity = maintenance`` 得 p。
    """
    qty = position_quantity
    if qty <= 0 or maintenance_margin_rate < 0 or maintenance_margin_rate >= 1:
        return Decimal("0")
    denom = qty * (maintenance_margin_rate - Decimal("1"))
    if denom == 0:
        return Decimal("0")
    price = (wallet_balance - qty * average_cost) / denom
    return price if price > 0 else Decimal("0")


def liquidation_price_short(
    wallet_balance: Decimal,
    position_quantity: Decimal,
    average_cost: Decimal,
    maintenance_margin_rate: Decimal,
) -> Decimal:
    """
    做空逐倉清算價（近似）。

    空頭：qty < 0，權益 = wallet + |qty|*(avg - p)。
    解 ``equity = |qty| * p * mmr`` 得 p。
    """
    qty = abs(position_quantity)
    if qty <= 0 or maintenance_margin_rate < 0:
        return Decimal("0")
    denom = qty * (Decimal("1") + maintenance_margin_rate)
    if denom == 0:
        return Decimal("0")
    price = (wallet_balance + qty * average_cost) / denom
    return price if price > 0 else Decimal("0")


def margin_state(
    *,
    wallet_balance: Decimal,
    position_quantity: Decimal,
    average_cost: Decimal,
    price: Decimal,
    leverage: int,
    maintenance_margin_rate: Decimal,
) -> dict:
    """計算單一價格的逐倉保證金狀態。"""
    qty = position_quantity
    abs_qty = abs(qty)
    unrealized = Decimal("0")
    if abs_qty > 0:
        if qty > 0:
            unrealized = (price - average_cost) * qty
        else:
            unrealized = (average_cost - price) * abs_qty

    position_value = abs_qty * price
    margin_used = position_value / Decimal(leverage) if leverage > 0 else Decimal("0")
    maintenance = position_value * maintenance_margin_rate
    equity = wallet_balance + unrealized

    coverage: Optional[Decimal] = None
    if maintenance > 0:
        coverage = equity / maintenance
    margin_ratio: Optional[Decimal] = coverage

    liquidation: Optional[Decimal] = None
    if qty > 0:
        liquidation = liquidation_price_long(
            wallet_balance, qty, average_cost, maintenance_margin_rate
        )
    elif qty < 0:
        liquidation = liquidation_price_short(
            wallet_balance, qty, average_cost, maintenance_margin_rate
        )

    liquidated = bool(
        abs_qty > 0
        and coverage is not None
        and coverage <= Decimal("1")
    )

    return {
        "wallet_balance": wallet_balance,
        "position_quantity": qty,
        "average_cost": average_cost,
        "price": price,
        "unrealized_pnl": unrealized,
        "margin_used": margin_used,
        "maintenance_margin": maintenance,
        "margin_ratio": _d(margin_ratio) if margin_ratio is not None else None,
        "equity": equity,
        "liquidation_price": _d(liquidation) if liquidation and liquidation > 0 else None,
        "liquidated": liquidated,
    }


def additional_capital_for_coverage(
    *,
    wallet_balance: Decimal,
    position_quantity: Decimal,
    average_cost: Decimal,
    price: Decimal,
    maintenance_margin_rate: Decimal,
    target_coverage: Decimal = _DEFAULT_TARGET_COVERAGE,
) -> Decimal:
    """
    在給定價格下，需再注入多少資金才能使 coverage >= target_coverage。

    若目前已達標則回傳 0。
    """
    qty = position_quantity
    abs_qty = abs(qty)
    if abs_qty <= 0:
        return Decimal("0")

    if qty > 0:
        unrealized = (price - average_cost) * qty
    else:
        unrealized = (average_cost - price) * abs_qty

    position_value = abs_qty * price
    maintenance = position_value * maintenance_margin_rate
    required_equity = maintenance * target_coverage
    current_equity = wallet_balance + unrealized
    needed = required_equity - current_equity
    return max(needed, Decimal("0"))


def required_additional_capital_from_point(point: GridEquityPoint) -> Optional[float]:
    """由單一權益點反推需追加資金（coverage=1 門檻）。

    若無部位或已達標則回傳 0.0；無資料回傳 None（G-5）。
    """
    if point.position_quantity == 0:
        return 0.0
    if point.maintenance_margin is None or point.equity is None:
        return None
    return round(max(0.0, point.maintenance_margin - point.equity), 8)


def build_risk_metrics(equity: Sequence[GridEquityPoint]) -> GridRiskMetrics:
    """
    從逐 bar 權益點彙總風險指標。

    輸出：
    - max_drawdown_pct
    - min_equity
    - max_margin_used_pct（相對當下權益，峰值）
    - liquidation_occurred / liquidation_ts / liquidation_price
    - worst_margin_buffer / worst_margin_buffer_ts（最接近清算的 coverage 餘裕）
    - additional_capital_required_now（最後一根 bar）
    - additional_capital_to_prevent_liquidation（歷史最嚴重的追加資金需求）
    """
    if not equity:
        return GridRiskMetrics()

    values = [float(pt.equity) for pt in equity]
    peak = values[0]
    worst_dd = 0.0
    for v in values:
        if v > peak:
            peak = v
        if peak > 0:
            worst_dd = max(worst_dd, (peak - v) / peak * 100.0)

    min_eq = min(values)

    margin_used_pcts = [
        (pt.margin_used / pt.equity * 100.0)
        for pt in equity
        if pt.equity is not None and pt.equity > 0 and pt.margin_used > 0
    ]
    max_margin_used_pct = max(margin_used_pcts) if margin_used_pcts else None

    liquidations = [pt for pt in equity if pt.status == "liquidated"]
    liquidation_occurred = bool(liquidations)
    liquidation_ts = liquidations[0].ts if liquidations else None
    liquidation_price = liquidations[0].liquidation_price if liquidations else None

    # margin buffer = coverage - 1（近 0 越危險）
    buffers = [
        (pt.margin_ratio - 1.0, pt.ts)
        for pt in equity
        if pt.margin_ratio is not None
    ]
    worst_buffer: Optional[float] = None
    worst_buffer_ts: Optional[int] = None
    if buffers:
        worst_buffer, worst_buffer_ts = min(buffers, key=lambda x: x[0])

    additional_now = required_additional_capital_from_point(equity[-1])

    required_list = [
        cap
        for pt in equity
        for cap in [required_additional_capital_from_point(pt)]
        if cap is not None
    ]
    additional_prev = max(required_list) if required_list else None

    return GridRiskMetrics(
        max_drawdown_pct=round(worst_dd, 4),
        min_equity=round(min_eq, 6),
        max_margin_used_pct=round(max_margin_used_pct, 4) if max_margin_used_pct is not None else None,
        liquidation_occurred=liquidation_occurred,
        liquidation_ts=liquidation_ts,
        liquidation_price=liquidation_price,
        worst_margin_buffer=round(worst_buffer, 6) if worst_buffer is not None else None,
        worst_margin_buffer_ts=worst_buffer_ts,
        additional_capital_required_now=additional_now,
        additional_capital_to_prevent_liquidation=additional_prev,
    )
