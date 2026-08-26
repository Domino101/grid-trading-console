"""In-process paper exchange: public ticker in, simulated fills, zero private orders."""

from __future__ import annotations

import asyncio
import hashlib
import os
import random as _random
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional

from ..interface import ExchangeConfig, ExchangeInterface, ExchangeStatus
from ..models import (
    BalanceData,
    ExchangeInfo,
    ExchangeType,
    MarginMode,
    OHLCVData,
    OrderBookData,
    OrderBookLevel,
    OrderData,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionData,
    PositionSide,
    TickerData,
    TradeData,
)


def is_paper_config(config_data: dict) -> bool:
    grid = (config_data or {}).get("grid_system") or {}
    mode = str(grid.get("account_mode") or grid.get("trading_mode") or "").lower()
    return mode == "paper"


def _as_decimal(value: Any, default: str = "0") -> Decimal:
    if value is None or value == "":
        return Decimal(default)
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _quote_currency(symbol: str) -> str:
    main = symbol.split(":")[0]
    if "/" in main:
        return main.split("/")[-1]
    if "-" in main:
        return main.split("-")[-1]
    return "USDT"


def _base_currency(symbol: str) -> str:
    main = symbol.split(":")[0]
    if "/" in main:
        return main.split("/")[0]
    if "-" in main:
        return main.split("-")[0]
    return main


TIMEFRAME_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}


def _stable_symbol_seed(symbol: str) -> int:
    """跨進程穩定的 symbol seed（不使用會隨 PYTHONHASHSEED 變動的 hex()）。"""
    digest = hashlib.sha256(symbol.encode("utf-8")).hexdigest()
    return int(digest[:16], 16)


def _synthetic_base_timestamp(timeframe: str) -> int:
    """固定基準時間戳，使同 seed 的合成序列與時間戳均可重現。"""
    step = TIMEFRAME_SECONDS.get(str(timeframe).lower(), 900)
    # 以 UTC 2023-11-14 作為基準，避免呼叫時間影響結果
    return 1_700_000_000 // step * step


