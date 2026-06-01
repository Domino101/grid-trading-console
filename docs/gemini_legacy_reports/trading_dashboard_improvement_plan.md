# 🚀 專業交易控制台全功能開發計畫 v2.0

> 基於現有專案狀態深度分析 + AI 顧問方案整合 + 參考 UI 截圖
> 建立日期：2026-05-24

---

## 🖼️ 參考 UI 截圖詳細描述與功能對照

本開發計畫所依據之目標 UI 截圖，展示了一個**整合式專業網格交易控制台（Trading Console）**，採用暗色系（Dark Theme）專業視覺風格，整體佈局採用**「左-中-右三欄式 + 底部狀態表格」**的經典交易介面設計。

### 1. 頂部導航欄 (Top Header)
* **左側**：標有 `Grid Bot` 主標題，旁邊附帶 `Tutorial` 快速教學連結。
* **右側功能組**：
  * `Quick start`：藍色突出的快速啟動按鈕。
  * `Get Telegram Notifications`：整合 Telegram 通知之配置入口。
  * `Free plan Upgrade`：帳戶升級/訂閱方案標示。
  * **系統圖標組**：包含全局設定、說明文件、系統通知與個人帳戶頭像。

### 2. 左側欄：利潤優化推薦列表 (Profit-Optimized Pairs)
此模組旨在為交易員即時掃描並展示當前市場中高波動率、高回報的交易對，支持複製跟單。
* **頂部篩選器**：
  * **Exchanges**：交易所選擇下拉選單（當前選中 `Binance Futures`）。
  * **Direction**：交易方向篩選（當前選中 `All directions`）。
* **推薦卡片列表**：依 ROI 百分比降序排列，每張卡片包含：
  * 交易對名稱（如 `LAB-USDT`、`BSB-USDT`、`PLAY-USDT`、`NAORIS-USDT` 等）。
  * 網格方向/類型標籤（如 `Long`、`Hedge Grid`、`Short`）。
  * 狀態輔助小圖示（如閃電、鑽石、警告等）。
  * **ROI 數據**：綠色粗體顯著標示預估或歷史回報率（如 `3496.19%`、`2796.98%`）。
  * **Copy 按鈕**：一鍵複製按鈕，點擊後可將該網格的推薦參數自動帶入右側編輯面板。

### 3. 中央欄：TradingView K線圖表 (Chart Panel)
提供高精度的即時行情走勢與網格區間視覺化。
* **標題資訊**：顯示當前行情為 `MEGAUSDT · 15 · Binance Futures (USDT-M)`。
* **即時開高低收與漲跌幅**：顯示 `O:0.08678 H:0.08685 L:0.08606 C:0.08621 -0.00056 (-0.65%)`。
* **圖表主體**：
  * 15 分鐘線的 K 線走勢圖（帶有網格背景線）。
  * 紅色水平虛線標記當前最新價格（`0.08621`）。
  * 左下角顯示 `TradingView` 官方商標，確認採用 Widget 嵌入方式。

### 4. 右側欄：機器人編輯面板 (edit_grid_bot Panel)
提供網格機器人核心參數的配置與操作入口，包含回測與優化功能。
* **標題**：`edit_grid_bot`（右上角帶有關閉 `X` 按鈕）。
* **帳戶選擇 (Exchange account)**：下拉選單選中帳戶 `Binance Futures (USDT-M) mainnew`。
* **保證金模式警告 (Margin Mode Warning)**：黃色醒目字體提示，確保合約保證金模式與當前機器人策略匹配。
* **交易對 (Pair)**：選中 `MEGAUSDT`，帶有搜索鏡頭圖示。
* **網格類型 (Grid type)**：
  * 單選鈕：`Long`、`Neutral`（當前選中）、`Short`。
  * 獨立選項：`Hedge Grid`。
* **投資額配置 (Investment)**：
  * **Available可用餘額**：顯示當前帳戶餘額（`672.24454 USDT`）。
  * **Required所需金額**：顯示此配置下所需的最低啟動資金（`680 USDT`）。
  * **餘額警告**：由於所需金額大於可用金額，面板上顯示橘色警告字樣 `Required amount exceeds your available balance. Leverage may be used.`。
