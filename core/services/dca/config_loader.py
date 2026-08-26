"""
DCA YAML 設定載入器（Phase 3 交付物）

分層：程式預設 ← defaults ← 個別 YAML。本載入器把 YAML dict 轉成
`DcaConfig`，驗證規則全部沿用 `models.dca_config.DcaConfig.__post_init__`，
額外補上：

- 缺 `symbol` / `base_order_amount` 產生明確中文錯誤（稽核 3.3）
- 未標 `account_mode` 的 YAML 由 `dca_account_mode_from_yaml()` 判為
  `unknown`（= legacy 實盤），**不得**自動視為 paper（稽核 3.4）
- YAML 含未定義鍵 → warning 日誌明確列出鍵名（稽核 3.2，不靜默吞掉）
- `dca_config_to_dict()`：把載入後的 DcaConfig 還原成可寫回 YAML 的 dict，
  供稽核 3.1「YAML 往返一致」與 Phase 4/7 的設定檢視使用。

YAML 格式（凍結）：
```yaml
dca:
  exchange: binance
  symbol: ETH/USDC:USDC
  direction: long
  account_mode: paper
  base_order_amount: 20
  ...
  entry:
    mode: indicator
    logic: all
    timeframe: 15m
    fetch_limit: 300
    price_source: close
    confirm_bars: 1
    max_wait_seconds:
    indicators:
      - type: rsi
        period: 14
        oversold: 30
        overbought: 70
        condition: level
```
也接受不含 `dca:` 外層鍵的「扁平 dict」（相容測試與 API 便利性），但優先讀
`dca:` 區塊。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Mapping, Optional

from .indicators.config import (
    BollingerConfig,
    IndicatorConfig,
    MacdConfig,
    PriceChangeConfig,
    RsiConfig,
)
from .models.dca_config import (
    ACCOUNT_MODE_CHOICES,
    DcaConfig,
    DcaConfigError,
    EntryConfig,
)

_logger = logging.getLogger("dca.config_loader")


# ────────────────────────────────────────────────────────────────────────────
# 已知欄位清單（unknown 鍵偵測用）
# ────────────────────────────────────────────────────────────────────────────
_DCA_ROOT_KEYS = frozenset(field for field in DcaConfig.__dataclass_fields__)
_ENTRY_KEYS = frozenset(field for field in EntryConfig.__dataclass_fields__)
_INDICATOR_KEYS = frozenset({
    "type",
    # RSI
    "period", "oversold", "overbought", "condition",
    # MACD
    "fast", "slow", "signal",
    # Bollinger
    "std_dev",
    # Price Change
    "lookback_bars", "long_threshold_percent", "short_threshold_percent",
})
# 其他允許的頂層鍵（非 dca 設定欄位，但常見於檔案）
_DCA_EXTRA_KEYS = frozenset({"dca", "schema", "version"})


def _section(data: Mapping[str, Any]) -> Mapping[str, Any]:
    """取得 dca 設定區段；支援扁平 dict。"""
    if not isinstance(data, Mapping):
        raise DcaConfigError("DCA 設定必須是 YAML mapping（object）")
    section = data.get("dca")
    if section is None:
        return data
    if not isinstance(section, Mapping):
        raise DcaConfigError("dca 區段必須是 mapping，收到: " + type(section).__name__)
    return section


def _warn_unknown_keys(raw: Mapping[str, Any], known: frozenset,
                       scope: str) -> List[str]:
    """回傳未知鍵清單並對每個鍵寫 warning 日誌（稽核 3.2）。"""
    unknown = [str(key) for key in raw.keys() if key not in known]
    if unknown:
        _logger.warning(
            "DCA YAML 含未定義鍵（將被忽略）: scope=%s keys=%s",
            scope, ", ".join(sorted(unknown)),
        )
    return unknown


def _build_indicator(raw_item: Mapping[str, Any]) -> IndicatorConfig:
    """由 YAML dict 建立單一指標設定。"""
    kind = str(raw_item.get("type", "")).strip().lower()
    if kind == "rsi":
        return RsiConfig(
            period=_as_int(raw_item, "period", 14),
            oversold=_as_float(raw_item, "oversold", 30.0),
            overbought=_as_float(raw_item, "overbought", 70.0),
            condition=str(raw_item.get("condition", "level")).lower(),
        )
    if kind == "macd":
        return MacdConfig(
            fast=_as_int(raw_item, "fast", 12),
            slow=_as_int(raw_item, "slow", 26),
            signal=_as_int(raw_item, "signal", 9),
            condition=str(raw_item.get("condition", "cross")).lower(),
        )
    if kind == "bollinger":
        return BollingerConfig(
            period=_as_int(raw_item, "period", 20),
            std_dev=_as_float(raw_item, "std_dev", 2.0),
            condition=str(raw_item.get("condition", "touch")).lower(),
        )
    if kind == "price_change":
        return PriceChangeConfig(
            lookback_bars=_as_int(raw_item, "lookback_bars", 24),
            long_threshold_percent=_as_float(
                raw_item, "long_threshold_percent", -3.0),
            short_threshold_percent=_as_float(
                raw_item, "short_threshold_percent", 3.0),
        )
    raise DcaConfigError(f"無效的指標 type: {kind!r}（合法: rsi / macd / bollinger / price_change）")


def _as_int(raw: Mapping[str, Any], key: str, default: int) -> int:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise DcaConfigError(f"entry.indicators.{key} 必須是整數，收到: {value!r}")
    return value


def _as_float(raw: Mapping[str, Any], key: str, default: float) -> float:
    value = raw.get(key, default)
    if isinstance(value, bool):
        raise DcaConfigError(f"entry.indicators.{key} 必須是數值，收到: {value!r}")
    try:
        return float(value)
    except (TypeError, ValueError):
        raise DcaConfigError(f"entry.indicators.{key} 必須是數值，收到: {value!r}")


# ────────────────────────────────────────────────────────────────────────────
# account_mode 語意（稽核 3.4）
# ────────────────────────────────────────────────────────────────────────────
def dca_account_mode_from_yaml(data: Mapping[str, Any]) -> str:
    """回傳 DCA 檔案的 account_mode；未標註時為 'unknown'（不得視為 paper）。"""
    try:
        section = _section(data)
    except DcaConfigError:
        return "unknown"
    value = section.get("account_mode")
    if isinstance(value, str) and value.strip().lower() in ACCOUNT_MODE_CHOICES:
        return str(value).strip().lower()
    return "unknown"


def _build_entry(raw: Mapping[str, Any]) -> EntryConfig:
    entry_raw = dict(raw.get("entry") or {})
    _warn_unknown_keys(entry_raw, _ENTRY_KEYS, "entry")

    indicators: List[IndicatorConfig] = []
    indicator_items = entry_raw.get("indicators", []) or []
    if not isinstance(indicator_items, (list, tuple)):
        raise DcaConfigError("entry.indicators 必須是 list")
    for idx, item in enumerate(indicator_items):
        if not isinstance(item, Mapping):
            raise DcaConfigError(f"entry.indicators[{idx}] 必須是 mapping")
        _warn_unknown_keys(item, _INDICATOR_KEYS, f"indicators[{idx}]")
        indicators.append(_build_indicator(item))

    return EntryConfig(
        mode=str(entry_raw.get("mode", "immediate")).lower(),
        logic=str(entry_raw.get("logic", "all")).lower(),
        timeframe=str(entry_raw.get("timeframe", "15m")).lower(),
        fetch_limit=_as_int(entry_raw, "fetch_limit", 300) if "fetch_limit" in entry_raw else 300,
        price_source=str(entry_raw.get("price_source", "close")).lower(),
        confirm_bars=_as_int(entry_raw, "confirm_bars", 1) if "confirm_bars" in entry_raw else 1,
        max_wait_seconds=entry_raw.get("max_wait_seconds"),
        indicators=tuple(indicators),
    )


def load_dca_config(data: Mapping[str, Any]) -> DcaConfig:
    """YAML dict → DcaConfig（僅載入，不做 I/O）。"""
    if not isinstance(data, Mapping):
        raise DcaConfigError("DCA 設定必須是 YAML mapping（object）")

    raw = _section(data)
    # 頂層未知鍵（含 dca 區段內的非定義欄位）警示與忽略
    _warn_unknown_keys(raw, _DCA_ROOT_KEYS | _DCA_EXTRA_KEYS, "dca")

    # 缺漏必填欄位（稽核 3.3）——不使用 0 或空字串預設
    if not raw.get("symbol"):
        raise DcaConfigError("缺少必填欄位 symbol（例如 ETH/USDC:USDC）")
    if raw.get("base_order_amount") is None:
        raise DcaConfigError("缺少必填欄位 base_order_amount")

    # account_mode 語意（稽核 3.4）
    account_mode = dca_account_mode_from_yaml(raw)
    if account_mode == "unknown":
        raise DcaConfigError(
            "缺少明確的 account_mode（必須為 paper 或 live）；"
            "未標註視為 unknown（legacy 實盤），不得自動視為 paper"
        )

    config = DcaConfig(
        exchange=str(raw.get("exchange", "")).strip().lower(),
        symbol=str(raw.get("symbol", "")).strip(),
        base_order_amount=raw.get("base_order_amount"),
        direction=raw.get("direction", "long"),
        account_mode=account_mode,
        leverage=_int_or_none(raw, "leverage", 10),
        margin_mode=str(raw.get("margin_mode", "isolated")).lower(),
        quantity_precision=_int_or_none(raw, "quantity_precision", None),
        price_decimals=_int_or_none(raw, "price_decimals", None),
        fee_rate=raw.get("fee_rate", "0.0004"),
        entry=_build_entry(raw),
        amount_unit=str(raw.get("amount_unit", "quote")).lower(),
        dca_mode=str(raw.get("dca_mode", "order")).lower(),
        averaging_method=str(raw.get("averaging_method", "fixed_value")).lower(),
        max_safety_orders=_int_or_none(raw, "max_safety_orders", 20),
        price_deviation_percent=raw.get("price_deviation_percent", "0.8"),
        price_deviation_multiplier=raw.get("price_deviation_multiplier", "1.2"),
        order_size_multiplier=raw.get("order_size_multiplier", "1.0"),
        take_profit_percent=raw.get("take_profit_percent", "1.0"),
        take_profit_order_type=str(raw.get("take_profit_order_type", "limit")).lower(),
        stop_loss_percent=raw.get("stop_loss_percent"),
        max_position_notional=raw.get("max_position_notional"),
        repeat_after_take_profit=_as_bool(raw, "repeat_after_take_profit", True),
        cooldown_seconds=_int_or_none(raw, "cooldown_seconds", 300),
        max_cycles=_int_or_none(raw, "max_cycles", None),
        both_tiebreak=str(raw.get("both_tiebreak", "long_first")).lower(),
        active_safety_orders_window=_int_or_none(
            raw, "active_safety_orders_window", 5),
        monitoring_interval=_int_or_none(raw, "monitoring_interval", 15),
        indicator_refresh_interval=_int_or_none(
            raw, "indicator_refresh_interval", 60),
        order_health_check_interval=_int_or_none(
            raw, "order_health_check_interval", 300),
    )
    return config


def _int_or_none(raw: Mapping[str, Any], key: str, default: Optional[int]) -> Optional[int]:
    if key not in raw or raw.get(key) is None:
        return default
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise DcaConfigError(f"{key} 必須是整數，收到: {value!r}")
    return value


def _as_bool(raw: Mapping[str, Any], key: str, default: bool) -> bool:
    value = raw.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1", "on"}:
            return True
        if lowered in {"false", "no", "0", "off"}:
            return False
    raise DcaConfigError(f"{key} 必須是布林值，收到: {value!r}")


# ────────────────────────────────────────────────────────────────────────────
# DcaConfig → dict（YAML 往返一致，稽核 3.1）
# ────────────────────────────────────────────────────────────────────────────
def _indicator_to_dict(cfg: IndicatorConfig) -> Dict[str, Any]:
    if isinstance(cfg, RsiConfig):
        return {
            "type": "rsi", "period": cfg.period,
            "oversold": cfg.oversold, "overbought": cfg.overbought,
            "condition": cfg.condition,
        }
    if isinstance(cfg, MacdConfig):
        return {
            "type": "macd", "fast": cfg.fast, "slow": cfg.slow,
            "signal": cfg.signal, "condition": cfg.condition,
        }
    if isinstance(cfg, BollingerConfig):
        return {
            "type": "bollinger", "period": cfg.period,
            "std_dev": cfg.std_dev, "condition": cfg.condition,
        }
    if isinstance(cfg, PriceChangeConfig):
        return {
            "type": "price_change", "lookback_bars": cfg.lookback_bars,
            "long_threshold_percent": cfg.long_threshold_percent,
            "short_threshold_percent": cfg.short_threshold_percent,
        }
    raise TypeError(f"未知的指標設定型別: {type(cfg).__name__}")


def dca_config_to_dict(config: DcaConfig) -> Dict[str, Any]:
    """把 DcaConfig 還原成可 YAML 序列化的 dict（exact Decimal 以 str 表示）。"""
    entry = config.entry
    data = {
        "exchange": config.exchange,
        "symbol": config.symbol,
        "direction": config.direction.value,
        "account_mode": config.account_mode,
        "leverage": config.leverage,
        "margin_mode": config.margin_mode,
        "quantity_precision": config.quantity_precision,
        "price_decimals": config.price_decimals,
        "fee_rate": str(config.fee_rate),
        "base_order_amount": str(config.base_order_amount),
        "amount_unit": config.amount_unit,
        "dca_mode": config.dca_mode.value,
        "averaging_method": config.averaging_method.value,
        "max_safety_orders": config.max_safety_orders,
        "price_deviation_percent": str(config.price_deviation_percent),
        "price_deviation_multiplier": str(config.price_deviation_multiplier),
        "order_size_multiplier": str(config.order_size_multiplier),
        "take_profit_percent": str(config.take_profit_percent),
        "take_profit_order_type": config.take_profit_order_type,
        "stop_loss_percent": (str(config.stop_loss_percent)
                              if config.stop_loss_percent is not None else None),
        "max_position_notional": (str(config.max_position_notional)
                                  if config.max_position_notional is not None else None),
        "repeat_after_take_profit": config.repeat_after_take_profit,
        "cooldown_seconds": config.cooldown_seconds,
        "max_cycles": config.max_cycles,
        "both_tiebreak": config.both_tiebreak,
        "active_safety_orders_window": config.active_safety_orders_window,
        "monitoring_interval": config.monitoring_interval,
        "indicator_refresh_interval": config.indicator_refresh_interval,
        "order_health_check_interval": config.order_health_check_interval,
        "entry": {
            "mode": entry.mode.value,
            "logic": entry.logic,
            "timeframe": entry.timeframe,
            "fetch_limit": entry.fetch_limit,
            "price_source": entry.price_source,
            "confirm_bars": entry.confirm_bars,
            "max_wait_seconds": entry.max_wait_seconds,
            "indicators": [_indicator_to_dict(i) for i in entry.indicators],
        },
    }
    return data


# ────────────────────────────────────────────────────────────────────────────
# 執行期精度自動填入（Phase 3：adapter load_markets 已經存在時）
# ────────────────────────────────────────────────────────────────────────────
def auto_fill_dca_precision(config: DcaConfig, exchange_adapter,
                            logger: Optional[logging.Logger] = None) -> None:
    """
    若 YAML 未指定 quantity_precision / price_decimals，嘗試從 adapter 的
    ccxt `markets` 自動填入；不可得時維持 None（executor 原樣傳送）。
    僅原地修改 config（DcaConfig 不是 frozen）。
    """
    log = logger or _logger
    try:
        exchange = getattr(exchange_adapter, "exchange", None)
        if exchange is None:
            return
        markets = getattr(exchange, "markets", None)
        if not markets:
            return
        market = markets.get(config.symbol)
        if not market:
            return
        precision = market.get("precision", {}) or {}
        qty_tick = precision.get("amount")
        price_tick = precision.get("price")

        def _tick_to_decimals(tick) -> int:
            if tick is None or tick <= 0:
                return 2
            if tick >= 1:
                return 0
            import math
            return int(round(-math.log10(float(tick))))

        if config.quantity_precision is None and qty_tick is not None:
            config.quantity_precision = (
                _tick_to_decimals(qty_tick) if 0 < qty_tick <= 1 else int(qty_tick)
            )
        if config.price_decimals is None and price_tick is not None:
            config.price_decimals = (
                _tick_to_decimals(price_tick) if 0 < price_tick <= 1 else int(price_tick)
            )
        log.info(
            "DCA 精度自動填入完成",
            extra={"symbol": config.symbol,
                   "quantity_precision": config.quantity_precision,
                   "price_decimals": config.price_decimals},
        )
    except Exception as exc:
        log.warning("DCA 精度自動填入失敗，使用設定/預設值: %s", exc)
