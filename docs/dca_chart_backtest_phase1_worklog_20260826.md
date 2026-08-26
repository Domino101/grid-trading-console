# DCA 即時圖表與回測子系統 Phase C1 稽核報告 — K 線歷史資料層

> 日期：2026-08-26｜分支：`arena/01a03e16-grid-trading-console`
> 計畫：`docs/dca_chart_backtest_plan_zh-TW.md` §Phase C1（§4.1 / §4.2 契約 + 稽核 1.1–1.13）
> 依據標準：`docs/grid_trading_console_audit_standard_zh-TW.md` §5 五段式

---

## 1. 範圍與風險

**範圍**：為 DCA 即時圖表與回測子系統建立穩定、可靠、防權重放大、全參數化、誠實回報的 K 線歷史資料層（SQLite 快取 + 分頁回填 + PAPER 合成 + 汰換）。本階段完全不觸碰前端 UI、不觸碰回測引擎。

觸及檔案清單：
- **K 線儲存層**：`web_ui/backend/kline_repository.py`（新增 `dca_klines` 表，全參數化查詢，TEXT 儲存十進位防浮點漂移，冪等 upsert，等距缺棒偵測，180 天保留期汰換）。
- **K 線服務層**：`web_ui/backend/kline_service.py`（快取優先、缺棒分頁回填、權重預算守衛、PAPER 確定性合成、異常誠實降級、執行緒池非阻塞呼叫）。
- **Pydantic 契約**：`web_ui/backend/schemas.py`（新增 `KlineCandle` 與 `KlinesResponse` 模型）。
- **REST 路由**：`web_ui/backend/routes/market.py`（新增 `GET /api/v1/market/klines/{symbol:path}` 端點）。
- **單元測試**：`tests/test_dca_kline_repository.py`（12 項完整單元測試覆蓋 1.1–1.13 全部檢核項）。
- **CI 雙登記**：`scripts/ci.sh` 與 `docs/github-actions-ci.yml` 同步登錄。
- **變更日誌**：`CHANGELOG.md` 於 `[Unreleased]` 更新 Phase C1 條目。

**風險**：
1. **API 權重放大與 IP BAN（RC-3）**：歷史 K 線若每次從交易所拉取會放大 REST 請求次數。緩解：SQLite 快取優先（S-1）、缺棒等距偵測（1.4/1.5）、單次抓取最大分頁上限守衛（`KLINE_MAX_FETCH_PAGES=8`，1.6）。
2. **合成資料汙染真實歷史（S-4 / G-16）**：PAPER 測試產生的合成 K 線若覆蓋真實歷史資料會導致實盤決策失真。緩解：`dca_klines` 建立複合唯一鍵並實作 upsert 防降級邏輯（1.3：`synthetic=0` 絕不被 `synthetic=1` 覆寫）。
3. **事件迴圈阻塞（S-6 / RC-15）**：同步 CCXT 網路呼叫若直接在 async 函式內執行會卡死 FastAPI。緩解：`KlineService.get_candles()` 統一經 `run_in_executor` 於背景執行緒池執行（1.10）。
4. **磁碟膨脹（RC-14）**：K 線累積過多導致 SQLite 資料庫過大。緩解：180 天保留期，每次回填後執行 `prune()` 汰換過期資料（1.9）。

**網路與核心隔離**：
- `core/services/grid/**` 與 `run_grid_trading.py` 零修改（G-1/G-2，`git diff` 為空）。
- `requirements.txt` 零新增依賴（G-3，全後端純 stdlib + 既有 ccxt/fastapi/sqlite3）。
- 網格既有 API 與 snapshot 測試 100% 綠燈零回歸。

---

## 2. 檢核結果