* **單筆交易量 (Amount per trade)**：單位下拉選單（`USDT`）、數值輸入框（`20`），以及右側輔助估算（`~20 USD`）。
* **槓桿調整 (Leverage)**：提供 `+`/`-` 調整框，並附有編輯小鉛筆圖示。
* **下單偏好 (Order Placement Preferences)**：當前為 `By Value (Leverage-agnostic)`，數值為 `20 USDT`。
* **啟動條件 (Bot start condition)**：單選鈕包含 `Immediate`（立即啟動，當前選中）與 `Webhook alert`（信號觸發啟動）。
* **進階設置**：`Settings ∨` 摺疊選單。
* **操作按鈕組**：
  * `Backtest`（回測）：深藍色按鈕，點擊以歷史數據驗證。
  * `Optimize`（參數最佳化）：深藍色按鈕，帶有新消息標籤 `1`，協助尋找最優網格間距與層數。
  * `Save`（儲存並啟動）：下方橫跨的明亮藍色按鈕，保存並立即投入實盤運行。

### 5. 底部欄：機器人列表與運行監控表格 (Bot Table)
即時監控所有機器人的運行績效與歷史記錄。
* **狀態分頁標籤**：`Active`（活動中機器人，藍色選中狀態）與 `Stopped`（已停止歷史）。
* **全局篩選器**：`All markets ▾`、`All exchanges ▾`、`All pairs ▾`。
* **表格數據列項目**（以運行的 `MEGAUSDT` 機器人為例）：
  * **Pair / Exchange**：顯示 `MEGAUSDT` 與交易所 `Binance Futures (USDT-M) mainnew`，並帶有橘色 `Neutral` 網格類型標記。
  * **Date started / In trading**：顯示啟動時間 `30.04.2028 19:38:59` 與累計運行時間 `22d 6h`。
  * **Settings**：簡要配置摘要 `Grid step: 3%`, `Levels: 35`, `High: 0.33`，並可點擊 `more..` 查看完整 JSON/YAML 參數。
  * **Investment / Amount per trade**：投入資金 `680 USDT` / 單次下單金額 `$20.00`。
  * **Realized P/L / Realized trades**：已實現盈虧 `$26.32` / 已成交筆數 `43` 筆。
  * **Unrealized P/L / Unrealized trades**：未實現盈虧 `-$92.53` / 剩餘掛單數 `12` 筆。
  * **Total P/L**：總盈虧為 `-$66.21`，採用粉紅色高亮顯示（因當前為負值）。
  * **Actions（操作工具列）**：
    * `ℹ` 資訊按鈕：查看詳細統計。
    * `✎` 編輯按鈕：拉回右側 edit_grid_bot 修改參數。
    * `▤` 日誌按鈕：打開即時日誌串流（WebSocket Logs）。
    * `■` 停止按鈕：停止該機器人並撤銷掛單。
    * `🗑` 刪除按鈕：刪除該機器人配置。
    * `📤` 分享按鈕：匯出或分享運行績效。

---

## 📊 現狀差距分析 (Gap Analysis)

### ✅ 已具備的能力

| 模組 | 現有狀態 | 備註 |
|------|----------|------|
| 多網格引擎 | `run_multiple_grids.py` — 單進程多工併發，共享適配器 | 已上線穩定運行 |
| 熱插拔控制 | `config/active_grids.json` — 動態啟停特定網格 | 5 秒輪詢偵測 |
| 熔斷器 | `BinanceRest` 類級熔斷 + `.circuit_breaker_state.json` | 全局防 BAN |
| Web UI 後端 | FastAPI + WebSocket log streaming (port 8001) | 基礎 CRUD |
| Web UI 前端 | React + Vite + Recharts，支援 PnL 圖表/日誌/參數卡片 | 僅 2 個頁面 |
| PM2 整合 | `ecosystem.config.js` 管理進程，Web UI 可讀取 PM2 狀態 | 僅讀取 |
| 統計輸出 | `[STATS_JSON]` 格式化統計，`GridStatistics.to_display_dict()` | 結構完整 |

