"""
DCA 安全單階梯數學（§4.5 凍結公式）

所有運算使用 Decimal（prec ≥ 28，localcontext 隔離，不污染全域精度）。
本模組是純函式：輸入 DcaConfig + 入場價，輸出階梯；不碰任何外部狀態。
後端 POST /preview 與引擎實際下單共用本模組（前後端數值一致的結構保證，G-4）。

凍結公式（§4.5，向量 A/B 已由兩份獨立實作交叉驗證鎖定）：

    步進偏移    dev_i = b × m^(i-1)                    （i = 1..N）
    安全單價格  order:    P_i = P_{i-1} × (1 ∓ dev_i)   （P_0 = E）
                position: P_i = avg_{i-1} × (1 ∓ dev_i) （avg_0 = E）
    下單量      fixed_value: V_i = V₀ × s^i;      Q_i = V_i / P_i
                fixed_coin:  Q_i = (V₀/E) × s^i;  V_i = Q_i × P_i
    均價        avg_k = ΣV_j / ΣQ_j
    止盈價      avg × (1 ± tp/100)
    所需資金    required = Σ_{j=0..N} V_j
    最大回撤    (E − P_N)/E × 100（做空相反）

偏移符號：做多用 (1 − dev) / 做空用 (1 + dev)；做空階梯為做多的逐步鏡像。
amount_unit=base 時，base_order_amount 為幣數，先換算成名目 V₀×E 再套用公式。
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_EVEN, localcontext
from typing import List, Optional, Tuple

from .models.dca_config import (
    AveragingMethod,
    DcaConfig,
    DcaDirection,
    DcaMode,
)

# §4.5 向量交叉驗證所用精度下限（實作要點：prec ≥ 28）
DECIMAL_PRECISION = 28

_BASE_INDEX = 0


def _to_decimal(value) -> Decimal:
    """把 DecimalLike 安全轉為 Decimal（經 str 避免 binary float 殘差）。"""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def resolve_direction(config: DcaConfig, direction: Optional[str] = None) -> str:
    """
    決定本輪階梯的計算方向。

    direction=both 時必須由呼叫方指定實際方向（決策 D-2：單方向互斥，
    兩方向階梯應各自由 build_ladder() 計算後於 UI 併列展示）。
    """
    resolved = direction or (
        config.direction.value if config.direction is not DcaDirection.BOTH else None
    )
    if resolved not in ("long", "short"):
        raise ValueError(
            "build_ladder 需要明確方向（long/short）；direction=both 時請分別試算兩個方向"
        )
    return resolved


def quantize_price(price: Decimal, price_decimals: Optional[int]) -> Decimal:
    """價格精度：ROUND_HALF_EVEN（實作要點）；price_decimals=None 時原樣回傳。"""
    if price_decimals is None:
        return price
    return price.quantize(
        Decimal(1).scaleb(-price_decimals), rounding=ROUND_HALF_EVEN
    )


def quantize_quantity(quantity: Decimal, quantity_precision: Optional[int]) -> Decimal:
    """數量精度：ROUND_DOWN（絕不得超出可成交數量）；未設定時原樣回傳。"""
    if quantity_precision is None:
        return quantity
    return quantity.quantize(
        Decimal(1).scaleb(-quantity_precision), rounding=ROUND_DOWN
    )


@dataclass(frozen=True)
class LadderLevel:
    """階梯單層（index=0 為基礎單，1..N 為安全單）。"""
    index: int
    deviation_percent: Decimal   # dev_i × 100（基礎單為 0）
    price: Decimal               # P_i
    notional: Decimal            # V_i（名目金額，無論 amount_unit 皆已換算為報價幣）
    quantity: Decimal            # Q_i
    avg_price: Decimal           # 截至本層的攤平均價 avg_i

    def take_profit_price(self, take_profit_percent: Decimal, direction: str) -> Decimal:
        """截至本層的止盈價（基準 = 本層攤平均價）。"""
        return take_profit_price(self.avg_price, take_profit_percent, direction)


@dataclass(frozen=True)
class LadderPlan:
    """完整階梯試算結果（levels 含基礎單 index=0）。"""
    direction: str
    entry_price: Decimal
    levels: Tuple[LadderLevel, ...]

    @property
    def required_capital(self) -> Decimal:
        """所需資金 = Σ V_j（§4.5；含基礎單）。"""
        with localcontext() as ctx:
            ctx.prec = DECIMAL_PRECISION
            total = Decimal(0)
            for level in self.levels:
                total += level.notional
            return +total

    @property
    def max_drawdown_percent(self) -> Decimal:
        """最大覆蓋跌幅/漲幅 = (E − P_N)/E × 100（做空相反）。"""
        with localcontext() as ctx:
            ctx.prec = DECIMAL_PRECISION
            last_price = self.levels[-1].price
            if self.direction == "long":
                return +(self.entry_price - last_price) / self.entry_price * 100
            return +(last_price - self.entry_price) / self.entry_price * 100

    @property
    def final_average_price(self) -> Decimal:
        return self.levels[-1].avg_price

    def summarize(self, take_profit_percent: Decimal) -> dict:
        """階梯摘要（供 API preview 使用；全部 Decimal，由 API 層序列化）。"""
        return {
            "direction": self.direction,
            "entry_price": self.entry_price,
            "required_capital": self.required_capital,
            "max_drawdown_percent": self.max_drawdown_percent,
            "final_average_price": self.final_average_price,
            "final_take_profit_price": take_profit_price(
                self.final_average_price, take_profit_percent, self.direction
            ),
            "levels": self.levels,
        }


def build_ladder(config: DcaConfig, entry_price,
                 direction: Optional[str] = None) -> LadderPlan:
    """
    依 DcaConfig 與入場價 E 建構完整邏輯階梯（N = max_safety_orders）。

    回傳 levels 長度為 N+1（含基礎單）；實際同時掛在交易所的張數由
    active_safety_orders_window 於執行期另行管理（決策 D-4）。
    """
    resolved = resolve_direction(config, direction)
    with localcontext() as ctx:
        ctx.prec = DECIMAL_PRECISION

        e = _to_decimal(entry_price)
        if e <= 0:
            raise ValueError(f"entry_price 必須大於 0，收到: {entry_price!r}")

        b = config.price_deviation_percent / Decimal(100)
        m = config.price_deviation_multiplier
        s = config.order_size_multiplier
        n_max = config.max_safety_orders

        # amount_unit=base 時先換算成名目金額（§4.5 公式以 V₀ 為名目）
        v0 = config.base_order_amount
        if config.amount_unit == "base":
            v0 = config.base_order_amount * e

        sign = Decimal(1) if resolved == "long" else Decimal(-1)

        # ── 基礎單（index=0）；兩種攤平方式在第 0 層等價（Q₀ = V₀/E）──
        base_v = v0
        base_q = v0 / e
        cum_v = base_v
        cum_q = base_q
        avg = cum_v / cum_q
        levels: List[LadderLevel] = [
            LadderLevel(
                index=_BASE_INDEX,
                deviation_percent=Decimal(0),
                price=e,
                notional=base_v,
                quantity=base_q,
                avg_price=+avg,
            )
        ]

        prev_price = e
        prev_avg = avg
        for i in range(1, n_max + 1):
            dev = b * m ** (i - 1)
            anchor = prev_price if config.dca_mode is DcaMode.ORDER else prev_avg
            price = anchor * (Decimal(1) - sign * dev)

            if config.averaging_method is AveragingMethod.FIXED_VALUE:
                notional = v0 * s ** i
                quantity = notional / price
            else:  # FIXED_COIN
                quantity = (v0 / e) * s ** i
                notional = quantity * price

            cum_v += notional
            cum_q += quantity
            avg = cum_v / cum_q

            levels.append(
                LadderLevel(
                    index=i,
                    deviation_percent=+(dev * 100),
                    price=+price,
                    notional=+notional,
                    quantity=+quantity,
                    avg_price=+avg,
                )
            )
            prev_price = price
            prev_avg = avg

        return LadderPlan(direction=resolved, entry_price=e, levels=tuple(levels))


def take_profit_price(avg_price, take_profit_percent, direction: str = "long") -> Decimal:
    """
    止盈價 = avg × (1 ± tp/100)（§4.5；基準為攤平後均價）。
    direction=short 時止盈價在均價下方。avg_price ≤ 0 視為未知 → 拋錯而非回傳 0（G-5）。
    """
    if direction not in ("long", "short"):
        raise ValueError(f"無效的方向: {direction!r}，合法值: long, short")
    with localcontext() as ctx:
        ctx.prec = DECIMAL_PRECISION
        avg = _to_decimal(avg_price)
        if avg <= 0:
            raise ValueError(f"avg_price 必須大於 0，收到: {avg_price!r}")
        tp = _to_decimal(take_profit_percent) / Decimal(100)
        if direction == "long":
            return +(avg * (Decimal(1) + tp))
        return +(avg * (Decimal(1) - tp))


def stop_loss_price(avg_price, stop_loss_percent, direction: str = "long") -> Decimal:
    """止損價 = avg × (1 ∓ sl/100)（§4.2-D；以最終均價為基準）。"""
    if direction not in ("long", "short"):
        raise ValueError(f"無效的方向: {direction!r}，合法值: long, short")
    with localcontext() as ctx:
        ctx.prec = DECIMAL_PRECISION
        avg = _to_decimal(avg_price)
        if avg <= 0:
            raise ValueError(f"avg_price 必須大於 0，收到: {avg_price!r}")
        sl = _to_decimal(stop_loss_percent) / Decimal(100)
        if direction == "long":
            return +(avg * (Decimal(1) - sl))
        return +(avg * (Decimal(1) + sl))


def required_capital(config: DcaConfig, entry_price,
                     direction: Optional[str] = None) -> Decimal:
    """
    所需資金試算（§4.4 資金試算）。

    direction=both 時回傳 max(long 階梯資金, short 階梯資金)——
    決策 D-2：兩方向不會同時發生，因此取最大值而非相加。
    """
    if direction is None and config.direction is DcaDirection.BOTH:
        plan_long = build_ladder(config, entry_price, "long")
        plan_short = build_ladder(config, entry_price, "short")
        return max(plan_long.required_capital, plan_short.required_capital)
    return build_ladder(config, entry_price, direction).required_capital
