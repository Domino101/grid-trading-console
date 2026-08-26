# DCA 機器人最終稽核報告（Phase 1–7）

> 日期：2026-08-26｜分支：`arena/01a03d63-grid-trading-console`
> 計畫：`docs/dca_bot_implementation_plan_zh-TW.md`
> 稽核標準：`docs/grid_trading_console_audit_standard_zh-TW.md` §5（五段式）
> 交付範圍：Phase 7（詳情工作台 + 端到端驗收 + 交付文件），並回顧 Phase 1–6 稽核結論

---

## 1. 範圍與風險

**本輪範圍（Phase 7）**：
- 前端：`DcaDetail.tsx`（六分頁工作台）、`DcaLadderTable.tsx`（階梯狀態表）；`DcaDashboard.tsx` 由占位條改為切換完整詳情頁；`utils/dca.ts` 追加詳情純函式；`services/api.ts` 追加 6 個詳情端點型別與方法。
- 後端：`routes/dca.py` 補實 `orders`/`fills` 端點（讀引擎 runtime 快照）、`ladder` 端點優先採用引擎槽位三態。
- 引擎：`coordinator/dca_coordinator.py` 追加 `open_orders_snapshot()`/`fills_snapshot()`/`ladder_slots_snapshot()` 與 `tp_replaced` 事件、成交歷史；`run_multiple_grids.py` 心跳追加詳情快照欄位。
- 文件：使用者手冊、最終稽核報告、`config/dca/README.md`、`README.md`、`CHANGELOG.md`、Phase 7 工作日誌。
- 測試與 CI：新增前後端 Phase 7 測試；修復 Phase 6 遺留的 4 項前端 lint error 使 `scripts/ci.sh` 全綠。

**涉及 mode / 交易所 / mutation / 資料來源**：
- Mode：**僅 PAPER**；LIVE 維持關閉（`GRID_LIVE_TRADING_ENABLED` 未開）。
- 交易所：沿用既有抽象層與 PAPER 合成撮合；未新增交易所程式碼。
- Mutation：詳情頁為唯讀讀模型；既有 start/stop/delete 未變更契約。
- 資料來源：詳情數值統一來自 `RuntimeSnapshotRepository`（引擎心跳）與純函式試算，前端零階梯數學。

**風險與緩解**：
- DCA 與網格共用監督者進程 → `run_single_dca_task()` 頂層例外隔離（Phase 3 P0，回歸未動）。
- 詳情快照導出失敗 → 以 `try/except` 包住且不阻斷 runtime 心跳（本輪新增）。
- 網格核心零回歸：`git diff --stat core/services/grid/ run_grid_trading.py` 為空（見 §3）。

---

## 2. 檢核結果（Phase 7 稽核表 7.1–7.21）

