"""
DCA 統計模型（對齊 GridStatistics 的 to_display_dict 契約）

供 runtime_repository / snapshot 端點複用；序列化規則：
- Decimal → float（僅此處允許，見 Phase 1 稽核 1.14）
- 未知數值一律 None（禁止以 0 代替，G-5）
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, Optional


def _to_float(value: Optional[Decimal]) -> Optional[float]:
    return float(value) if value is not None else None


@dataclass
class DcaStatistics:
    """
    單台 DCA 機器人的顯示層統計。

    欄位語意與 GridStatistics.to_display_dict() 對齊（§2 複用 runtime_repository）。
    """

    bot_id: str
    symbol: str
    status: str                       # DcaStatus.value
    active_direction: Optional[str]   # 本輪鎖定方向；armed/cooling 為 None

    # 當前狀態
    current_price: Optional[Decimal] = None    # Unknown → None（G-5）
    position_quantity: Decimal = Decimal("0")
    average_cost: Optional[Decimal] = None
    total_cost: Decimal = Decimal("0")

    # 階梯進度
    max_safety_orders: int = 0
    filled_safety_orders: int = 0
    active_safety_orders: int = 0     # 當前掛在交易所的安全單數
    take_profit_orders: int = 0       # INV-2：averaging 中必須恰好 1

    # 循環與損益
    cycle_count: int = 0
    realized_profit: Decimal = Decimal("0")
    total_fees: Decimal = Decimal("0")
    unrealized_profit: Optional[Decimal] = None

    def to_display_dict(self) -> Dict:
        return {
            "bot_id": self.bot_id,
            "symbol": self.symbol,
            "status": self.status,
            "active_direction": self.active_direction,
            "current_price": _to_float(self.current_price),
            "position": {
                "quantity": float(self.position_quantity),
                "average_cost": _to_float(self.average_cost),
                "total_cost": float(self.total_cost),
            },
            "ladder": {
                "max_safety_orders": self.max_safety_orders,
                "filled_safety_orders": self.filled_safety_orders,
                "active_safety_orders": self.active_safety_orders,
                "take_profit_orders": self.take_profit_orders,
            },
            "cycles": self.cycle_count,
            "profit": {
                "realized": float(self.realized_profit),
                "unrealized": _to_float(self.unrealized_profit),
                "fees": float(self.total_fees),
            },
        }
