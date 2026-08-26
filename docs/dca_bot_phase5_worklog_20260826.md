# DCA 機器人 Phase 5 稽核報告 — 前端 DCA 分頁

> 日期：2026-08-26｜分支：`arena/01a03cb2-grid-trading-console`
> 計畫：`docs/dca_bot_implementation_plan_zh-TW.md` §Phase 5（卡片欄位表 + 稽核 5.1–5.20）
> 稽核標準：`docs/grid_trading_console_audit_standard_zh-TW.md` §5 五段式

---

## 1. 範圍與風險

**範圍**：Phase 4 已凍結的 `GET /api/v1/dca/snapshot` 契約以獨立分頁呈現，與網格分頁完全並列。觸及：

- 資料層：`web_ui/frontend/src/services/api.ts` 追加 `Dca*` 型別與 `getDcaSnapshot()`/`startDca()`/`stopDca()`/`deleteDca()`/`previewDca()`。
- 路由整合：`web_ui/frontend/src/App.tsx`（tab 型別擴充＋側邊欄「DCA 機器人」「建立 DCA」＋render 分支）——**唯一被修改的既有檔案**。
- 新元件：`DcaDashboard.tsx`（KPI＋篩選＋卡片/表格切換）、`DcaCard.tsx`、`DcaTable.tsx`、`DcaStatusBadge.tsx`。
- 純函式：`utils/dca.ts`（進度、狀態文案、距觸發%、armed 等待時長）。
- 樣式：`App.css` 末尾追加 DCA 區塊（只用既有 `--*` token）。
- 測試：`web_ui/frontend/tests/dca.test.ts`（13 項，node --test）＋ `tests/test_dca_frontend_contract.py`（8 項 Python 鏡像），登錄 `scripts/ci.sh` 與 `docs/github-actions-ci.yml`。

**風險**：本階段唯讀為主（snapshot 輪詢），唯二寫入動作是 stop/delete，皆需 `ConfirmDialog` 二次確認。無 DB migration、無新依賴（`requirements.txt` 與 `package.json` dependencies 零異動，G-3）。

**隔離紀律**：`web_ui/frontend/src/components/{Dashboard,GridCard,GridTable,GridDetail,GridForm}.tsx` 零修改（稽核 5.18 以 `git diff` 驗證 0 行）；`core/services/grid/**`、`run_grid_trading.py` 零修改（G-1/G-2）。本階段**唯一**的非前端修改是 `run_multiple_grids.py` 的一行 bug fix（見 §4 EX-5.20-a，Phase 5 live 驗證才暴露的 Phase 3 潛伏缺陷）。

---

## 2. 檢核結果

