"""
TakeProfitManager —— TP 掛單的建立 / 取消 / 重掛（計畫 Phase 2 交付物）

核心不變式（籍核 2.4/2.5/2.6，風險 R-3）：
- INV-2：任意時刻 TP 掛單恰好 1 筆，價格 = avg_current × (1 ± tp/100)
- 重掛原子性：先 cancel 舊 TP、成功後才 place 新 TP；順序相反會出現兩張 TP（超賣）
- cancel 成功但 place 失敗 → 進入 degraded 待重試，**不可靜默留在無 TP 狀態**
"""

from decimal import Decimal
from typing import Optional

from ..interfaces.dca_executor import IDcaOrderExecutor, OutcomeKind
from ..ladder import take_profit_price
from ..models.dca_order import DcaOrder, DcaOrderRole


class TpReplaceError(Exception):
    """TP 重掛失敗（cancel 成功但 place 未成功等）。coordinator 據此轉 degraded。"""

    def __init__(self, reason: str, tp_missing: bool):
        self.tp_missing = tp_missing  # True = 交易所端目前無 TP（必須重試）
        super().__init__(f"TP 重掛失敗（{'無 TP 保護中' if tp_missing else '舊 TP 仍在'}）: {reason}")


class TpPositionClosedExternallyError(Exception):
    """TP 掛單失敗且交易所持倉已歸零（外部已平倉）。coordinator 據此直接收輪完成。"""
    pass


class TakeProfitManager:
    """管理一台 bot 的止盈單生命週期。"""

    def __init__(self, executor: IDcaOrderExecutor, direction: str):
        self._executor = executor
        self._direction = direction  # "long" / "short"
        self.current_tp: Optional[DcaOrder] = None

    @property
    def _close_side(self) -> str:
        # 平倉方向：做多賣出止盈、做空買回止盈
        return "sell" if self._direction == "long" else "buy"

    def price_for(self, avg_price: Decimal, take_profit_percent: Decimal) -> Decimal:
        return take_profit_price(avg_price, take_profit_percent, self._direction)

    async def place(self, avg_price: Decimal, take_profit_percent: Decimal,
                    position_qty: Decimal) -> DcaOrder:
        """建立 TP；失敗拋 TpReplaceError（tp_missing=True 表示無 TP 保護）。"""
        if self.current_tp is not None and self.current_tp.is_active_on_exchange():
            raise TpReplaceError("已有 TP 掛單，重複建立會違反 INV-2", tp_missing=False)

        price = self.price_for(avg_price, take_profit_percent)
        outcome = await self._executor.place_limit(
            DcaOrderRole.TAKE_PROFIT, self._close_side, price, position_qty,
            leg=self._direction,
        )
        if outcome.kind is OutcomeKind.POSITION_CLOSED_EXTERNALLY:
            raise TpPositionClosedExternallyError(outcome.reason or "position closed externally")
        if outcome.kind is not OutcomeKind.PLACED:
            raise TpReplaceError(outcome.reason or outcome.kind.value, tp_missing=True)
        self.current_tp = outcome.order
        return outcome.order

    async def replace(self, avg_price: Decimal, take_profit_percent: Decimal,
                      position_qty: Decimal) -> DcaOrder:
        """
        重掛 TP（安全單成交後均價改變）。

        嚴格 cancel-then-place；cancel 成功但 place 失敗 → TpReplaceError(tp_missing=True)。
        """
        old = self.current_tp
        if old is not None and old.is_active_on_exchange():
            cancelled = await self._executor.cancel(old)
            if not cancelled:
                # 撤不掉舊 TP：保守地仍在舊 TP 保護下 → 報錯讓狀態機決定
                raise TpReplaceError("cancel 舊 TP 失敗", tp_missing=False)
            self.current_tp = None

        new_price = self.price_for(avg_price, take_profit_percent)
        outcome = await self._executor.place_limit(
            DcaOrderRole.TAKE_PROFIT, self._close_side, new_price, position_qty,
            leg=self._direction,
        )
        if outcome.kind is OutcomeKind.POSITION_CLOSED_EXTERNALLY:
            raise TpPositionClosedExternallyError(outcome.reason or "position closed externally")
        if outcome.kind is not OutcomeKind.PLACED:
            raise TpReplaceError(outcome.reason or outcome.kind.value, tp_missing=True)
        self.current_tp = outcome.order
        return outcome.order

    async def cancel(self) -> bool:
        old = self.current_tp
        self.current_tp = None
        if old is not None and old.is_active_on_exchange():
            return await self._executor.cancel(old)
        return True