| # | 檢核項 | 等級 | 結果 | 證據 |
|---|---|---|---|---|
| 7.1 | E2E：armed 零掛單 | P0 | PASS | `test_full_round_inv1_to_inv4` 斷言 armed 時 `get_open_orders=[]` 且 `create_order` 計數 0；live curl `GET /orders` 回 `available:false, orders:[]`；詳情頁 armed 顯示「監控中 · 交易所零掛單」（`test_detail_armed_shows_zero_orders_semantics`） |
| 7.2 | 觸發後掛單正確 | P0 | PASS | E2E：基礎單 1 + TP 恰 1 筆，安全單 ≤ 窗口；`open_orders_snapshot` 斷言 TP 恰 1（`test_snapshots_and_tp_replaced_event`） |
| 7.3 | TP 隨均價重掛 | P0 | PASS | 安全單成交後發 `tp_replaced` 事件（coordinator `_attempt_tp_replace`）；E2E 斷言 `"tp_replaced" in coord.events`；事件時間線 UI 呈現 |
| 7.4 | 止盈完成 | P0 | PASS | E2E：TP 成交 → `COMPLETED`、殘單全撤、持倉 0、`realized` 入帳 |
| 7.5 | 停止與刪除 | P0 | PASS | live curl：stop 後 already_stopped；delete 後 YAML 移至 `config/dca/archived/`、`active_dca.json` 為 `{}` |
| 7.6 | 審計可追溯 | P0 | PASS | live curl `GET /system/audit` 含 `create_dca`/`start`/`stop`/`delete`；`test_audit_logs_create_start_stop_and_no_secrets` |
| 7.7 | 詳情資料一致 | P0 | PASS | `DcaDetail` KPI 取 `stats` + snapshot 同一讀模型；均價/持倉/盈虧與卡片一致（同源） |
| 7.8 | 階梯表狀態正確 | P1 | PASS | `DcaLadderTable` 三態（已成交/掛單中/未觸及）＋略過；標當前價列與均價線；`ladder_slots_snapshot` 三態 E2E 斷言 |
| 7.9 | 長列表處理 | P1 | PASS | 成交分頁 `PAGE_SIZE=50`，`pagedFills` 切片 |
| 7.10 | 錯誤路徑覆蓋 | P0 | PASS | 詳情頁 `notice-error` 顯示載入錯誤；端點無 runtime 時 `available:false` 附說明，不顯示為正常（`test_orders_endpoint_unavailable_without_runtime`） |
| 7.11 | 重啟恢復 | P0 | PASS | runtime 快照持久化於 SQLite（`RuntimeSnapshotRepository`）；重啟後端 snapshot 仍可讀；`averaging` bot 由交易所掛單對帳恢復（Phase 2 `_reconcile`，回歸未動） |
| 7.12 | 並發安全 | P1 | PASS | start/stop 走既有冪等鍵與命令序列化（Phase 4，回歸未動） |
| 7.13 | 完整 CI 綠燈 | P0 | PASS | `bash scripts/ci.sh` 退出碼 0（Python 226 + npm test 36 + lint 0 error + build OK） |
| 7.14 | 全域回歸 | P0 | PASS | `git diff --stat core/services/grid/ run_grid_trading.py` 為空；全部既有測試綠 |
| 7.15 | 文件完整 | P1 | PASS | `dca_bot_user_guide_zh-TW.md` 涵蓋四指標/階梯數學/armed 語意/風險；`config/dca/README.md` 欄位與端點更新 |
| 7.16 | CHANGELOG | P1 | PASS | `[Unreleased]` Phase 7 條目含 Added（元件/端點/純函式/文件/測試/CI 修復） |
| 7.17 | 稽核報告 | P0 | PASS | 本文件（五段式，逐項列 Phase 1–7） |
| 7.18 | 無憑證外洩 | P0 | PASS | `git diff` grep `API_KEY\|SECRET\|TOKEN\|PRIVATE` 僅命中 `.gitignore` 條目與 CSS 設計 token 註解；`config/dca/*.yaml` 無憑證 |
| 7.19 | 產物不進版控 | P1 | PASS | `.gitignore` 新增 `config/{grid,dca}/archived/`；`data/`/`logs/`/`active_dca.json`/`node_modules`/`dist` 皆已排除 |
| 7.20 | 重啟指令明示 | P1 | PASS | 見 §5 部署/回滾 |
| 7.21 | LIVE 放行門檻 | P0 | PASS | 本交付**不開啟 LIVE**；`GRID_LIVE_TRADING_ENABLED` 維持關閉；見 §5 |

### Phase 1–6 稽核結論回顧（前置 Phase 皆已通過並凍結）

| Phase | 主題 | 結論 | 工作日誌 |
|---|---|---|---|
| 1 | 領域模型與指標引擎（純函式核心） | 通過（鎖定向量 A/B、指標向量、收斂性逐位對上） | `dca_bot_phase1_worklog_20260825.md` |
| 2 | 執行引擎與狀態機（armed 不掛單不變式 INV-1..6） | 通過 | `dca_bot_phase2_worklog_20260826.md` |
| 3 | 設定持久化、OHLCV 快取、監督者整合（熱插拔、task 隔離） | 通過 | `dca_bot_phase3_worklog_20260826.md` |
| 4 | 後端 API（CRUD/生命週期/snapshot/preview/指標端點） | 通過 | `dca_bot_phase4_worklog_20260826.md` |
| 5 | 前端 DCA 分頁（總覽/卡片/表格） | 通過 | `dca_bot_phase5_worklog_20260826.md` |
| 6 | 建立 DCA 精靈（4 步 + 即時階梯試算） | 通過（例外 EX-6.1：前端 lint 4 error，Phase 7 已修復） | `dca_bot_phase6_worklog_20260826.md` |
| 7 | 詳情工作台、E2E、交付文件 | 通過（本報告） | `dca_bot_phase7_worklog_20260826.md` |

---

## 3. 測試證據

**指令與版本**：
- Python 3.11；`bash scripts/ci.sh`（unittest 全套）
- Node/Vite：`npm test`（node --test）、`npm run lint`（eslint 10）、`npm run build`（tsc -b + vite）

**結果**：
```
# Python（scripts/ci.sh 全套）
Ran 226 tests ... OK

# DCA 專屬 12 檔
Ran 194 tests ... OK

# 前端 node --test
# tests 36  # pass 36  # fail 0

# 前端 lint
✖ 1 problem (0 errors, 1 warning)   # 僅 1 個既有 exhaustive-deps warning，不阻斷 CI

# 前端 build
✓ built（tsc -b 0 error + vite build 成功）

# scripts/ci.sh 退出碼：0
```

