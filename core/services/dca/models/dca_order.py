"""
DCA 訂單模型（§4.1 客戶端訂單 ID 命名空間）

客戶端訂單 ID 格式（凍結）：
    dca_{bot_id_hash8}_{leg}_{slot}
    leg  = long / short（決策 D-2 單方向互斥，一台 bot 一個命名空間）
    slot = base / so01..soNN / tp / sl

此格式使「armed 狀態下該命名空間掛單數 = 0」成為可機器驗證的斷言（INV-1）。
"""

import hashlib
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Optional


class DcaOrderRole(Enum):
    """DCA 訂單角色"""
    BASE = "base"                    # 基礎單
    SAFETY = "safety"                # 安全單（攤平單）
    TAKE_PROFIT = "take_profit"      # 止盈單
    STOP_LOSS = "stop_loss"          # 止損單


class DcaOrderStatus(Enum):
    """訂單狀態（與交易所狀態對齊的子集）"""
    PLACED = "placed"          # 已掛出
    PARTIAL = "partial"        # 部分成交
    FILLED = "filled"          # 完全成交
    CANCELLED = "cancelled"
    REJECTED = "rejected"


def bot_id_hash8(bot_id: str) -> str:
    """bot_id 的 8 字元雜湊（sha1 前 8 碼；純識別用途，非安全用途）。"""
    return hashlib.sha1(bot_id.encode("utf-8")).hexdigest()[:8]


def build_client_order_id(bot_id: str, leg: str, slot: str,
                          seq: int = 0) -> str:
    """
    組出凍結格式的客戶端訂單 ID。

    slot 建議值：base / soNN（NN 兩位數）/ tp / sl。
    seq 為單調遞增序號，保證跨輪次與 TP 重掛的 client_order_id 唯一性——
    不加序號時，同輪次的 TP cancel-then-place 和跨輪次的 base 單
    會重用同一 client_order_id，導致 REST 對帳匹配到已成交的舊單。
    """
    if leg not in ("long", "short"):
        raise ValueError(f"無效的 leg: {leg!r}，合法值: long, short")
    return f"dca_{bot_id_hash8(bot_id)}_{leg}_{slot}_{seq:04d}"


@dataclass
class DcaOrder:
    """單筆 DCA 訂單（引擎內部表示；交易所 ID 成交後回填）。"""
    client_order_id: str
    symbol: str
    side: str                     # "buy" / "sell"
    role: DcaOrderRole
    price: Decimal
    quantity: Decimal
    order_id: Optional[str] = None          # 交易所訂單 ID
    slot_index: Optional[int] = None        # safety 單的階梯層號
    status: DcaOrderStatus = DcaOrderStatus.PLACED
    filled_quantity: Decimal = Decimal("0")
    average_fill_price: Optional[Decimal] = None

    def is_buy(self) -> bool:
        return self.side == "buy"

    def is_active_on_exchange(self) -> bool:
        """仍在交易所等待成交（INV 稽核計數口徑）。"""
        return self.status in (DcaOrderStatus.PLACED, DcaOrderStatus.PARTIAL)

    def is_filled(self) -> bool:
        return self.status is DcaOrderStatus.FILLED

    def mark_filled(self, price: Decimal, quantity: Decimal):
        self.status = DcaOrderStatus.FILLED
        self.average_fill_price = price
        self.filled_quantity = quantity

    def mark_cancelled(self):
        self.status = DcaOrderStatus.CANCELLED
