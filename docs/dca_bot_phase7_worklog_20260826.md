# DCA 機器人 Phase 7 稽核報告 — 詳情工作台、端到端驗收與交付文件

> 日期：2026-08-26｜分支：`arena/01a03d63-grid-trading-console`
> 計畫：`docs/dca_bot_implementation_plan_zh-TW.md` §Phase 7（稽核 7.1–7.21）
> 稽核標準：`docs/grid_trading_console_audit_standard_zh-TW.md` §5 五段式

---

## 1. 範圍與風險

**範圍**：補齊 DCA 最後的深度視圖（詳情工作台），跑完整 PAPER E2E，產出可放行的交付文件與最終稽核報告。觸及：

- 新元件：`web_ui/frontend/src/components/DcaDetail.tsx`（六分頁：階梯 / 訂單 / 成交 / 事件 / 指標 / 設定）、`DcaLadderTable.tsx`（階梯狀態表，三態＋略過）。
- 整合：`DcaDashboard.tsx` 選取機器人後由占位條改為切換完整詳情頁；`App.tsx` DCA 分頁維持既有路由（詳情由 Dashboard 內部切換，符合 Phase 5/6 隔離）。
- 資料層：`services/api.ts` 追加 `DcaInfoResponse`/`DcaStatsResponse`/`DcaLadderResponse`/`DcaOrdersResponse`/`DcaFillsResponse`/`DcaEventListResponse`/`DcaLevelView`/`DcaEvent` 型別與 `getDcaBot`/`getDcaStats`/`getDcaLadder`/`getDcaOrders`/`getDcaFills`/`listDcaEvents`。
- 純函式：`utils/dca.ts` 追加 `dcaSlotStatusMeta`/`DCA_SLOT_STATUS_META`、`dcaEventLabel`/`dcaEventTone`、`dcaOrderRoleLabel`/`dcaSideLabel`。
- 引擎：`core/services/dca/coordinator/dca_coordinator.py` 追加 `open_orders_snapshot()`/`fills_snapshot()`/`ladder_slots_snapshot()`、成交歷史 `_fills`（含手續費與累積均價）、安全單成交後 `tp_replaced` 事件。
- 監督者：`run_multiple_grids.py` runtime 心跳追加 `open_orders`/`fills`/`ladder_slots` 快照欄位（導出失敗不阻斷心跳）。
- 後端：`web_ui/backend/routes/dca.py` 補實 `orders`/`fills` 端點（讀 runtime 快照）、`ladder` 端點優先採引擎槽位三態。
- 文件：`docs/dca_bot_user_guide_zh-TW.md`、`docs/dca_bot_audit_report_zh-TW.md`、`config/dca/README.md`、`README.md`、`CHANGELOG.md`。
- 測試：`tests/test_dca_paper_e2e.py`、`tests/test_dca_http_contract.py`、`tests/test_dca_frontend_contract.py`、`web_ui/frontend/tests/dca.test.ts` 各追加 Phase 7 測試。
- CI 修復：`Dashboard.tsx`/`GridForm.tsx`/`MarketRankings.tsx` 前既有 4 項 lint error（Phase 6 例外 EX-6.1）修復，使 `scripts/ci.sh` 全綠。
- 預覽：`vite.config.ts` 加 `allowedHosts: true`（僅影響 dev server）。

**風險**：
- 詳情快照導出失敗可能影響心跳 → 以 `try/except` 隔離，快照建構失敗只 warning、不阻斷 runtime 寫入。
- 網格核心零回歸：`git diff --stat core/services/grid/ run_grid_trading.py` 為空（7.14）。
- 本階段 mode 僅 PAPER；LIVE 維持關閉（7.21）。

---

## 2. 檢核結果（摘要）

Phase 7 稽核 7.1–7.21 全數 PASS（P0 無未修）。逐項證據見 `docs/dca_bot_audit_report_zh-TW.md` §2。重點：

- **7.1 armed 零掛單**：E2E 斷言 `get_open_orders=[]` 且 `create_order` 計數 0；live curl `/orders` → `available:false, orders:[]`；詳情頁 armed 明示「監控中 · 交易所零掛單」。
- **7.3 TP 隨均價重掛**：安全單成交後 coordinator 發 `tp_replaced` 事件，E2E 斷言其存在。
- **7.13 CI 全綠**：`bash scripts/ci.sh` 退出碼 0（Python 226 + npm test 36 + lint 0 error + build OK）。
- **7.14 全域回歸**：網格核心檔案 `git diff` 零變更。
- **7.17 稽核報告**：`docs/dca_bot_audit_report_zh-TW.md` 五段式產出。
- **7.21 LIVE 門檻**：不開啟 LIVE，`GRID_LIVE_TRADING_ENABLED` 維持關閉。

---

## 3. 測試證據

```
# scripts/ci.sh 全套
Ran 226 tests ... OK
# 前端
# tests 36  # pass 36  # fail 0
lint: 0 error, 1 warning（既有 exhaustive-deps）
build: tsc -b 0 error + vite build OK
# scripts/ci.sh 退出碼：0
```

新增測試：
- Python：`DetailWorkbenchSnapshotTests`（快照結構 + `tp_replaced` + 累積均價）、HTTP `test_detail_endpoints_read_engine_runtime_snapshot` / `test_orders_endpoint_unavailable_without_runtime`、`DcaDetailSourceContractTests`（六分頁 / 端點接線 / 分頁隔離 / 零數學 / armed 語意）。
- 前端：Phase 7 純函式（slot 三態、事件文案/語氣、角色/方向文案）。

Live E2E（PAPER，backend :8001 + vite :5173 preview proxy）：create → armed（endpoints 正確）→ start/stop → delete（YAML 歸檔、`active_dca.json={}`）→ audit 四筆齊備。

---

## 4. 例外接受

| 編號 | 項目 | 等級 | 緩解 |
|---|---|---|---|
| EX-7.1 | `MarketRankings.tsx` exhaustive-deps warning | P2 | 既有 warning，非 error，不阻斷 CI；屬網格頁不在 DCA 範圍 |
| EX-7.2 | 事件 `command` 類型 detail 顯示原文 | P2 | 不影響可追溯性 |
| EX-7.3 | 指標分頁未含 armed 指標歷史曲線 | P2 | 需時序儲存，另立計畫 |

P0 全數 PASS，無未修 P0。

---

## 5. 部署 / 回滾

- Feature flag：不開啟 LIVE。
- 重啟：引擎變更 → `pm2 restart multi-grid-engine`；後端變更 → 重啟 `main.py`；前端變更 → `npm run build`。
- Migration：無（沿用既有 SQLite repository，零 schema 變更）。
- 回滾：`git revert` 本輪提交 → 前端 rebuild → 重啟後端與引擎；產物不進版控，回滾不影響資料。

詳見 `docs/dca_bot_audit_report_zh-TW.md` §5。
