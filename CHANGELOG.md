# Changelog

本專案的重要變更記錄於此檔案，格式參考 [Keep a Changelog](https://keepachangelog.com/zh-TW/1.1.0/)。

## [Unreleased] — 2026-08-14

### Added

- **AI 智能網格推薦與即時回測引擎 (AI Grid Recommendation & Backtest Engine)**:
  - 新增後端推薦與回測模組 `web_ui/backend/grid_recommendation.py` 與 `GET /api/v1/market/recommendation/{symbol}` 端點。
  - 自動抓取歷史 OHLCV K 線數據，支援多回測週期（7D / 30D / 180D）。
  - 結合 ATR 動態波動率與虛擬網格歷史成交模擬，自動計算推薦上下界價格、網格數量、網格間距、回測完成網格套利輪迴數 (`completed_cycles`) 與預估網格年化收益率 (`estimated_apr`)。
  - 串接交易所市場規則（`price_precision`、`quantity_precision`、`min_notional`），自動校正推薦價格與下單量。
  - 前端建立策略精靈 (`GridForm.tsx`) 整合 AI 智能推薦卡片與週期切換器，支援一鍵套用至表單並即時預覽回測數據。
- **全市場熱門代幣波動度與年化排行榜 (Market Volatility & APR Leaderboard)**:
  - 新增後端 `GET /api/v1/market/rankings` 端點，提供主流交易對（Binance/Lighter/Hyperliquid 等）年化收益與波動度排行。
  - 實作 15 分鐘後端記憶體快取 (Cache-Aside Pattern)，防止高頻輪詢觸發交易所 API Rate Limit / IP Ban。
  - 前端新增 `MarketRankings.tsx` 頁面與組件，支援即時行情、24H 漲跌、評級標籤（S/A/B/C）與一鍵「建立策略」直通精靈。
- **控制面板清單表格視圖 (Table View)**:
  - 新增 `GridTable.tsx` 組件，Dashboard 支援「卡片視圖 (Cards)」與「清單表格視圖 (Table)」自由切換，利於大批量網格策略的集中快速監控。
- **進程與環境變數自動載入**:
  - `ecosystem.config.js`、`run_multiple_grids.py` 與 `run_grid_trading.py` 支援自動讀取 `.env` 並注入執行環境，確保 PM2 守護進程能正確讀取 API Key 與各項旗標設定。

### Fixed

- **網格啟動逾時與即時失敗回報**:
  - 修復熱插拔監控器 (`active_grids_monitor`) 遇到不存在配置或被安全規則阻擋時的靜默忽略問題；即時發送 `failed` lifecycle event 與錯誤原因，防止前端無止盡處於 `pending_engine` 逾時。
- **策略刪除控制與強制刪除**:
  - `DELETE /api/v1/grids/{id}` 新增 `force=true` 支援，允許在刪除策略時自動觸發停止命令並優雅取消所有未成交掛單。
- **SQLite 儲存庫連線與 Schema 初始化競爭修復**:
  - `RuntimeSnapshotRepository` 與 `GridCommandRepository` 強化 schema auto-ensure 機制與連線隔離，新增 `reset_instance()` 輔助單元測試隔離。
- **例外處理防禦性強化**:
  - 修正 Python 寬鬆裸 `except:` 為明確的 `except Exception:`，提升異常捕獲安全性與排錯透明度。

### Changed

- **暗黑交易終端 UI/UX 全面升級**:
  - 重構 `App.css` 與 `index.css`，採用暗黑極客交易終端風格，加入流暢轉場動畫、微互動、狀態 Glow 光效與手機版 Drawer 側欄。
  - `GridDetail.tsx`、`ConfirmDialog.tsx`、`LoginPage.tsx`、`TopBar.tsx` 元件視覺與操作流暢度大幅提升。
- **專案結構與配置精簡**:
  - 清理廢棄的 `.bak`、舊版備份檔與 `.DS_Store`。
  - 清理過時與無效的 `config/grid/` 模板，更新 `config/grid/README.md` 指南。

### Testing

- 新增 `tests/test_grid_recommendation.py`，完整涵蓋 AI 推薦演算法、虛擬網格回測指標與精度修約邊界測試。
- 總計 117 項 Python 單元測試與契約測試全部 100% 通過。
- 前端 TypeScript 編譯與 Vite production bundle 建置 100% 通過。

## [0.2.0] — 2026-08-13

### Changed

- 專案顯示名稱改為 `grid-trading-console`。新 private 庫請用本機 `gh auth login` 後執行 `scripts/publish_private_repo.sh`（Arena token 無法建立新 GitHub 庫）。
- 總覽 / list / summary 只顯示 `active_grids.json` 已註冊策略；archived 永不進清單。
- Dashboard 輪詢改 AbortController + 指數退避。
- `start_web_ui.sh` 標示非正式部署，並指向 systemd unit。
- `ecosystem.config.js` 移除與 multi-grid 重複的單幣進程，路徑改 `PROJECT_ROOT` / `__dirname`。
- README 改為真實專案說明；舊假下載頁移到 `docs/archive/`。
- PAPER 改走 `PaperExchangeAdapter` 模擬盤，可以 start；LIVE 仍需 `GRID_LIVE_TRADING_ENABLED`。
- Dashboard 總覽 KPI 不再把 PAPER 盈虧加進 LIVE。
- `auto_start` 預設改為 false；若啟動被接受，回傳 `pending_engine` 而非 `created_and_started`。
- DELETE 僅允許已停止且無 pending 命令的策略。
- `run_multiple_grids.py` 不再預設啟動五個內建實盤 YAML；未指定 CLI 時進入熱插拔待命。
- 熱插拔接受 `active_grids.json` 中任何存在的 YAML，不再受啟動時 `valid_paths` 限制。
- 引擎依交易所分 adapter 池，並把 runtime / lifecycle 直寫 SQLite。
- `/grids`、`/stats`、`/summary`、`/system/health` 與詳情頁改用同一套 lifecycle 讀模型；未知值維持 null。
- 瀏覽器端 API 改為同源相對 URL；Vite 開發環境以可設定 proxy 轉發 `/api` 與 `/ws`，不再讓遠端瀏覽器請求 localhost。
- Dashboard 改為顯示 `starting`、`stopping` 與 command failure/timeout，而非把 `active_grids.json` 的 desired state 直接視為策略已運行。
- Start / Stop 現在使用 idempotency key，並以 `accepted → pending_engine → running/stopped/failed/timed_out` 呈現實際命令進度。
- GridDetail 使用 lazy loading，避免圖表依賴增加 Dashboard 首屏 bundle。
- 正式部署建議從 `nohup` 流程遷移至 systemd service 與 `/healthz` 健康檢查。

### Added

- Token 登入頁；401 / 403 會退出到登入，與「尚未同步」分開。
- TopBar 顯示引擎 PID、熔斷、最後同步與 PAPER/LIVE 徽章。
- 策略詳情顯示 pending orders / fills；引擎未提供時顯示「引擎尚未提供」。
- 停止確認 Dialog：明確寫出會取消掛單、預設不平倉。
- 抽出 LoginPage / TopBar / KpiCard / GridCard / ConfirmDialog；手機側欄改 Drawer。
- 前端 formatter / 建立精靈驗證單測（`npm test`）與 Python 契約測試。
- CI 入口：`scripts/ci.sh`（unittest + `npm test` + `npm run lint` + `npm run build`）；GitHub Actions 範本在 `docs/github-actions-ci.yml`。
- `GET /api/v1/grids/{id}/orders` 與 `/fills`。
- 新增批次總覽 API：`GET /api/v1/dashboard/snapshot`，以單一快照回傳全域 KPI 與策略卡片資料，移除 Dashboard 對每張卡片的 N+1 stats 輪詢模式。
- 新增 `live`、`stale`、`unavailable` 與 `degraded` 資料品質標示；未知 runtime 數值維持 `null`，不再以零值偽裝真實帳戶資料。
- 新增 SQLite runtime snapshot 與 7 天歷史資料保存，支援 API 重啟後恢復最後快照，以及策略詳情頁 24H / 7D 淨盈虧曲線。
- 新增持久化 grid lifecycle command journal：command id、來源、原因、狀態、時間與 idempotency key。
- 新增 command API：
  - `GET /api/v1/commands/{command_id}`
  - `GET /api/v1/commands?grid_id=...`
  - `GET /api/v1/commands/events?grid_id=...`
- 引擎新增機器可讀的 `GRID_LIFECYCLE` running/stopped/failed 訊號；ProcessManager 會將訊號寫入 command 與 engine event timeline。
- 新增策略詳情工作台：網格/風控摘要、資產與收益、PNL 曲線、命令稽核時間線與引擎事件。
- 新增三步式建立策略流程：市場、網格、風控確認；支援 PAPER/LIVE、交易對規則讀取、格數/格距換算、名義金額提示與 LIVE typed confirmation。
- YAML 新增 `account_mode: paper | live`。
- 新增 LIVE feature flag：`GRID_LIVE_TRADING_ENABLED=true` 才允許建立 LIVE 策略。
- LIVE 建立前新增後端 market rule 驗證：交易對存在性、min notional、價格精度、數量精度；驗證證據寫入 audit params。
- 新增公開、無敏感資訊的 `GET /healthz` readiness probe。
- 新增 systemd service 模板：`scripts/deployment/crypto-webui.service`。
- 新增部署與 release 稽核清單：`docs/web_ui_deployment_and_release_checklist_zh-TW.md`。
- 新增 runtime repository、command repository、PAPER lifecycle acceptance 與 HTTP contract tests。

### Fixed

- SQLite 啟用 WAL 與 busy_timeout；冪等鍵改為 `(grid_id, action, key)`。
- `active_grids.json` 改為原子寫入。

### Security

- 舊版 `/api/scripts`、`/api/configs`、`/api/config`、`/api/process*` 與 log WebSocket 現在需要 API token；`config/exchanges` 不可經 API 讀寫。
- CORS 改由 `GRID_CORS_ORIGINS` 白名單控制；`*` 時關閉 credentials。
- 臨時 API token 只寫入 `.grid_api_token`（0600），不再出現在 log。
- LIVE 策略建立預設關閉，必須由部署端明確開啟 feature flag。
- LIVE 建立需經 server-side market rule 驗證；前端驗證僅作為使用體驗輔助。
- 建立策略 UI 對 LIVE 模式要求輸入 `CONFIRM LIVE`。
- 操作命令、引擎事件與 market validation evidence 可持久化稽核。

### Testing

- `tests/test_runtime_repository.py`：runtime snapshot/history persistence。
- `tests/test_command_repository.py`：command、idempotency、timeout 與 engine event persistence。
- `tests/test_paper_grid_lifecycle_e2e.py`：PAPER create/start/engine confirmation/restart recovery/stop/audit durable flow。
- `tests/test_lifecycle_and_safety.py`：PAPER 可 start、LIVE/PAPER 分帳、熱插拔、CORS、file_manager、引擎直寫 SQLite。
- `tests/test_paper_adapter.py`：模擬盤限價穿越成交、市價平倉 PnL、account_mode 讀取。
- `tests/test_web_ui_http_contract.py`：health probe、舊 API 認證、PAPER start 回 pending_engine。
