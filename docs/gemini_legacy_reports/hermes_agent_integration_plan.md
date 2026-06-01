# 🤖 Hermes Agent × Grid Trading 整合計畫書 v1.0

> 目標：建立一套讓外部 AI Agent (Hermes) 能自動發現交易機會、生成最佳參數、並遠端控制網格系統的完整接口層
> 建立日期：2026-05-24

---

## 📊 現狀分析

### ✅ 已具備（可直接複用）

| 能力 | 實現 | 對 Hermes 的價值 |
|------|------|------------------|
| 單進程多網格併發 | `run_multiple_grids.py` | Hermes 無需管理多進程 |
| 熱插拔控制 | `config/active_grids.json` (5秒輪詢) | 最簡單的啟停機制 |
| 全局熔斷器 | `BinanceRest` 類級熔斷 | 自動防 BAN，Hermes 無需擔心 |
| 共享適配器快取 | 餘額/持倉 30s TTL | API 權重已極度節省 |
| WebSocket 驅動 | 訂單成交+行情全走 WS | REST 調用降至最低 |
| YAML 配置體系 | `config/grid/*.yaml` | 參數結構完整，可程式化生成 |
| FastAPI 後端 | port 8001，基礎 CRUD | 可直接擴展 |

### ❌ 缺失項（需新建）

| 需求 | 說明 |
|------|------|
| **Grid Lifecycle API** | 程式化建立/啟動/停止/刪除網格的 REST 接口 |
| **系統健康狀態端點** | Hermes 需知道熔斷狀態、API 餘量再決定是否操作 |
| **YAML 自動生成器** | 從 JSON 參數 → 合規 YAML 配置檔 |
| **運行統計查詢 API** | 查詢各網格即時 PnL、持倉、掛單 |
| **認證與安全層** | API Token 驗證，防止未授權存取 |
| **操作日誌/審計** | 記錄每次 Hermes 的操作決策 |

---

