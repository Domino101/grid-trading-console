"""
DCA 機器人設定模型（§4.2 凍結欄位表）

所有金額欄位一律使用 Decimal；字串 / int 輸入會在建構時強制轉換。
__post_init__ 會收集「全部」驗證錯誤後一次拋出 DcaConfigError（繁體中文訊息），
讓 API 層能一次回報所有問題，而非逐欄位來回修正。

合法性規格來源：docs/dca_bot_implementation_plan_zh-TW.md §4.2 / §4.3 / §4.4。
交易所清單沿用 web_ui/backend/yaml_generator.py 的 valid_exchanges。
"""

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Optional, Sequence, Tuple, Union

from ..indicators.config import (
    DEFAULT_FETCH_LIMIT,
    DEFAULT_PRICE_SOURCE,
    DEFAULT_TIMEFRAME,
    VALID_FETCH_LIMIT_RANGE,
    VALID_PRICE_SOURCES,
    VALID_TIMEFRAMES,
    IndicatorConfig,
)

DecimalLike = Union[Decimal, str, int]


class DcaConfigError(ValueError):
    """DCA 設定驗證失敗（訊息已彙整所有錯誤欄位，繁體中文）。"""


class DcaDirection(Enum):
    """DCA 方向；both = 同一時刻單一方向互斥（決策 D-2，§4.4）"""
    LONG = "long"
    SHORT = "short"
    BOTH = "both"


class DcaMode(Enum):
    """DCA 階梯模式：order = 以各安全單價遞減；position = 以攤平後均價遞減"""
    ORDER = "order"
    POSITION = "position"


class AveragingMethod(Enum):
    """攤平方式：fixed_value = 固定名目金額；fixed_coin = 固定幣數"""
    FIXED_VALUE = "fixed_value"
    FIXED_COIN = "fixed_coin"


class EntryMode(Enum):
    """入場模式：immediate = 從開單開始起算；indicator = 符合特定指標"""
    IMMEDIATE = "immediate"
    INDICATOR = "indicator"


VALID_EXCHANGES: Tuple[str, ...] = ("binance", "lighter", "backpack", "hyperliquid")

# 各交易所做空能力表（DCA v1 為合約策略；one-way 帳戶即可，見決策 D-2）。
# 未列入的交易所僅允許 direction=long。
SHORT_CAPABLE_EXCHANGES = frozenset(VALID_EXCHANGES)

ENTRY_LOGIC_CHOICES: Tuple[str, ...] = ("all", "any")
AMOUNT_UNIT_CHOICES: Tuple[str, ...] = ("quote", "base")
MARGIN_MODE_CHOICES: Tuple[str, ...] = ("cross", "isolated")
ACCOUNT_MODE_CHOICES: Tuple[str, ...] = ("paper", "live")
TAKE_PROFIT_ORDER_TYPE_CHOICES: Tuple[str, ...] = ("limit", "market")
BOTH_TIEBREAK_CHOICES: Tuple[str, ...] = ("long_first", "short_first")


def _coerce_decimal(value: DecimalLike, name: str, errors: list) -> Decimal:
    """把 str/int/Decimal 強制轉為 Decimal；失敗時登記中文錯誤並回傳原值。"""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):  # bool 是 int 子類，需優先排除
        errors.append(f"{name} 必須是數值，收到布林值: {value}")
        return Decimal(0)
    if isinstance(value, (int, str)):
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError):
            errors.append(f"{name} 必須是數值，收到: {value!r}")
            return Decimal(0)
    errors.append(f"{name} 必須是數值（Decimal/str/int），收到型別: {type(value).__name__}")
    return Decimal(0)


def _coerce_enum(enum_cls, value, name: str, errors: list):
    """允許以字串指定 enum；失敗時登記中文錯誤並回傳 enum 首個成員。"""
    if isinstance(value, enum_cls):
        return value
    if isinstance(value, str):
        try:
            return enum_cls(value)
        except ValueError:
            pass
    valid = ", ".join(member.value for member in enum_cls)
    errors.append(f"無效的 {name}: {value!r}，合法值: {valid}")
    return next(iter(enum_cls))


