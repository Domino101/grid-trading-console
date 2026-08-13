# 網格交易總控台升級方向與實作計畫

> 建立日期：2026-08-13  
> 範圍：`web_ui/frontend`、`web_ui/backend` 與 Hermes grid 執行/狀態介面  
> 目標：以參考截圖的「多交易所網格總控台」操作模型，升級目前專案既有 Web UI；優先確保交易狀態、風控資訊與操作指令**正確、可追溯、不中斷**，再改善視覺與互動。

---

## 1. 參考演示萃取的產品模型

三張參考圖共有一個清楚且適合本專案的分層：

1. **全域狀態列**：交易所/策略的 LIVE、PAPER 狀態、代理與系統健康、現在時間。
2. **總覽 KPI**：總權益、總盈虧（已實現／未實現）、交易量／完成網格、運行中策略數。
3. **帳戶模式摘要**：清楚隔離 LIVE 與 PAPER，PAPER 金額不能與實盤收益混算。
4. **策略卡片矩陣**：每一策略/交易所一張卡；顯示狀態、PNL、餘額、權益、成交量、完成格數、最新價與市場，並附當前參數、趨勢判讀、告警與安全操作。
5. **策略詳情工作台**：左側可編輯設定與風控、中央收益/價格曲線、右側成交與訂單資料；可由總覽直接進入。
6. **AI 市況分析區**：顯示資料新鮮度、分析結果、建議區間/格距與風險提示；手動分析與排程間隔可控。

採用此模型，但不複製畫面中的交易所名稱與數字。所有數值必須來自已驗證 API/引擎狀態；資料缺失時顯示「尚未同步／資料過期」，不得以 `0` 偽裝成真實帳戶餘額或收益。

---

## 2. 目前程式碼盤點與主要缺口

### 已具備的基礎

- FastAPI 已掛載 `/api/v1/grids`，具有建立、列表、統計、summary、啟動、停止、刪除端點：`web_ui/backend/routes/grids.py`。
- `GridStateStore` 可提供單一網格的 PNL、訂單、持倉等記憶體快取：`web_ui/backend/grid_state_store.py`。
- React/Vite 前端已有 `Dashboard.tsx`、`GridForm.tsx`、`api.ts`，並已使用深色視覺基調與 `lucide-react`。
- Backend 啟動時可掛載已編譯的前端靜態檔案；執行設定已有 `ecosystem.config.js`。
- 現有測試已涵蓋部分 Hermes API／grid 服務，但 UI、契約與生命週期測試不足。

### 必須先修正的問題（P0）

| 問題 | 現況與風險 | 改善方向 |
|---|---|---|
| 前端 API 位址 | `api.ts` 在 Vite 開發時硬編碼 `localhost:8001`；在 Arena/遠端瀏覽器與代理環境會失效，也不符合相對 URL 要求。 | 改成同源相對 `/api`；Vite 以 proxy 轉發 `/api`、`/ws` 到可設定的後端目標。 |
| 輪詢 N+1 | `Dashboard` 每 5 秒先列出所有網格，再對每張卡逐一請求 stats；網格增加時請求量線性放大，且請求可能重疊。 | 使用單一批次 snapshot/summary API，或 WebSocket/SSE 推送；加入 AbortController、in-flight guard、指數退避與可見頁面節流。 |
| 交易真實性 | `create_grid(auto_start)` 現階段主要寫入 YAML 與 `active_grids.json`；必須驗證是否真的由引擎接手、掛單成功及可恢復。 | 建立 command → accepted → engine acknowledged → running/failed 的明確狀態機與 command id；UI 僅在引擎確認後標示 LIVE。 |
| 狀態持久性 | `GridStateStore` 是單程序 in-memory；UI/API 重啟便遺失即時資料，且跨程序讀取未被保證。 | 運行狀態採 SQLite/Redis 或引擎持久化事件流；保留最近 snapshot 與 `last_updated_at`，API 重啟可恢復。 |
| 資料語意 | 無資料時 stats 預設全為 `0`，會與「真實 0」混淆；summary 與 grid list 的來源也不同。 | 建立 `data_status: live | stale | unavailable`、`last_updated_at`、`source`；金額欄可為 null。總覽使用同一 snapshot 來源。 |
| 高風險操作 | Dashboard 只有全域 `loading`，一張卡操作時全部卡被鎖；使用 `alert/confirm`，未區分停止、撤單、平倉、刪除。 | 以每 grid action state、確認 Dialog、typed acknowledgement、idempotency key、審計紀錄及操作結果 toast 處理。 |
| 安全設定 | CORS 為 `*` 且允許 credentials；token 直接放 localStorage 的策略需重新檢討。 | 生產環境 origin allowlist、HttpOnly/Secure session cookie 或短期 token、RBAC、CSRF 防護、審計遮罩。 |
| UI 維護性 | App/Dashboard 多個 inline style、手刻 utility class；`App.css` 仍有 Vite 樣板巢狀 CSS，RWD/無障礙未完整。 | 改為 token 化設計系統與元件化 CSS；移除死碼；採 semantic HTML、focus、ARIA、RWD 與 reduced motion。 |