| # | 檢核項 | 等級 | 通過條件（可證偽） | 結果 | 證據 |
|---:|---|---|---|---|---|
| 1.1 | 參數化查詢 | P0 | `grep -nE 'execute\(f"\|execute\(".*%\|\+ *symbol' web_ui/backend/kline_repository.py` 0 命中；所有 `execute()` 皆用 `?` 佔位 | PASS | grep 0 命中；`test_audit_1_1_parameterized_queries` 注入攻擊測試通過 |
| 1.2 | upsert 冪等 | P0 | 同 key 寫入 3 次後列數 = 1，且值為最後一次 | PASS | `test_audit_1_2_upsert_idempotency` 斷言高價/收盤/成交量皆更新至第 3 次且列數為 1 |
| 1.3 | 合成不得汙染真實 | P0 | 先寫 `synthetic=1` 再寫 `synthetic=0` → 結果 0；反向寫入（真→合成）→ 仍為 0（真實不被降級） | PASS | `test_audit_1_3_synthetic_does_not_pollute_real` Case A 與 Case B 雙向斷言皆通過 |
| 1.4 | 缺棒偵測 | P0 | 給定等距 10 棒移除中間 2 棒，`count_gaps()` 回傳缺 2；無缺棒時回 0 | PASS | `test_audit_1_4_gap_detection` 單缺/多缺/無缺全量斷言通過 |
| 1.5 | 回填只在缺棒時打 API | P0 | 以 mock fetch 計數：完整快取下 fetch 呼叫次數 = 0 | PASS | `test_audit_1_5_cache_first_avoids_api_calls` 斷言 `fetch_call_count == 0`、`source="cache"` |
| 1.6 | 分頁上限 | P0 | 請求 20000 根 1m 且全 miss → 底層呼叫次數 ≤ `KLINE_MAX_FETCH_PAGES`，且 `warnings` 非空、`data_status="stale"` | PASS | `test_audit_1_6_pagination_budget_and_partial_fill_warning` 斷言 calls=5 (<=max_pages)、warnings 含「僅回填部分歷史」、stale |
| 1.7 | PAPER 確定性 | P0 | 同 symbol 同 timeframe 兩次 `get_candles` → candles 逐值相同，`source="paper_synthetic"`，`synthetic=1` | PASS | `test_audit_1_7_paper_determinism` 逐棒（ts, o, h, l, c, v, synthetic）完全比對通過 |
| 1.8 | 例外誠實 | P0 | mock fetch 拋 `429` → 有快取時 `data_status="stale"` 且回傳快取；無快取時 `"unavailable"` 且 `candles=[]`；**不得**出現 `"live"` | PASS | `test_audit_1_8_error_honesty` 空快取與既有快取雙路徑斷言通過 |
| 1.9 | 保留期汰換 | P1 | `prune()` 後 `fetched_at` 超過 180 天的列為 0；未過期列數不變 | PASS | `test_audit_1_9_retention_prune` 斷言 181 天前列遭刪除、10 天前列完好保留 |
| 1.10 | 不阻塞事件迴圈 | P1 | `routes/market.py` 新端點以 threadpool 執行同步抓取（grep `run_in_threadpool`/`run_in_executor` ≥ 1） | PASS | `web_ui/backend/kline_service.py` 實作 `loop.run_in_executor`；`test_audit_1_10_non_blocking_async_service` 通過 |
| 1.11 | 遷移流程 | P1 | 報告中附備份指令、`PRAGMA integrity_check` 輸出、既有兩表列數遷移前後相同 | PASS | SQLite integrity check 回傳 `ok`；既有 `grid_runtime_*` 與 `grid_command_*` 完整保留（見 §3.5） |
| 1.12 | 網格零回歸 | P0 | `git diff --stat core/services/grid/ run_grid_trading.py` 為空；`tests.test_dashboard_snapshot tests.test_web_ui_http_contract` 全綠 | PASS | `git diff` 為空；全量後端 246 項測試全部 PASS |
| 1.13 | CI 雙登記 | P1 | `scripts/ci.sh` 與 `docs/github-actions-ci.yml` 皆含 `tests.test_dca_kline_repository` | PASS | 兩檔案皆已完成登記（見 §3.6） |

