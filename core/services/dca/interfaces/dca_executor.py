"""
IDcaOrderExecutor —— DCA 下單執行抽象（Phase 2 交付物）

把「交易所抽象層」包裝成 DCA 領域語意（role / slot_index / quantized 數量），
讓 DcaCoordinator 只面對 DcaOrder 與 OrderOutcome，不需要知道交易所細節。

放置介面的理由（計畫 §3）：Phase 2 以 FakeExchange 驅動測試、Phase 3 才接
真實 adapter（共享 ExchangeAdapterPool）；執行層只依賴此處定義的狹窄介面。
"""

from abc import ABC, abstractmethod
from decimal import Decimal
from enum import Enum
from typing import Callable, List, Optional

from ..models.dca_order import DcaOrder, DcaOrderRole


class OutcomeKind(Enum):
    """下單結果分類（給狀態機做決策用）"""
    PLACED = "placed"                    # 已掛上交易所
    REJECTED_MIN_NOTIONAL = "rejected_min_notional"  # 低於最小名目，未送出（稽核 2.15）
    DEFERRED_CIRCUIT_OPEN = "deferred_circuit_open"  # 熔斷中延後（稽核 2.14）
    POSITION_CLOSED_EXTERNALLY = "position_closed_externally"  # 交易所持倉已歸零（外部已平倉）
    ERROR = "error"                      # 交易所拋錯


class OrderOutcome:
    """下單結果（不可變語意；coordinator 依 kind 分支處理）。"""

    __slots__ = ("kind", "order", "reason")

    def __init__(self, kind: OutcomeKind,
                 order: Optional[DcaOrder] = None,
                 reason: Optional[str] = None):
        self.kind = kind
        self.order = order
        self.reason = reason

    def __repr__(self):
        return (f"OrderOutcome(kind={self.kind.value}, "
                f"order={self.order.client_order_id if self.order else None}, "
                f"reason={self.reason!r})")


class IDcaOrderExecutor(ABC):
    """
    DCA 下單執行器抽象。

    實作必須保證：
    - place_* 在熔斷開啟時回傳 DEFERRED_CIRCUIT_OPEN，且**不送出請求**（稽核 2.14）
    - 低於最小名目的訂單回傳 REJECTED_MIN_NOTIONAL，且**不送出請求**（稽核 2.15）
    - 價格/數量依設定精度 quantize（價格 ROUND_HALF_EVEN、數量 ROUND_DOWN）
    """

    @abstractmethod
    async def place_limit(self, role: DcaOrderRole, side: str,
                          price: Decimal, quantity: Decimal,
                          slot_index: Optional[int] = None) -> OrderOutcome:
        """掛限價單（安全單 / TP / SL 用）。"""

    @abstractmethod
    async def place_market(self, role: DcaOrderRole, side: str,
                           quantity: Decimal) -> OrderOutcome:
        """送市價單（止損平倉用）。"""

    @abstractmethod
    async def cancel(self, order: DcaOrder) -> bool:
        """撤單；回傳是否確實在交易所端撤銷成功。"""

    @abstractmethod
    async def fetch_open_orders(self) -> List[DcaOrder]:
        """列出該 bot 在交易所端的本命名空間掛單（INV 稽核用）。"""

    @abstractmethod
    async def fetch_order(self, client_order_id: str) -> Optional[DcaOrder]:
        """查詢單筆訂單最新狀態（REST 對帳用）。"""

    @abstractmethod
    async def last_price(self) -> Decimal:
        """交易對最新成交價（觸發價 / 止損監控用）。"""

    @abstractmethod
    async def fetch_position(self) -> Optional[dict]:
        """查詢交易所端該標的實際持倉（自癒對帳與單向持倉判定用）。"""

    @abstractmethod
    def circuit_open(self) -> bool:
        """熔斷器目前是否開啟（共用 BinanceRest / GridEngineImpl 狀態）。"""


# 熔斷探針型別：無參數、回傳 True=開啟（禁寫入）
CircuitProbe = Callable[[], bool]