---

## 3. 目標架構

```text
React console
  ├─ OverviewRoute             全域健康 + KPI + 策略卡片
  ├─ GridDetailRoute           設定/圖表/訂單/事件/操作
  ├─ GridCreateRoute           分步設定、試算、風險確認
  └─ api client + query cache  同源 REST + Event stream
              │
FastAPI /api/v1
  ├─ Grid read model           GET /dashboard/snapshot（批次）
  ├─ Command service           start/stop/cancel/close/delete，命令狀態與冪等
  ├─ Event stream              SSE 或 WebSocket（狀態/PNL/訂單/告警）
  ├─ Market/AI analysis        可快取、限流、標註資料時間
  └─ Audit / auth / health
              │
Grid engine adapter/event publisher
  ├─ 各 exchange adapter 真實回報 order、fill、balance、health
  └─ persistent state store（SQLite 起步；多執行個體再換 Redis/PostgreSQL）
```

**資料原則**：引擎為成交、掛單、餘額與狀態的 source of truth；API 僅建立讀模型與命令邊界。UI 不解析 log 來計算 PNL，也不把設定檔是否存在視作策略正在運行。

---

## 4. API / 資料契約調整

### 4.1 新增批次快照（第一優先）

新增 `GET /api/v1/dashboard/snapshot`，一次回傳：

- `generated_at`、`data_status`、`system_health`（代理、引擎、circuit breaker、API throttle）
- `account_mode: live | paper | mixed`，以及**按 mode 分開**的 aggregates
- 每個 `GridDashboardItem`：`grid_id`、display name、exchange、symbol、mode、lifecycle status、data_status、PNL breakdown、equity/balance（允許 null）、volume、filled cycles、current price、range、grid count/interval、last trade、last update、alerts、capabilities。

此端點取代 Dashboard 的「list + 每卡 stats」輪詢。資料量仍大時使用 `fields`、pagination 或 summary/detail 分層。

### 4.2 指令與生命週期

- 將 `start/stop/delete` 改為回傳 `command_id`、`accepted_at`、`desired_state`，並提供 `GET /commands/{id}` 或事件回推最終結果。
- 明確定義：`draft` → `starting` → `running` → `stopping` → `stopped`；另有 `degraded`、`error`、`out_of_range`、`stale`。`paper` 是 mode 而非 status。
- 將「停止策略」拆為可理解的安全選項：`pause_keep_orders`、`cancel_orders_keep_position`、`cancel_orders_and_close_position`（後兩項須後端能力與高風險確認）。
- delete 僅允許 stopped 且無未處理命令；全操作帶 idempotency key、source、reason，寫入 audit log。

### 4.3 詳情與事件

- `GET /grids/{id}/dashboard`：單卡完整資料與歷史摘要。
- `GET /grids/{id}/equity-curve?range=24h`、`/orders`、`/fills`、`/events`：圖表與表格分頁，避免 snapshot 過大。
- `GET /market/analysis?symbol=&exchange=`：回傳分析版本、輸入資料時間、有效期、建議值與風險聲明；AI 結論不可直接自動下單。
- OpenAPI/Pydantic schema 作為唯一契約來源，前端型別以 codegen 或契約測試同步。