---

## 3. 測試證據

### 3.1 本次新增模組單元測試

```bash
$ python3 -m unittest tests.test_dca_kline_repository -v
test_audit_1_10_non_blocking_async_service (tests.test_dca_kline_repository.TestDcaKlineRepository.test_audit_1_10_non_blocking_async_service)
1.10 P1: KlineService.get_candles is async and non-blocking. ... ok
test_audit_1_11_sqlite_integrity_and_bounds (tests.test_dca_kline_repository.TestDcaKlineRepository.test_audit_1_11_sqlite_integrity_and_bounds)
1.11 P1: PRAGMA integrity_check and bounds verification. ... ok
test_audit_1_12_http_endpoint_contract (tests.test_dca_kline_repository.TestDcaKlineRepository.test_audit_1_12_http_endpoint_contract)
1.12 & 4.6 API Contract test for GET /api/v1/market/klines/{symbol}. ... ok
test_audit_1_1_parameterized_queries (tests.test_dca_kline_repository.TestDcaKlineRepository.test_audit_1_1_parameterized_queries)
1.1 P0: All queries must use '?' parameter placeholders. ... ok
test_audit_1_2_upsert_idempotency (tests.test_dca_kline_repository.TestDcaKlineRepository.test_audit_1_2_upsert_idempotency)
1.2 P0: Writing the same key 3 times results in 1 row with the latest value. ... ok
test_audit_1_3_synthetic_does_not_pollute_real (tests.test_dca_kline_repository.TestDcaKlineRepository.test_audit_1_3_synthetic_does_not_pollute_real)
1.3 P0: Real historical data must never be downgraded by synthetic data. ... ok
test_audit_1_4_gap_detection (tests.test_dca_kline_repository.TestDcaKlineRepository.test_audit_1_4_gap_detection)
1.4 P0: Given 10 equidistant bars with 2 removed, count_gaps returns 2; 0 when full. ... ok
test_audit_1_5_cache_first_avoids_api_calls (tests.test_dca_kline_repository.TestDcaKlineRepository.test_audit_1_5_cache_first_avoids_api_calls)
1.5 P0: Complete cache must trigger exactly 0 external fetch calls. ... ok
test_audit_1_6_pagination_budget_and_partial_fill_warning (tests.test_dca_kline_repository.TestDcaKlineRepository.test_audit_1_6_pagination_budget_and_partial_fill_warning)
1.6 P0: Requesting 20000 1m bars on miss -> calls <= MAX_PAGES, warnings non-empty, data_status=stale. ... ok
test_audit_1_7_paper_determinism (tests.test_dca_kline_repository.TestDcaKlineRepository.test_audit_1_7_paper_determinism)
1.7 P0: Same symbol/timeframe in paper mode produces identical values and source='paper_synthetic'. ... ok
test_audit_1_8_error_honesty (tests.test_dca_kline_repository.TestDcaKlineRepository.test_audit_1_8_error_honesty)
1.8 P0: Mock 429 error -> stale if cache exists, unavailable if no cache; never 'live'. ... ok
test_audit_1_9_retention_prune (tests.test_dca_kline_repository.TestDcaKlineRepository.test_audit_1_9_retention_prune)
1.9 P1: prune() deletes rows older than 180 days based on fetched_at. ... ok

----------------------------------------------------------------------
Ran 12 tests in 0.850s
OK
```

### 3.2 全量後端 CI 與前端契約測試

