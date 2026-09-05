"""
DcaOrderExecutor —— 包裝 ExchangeInterface 的 DCA 下單執行器（Phase 2）

職責（計畫 Phase 2 實作要點）：
1. 熔斷守衛：下單前查 BinanceRest._is_adapter_circuit_open() 與
   GridEngineImpl._shared_api_circuit_until；熔斷中回 DEFERRED_CIRCUIT_OPEN，
   絕對不送出交易所寫入請求（稽核 2.14）。
2. 精度 quantize：價格依 price_decimals（ROUND_HALF_EVEN）、
   數量依 quantity_precision（ROUND_DOWN）（稽核 2.15）。
3. 最小名目檢查：notional < min_notional 直接拒絕並記錄，不送交易所。
4. 客戶端 ID：dca_{hash8}_{leg}_{slot} 命名空間（§4.1，INV-1 機器驗證鍵）。

本類不 import 任何具體交易所 adapter；交易所物件以狹窄 duck-typing 介面注入
（create_order / cancel_order / get_open_orders / get_order / get_ticker），
由 Phase 3 的監督者提供（共享 ExchangeAdapterPool 內的實例）。
"""

import time
from typing import Any, Callable, List, Optional

from ..interfaces.dca_executor import (
    CircuitProbe,
    IDcaOrderExecutor,
    OrderOutcome,
    OutcomeKind,
)
from ..ladder import quantize_price, quantize_quantity
from ..models.dca_config import DcaConfig
from ..models.dca_order import (
    DcaOrder,
    DcaOrderRole,
    DcaOrderStatus,
    build_client_order_id,
)
from core.adapters.exchanges.models import OrderSide, OrderType
from core.logging import get_logger

from decimal import Decimal

logger = get_logger(__name__)

_ROLE_SLOT = {
    DcaOrderRole.BASE: "base",
    DcaOrderRole.TAKE_PROFIT: "tp",
    DcaOrderRole.STOP_LOSS: "sl",
}


def _to_order_side(side: Any) -> OrderSide:
    """確保 side 為 OrderSide 列舉，相容字串與 Enum。"""
    if isinstance(side, OrderSide):
        return side
    s = str(getattr(side, "value", side)).lower()
    return OrderSide.BUY if s in ("buy", "long") else OrderSide.SELL


def _to_order_type(order_type: Any) -> OrderType:
    """確保 order_type 為 OrderType 列舉，相容字串與 Enum。"""
    if isinstance(order_type, OrderType):
        return order_type
    s = str(getattr(order_type, "value", order_type)).lower()
    return OrderType.MARKET if s == "market" else OrderType.LIMIT


def _status_from_exchange(value: Any) -> DcaOrderStatus:
    """把交易所回傳的狀態（Enum 或字串）映射為 DcaOrderStatus。"""
    name = getattr(value, "value", value)
    name = str(name).lower()
    if "fill" in name or "closed" in name:
        return DcaOrderStatus.FILLED
    if "cancel" in name:
        return DcaOrderStatus.CANCELLED
    if "reject" in name or "expired" in name:
        return DcaOrderStatus.REJECTED
    if "partial" in name:
        return DcaOrderStatus.PARTIAL
    return DcaOrderStatus.PLACED


