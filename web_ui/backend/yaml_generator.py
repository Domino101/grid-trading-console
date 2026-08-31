"""
YAML 配置自動生成器

從 API JSON 參數自動生成合規 YAML 配置檔，完整對齊
run_grid_trading.py:create_grid_config() 的 30+ 個參數。
"""

import os
import yaml
import re
from typing import Optional, Tuple, List

from core.infrastructure.identity import (
    find_config_path,
    generate_bot_id,
    resolve_bot_id,
    with_metadata_bot_id,
)


class GridYAMLGenerator:
    """從 API JSON 參數自動生成合規 YAML 配置檔

    基於實際 config/grid/*.yaml 結構和
    run_grid_trading.py:create_grid_config() 的參數映射。
    """

    # Binance 速率保護安全預設值（Hermes 專用）
    BINANCE_SAFE_DEFAULTS = {
        "limit_only": True,
        "active_window_each_side": 2,
        "active_window_refresh_interval": 60,
        "order_health_check_enabled": True,
        "order_health_check_interval": 3000,
        "health_check_adjust_position_enabled": False,
        "rest_position_query_interval": 120,
        "fee_rate": 0.0,
        "reverse_order_grid_distance": 1,
        "ws_only_mode": True,
        "rest_backup_enabled": False,
    }

    # 所有可選高級功能參數鍵（僅在 params 中出現時寫入 YAML）
    OPTIONAL_KEYS = [
        # 剝頭皮
        "scalping_enabled", "scalping_trigger_percent",
        "scalping_take_profit_grids", "smart_scalping_enabled",
        "allowed_deep_drops", "min_drop_threshold_percent",
        # 本金保護
        "capital_protection_enabled", "capital_protection_trigger_percent",
        # 止盈
        "take_profit_enabled", "take_profit_percentage",
        # 價格鎖定
        "price_lock_enabled", "price_lock_threshold",
        "price_lock_start_at_threshold",
        # 止損保護
        "stop_loss_protection_enabled", "stop_loss_trigger_percent",
        "stop_loss_escape_timeout", "stop_loss_apr_threshold",
        # 馬丁格爾
        "martingale_increment",
        # 持倉容差
        "position_tolerance",
        # Follow 模式額外參數
        "follow_timeout", "follow_distance", "price_offset_grids",
    ]

    # 合法 grid_type 值
    VALID_GRID_TYPES = [
        "long", "short", "neutral",
        "follow_long", "follow_short",
        "martingale_long", "martingale_short",
    ]

    def validate_params(self, params: dict) -> List[str]:
        """
        參數驗證，回傳錯誤列表。

        基於 GridConfig.__post_init__() 和 _validate() 的規則。
        """
        errors = []

        # 必填欄位
        required = ["symbol", "exchange", "grid_type", "grid_interval", "order_amount"]
        for field_name in required:
            if field_name not in params or params[field_name] is None:
                errors.append(f"缺少必填欄位: {field_name}")

        # grid_type 合法值
        gt = params.get("grid_type")
        if gt not in self.VALID_GRID_TYPES:
            errors.append(
                f"無效的 grid_type: {gt}，"
                f"合法值: {', '.join(self.VALID_GRID_TYPES)}"
            )

        # 非 follow 模式需要價格區間
        if gt and gt not in ("follow_long", "follow_short"):
            if "lower_price" not in params or params.get("lower_price") is None:
                errors.append("普通/中性/馬丁網格必須指定 lower_price")
            if "upper_price" not in params or params.get("upper_price") is None:
                errors.append("普通/中性/馬丁網格必須指定 upper_price")
            if (params.get("lower_price") is not None
                    and params.get("upper_price") is not None
                    and params["lower_price"] >= params["upper_price"]):
                errors.append("lower_price 必須小於 upper_price")
        else:
            if gt and "follow_grid_count" not in params:
                errors.append("價格移動網格必須指定 follow_grid_count")

        # 數值範圍
        if params.get("grid_interval") is not None and params["grid_interval"] <= 0:
            errors.append("grid_interval 必須大於 0")
        if params.get("order_amount") is not None and params["order_amount"] <= 0:
            errors.append("order_amount 必須大於 0")
        lev = params.get("leverage")
        if lev is not None and (lev < 1 or lev > 125):
            errors.append("leverage 必須在 1-125 之間")

        # margin_mode 合法值
        mm = params.get("margin_mode")
        if mm is not None and mm not in ("cross", "isolated"):
            errors.append(f"無效的 margin_mode: {mm}，合法值: cross, isolated")

        # exchange 合法值
        exchange = params.get("exchange")
        valid_exchanges = ["binance", "lighter", "backpack", "hyperliquid"]
        if exchange is not None and exchange.lower() not in valid_exchanges:
            errors.append(
                f"無效的 exchange: {exchange}，"
                f"合法值: {', '.join(valid_exchanges)}"
            )

        return errors

    def generate(self, params: dict, existing_bot_id: Optional[str] = None) -> Tuple[str, str]:
        """
        從 JSON 參數生成 YAML 配置檔。

        Args:
            params: API 請求體 JSON（已通過 validate_params 驗證）
            existing_bot_id: 編輯既有網格時傳入的穩定身份；未提供時產生新 id。
                            確保改價格區間/重設後 bot 身份不漂移（P1-1）。

        Returns:
            (yaml_content, relative_file_path)
        """
        # 1. 建構 grid_system 字典
        grid_system = {
            "exchange": params["exchange"],
            "symbol": params["symbol"],
            "grid_type": params["grid_type"],
        }

        # 2. 價格區間（非 follow 模式必填）
        grid_type = params["grid_type"]
        if grid_type not in ("follow_long", "follow_short"):
            grid_system["price_range"] = {
                "lower_price": params["lower_price"],
                "upper_price": params["upper_price"],
            }
        else:
            grid_system["follow_grid_count"] = params["follow_grid_count"]

        # 3. 基礎參數
        grid_system["grid_interval"] = params["grid_interval"]
        grid_system["order_amount"] = params["order_amount"]
        grid_system["quantity_precision"] = params.get("quantity_precision", 4)
        grid_system["price_decimals"] = params.get("price_decimals", 2)
        grid_system["leverage"] = params.get("leverage", 10)
        grid_system["margin_mode"] = params.get("margin_mode", "cross")
        # Explicit mode prevents PAPER results from being mistaken for LIVE.
        grid_system["account_mode"] = params.get("account_mode", "paper")

        # 4. Binance 安全預設值（允許顯式覆蓋）
        if params.get("exchange", "").lower() == "binance":
            for key, default_val in self.BINANCE_SAFE_DEFAULTS.items():
                grid_system[key] = params.get(key, default_val)

        # 5. 可選高級參數（僅設定時才寫入）
        for key in self.OPTIONAL_KEYS:
            if key in params and params[key] is not None:
                grid_system[key] = params[key]

        # 6. 生成檔案路徑（清理特殊字元，防止路徑穿越）
        file_path = self._build_file_path(params, grid_type)

        # 7. 生成 YAML 內容（含穩定 bot_id；P1-1 身份與檔名解耦）
        config = with_metadata_bot_id(
            {"grid_system": grid_system},
            existing_bot_id or generate_bot_id(),
        )
        yaml_content = yaml.dump(
            config,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )

        return yaml_content, file_path

    def _build_file_path(self, params: dict, grid_type: str) -> str:
        """
        生成安全的 YAML 檔案相對路徑。

        檔名清理規則：
        - 移除 / : \\ 等路徑分隔符
        - 只允許 a-z, 0-9, -, _ 和 .
        """
        # 清理 symbol（SOL/USDC:USDC → sol-usdc-usdc）
        symbol_clean = params["symbol"].replace("/", "-").replace(":", "-").lower()
        symbol_clean = re.sub(r"[^a-z0-9\-]", "", symbol_clean)

        exchange = re.sub(r"[^a-z0-9]", "", params["exchange"].lower())
        grid_type_str = grid_type.replace("_", "-")

        if grid_type not in ("follow_long", "follow_short"):
            lower = int(params.get("lower_price", 0))
            upper = int(params.get("upper_price", 0))
            filename = f"{exchange}-{grid_type_str}-perp-{symbol_clean}-{lower}-{upper}.yaml"
        else:
            filename = f"{exchange}-{grid_type_str}-perp-{symbol_clean}.yaml"

        return f"config/grid/{filename}"

    @staticmethod
    def grid_id_from_path(config_path: str) -> str:
        """解析穩定 grid_id：優先 metadata.bot_id，缺漏時退回檔名 stem（legacy）。"""
        return resolve_bot_id(config_path)

    @staticmethod
    def path_from_grid_id(grid_id: str) -> str:
        """從 grid_id 還原配置檔案路徑。

        優先嘗試檔名 stem（legacy）；否則以 metadata.bot_id 反向掃描 config/grid/。
        """
        direct = f"config/grid/{grid_id}.yaml"
        if os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", direct)):
            return direct
        return find_config_path(grid_id, "config/grid") or direct