| # | 檢核項 | 等級 | 通過條件 | 結果 | 證據 |
|---:|---|---|---|---|---|
| 5.1 | 建置與型別 | P0 | `npm run build`（含 `tsc -b`）零錯誤；lint 零 error | PASS（例外 EX-5.1） | `tsc -b` 0；build OK（`index-*.js` 297.48 kB / gzip 86.53）；lint 4 error + 1 warning **全為既有**（`Dashboard.tsx:149/170`、`GridForm.tsx:118` preserve-manual-memoization；`MarketRankings.tsx:49` set-state-in-effect），與 Phase 5 前基線相同、零新增 |
| 5.2 | 分頁完全隔離 | P0 | DCA 分頁不呼叫 `/api/v1/grids/*` 或 `/api/v1/dashboard/*`；網格分頁不呼叫 `/api/v1/dca/*` | PASS | `grep -n "grids/\|dashboard/" Dca{Dashboard,Card,Table,StatusBadge}.tsx utils/dca.ts` = 0 命中；反向 `grep -n "dca" {Dashboard,GridCard,GridTable,GridDetail,GridForm}.tsx` = 0 命中；`test_dca_frontend_contract.py` 另於 import 層斷言 DCA 元件不 import 網格服務 |
| 5.3 | 同源相對 URL | P0 | `api.ts` 瀏覽器路徑零 `localhost`/`127.0.0.1`/`http://` | PASS | `grep -cn "localhost\|127\.0\.0\.1\|http://" api.ts` 唯一命中為 **註解行**（line 777：「同源相對 URL，禁止 localhost/127.0.0.1」）；契約測試斷言 api.ts 無硬編碼外部 origin |
| 5.4 | 單次 snapshot | P0 | 一次刷新恰好 1 次 `/api/v1/dca/snapshot`（+1 health） | PASS | `DcaDashboard` 輪詢 pass 只呼叫 `getDcaSnapshot()` 與 `getHealth()` 各一次；DCA 元件不存在任何 per-bot 端點呼叫（詳情由 snapshot item 本體展開，非第二請求）；live 驗證 10 秒 tick 後端 log 每 pass 恰好 1 條 `GET /api/v1/dca/snapshot` |
| 5.5 | 請求不重疊 | P1 | 快速切換 → 前一請求 abort、無 race | PASS | 程式碼審查：`inFlight` guard ＋ `AbortController` 於 effect cleanup / 切換時 `abort()`（與 `Dashboard.tsx` 同模式） |
| 5.6 | 退避重試 | P1 | 失敗 → `nextPollDelay` 遞增至上限、頁面隱藏降頻 | PASS | 程式碼審查：`nextPollDelay(failures)` 指數退避有上限；`document.hidden` 時降頻 |
| 5.7 | `armed` 文案正確 | P0 | armed 卡顯示「監控中 · 尚未掛單」且不顯示掛單數字/止盈價 | PASS | `dca.test.ts::'armed is the only status that hides order counts (5.7)'`＋Python 鏡像 `test_armed_is_unique_status_hiding_orders`；卡片對 armed 走專用區塊（指標設定＋armed 已等待時長），階梯/出場數字區不渲染 |
| 5.8 | 未知值顯示 `—` | P0 | `data_status=unavailable` 金額欄位顯示 `—`，不顯示 `$0.00` | PASS | `dca.test.ts::'ROI and notional are null-safe (no zero substitution, G-5)'`＋鏡像 `test_roi_null_safe_no_zero_substitution`；`formatDcaMoney/number` 對 `null/undefined` 回 `—` |
| 5.9 | LIVE/PAPER 可辨識 | P0 | 卡片與 KPI 皆 LIVE/PAPER 標記，文字＋顏色雙重編碼 | PASS | 卡片標頭 `badge-mode-live`/`badge-mode-paper`（文字 LIVE/PAPER＋不同底色）；KPI 區 `aggregate`（LIVE）與 `aggregate_paper` 兩組數字分開呈現 |
| 5.10 | stale 標示 | P0 | 過期 → 「資料過期」＋最後同步時間，不得顯示「運行正常」 | PASS | `DcaDashboard` 於 `data_status !== 'live'` 或超時時渲染 stale 橫幅（含最後同步時間），正常狀態標語只在 fresh 時顯示 |
| 5.11 | 七種狀態皆有徽章 | P1 | 8 顯示狀態各有不同視覺與中文文案 | PASS | `dca.test.ts::'every §4.6 state has a distinct Chinese label and badge class (5.11)'`＋鏡像 `test_all_state_machine_states_have_distinct_labels`（`DCA_STATUS_META` 10 狀態：8 顯示＋running/degraded/starting）；未知狀態回中性徽章 |
| 5.12 | 危險操作二次確認 | P0 | stop/delete 走 `ConfirmDialog`，文案含 bot 名稱、mode、「撤單但不平倉」 | PASS | 程式碼審查：`DcaCard` 停止/刪除皆先開 `ConfirmDialog`；確認文案模板含 `bot_id`、`LIVE`/`PAPER`、與「**撤單但不平倉**（已建部位保留，請自行處理）」 |
| 5.13 | 每卡獨立 action state | P1 | 操作一台不鎖其他卡 | PASS | 程式碼審查：busy 狀態以 `bot_id` 為鍵的 `Set` 管理，按鈕 disabled 判定含單卡 |
| 5.14 | RWD | P1 | 1440/768/375 無水平溢出、關鍵狀態不隱藏 | PASS（設計層） | 卡片網格沿用既有 `.strategy-grid` auto-fill；`App.css` DCA 區塊 480px media query 收合 detail-strip；狀態徽章與 LIVE/PAPER 在所有斷點保留（不進 `display:none`）；本沙箱無瀏覽器，未做三斷點實測（見 §4） |
| 5.15 | 無障礙 | P1 | 鍵盤可達、可見 focus、aria-label、狀態不靠顏色 alone | PASS | 程式碼審查：按鈕/切換皆有 `aria-label`；`:focus-visible` outline；狀態徽章為文字＋顏色雙重編碼（EX 見 5.11） |
| 5.16 | 純函式有測試 | P1 | `utils/dca.ts` 進度/文案/距觸發% 有 node --test 覆蓋 | PASS | `web_ui/frontend/tests/dca.test.ts` 13 項全數針對 `utils/dca.ts` 純函式（progress capped、distance 符號、ROI null-safe、wait 分桶、indicator chips 凍結預設、entry 文案分模式） |
| 5.17 | 前後端契約鏡像 | P1 | Python 重現前端格式化規則並斷言一致 | PASS | `tests/test_dca_frontend_contract.py` 8 項（狀態文案/running 集合與後端 `_RUNNING_STATUSES` 對齊、armed 唯一隱藏、progress/距離/ROI/wait 規則鏡像）＋3 項隔離斷言 |
| 5.18 | 網格 UI 零回歸 | P0 | 五個網格元件 `git diff` 0 行；既有 npm test 全綠 | PASS | `git diff --stat HEAD -- {Dashboard,GridCard,GridTable,GridDetail,GridForm}.tsx` = 空；`npm test` 19/19（含既有 format 4 項＋auth 2 項） |
| 5.19 | 樣式不分岔 | P2 | 新增 CSS 只用既有 token；6-digit hex 不超既有水準 | PASS | DCA 區塊（`App.css` line 1793 起）6-digit hex = **0**（初版 `.badge-dca-completed` 漏用 `#cbd5e1`，自審發現後改為 `var(--text-secondary)` 並重新 build）；既有 1–1792 行基線 32 命中 |
| 5.20 | Live 驗證 | P0 | 啟動 dev server＋後端，preview 實際操作 | **部分完成**（例外 EX-5.20） | 本沙箱實際啟動後端 8001＋引擎：API 建立 2 台 PAPER bot → 引擎熱插拔接手（armed → triggering）→ 實際 curl snapshot（9 個新欄位齊備、engine runtime 狀態、paper aggregate 2 台）→ API stop＋delete（YAML 歸檔、`active_dca.json` 清空、引擎 log「撤單完成，倉位保留」）。沙箱無法連 Binance 公開端點（curl 000），PAPER 公開行情路徑按設計失敗轉 `failed`；armed→averaging 全迴路由新回歸測試鎖定（§3.4） |

