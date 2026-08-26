"""
安全護欄模組

集中管理所有操作前的安全檢查，防止 AI 失控操作。
"""

import os
import json
import logging
import time
import threading
from collections import defaultdict
from pathlib import Path
from typing import Tuple, Optional

logger = logging.getLogger("GridAPI.Safety")

# ────────────────────────────────────────────────────────
# 常數
# ────────────────────────────────────────────────────────

MAX_CONCURRENT_GRIDS = 5
MAX_CONCURRENT_DCA_BOTS = 5
MAX_CONCURRENT_COMBINED_BOTS = 10
ENGINE_PID_FILE = "/tmp/grid_multiple_runners.pid"


class SafetyGuard:
    """安全護欄：集中管理所有操作前的安全檢查"""

    def __init__(self, project_root: str):
        self.project_root = project_root
        self.active_grids_path = os.path.join(project_root, "config", "active_grids.json")
        self.active_dca_path = os.path.join(project_root, "config", "active_dca.json")
        self.config_grid_dir = os.path.join(project_root, "config", "grid")
        self.config_dca_dir = os.path.join(project_root, "config", "dca")

    # ────────────────────────────────────────────────────
    # 公開方法
    # ────────────────────────────────────────────────────

    def check_can_create_grid(self) -> Tuple[bool, Optional[str]]:
        """
        建立網格前的完整安全檢查。

        Returns:
            (can_proceed, error_message)
            若 can_proceed=False，error_message 說明原因。
        """
        # 1. 熔斷器檢查
        ok, msg = self.check_circuit_breaker()
        if not ok:
            return False, msg

        # 2. 最大同時運行數
        ok, msg = self.check_max_grids()
        if not ok:
            return False, msg

        # 3. 引擎進程存活
        ok, msg = self.check_engine_alive()
        if not ok:
            return False, msg

        # 4. 配置目錄可寫
        ok, msg = self.check_config_dir_writable()
        if not ok:
            return False, msg

        return True, None

    def check_can_create_dca(self) -> Tuple[bool, Optional[str]]:
        """建立/啟動 DCA 前的完整安全檢查（Phase 4）。

        總量檢查必須涵蓋網格 + DCA（稽核 4.15），不得只檢查 DCA。
        """
        # 1. 熔斷器檢查
        ok, msg = self.check_circuit_breaker()
        if not ok:
            return False, msg

        # 2. DCA 自身上限
        ok, msg = self.check_max_dca()
        if not ok:
            return False, msg

        # 3. 網格 + DCA 合計上限
        ok, msg = self.check_max_combined_bots()
        if not ok:
            return False, msg

        # 4. 引擎進程存活
        ok, msg = self.check_engine_alive()
        if not ok:
            return False, msg

        # 5. 配置目錄可寫
        ok, msg = self.check_dca_config_dir_writable()
        if not ok:
            return False, msg

        return True, None

    def check_max_dca(self) -> Tuple[bool, Optional[str]]:
        """檢查運行中 DCA 數是否已達上限。"""
        active = self._get_active_dca_count()
        if active >= MAX_CONCURRENT_DCA_BOTS:
            return False, (
                f"已達最大同時運行 DCA 數 ({active}/{MAX_CONCURRENT_DCA_BOTS})，"
                "請先停止其他 DCA 再建立新的"
            )
        return True, None

    def check_max_combined_bots(self) -> Tuple[bool, Optional[str]]:
        """檢查「網格 + DCA」合計是否已達總量上限（稽核 4.15）。"""
        grids = self._get_active_grid_count()
        dca = self._get_active_dca_count()
        total = grids + dca
        if total >= MAX_CONCURRENT_COMBINED_BOTS:
            return False, (
                f"已達最大同時運行策略數 ({grids} 網格 + {dca} DCA = {total}/"
                f"{MAX_CONCURRENT_COMBINED_BOTS})，請先停止其他策略再建立新的"
            )
        return True, None

    def check_dca_config_dir_writable(self) -> Tuple[bool, Optional[str]]:
        """確認 config/dca/ 目錄存在且可寫入。"""
        return self._check_dir_writable(self.config_dca_dir, "config/dca")

    def check_circuit_breaker(self) -> Tuple[bool, Optional[str]]:
        """檢查 BinanceRest 熔斷狀態"""
        try:
            from core.adapters.exchanges.adapters.binance_rest import BinanceRest
            if BinanceRest._is_adapter_circuit_open():
                code = BinanceRest._adapter_last_error_code or "unknown"
                return False, f"API 熔斷器開啟 (code={code})，拒絕操作"
        except ImportError:
            # BinanceRest 未載入（非 Binance 環境），跳過檢查
            pass
        return True, None

    def check_max_grids(self) -> Tuple[bool, Optional[str]]:
        """檢查運行中網格數是否已達上限"""
        active = self._get_active_grid_count()
        if active >= MAX_CONCURRENT_GRIDS:
            return False, (
                f"已達最大同時運行網格數 ({active}/{MAX_CONCURRENT_GRIDS})，"
                "請先停止其他網格再建立新的"
            )
        return True, None

    def check_duplicate_symbol(self, symbol: str) -> Tuple[bool, Optional[str]]:
        """
        檢查同一交易對是否已有 active 網格。

        注意：只檢查 active_grids.json 中 enabled=true 的項目。
        """
        active_grids = self._read_active_grids()
        for config_path, is_enabled in active_grids.items():
            if not is_enabled:
                continue
            # 嘗試從 YAML 讀取 symbol
            yaml_symbol = self._read_symbol_from_yaml(config_path)
            if yaml_symbol and yaml_symbol == symbol:
                return False, (
                    f"交易對 {symbol} 已有活躍網格 ({config_path})，"
                    "不允許重複建立"
                )
        return True, None

    def check_engine_alive(self) -> Tuple[bool, Optional[str]]:
        """檢查 run_multiple_grids.py 引擎進程是否存活"""
        if not os.path.exists(ENGINE_PID_FILE):
            return False, (
                "找不到引擎進程鎖檔 (grid_multiple_runners.pid)，"
                "請先啟動 run_multiple_grids.py"
            )

        try:
            with open(ENGINE_PID_FILE, "r") as f:
                pid_str = f.read().strip()
            if not pid_str.isdigit():
                return False, "引擎進程鎖檔內容無效"

            pid = int(pid_str)
            # signal 0 檢查進程是否存在
            os.kill(pid, 0)
            return True, None
        except OSError:
            return False, (
                f"引擎進程 (PID: {pid_str}) 已不存在，"
                "請重新啟動 run_multiple_grids.py"
            )
        except Exception as e:
            return False, f"檢查引擎進程時發生錯誤: {e}"

    def check_config_dir_writable(self) -> Tuple[bool, Optional[str]]:
        """確認 config/grid/ 目錄存在且可寫入"""
        return self._check_dir_writable(self.config_grid_dir, "config/grid")

    def _check_dir_writable(self, directory: str, label: str) -> Tuple[bool, Optional[str]]:
        if not os.path.isdir(directory):
            return False, f"配置目錄不存在: {directory}"
        if not os.access(directory, os.W_OK):
            return False, f"配置目錄不可寫入: {directory}"
        return True, None

    def check_config_not_exists(self, config_path: str) -> Tuple[bool, Optional[str]]:
        """檢查配置檔案是否已存在（防止意外覆蓋）"""
        abs_path = os.path.join(self.project_root, config_path)
        if os.path.exists(abs_path):
            return False, f"配置檔案已存在: {config_path}，請先刪除或使用其他名稱"
        return True, None

    def get_engine_pid(self) -> Optional[int]:
        """取得引擎進程 PID"""
        if not os.path.exists(ENGINE_PID_FILE):
            return None
        try:
            with open(ENGINE_PID_FILE, "r") as f:
                pid_str = f.read().strip()
            if pid_str.isdigit():
                pid = int(pid_str)
                os.kill(pid, 0)  # 檢查是否存活
                return pid
        except (OSError, ValueError):
            pass
        return None

    # ────────────────────────────────────────────────────
    # 輔助方法
    # ────────────────────────────────────────────────────

    def _read_active_grids(self) -> dict:
        """讀取 active_grids.json"""
        if not os.path.exists(self.active_grids_path):
            return {}
        try:
            with open(self.active_grids_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"讀取 active_grids.json 失敗: {e}")
            return {}

    def _get_active_grid_count(self) -> int:
        """取得目前運行中的網格數"""
        active_grids = self._read_active_grids()
        return sum(1 for v in active_grids.values() if v)

    def _read_active_dca(self) -> dict:
        """讀取 active_dca.json"""
        if not os.path.exists(self.active_dca_path):
            return {}
        try:
            with open(self.active_dca_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"讀取 active_dca.json 失敗: {e}")
            return {}

    def _get_active_dca_count(self) -> int:
        """取得目前運行中（desired enabled）的 DCA 數"""
        active_dca = self._read_active_dca()
        return sum(1 for v in active_dca.values() if v)

    def _read_symbol_from_yaml(self, config_path: str) -> Optional[str]:
        """從 YAML 配置檔讀取 symbol"""
        import yaml as _yaml

        abs_path = os.path.join(self.project_root, config_path)
        if not os.path.exists(abs_path):
            return None
        try:
            with open(abs_path, "r", encoding="utf-8") as f:
                data = _yaml.safe_load(f)
            return data.get("grid_system", {}).get("symbol")
        except Exception:
            return None

    def check_min_investment(self, params: dict) -> Tuple[bool, Optional[str]]:
        """
        驗證最小投資額安全限制。
        `order_amount * grid_count * min_price >= MIN_INVEST`
        """
        try:
            grid_type = params.get("grid_type", "")
            order_amount = params.get("order_amount")
            grid_interval = params.get("grid_interval")
            symbol = params.get("symbol")
            exchange = params.get("exchange", "binance")

            if order_amount is None or not symbol:
                return True, None

            # 1. 計算網格數量 (grid_count)
            if grid_type in ("follow_long", "follow_short"):
                grid_count = params.get("follow_grid_count")
            else:
                lower_price = params.get("lower_price")
                upper_price = params.get("upper_price")
                if lower_price is not None and upper_price is not None and grid_interval:
                    grid_count = int(abs(upper_price - lower_price) / grid_interval)
                else:
                    grid_count = None

            if grid_count is None or grid_count <= 0:
                return True, None

            # 2. 獲取最有利（最低）價格 (min_price)
            if grid_type not in ("follow_long", "follow_short"):
                min_price = params.get("lower_price", 0.0)
            else:
                # Follow 網格動態解析 current_price / min_price
                min_price = None
                # 先嘗試從快取 (GridStateStore) 讀取
                try:
                    from grid_state_store import GridStateStore
                    store = GridStateStore.instance()
                    for path, stats in store.get_all().items():
                        if stats.get("symbol") == symbol:
                            min_price = stats.get("current_price")
                            if min_price:
                                break
                except Exception:
                    pass

                # 若快取無資料，則透過 CCXT 查詢
                if min_price is None:
                    try:
                        import ccxt
                        exchange_class = getattr(ccxt, exchange.lower(), None)
                        if exchange_class:
                            ex = exchange_class({"options": {"defaultType": "swap"}})
                            ticker = ex.fetch_ticker(symbol)
                            min_price = ticker.get("last")
                    except Exception as e:
                        logger.warning(f"安全護欄: 無法透過 CCXT 獲取 {symbol} 即時價以進行投資額驗證: {e}")

                # 兜底 fallback
                if min_price is None:
                    logger.warning("安全護欄: 無法獲取價格，跳過 Follow 網格最小投資驗證")
                    return True, None

            # 3. 執行最小投資額比對
            investment = float(order_amount) * float(grid_count) * float(min_price)
            min_invest = float(os.getenv("GRID_MIN_INVESTMENT", "10.0"))

            if investment < min_invest:
                return False, f"網格總投資額估算不足 (${investment:.2f} < 最小限制 ${min_invest:.2f})，請增加單格下單量或網格數"

            return True, None

        except Exception as e:
            logger.error(f"最小投資驗證發生異常: {e}")
            return True, None

    def check_rate_limit(self, source: str) -> Tuple[bool, Optional[str]]:
        """寫操作頻率限制檢查"""
        return rate_limiter.check_rate_limit(source)

    def record_action(self, source: str) -> None:
        """記錄一次寫操作時間戳"""
        rate_limiter.record_action(source)


class WriteRateLimiter:
    """寫操作頻率限制器 (單一來源 60 秒內最多 3 次寫操作)"""

    def __init__(self):
        self._lock = threading.Lock()
        self._history = defaultdict(list)

    def check_rate_limit(self, source: str) -> Tuple[bool, Optional[str]]:
        now = time.time()
        with self._lock:
            self._history[source] = [t for t in self._history[source] if now - t <= 60.0]
            if len(self._history[source]) >= 3:
                return False, f"來源 '{source}' 已達寫操作頻率限制 (60 秒內最多 3 次)，請稍後再試"
            return True, None

    def record_action(self, source: str) -> None:
        now = time.time()
        with self._lock:
            self._history[source].append(now)


# Module-level singletons
rate_limiter = WriteRateLimiter()