class PaperExchangeAdapter(ExchangeInterface):
    """Simulated venue used only when YAML account_mode is paper.

    Public market data may be read. create_order / cancel never touch a real
    exchange. Each instance is one isolated paper account.
    """

    def __init__(
        self,
        config: ExchangeConfig,
        ticker_provider: Optional[Callable[[str], Decimal]] = None,
        starting_equity: Optional[Decimal] = None,
    ):
        super().__init__(config)
        self.exchange = None  # auto_fill_precision looks for ccxt; stay None
        self._ticker_provider = ticker_provider
        env_equity = os.getenv("PAPER_STARTING_EQUITY", "10000")
        self._starting_equity = _as_decimal(starting_equity if starting_equity is not None else env_equity, "10000")
        self._cash = self._starting_equity
        self._fee_rate = _as_decimal(os.getenv("PAPER_FEE_RATE", "0.0002"), "0.0002")
        self._last: Dict[str, Decimal] = {}
        self._orders: Dict[str, OrderData] = {}
        self._all_orders: Dict[str, OrderData] = {}
        self._position_size = Decimal("0")
        self._entry_price = Decimal("0")
        self._realized = Decimal("0")
        self._fees = Decimal("0")
        self._volume = Decimal("0")
        self._user_callbacks: List[Callable] = []
        self._ticker_callbacks: Dict[str, List[Callable]] = {}
        self._poll_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._position_cache: Dict[str, dict] = {}
        self._ws_connected = True
        self._last_heartbeat = datetime.now()
        self.is_user_stream_connected = True

    def _map_symbol(self, symbol: str) -> str:
        return symbol

    def get_cached_ticker(self, symbol: str) -> Optional[TickerData]:
        price = self._last.get(symbol)
        if price is None:
            return None
        return self._ticker_from_price(symbol, price)

    def get_cached_orderbook(self, symbol: str) -> Optional[OrderBookData]:
        price = self._last.get(symbol)
        if price is None:
            return None
        tick = Decimal("0.01")
        return OrderBookData(
            symbol=symbol,
            bids=[OrderBookLevel(price=price - tick, size=Decimal("1"))],
            asks=[OrderBookLevel(price=price + tick, size=Decimal("1"))],
            timestamp=datetime.now(),
            nonce=None,
        )

    def set_last_price(self, symbol: str, price: Decimal) -> List[OrderData]:
        """Test/helper hook: set the mark and match resting limits."""
        last = _as_decimal(price)
        self._last[symbol] = last
        self._refresh_position_cache(symbol, last)
        return self._match_orders(symbol, last)

    async def connect(self) -> bool:
        self.status = ExchangeStatus.AUTHENTICATED
        self._ws_connected = True
        self._last_heartbeat = datetime.now()
        if self._poll_task is None or self._poll_task.done():
            self._poll_task = asyncio.create_task(self._poll_loop())
        return True

    async def disconnect(self) -> None:
        self.status = ExchangeStatus.DISCONNECTED
        self._ws_connected = False
        if self._poll_task:
            self._poll_task.cancel()
            self._poll_task = None

    async def authenticate(self) -> bool:
        self.status = ExchangeStatus.AUTHENTICATED
        return True

    async def health_check(self) -> Dict[str, Any]:
        return {"status": "paper", "exchange_id": "paper", "private_api": False}

    async def get_exchange_info(self) -> ExchangeInfo:
        return ExchangeInfo(
            name="Paper",
            id="paper",
            type=self.config.exchange_type,
            supported_features=["paper_trading"],
            rate_limits={},
            precision={},
            fees={"taker": float(self._fee_rate)},
            markets={},
            status="ok",
            timestamp=datetime.now(),
        )

    def _resolve_price(self, symbol: str) -> Decimal:
        if self._ticker_provider is not None:
            return _as_decimal(self._ticker_provider(symbol))
        if symbol in self._last:
            return self._last[symbol]
        try:
            import ccxt
            exchange_id = (self.config.extra_params or {}).get("venue") or "binance"
            klass = getattr(ccxt, str(exchange_id).lower(), None)
            if klass is None:
                raise RuntimeError("unsupported venue")
            client = klass({"enableRateLimit": True, "options": {"defaultType": "swap"}})
            ticker = client.fetch_ticker(symbol)
            return _as_decimal(ticker.get("last") or ticker.get("close"))
        except Exception as exc:
            if symbol in self._last:
                return self._last[symbol]
            raise RuntimeError(f"PAPER 無法取得公開行情 {symbol}: {exc}") from exc

    def _ticker_from_price(self, symbol: str, price: Decimal) -> TickerData:
        tick = Decimal("0.01")
        return TickerData(
            symbol=symbol,
            timestamp=datetime.now(),
            last=price,
            bid=price - tick,
            ask=price + tick,
            close=price,
            mark_price=price,
        )

    async def get_ticker(self, symbol: str) -> TickerData:
        price = self._resolve_price(symbol)
        self._last[symbol] = price
        filled = self._match_orders(symbol, price)
        await self._emit_fills(filled)
        self._refresh_position_cache(symbol, price)
        return self._ticker_from_price(symbol, price)

    async def get_tickers(self, symbols: Optional[List[str]] = None) -> List[TickerData]:
        targets = symbols or list(self._last.keys())
        return [await self.get_ticker(symbol) for symbol in targets]

    async def get_orderbook(self, symbol: str, limit: Optional[int] = None) -> OrderBookData:
        ticker = await self.get_ticker(symbol)
        cached = self.get_cached_orderbook(symbol)
        if cached:
            return cached
        price = ticker.last or Decimal("0")
        return OrderBookData(
            symbol=symbol,
            bids=[OrderBookLevel(price=price, size=Decimal("1"))],
            asks=[OrderBookLevel(price=price, size=Decimal("1"))],
            timestamp=datetime.now(),
            nonce=None,
        )

    async def get_ohlcv(self, symbol: str, timeframe: str, since=None, limit=None) -> List[OHLCVData]:
        """PAPER 合成 K 線：以 symbol 字串穩定 hash 為 seed 的確定性隨機遊走。

        同 symbol 兩次呼叫結果完全相同（稽核 3.10），資料標記
        ``raw_data={'synthetic': True}``（稽核 3.11）。不依賴真實行情，
        也絕不誤認為真實歷史。
        """
        count = int(limit or 300)
        seed = _stable_symbol_seed(symbol)
        rng = _random.Random(seed)
        # 起始價由 seed 決定：50～600 之間的可重現起點
        price = Decimal(str(round(50.0 + (seed % 50000) / 100.0, 4)))
        base_ts = _synthetic_base_timestamp(timeframe)
        step_seconds = TIMEFRAME_SECONDS.get(str(timeframe).lower(), 900)
        out: List[OHLCVData] = []
        for i in range(count):
            open_price = price
            step = Decimal(str(rng.gauss(0, 0.004)))
            close_price = (price * (1 + step)).quantize(Decimal("0.0001"))
            high = (max(open_price, close_price) * (1 + abs(Decimal(str(rng.gauss(0, 0.001)))))).quantize(Decimal("0.0001"))
            low = (min(open_price, close_price) * (1 - abs(Decimal(str(rng.gauss(0, 0.001)))))).quantize(Decimal("0.0001"))
            volume = Decimal(str(round(rng.uniform(10, 1000), 4)))
            ts = datetime.fromtimestamp(base_ts + i * step_seconds, tz=timezone.utc)
            out.append(OHLCVData(
                symbol=symbol,
                timeframe=str(timeframe),
                timestamp=ts,
                open=open_price,
                high=high,
                low=low,
                close=close_price,
                volume=volume,
                quote_volume=volume * close_price,
                trades_count=rng.randint(10, 500),
                raw_data={"synthetic": True, "venue": "paper"},
            ))
            price = close_price
        return out

    async def get_trades(self, symbol: str, since=None, limit=None) -> List[TradeData]:
        return []

    async def get_balances(self) -> List[BalanceData]:
        mark = next(iter(self._last.values()), Decimal("0"))
        notional = abs(self._position_size) * mark
        equity = self._cash + self._unrealized(mark)
        quote = "USDT"
        return [
            BalanceData(
                currency=quote,
                free=self._cash,
                used=notional,
                total=equity,
                usd_value=equity,
                timestamp=datetime.now(),
                raw_data={"paper": True},
            )
        ]

    def _unrealized(self, mark: Decimal) -> Decimal:
        if self._position_size == 0 or self._entry_price == 0:
            return Decimal("0")
        return (mark - self._entry_price) * self._position_size

    def _refresh_position_cache(self, symbol: str, mark: Decimal) -> None:
        self._position_cache[symbol] = {
            "size": self._position_size,
            "entry_price": self._entry_price,
            "unrealized_pnl": self._unrealized(mark),
            "timestamp": datetime.now(),
        }

    async def get_positions(self, symbols: Optional[List[str]] = None) -> List[PositionData]:
        symbol = (symbols or [next(iter(self._last.keys()), "UNKNOWN")])[0]
        mark = self._last.get(symbol, Decimal("0"))
        side = PositionSide.LONG if self._position_size >= 0 else PositionSide.SHORT
        return [
            PositionData(
                symbol=symbol,
                side=side,
                size=self._position_size,
                entry_price=self._entry_price,
                mark_price=mark,
                current_price=mark,
                unrealized_pnl=self._unrealized(mark),
                realized_pnl=self._realized,
                percentage=None,
                leverage=self.config.default_leverage,
                margin_mode=self.config.default_margin_mode,  # type: ignore[arg-type]
                margin=Decimal("0"),
                liquidation_price=None,
                timestamp=datetime.now(),
                raw_data={"paper": True},
            )
        ]

    async def create_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        amount: Decimal,
        price: Optional[Decimal] = None,
        params: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> OrderData:
        async with self._lock:
            if symbol not in self._last:
                self._last[symbol] = self._resolve_price(symbol)
            order_id = f"paper-{uuid.uuid4().hex[:12]}"
            client_id = None
            if params:
                client_id = (params.get("clientOrderId")
                             or params.get("client_order_id")
                             or params.get("client_id"))
            qty = _as_decimal(amount)
            limit = _as_decimal(price) if price is not None else self._last[symbol]
            now = datetime.now()
            order = OrderData(
                id=order_id,
                client_id=str(client_id) if client_id else order_id,
                symbol=symbol,
                side=side,
                type=order_type,
                amount=qty,
                price=limit,
                filled=Decimal("0"),
                remaining=qty,
                cost=Decimal("0"),
                average=None,
                status=OrderStatus.OPEN,
                timestamp=now,
                updated=now,
                fee=None,
                trades=[],
                params=params or {},
                raw_data={"paper": True},
            )
            self._all_orders[order_id] = order
            if order.client_id and order.client_id != order_id:
                self._all_orders[order.client_id] = order
            if order_type == OrderType.MARKET:
                self._fill(order, self._last[symbol])
            else:
                self._orders[order_id] = order
                crossed = self._should_fill(order, self._last[symbol])
                if crossed:
                    self._fill(order, order.price or self._last[symbol])
                    self._orders.pop(order_id, None)
            self._refresh_position_cache(symbol, self._last[symbol])
        if order.status == OrderStatus.FILLED:
            await self._emit_fills([order])
        return order

    def _should_fill(self, order: OrderData, last: Decimal) -> bool:
        if order.price is None:
            return True
        if order.side == OrderSide.BUY:
            return last <= order.price
        return last >= order.price

    def _fill(self, order: OrderData, fill_price: Decimal) -> None:
        qty = order.remaining if order.remaining > 0 else order.amount
        fee = (qty * fill_price) * self._fee_rate
        self._fees += fee
        self._volume += qty * fill_price
        signed = qty if order.side == OrderSide.BUY else -qty
        self._apply_fill(signed, fill_price)
        self._cash -= fee
        if order.side == OrderSide.BUY:
            self._cash -= qty * fill_price
        else:
            self._cash += qty * fill_price
        order.filled = order.amount
        order.remaining = Decimal("0")
        order.average = fill_price
        order.cost = qty * fill_price
        order.status = OrderStatus.FILLED
        order.updated = datetime.now()
        order.fee = {"cost": fee, "currency": "USDT"}

    def _apply_fill(self, signed_qty: Decimal, price: Decimal) -> None:
        if self._position_size == 0 or (self._position_size > 0) == (signed_qty > 0):
            new_size = self._position_size + signed_qty
            if new_size == 0:
                self._position_size = Decimal("0")
                self._entry_price = Decimal("0")
                return
            total_cost = abs(self._position_size) * self._entry_price + abs(signed_qty) * price
            self._position_size = new_size
            self._entry_price = total_cost / abs(new_size)
            return
        closing = min(abs(signed_qty), abs(self._position_size))
        direction = Decimal("1") if self._position_size > 0 else Decimal("-1")
        self._realized += (price - self._entry_price) * closing * direction
        remaining = abs(self._position_size) - closing
        leftover = abs(signed_qty) - closing
        if remaining == 0 and leftover == 0:
            self._position_size = Decimal("0")
            self._entry_price = Decimal("0")
        elif remaining == 0:
            self._position_size = leftover if signed_qty > 0 else -leftover
            self._entry_price = price
        else:
            self._position_size = remaining if self._position_size > 0 else -remaining

    def _match_orders(self, symbol: str, last: Decimal) -> List[OrderData]:
        filled: List[OrderData] = []
        for order_id, order in list(self._orders.items()):
            if order.symbol != symbol or order.status != OrderStatus.OPEN:
                continue
            if self._should_fill(order, last):
                self._fill(order, order.price or last)
                self._orders.pop(order_id, None)
                filled.append(order)
        return filled

    async def _emit_fills(self, orders: List[OrderData]) -> None:
        for order in orders:
            for callback in list(self._user_callbacks):
                try:
                    result = callback(order)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:
                    continue

    async def cancel_order(self, order_id: str, symbol: str) -> OrderData:
        order = self._orders.pop(order_id, None)
        if order is None:
            raise Exception("Unknown order sent")
        order.status = OrderStatus.CANCELED
        order.updated = datetime.now()
        return order

    async def cancel_all_orders(self, symbol: Optional[str] = None) -> List[OrderData]:
        cancelled = []
        for order_id, order in list(self._orders.items()):
            if symbol and order.symbol != symbol:
                continue
            order.status = OrderStatus.CANCELED
            order.updated = datetime.now()
            self._orders.pop(order_id, None)
            cancelled.append(order)
        return cancelled

    async def get_order(self, order_id: str, symbol: str) -> OrderData:
        order = self._all_orders.get(order_id)
        if order is None:
            raise Exception(f"paper order not found: {order_id}")
        return order

    async def get_open_orders(self, symbol: Optional[str] = None) -> List[OrderData]:
        return [
            order for order in self._orders.values()
            if order.status == OrderStatus.OPEN and (symbol is None or order.symbol == symbol)
        ]

    async def get_order_history(self, symbol=None, since=None, limit=None) -> List[OrderData]:
        return []

    async def set_leverage(self, symbol: str, leverage: int) -> Dict[str, Any]:
        return {"paper": True, "leverage": leverage}

    async def set_margin_mode(self, symbol: str, margin_mode: str) -> Dict[str, Any]:
        return {"paper": True, "margin_mode": margin_mode}

    async def subscribe_ticker(self, symbol: str, callback: Callable[[TickerData], None]) -> None:
        self._ticker_callbacks.setdefault(symbol, []).append(callback)
        if symbol not in self._last:
            try:
                self._last[symbol] = self._resolve_price(symbol)
            except Exception:
                pass
        if symbol in self._last:
            callback(self._ticker_from_price(symbol, self._last[symbol]))

    async def subscribe_orderbook(self, symbol: str, callback: Callable[[OrderBookData], None]) -> None:
        return None

    async def subscribe_trades(self, symbol: str, callback: Callable[[TradeData], None]) -> None:
        return None

    async def subscribe_user_data(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        self._user_callbacks.append(callback)
        self.is_user_stream_connected = True

    async def unsubscribe(self, symbol: Optional[str] = None) -> None:
        if symbol is None:
            self._ticker_callbacks.clear()
            return
        self._ticker_callbacks.pop(symbol, None)

    async def _poll_loop(self) -> None:
        while self.status in (ExchangeStatus.CONNECTED, ExchangeStatus.AUTHENTICATED):
            try:
                await asyncio.sleep(2)
                self._last_heartbeat = datetime.now()
                for symbol in list(self._ticker_callbacks.keys()):
                    try:
                        ticker = await self.get_ticker(symbol)
                    except Exception:
                        continue
                    for callback in list(self._ticker_callbacks.get(symbol, [])):
                        try:
                            callback(ticker)
                        except Exception:
                            continue
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(2)


def create_paper_adapter(config_data: dict, ticker_provider=None) -> PaperExchangeAdapter:
    grid = (config_data or {}).get("grid_system") or {}
    venue = str(grid.get("exchange") or "binance").lower()
    symbol = str(grid.get("symbol") or "")
    market_type = ExchangeType.SPOT if ":SPOT" in symbol.upper() else ExchangeType.PERPETUAL
    config = ExchangeConfig(
        exchange_id="paper",
        name=f"Paper/{venue}",
        exchange_type=market_type,
        api_key="",
        api_secret="",
        extra_params={"venue": venue, "paper": True},
    )
    return PaperExchangeAdapter(config, ticker_provider=ticker_provider)