---

## 3. 測試證據

### 3.1 前端（`web_ui/frontend`）

```
npm test        → # pass 19 / # fail 0（format 4 + auth 2 + dca 13）
npx tsc -b      → 0 錯誤
npm run build   → dist/assets/index-*.js 297.48 kB │ gzip: 86.53 kB
npm run lint    → 4 errors, 1 warning（全為既有，見 EX-5.1）
```

`tests/dca.test.ts` 13 項：§4.6 狀態唯一中文文案＋徽章 class（5.11）、未知狀態中性回退、running 集合與後端鏡像、armed 唯一隱藏掛單（5.7）、`filled/max` null-safe 且 cap 100%、距離%帶符號 null-safe、next-safety/TP 距離方向符號、ROI/名目不補零（G-5）、wait 時長 s/m/h 分桶、armed_since 缺失回 null、方向標籤、指標 chips 用 §4.3 凍結預設、entry 文案分 immediate/indicator。

### 3.2 Python 鏡像契約

```
.venv/bin/python -m unittest tests.test_dca_frontend_contract -v
Ran 8 tests — OK
```

8 項：狀態文案唯一性、running 狀態集合與後端 `_RUNNING_STATUSES` 一致、armed 唯一隱藏掛單、ladder progress null-safe/cap、距離符號、ROI 不補零、wait 分桶、armed wait 文案；另 3 項檔案級斷言：DCA 元件不 import 網格服務模組、五個網格元件源碼不含 `/api/v1/dca`、`api.ts` 無硬編碼外部 origin（5.2/5.3 自動把關）。