---

## 5. 前端資訊架構與畫面計畫

### A. 總覽（第一個交付畫面）

1. **Top bar**：品牌、各 connector 的 Live/Paper/Disconnected、小型系統健康徽章、最後同步時間、手動刷新。
2. **Tabs**：總覽、策略、建立策略、AI 分析、系統/審計；手機版收合為可存取的 Drawer。
3. **KPI row**：總權益、總淨 PNL（已實現／未實現／fees）、交易量/完成格數、運行策略；顯示資料模式與 freshness。
4. **Paper account band**：僅在存在 paper 策略時顯示，與實盤數據明確隔離。
5. **Grid cards**：沿用演示中的高資訊密度，但採可折疊 detail：
   - 標題：策略名、exchange、symbol、LIVE/PAPER、狀態、freshness。
   - 核心：net PNL、ROI、equity、current price、range、volume、cycles、last trade。
   - 策略摘要：格數、grid spacing、槓桿、方向/模式；未知值顯示 `—`。
   - 告警：stale、無掛單、risk limit、API/代理異常，以 severity 排序。
   - 操作：詳情、reconnect（僅 connector 支援時）、pause/stop；危險操作不能一鍵誤觸。
6. **AI 市況分析**：最下方，先做 read-only 結果 + 手動刷新；排程設定放至詳情設定，不與交易操作混雜。

### B. 策略詳情（第二交付畫面）

- Header：名稱、狀態、mode、最後更新與操作。
- 左欄：設定摘要、資金與風控限制、編輯草稿（running 策略不能直接覆蓋關鍵參數）。
- 中欄：equity/PNL 曲線，可切 24h/7d/30d；價格與網格區間圖。
- 右欄：pending orders、recent fills、事件/告警時間線。
- 表格採虛擬化/分頁，圖表資料採範圍查詢與 downsampling。

### C. 建立策略（第三交付畫面）

由現在的單頁簡表改為三步：

1. 市場：交易所、交易對、Spot/Perp、Live/Paper。
2. 網格：下限、上限、格數或間距（兩者互推）、每格名義金額、預估資金、精度與手續費。
3. 風控與確認：最大曝險、stop loss/take profit、槓桿（只在合約）、啟動模式、風險摘要，最後確認。

前端即時驗證只做體驗輔助；後端仍為唯一驗證者。必須先透過 market info 檢查 tick size、quantity precision、min notional，再允許提交。

---

## 6. 分期執行計畫

### Phase 0 — 基線、風險與契約（1–2 天）

- 補齊目前 API 的 OpenAPI snapshot、生命週期 transition 表、錯誤碼規格與資料字典。
- 對 start/stop 進行 sandbox/PAPER 端到端驗證：API → engine 接手 → order/heartbeat → UI 狀態。
- 建立固定 demo fixture，禁止前端以假資料混入 production endpoint。
- 修正同源 API/Vite proxy、CORS 生產設定、型別/formatter 基礎設施。

**驗收**：遠端 preview 不請求 localhost；API 失敗有一致錯誤；所有 PNL 可追溯到來源與更新時間。

### Phase 1 — 穩定資料平面（2–4 天）

- 實作 persistent grid runtime repository 與 engine event publisher；先選 SQLite（單機），抽象 repository 以便切 Redis/PostgreSQL。
- 實作 dashboard snapshot；將 summary/list/stats 的讀取對齊同一 read model。
- 狀態和命令模型、idempotency、atomic config/active-state 寫入、audit log。
- 在 API 重啟、engine 斷線、資料超過 SLA 時正確回覆 stale/unavailable，絕不回填成零。

**驗收**：20 個網格 dashboard 更新不產生 N+1；重啟 API 後仍可見最近 snapshot/狀態；重複點擊 start 不會產生雙重啟動。

### Phase 2 — 總覽 UI 重構（2–3 天）