**Phase 7 新增測試**：
- `tests/test_dca_paper_e2e.py::DetailWorkbenchSnapshotTests::test_snapshots_and_tp_replaced_event`：open_orders/fills/ladder 快照結構 + TP 恰 1 + `tp_replaced` 事件 + 累積均價回填。
- `tests/test_dca_http_contract.py::test_detail_endpoints_read_engine_runtime_snapshot` / `test_orders_endpoint_unavailable_without_runtime`：orders/fills/ladder 端點讀 runtime 快照與無 runtime 的 `available:false`。
- `tests/test_dca_frontend_contract.py::DcaDetailSourceContractTests`：六分頁齊備、端點接線、分頁隔離、階梯表零數學、armed 零掛單語意（檔案級契約）。
- `web_ui/frontend/tests/dca.test.ts`：Phase 7 純函式（slot 三態、事件文案/語氣、角色/方向文案）。

**Live E2E（PAPER，correlation：backend :8001 + vite :5173 preview proxy）**：
- create → armed（`/orders` 空、`/fills` 空、`/ladder` unavailable、`/stats` stopped）→ start（無引擎進程時明確回「找不到引擎進程鎖」）→ stop → delete（YAML 歸檔、`active_dca.json={}`）→ audit 四筆齊備。

---

## 4. 例外接受（P1/P2 未修項目）

| 編號 | 項目 | 等級 | 說明 / 緩解 | Owner / 到期 |
|---|---|---|---|---|
| EX-7.1 | `MarketRankings.tsx` `exhaustive-deps` warning | P2 | 既有 warning（非 error，不阻斷 CI）；`fetchRankings` 未列入 deps 為刻意（避免每次重建）。屬網格頁，不在 DCA 範圍。 | 前端 / 下次網格頁整理 |
| EX-7.2 | 詳情「事件」時間線的 `command` 事件 detail 為引擎原文 | P2 | 目前顯示原始 detail 字串；未做逐類型友善化。不影響可追溯性。 | 前端 / 視需要 |
| EX-7.3 | 指標分頁不含「armed 期間指標歷史曲線」 | P2 | 計畫原列「指標歷史與觸發點標記」；本版顯示指標設定與參數摘要 + 即時值於建立精靈。歷史曲線需額外時序儲存，另立計畫（與 §8 明確排除的回測同源）。 | 產品 / 另立計畫 |

> P0 項目全數 PASS，無未修 P0。

---

## 5. 部署 / 回滾

**Feature flag**：本交付**不開啟 LIVE**。`GRID_LIVE_TRADING_ENABLED` 維持關閉（預設）。LIVE 放行需另行完成稽核標準 §2 的最低條件（憑證隔離、限速、熔斷跨進程一致性等），不在本輪範圍。

**重啟指令（哪些檔案觸發哪個重啟）**：
- 引擎（`run_multiple_grids.py`、`core/services/dca/**`）變更 → `pm2 restart multi-grid-engine`（本地：重跑 `python run_multiple_grids.py`）。
- 後端 API（`web_ui/backend/**`）變更 → 重啟後端（`cd web_ui/backend && python main.py`，或 systemd `crypto-webui.service`）。
- 前端（`web_ui/frontend/src/**`、`vite.config.ts`）變更 → `cd web_ui/frontend && npm run build`（開發預覽 `npm run dev`）。

**Migration**：無 schema 變更。DCA 沿用既有 `RuntimeSnapshotRepository` / `GridCommandRepository`（以 `config_path` / `bot_id` 為鍵，零 schema 變更）。

**Health checks**：
- `GET /api/v1/dca/snapshot`（大盤）、`GET /api/v1/dca/{id}/stats`（單台）。
- 引擎心跳：runtime `last_update` 新鮮度（`data_status` = live/stale/unavailable）。

**回滾步驟**：
1. `git revert` 本輪提交（或還原到 Phase 6 commit `ee0ab62`）。
2. 前端 `npm run build`、重啟後端與引擎。
3. DCA 分頁與詳情工作台會回到 Phase 6 的占位詳情條；核心引擎與資料不受影響（無 migration 需回滾）。
4. 產物（`config/{grid,dca}/archived/`、`config/active_dca.json`、`data/`）本就不進版控，回滾不影響。

---

## 附錄：關鍵不變式最終確認（P0）

- **INV-1**（armed/cooling/stopped 掛單 0）：E2E + live curl 雙證。
- **INV-2**（averaging TP 恰 1）：E2E `open_orders_snapshot` + coordinator 計數。
- **INV-3**（安全單 ≤ N，同時掛單 ≤ 窗口）：E2E 斷言。
- **INV-4**（Σ 名目 ≤ required）：E2E 於每次成交後斷言。
- **INV-5**（狀態轉移合法，否則轉 failed）：`dca_state.transition_to` 拋 `InvalidDcaTransition`（Phase 2，回歸未動）。
- **INV-6**（單方向互斥）：`DirectionLock`（Phase 2，回歸未動）。
- **網格零回歸**：`git diff --stat core/services/grid/ run_grid_trading.py` 為空。
