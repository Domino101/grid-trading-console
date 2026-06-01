# 🤖 Hermes Grid API — 階段二完工日誌：狀態同步 + 統計查詢層

> 前置依賴：[階段一完工日誌](file:///root/crypto-trading-open/docs/hermes_grid_modularization_plan_phase1_worklog_20260525.md)（已完成 ✅）
> 基礎計畫書：[`hermes_grid_modularization_plan.md`](file:///root/crypto-trading-open/docs/hermes_grid_modularization_plan.md) §2.1–2.3
> 完工日期：2026-05-25 ✅
> 目標：Hermes 能即時查詢網格運行統計（PnL、持倉、掛單、APR），用於自動化決策

---

## 📊 階段二完工總覽

階段二順利完成了 **網格狀態同步與即時統計查詢層** 的實作，透過進程內共享且 thread-safe 的 `GridStateStore` 提供無開銷、超高效率的資料查詢。

### 實現功能
1. **即時查詢端點**：
   - `GET /api/v1/grids/{grid_id}/stats` -> 獲取單個網格的即時統計資訊（包含現貨與合約保證金、即時 PnL、已成交單數與循環數、當前持倉與均價、掛單詳情、資金利用率、運行時間與最後交易時間）。
   - `GET /api/v1/grids/summary` -> 獲取所有運行中網格的全局 PnL 與統計數據聚合，支援 Hermes Agent 的全局策略管理。
2. **stdout 攔截解析**：
   - 修改 `ProcessManager` 的進程 stream reader 與 PM2 tail reader。當檢測到網格進程輸出的 `[STATS_JSON]` 時，會自動攔截並解析其 JSON 資料，即時寫入 `GridStateStore`，全程零資料庫開銷與外部 API 調用開銷。
3. **動態對齊與補償**：
   - `GridStateStore` 新增了動態 `grid_id` 推導補償機制，防止 FastAPI 回傳 ResponseValidationError，提升了系統的健壯度。
   - `GET /summary` 與 `/stats` 自動與 `active_grids.json` 及專案 YAML 設定對齊，支援網格剛啟動未滿 60 秒（無統計行輸出）時的 stale 預設結構回傳。
4. **清理機制**：
   - 當調用 `DELETE` 端點刪除或歸檔網格時，同步從 `GridStateStore` 清理該網格快取，防範記憶體殘留。

---

## 🏗️ 完工架構與流程

```
Grid Engine (run_multiple_grids.py)
  │ stdout: [STATS_JSON][SOL/USDC:USDC] {...}
  ▼
ProcessManager._read_stream()  ← 修改：新增 _parse_stats_line() 攔截 [STATS_JSON] 行
  │
  ▼
GridStateStore.instance().update(config_path, stats_dict)
  │
  ▼
REST /api/v1/grids/{grid_id}/stats  → 讀取 GridStateStore → 回傳給 Hermes (相容 Pydantic 驗證)
```

---

## 📋 實作任務 Checklist

### 任務 1：建立 GridStateStore（網格狀態共享儲存） — 已完成 ✅
- 新建 `web_ui/backend/grid_state_store.py` 實作 thread-safe 單例快取層。
- 支援 `update()`, `get()`, `get_all()`, `remove()`, 以及全局 `get_summary()`。
- TTL 機制：超過 180 秒未更新的狀態標記為 `stale`。
- 新增動態 `grid_id` 推導及 `symbol` 自動讀取補償，防範 validation errors。

### 任務 2：修改 ProcessManager 攔截 `[STATS_JSON]` — 已完成 ✅
- 修改 `web_ui/backend/process_manager.py` 的 `_read_stream()` 方法。
- 修改 `add_log_listener()` 內部 PM2 的 `tail_reader`。
- 新增 `_parse_stats_line()` 與 `_resolve_config_path()`。
- 正則相容 `[STATS_JSON][SYMBOL] {...}` 與 `[STATS_JSON] {...}`。

### 任務 5：新增 Pydantic 回應模型 — 已完成 ✅
- 於 `web_ui/backend/schemas.py` 中定義完整的回應模型：
  - `GridStatsResponse` (包含 profit、balance、time、filled_orders 等嵌套物件)
  - `GridSummaryItem`
  - `GridSummaryResponse`

### 任務 3 & 4：新增統計與批次查詢端點 — 已完成 ✅
- 於 `web_ui/backend/routes/grids.py` 中新增：
  - `GET /api/v1/grids/summary`
  - `GET /api/v1/grids/{grid_id}/stats`
- 保證 `/summary` 優先定義於 `/{grid_id}` 之前，防範 FastAPI 路由衝突。
- 支援剛啟動無數據網格的回傳，並標記為 `stale: true`。

### 任務 6：DELETE 操作清理快取 — 已完成 ✅
- 修改 `routes/grids.py` 的 `DELETE` 端點，刪除網格時自動調用 `GridStateStore.instance().remove(config_path)`。

### 任務 7：單元與整合測試 — 已完成 ✅
- 在 `tests/test_hermes_api.py` 中新增：
  - `TestGridStateStore`：驗證基本的 CRUD、TTL 過期判定與全局數據聚合。
  - `TestProcessManagerParseStats`：驗證多種 `[STATS_JSON]` 格式攔截、非統計行忽略、無效 JSON 容錯。
  - `TestGridStatsEndpoints`：驗證 `GET /stats` 與 `GET /summary` 端點的 HTTP 回應結構與 Mock token 認證。

---

## 📁 檔案變更清單

| 操作 | 檔案 | 說明 |
|------|------|------|
| 🆕 新建 | [`web_ui/backend/grid_state_store.py`](file:///root/crypto-trading-open/web_ui/backend/grid_state_store.py) | 網格狀態共享儲存 |
| ✏️ 修改 | [`web_ui/backend/process_manager.py`](file:///root/crypto-trading-open/web_ui/backend/process_manager.py) | `_read_stream()` 加入 `[STATS_JSON]` 攔截 |
| ✏️ 修改 | [`web_ui/backend/routes/grids.py`](file:///root/crypto-trading-open/web_ui/backend/routes/grids.py) | 新增 `GET /stats` + `GET /summary` 端點；`DELETE` 清理 state |
| ✏️ 修改 | [`web_ui/backend/schemas.py`](file:///root/crypto-trading-open/web_ui/backend/schemas.py) | 新增 stats/summary 回應模型 |
| ✏️ 修改 | [`tests/test_hermes_api.py`](file:///root/crypto-trading-open/tests/test_hermes_api.py) | 新增 GridStateStore + 解析 + 端點測試 |

---

## 🧪 驗證與測試結果

我們在本地執行了單元與整合測試，包含舊有的階段一測試與新增的階段二測試，共 **41 項測試全部通過 (100% Pass)**！

### 測試指令
```bash
python3 tests/test_hermes_api.py
```

### 測試輸出
```
test_token_constant_time_comparison (__main__.TestAuth.test_token_constant_time_comparison)
驗證 verify_token 使用 secrets.compare_digest ... ok
test_token_resolution_from_env (__main__.TestAuth.test_token_resolution_from_env)
GRID_API_TOKEN 環境變數優先 ... ok
test_basic_crud (__main__.TestGridStateStore.test_basic_crud)
測試狀態更新、獲取與刪除 ... ok
test_get_summary (__main__.TestGridStateStore.test_get_summary)
測試全局數據聚合計算 ... ok
test_stale_detection (__main__.TestGridStateStore.test_stale_detection)
測試 TTL 快取過期判定 ... ok
test_stats_not_found (__main__.TestGridStatsEndpoints.test_stats_not_found)
測試查詢不存在的網格 → 404 ... ok
test_stats_running_but_empty (__main__.TestGridStatsEndpoints.test_stats_running_but_empty)
測試查詢運行中但尚未有統計數據的網格 → 回傳 stale: True 預設結構 ... ok
test_stats_success (__main__.TestGridStatsEndpoints.test_stats_success)
測試查詢有數據的網格 → 200 成功 ... ok
test_summary_success (__main__.TestGridStatsEndpoints.test_summary_success)
測試全局摘要 → 200 成功 ... ok
test_parse_stats_ignored_lines (__main__.TestProcessManagerParseStats.test_parse_stats_ignored_lines)
測試非統計資料行會被忽略且無害 ... ok
test_parse_stats_invalid_json (__main__.TestProcessManagerParseStats.test_parse_stats_invalid_json)
測試無效 JSON 不崩潰 ... ok
test_parse_stats_with_only_stats_json (__main__.TestProcessManagerParseStats.test_parse_stats_with_only_stats_json)
測試 [STATS_JSON] {...} 格式（帶有 default_config_path） ... ok
test_parse_stats_with_symbol_in_brackets (__main__.TestProcessManagerParseStats.test_parse_stats_with_symbol_in_brackets)
測試 [STATS_JSON][SYMBOL] {...} 格式 ... ok
...
Ran 41 tests in 0.649s

OK
```

階段二功能驗證完畢，所有模組穩定上線，系統已具備為 Hermes 提供即時網格運行數據的能力！🚀