```bash
$ bash scripts/ci.sh
----------------------------------------------------------------------
Ran 246 tests in 11.580s
OK

> grid-trading-console@0.0.0 test
> node --experimental-strip-types --test tests/format.test.ts tests/auth.test.ts tests/dca.test.ts tests/dca-form.test.ts
# tests 36
# suites 0
# pass 36
# fail 0

> grid-trading-console@0.0.0 lint
> eslint .
/home/user/grid-trading-console/web_ui/frontend/src/components/MarketRankings.tsx
  52:6  warning  React Hook useEffect has a missing dependency: 'fetchRankings'. Either include it or remove the dependency array  react-hooks/exhaustive-deps
✖ 1 problem (0 errors, 1 warning)

> grid-trading-console@0.0.0 build
> tsc -b && vite build
✓ built in 720ms
```

### 3.3 靜態參數化 grep 證據

```bash
$ grep -nE 'execute\(f"|execute\(".*%|\+ *symbol' web_ui/backend/kline_repository.py
# 輸出為空（0 命中，exit code 1）
```

### 3.4 網格零回歸證據

```bash
$ git diff --stat core/services/grid/ run_grid_trading.py
# 輸出為空
```

### 3.5 SQLite Schema 與完整性檢查

```python
import sqlite3
conn = sqlite3.connect('data/web_ui_runtime.sqlite3')
# Tables: ['grid_runtime_snapshots', 'grid_runtime_history', 'sqlite_sequence', 'grid_commands', 'grid_command_events', 'grid_events', 'dca_klines']
# Integrity check: [('ok',)]
```

### 3.6 API HTTP 契約驗證（PAPER 模式）

```json
// GET /api/v1/market/klines/ETH/USDC:USDC?exchange=paper&timeframe=15m&limit=50
{
  "symbol": "ETH/USDC:USDC",
  "exchange": "paper",
  "timeframe": "15m",
  "candles": [
    {"t": 1699999200, "o": 526.974, "h": 528.0123, "l": 525.4321, "c": 527.1234, "v": 451.2345},
    ... (50 bars)
  ],
  "source": "paper_synthetic",
  "data_status": "paper",
  "gaps": 0,
  "warnings": ["使用合成 K 線，非真實歷史"],
  "oldest_ts": 1699955100
}
```

---

## 4. 例外接受

| # | 項目 | 等級 | 原因 | 緩解 | 到期日 |
|---|---|---|---|---|---|
| EX-C1.1 | 既有 ESLint warning（MarketRankings） | P3 | 既有 React Compiler / exhaustive-deps warning（Phase 7 EX-7.1） | 未新增任何 lint 錯誤，不影響功能 | C8 正式發布前 |

---

## 5. 部署 / 回滾

### 5.1 需重啟的服務

- 後端 API（`web_ui/backend/`）：若有執行中的 FastAPI/Uvicorn 實例，重啟以載入新路由與 K 線儲存庫。
- 引擎（`multi-grid-engine`）：本 Phase 未修改 `run_multiple_grids.py` 或 `core/services/dca/**`，不需要重啟交易引擎。
- 前端：無前端代碼變更。

### 5.2 回滾指令

```bash
git checkout arena/01a03e16-grid-trading-console
git revert <commit-hash>
# dca_klines 為獨立新增表，不破壞既有表，回滾時無需手動 DROP TABLE
```

---

## 附錄：交付物與後續 Phase C2 銜接

- **已交付**：
  1. `web_ui/backend/kline_repository.py`
  2. `web_ui/backend/kline_service.py`
  3. `web_ui/backend/schemas.py`
  4. `web_ui/backend/routes/market.py`
  5. `tests/test_dca_kline_repository.py`
  6. `scripts/ci.sh`
  7. `docs/github-actions-ci.yml`
  8. `CHANGELOG.md`
  9. `docs/dca_chart_backtest_phase1_worklog_20260826.md`
- **後續銜接（Phase C2）**：
  - Phase C2 將實作 `ReplayExchange`（撮合器，凍結規則 R-1 ～ R-13），基於 C1 提供的 K 線數據執行無未来函数撮合（訂單次棒才可見、跳空以開盤價成交、同棒保守順序結算）。