### ❌ 缺失的能力（目標 UI 需求）

| 目標模組 | 缺失項 |
|----------|--------|
| **底部：Bot 列表表格** | 無資料庫持久化、無多 Bot 列表 UI、無 Active/Stopped 篩選 |
| **右側：edit_grid_bot 面板** | 無前端表單新增/編輯 Bot、無 YAML 自動生成、無前端→PM2 串接 |
| **中央：TradingView K 線** | 無 K 線圖表、無動態切換交易對、無網格區間線 |
| **左側：Profit-Optimized Pairs** | 無 ROI 計算引擎、無推薦排名 UI、無 Copy→Edit 聯動 |
| **回測/最佳化** | 無 Backtest API、無 Optimize 流程、無結果彈窗 |
| **資金安全驗證** | 無餘額校驗、無 Margin Mode 警告 |

---

## 🏗️ 系統架構目標

```
[前端 Dashboard 全螢幕 — React + Vite + TypeScript]
 ├── 左模組：ProfitPairs ──► GET /api/recommendations
 ├── 中模組：ChartPanel  ──► TradingView Widget + GET /api/bots/{id}/grid-lines
 ├── 右模組：BotEditor   ──► POST/PUT /api/bots + POST /api/bots/backtest
 └── 下模組：BotTable     ──► GET /api/bots (SQLite) + WebSocket 即時統計

[後端 FastAPI — port 8001]
 ├── /api/bots/*          ──► SQLite CRUD + PM2 控制 + YAML 生成
 ├── /api/recommendations ──► 定時計算 ROI 推薦
 ├── /api/bots/backtest   ──► 非同步回測引擎
 ├── /api/exchange/balance──► 快取式餘額查詢（防頻繁 API 呼叫）
 └── /ws/stats             ──► 全域統計 WebSocket 推送

[引擎層 — Python asyncio]
 └── run_multiple_grids.py ──► 寫入 SQLite performance 表
```

---

## 📅 五階段開發計畫

### 階段一：後端重構 — SQLite 持久化 + API 擴展 (3-4 天)

> **目標**：建立資料庫核心，讓前端有穩定的數據源

#### 1.1 資料庫設計

新建 `data/grid_bots.db` (SQLite)：

```sql
-- 機器人主表
CREATE TABLE bots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,              -- 顯示名稱 e.g. "SOL/USDC Neutral"
    config_path TEXT UNIQUE NOT NULL,       -- YAML 路徑
    symbol      TEXT NOT NULL,              -- e.g. "SOL/USDC:USDC"
    exchange    TEXT DEFAULT 'binance',
    grid_type   TEXT NOT NULL,              -- long/neutral/short/hedge
    investment  REAL DEFAULT 0,             -- 投入資金 USDT
    status      TEXT DEFAULT 'stopped',     -- active/stopped/error
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 動態績效表（引擎每 60 秒寫入）
CREATE TABLE performance (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id          INTEGER REFERENCES bots(id),
    realized_pnl    REAL DEFAULT 0,
    unrealized_pnl  REAL DEFAULT 0,
    total_pnl       REAL DEFAULT 0,
    current_price   REAL DEFAULT 0,
    position_amount REAL DEFAULT 0,
    pending_orders  INTEGER DEFAULT 0,
    filled_buy      INTEGER DEFAULT 0,
    filled_sell     INTEGER DEFAULT 0,
    recorded_at     DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_perf_bot ON performance(bot_id, recorded_at DESC);
```

#### 1.2 引擎側改造

修改 `run_multiple_grids.py` 的統計迴圈，每 60 秒除了 `print([STATS_JSON])` 外，同步寫入 SQLite：

```python
# 新增 db_writer.py 模組
import sqlite3, threading

class GridDBWriter:
    def __init__(self, db_path="data/grid_bots.db"):
        self.db_path = db_path
        self._init_db()

    def upsert_bot(self, config_path, symbol, grid_type, status="active"):
        ...

    def update_performance(self, config_path, stats_dict):
        ...

    def set_bot_status(self, config_path, status):
        ...
```