class DcaOrderExecutor(IDcaOrderExecutor):
    """DcaConfig + 交易所 duck-typing adapter 的執行橋。"""

    def __init__(self, config: DcaConfig, bot_id: str, exchange,
                 min_notional: Decimal = Decimal("0"),
                 circuit_probe: Optional[CircuitProbe] = None):
        self._config = config
        self._bot_id = bot_id
        self._exchange = exchange
        self._min_notional = min_notional
        self._circuit_probe = circuit_probe or self._default_circuit_probe
        self._symbol = config.symbol

    # ── 熔斷 ──────────────────────────────────────────────

    @staticmethod
    def _default_circuit_probe() -> bool:
        """
        共用既有熔斷真相（計畫 §2）。

        1. BinanceRest._adapter_circuit_until（類別層級）
        2. GridEngineImpl._shared_api_circuit_until（含磁碟持久化狀態）

        延遲載入以避免模組層級依賴；任何失敗視為「非熔斷」（fail-open 於此，
        因真正的 adapter 層也會自行攔截熔斷）。
        """
        now = time.time()
        try:
            from core.adapters.exchanges.adapters.binance_rest import BinanceRest
            if getattr(BinanceRest, "_adapter_circuit_until", 0.0) > now:
                return True
        except Exception:
            pass
        try:
            from core.services.grid.implementations.grid_engine_impl import GridEngineImpl
            if getattr(GridEngineImpl, "_shared_api_circuit_until", 0.0) > now:
                return True
        except Exception:
            pass
        return False

    def circuit_open(self) -> bool:
        return bool(self._circuit_probe())

    # ── 下單 ──────────────────────────────────────────────

    def _client_id(self, role: DcaOrderRole, leg: str,
                   slot_index: Optional[int]) -> str:
        if role is DcaOrderRole.SAFETY:
            slot = f"so{slot_index:02d}"
        else:
            slot = _ROLE_SLOT[role]
        return build_client_order_id(self._bot_id, leg, slot)

    def _quantize(self, price: Optional[Decimal], quantity: Decimal):
        q_price = quantize_price(price, self._config.price_decimals) \
            if price is not None else None
        q_qty = quantize_quantity(quantity, self._config.quantity_precision)
        return q_price, q_qty

    def _check_circuit(self, role: DcaOrderRole) -> Optional[OrderOutcome]:
        if self.circuit_open():
            logger.warning(
                "熔斷開啟，延後下單",
                extra={
                    "symbol": self._symbol, "bot_id": self._bot_id,
                    "action": "place_order", "result": "deferred_circuit_open",
                    "role": role.value,
                },
            )
            return OrderOutcome(OutcomeKind.DEFERRED_CIRCUIT_OPEN,
                                reason="circuit_open")
        return None

    def _check_min_notional(self, price: Optional[Decimal], quantity: Decimal,
                            role: DcaOrderRole) -> Optional[OrderOutcome]:
        ref_price = price if price is not None else Decimal("0")
        notional = ref_price * quantity if ref_price > 0 else Decimal("0")
        # 市價單由呼叫方提供估算價；此處 ref_price=0 表示無法估→仍檢查下限為 0
        if self._min_notional > 0 and price is not None \
                and notional < self._min_notional:
            logger.warning(
                "訂單低於最小名目，拒絕送出",
                extra={
                    "symbol": self._symbol, "bot_id": self._bot_id,
                    "action": "place_order", "result": "rejected_min_notional",
                    "role": role.value, "notional": str(notional),
                    "min_notional": str(self._min_notional),
                },
            )
            return OrderOutcome(
                OutcomeKind.REJECTED_MIN_NOTIONAL,
                reason=f"notional {notional} < min_notional {self._min_notional}",
            )
        return None

    async def place_limit(self, role: DcaOrderRole, side: str,
                          price: Decimal, quantity: Decimal,
                          slot_index: Optional[int] = None,
                          leg: str = "long") -> OrderOutcome:
        deferred = self._check_circuit(role)
        if deferred:
            return deferred
        rejected = self._check_min_notional(price, quantity, role)
        if rejected:
            return rejected

        q_price, q_qty = self._quantize(price, quantity)
        if q_qty <= 0:
            return OrderOutcome(OutcomeKind.REJECTED_MIN_NOTIONAL,
                                reason="quantized quantity <= 0")
        client_id = self._client_id(role, leg, slot_index)
        order = DcaOrder(
            client_order_id=client_id, symbol=self._symbol, side=side,
            role=role, price=q_price, quantity=q_qty, slot_index=slot_index,
        )
        try:
            order_side = _to_order_side(side)
            order_type = OrderType.LIMIT
            is_close_order = role in (DcaOrderRole.TAKE_PROFIT, DcaOrderRole.STOP_LOSS)
            order_params = {
                "client_order_id": client_id,
                "clientOrderId": client_id,
                "newClientOrderId": client_id,
            }
            if is_close_order:
                order_params["reduce_only"] = True
                order_params["reduceOnly"] = True

            raw = await self._exchange.create_order(
                self._symbol, order_side, order_type, q_qty, q_price,
                params=order_params,
            )
            order.order_id = str(getattr(raw, "id", getattr(raw, "order_id", "")) or "")
            logger.info(
                "限價單已送出",
                extra={
                    "symbol": self._symbol, "bot_id": self._bot_id,
                    "action": "place_order", "result": "placed",
                    "role": role.value, "client_order_id": client_id,
                    "price": str(q_price), "quantity": str(q_qty),
                },
            )
            return OrderOutcome(OutcomeKind.PLACED, order=order)
        except Exception as exc:  # 交易所拋錯 —— 由狀態機決定降級或失敗
            err_msg = str(exc)
            is_reduce_only_err = "-2022" in err_msg or "ReduceOnly" in err_msg or "reduceonly" in err_msg.lower()
            if is_close_order and is_reduce_only_err:
                # 檢查交易所實際持倉
                pos = await self.fetch_position()
                if pos is not None and pos["size"] == Decimal("0"):
                    # 狀況 A：交易所端持倉已歸 0（外部已平倉完畢）
                    logger.warning(
                        "平倉單因 ReduceOnly 被拒且交易所持倉已為 0，判定外部已平倉完畢",
                        extra={
                            "symbol": self._symbol, "bot_id": self._bot_id,
                            "action": "place_order", "result": "position_closed_externally",
                            "role": role.value, "client_order_id": client_id,
                            "error": err_msg,
                        },
                    )
                    return OrderOutcome(
                        OutcomeKind.POSITION_CLOSED_EXTERNALLY,
                        order=order,
                        reason="exchange position is zero; position closed externally",
                    )
                elif pos is not None and pos["size"] > Decimal("0"):
                    # 狀況 B：交易所端有持倉但方向相反（單向持倉下多空衝突）
                    # 例如做多機器人想掛 SELL TP，但單向模式下淨持倉為空頭
                    logger.warning(
                        "平倉單因 ReduceOnly 被拒但實盤存在反向淨持倉，自動降級為普通限價單重試",
                        extra={
                            "symbol": self._symbol, "bot_id": self._bot_id,
                            "action": "place_order", "result": "fallback_normal_limit",
                            "role": role.value, "client_order_id": client_id,
                            "exchange_raw_amount": str(pos.get("raw_amount", 0)),
                        },
                    )
                    order_params.pop("reduce_only", None)
                    order_params.pop("reduceOnly", None)
                    try:
                        raw = await self._exchange.create_order(
                            self._symbol, order_side, order_type, q_qty, q_price,
                            params=order_params,
                        )
                        order.order_id = str(getattr(raw, "id", getattr(raw, "order_id", "")) or "")
                        logger.info(
                            "普通限價平倉單（無 ReduceOnly）已成功送出",
                            extra={
                                "symbol": self._symbol, "bot_id": self._bot_id,
                                "action": "place_order", "result": "placed",
                                "role": role.value, "client_order_id": client_id,
                                "price": str(q_price), "quantity": str(q_qty),
                            },
                        )
                        return OrderOutcome(OutcomeKind.PLACED, order=order)
                    except Exception as fallback_exc:
                        logger.error(
                            "普通限價平倉單降級重試失敗",
                            extra={
                                "symbol": self._symbol, "bot_id": self._bot_id,
                                "action": "place_order", "result": "error",
                                "role": role.value, "client_order_id": client_id,
                                "error": str(fallback_exc),
                            },
                        )
                        return OrderOutcome(OutcomeKind.ERROR, order=order, reason=str(fallback_exc))

            logger.error(
                "限價單送出失敗",
                extra={
                    "symbol": self._symbol, "bot_id": self._bot_id,
                    "action": "place_order", "result": "error",
                    "role": role.value, "client_order_id": client_id,
                    "error": str(exc),
                },
            )
            return OrderOutcome(OutcomeKind.ERROR, order=order, reason=str(exc))

    async def place_market(self, role: DcaOrderRole, side: str,
                           quantity: Decimal, leg: str = "long",
                           slot_index: Optional[int] = None) -> OrderOutcome:
        deferred = self._check_circuit(role)
        if deferred:
            return deferred
        _, q_qty = self._quantize(None, quantity)
        if q_qty <= 0:
            return OrderOutcome(OutcomeKind.REJECTED_MIN_NOTIONAL,
                                reason="quantized quantity <= 0")
        client_id = self._client_id(role, leg, slot_index)
        # 市價單價格以最新價估計（用途：名目檢查與記錄）
        mark = Decimal("0")
        try:
            mark = await self.last_price()
        except Exception:
            pass
        rejected = self._check_min_notional(mark if mark > 0 else Decimal("1e18"),
                                            q_qty, role)
        if rejected:
            return rejected
        order = DcaOrder(
            client_order_id=client_id, symbol=self._symbol, side=side,
            role=role, price=mark, quantity=q_qty, slot_index=slot_index,
        )
        try:
            order_side = _to_order_side(side)
            order_type = OrderType.MARKET
            is_close_order = role in (DcaOrderRole.TAKE_PROFIT, DcaOrderRole.STOP_LOSS)
            order_params = {
                "client_order_id": client_id,
                "clientOrderId": client_id,
                "newClientOrderId": client_id,
            }
            if is_close_order:
                order_params["reduce_only"] = True
                order_params["reduceOnly"] = True

            raw = await self._exchange.create_order(
                self._symbol, order_side, order_type, q_qty, None,
                params=order_params,
            )
            order.order_id = str(getattr(raw, "id", getattr(raw, "order_id", "")) or "")
            order.mark_filled(price=mark, quantity=q_qty)
            logger.info(
                "市價單已送出",
                extra={
                    "symbol": self._symbol, "bot_id": self._bot_id,
                    "action": "place_market", "result": "placed",
                    "role": role.value, "client_order_id": client_id,
                    "quantity": str(q_qty),
                },
            )
            return OrderOutcome(OutcomeKind.PLACED, order=order)
        except Exception as exc:
            logger.error(
                "市價單送出失敗",
                extra={
                    "symbol": self._symbol, "bot_id": self._bot_id,
                    "action": "place_market", "result": "error",
                    "role": role.value, "client_order_id": client_id,
                    "error": str(exc),
                },
            )
            return OrderOutcome(OutcomeKind.ERROR, order=order, reason=str(exc))

    # ── 撤單 / 查詢 ───────────────────────────────────────

    async def cancel(self, order: DcaOrder) -> bool:
        if order.order_id is None:
            return True  # 尚未掛上交易所，視為已撤
        try:
            await self._exchange.cancel_order(order.order_id, self._symbol)
            order.mark_cancelled()
            logger.info(
                "訂單已撤銷",
                extra={
                    "symbol": self._symbol, "bot_id": self._bot_id,
                    "action": "cancel_order", "result": "ok",
                    "client_order_id": order.client_order_id,
                },
            )
            return True
        except Exception as exc:
            logger.error(
                "撤單失敗",
                extra={
                    "symbol": self._symbol, "bot_id": self._bot_id,
                    "action": "cancel_order", "result": "error",
                    "client_order_id": order.client_order_id, "error": str(exc),
                },
            )
            return False

    async def fetch_open_orders(self) -> List[DcaOrder]:
        raw_orders = await self._exchange.get_open_orders(self._symbol)
        from ..models.dca_order import bot_id_hash8
        prefix = f"dca_{bot_id_hash8(self._bot_id)}_"  # INV 計數只算本 bot 命名空間
        out: List[DcaOrder] = []
        for raw in raw_orders:
            client_id = getattr(raw, "client_id", None) or getattr(
                raw, "client_order_id", None)
            if client_id and not str(client_id).startswith(prefix):
                continue  # 不屬於本 bot 命名空間（INV 計數口徑）
            out.append(self._map_raw_order(raw))
        return out

    async def fetch_order(self, client_order_id: str) -> Optional[DcaOrder]:
        """以 exchange get_order 依 client id 反查；無法找到時回傳 None。"""
        try:
            raw = await self._exchange.get_order(
                client_order_id,
                self._symbol,
                params={
                    "origClientOrderId": client_order_id,
                    "clientOrderId": client_order_id,
                    "clOrdId": client_order_id,
                },
            )
        except TypeError:
            try:
                raw = await self._exchange.get_order(client_order_id, self._symbol)
            except Exception:
                return None
        except Exception:
            return None
        if raw is None:
            return None
        return self._map_raw_order(raw)

    def _map_raw_order(self, raw) -> DcaOrder:
        client_id = getattr(raw, "client_id", None) or getattr(
            raw, "client_order_id", "") or ""
        params = getattr(raw, "params", {}) or {}
        raw_data = getattr(raw, "raw_data", {}) or {}
        if not client_id:
            client_id = (
                params.get("origClientOrderId")
                or params.get("clientOrderId")
                or raw_data.get("origClientOrderId")
                or raw_data.get("clientOrderId")
                or raw_data.get("client_order_id")
                or ""
            )
        order = DcaOrder(
            client_order_id=str(client_id),
            symbol=self._symbol,
            side=str(getattr(raw, "side", "")).lower(),
            role=DcaOrderRole.BASE,  # 角色由 coordinator 依 client_id 決定
            price=Decimal(str(getattr(raw, "price", 0) or 0)),
            quantity=Decimal(str(getattr(raw, "amount", getattr(raw, "quantity", 0)) or 0)),
        )
        order.order_id = str(getattr(raw, "id", getattr(raw, "order_id", "")) or "")
        order.status = _status_from_exchange(getattr(raw, "status", "placed"))
        filled = getattr(raw, "filled", None)
        if filled is not None:
            order.filled_quantity = Decimal(str(filled))
        avg = getattr(raw, "average", None)
        if avg is not None:
            order.average_fill_price = Decimal(str(avg))
        return order

    async def last_price(self) -> Decimal:
        ticker = await self._exchange.get_ticker(self._symbol)
        for attr in ("last", "price", "close"):
            value = getattr(ticker, attr, None)
            if value is not None:
                return Decimal(str(value))
        raise RuntimeError(f"無法從 ticker 取得最新價: {ticker!r}")

    async def fetch_position(self) -> Optional[dict]:
        """
        查詢交易所端該標的實際持倉（自癒對帳與單向持倉判定用）。
        回傳字典包含:
            size: Decimal (持倉絕對值, >= 0)
            side: "long" | "short" | "none"
            raw_amount: Decimal (帶正負號淨額)
            entry_price: Decimal
        若交易所適配器無 get_positions 方法，回傳 None。
        """
        if not hasattr(self._exchange, "get_positions"):
            return None
        try:
            positions = await self._exchange.get_positions([self._symbol])
            if not positions:
                return {
                    "size": Decimal("0"),
                    "side": "none",
                    "raw_amount": Decimal("0"),
                    "entry_price": Decimal("0"),
                }
            target_norm = self._symbol.replace("/", "").replace(":", "").upper()
            for p in positions:
                sym = getattr(p, "symbol", "")
                sym_norm = sym.replace("/", "").replace(":", "").upper()
                if sym == self._symbol or sym_norm == target_norm:
                    raw_amt = getattr(p, "amount", getattr(p, "size", 0))
                    amt_dec = Decimal(str(raw_amt or 0))
                    side_attr = getattr(p, "side", None)
                    side_str = str(getattr(side_attr, "value", side_attr or "")).lower()
                    if "long" in side_str or amt_dec > 0:
                        pos_side = "long"
                    elif "short" in side_str or amt_dec < 0:
                        pos_side = "short"
                    else:
                        pos_side = "none"
                    size_dec = abs(amt_dec)
                    entry_px = Decimal(str(getattr(p, "entry_price", 0) or 0))
                    return {
                        "size": size_dec,
                        "side": pos_side if size_dec > 0 else "none",
                        "raw_amount": amt_dec,
                        "entry_price": entry_px,
                    }
            return {
                "size": Decimal("0"),
                "side": "none",
                "raw_amount": Decimal("0"),
                "entry_price": Decimal("0"),
            }
        except Exception as exc:
            logger.warning(
                "查詢交易所持倉異常",
                extra={"symbol": self._symbol, "bot_id": self._bot_id, "error": str(exc)},
            )
            return None