@dataclass
class EntryConfig:
    """
    入場設定（§4.2-B 與 §4.3.0 共用管線參數）。

    所有指標共用同一 timeframe（§4.3.0：避免 N 個 timeframe × N 台機器人的
    K 線輪詢放大）；indicators 僅接受 indicators.config 定義的指標設定型別。
    """
    mode: EntryMode = EntryMode.IMMEDIATE
    logic: str = "all"                         # all(AND) / any(OR)
    timeframe: str = DEFAULT_TIMEFRAME
    fetch_limit: int = DEFAULT_FETCH_LIMIT
    price_source: str = DEFAULT_PRICE_SOURCE
    confirm_bars: int = 1                      # 條件需連續成立幾根已收線 K 棒（1–5）
    max_wait_seconds: Optional[int] = None     # 待命逾時自動停止；None = 無限等待
    indicators: Sequence[IndicatorConfig] = field(default_factory=tuple)

    def __post_init__(self):
        errors: list = []

        self.mode = _coerce_enum(EntryMode, self.mode, "entry.mode", errors)

        if self.logic not in ENTRY_LOGIC_CHOICES:
            errors.append(
                f"無效的 entry.logic: {self.logic!r}，合法值: {', '.join(ENTRY_LOGIC_CHOICES)}"
            )

        if self.timeframe not in VALID_TIMEFRAMES:
            errors.append(
                f"無效的 entry.timeframe: {self.timeframe!r}，合法值: {', '.join(VALID_TIMEFRAMES)}"
            )

        if self.price_source not in VALID_PRICE_SOURCES:
            errors.append(
                f"無效的 entry.price_source: {self.price_source!r}，"
                f"合法值: {', '.join(VALID_PRICE_SOURCES)}"
            )

        lo, hi = VALID_FETCH_LIMIT_RANGE
        if isinstance(self.fetch_limit, bool) or not isinstance(self.fetch_limit, int):
            errors.append(f"entry.fetch_limit 必須是整數，收到: {self.fetch_limit!r}")
        elif not lo <= self.fetch_limit <= hi:
            errors.append(f"entry.fetch_limit 必須在 {lo}-{hi} 之間，收到: {self.fetch_limit}")

        if isinstance(self.confirm_bars, bool) or not isinstance(self.confirm_bars, int):
            errors.append(f"entry.confirm_bars 必須是整數，收到: {self.confirm_bars!r}")
        elif not 1 <= self.confirm_bars <= 5:
            errors.append(f"entry.confirm_bars 必須在 1-5 之間，收到: {self.confirm_bars}")

        if self.max_wait_seconds is not None:
            if isinstance(self.max_wait_seconds, bool) or not isinstance(self.max_wait_seconds, int):
                errors.append(f"entry.max_wait_seconds 必須是整數或 None，收到: {self.max_wait_seconds!r}")
            elif self.max_wait_seconds <= 0:
                errors.append(f"entry.max_wait_seconds 必須大於 0 或為 None，收到: {self.max_wait_seconds}")

        self.indicators = tuple(self.indicators or ())
        if self.mode is EntryMode.INDICATOR and len(self.indicators) == 0:
            errors.append("entry.mode=indicator 時，entry.indicators 至少需要 1 個指標")

        if errors:
            raise DcaConfigError("；".join(errors))


