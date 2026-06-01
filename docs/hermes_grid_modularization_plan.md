# 🤖 Hermes Agent × Grid Trading 模組化整合計畫書 v2.0

> 目標：建立一套讓外部 AI Agent (Hermes) 能自動發現交易機會、生成最佳參數、並遠端控制網格系統的完整接口層
> 原始版本：v1.0 (2026-05-24)
> 更新日期：2026-05-25
> 更新原因：基於程式碼現狀深度調研，修正 v1.0 中的不準確描述，補充新發現的能力與限制

---

## 📊 程式碼現狀深度分析（v2.0 更新）

### ✅ 已具備（可直接複用）

| 能力 | 實現位置 | 對 Hermes 的價值 | v2.0 補充 |
|------|----------|------------------|-----------|
| 單進程多網格併發 | [`run_multiple_grids.py`](file:///root/crypto-trading-open/run_multiple_grids.py) | Hermes 無需管理多進程 | 433 行，架構清晰，`run_single_grid_task()` 可被 API 層直接調用 |
| 熱插拔控制 | [`config/active_grids.json`](file:///root/crypto-trading-open/config/active_grids.json) (5 秒輪詢) | 最簡單的啟停機制 | `active_grids_monitor()` 協程，L353-385，支援動態新增/停止 |
| 全局熔斷器 | `BinanceRest` 類級熔斷 + [`.circuit_breaker_state.json`](file:///root/crypto-trading-open/data/.circuit_breaker_state.json) | 自動防 BAN，Hermes 無需擔心 | 啟動前自動檢測 BAN 狀態並等待解封 (L314-347) |
| 共享適配器快取 | 餘額/持倉 30s TTL，WS 訂單+行情 | API 權重已極度節省 | `BalanceMonitor` 60 秒更新，`PositionMonitor` REST 備用 |
| YAML 配置體系 | [`config/grid/*.yaml`](file:///root/crypto-trading-open/config/grid/) | 參數結構完整，可程式化生成 | 32 個 YAML 檔案，支援 Binance/Lighter/Backpack/Hyperliquid |
| FastAPI 後端 | [`web_ui/backend/main.py`](file:///root/crypto-trading-open/web_ui/backend/main.py) (port 8001) | 可直接擴展 | 已有 scripts/configs CRUD + process start/stop + WS log streaming |
| PM2 整合 | [`process_manager.py`](file:///root/crypto-trading-open/web_ui/backend/process_manager.py) | 可讀取 PM2 狀態 | 支援 PM2 + 原生 subprocess 雙模式管理 |
| 統計輸出 | `[STATS_JSON]` 格式化 + [`GridStatistics.to_display_dict()`](file:///root/crypto-trading-open/core/services/grid/models/grid_metrics.py#L135-L178) | 結構完整 | 包含 PnL、持倉、掛單、餘額、APR 等完整數據 |
| 豐富的網格模式 | [`GridConfig`](file:///root/crypto-trading-open/core/services/grid/models/grid_config.py#L60-L730) | 策略靈活 | Long/Short/Neutral/Follow/Martingale + 剝頭皮/本金保護/止盈/止損/價格鎖定 |
| 多交易所支援 | `core/adapters/exchanges/` | 跨所部署 | Binance/Lighter/Backpack/Hyperliquid 四所適配器 |

### ❌ 缺失項（需新建）

| 需求 | 說明 | v2.0 更新 |
|------|------|-----------|
| **Grid Lifecycle API** | 程式化建立/啟動/停止/刪除網格的 REST 接口 | 後端已有 process start/stop，但缺少 YAML 自動生成 + active_grids.json 控制的統一流程 |
| **系統健康狀態端點** | Hermes 需知道熔斷狀態、API 餘量再決定是否操作 | 熔斷資訊可從 `BinanceRest` 類別靜態屬性直接讀取 |
| **YAML 自動生成器** | 從 JSON 參數 → 合規 YAML 配置檔 | v2.0: 需考慮 [`create_grid_config()`](file:///root/crypto-trading-open/run_grid_trading.py#L146-L292) 中所有 30+ 個參數的映射 |
| **運行統計查詢 API** | 查詢各網格即時 PnL、持倉、掛單 | `[STATS_JSON]` 已輸出至 stdout，需攔截解析或走 `GridStateStore` 共享 |
| **認證與安全層** | API Token 驗證，防止未授權存取 | 保持 v1.0 方案（Bearer Token） |
| **操作日誌/審計** | 記錄每次 Hermes 的操作決策 | 保持 v1.0 方案（SQLite 審計表） |

---

## 🔍 v2.0 新增：YAML 配置完整參數清單

> 此節是 v1.0 缺失的關鍵資訊。Hermes 建立網格時，YAML 生成器必須支援以下所有參數。

### 必填參數

```yaml
grid_system:
  exchange: "binance"               # 交易所名稱
  symbol: "SOL/USDC:USDC"           # 交易對符號
  grid_type: "neutral"              # long/short/neutral/follow_long/follow_short/martingale_long/martingale_short
  grid_interval: 0.2                # 網格間隔
  order_amount: 0.1                 # 每格訂單數量
  quantity_precision: 4             # 數量精度（小數位數）
  price_decimals: 2                 # 價格精度（小數位數）
  leverage: 100                     # 槓桿倍數
  margin_mode: "cross"              # cross/isolated
```

### 價格區間（普通/中性網格必填）

```yaml
  price_range:
    lower_price: 50.0
    upper_price: 200.0
```

### 速率保護（Binance 強烈建議設定）

```yaml
  limit_only: true                  # 全系統僅限價執行
  active_window_each_side: 2        # 現價上下各N格為活躍窗口
  active_window_refresh_interval: 60
  order_health_check_enabled: true
  order_health_check_interval: 3000
  health_check_adjust_position_enabled: false
  rest_position_query_interval: 120
  fee_rate: 0.000
  reverse_order_grid_distance: 1
  ws_only_mode: true
  rest_backup_enabled: false
```

### 可選高級功能參數

```yaml
  # 價格移動網格
  follow_grid_count: 200            # 網格數量
  follow_timeout: 300               # 脫離超時（秒）
  follow_distance: 1                # 脫離距離（格數）
  price_offset_grids: 3             # 價格偏移網格數

  # 剝頭皮模式
  scalping_enabled: false
  scalping_trigger_percent: 80
  scalping_take_profit_grids: 2
  smart_scalping_enabled: false
  allowed_deep_drops: 1
  min_drop_threshold_percent: 10

  # 本金保護
  capital_protection_enabled: false
  capital_protection_trigger_percent: 50

  # 止盈
  take_profit_enabled: false
  take_profit_percentage: 0.01

  # 價格鎖定
  price_lock_enabled: false
  price_lock_threshold: 150.0
  price_lock_start_at_threshold: false

  # 止損保護（優先級最高）
  stop_loss_protection_enabled: false
  stop_loss_trigger_percent: 100.0
  stop_loss_escape_timeout: 300
  stop_loss_apr_threshold: 50.0

  # 馬丁格爾
  martingale_increment: 0.001

  # 持倉容差
  position_tolerance:
    amount_diff: 0.006
    price_diff: 0.1
```

---

## 🏗️ 系統架構（v2.0 更新）

```
┌─────────────────────────────────────────────────────────────────┐
│                    Hermes Agent (外部 AI)                        │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐       │
│  │ 市場掃描  │  │ 機會研判  │  │ 參數生成  │  │ 生命週期  │       │
│  │ Monitor  │→│ Analyze  │→│ Params   │→│ Control  │       │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘       │
│        ↓              ↓              ↓              ↓          │
│   (自有網路)      (自有邏輯)     (JSON 參數)    (REST 調用)      │
└────────────────────────────────────┬────────────────────────────┘
                                     │ HTTP REST (Bearer Token)
                                     ▼
┌─────────────────────────────────────────────────────────────────┐
│              Grid Control API (FastAPI port 8001)               │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ 🆕 /api/v1/grids/*            Grid Lifecycle CRUD       │   │
│  │ 🆕 /api/v1/system/health      熔斷狀態 + API 餘量        │   │
│  │ 🆕 /api/v1/system/stats       全局統計聚合                │   │
│  │ 🆕 /api/v1/market/info        交易對精度/限制查詢 (快取)   │   │
│  │    /api/scripts, /api/configs  現有 CRUD (保留)           │   │
│  │    /api/process/*              現有進程管理 (保留)         │   │
│  │    /ws/stats                   WebSocket 即時推送 (現有)   │   │
│  └─────────────────────────────────────────────────────────┘   │
│                              │                                  │
│  ┌───────────────┐  ┌───────┴───────┐  ┌──────────────────┐   │
│  │ YAML Generator│  │ active_grids  │  │ SQLite Audit Log │   │
│  │ (參數→配置檔)  │  │ .json 寫入    │  │ (操作記錄)        │   │
│  └───────────────┘  └───────────────┘  └──────────────────┘   │
│                              │                                  │
│  ┌───────────────────────────────────────────────────────┐     │
│  │ 🆕 GridStateStore                                     │     │
│  │ 攔截 [STATS_JSON] 輸出 → 記憶體字典 → REST/WS 查詢    │     │
│  └───────────────────────────────────────────────────────┘     │
└────────────────────────────────────┬────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────┐
│              Grid Engine (run_multiple_grids.py)                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ active_grids_monitor() → 5s 偵測 active_grids.json 變更  │  │
│  │ Shared Adapter        → 全局快取 + 限流鎖 + 熔斷器       │  │
│  │ run_single_grid_task() → 每格獨立 GridCoordinator        │  │
│  │   └── GridCoordinator (2785 行)                          │  │
│  │       ├── GridResetManager    (重置管理)                  │  │
│  │       ├── PositionMonitor     (持倉監控)                  │  │
│  │       ├── BalanceMonitor      (餘額監控)                  │  │
│  │       ├── StopLossMonitor     (止損監控)                  │  │
│  │       ├── ScalpingOperations  (剝頭皮操作)                │  │
│  │       └── OrderHealthChecker  (訂單健康檢查)              │  │
│  └──────────────────────────────────────────────────────────┘  │
│                              │                                  │
│                    WebSocket + 快取式 REST                      │
│                              ▼                                  │
│                      Exchange APIs (Binance/Lighter/etc.)       │
└─────────────────────────────────────────────────────────────────┘
```

### 🔑 核心設計原則（不變）

1. **Hermes 永不直接碰交易所 API** — 所有交互走本地 REST
2. **零額外 API 權重** — 新接口全讀本地快取/記憶體，不觸發交易所 REST
3. **冪等操作** — 重複調用同一指令不會產生副作用
4. **先查後做** — Hermes 必須先查 `/system/health`，確認非熔斷才操作

---

## 📅 四階段開發計畫（v2.0 修訂）

### 階段一：Grid Lifecycle API + YAML 生成器 (3-4 天)

> **目標**：Hermes 能透過 REST 建立、啟動、停止、刪除網格

#### 1.1 API 端點設計（v2.0 更新：與現有端點共存）

| 方法 | 路徑 | 功能 | API 權重 |
|------|------|------|----------|
| `GET` | `/api/v1/system/health` | 系統健康狀態（熔斷/API餘量/運行中網格數） | 0 (讀本地) |
| `GET` | `/api/v1/grids` | 列出所有網格及即時狀態 | 0 |
| `GET` | `/api/v1/grids/{grid_id}` | 單一網格詳細狀態+統計 | 0 |
| `POST` | `/api/v1/grids` | 建立新網格（生成 YAML + 註冊） | 0 |
| `POST` | `/api/v1/grids/{grid_id}/start` | 啟動網格（寫入 active_grids.json） | 0 |
| `POST` | `/api/v1/grids/{grid_id}/stop` | 停止網格（寫入 active_grids.json） | 0 |
| `DELETE` | `/api/v1/grids/{grid_id}` | 停止+刪除配置+歸檔 | 0 |
| `GET` | `/api/v1/market/info/{symbol}` | 交易對精度/最小下單量 (60s 快取) | ≤1 (首次) |

> [!IMPORTANT]
> 新端點使用 `/api/v1/` 前綴，與現有 `/api/scripts`、`/api/configs`、`/api/process/*` 端點共存。
> 現有 `ProcessManager` 的 `start_process()` / `stop_process()` 僅操作 OS 進程層級，新 API 需操作更高抽象層級（YAML 生成 + active_grids.json + stats 查詢）。

#### 1.2 POST /api/v1/grids 請求體（v2.0 更新：簡化版 + 完整版）

**簡化版（Hermes 常用場景：Binance 中性網格）**：

```json
{
  "symbol": "SOL/USDC:USDC",
  "exchange": "binance",
  "grid_type": "neutral",
  "lower_price": 100.0,
  "upper_price": 200.0,
  "grid_interval": 0.2,
  "order_amount": 0.1,
  "leverage": 50,
  "margin_mode": "cross",
  "quantity_precision": 4,
  "price_decimals": 2,
  "auto_start": true,
  "metadata": {
    "source": "hermes",
    "reason": "SOL 30d 波動率 42%, 適合中性網格",
    "confidence": 0.85
  }
}
```

**完整版（包含所有可選高級參數）**：

省略的欄位將自動填入安全預設值（見 1.3 節）。

```json
{
  "symbol": "SOL/USDC:USDC",
  "exchange": "binance",
  "grid_type": "neutral",
  "lower_price": 100.0,
  "upper_price": 200.0,
  "grid_interval": 0.2,
  "order_amount": 0.1,
  "leverage": 50,
  "margin_mode": "cross",
  "quantity_precision": 4,
  "price_decimals": 2,
  "limit_only": true,
  "active_window_each_side": 2,
  "active_window_refresh_interval": 60,
  "order_health_check_enabled": true,
  "order_health_check_interval": 3000,
  "health_check_adjust_position_enabled": false,
  "rest_position_query_interval": 120,
  "fee_rate": 0.0,
  "reverse_order_grid_distance": 1,
  "ws_only_mode": true,
  "rest_backup_enabled": false,
  "auto_start": true,
  "metadata": {
    "source": "hermes",
    "reason": "策略原因",
    "confidence": 0.85
  }
}
```

#### 1.3 YAML 自動生成器（v2.0 修訂：基於實際 YAML 結構）

```python
# web_ui/backend/yaml_generator.py
import yaml
from pathlib import Path
from typing import Optional

class GridYAMLGenerator:
    """從 API JSON 參數自動生成合規 YAML 配置檔
    
    v2.0: 基於實際 config/grid/*.yaml 結構和
          run_grid_trading.py:create_grid_config() 的參數映射
    """
    
    # Binance 速率保護安全預設值
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
    
    def generate(self, params: dict) -> tuple[str, str]:
        """
        從 JSON 參數生成 YAML 配置檔
        
        Args:
            params: API 請求體 JSON
            
        Returns:
            (yaml_content, file_path)
        """
        # 1. 建構 grid_system 字典
        grid_system = {
            "exchange": params["exchange"],
            "symbol": params["symbol"],
            "grid_type": params["grid_type"],
            "grid_interval": params["grid_interval"],
            "order_amount": params["order_amount"],
            "quantity_precision": params.get("quantity_precision", 4),
            "price_decimals": params.get("price_decimals", 2),
            "leverage": params.get("leverage", 10),
            "margin_mode": params.get("margin_mode", "cross"),
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
            if "follow_timeout" in params:
                grid_system["follow_timeout"] = params["follow_timeout"]
            if "follow_distance" in params:
                grid_system["follow_distance"] = params["follow_distance"]
        
        # 3. 合併安全預設值（允許顯式覆蓋）
        if params.get("exchange", "").lower() == "binance":
            for key, default_val in self.BINANCE_SAFE_DEFAULTS.items():
                grid_system[key] = params.get(key, default_val)
        
        # 4. 可選高級參數（僅設定時才寫入）
        optional_keys = [
            "scalping_enabled", "scalping_trigger_percent",
            "scalping_take_profit_grids", "smart_scalping_enabled",
            "capital_protection_enabled", "capital_protection_trigger_percent",
            "take_profit_enabled", "take_profit_percentage",
            "price_lock_enabled", "price_lock_threshold",
            "stop_loss_protection_enabled", "stop_loss_trigger_percent",
            "stop_loss_escape_timeout", "stop_loss_apr_threshold",
            "martingale_increment", "position_tolerance",
        ]
        for key in optional_keys:
            if key in params:
                grid_system[key] = params[key]
        
        # 5. 生成檔案路徑
        symbol_clean = params["symbol"].replace("/", "-").replace(":", "-").lower()
        exchange = params["exchange"].lower()
        grid_type_str = params["grid_type"].replace("_", "-")
        
        if grid_type not in ("follow_long", "follow_short"):
            lower = int(params.get("lower_price", 0))
            upper = int(params.get("upper_price", 0))
            filename = f"{exchange}-{grid_type_str}-perp-{symbol_clean}-{lower}-{upper}.yaml"
        else:
            filename = f"{exchange}-{grid_type_str}-perp-{symbol_clean}.yaml"
        
        file_path = f"config/grid/{filename}"
        
        # 6. 生成 YAML 內容
        config = {"grid_system": grid_system}
        yaml_content = yaml.dump(config, default_flow_style=False, 
                                  allow_unicode=True, sort_keys=False)
        
        return yaml_content, file_path
    
    def validate_params(self, params: dict) -> list[str]:
        """
        參數驗證，回傳錯誤列表
        
        v2.0: 基於 GridConfig.__post_init__() 和 _validate() 的規則
        """
        errors = []
        
        # 必填欄位
        required = ["symbol", "exchange", "grid_type", "grid_interval", "order_amount"]
        for field in required:
            if field not in params:
                errors.append(f"缺少必填欄位: {field}")
        
        # grid_type 合法值
        valid_types = ["long", "short", "neutral", "follow_long", "follow_short",
                       "martingale_long", "martingale_short"]
        if params.get("grid_type") not in valid_types:
            errors.append(f"無效的 grid_type: {params.get('grid_type')}")
        
        # 非 follow 模式需要價格區間
        gt = params.get("grid_type", "")
        if gt not in ("follow_long", "follow_short"):
            if "lower_price" not in params or "upper_price" not in params:
                errors.append("普通/中性/馬丁網格必須指定 lower_price 和 upper_price")
            elif params.get("lower_price", 0) >= params.get("upper_price", 0):
                errors.append("lower_price 必須小於 upper_price")
        else:
            if "follow_grid_count" not in params:
                errors.append("價格移動網格必須指定 follow_grid_count")
        
        # 數值範圍
        if params.get("grid_interval", 0) <= 0:
            errors.append("grid_interval 必須大於 0")
        if params.get("order_amount", 0) <= 0:
            errors.append("order_amount 必須大於 0")
        if params.get("leverage", 1) < 1 or params.get("leverage", 1) > 125:
            errors.append("leverage 必須在 1-125 之間")
        
        return errors
```

#### 1.4 GET /api/v1/system/health 回應（v2.0 更新：增加引擎細節）

```json
{
  "status": "healthy",
  "circuit_breaker": {
    "is_open": false,
    "error_code": null,
    "until": null
  },
  "api_throttle": {
    "total_calls": 142,
    "window_current": 23,
    "window_max": 2400
  },
  "engine": {
    "process_running": true,
    "uptime_seconds": 86400,
    "shared_adapter_connected": true
  },
  "running_grids": 2,
  "running_grid_symbols": ["SOL/USDC:USDC", "ETH/USDC:USDC"],
  "max_recommended_grids": 5,
  "can_accept_new_grid": true,
  "available_config_slots": 3
}
```

> [!IMPORTANT]
> Hermes 應在每次操作前呼叫此端點。若 `can_accept_new_grid: false` 或 `circuit_breaker.is_open: true`，**必須放棄本次操作**。

#### 1.5 認證機制（不變）

```python
# .env 中設定 GRID_API_TOKEN=<random-64-char>
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security = HTTPBearer()

async def verify_token(cred: HTTPAuthorizationCredentials = Depends(security)):
    if cred.credentials != os.getenv("GRID_API_TOKEN"):
        raise HTTPException(status_code=401, detail="Invalid token")
```

---

### 階段二：狀態同步 + 統計查詢層 (2-3 天)

> **目標**：Hermes 能即時查詢網格運行狀態，用於決策

#### 2.1 GridStateStore（v2.0 更新：攔截 [STATS_JSON] 方案）

**方案 A（推薦）：進程內共享記憶體**

若 FastAPI 與 `run_multiple_grids.py` 在同一進程或共享 `GridCoordinator` 引用：

```python
# web_ui/backend/grid_state_store.py
import threading, time

class GridStateStore:
    """進程內共享的網格狀態儲存（零 DB 開銷）"""
    
    _instance = None
    _lock = threading.Lock()
    
    def __init__(self):
        self._states: dict[str, dict] = {}
        self._updated_at: dict[str, float] = {}
    
    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def update(self, config_path: str, stats: dict):
        with self._lock:
            self._states[config_path] = stats
            self._updated_at[config_path] = time.time()
    
    def get_all(self) -> dict:
        with self._lock:
            return {
                path: {
                    **stats,
                    "last_update": self._updated_at.get(path, 0)
                }
                for path, stats in self._states.items()
            }
    
    def get(self, config_path: str) -> dict | None:
        with self._lock:
            return self._states.get(config_path)
```

**方案 B（備選）：stdout 攔截解析**

若引擎與 API 在不同進程，攔截 PM2 日誌中的 `[STATS_JSON]` 行：

```python
# 在 ProcessManager 的 _read_stream 中加入解析
import re, json

STATS_PATTERN = re.compile(r'\[STATS_JSON\]\[(.+?)\]\s*(.+)')

def _parse_stats_line(self, line: str):
    match = STATS_PATTERN.search(line)
    if match:
        symbol = match.group(1)
        stats = json.loads(match.group(2))
        GridStateStore.instance().update(symbol, stats)
```

#### 2.2 統計查詢端點（不變）

```
GET /api/v1/grids/{grid_id}/stats
Response: {
  "symbol": "SOL/USDC:USDC",
  "current_price": 167.42,
  "realized_pnl": 12.35,
  "unrealized_pnl": -2.10,
  "total_pnl": 10.25,
  "current_position": 0.3,
  "active_orders": 4,
  "filled_buy": 15,
  "filled_sell": 12,
  "grid_utilization": 68.5,
  "balance": {
    "collateral": 500.0,
    "spot": 200.0,
    "total": 700.0
  },
  "uptime": "2d 3h 15m",
  "last_update": "2026-05-25T01:30:00+08:00"
}
```

#### 2.3 批次查詢（Hermes 首選端點）

```
GET /api/v1/grids/summary
Response: {
  "total_grids": 3,
  "active_grids": 2,
  "total_realized_pnl": 45.67,
  "total_unrealized_pnl": -5.20,
  "grids": [
    {"id": "sol-neutral-100-200", "symbol": "SOL/USDC:USDC", "pnl": 10.25, "status": "running"},
    {"id": "eth-neutral-1800-3160", "symbol": "ETH/USDC:USDC", "pnl": 35.42, "status": "running"}
  ]
}
```

---

### 階段三：操作審計 + 安全護欄 (2 天)

> **目標**：防止 AI 失控操作，建立可追溯的操作記錄

#### 3.1 審計日誌（不變）

```sql
CREATE TABLE audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   DATETIME DEFAULT CURRENT_TIMESTAMP,
    source      TEXT NOT NULL,        -- 'hermes' / 'dashboard' / 'manual'
    action      TEXT NOT NULL,        -- 'create_grid' / 'start' / 'stop' / 'delete'
    grid_id     TEXT,
    params_json TEXT,                 -- 完整請求參數
    result      TEXT,                 -- 'success' / 'rejected' / 'error'
    reason      TEXT,                 -- Hermes 的決策理由
    error_msg   TEXT
);
```

#### 3.2 安全護欄規則（v2.0 更新：新增實際限制）

| 規則 | 實現 | 說明 |
|------|------|------|
| 最大同時運行數 | `MAX_CONCURRENT_GRIDS = 5` | 超過拒絕建立 |
| 熔斷中禁操作 | 檢查 `BinanceRest._is_adapter_circuit_open()` | 返回 503 |
| 重複標的檢查 | 同 symbol 已有 active grid 時拒絕 | 返回 409 |
| 操作頻率限制 | 同一 source 60 秒內最多 3 次寫操作 | 返回 429 |
| 最小投資驗證 | `order_amount × grid_count × min_price ≥ MIN_INVEST` | 返回 400 |
| 參數範圍校驗 | leverage ≤ 125, grid_interval > 0, etc. | 返回 422 |
| **v2.0 新增** | 進程鎖檢查 (`/tmp/grid_multiple_runners.pid`) | 確認引擎進程存活 |
| **v2.0 新增** | YAML 寫入權限校驗 | 確保 `config/grid/` 目錄可寫 |

---

### 階段四：Hermes 端整合指南 + 監控循環設計 (1-2 天)

> **目標**：定義 Hermes 的調用流程與最佳實踐

#### 4.1 Hermes 簡化調用範例（v2.0 新增：最小可行範例）

```python
import httpx

class HermesGridClient:
    """Hermes Agent 調用網格 API 的最小範例"""
    
    def __init__(self, base_url="http://localhost:8001", token="xxx"):
        self.base = base_url
        self.headers = {"Authorization": f"Bearer {token}"}
    
    async def is_system_ready(self) -> bool:
        """Step 1: 檢查系統是否可操作"""
        r = await httpx.AsyncClient().get(
            f"{self.base}/api/v1/system/health", 
            headers=self.headers
        )
        health = r.json()
        return health["can_accept_new_grid"]
    
    async def create_neutral_grid(
        self, symbol: str, lower: float, upper: float, 
        interval: float, amount: float
    ) -> dict:
        """Step 2: 一鍵建立中性網格（最常用場景）"""
        r = await httpx.AsyncClient().post(
            f"{self.base}/api/v1/grids",
            json={
                "symbol": symbol,
                "exchange": "binance",
                "grid_type": "neutral",
                "lower_price": lower,
                "upper_price": upper,
                "grid_interval": interval,
                "order_amount": amount,
                "leverage": 50,
                "margin_mode": "cross",
                "quantity_precision": 4,
                "price_decimals": 2,
                "auto_start": True,
                "metadata": {"source": "hermes"}
            },
            headers=self.headers,
            timeout=30
        )
        return r.json()
    
    async def stop_grid(self, grid_id: str) -> dict:
        """Step 3: 停止網格"""
        r = await httpx.AsyncClient().post(
            f"{self.base}/api/v1/grids/{grid_id}/stop",
            headers=self.headers
        )
        return r.json()
    
    async def get_pnl_summary(self) -> dict:
        """Step 4: 查詢全局 PnL"""
        r = await httpx.AsyncClient().get(
            f"{self.base}/api/v1/grids/summary",
            headers=self.headers
        )
        return r.json()
```

#### 4.2 Hermes Monitor 循環建議架構

```python
class HermesGridController:
    """Hermes Agent 控制網格的標準流程"""
    
    def __init__(self, grid_api_url: str, api_token: str):
        self.client = HermesGridClient(grid_api_url, api_token)
    
    async def monitor_loop(self):
        """主監控循環 — 建議 15-30 分鐘一次"""
        while True:
            # Step 1: 檢查系統健康
            if not await self.client.is_system_ready():
                await asyncio.sleep(900)  # 15 min
                continue
            
            # Step 2: 取得當前運行狀態
            summary = await self.client.get_pnl_summary()
            
            # Step 3: AI 分析 — 是否需要調整
            decisions = await self.ai_analyze(summary)
            
            # Step 4: 執行決策
            for decision in decisions:
                if decision.action == "create":
                    await self.client.create_neutral_grid(**decision.params)
                elif decision.action == "stop":
                    await self.client.stop_grid(decision.grid_id)
            
            await asyncio.sleep(1800)  # 30 min
```

#### 4.3 Hermes 調用時序圖（不變）

```
Hermes                    Grid API                  Grid Engine
  │                          │                          │
  │─── GET /system/health ──→│                          │
  │←── {can_accept: true} ───│                          │
  │                          │                          │
  │─── GET /grids/summary ──→│                          │
  │←── {grids: [...]} ───────│                          │
  │                          │                          │
  │  (AI 分析: SOL 適合開格)  │                          │
  │                          │                          │
  │─── POST /grids ─────────→│                          │
  │    {symbol, params...}   │── generate YAML ─────────│
  │                          │── write active_grids ────→│
  │                          │                          │── hot-reload detect (5s)
  │                          │                          │── start coordinator
  │←── {id, status: ok} ─────│                          │
  │                          │                          │
  │  (15分鐘後...)            │                          │
  │─── GET /grids/{id}/stats→│                          │
  │←── {pnl: 5.2, ...} ─────│                          │
```

#### 4.4 API 權重影響分析（不變）

| Hermes 操作 | 觸發的 Binance REST 調用 | 權重消耗 |
|------------|------------------------|---------| 
| GET /system/health | 0（讀本地熔斷狀態） | **0** |
| GET /grids/summary | 0（讀記憶體快取） | **0** |
| POST /grids (建立) | 0（僅寫 YAML + JSON） | **0** |
| 網格啟動 (自動觸發) | `set_leverage` + `set_margin_mode` + `create_orders` (batch) | **~20** |
| POST /grids/{id}/stop | `cancel_all_orders` (1次) | **~5** |
| GET /market/info (首次) | `exchange.load_markets()` (快取 60s) | **~10** |

> [!TIP]
> Hermes 每 30 分鐘監控一次，單次完整流程（健康檢查 + 摘要 + 可能的建立/停止）消耗 ≤ 35 API 權重，遠低於每分鐘 2400 的限制。**即使 Hermes 每分鐘調用一次也完全安全**。

---

## 📁 檔案變更清單（v2.0 更新）

```
web_ui/backend/
├── main.py                    # 擴展：掛載 v1 路由（保留現有所有端點）
├── routes/
│   ├── __init__.py            # 🆕
│   ├── grids.py               # 🆕 Grid Lifecycle CRUD
│   ├── system.py              # 🆕 健康狀態 + 全局統計
│   └── market.py              # 🆕 交易對資訊查詢
├── yaml_generator.py          # 🆕 YAML 配置自動生成（基於完整參數清單）
├── grid_state_store.py        # 🆕 運行時狀態共享儲存
├── safety_guard.py            # 🆕 安全護欄
├── auth.py                    # 🆕 Token 認證
├── audit.py                   # 🆕 審計日誌
├── process_manager.py         # 現有，擴展 [STATS_JSON] 攔截（方案 B）
├── file_manager.py            # 現有，不動
└── models.py                  # 現有，不動
data/
├── grid_audit.db              # 🆕 操作審計 SQLite
run_multiple_grids.py          # 修改：統計寫入 GridStateStore（方案 A）
.env                           # 新增：GRID_API_TOKEN=xxx
```

---

## ⏱️ 時間估算

| 階段 | 預估天數 | 依賴 |
|------|----------|------|
| 一：Lifecycle API + YAML 生成 | 3-4 天 | 無 |
| 二：狀態同步 + 統計查詢 | 2-3 天 | 階段一 |
| 三：審計 + 安全護欄 | 2 天 | 階段一 |
| 四：Hermes 整合指南 | 1-2 天 | 階段一~三 |
| **總計** | **8-11 天** | |

> [!TIP]
> 階段二和三可並行。最短路徑約 **6-7 天**。

---

## 🔐 與方向一 (Dashboard) 的協同

兩個方向共享同一套後端 API：

- Dashboard 前端調用 `/api/v1/grids` 展示 Bot 列表
- Hermes Agent 調用同一組端點進行自動化控制
- `GridStateStore` 同時服務 WebSocket 推送（Dashboard）和 REST 查詢（Hermes）
- 審計日誌區分 `source: "dashboard"` vs `source: "hermes"`

**建議：先完成本計畫（Hermes API 層），再做 Dashboard 前端。** 因為 API 層是兩者的共同基礎。

---

## 📝 v2.0 變更摘要

| 項目 | v1.0 | v2.0 |
|------|------|------|
| YAML 參數清單 | 僅列出基礎 6 項 | **完整 30+ 項參數**，含速率保護/高級功能 |
| YAML 生成器 | 草案 | **完整實現**，含安全預設值自動填充 |
| 架構圖 | 簡化版 | **增加 GridCoordinator 內部子模組** |
| 現有後端描述 | 籠統「FastAPI 基礎 CRUD」 | **精確列出所有現有端點與模式** |
| API 共存策略 | 未提及 | **明確 `/api/v1/` 前綴**，不影響現有端點 |
| 統計攔截方案 | 僅方案 A | **新增方案 B**（stdout 攔截，適用獨立進程） |
| Hermes 調用範例 | 複雜版 | **新增最小可行範例** `HermesGridClient` |
| 安全護欄 | 6 條規則 | **新增 2 條**（進程鎖/YAML 寫入權限） |