## 🏗️ 系統架構

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
│  │ 🆕 /api/v1/grids/*        Grid Lifecycle CRUD           │   │
│  │ 🆕 /api/v1/system/health  熔斷狀態 + API 餘量            │   │
│  │ 🆕 /api/v1/system/stats   全局統計聚合                    │   │
│  │ 🆕 /api/v1/market/info    交易對精度/限制查詢 (快取)       │   │
│  │    /ws/stats               WebSocket 即時推送 (現有)      │   │
│  └─────────────────────────────────────────────────────────┘   │
│                              │                                  │
│  ┌───────────────┐  ┌───────┴───────┐  ┌──────────────────┐   │
│  │ YAML Generator│  │ active_grids  │  │ SQLite Audit Log │   │
│  │ (參數→配置檔)  │  │ .json 寫入    │  │ (操作記錄)        │   │
│  └───────────────┘  └───────────────┘  └──────────────────┘   │
└────────────────────────────────────┬────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────┐
│              Grid Engine (run_multiple_grids.py)                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ Hot-reload Monitor → 偵測 active_grids.json 變更          │  │
│  │ Shared Adapter     → 全局快取 + 限流鎖 + 熔斷器           │  │
│  │ GridCoordinator×N  → 各網格獨立運行                       │  │
│  └──────────────────────────────────────────────────────────┘  │
│                              │                                  │
│                    WebSocket + 快取式 REST                      │
│                              ▼                                  │
│                      Binance Futures API                        │
└─────────────────────────────────────────────────────────────────┘
```

### 🔑 核心設計原則

1. **Hermes 永不直接碰交易所 API** — 所有交互走本地 REST
2. **零額外 API 權重** — 新接口全讀本地快取/SQLite，不觸發交易所 REST
3. **冪等操作** — 重複調用同一指令不會產生副作用
4. **先查後做** — Hermes 必須先查 `/system/health`，確認非熔斷才操作

---

## 📅 四階段開發計畫

### 階段一：Grid Lifecycle API + YAML 生成器 (3-4 天)

> **目標**：Hermes 能透過 REST 建立、啟動、停止、刪除網格

#### 1.1 API 端點設計

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

#### 1.2 POST /api/v1/grids 請求體 (Hermes 調用格式)

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

#### 1.3 YAML 自動生成器

```python
# web_ui/backend/yaml_generator.py
class GridYAMLGenerator:
    """從 API JSON 參數自動生成合規 YAML 配置檔"""
    
    TEMPLATE = {
        "grid_system": {
            # 基礎參數（必填）
            "exchange": "",
            "symbol": "",
            "grid_type": "",
            "price_range": {"lower_price": 0, "upper_price": 0},
            "grid_interval": 0,
            "order_amount": 0,
            "quantity_precision": 3,
            "price_decimals": 2,
            "leverage": 10,
            "margin_mode": "cross",
            "limit_only": True,
            # 速率保護（硬編碼安全值）
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
    }
    
    def generate(self, params: dict) -> tuple[str, str]:
        """回傳 (yaml_content, file_path)"""
        ...
    
    def validate_params(self, params: dict) -> list[str]:
        """參數驗證，回傳錯誤列表"""
        ...
```

#### 1.4 GET /api/v1/system/health 回應 (Hermes 決策依據)

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
  "running_grids": 2,
  "max_recommended_grids": 5,
  "can_accept_new_grid": true,
  "uptime_seconds": 86400
}
```

> [!IMPORTANT]
> Hermes 應在每次操作前呼叫此端點。若 `can_accept_new_grid: false` 或 `circuit_breaker.is_open: true`，**必須放棄本次操作**。

#### 1.5 認證機制

```python
# 簡單 Bearer Token 驗證（避免未授權存取）
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

#### 2.1 運行時統計收集器

現有 `run_multiple_grids.py` 每 60 秒列印 `[STATS_JSON]`。改為同時寫入共享記憶體字典：

```python
# web_ui/backend/grid_state_store.py
import threading, time

class GridStateStore:
    """進程內共享的網格狀態儲存（無 DB 開銷）"""
    
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

#### 2.2 統計查詢端點

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
  "uptime": "2d 3h 15m",
  "last_update": "2026-05-24T01:30:00+08:00"
}
```

#### 2.3 批次查詢（減少 Hermes 調用次數）

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

#### 3.1 審計日誌 (SQLite)

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

#### 3.2 安全護欄規則

| 規則 | 實現 | 說明 |
|------|------|------|
| 最大同時運行數 | `MAX_CONCURRENT_GRIDS = 5` | 超過拒絕建立 |
| 熔斷中禁操作 | 檢查 `BinanceRest._is_adapter_circuit_open()` | 返回 503 |
| 重複標的檢查 | 同 symbol 已有 active grid 時拒絕 | 返回 409 |
| 操作頻率限制 | 同一 source 60 秒內最多 3 次寫操作 | 返回 429 |
| 最小投資驗證 | `order_amount × grid_count × min_price ≥ MIN_INVEST` | 返回 400 |
| 參數範圍校驗 | leverage ≤ 125, grid_interval > 0, etc. | 返回 422 |

```python
# web_ui/backend/safety_guard.py
class SafetyGuard:
    MAX_CONCURRENT_GRIDS = 5
    OP_RATE_LIMIT = 3        # 次/分鐘
    MIN_GRID_INTERVAL = 0.01
    
    def can_create_grid(self, params: dict) -> tuple[bool, str]:
        """回傳 (允許, 拒絕原因)"""
        # 1. 熔斷檢查
        if BinanceRest._is_adapter_circuit_open():
            return False, "系統處於 API 熔斷狀態，禁止新增網格"
        # 2. 數量限制
        if len(active_grids) >= self.MAX_CONCURRENT_GRIDS:
            return False, f"已達最大並發數 {self.MAX_CONCURRENT_GRIDS}"
        # 3. 重複檢查
        if symbol_already_active(params['symbol']):
            return False, f"標的 {params['symbol']} 已有運行中的網格"
        # 4. 參數驗證
        ...
        return True, ""
```

---

### 階段四：Hermes 端整合指南 + 監控循環設計 (1-2 天)

> **目標**：定義 Hermes 的調用流程與最佳實踐

#### 4.1 Hermes Monitor 循環建議架構

```python
# hermes_grid_controller.py (Hermes 端實現參考)
class HermesGridController:
    """Hermes Agent 控制網格的標準流程"""
    
    def __init__(self, grid_api_url: str, api_token: str):
        self.base_url = grid_api_url  # e.g. "http://localhost:8001"
        self.headers = {"Authorization": f"Bearer {api_token}"}
    
    async def monitor_loop(self):
        """主監控循環 — 建議 15-30 分鐘一次"""
        while True:
            # Step 1: 檢查系統健康
            health = await self.get_health()
            if not health["can_accept_new_grid"]:
                await asyncio.sleep(900)  # 15 min
                continue
            
            # Step 2: 取得當前運行狀態
            summary = await self.get_summary()
            
            # Step 3: AI 分析 — 是否需要調整
            # （此處為 Hermes 自身的 AI 邏輯）
            decisions = await self.ai_analyze(summary)
            
            # Step 4: 執行決策
            for decision in decisions:
                if decision.action == "create":
                    await self.create_grid(decision.params)
                elif decision.action == "stop":
                    await self.stop_grid(decision.grid_id)
            
            await asyncio.sleep(1800)  # 30 min
    
    async def create_grid(self, params: dict) -> dict:
        """建立並啟動網格 — 單次 HTTP 調用"""
        resp = await httpx.post(
            f"{self.base_url}/api/v1/grids",
            json={**params, "auto_start": True},
            headers=self.headers,
            timeout=30
        )
        return resp.json()
```

#### 4.2 Hermes 調用時序圖

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
  │                          │                          │── hot-reload detect
  │                          │                          │── start coordinator
  │←── {id, status: ok} ─────│                          │
  │                          │                          │
  │  (15分鐘後...)            │                          │
  │─── GET /grids/{id}/stats→│                          │
  │←── {pnl: 5.2, ...} ─────│                          │
```

#### 4.3 API 權重影響分析

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

## 📁 檔案變更清單

```
web_ui/backend/
├── main.py                    # 擴展：掛載 v1 路由
├── routes/
│   ├── __init__.py            # 🆕
│   ├── grids.py               # 🆕 Grid Lifecycle CRUD
│   ├── system.py              # 🆕 健康狀態 + 全局統計
│   └── market.py              # 🆕 交易對資訊查詢
├── yaml_generator.py          # 🆕 YAML 配置自動生成
├── grid_state_store.py        # 🆕 運行時狀態共享儲存
├── safety_guard.py            # 🆕 安全護欄
├── auth.py                    # 🆕 Token 認證
└── audit.py                   # 🆕 審計日誌
data/
├── grid_audit.db              # 🆕 操作審計 SQLite
run_multiple_grids.py          # 修改：統計寫入 GridStateStore
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

## 🎯 開發優先級

1. **先做階段一** — 這是 Hermes 能控制網格的最小可行基礎
2. **階段二+三並行** — 狀態查詢讓 Hermes 有決策依據，安全護欄防失控
3. **最後階段四** — 整合指南與範例程式碼

---

## 🔐 與方向一 (Dashboard) 的協同

兩個方向共享同一套後端 API：

- Dashboard 前端調用 `/api/v1/grids` 展示 Bot 列表
- Hermes Agent 調用同一組端點進行自動化控制
- `GridStateStore` 同時服務 WebSocket 推送（Dashboard）和 REST 查詢（Hermes）
- 審計日誌區分 `source: "dashboard"` vs `source: "hermes"`

**建議：先完成本計畫（Hermes API 層），再做 Dashboard 前端。** 因為 API 層是兩者的共同基礎。
