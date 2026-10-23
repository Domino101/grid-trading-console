"""交易所過濾器（tickSize / stepSize / minNotional）的即時快取與量化。

背景（2026-10-23 BRUSDT 事故）
--------------------------------
tickSize 是交易所**會變動**的契約，但舊路徑把它當成進程啟動時的常數：
```
BinanceRest.initialize() -> load_markets()   # 只做一次
run_grid_trading.auto_fill_precision()       # 用這份快取覆寫 config
grid_engine_impl._place_order_internal()     # 用 config 的 price_decimals 算 min_tick
```
BRUSDT 的 tickSize 由 0.00001 改為 0.0001 後，網格仍以 5 位小數掛單，
交易所連續 1.5 天回 `-4014 Price not increased by tick size`（6,030 次），
而機器人狀態仍是 running、交易所掛單為 0。

本模組是**唯一允許**把價格/數量轉成交易所可接受值的來源：
- 需要時機：下單前（adapter 邊界）與啟動 preflight。
- 禁止在策略層自行用 `10 ** -price_decimals` 推導 tick 後直接送單。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_DOWN
from typing import Any, Dict, Iterable, Optional, Tuple

#: 過濾器快取存活時間。交易所調整 tickSize 時，最慢這麼久會自動跟上；
#: 收到 -4014/-1111/-4024 時會強制刷新，不必等 TTL。
DEFAULT_TTL_SECONDS = 600.0


def _to_decimal(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        dec = Decimal(str(value))
    except Exception:
        return None
    return dec if dec.is_finite() and dec > 0 else None


def _decimals_from_tick(tick: Optional[Decimal]) -> Optional[int]:
    if tick is None or tick <= 0:
        return None
    exponent = tick.normalize().as_tuple().exponent
    return max(0, -int(exponent))


def _filter_value(filters: Iterable[Any], filter_type: str, *keys: str) -> Optional[Decimal]:
    for item in filters or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("filterType") or item.get("filter_type") or "") != filter_type:
            continue
        for key in keys:
            value = _to_decimal(item.get(key))
            if value is not None:
                return value
    return None


@dataclass(frozen=True)
class MarketRules:
    """單一交易對當下的交易所過濾器。"""

    symbol: str
    price_tick: Optional[Decimal]
    qty_step: Optional[Decimal]
    min_notional: Optional[Decimal]
    fetched_at: float
    source: str = "live"

    # ── 能力查詢 ────────────────────────────────────────────
    @property
    def price_decimals(self) -> Optional[int]:
        return _decimals_from_tick(self.price_tick)

    @property
    def qty_decimals(self) -> Optional[int]:
        return _decimals_from_tick(self.qty_step)

    def is_on_price_tick(self, price: Any) -> bool:
        tick = self.price_tick
        value = _to_decimal(price)
        if tick is None or value is None:
            return True
        return (value % tick) == 0

    # ── 量化 ────────────────────────────────────────────────
    def quantize_price(self, price: Any, side: Optional[str] = None) -> Optional[Decimal]:
        """把價格對齊 tickSize。

        買單向下取整（maker 更安全、不會追價），賣單向上取整
        （避免降到 best_ask 之下被 post-only 判成 -5022）。
        """
        value = _to_decimal(price)
        tick = self.price_tick
        if value is None or tick is None:
            return value
        rounding = ROUND_CEILING if str(side or "").lower() in ("sell", "short") else ROUND_DOWN
        steps = (value / tick).to_integral_value(rounding=rounding)
        if steps <= 0:
            steps = Decimal(1)
        return steps * tick

    def quantize_qty(self, quantity: Any) -> Optional[Decimal]:
        value = _to_decimal(quantity)
        step = self.qty_step
        if value is None or step is None:
            return value
        steps = (value / step).to_integral_value(rounding=ROUND_DOWN)
        return steps * step

    def describe(self) -> str:
        return (
            f"{self.symbol} tick={self.price_tick} step={self.qty_step} "
            f"minNotional={self.min_notional} fetched_at={self.fetched_at:.0f}"
        )


def _lookup_market(exchange: Any, symbol: str) -> Optional[Dict[str, Any]]:
    markets = getattr(exchange, "markets", None) or {}
    if not isinstance(markets, dict):
        return None
    market = markets.get(symbol)
    if isinstance(market, dict):
        return market
    try:
        market = exchange.market(symbol)
        if isinstance(market, dict):
            return market
    except Exception:
        pass
    # 以交易所原生 id 反查（BR/USDT:USDT <-> BRUSDT）
    clean = str(symbol).upper().split(":")[0].replace("/", "").replace("-", "").replace("_", "")
    for candidate in markets.values():
        if not isinstance(candidate, dict):
            continue
        if str(candidate.get("id") or "").upper() == clean:
            return candidate
        if str(candidate.get("symbol") or "").upper().split(":")[0] == str(symbol).upper().split(":")[0]:
            return candidate
    return None


def rules_from_market(symbol: str, market: Dict[str, Any], *, fetched_at: Optional[float] = None) -> MarketRules:
    """從 ccxt market dict 解析過濾器；優先使用原始 filters 字串（精確 Decimal）。"""
    info = market.get("info") if isinstance(market.get("info"), dict) else {}
    filters = info.get("filters") or []
    if not filters and isinstance(market.get("info"), dict):
        filters = market["info"].get("filterList") or []

    price_tick = _filter_value(filters, "PRICE_FILTER", "tickSize", "tick_size")
    qty_step = _filter_value(filters, "LOT_SIZE", "stepSize", "step_size")
    min_notional = _filter_value(filters, "MIN_NOTIONAL", "notional", "minNotional")

    precision = market.get("precision") if isinstance(market.get("precision"), dict) else {}
    if price_tick is None:
        fallback_price = _to_decimal(precision.get("price"))
        # ccxt 在 TICK_SIZE 模式回傳 tick（通常 < 1）；在 DECIMAL_PLACES 模式
        # 回傳「小數位數」整數，兩者無法安全區分 → 寧可不量化，也不要算錯。
        if fallback_price is not None and fallback_price < 1:
            price_tick = fallback_price
    if qty_step is None:
        fallback_qty = _to_decimal(precision.get("amount"))
        if fallback_qty is not None and fallback_qty < 1:
            qty_step = fallback_qty
    if min_notional is None:
        limits = market.get("limits") if isinstance(market.get("limits"), dict) else {}
        cost = limits.get("cost") if isinstance(limits.get("cost"), dict) else {}
        min_notional = _to_decimal(cost.get("min"))

    return MarketRules(
        symbol=str(symbol),
        price_tick=price_tick,
        qty_step=qty_step,
        min_notional=min_notional,
        fetched_at=fetched_at if fetched_at is not None else time.time(),
    )


class MarketRulesCache:
    """per-(exchange, symbol) 的過濾器快取；過期即 reload_markets。"""

    def __init__(self, ttl_seconds: float = DEFAULT_TTL_SECONDS):
        self._ttl = float(ttl_seconds)
        self._entries: Dict[Tuple[int, str], MarketRules] = {}
        self._last_attempt: Dict[Tuple[int, str], float] = {}
        self._lock = threading.RLock()
        self.refresh_count = 0

    @staticmethod
    def _key(exchange: Any, symbol: str) -> Tuple[int, str]:
        return (id(exchange), str(symbol))

    def get(self, exchange: Any, symbol: str, *, force_refresh: bool = False) -> Optional[MarketRules]:
        key = self._key(exchange, symbol)
        now = time.time()
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and not force_refresh and (now - entry.fetched_at) < self._ttl:
                return entry
        return self._refresh(exchange, symbol, force=force_refresh)

    def _refresh(self, exchange: Any, symbol: str, *, force: bool) -> Optional[MarketRules]:
        key = self._key(exchange, symbol)
        with self._lock:
            now = time.time()
            entry = self._entries.get(key)
            if not force and entry is not None and (now - entry.fetched_at) < self._ttl:
                return entry
            if not force and now - self._last_attempt.get(key, 0.0) < 1.0:
                # 1 秒內重複刷新（多單並發）直接沿用現值，避免打爆 REST；
                # force_refresh（-4014 自癒）不受此限，否則修不好就永遠是舊 tick。
                return entry
            self._last_attempt[key] = now

        try:
            exchange.load_markets(True)
            self.refresh_count += 1
        except TypeError:
            try:
                exchange.load_markets()
                self.refresh_count += 1
            except Exception:
                pass
        except Exception:
            pass

        market = _lookup_market(exchange, symbol)
        if market is None:
            with self._lock:
                return self._entries.get(key)
        rules = rules_from_market(symbol, market)
        with self._lock:
            self._entries[key] = rules
        return rules

    def invalidate(self, exchange: Any, symbol: Optional[str] = None) -> None:
        with self._lock:
            if symbol is None:
                prefix = id(exchange)
                for key in [k for k in self._entries if k[0] == prefix]:
                    self._entries.pop(key, None)
                    self._last_attempt.pop(key, None)
                return
            key = self._key(exchange, symbol)
            self._entries.pop(key, None)
            self._last_attempt.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._last_attempt.clear()


#: 進程內共享快取（網格與 DCA 共用同一份真相）
market_rules_cache = MarketRulesCache()


def resolve_ccxt_exchange(candidate: Any) -> Any:
    """從 adapter 包裝物件找出真正的 ccxt 實例。

    BinanceAdapter 的 ccxt 在 `adapter.rest.exchange`，沒有 `adapter.exchange`；
    舊的 auto_fill_precision 只認 `.exchange`，因此在 Binance 上一直靜默失效。
    """
    if candidate is None:
        return None
    if hasattr(candidate, "load_markets") and hasattr(candidate, "markets"):
        return candidate
    for attr in ("exchange", "rest", "_rest", "_ccxt_exchange"):
        try:
            obj = getattr(candidate, attr, None)
        except Exception:
            obj = None
        if obj is None:
            continue
        if hasattr(obj, "load_markets") and hasattr(obj, "markets"):
            return obj
        inner = getattr(obj, "exchange", None)
        if inner is not None and hasattr(inner, "load_markets"):
            return inner
    return None


async def aget_rules(exchange: Any, symbol: str, *, force_refresh: bool = False) -> Optional[MarketRules]:
    """async 包裝：load_markets 是阻塞呼叫，不可直接在 event loop 上跑。"""
    import asyncio
    import functools

    exchange = resolve_ccxt_exchange(exchange)
    if exchange is None:
        return None
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        functools.partial(
            market_rules_cache.get, exchange, symbol, force_refresh=force_refresh
        ),
    )


def normalize_grid_config(config: Any, rules: MarketRules, logger: Any = None) -> list[str]:
    """把網格 config 的精度/間隔對齊交易所當下的 tick/step。

    回傳警告訊息清單（給呼叫端寫入日誌與 lifecycle）。任何修改都會被記錄，
    不允許靜默改寫使用者配置。
    """
    warnings: list[str] = []
    tick = rules.price_tick
    step = rules.qty_step

    if tick is not None:
        live_decimals = rules.price_decimals
        current = getattr(config, "price_decimals", None)
        if live_decimals is not None and current != live_decimals:
            warnings.append(
                f"price_decimals {current} → {live_decimals}（交易所 tick={tick}）"
            )
            config.price_decimals = live_decimals

        # 下限/上限本身也必須在 tick 上
        for field in ("lower_price", "upper_price"):
            value = getattr(config, field, None)
            if value is not None and not rules.is_on_price_tick(value):
                snapped = rules.quantize_price(value)
                warnings.append(f"{field} {value} → {snapped}（對齊 tick={tick}）")
                setattr(config, field, snapped)

        interval = getattr(config, "grid_interval", None)
        if interval is not None and interval > 0 and not rules.is_on_price_tick(interval):
            snapped = rules.quantize_price(interval)
            if snapped is None or snapped <= 0:
                snapped = tick
            warnings.append(f"grid_interval {interval} → {snapped}（對齊 tick={tick}）")
            config.grid_interval = snapped
            try:
                price_range = abs(config.upper_price - config.lower_price)
                config.grid_count = int(price_range / snapped)
            except Exception:
                pass

    if step is not None:
        amount = getattr(config, "order_amount", None)
        snapped_amount = rules.quantize_qty(amount)
        if snapped_amount is not None and amount is not None and snapped_amount != amount:
            warnings.append(f"order_amount {amount} → {snapped_amount}（交易所 step={step}）")
            config.order_amount = snapped_amount
        live_qty_decimals = rules.qty_decimals
        current_qty = getattr(config, "quantity_precision", None)
        if live_qty_decimals is not None and current_qty != live_qty_decimals:
            warnings.append(
                f"quantity_precision {current_qty} → {live_qty_decimals}（交易所 step={step}）"
            )
            config.quantity_precision = live_qty_decimals

    # 最後硬驗證：所有會送出的網格價都必須在 tick 上
    if tick is not None and hasattr(config, "get_grid_price"):
        try:
            for index in range(1, int(config.grid_count) + 1):
                price = config.get_grid_price(index)
                if not rules.is_on_price_tick(price):
                    raise ValueError(
                        f"grid {index} price {price} 不在交易所 tick {tick} 上；"
                        f"請調整 lower_price/grid_interval（config={getattr(config, 'lower_price', '?')}.."
                        f"{getattr(config, 'upper_price', '?')} interval={getattr(config, 'grid_interval', '?')}）"
                    )
        except ValueError:
            raise
    return warnings
