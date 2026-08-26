"""
FakeExchange —— Phase 2 測試囈語（deterministic 模擬交易所）

貫徹計畫 Phase 2 的前置條件：只不接真實交易所與 DB。duck-typing 對齊
core.adapters.exchanges.interface.ExchangeInterface 的 DCA 所需子集：
    create_order / cancel_order / get_open_orders / get_order / get_ticker

行為語意（全部確定性）：
- create_order：記錄呼叫日誌後建單；市價單立即以最新價成交
- 限價單：買單在最新價 ≤ 掛價時成交、賣單在最新價 ≥ 掛價時成交
  （建單當下與每次 set_last_price() 皆檢查）
- 成交同時放開 WS 模擬推送（fill_listeners）
- get_order 以 client_order_id 反查（DcaOrderExecutor.fetch_order 契約）
- 故障注入：fail_next_creates（下 N 次 create_order 拋例外）、
  fail_next_cancel（下一次 cancel 拋例外）
"""

from decimal import Decimal
from types import SimpleNamespace
from typing import Callable, Dict, List, Optional


class FakeExchange:
    """確定性模擬交易所。"""

    def __init__(self, initial_price: str = "2500",
                 min_price_decimals: Optional[int] = None):
        self._last_price = Decimal(str(initial_price))
        self._seq = 0
        self.orders: Dict[str, SimpleNamespace] = {}   # fx-id → 訂單 record
        self.by_client_id: Dict[str, SimpleNamespace] = {}
        self.calls: List[tuple] = []                    # ("create"|"cancel", ...)
        self.fill_listeners: List[Callable] = []        # WS 模擬回調
        self.fail_next_creates = 0
        self.fail_next_cancel = False

    # ── 測試控制 ──

    def set_last_price(self, price) -> None:
        self._last_price = Decimal(str(price))
        for rec in list(self.orders.values()):
            if rec.status == "open":
                self._maybe_fill(rec)

    def create_call_count(self, client_id_contains: Optional[str] = None) -> int:
        return sum(
            1 for c in self.calls
            if c[0] == "create" and (
                client_id_contains is None or client_id_contains in str(c[1])
            )
        )

    def open_order_records(self, symbol: str = "ETH/USDC:USDC") -> List[SimpleNamespace]:
        return [r for r in self.orders.values()
                if r.status == "open" and r.symbol == symbol]

    def cancel_call_count(self) -> int:
        return sum(1 for c in self.calls if c[0] == "cancel")

    # ── 交易所介面 ──

    async def create_order(self, symbol, side, order_type, amount,
                           price=None, params=None):
        client_id = (params or {}).get("client_order_id", "")
        self.calls.append(("create", client_id, side, order_type,
                           Decimal(str(amount)),
                           Decimal(str(price)) if price is not None else None))
        if self.fail_next_creates > 0:
            self.fail_next_creates -= 1
            raise RuntimeError("injected create_order failure")

        self._seq += 1
        oid = f"fx-{self._seq}"
        rec = SimpleNamespace(
            id=oid, client_id=client_id, symbol=symbol, side=side,
            type=order_type, amount=Decimal(str(amount)),
            price=Decimal(str(price)) if price is not None else None,
            status="open", filled=Decimal("0"), average=None,
        )
        self.orders[oid] = rec
        self.by_client_id[client_id] = rec

        if order_type == "market":
            self._do_fill(rec, self._last_price)
        else:
            self._maybe_fill(rec)
        return rec

    async def cancel_order(self, order_id, symbol):
        self.calls.append(("cancel", order_id))
        if self.fail_next_cancel:
            self.fail_next_cancel = False
            raise RuntimeError("injected cancel_order failure")
        rec = self.orders.get(order_id)
        if rec is None or rec.status != "open":
            raise RuntimeError(f"order not found or not open: {order_id}")
        rec.status = "cancelled"
        return {"status": "cancelled", "id": order_id}

    async def get_open_orders(self, symbol=None):
        return [r for r in self.orders.values()
                if r.status == "open" and (symbol is None or r.symbol == symbol)]

    async def get_order(self, order_id, symbol):
        # DCA 執行器以 client_order_id 當查詢鍵（契約見 fetch_order docstring）
        rec = self.by_client_id.get(order_id)
        if rec is None:
            rec = self.orders.get(order_id)
        if rec is None:
            return None
        avg = rec.average if rec.status == "filled" else None
        status = "filled" if rec.status == "filled" else (
            "cancelled" if rec.status == "cancelled" else "open"
        )
        return SimpleNamespace(
            id=rec.id, client_id=rec.client_id, symbol=rec.symbol,
            side=rec.side, type=rec.type, amount=rec.amount, price=rec.price,
            status=status, filled=rec.filled, average=avg,
        )

    async def get_ticker(self, symbol):
        return SimpleNamespace(last=self._last_price)

    # ── 內部 ──

    def _maybe_fill(self, rec) -> None:
        if rec.type != "limit" or rec.price is None:
            return
        fillable = (
            (rec.side == "buy" and self._last_price <= rec.price)
            or (rec.side == "sell" and self._last_price >= rec.price)
        )
        if fillable:
            self._do_fill(rec, rec.price)

    def _do_fill(self, rec, fill_price: Decimal) -> None:
        rec.status = "filled"
        rec.filled = rec.amount
        rec.average = fill_price
        for listener in list(self.fill_listeners):
            listener(rec.client_id, fill_price, rec.amount)