@dataclass
class DcaConfig:
    """
    DCA 機器人完整設定（§4.2 欄位表 A–E）。

    必填：exchange / symbol / base_order_amount；其餘全部有凍結預設值。
    """
    # ── A. 基礎 ──
    exchange: str
    symbol: str
    base_order_amount: DecimalLike            # 基礎單金額（amount_unit 決定單位）
    direction: Union[DcaDirection, str] = DcaDirection.LONG
    account_mode: str = "paper"
    leverage: int = 10
    margin_mode: str = "isolated"
    quantity_precision: Optional[int] = None  # None 時由交易所 load_markets() 填入
    price_decimals: Optional[int] = None
    fee_rate: DecimalLike = Decimal("0.0004")

    # ── B. 入場 ──
    entry: EntryConfig = field(default_factory=EntryConfig)

    # ── C. DCA 階梯 ──
    amount_unit: str = "quote"
    dca_mode: Union[DcaMode, str] = DcaMode.ORDER
    averaging_method: Union[AveragingMethod, str] = AveragingMethod.FIXED_VALUE
    max_safety_orders: int = 20
    price_deviation_percent: DecimalLike = Decimal("0.8")
    price_deviation_multiplier: DecimalLike = Decimal("1.2")
    order_size_multiplier: DecimalLike = Decimal("1.0")

    # ── D. 出場與風控 ──
    take_profit_percent: DecimalLike = Decimal("1.0")
    take_profit_order_type: str = "limit"
    stop_loss_percent: Optional[DecimalLike] = None
    max_position_notional: Optional[DecimalLike] = None
    repeat_after_take_profit: bool = True     # 決策 D-3：預設循環
    cooldown_seconds: int = 300
    max_cycles: Optional[int] = None
    both_tiebreak: str = "long_first"         # 僅 direction=both 使用（§4.4）

    # ── E. 運行參數 ──
    active_safety_orders_window: int = 5      # 決策 D-4：活躍掛單窗口
    monitoring_interval: int = 15
    indicator_refresh_interval: int = 60
    order_health_check_interval: int = 300

    def __post_init__(self):
        errors: list = []

        # ── A. 基礎 ──
        if not isinstance(self.exchange, str) or not self.exchange.strip():
            errors.append("exchange 不可為空（合法值: " + ", ".join(VALID_EXCHANGES) + "）")
        elif self.exchange.strip().lower() not in VALID_EXCHANGES:
            errors.append(
                f"無效的 exchange: {self.exchange}，合法值: {', '.join(VALID_EXCHANGES)}"
            )

        if not isinstance(self.symbol, str) or not self.symbol.strip():
            errors.append("symbol 不可為空，例如 ETH/USDC:USDC")

        self.direction = _coerce_enum(DcaDirection, self.direction, "direction", errors)
        if (
            self.direction in (DcaDirection.SHORT, DcaDirection.BOTH)
            and isinstance(self.exchange, str)
            and self.exchange.strip().lower() not in SHORT_CAPABLE_EXCHANGES
        ):
            errors.append(
                f"交易所 {self.exchange} 不支援做空方向（direction={self.direction.value}），"
                f"做空/雙向僅支援: {', '.join(sorted(SHORT_CAPABLE_EXCHANGES))}"
            )

        if self.account_mode not in ACCOUNT_MODE_CHOICES:
            errors.append(
                f"無效的 account_mode: {self.account_mode!r}，"
                f"合法值: {', '.join(ACCOUNT_MODE_CHOICES)}"
            )

        if isinstance(self.leverage, bool) or not isinstance(self.leverage, int):
            errors.append(f"leverage 必須是整數，收到: {self.leverage!r}")
        elif not 1 <= self.leverage <= 125:
            errors.append(f"leverage 必須在 1-125 之間，收到: {self.leverage}")

        if self.margin_mode not in MARGIN_MODE_CHOICES:
            errors.append(
                f"無效的 margin_mode: {self.margin_mode!r}，合法值: {', '.join(MARGIN_MODE_CHOICES)}"
            )

        for name in ("quantity_precision", "price_decimals"):
            value = getattr(self, name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 10:
                errors.append(f"{name} 必須在 0-10 之間或為 None，收到: {value!r}")

        self.fee_rate = _coerce_decimal(self.fee_rate, "fee_rate", errors)
        if self.fee_rate < 0:
            errors.append(f"fee_rate 不可為負數，收到: {self.fee_rate}")

        if not isinstance(self.entry, EntryConfig):
            errors.append(f"entry 必須是 EntryConfig，收到型別: {type(self.entry).__name__}")

        # ── C. DCA 階梯 ──
        self.base_order_amount = _coerce_decimal(
            self.base_order_amount, "base_order_amount", errors
        )
        if self.base_order_amount <= 0:
            errors.append(f"base_order_amount 必須大於 0，收到: {self.base_order_amount}")

        if self.amount_unit not in AMOUNT_UNIT_CHOICES:
            errors.append(
                f"無效的 amount_unit: {self.amount_unit!r}，合法值: {', '.join(AMOUNT_UNIT_CHOICES)}"
            )

        self.dca_mode = _coerce_enum(DcaMode, self.dca_mode, "dca_mode", errors)
        self.averaging_method = _coerce_enum(
            AveragingMethod, self.averaging_method, "averaging_method", errors
        )

        if isinstance(self.max_safety_orders, bool) or not isinstance(self.max_safety_orders, int):
            errors.append(f"max_safety_orders 必須是整數，收到: {self.max_safety_orders!r}")
        elif not 1 <= self.max_safety_orders <= 50:
            errors.append(f"max_safety_orders 必須在 1-50 之間，收到: {self.max_safety_orders}")

        self.price_deviation_percent = _coerce_decimal(
            self.price_deviation_percent, "price_deviation_percent", errors
        )
        if self.price_deviation_percent <= 0 or self.price_deviation_percent > 50:
            errors.append(
                f"price_deviation_percent 必須大於 0 且不超過 50，收到: {self.price_deviation_percent}"
            )

        self.price_deviation_multiplier = _coerce_decimal(
            self.price_deviation_multiplier, "price_deviation_multiplier", errors
        )
        if self.price_deviation_multiplier < 1 or self.price_deviation_multiplier > 3:
            errors.append(
                f"price_deviation_multiplier 必須在 1.0-3.0 之間，收到: {self.price_deviation_multiplier}"
            )

        self.order_size_multiplier = _coerce_decimal(
            self.order_size_multiplier, "order_size_multiplier", errors
        )
        if self.order_size_multiplier < 1 or self.order_size_multiplier > 3:
            errors.append(
                f"order_size_multiplier 必須在 1.0-3.0 之間，收到: {self.order_size_multiplier}"
            )

        # ── D. 出場與風控 ──
        self.take_profit_percent = _coerce_decimal(
            self.take_profit_percent, "take_profit_percent", errors
        )
        if self.take_profit_percent <= 0 or self.take_profit_percent > 100:
            errors.append(
                f"take_profit_percent 必須大於 0 且不超過 100，收到: {self.take_profit_percent}"
            )

        if self.take_profit_order_type not in TAKE_PROFIT_ORDER_TYPE_CHOICES:
            errors.append(
                f"無效的 take_profit_order_type: {self.take_profit_order_type!r}，"
                f"合法值: {', '.join(TAKE_PROFIT_ORDER_TYPE_CHOICES)}"
            )

        if self.stop_loss_percent is not None:
            self.stop_loss_percent = _coerce_decimal(self.stop_loss_percent, "stop_loss_percent", errors)
            if self.stop_loss_percent <= 0:
                errors.append(f"stop_loss_percent 必須大於 0 或為 None，收到: {self.stop_loss_percent}")

        if self.max_position_notional is not None:
            self.max_position_notional = _coerce_decimal(
                self.max_position_notional, "max_position_notional", errors
            )
            if self.max_position_notional <= 0:
                errors.append(
                    f"max_position_notional 必須大於 0 或為 None，收到: {self.max_position_notional}"
                )

        if not isinstance(self.repeat_after_take_profit, bool):
            errors.append(
                f"repeat_after_take_profit 必須是布林值，收到: {self.repeat_after_take_profit!r}"
            )

        if isinstance(self.cooldown_seconds, bool) or not isinstance(self.cooldown_seconds, int):
            errors.append(f"cooldown_seconds 必須是整數，收到: {self.cooldown_seconds!r}")
        elif self.cooldown_seconds < 0:
            errors.append(f"cooldown_seconds 不可為負數，收到: {self.cooldown_seconds}")

        if self.max_cycles is not None:
            if isinstance(self.max_cycles, bool) or not isinstance(self.max_cycles, int):
                errors.append(f"max_cycles 必須是整數或 None，收到: {self.max_cycles!r}")
            elif self.max_cycles <= 0:
                errors.append(f"max_cycles 必須大於 0 或為 None，收到: {self.max_cycles}")

        if self.both_tiebreak not in BOTH_TIEBREAK_CHOICES:
            errors.append(
                f"無效的 both_tiebreak: {self.both_tiebreak!r}，合法值: {', '.join(BOTH_TIEBREAK_CHOICES)}"
            )

        # ── E. 運行參數 ──
        if isinstance(self.active_safety_orders_window, bool) or not isinstance(
            self.active_safety_orders_window, int
        ):
            errors.append(
                f"active_safety_orders_window 必須是整數，收到: {self.active_safety_orders_window!r}"
            )
        elif not 1 <= self.active_safety_orders_window <= 50:
            errors.append(
                f"active_safety_orders_window 必須在 1-50 之間，收到: {self.active_safety_orders_window}"
            )

        if isinstance(self.monitoring_interval, bool) or not isinstance(self.monitoring_interval, int):
            errors.append(f"monitoring_interval 必須是整數，收到: {self.monitoring_interval!r}")
        elif not 5 <= self.monitoring_interval <= 300:
            errors.append(f"monitoring_interval 必須在 5-300 秒之間，收到: {self.monitoring_interval}")

        if isinstance(self.indicator_refresh_interval, bool) or not isinstance(
            self.indicator_refresh_interval, int
        ):
            errors.append(
                f"indicator_refresh_interval 必須是整數，收到: {self.indicator_refresh_interval!r}"
            )
        elif self.indicator_refresh_interval < 30:
            errors.append(
                f"indicator_refresh_interval 不可小於 30 秒，收到: {self.indicator_refresh_interval}"
            )

        if isinstance(self.order_health_check_interval, bool) or not isinstance(
            self.order_health_check_interval, int
        ):
            errors.append(
                f"order_health_check_interval 必須是整數，收到: {self.order_health_check_interval!r}"
            )
        elif self.order_health_check_interval < 60:
            errors.append(
                f"order_health_check_interval 不可小於 60 秒，收到: {self.order_health_check_interval}"
            )

        if errors:
            raise DcaConfigError("；".join(errors))