- 建立 design tokens、layout、TopBar、KpiCard、GridCard、StatusBadge、AlertPanel、Toast/Dialog 等可重用元件。
- 導入 query cache（可選 TanStack Query）與 SSE/WebSocket；若 fallback polling，採單 endpoint、15–30 秒、tab hidden 降頻、jitter/backoff。
- 完成 LIVE/PAPER 分流、freshness、錯誤/載入/空狀態、逐卡操作 state、RWD 與無障礙。
- 移除 Vite 樣板樣式與散落 inline style，保留深色高對比視覺但不過度 glow。

**驗收**：1440px 四欄、平板兩欄、手機單欄不橫向溢出；鍵盤可到達所有操作；使用者可一眼辨識 stale 與 paper。

### Phase 3 — 詳情、建立策略、風控操作（3–5 天）

- 詳情圖表、訂單/成交/事件資料表、時間範圍查詢。
- 三步建立流程、market-info 預檢、資金試算、風險確認。
- 實作安全的 stop/cancel/close 工作流程，權限和理由記錄；先只開啟已被 engine 支援的能力。

**驗收**：用 PAPER 完成建立→啟動→看到 heartbeat/訂單→停止→審計可查；任何不支援/失敗狀況不可顯示為成功。

### Phase 4 — AI 分析、可觀測性與上線（2–4 天）

- 導入可快取的 market/AI analysis 區與分析排程；標明資料時間、模型版本、失效時間與風險提示。
- Prometheus/structured logs、前端錯誤追蹤、API latency/error rate、connector heartbeat、command success rate。
- systemd/PM2 部署驗證（restart、healthcheck、日誌輪替、build 產物版本），備份/回滾和 release checklist。

**驗收**：服務崩潰自動重啟；健康檢查失敗不顯示「正常運行」；部署可回滾且不遺失狀態。

---

## 7. 品質、性能與可靠性門檻

### 前端

- 初始總覽：目標 LCP < 2.5s（一般管理網路），無 API 時顯示 skeleton/離線狀態。
- 每次 snapshot 僅一個請求；Event stream 斷線採指數回連並有可見狀態。
- 避免每次更新重新建立所有卡片；使用穩定 key、memo 化及圖表/表格 lazy loading。
- 金額使用 Decimal/後端最小單位或字串傳輸，前端只負責格式化，避免 JS float 對帳誤差。

### 後端與交易安全

- PNL、費用、餘額更新具 `as_of`／source；讀模型可容忍 eventual consistency，但命令結果必須可查。
- 所有 mutation 必須記 audit event、correlation/command id、actor/source，敏感 token 不寫入 log。
- API 限流、timeouts、connector circuit breaker、外部 API retry（只限安全讀取或具 idempotency 的操作）。
- 實盤功能以 feature flag 開啟，先驗證 PAPER；部署時預設不自動啟用新策略。

### 測試門檻

- Python：schema / repository / transition / route unit tests，PAPER e2e workflow，重啟恢復與併發 start 測試。
- TypeScript：API client、formatters、card state、form validation unit tests；Playwright 走 overview/create/detail 的 paper e2e。
- CI：`npm run lint`、`npm run build`、pytest、契約測試、dependency/security scan；PR 不可跳過。

---

## 8. 建議的第一個可提交工作包

為降低實盤風險，第一個 code change 不直接追求整頁像素級重製，而是完成下列垂直切片：

1. `api.ts` 改為同源 URL，`vite.config.ts` 增加可設定 proxy。
2. FastAPI 增加 `GET /api/v1/dashboard/snapshot` 與對應 Pydantic schema；先由既有 `GridStateStore` 組裝，並明確輸出 `data_status`、`last_updated_at`，不再把 unknown 當 0。
3. 重寫 Dashboard 的資料取得層，移除每張卡 `getGridStats` 的 N+1 請求與全域 loading。
4. 新增 Overview TopBar、KPI、GridCard 的元件化畫面和 loading/error/stale states；其中 LIVE/PAPER/unknown 有明確顏色與文字。
5. 為 snapshot route、Dashboard empty/stale/action state 新增測試，完成前端 build/lint 與 Python tests。

此工作包完成後，才進入持久化狀態與詳情圖表，能在不改變交易策略邏輯的前提下，先讓介面更可靠且能承載演示所需的資訊架構。
