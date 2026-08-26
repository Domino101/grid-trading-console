"""ReplayExchange —— 確定性 K 線撮合交易所（無未来函数、保守順序、全 Decimal）

本撮合器貫徹《DCA 即時圖表與回測子系統 實作計畫》§4.3 撮合規則 R-1 至 R-13：
- R-1: 無未来函数。在棒 i 送出的限價單記 placed_bar=i，僅在棒 j > i 參與撮合。
- R-2: 限價買單條件 low_j <= price，成交價 = min(open_j, price)（跳空以更優的開盤價成交）。
- R-3: 限價賣單條件 high_j >= price，成交價 = max(open_j, price)（跳空以更優的開盤價成交）。
- R-4: 同棒多單保守順序：先結算對當前部位不利的單（做多：先安全單買進，後止盈賣出；安全單由高到低）。
- R-5: 市價單（止損）以觸發棒 open_j 成交（內建跳空滑價）。
- R-6: 不模擬部分成交（整張成交，v1 簡化）。
- R-7: 手續費沿用引擎 config.fee_rate，本交易所不重複扣費。
- R-8: 熔斷固定關閉。
- R-11: 確定性執行。
- R-13: 記錄 client_order_id 命名空間。

注意：本模組為純核心撮合邏輯，零 I/O、零網路呼叫、全 Decimal 計算。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .candles import Candle, to_candles


@dataclass
class ReplayOrderRecord:
    id: str
    client_id: str
    symbol: str
    side: str  # "buy" | "sell"
    type: str  # "limit" | "market"
    amount: Decimal
    price: Optional[Decimal]
    status: str  # "open" | "filled" | "cancelled"
    filled: Decimal
    average: Optional[Decimal]
    placed_bar: int
    placed_ts: int
    filled_bar: Optional[int] = None
    filled_ts: Optional[int] = None


class ReplayExchange:
    """確定性 K 線撮合交易所（無未来函数、保守順序、全 Decimal）。
    
    遵守 duck-typing 契約（create_order / cancel_order / get_open_orders / get_order / get_ticker），
    由 ReplayDriver 逐棒調用 advance() 結算撮合。
    """

    def __init__(
        self,
        candles: Sequence[Any],
        initial_price: Optional[Decimal] = None,
    ):
        self.candles: List[Candle] = to_candles(candles)
        self.current_bar: int = 0
        self._seq: int = 0
        self.orders: Dict[str, ReplayOrderRecord] = {}  # order_id -> record
        self.by_client_id: Dict[str, ReplayOrderRecord] = {}  # client_id -> record
        self.calls: List[tuple] = []
        self.fill_listeners: List[Callable[[str, Decimal, Decimal], None]] = []
        self.fills: List[dict] = []
        self._initial_price: Optional[Decimal] = initial_price

    # ── 回放驅動與撮合 ────────────────────────────────────

    def set_bar(self, bar_index: int) -> None:
        """設定當前棒索引（決策與送單發生的當前時間點）。"""
        self.current_bar = bar_index

    def advance(self, bar_index: int) -> List[ReplayOrderRecord]:
        """推進至棒 bar_index 並撮合所有在 bar_index 之前已掛出的在世訂單（R-1 至 R-6）。
        
        撮合規則：
        - R-1: 僅撮合 placed_bar < bar_index 的訂單。
        - R-4: 同棒保守順序（做多：安全買單由高到低 -> 止盈賣單 -> 止損市價單；做空相反）。
        - R-2: 限價買單 low <= price -> 成交價 = min(open, price)。
        - R-3: 限價賣單 high >= price -> 成交價 = max(open, price)。
        - R-5: 市價單以 open 成交。
        - R-6: 整張成交（不模擬部分成交）。
        """
        if bar_index < 0 or bar_index >= len(self.candles):
            return []

        candle = self.candles[bar_index]
        self.current_bar = bar_index

        # R-1: 僅篩選在棒 bar_index 之前掛出的在世訂單
        eligible_orders = [
            rec for rec in self.orders.values()
            if rec.status == "open" and rec.placed_bar < bar_index
        ]

        if not eligible_orders:
            return []

        # R-4: 同棒保守順序排序
        sorted_orders = self._sort_orders_conservative(eligible_orders)
        newly_filled: List[ReplayOrderRecord] = []

        for rec in sorted_orders:
            if rec.status != "open":
                continue

            match_result = self._check_match(rec, candle)
            if match_result is None:
                continue

            fill_price = match_result
            rec.status = "filled"
            rec.filled = rec.amount
            rec.average = fill_price
            rec.filled_bar = bar_index
            rec.filled_ts = candle.ts

            self.fills.append({
                "client_id": rec.client_id,
                "price": fill_price,
                "amount": rec.amount,
                "bar": bar_index,
                "ts": candle.ts,
                "side": rec.side,
                "type": rec.type,
            })
            newly_filled.append(rec)

            # 觸發回調（WS 推送模擬）
            for listener in list(self.fill_listeners):
                listener(rec.client_id, fill_price, rec.amount)

        return newly_filled

    def _sort_orders_conservative(
        self, orders: List[ReplayOrderRecord]
    ) -> List[ReplayOrderRecord]:
        """R-4 同棒保守順序：不利優先。
        
        做多場景：
        1. 安全單買進 / 基礎單買進（對部位不利，由高價到低價）
        2. 止盈單賣出（平倉獲利，由低價到高價）
        3. 市價止損單
        
        做空場景：
        1. 安全單賣出 / 基礎單賣出（對部位不利，由低價到高價）
        2. 止盈單買進（平倉獲利，由高價到低價）
        3. 市價止損單
        """
        def sort_key(rec: ReplayOrderRecord) -> Tuple[int, Decimal, int]:
            cid = rec.client_id.lower()
            is_tp = ("_tp" in cid) or ("tp" in cid)
            is_sl = ("_sl" in cid) or ("sl" in cid) or (rec.type == "market")
            p = rec.price if rec.price is not None else Decimal("0")

            if is_sl:
                return (2, Decimal("0"), rec.placed_bar)

            if rec.side == "buy":
                if is_tp:
                    # 做空之 TP 買單：較低優先級，低價優先
                    return (1, p, rec.placed_bar)
                else:
                    # 做多之安全/基礎買單：不利優先，高價優先 (price DESC)
                    return (0, -p, rec.placed_bar)
            else:  # sell
                if is_tp:
                    # 做多之 TP 賣單：較低優先級，低價優先
                    return (1, p, rec.placed_bar)
                else:
                    # 做空之安全/基礎賣單：不利優先，低價優先 (price ASC)
                    return (0, p, rec.placed_bar)

        return sorted(orders, key=sort_key)

    def _check_match(
        self, rec: ReplayOrderRecord, candle: Candle
    ) -> Optional[Decimal]:
        """檢驗訂單是否在此根 K 棒撮合成交，並計算跳空成交價（R-2, R-3, R-5）。"""
        if rec.type == "market":
            # R-5: 市價單以開盤價成交
            return candle.open

        if rec.price is None:
            return None

        if rec.side == "buy":
            # R-2: 限價買單 low <= price
            if candle.low <= rec.price:
                # 跳空向下以更優的開盤價成交
                return min(candle.open, rec.price)
        elif rec.side == "sell":
            # R-3: 限價賣單 high >= price
            if candle.high >= rec.price:
                # 跳空向上以更優的開盤價成交
                return max(candle.open, rec.price)

        return None

    # ── 交易所 duck-typing 介面 ───────────────────────────

    async def create_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        amount: Any,
        price: Optional[Any] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> SimpleNamespace:
        """下單（不立即成交，僅註冊 placed_bar = current_bar，次棒才可撮合，R-1）。"""
        client_id = ""
        if params:
            client_id = str(
                params.get("client_order_id")
                or params.get("clientOrderId")
                or params.get("client_id")
                or ""
            )

        amt = Decimal(str(amount))
        p = Decimal(str(price)) if price is not None else None
        side_clean = str(side).lower()
        type_clean = str(order_type).lower()

        self.calls.append(("create", client_id, side_clean, type_clean, amt, p))
        self._seq += 1
        oid = f"replay-{self._seq}"

        candle = self.candles[self.current_bar] if self.current_bar < len(self.candles) else None
        placed_ts = candle.ts if candle else 0

        rec = ReplayOrderRecord(
            id=oid,
            client_id=client_id if client_id else oid,
            symbol=str(symbol),
            side=side_clean,
            type=type_clean,
            amount=amt,
            price=p,
            status="open",
            filled=Decimal("0"),
            average=None,
            placed_bar=self.current_bar,
            placed_ts=placed_ts,
        )

        self.orders[oid] = rec
        self.by_client_id[rec.client_id] = rec

        return SimpleNamespace(
            id=rec.id,
            client_id=rec.client_id,
            symbol=rec.symbol,
            side=rec.side,
            type=rec.type,
            amount=rec.amount,
            price=rec.price,
            status=rec.status,
            filled=rec.filled,
            average=rec.average,
        )

    async def cancel_order(
        self, order_id: str, symbol: str
    ) -> Dict[str, Any]:
        """撤單。若訂單已成交或不存在則回傳失敗。"""
        self.calls.append(("cancel", order_id))
        rec = self.orders.get(order_id) or self.by_client_id.get(order_id)
        if rec is None or rec.status != "open":
            return {"status": "error", "id": order_id, "success": False}

        rec.status = "cancelled"
        return {"status": "cancelled", "id": order_id, "success": True}

    async def get_open_orders(
        self, symbol: Optional[str] = None
    ) -> List[SimpleNamespace]:
        """取得目前所有狀態為 open 的訂單。"""
        return [
            SimpleNamespace(
                id=r.id,
                client_id=r.client_id,
                symbol=r.symbol,
                side=r.side,
                type=r.type,
                amount=r.amount,
                price=r.price,
                status=r.status,
                filled=r.filled,
                average=r.average,
            )
            for r in self.orders.values()
            if r.status == "open" and (symbol is None or r.symbol == symbol)
        ]

    async def get_order(
        self, order_id: str, symbol: str
    ) -> Optional[SimpleNamespace]:
        """以 order_id 或 client_order_id 反查訂單。"""
        rec = self.by_client_id.get(order_id) or self.orders.get(order_id)
        if rec is None:
            return None

        return SimpleNamespace(
            id=rec.id,
            client_id=rec.client_id,
            symbol=rec.symbol,
            side=rec.side,
            type=rec.type,
            amount=rec.amount,
            price=rec.price,
            status=rec.status,
            filled=rec.filled,
            average=rec.average,
        )

    async def get_ticker(self, symbol: str) -> SimpleNamespace:
        """回傳當前棒的收盤價（作為 last_price 基準）。"""
        if self.current_bar < len(self.candles):
            close_price = self.candles[self.current_bar].close
            return SimpleNamespace(
                last=close_price,
                close=close_price,
                price=close_price,
                open=self.candles[self.current_bar].open,
                high=self.candles[self.current_bar].high,
                low=self.candles[self.current_bar].low,
                timestamp=self.candles[self.current_bar].ts,
            )
        if self._initial_price is not None:
            return SimpleNamespace(last=self._initial_price)
        return SimpleNamespace(last=Decimal("0"))