#### 1.3 新增 API 端點

| 方法 | 路徑 | 功能 |
|------|------|------|
| `GET` | `/api/bots` | 列出所有 Bot（支援 `?status=active` 篩選） |
| `GET` | `/api/bots/{id}` | 取得單一 Bot 詳情 + 最新績效 |
| `POST` | `/api/bots` | 新增 Bot（生成 YAML + 寫入 DB） |
| `PUT` | `/api/bots/{id}` | 編輯 Bot 參數（更新 YAML + restart） |
| `POST` | `/api/bots/{id}/toggle` | 啟動/停止（操作 `active_grids.json` 或 PM2） |
| `DELETE` | `/api/bots/{id}` | 刪除 Bot（停止 + 刪除 YAML + 標記 DB） |
| `GET` | `/api/exchange/balance` | 取得帳戶可用餘額（30 秒快取） |

#### 1.4 餘額快取機制（防 Rate Limit）

```python
# 在後端維護餘額快取，前端永遠只問本地後端
_balance_cache = {"value": None, "expires": 0}

async def get_cached_balance():
    if time.time() < _balance_cache["expires"]:
        return _balance_cache["value"]
    balance = await shared_adapter.get_balance()  # 一次 REST 調用
    _balance_cache["value"] = balance
    _balance_cache["expires"] = time.time() + 30
    return balance
```

---

### 階段二：前端核心改造 — Bot 列表 + 編輯面板 (4-5 天)

> **目標**：實現截圖中的底部表格 + 右側編輯面板

#### 2.1 底部 Bot 列表表格 (`BotTable.tsx`)

參照截圖中的表格，欄位包含：

| 欄位 | 數據來源 |
|------|----------|
| Pair / Exchange | `bots.symbol` + `bots.exchange` |
| Grid Type Badge | `bots.grid_type` → 彩色標籤 (Neutral=藍/Long=綠/Short=紅) |
| Date Started | `bots.created_at` |
| Settings (interval/levels/high) | 從 YAML 解析或 DB 冗餘存儲 |
| Investment | `bots.investment` |
| Realized P/L | `performance.realized_pnl` |
| Unrealized P/L | `performance.unrealized_pnl` |
| Total P/L | `performance.total_pnl` |
| Actions | ▶️停止 / ✏️編輯 / 📋複製 / 🗑刪除 |

頂部篩選列：`[Active] [Stopped] [All markets ▾] [All exchanges ▾] [All pairs ▾]`

#### 2.2 右側編輯面板 (`BotEditor.tsx`)

```
┌─────────────────────────┐
│ edit_grid_bot        ✕  │
│─────────────────────────│
│ Exchange account        │
│ [Binance Futures ▾]     │
│                         │
│ ⚠ Margin Mode Warning  │
│                         │
│ Pair                    │
│ [🔍 SOLUSDC        ▾]  │
│                         │
│ Grid type               │
│ ○ Long ● Neutral ○ Short│
│ ○ Hedge Grid            │
│                         │
│ Investment              │
│ Available: XXX USDT     │
│ Required:  XXX USDT     │
│ ⚠ 紅字警告(超額時)       │
│                         │
│ Amount per trade        │
│ [USDT ▾] [20] [-20 USD] │
│                         │
│ [Backtest] [Optimize]   │
│ [        Save         ] │
└─────────────────────────┘
```

**關鍵安全機制**：
- 點擊 Save 前，前端呼叫 `GET /api/exchange/balance` 校驗餘額
- 若 `required > available`：Save 按鈕 disabled + 紅字警告
- Margin Mode 不匹配時顯示黃色警告

#### 2.3 YAML 自動生成流程

```
用戶填表 → POST /api/bots → 後端依據模板生成 YAML
                            → 寫入 config/grid/{name}.yaml
                            → INSERT INTO bots
                            → 更新 active_grids.json (true)
                            → 引擎熱插拔監控自動啟動
```

YAML 模板基於現有 `binance-neutral-perp-sol-usdc-100-160.yaml` 結構。

---