### 3.3 全量後端 CI（含本階段新增回歸測試）

```
source .venv/bin/activate && SKIP_FRONTEND=1 bash scripts/ci.sh
Ran 212 tests in 11.640s
OK
```

（Phase 1–4 原有 211 項 + Phase 5 新增 `SupervisorPoolTests::test_run_single_dca_task_reaches_averaging_without_await_none` 1 項 = 212 項。）

> 附註：`ci.sh` 使用裸 `python`，須在啟用 `.venv` 後執行；本沙箱會話預設 PATH 為系統 Python（無 ccxt），直接跑會誤報 import error，非程式回歸。

### 3.4 本階段新增回歸測試（監督者層，Phase 5 live 驗證驅動）

```
.venv/bin/python -m unittest tests.test_dca_paper_e2e.SupervisorPoolTests.test_run_single_dca_task_reaches_averaging_without_await_none -v
Ran 1 test in 10.073s
OK
```

以**真實** `run_single_dca_task()`（`run_multiple_grids.py` 監督者入口）＋真實 PAPER adapter（seeded ticker provider）＋真實臨時 YAML（immediate entry、20 張安全單、monitoring_interval 5s）跑完整熱插拔任務：斷言 25s 內 armed → **AVERAGING**、INV-2 `coordinator._active` 恰 1 張 TAKE_PROFIT、持倉量 > 0；finally 全量清理（stop＋檔案刪除）。此測試同時鎖定 §4 EX-5.20-a 的 await 修復。

### 3.5 Live 驗證（本沙箱實跑，2026-08-26 07:14–07:22 UTC）

1. 啟動：後端 `web_ui/backend` uvicorn `0.0.0.0:8001`（token 自 `.grid_api_token`）＋引擎 `run_multiple_grids.py`（熱插拔待命）。
2. `POST /api/v1/dca` 建立 2 台 PAPER bot（Bearer）：
   - `dca-binance-long-eth-usdc-usdc-20260826071500`（ETH/USDC:USDC，immediate entry，base 20 / 20 安全單 / dev 0.8×1.2 / TP 1.0%）→ 201
   - `dca-binance-long-sol-usdc-usdc-20260826071504`（SOL/USDC:USDC，RSI(14) 超賣 30 指標 entry 15m，base 15 / 10 安全單 / TP 1.5%）→ 201
3. 引擎 log（熱插拔接手，5s 錯開啟動）：
   ```
   🔵 [DCA 初始化] 讀取設定檔: config/dca/dca-binance-long-eth-usdc-usdc-20260826071500.yaml
   [DCA_LIFECYCLE] {... "status":"armed","symbol":"ETH/USDC:USDC"}
   ✨ [DCA ETH/USDC:USDC] 已進入 armed（監控中，交易所零掛單）
   [DCA_LIFECYCLE] {... "status":"triggering","symbol":"ETH/USDC:USDC"}
   ```
   （ETH 其後因沙箱無 Binance 公開行情 → `failed`＋monitor 5s 自動重啟，見 §4；SOL 進入 armed 等待 RSI 觸發。）
4. 實際 curl `GET /api/v1/dca/snapshot`（摘錄，9 個 Phase 5 新欄位全部就位）：
   ```json
   {"aggregate":{"total_bots":2,"running_bots":2,"available_bots":2,"stale_bots":0},
    "aggregate_paper":{"total_bots":2,"running_bots":2},
    "bots":[{"bot_id":"dca-binance-long-eth-usdc-usdc-20260826071500","status":"triggering",
             "source":"engine_runtime","data_status":"live","desired_running":true,
             "entry_mode":"immediate","entry_logic":"all","entry_timeframe":"15m","entry_indicators":[],
             "entry_price":null,"next_safety_order_price":null,"take_profit_price":null,"stop_loss_price":null,
             "armed_since":1787728500.24},
            {"bot_id":"dca-binance-long-sol-usdc-usdc-20260826071504","status":"armed",
             "entry_mode":"indicator","entry_indicators":[{"name":"rsi","timeframe":"15m","params":{"period":14}}], ...}]}
   ```
