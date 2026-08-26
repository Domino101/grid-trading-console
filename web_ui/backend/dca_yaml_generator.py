"""DCA YAML 配置自動生成器（Phase 4）

從 API JSON 參數自動生成 `config/dca/*.yaml`（以 `dca:` 區段包覆），並保證：
- 產出可被 `core.services.dca.config_loader.load_dca_config()` 解析（稽核 4.8）
- 缺漏/非法參數由 `DcaConfig.__post_init__` 統一驗證，錯誤一次回報
- 檔名遵循 `config/dca/README.md`：`dca-{exchange}-{direction}-{symbol}-{timestamp}.yaml`

注意：本模組**不複製**階梯／指標計算邏輯；preview 直接使用
`core.services.dca.ladder.build_ladder()`（稽核 4.2）。
"""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

import yaml

# 加入專案根目錄到 sys.path 以便匯入 core 模組
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from core.services.dca.config_loader import (
    dca_config_to_dict,
    load_dca_config,
)
from core.services.dca.models.dca_config import DcaConfig, DcaConfigError


class DcaYAMLGenerator:
    """從 API JSON 參數自動生成合規 DCA YAML 配置檔。"""

    # 已知會出現在 API params 但非 DCA YAML 欄位的控制鍵（生成時忽略）
    CONTROL_KEYS = frozenset({"auto_start", "entry_price", "metadata"})

    def __init__(self, timestamp_factory: Optional[Callable[[], str]] = None):
        self._timestamp_factory = timestamp_factory

    # ── 參數驗證 ──────────────────────────────────────────
    def validate_params(self, params: Mapping[str, Any]) -> List[str]:
        """回傳錯誤列表；空列表代表通過。"""
        try:
            self.build_config(params)
            return []
        except DcaConfigError as exc:
            msg = str(exc)
            return [part for part in (part.strip() for part in msg.split("；")) if part]
        except (KeyError, TypeError, ValueError) as exc:
            return [str(exc)]

    def build_config(self, params: Mapping[str, Any]) -> DcaConfig:
        """把 API 參數轉成 DcaConfig（會完整驗證）。"""
        raw = self._params_to_raw(params)
        return load_dca_config(raw)

    # ── 生成 ──────────────────────────────────────────────
    def generate(self, params: Mapping[str, Any]) -> Tuple[str, str]:
        """
        生成 YAML 配置檔。

        Args:
            params: API 請求體 JSON（已通過 validate_params）

        Returns:
            (yaml_content, relative_file_path)
        """
        config = self.build_config(params)
        data = dca_config_to_dict(config)
        yaml_content = yaml.dump(
            {"dca": data},
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )
        file_path = self._build_file_path(params)
        return yaml_content, file_path

    # ── 檔名與路徑 ────────────────────────────────────────
    def _build_file_path(self, params: Mapping[str, Any]) -> str:
        exchange = re.sub(r"[^a-z0-9]", "", str(params.get("exchange", "")).lower()) or "exchange"
        direction = re.sub(r"[^a-z]", "", str(params.get("direction", "long")).lower()) or "long"
        symbol = str(params.get("symbol", ""))
        symbol_clean = re.sub(r"[^a-z0-9\-]", "", symbol.replace("/", "-").replace(":", "-").lower())
        timestamp = self._timestamp_factory() if self._timestamp_factory else datetime.now().strftime("%Y%m%d%H%M%S")
        filename = f"dca-{exchange}-{direction}-{symbol_clean}-{timestamp}.yaml"
        return f"config/dca/{filename}"

    @staticmethod
    def bot_id_from_path(config_path: str) -> str:
        """從配置檔案路徑提取 bot_id（去掉目錄與 .yaml 後綴）。"""
        return Path(config_path).stem

    @staticmethod
    def path_from_bot_id(bot_id: str) -> str:
        """從 bot_id 還原配置檔案路徑；呼叫端應先做路徑穿越驗證。"""
        return f"config/dca/{bot_id}.yaml"

    @staticmethod
    def is_valid_bot_id(bot_id: str) -> bool:
        """路徑穿越防護（稽核 4.22）：只允許安全的 bot 識別碼。"""
        if not isinstance(bot_id, str) or not bot_id:
            return False
        if "/" in bot_id or "\\" in bot_id or ".." in bot_id:
            return False
        return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", bot_id))

    # ── params → YAML raw dict ────────────────────────────
    @staticmethod
    def _dec(value: Any, default: Optional[str] = None) -> Optional[str]:
        """API 邊界 float → 精確字串（核心層使用 Decimal，避免 binary 殘差）。"""
        if value is None:
            return default
        return str(value)

    @staticmethod
    def _indicator_raw(items: Any) -> List[Dict[str, Any]]:
        """移除指標 dict 中為 None 的欄位（避免覆蓋核心層預設值）。"""
        cleaned: List[Dict[str, Any]] = []
        for item in items or []:
            if isinstance(item, Mapping):
                cleaned.append({k: v for k, v in item.items() if v is not None})
            else:
                cleaned.append(dict(item))
        return cleaned

    def _params_to_raw(self, params: Mapping[str, Any]) -> Dict[str, Any]:
        """把 API 參數映射成 load_dca_config 接受的扁平 dict。"""
        entry_raw = params.get("entry") if isinstance(params.get("entry"), Mapping) else {}
        raw = {
            "exchange": str(params.get("exchange", "")).strip(),
            "symbol": str(params.get("symbol", "")).strip(),
            "direction": str(params.get("direction", "long")).strip(),
            "account_mode": str(params.get("account_mode", "paper")).strip(),
            "base_order_amount": self._dec(params.get("base_order_amount")),
            "leverage": params.get("leverage", 10),
            "margin_mode": str(params.get("margin_mode", "isolated")).strip(),
            "quantity_precision": params.get("quantity_precision"),
            "price_decimals": params.get("price_decimals"),
            "fee_rate": self._dec(params.get("fee_rate"), "0.0004"),
            "amount_unit": str(params.get("amount_unit", "quote")).strip(),
            "dca_mode": str(params.get("dca_mode", "order")).strip(),
            "averaging_method": str(params.get("averaging_method", "fixed_value")).strip(),
            "max_safety_orders": params.get("max_safety_orders", 20),
            "price_deviation_percent": self._dec(params.get("price_deviation_percent"), "0.8"),
            "price_deviation_multiplier": self._dec(params.get("price_deviation_multiplier"), "1.2"),
            "order_size_multiplier": self._dec(params.get("order_size_multiplier"), "1.0"),
            "take_profit_percent": self._dec(params.get("take_profit_percent"), "1.0"),
            "take_profit_order_type": str(params.get("take_profit_order_type", "limit")).strip(),
            "stop_loss_percent": self._dec(params.get("stop_loss_percent")),
            "max_position_notional": self._dec(params.get("max_position_notional")),
            "repeat_after_take_profit": params.get("repeat_after_take_profit", True),
            "cooldown_seconds": params.get("cooldown_seconds", 300),
            "max_cycles": params.get("max_cycles"),
            "both_tiebreak": str(params.get("both_tiebreak", "long_first")).strip(),
            "active_safety_orders_window": params.get("active_safety_orders_window", 5),
            "monitoring_interval": params.get("monitoring_interval", 15),
            "indicator_refresh_interval": params.get("indicator_refresh_interval", 60),
            "order_health_check_interval": params.get("order_health_check_interval", 300),
            "entry": {
                "mode": str(entry_raw.get("mode", "immediate")).strip(),
                "logic": str(entry_raw.get("logic", "all")).strip(),
                "timeframe": str(entry_raw.get("timeframe", "15m")).strip(),
                "fetch_limit": entry_raw.get("fetch_limit", 300),
                "price_source": str(entry_raw.get("price_source", "close")).strip(),
                "confirm_bars": entry_raw.get("confirm_bars", 1),
                "max_wait_seconds": entry_raw.get("max_wait_seconds"),
                "indicators": self._indicator_raw(entry_raw.get("indicators")),
            },
        }
        return raw
