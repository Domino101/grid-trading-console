# 🤖 Hermes Grid API — 階段一完工日誌

> 日期：2026-05-25
> 基礎計畫書：[`hermes_grid_modularization_plan.md`](file:///root/crypto-trading-open/docs/hermes_grid_modularization_plan.md)
> 對應階段：階段一 — Grid Lifecycle API + YAML 生成器

---

## 📋 階段一完工項目 Checklist

### 1. API 認證模組 (`auth.py`)

- [x] 建立 [`web_ui/backend/auth.py`](file:///root/crypto-trading-open/web_ui/backend/auth.py)
- [x] 實現 Bearer Token 認證（`HTTPBearer` + `verify_token` dependency）
- [x] Token 來源三級 fallback：環境變數 `GRID_API_TOKEN` → 本地檔案 `.grid_api_token` → 生成 ephemeral 隨機 token + 嚴重警告
- [x] 使用 `secrets.compare_digest()` 常數時間比較，防止 timing attack
- [x] 在 `.env` 中設定持久化 `GRID_API_TOKEN`（64 字元 hex）
- [x] 在 `main.py` 啟動時透過 `python-dotenv` 載入 `.env`

### 2. Pydantic 請求/回應模型 (`schemas.py`)

- [x] 建立 [`web_ui/backend/schemas.py`](file:///root/crypto-trading-open/web_ui/backend/schemas.py)
- [x] `CreateGridRequest` — 完整 30+ 欄位（必填 + 速率保護 + Follow 模式 + 剝頭皮 + 本金保護 + 止盈 + 價格鎖定 + 止損 + 馬丁格爾 + 持倉容差）
- [x] `GridMetadata` — Hermes 操作元資料（source / reason / confidence）
- [x] `GridInfo` / `GridListResponse` — GET 列表回應
- [x] `GridCreateResponse` — POST 建立回應（含 yaml_content）
- [x] `GridActionResponse` — Start/Stop/Delete 操作回應
- [x] `SystemHealthResponse` — 系統健康狀態回應（含 CircuitBreakerInfo / ApiThrottleInfo / EngineInfo）
- [x] `MarketInfoResponse` — 交易對精度/最小下單量回應

### 3. 安全護欄 (`safety_guard.py`)

- [x] 建立 [`web_ui/backend/safety_guard.py`](file:///root/crypto-trading-open/web_ui/backend/safety_guard.py)
- [x] `check_circuit_breaker()` — 讀取 `BinanceRest._is_adapter_circuit_open()` 靜態屬性
- [x] `check_max_grids()` — 運行中網格數上限檢查（MAX_CONCURRENT_GRIDS = 5）
- [x] `check_duplicate_symbol()` — 同 symbol 已有 active grid 時拒絕（讀取 YAML 比對）
- [x] `check_engine_alive()` — 引擎進程存活檢查（讀 `/tmp/grid_multiple_runners.pid` + `os.kill(pid, 0)`）
- [x] `check_config_dir_writable()` — config/grid/ 目錄存在且可寫
- [x] `check_config_not_exists()` — 防止意外覆蓋已有配置
- [x] `check_can_create_grid()` — 建立網格前的完整安全檢查（組合以上 4 項）
- [x] `get_engine_pid()` — 取得引擎進程 PID

### 4. YAML 配置自動生成器 (`yaml_generator.py`)

- [x] 建立 [`web_ui/backend/yaml_generator.py`](file:///root/crypto-trading-open/web_ui/backend/yaml_generator.py)
- [x] `validate_params()` — 完整參數驗證（必填/grid_type/價格區間/follow_grid_count/數值範圍/exchange/margin_mode）
- [x] `generate()` — JSON → YAML 轉換
  - [x] grid_system 基礎結構
  - [x] 價格區間（非 follow 模式）vs follow_grid_count（follow 模式）
  - [x] Binance 安全預設值自動填充（`BINANCE_SAFE_DEFAULTS`：limit_only / ws_only_mode / active_window 等 11 項）
  - [x] 可選高級參數條件寫入（`OPTIONAL_KEYS`：17 項）
- [x] `_build_file_path()` — 安全檔名生成（清理特殊字元，防止路徑穿越）
- [x] `grid_id_from_path()` / `path_from_grid_id()` — 雙向轉換

### 5. Grid Lifecycle CRUD 端點 (`routes/grids.py`)

- [x] 建立 [`web_ui/backend/routes/grids.py`](file:///root/crypto-trading-open/web_ui/backend/routes/grids.py)
- [x] `GET /api/v1/grids` — 列出所有網格（掃描 active_grids.json + config/grid/ 目錄 YAML）
- [x] `GET /api/v1/grids/{grid_id}` — 單一網格詳細資訊（讀 YAML 填充 GridInfo）
- [x] `POST /api/v1/grids` — 建立新網格
  - [x] 參數驗證 → 安全護欄 → 重複交易對 → 生成 YAML 寫磁碟 → auto_start 寫 active_grids.json
  - [x] 配置檔案覆蓋防護
  - [x] Hermes metadata 記錄
- [x] `POST /api/v1/grids/{grid_id}/start` — 啟動網格（寫入 active_grids.json = true）
  - [x] 冪等設計（already_running 直接回傳）
  - [x] 安全護欄前置檢查
- [x] `POST /api/v1/grids/{grid_id}/stop` — 停止網格（寫入 active_grids.json = false）
  - [x] 冪等設計（already_stopped 直接回傳）
- [x] `DELETE /api/v1/grids/{grid_id}` — 停止 + 歸檔 YAML（移至 config/grid/archived/）
  - [x] 先停止 → 從 active_grids 移除 → 歸檔 YAML

### 6. 系統健康狀態端點 (`routes/system.py`)

- [x] 建立 [`web_ui/backend/routes/system.py`](file:///root/crypto-trading-open/web_ui/backend/routes/system.py)
- [x] `GET /api/v1/system/health` — 系統健康狀態（零 API 權重）
  - [x] 熔斷器狀態（讀 `BinanceRest` 類變量）
  - [x] API 限速器統計（讀 `BinanceRest.get_throttle_stats()`）
  - [x] 引擎進程狀態（讀 PID 檔案）
  - [x] 運行中網格數/清單（讀 active_grids.json）
  - [x] 綜合狀態判定（healthy / degraded / unhealthy）
  - [x] `can_accept_new_grid` 計算

### 7. 交易對資訊查詢端點 (`routes/market.py`)

- [x] 建立 [`web_ui/backend/routes/market.py`](file:///root/crypto-trading-open/web_ui/backend/routes/market.py)
- [x] `GET /api/v1/market/info/{symbol}` — 查詢交易對精度/最小下單量
  - [x] 60 秒記憶體快取（`_market_cache`）
  - [x] ccxt 臨時實例查詢 markets
  - [x] 回傳 price_precision / quantity_precision / min_order_amount / min_notional / contract_size

### 8. FastAPI 主應用整合 (`main.py`)

- [x] 修改 [`web_ui/backend/main.py`](file:///root/crypto-trading-open/web_ui/backend/main.py)
- [x] 掛載 v1 路由（system_router / grids_router / market_router）
- [x] 與現有 `/api/scripts`、`/api/configs`、`/api/process/*`、`/ws/logs/*` 端點共存
- [x] 載入 `.env` 環境變數

### 9. 路由包初始化 (`routes/__init__.py`)

- [x] 建立 [`web_ui/backend/routes/__init__.py`](file:///root/crypto-trading-open/web_ui/backend/routes/__init__.py)

---

## 🐛 修復的 Bug

### Bug 1: `market.py` 同步/異步混用 (嚴重)

**問題位置**：[`routes/market.py`](file:///root/crypto-trading-open/web_ui/backend/routes/market.py) 原 L97-104

**症狀**：
- `loop.run_in_executor(None, ex.load_markets)` 返回 coroutine 但未 await
- 緊接著又同步調用 `ex.load_markets()`，blocking event loop
- 存在死代碼（`mapped = symbol` 無效變數）

**修復**：
- 重命名為 `_lookup_market_from_ccxt_sync()` — 純同步函式
- 在 async 端點中用 `await loop.run_in_executor(None, ...)` 包裝
- 移除無效代碼

### Bug 2: `system.py` window_max 預設值錯誤

**問題位置**：[`routes/system.py`](file:///root/crypto-trading-open/web_ui/backend/routes/system.py) L62

**症狀**：`ApiThrottleInfo(window_max=3)` — Binance 實際限制為 2400/分鐘

**修復**：改為 `window_max=2400`

### Bug 3: `.env` 未配置 + dotenv 未載入

**問題位置**：[`main.py`](file:///root/crypto-trading-open/web_ui/backend/main.py) + [`.env`](file:///root/crypto-trading-open/.env)

**症狀**：
- `main.py` 沒有調用 `load_dotenv()`，即使 `.env` 中有 token 也讀不到
- `.env` 中沒有 `GRID_API_TOKEN`，每次啟動生成臨時 token

**修復**：
- `main.py` 啟動時 `load_dotenv()`（在所有模組 import 之前）
- `.env` 追加 `GRID_API_TOKEN=<64-char-hex>`

---

## ✅ 測試驗證

### 單元測試（30/30 全通過）

建立 [`tests/test_hermes_api.py`](file:///root/crypto-trading-open/tests/test_hermes_api.py)：

| 測試類別 | 測試數 | 覆蓋範圍 |
|----------|--------|----------|
| `TestYAMLGenerator` | 14 | validate_params 10 種情境 + generate 4 種模式 + grid_id 轉換 |
| `TestSafetyGuard` | 7 | 目錄可寫/不存在 + 配置覆蓋 + 上限/重複/停止狀態 |
| `TestSchemas` | 5 | 最小/完整請求 + ValidationError + dump + 回應序列化 |
| `TestAuth` | 2 | env 優先級 + constant-time compare |

```
Ran 30 tests in 0.539s — OK
```

### 語法檢查

所有 8 個模組檔案 + 測試檔案均通過 `ast.parse()` 驗證。

---

## 📁 檔案變更清單

| 操作 | 檔案 | 說明 |
|------|------|------|
| 🆕 新建 | `web_ui/backend/auth.py` | API Token 認證模組 |
| 🆕 新建 | `web_ui/backend/schemas.py` | Pydantic 請求/回應模型 |
| 🆕 新建 | `web_ui/backend/safety_guard.py` | 安全護欄（6 項檢查） |
| 🆕 新建 | `web_ui/backend/yaml_generator.py` | YAML 配置自動生成器 |
| 🆕 新建 | `web_ui/backend/routes/__init__.py` | 路由包初始化 |
| 🆕 新建 | `web_ui/backend/routes/grids.py` | Grid Lifecycle CRUD（6 端點） |
| 🆕 新建 | `web_ui/backend/routes/system.py` | 系統健康狀態端點 |
| 🆕 新建 | `web_ui/backend/routes/market.py` | 交易對資訊查詢端點 |
| 🆕 新建 | `tests/test_hermes_api.py` | 離線單元測試（30 tests） |
| ✏️ 修改 | `web_ui/backend/main.py` | 掛載 v1 路由 + dotenv 載入 |
| ✏️ 修改 | `.env` | 追加 `GRID_API_TOKEN` |

---

## 📊 階段一 API 端點總覽

| 方法 | 路徑 | 功能 | 認證 | API 權重 |
|------|------|------|------|----------|
| `GET` | `/api/v1/system/health` | 系統健康狀態 | ✅ Bearer | 0 |
| `GET` | `/api/v1/grids` | 列出所有網格 | ✅ Bearer | 0 |
| `GET` | `/api/v1/grids/{grid_id}` | 單一網格資訊 | ✅ Bearer | 0 |
| `POST` | `/api/v1/grids` | 建立新網格 | ✅ Bearer | 0 |
| `POST` | `/api/v1/grids/{grid_id}/start` | 啟動網格 | ✅ Bearer | 0 |
| `POST` | `/api/v1/grids/{grid_id}/stop` | 停止網格 | ✅ Bearer | 0 |
| `DELETE` | `/api/v1/grids/{grid_id}` | 停止+刪除 | ✅ Bearer | 0 |
| `GET` | `/api/v1/market/info/{symbol}` | 交易對精度 | ✅ Bearer | ≤10(首次) |

---

## ⏭️ 後續工作

階段一已 100% 完成。下一步進入：
- **階段二**：狀態同步 + 統計查詢層（GridStateStore + stats/summary 端點）
- **階段三**：操作審計 + 安全護欄強化（SQLite 審計表 + 頻率限制）

詳見：[`hermes_grid_api_phase2_plan.md`](file:///root/crypto-trading-open/docs/hermes_grid_api_phase2_plan.md)