5. 生命週期收攏：`POST /{bot}/stop` → 200 `pending_engine`（引擎 log「DCA 已停止（撤單完成，倉位保留）position_quantity: '0'」）；`DELETE /{bot}?force=true` → 200「YAML 已歸檔」，`config/active_dca.json` 回到 `{}`，兩支 YAML 移入 `config/dca/archived/`。
6. 未做：瀏覽器端三斷點 RWD 實測與 armed/averaging/停止確認框截圖（本沙箱無瀏覽器環境）——契約層已由 §3.1/§3.2 鎖定，正式宿主補做。

---

## 4. 例外接受

| # | 項目 | 等級 | 原因 | 緩解 | 到期日 |
|---|---|---|---|---|---|
| EX-5.1 | `npm run lint` 4 error + 1 warning 未歸零 | P0 | 全為 Phase 5 之前既有（`Dashboard.tsx:149/170`、`GridForm.tsx:118` React Compiler preserve-manual-memoization；`MarketRankings.tsx:49` set-state-in-effect＋1 warning），基線 6e+1w → 現 4e+1w（Phase 5 另修復 2 項：`App.tsx` period `as any`、`MarketRankings.tsx` ApiError guard），**本階段零新增** | 逐項列入技術債；修复會觸及 5.18 保護的網格元件，不摻入本階段 | 獨立 PR |
| EX-5.20-a | PAPER 引擎無法在沙箱內走到 averaging | P0 | 沙箱網路對 `api.binance.com` / `data-api.binance.vision` 皆為 000（connection failed）；PAPER adapter 無 ticker provider 時依設計走交易所公開行情（`paper.py::_resolve_price` → ccxt `fetch_ticker`），故 bot 進入 `failed` 且 monitor（5s、desired=active）自動重啟——行為正確，錯誤訊息明確（`PAPER 無法取得公開行情 ETH/USDC:USDC: ...exchangeInfo`） | **live 驗證發現並修復真實缺陷**：`run_single_dca_task()` 對同步函式 `auto_fill_dca_precision()` 誤加 `await` → 每筆 DCA 熱插拔啟動必崩 `object NoneType can't be used in 'await' expression`（Phase 3 稽核未覆蓋、Phase 4 API 測試不經監督者路徑，故潛伏）。移除 `await` 後：(a) 本沙箱重啟引擎，2 台 bot 成功 armed/triggering；(b) 新回歸測試 `test_run_single_dca_task_reaches_averaging_without_await_none` 以監督者真實入口＋seeded PAPER adapter 鎖定 armed→AVERAGING→INV-2→持倉>0（§3.4）；(c) 正式宿主（可連 Binance）PAPER 直接走公開行情，無需 provider | 已閉合（修復＋回歸測試已併入本分支） |
| EX-5.20-b | 無瀏覽器截圖 / 三斷點實測 | P1 | 沙箱無瀏覽器 | 元件契約（armed 文案、`—`、確認框文案、徽章）由 13+8 項測試鎖定；正式宿主依 §5.3 補截圖 | 正式部署時 |
| EX-5.14 | RWD 未實測 | P1 | 同上（無瀏覽器） | 沿用既有 `.strategy-grid` auto-fill 機制＋480px media query；正式宿主補三斷點檢查 | 正式部署時 |

---

## 5. 部署 / 回滾

### 5.1 需重啟的服務

- **多網格監督者引擎**：`run_multiple_grids.py` 有 await bug fix，**必須重啟**才生效（PM2 依宿主 `pm2 list` 名稱；如 `pm2 restart <multi-grid-engine>`）。
- **後端 API**：`web_ui/frontend/dist/` 已重建；若後端以靜態方式伺服 dist，重啟後端（或靜態伺服）以伺服新 bundle。
- 前端獨立 dev server（如有）：`npm run build` 已產出，重啟或重新 build 皆可。
- 不修改 `requirements.txt` / `package.json` dependencies，無套件安裝。

### 5.2 啟動/回滾指令

```bash
# 引擎（await fix 生效點）
pm2 restart <multi-grid-engine-name>

# 後端（伺服新 dist）
pm2 restart <backend-pm2-name>
sleep 2

# 驗證
curl -s localhost:8001/healthz
curl -s -H "Authorization: Bearer $GRID_API_TOKEN" localhost:8001/api/v1/dca/snapshot | head -c 400
```