### 階段三：中央 K 線圖表 + 網格視覺化 (3-4 天)

> **目標**：嵌入 TradingView K 線，實現點擊表格切換交易對

#### 3.1 TradingView Widget 嵌入 (`ChartPanel.tsx`)

使用 TradingView 官方免費 [Advanced Chart Widget](https://www.tradingview.com/widget/advanced-chart/)：

```tsx
// 核心：動態切換 symbol
useEffect(() => {
  if (!containerRef.current || !selectedSymbol) return;
  new TradingView.widget({
    container_id: "tv_chart",
    symbol: `BINANCE:${selectedSymbol.replace("/", "").replace(":USDC", "PERP")}`,
    theme: "dark",
    style: "1",
    interval: "15",
    timezone: "Asia/Taipei",
    allow_symbol_change: true,
    height: "100%",
    width: "100%",
  });
}, [selectedSymbol]);
```

#### 3.2 網格區間線（進階）

新增 API `GET /api/bots/{id}/grid-lines` 回傳 `{ upper, lower, grid_interval }`，前端透過 TradingView 的 `createPositionLine()` 繪製水平線。

#### 3.3 聯動邏輯

- 點擊底部 BotTable 某行 → `setSelectedSymbol("SOL/USDC:USDC")` → ChartPanel 重載
- 同時右側 BotEditor 帶入該 Bot 參數

---

### 階段四：左側推薦列表 + Copy 聯動 (2-3 天)

> **目標**：實現 Profit-Optimized Pairs 推薦面板

#### 4.1 ROI 計算引擎

新增 `scripts/roi_scanner.py`（定時任務，每 4 小時執行一次）：

```python
# 計算邏輯：
# 1. 從 Binance 取得過去 7 天 K 線數據（公開 API，無需 API Key）
# 2. 計算波動率 (ATR / price) × grid_type_factor
# 3. 模擬假設的網格 ROI
# 4. 結果寫入 data/recommendations.json
```

#### 4.2 推薦面板 UI (`ProfitPairs.tsx`)

```
┌─────────────────────┐
│ Profit-Optimized    │
│ Pairs               │
│─────────────────────│
│ Exchanges  Direction│
│ [Binance▾] [All ▾]  │
│─────────────────────│
│ LAB-USDT      Long  │
│ 🔥💎⚡ ...          │
│ ROI: 3496.19%       │
│           [Copy]    │
│─────────────────────│
│ BSR-USDT  Hedge Grid│
│ ROI: 2796.98%       │
│           [Copy]    │
│─────────────────────│
│ ...                 │
└─────────────────────┘
```

#### 4.3 Copy 聯動

點擊 `[Copy]` → 自動填充右側 BotEditor 的 Pair、Grid Type、推薦參數 → 用戶微調後點 Save 上線。

---

### 階段五：回測與最佳化整合 (5-7 天)

> **目標**：在正式上線前可先跑歷史數據驗證策略

#### 5.1 後端回測 API

```
POST /api/bots/backtest
Body: { symbol, grid_type, lower_price, upper_price, grid_interval, 
        order_amount, days: 30 }
Response: { roi, max_drawdown, total_trades, sharpe_ratio, 
            equity_curve: [...] }
```

後端使用現有 K 線數據模擬網格成交。

#### 5.2 前端整合

- BotEditor 的 `[Backtest]` 按鈕 → Loading 動畫 → 彈窗顯示結果
- `[Optimize]` 按鈕 → 後端跑多組參數組合 → 回傳最佳參數組合
- 用戶滿意後點 `[Save]` 上線實盤

---

## 🔐 安全護欄設計

### 資金安全

| 校驗項 | 實作方式 |
|--------|----------|
| 餘額不足 | Save 前呼叫 `/api/exchange/balance`，不足時 disabled |
| Margin Mode 衝突 | 偵測帳戶 margin mode，不匹配時黃色警告 |
| 重複交易對 | 新增 Bot 時檢查是否已有相同 symbol 的 active Bot |
| 槓桿超限 | 校驗槓桿倍數是否在交易所允許範圍內 |

### API 頻率保護

| 策略 | 細節 |
|------|------|
| 餘額快取 | 後端 30 秒 TTL，前端不直接呼叫交易所 |
| 績效數據 | 引擎每 60 秒寫入 DB，前端讀 DB 不讀交易所 |
| K 線數據 | TradingView Widget 自帶數據源，零 API 消耗 |
| 推薦計算 | 使用公開 API，每 4 小時一次，與交易 API Key 無關 |

---

## 📐 前端佈局架構

```
┌─────────────────────────────────────────────────────────────┐
│ Header: [Grid Bot] [Tutorial]  [Quick Start] [Settings] [⚙]│
├────────┬──────────────────────────────┬─────────────────────┤
│        │                              │                     │
│  左側   │     中央 TradingView         │   右側               │
│ Profit │     K 線圖表                  │  edit_grid_bot      │
│ Pairs  │     + 網格區間線              │  表單面板             │
│        │                              │                     │
│ 240px  │        flex: 1               │     320px           │
│        │                              │                     │
├────────┴──────────────────────────────┴─────────────────────┤
│ 底部：Bot 列表表格                                           │
│ [Active] [Stopped] [All markets ▾] [All exchanges ▾]       │
│ ┌──────┬──────┬────────┬──────┬──────┬──────┬───────┐      │
│ │ Pair │ Date │Settings│Invest│ R.PL │ U.PL │Actions│      │
│ └──────┴──────┴────────┴──────┴──────┴──────┴───────┘      │
│ height: ~200px                                              │
└─────────────────────────────────────────────────────────────┘
```

---

## 📋 檔案結構變更

```
web_ui/
├── backend/
│   ├── main.py                 # 擴展 API 路由
│   ├── db.py                   # 🆕 SQLite 連接管理
│   ├── db_writer.py            # 🆕 引擎寫入模組
│   ├── routes/
│   │   ├── bots.py             # 🆕 Bot CRUD API
│   │   ├── exchange.py         # 🆕 餘額/交易所查詢 API
│   │   └── recommendations.py  # 🆕 推薦 API
│   ├── process_manager.py      # 現有，擴展 PM2 控制
│   └── backtest_engine.py      # 🆕 回測引擎
├── frontend/src/
│   ├── App.tsx                 # 重構為全屏佈局
│   ├── components/
│   │   ├── Dashboard.tsx       # 保留，作為總覽頁
│   │   ├── TradingConsole.tsx  # 🆕 主控制台（四模組佈局）
│   │   ├── BotTable.tsx        # 🆕 底部 Bot 列表
│   │   ├── BotEditor.tsx       # 🆕 右側編輯面板
│   │   ├── ChartPanel.tsx      # 🆕 中央 K 線
│   │   ├── ProfitPairs.tsx     # 🆕 左側推薦
│   │   └── BacktestModal.tsx   # 🆕 回測結果彈窗
│   └── services/
│       └── api.ts              # 擴展 API 方法
data/
└── grid_bots.db                # 🆕 SQLite 資料庫
scripts/
└── roi_scanner.py              # 🆕 ROI 計算定時任務
```

---

## ⏱️ 時間估算

| 階段 | 預估天數 | 依賴 |
|------|----------|------|
| 一：SQLite + API | 3-4 天 | 無 |
| 二：BotTable + BotEditor | 4-5 天 | 階段一 |
| 三：TradingView 圖表 | 3-4 天 | 階段二 |
| 四：推薦列表 | 2-3 天 | 階段二 |
| 五：回測/最佳化 | 5-7 天 | 階段一~二 |
| **總計** | **17-23 天** | |

> [!TIP]
> 階段三和四可以與階段二並行開發，階段五可最後獨立進行。實際最短路徑約 **12-15 天**。

---

## 🎯 開發優先級建議

1. **先做階段一+二**（DB + 表格 + 編輯面板）— 這是核心功能價值
2. **再做階段三**（K 線圖表）— 嵌入 TradingView Widget 難度低、視覺衝擊大
3. **階段四**（推薦）— 錦上添花
4. **最後階段五**（回測）— 複雜度最高但非即時必需