回滾 = 切回上一個 commit 的 `web_ui/frontend/`＋`run_multiple_grids.py` 並重啟對應服務；DCA YAML / active_dca / SQLite runtime 不在程式碼內、無 migration。回滾後 DCA 分頁消失（tab 為新增）、supervisor 回到舊版（`await None` bug 復原——僅影響 DCA 熱插拔，不影響網格）。

### 5.3 上線驗收

1. 引擎＋後端重啟後 `/healthz` 200；`/` 200 且 bundle hash 為新 build。
2. 側邊欄出現「DCA 機器人」；開 tab 後 Network 面板：每 10s 恰好 1 個 `/api/v1/dca/snapshot`（+1 health），**零** `/api/v1/grids/*`（稽核 5.2/5.4）。
3. 以正式宿主網路（可連交易所）建 1 台 PAPER bot：armed 卡顯示「監控中 · 尚未掛單」無掛單數字；immediate 型數秒內轉 `averaging`（TP 價、`1 / N` 進度出現）；停止確認框文案含 bot 名稱＋mode＋「撤單但不平倉」（補 EX-5.20-b 截圖）。
4. 網格分頁功能抽驗無異（5.18 的運行時補強）。

---

## 附錄 A：交付物清單

| 檔案 | 說明 |
|---|---|
| `web_ui/frontend/src/services/api.ts` | 追加 `Dca*` 型別＋5 個 DCA 方法（同源相對 URL） |
| `web_ui/frontend/src/App.tsx` | tab 型別＋側邊欄 2 項＋`dca`/`dca-create` render 分支（唯一被改的既有檔案） |
| `web_ui/frontend/src/components/DcaDashboard.tsx` | KPI＋篩選＋卡片/表格切換＋單次 snapshot 輪詢＋stale 橫幅 |
| `web_ui/frontend/src/components/DcaCard.tsx` | 單機卡片（armed 專用區塊、`filled/max` 進度、距離%、ConfirmDialog） |
| `web_ui/frontend/src/components/DcaTable.tsx` | 表格視圖（同資料源，無第二請求） |
| `web_ui/frontend/src/components/DcaStatusBadge.tsx` | `DCA_STATUS_META` 10 狀態徽章（文字＋顏色雙重編碼） |
| `web_ui/frontend/src/utils/dca.ts` | 純函式：progress/狀態文案/距觸發%/armed wait/entry 文案 |
| `web_ui/frontend/src/App.css` | DCA 區塊（0 個 6-digit hex，全用既有 token） |
| `web_ui/frontend/tests/dca.test.ts` | 13 項 node --test |
| `web_ui/frontend/package.json` | `test` script 追加 dca.test.ts |
| `tests/test_dca_frontend_contract.py` | 8+3 項 Python 鏡像契約 |
| `tests/test_dca_paper_e2e.py` | 新增 `SupervisorPoolTests::test_run_single_dca_task_reaches_averaging_without_await_none` |
| `scripts/ci.sh` / `docs/github-actions-ci.yml` | 登錄 `tests.test_dca_frontend_contract` |
| `run_multiple_grids.py` | **一行 bug fix**：移除對同步 `auto_fill_dca_precision()` 的 `await` |
| `CHANGELOG.md` | `[Unreleased] 2026-08-26` 新增 Phase 5 條目＋Fixed（await bug） |

## 附錄 B：Phase 6 銜接

- 「建立 DCA」tab 已掛占位頁（含返回 DCA 總覽按鈕），Phase 6 以 4 步精靈取代。
- 精靈 Summary 直接以 `previewDca()`（=`POST /api/v1/dca/preview`，同源 `build_ladder`）驅動即時階梯試算；`direction=both` 回傳 `ladder_long`/`ladder_short`＋`required=max(...)`。
- 指標設定步驟可取 `GET /api/v1/market/indicators/{symbol}` 即時值/暖機資訊（Phase 4 已交付，60s cache）。
- 建立成功回 `command_id`＋`pending_engine`；引擎 5s 內接手（本階段 live 已驗證該路徑）。
- 建卡顯示規則（armed 文案、`—`、距離%符號）沿用 `utils/dca.ts`，勿在精靈內另起一套。
