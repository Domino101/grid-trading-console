# Changelog

本專案的重要變更記錄於此檔案，遵循 [Semantic Versioning](https://semver.org/lang/zh-TW/) 與 [Keep a Changelog](https://keepachangelog.com/zh-TW/1.1.0/) 規範。
詳細歷史工程記錄請參閱 [CHANGELOG_LEGACY.md](file:///root/grid-trading-console/CHANGELOG_LEGACY.md)。

## [0.3.4] — 2026-09-16

### Added
- **CoinFull Prime 統一記帳層整合 (Canonical Ledger Integration)**：
  - 將 CoinFull Prime 自動調倉策略全面收斂至專案標準記帳層（`data/web_ui_runtime.sqlite3` 之 `bots`、`trade_fills`、`bot_events`），消除與網格及 DCA 之間的記帳孤島。
  - `service.py` 於啟動初始化時向 `bots` 表登記 `coinfull_prime:paper`、`coinfull_prime:binance_testnet`、`coinfull_prime:binance_live` 身分，嚴格符合 P1-6 帳本外鍵約束。
  - `rebalancer.py` 在實盤下單與 Dry-Run 模擬換倉時，逐筆寫入標準 `trade_fills` 成交流水（保留幣種、方向、價格、數量、名義價值與角色標記）；調倉完成後將包含完整 16 幣詳細訂單陣列持久化至 `bot_events`（`daily_rebalance`），防範伺服器重啟丟失。
- **後端成交流水與調倉歷史查詢端點 (CoinFull Prime History & Fills API)**：
  - 新增 `GET /api/v1/coinfull-prime/fills`：支援依帳號環境篩選（paper / testnet / live）、時間倒序分頁、以及 Dry-Run 模擬成交流水切換。
  - 新增 `GET /api/v1/coinfull-prime/history`：回傳歷次調倉批次歷史與詳細訂單執行明細（包含幣種、方向、成交量、成交價、名義金額與狀態）。
- **前端調倉歷史與成交紀錄雙分頁控制台 (Frontend History & Fills UI)**：
  - `CoinFullPrimePage.tsx` 新增「調倉歷史與成交紀錄」控制台面板，提供「調倉批次歷史（可展開 16 幣訂單）」與「逐筆成交流水」雙分頁檢視。
  - 支援帳號環境過濾、含 Dry-Run 模擬開關、即時手動刷新與換倉完成後自動刷新機制。
- **CoinFull Prime 策略實證稽核與優化研究文件包 (Empirical Audit & Optimization Package)**：
  - 整合 19 檔主流永續合約 2021～2026 年完整日線實證回測研究與稽核主報告（`docs/CoinFull_Prime_Audit_Report_2026-09-16.md`）。
  - 納入獲利優化計畫、風險情境手冊、規避下單觸發規格與倉位增減時機等 5 份全套指引文件（`docs/CoinFull_Prime_00～04`）。
  - 匯入 `research/coinfull_prime_audit/` 完整可重現回測引擎與 exp1～exp14 統計檢定實驗輸出。

---

## [0.3.3] — 2026-09-16

### Added
- **DCA 動態冷卻與循環重啟控制 (DCA Dynamic Cooldown & Auto-Repeat Controls)**：
  - 後端新增 `PATCH /api/v1/dca/{bot_id}/config` 路由，支援機器人運行中動態熱更新 `cooldown_seconds`（冷卻時間）與 `repeat_after_take_profit`（止盈後自動循環開單開關），無需重啟進程。
  - `POST /api/v1/dca/{bot_id}/start` 支援傳遞 `cooldown_seconds` 參數覆蓋配置。
  - 前端 DCA 監控工作台 (`DcaDetail.tsx`) 與建立表單 (`DcaForm.tsx`) 補齊冷卻倒數計時器即時視覺反饋，並支援即時開關與滑桿調節。
- **多空截面即時排行觀測介面 (Cross-Sectional Market Observer)**：
  - 新增前端獨立分頁 `#cross-section`（`CrossSectionRankings.tsx` 與 `router.ts` 路由註冊），提供純盤面 79 幣截面多空排行榜、動能、主動買賣流、資金費率、大戶多空比等指標全屏沉浸式即時觀測，與實盤下單完全解耦。
- **多交易所適配擴充與合約格式引導 (Multi-Exchange Expansion & Dynamic Placeholders)**：
  - 網格創建表單 (`GridForm.tsx`) 與市場行情排行 (`MarketRankings.tsx`) 新增 OKX (歐易) 與 Backpack 交易所選項。
  - 依交易所動態切換專屬合約格式預填範例（OKX `BTC-USDT-SWAP`、Hyperliquid `SOL/USDC:USDC`、Lighter `BTC-USD`、Backpack `SOL_USDC_PERP` 等）。
  - `core/adapters/exchanges/factory.py`、`okx.py`、`binance_rest.py` 強化錯誤重試、合約市場資訊提取與連線容錯。
- **單元測試與安全防護套件擴充**：
  - 新增 `tests/test_exchange_audit_and_switch.py`，擴充 `tests/test_exchange_reconciler.py` 與 `tests/test_dca_http_contract.py`，完整涵蓋方向衝突過濾與冷卻接口測試。

### Fixed
- **DCA 對帳方向衝突與外部持倉誤領防護 (Reconciler Direction Filter & External Isolation)**：
  - 修復 `ExchangeReconciler`：單向機器人（`long` / `short`）僅匹配同向交易所持倉，遇反向持倉時主動略過認領並日誌警告，杜絕單向機器人誤接管反向部位。
  - 新建立、無歷史快照且無專屬掛單之 DCA 機器人，略過自動認領交易所外部持倉，防止新啟動機器人誤轉入 `AVERAGING` 狀態。
- **CoinFull Prime 自動換倉穩定度與持久化優化**：
  - 完善定時排程器狀態持久化 (`data/coinfull_prime_scheduler.json`) 與專屬持倉籃子記帳 (`data/coinfull_prime_active_basket.json`)。
  - 優化 Binance Live 實盤下單合約步長與精度動態裁切。

---

## [0.3.2] — 2026-09-08

### Added
- **CoinFull Prime 市場中性截面多空策略模組 (CoinFull Prime Market-Neutral Quant Engine)**：
  - 全新量化架構模組 (`core/services/coinfull_prime/`)：
    - `quant_engine.py`：非同步獲取流動性前 79 幣種，計算 24h 動能、主動買賣流比例、資金費率反向因子、大戶多空帳戶比等 4 大因子標準化 (Z-score) 與加權合成打分模型。
    - `rebalancer.py`：一籃子持倉平衡器與狀態機 (Position Diffing Engine)，支援 Top 8 做多 / Bottom 8 做空名義價值均衡配置、合約精度與步長動態裁切 (`stepSize`)、分批並發限速發單 (`Semaphore=3`)。
    - `service.py`：策略協調服務與生命週期管理，支援 0.0% CPU 休眠排程（每日 UTC 00:00 / 台北 08:00 自動喚醒執行換倉）。
    - 支援 Paper 本地模擬盤、Binance 合約測試網、Binance 合約實盤三種帳號環境切換。
- **三層抗干擾與避碰防護體系 (3-Layer Anti-Interference & Collision Avoidance System)**：
  - **第一層【專屬持倉籃子獨立記帳與隔離護欄】**：
    - 引入 `data/coinfull_prime_active_basket.json` 專屬持久化持倉帳本。
    - 換倉比對時嚴格執行「平倉白名單」：非本策略建立之部位（如網格/DCA/手動倉位）全面強制豁免，絕對不執行市價平倉與干擾。
  - **第二層【外部活躍機器人標的動態避碰與排除】**：
    - 每輪換倉與算分前，自動動態掃描 SQLite (`bots` 表) 與 `config/{grid,dca}` 設定檔，提取 13+ 個運行中網格/DCA 機器人標的與基礎幣種。
    - 自 79 幣流動性候選池直接剔除上述標的，CoinFull Prime 絕不挑選或開立衝突幣種，徹底杜絕保證金擠兌、重複開倉或反向對沖。
  - **第三層【排程目標雙向同步與防呆標記】**：
    - 新增 `/api/v1/coinfull-prime/scheduler/target` API。前端切換環境時即時雙向同動後端定時器目標。
    - 排程卡片明確標示「定時排程實際目標」與一鍵同步按鈕；合約持倉列表即時標註 `🛡️ 外部機器人保護中`、`⚪ 外部獨立持倉 (已隔離)`、`🔵 專屬籃子 (留榜微調)`、`🟠 專屬籃子 (退榜平倉)` 徽章。
- **動態倉位規模與槓桿控制 (Dynamic Sizing & Leverage Preview)**：
  - 支援「固定金額 (USDT)」與「帳戶可用餘額百分比 (%)」雙模式，動態讀取合約帳戶即時保證金餘額。
  - 支援自訂槓桿倍數 (1.0x ~ 2.0x)，前端提供動態即時試算徽章與二次確認彈窗，清晰展示總名義曝險與單幣分配額。
- **架構演進 Phase 0 ~ Phase 3 (Architecture Evolution P0 - P3)**：
  - P0: 零停機配置遷移與即時安全金鑰加密保存 (`core/security/`、`tools/encrypt_api_key.py`)。
  - P1: 工作行程隔離與 Supervisor 行程生命週期管理 (`core/supervisor/`、`scripts/deployment/crypto-engine.service`)。
  - P2: 雙模持久化與 Canonical Ledger 事件帳本整合 (`core/infrastructure/database/`、`async_writer.py`、`ledger_store.py`、`redis_support.py`)。
  - P3: 極致低延遲 Go 交易核心與高效 IPC 通訊協定 (`trading-core-go/`、`proto/`、`core/ipc/`、`core/adapters/exchanges/adapters/binance_fast.py`)。

### Changed
- **前端版本升級至 0.3.2** (`web_ui/frontend/package.json`)。
- **知識圖譜同步重構**：知識圖譜 `graphify` 完成最新程式碼 AST 提取與多策略社群索引更新。

### Fixed
- **定時排程目標帳號未同步修復**：解決前端切換實盤/測試網時背景定時排程器未即時連動更新目標的問題。
- **持倉取得小數精度裁切與退榜狀態誤導修復**：解決無持倉或非專屬持倉被誤標記為「退榜 (即將平倉)」之視覺誤導，增強防護透明度。

---

## [0.3.1] — 2026-09-05

### Fixed
- **網格機器人 HTTP 非安全上下文啟動按鈕修復 (Safe UUID Fallback)**：
  - 修復 `web_ui/frontend/src/services/api.ts` 中 `startGrid`、`stopGrid`、`startDca`、`stopDca` 使用 `crypto.randomUUID()` 作為預設參數的問題。
  - 由於瀏覽器安全規範在非 HTTPS（HTTP IP 存取）環境下禁用 `window.crypto.randomUUID`，點擊按鈕原會拋出 `TypeError` 阻斷請求。新增相容性 `generateUUID()`（支援 RFC4122 v4 降級）解決點擊無反應問題。
- **網格監控工作台 (GridDetail) 操作按鈕補齊**：
  - 在 `web_ui/frontend/src/components/GridDetail.tsx` 頁面頂部補齊「啟動策略」（Play）與「停止策略」（Square）控制按鈕，支援執行狀態鎖定、防抖與即時頁面重載。
- **中文交易對與小數價格配置檔命名防護**：
  - 優化 `web_ui/backend/yaml_generator.py` 中的 `_build_file_path`：針對中文幣種（如「牛来USDT」）經正則過濾後為純 quote 貨幣或空字串的情況，自動注入短 hash 防止檔名塌陷；支援小於 1 的小數價格格式化（如 `0_06442`），防止 `int()` 強制截斷為 `0-0`。
- **網格策略會計恆等式與純淨收益 (Pure Net PnL) 校正**：
  - 修復 `web_ui/backend/runtime_repository.py` 與前端展示邏輯：嚴格貫徹會計恆等式 `net_pnl = grid_profit + unrealized_pnl - total_fees`。
  - 解決過去重啟或讀取歷史快照時 `net_pnl: 0` 覆蓋計算欄位的問題，前後端同步持久化手續費與純淨收益。
- **實盤歷史成交流水補回與帳本修復工具**：
  - 新增 `tools/sync_binance_grid_trades.py`：直連幣安實盤 API 同步各機器人的真實成交單據與扣除手續費。
  - 新增 `tools/rebuild_grid_history_from_fills.py`：自真實 `trade_fills` 重新核算各機器人累計毛利、手續費與純淨收益，修正受損快照。
- **DCA 雙向與單向自癒回退優化**：
  - `DcaCoordinator` 與 `TakeProfitManager`：強化平倉與加倉訂單執行邊界處理，支援自癒與單向回退，防止市價單與限價單競態。

### Added
- **單元與整合測試套件擴充**：
  - 新增 `tests/test_binance_symbol_mapping.py`：驗證中文、特殊與常規合約交易對符號正規化。
  - 新增 `tests/services/grid/test_check_pending_orders_reconcile.py`：驗證網格掛單三路對帳與殘單標記取消機制。
  - 新增 `tests/test_dca_self_healing_and_oneway_fallback.py`：驗證 DCA 執行自癒保護。

---

## [0.3.0] — 2026-09-01

### Added
- **策略市場完整回測（網格 / DCA 單向 / DCA 雙向 + 指標回測）**：
  - 左側新增「網格回測」分頁；策略市場每行新增「回測」按鈕（原「一鍵開格」保留）。
  - 網格區域：`core/services/grid/backtest/` 資金感知模擬器（逐倉保證金、權益曲線、最大回撤、清算價、壓力情境、參數掃描、需追加資金防爆倉建議）；`/api/v1/grid-backtest/{run,stress,sweep}`。
  - 資金分配：支援「每格固定金額」與「每格佔總資金 %」；資金不足以掛滿格數時警示。
  - DCA 區域：統一入口支援單向做多 / 單向做空 / 雙向；指標入場（RSI / MACD / Bollinger / PriceChange）；來源為手動 draft 或既有 DCA bot；直接複用既有 `/api/v1/dca/backtest` 真實引擎與 `DcaBacktestPanel/Result`。
  - 見 `docs/grid_backtest_user_guide_zh-TW.md`、`docs/grid_backtest_audit_report_zh-TW.md`、`docs/grid_backtest_implementation_plan_zh-TW.md`。
- **Agent 指導文件與 CI 適配（2026-09-01）**：
  - `.agents/rules/README.md` 與 `AGENTS.md`：資料庫段落改為現況（SQLite 預設、`DATABASE_URL` PostgreSQL、Alembic `0001/0002`、既有 DB 不可 `alembic upgrade head`），服務重啟改為 repo-relative / systemd / PM2，測試改為 `scripts/ci.sh` 或 `unittest`。
  - `.agents/skills/*`：修正 `db-migration` 表清單（`audit_log` 只在 `grid_audit.db`、`dca_klines` 在 runtime DB）、`code-quality`/`dashboard-enhancement`/`deployment-check`/`ssh-tunnel` 的硬編碼路徑與部署方式、`skill-creator` 的 Python/Web UI/adapter 元件清單、`test-generation`/`release-gate` 的測試命令與新回測回歸。
  - `scripts/ci.sh`：納入既有 grid/DCA 回歸與新 grid backtest 測試模組。
  - `PositionTrackerImpl`：新增 `self.total_volume` 累計成交額 (USD) 指標，每筆成交即時累加 `filled_price * filled_amount`，重置時清零。
  - `GridStatistics` 與 `GridMetrics`：新增 `total_volume` 欄位並輸出至展示字典 `volume`。
  - `GridCoordinator`：重啟從持久化快照恢復時支援載入 `volume`；對舊版未帶 volume 的快照以歷史成交單數與配置金額進行基準平滑回補。
  - `run_multiple_grids.py`：多網格心跳狀態回傳即時注入 `volume` 指標。
- **UI/UX 前端深色體驗升級 (Obsidian Pro Dark Theme Form Controls)**：
  - 重構全域下拉選單（`select`）與回測表單輸入框，統一暗色配色、自訂 SVG 箭頭圖示、高亮聚焦邊框，徹底解決深色模式下系統預設原生表單元件全白反光問題。

### Changed
- **系統併發與安全閾值擴容 (Concurrency Limit Expansion)**：
  - `SafetyGuard` (`web_ui/backend/safety_guard.py`)：將最大同時運行網格策略數 `MAX_CONCURRENT_GRIDS` 提升至 10，合併最大策略上限 `MAX_CONCURRENT_COMBINED_BOTS` 提升至 20。
  - `BinanceRest`：節流閥 `_throttle_max_calls` 上限由 3 提升至 5，增強多機器人並行時的 REST API 吞吐能力。

### Fixed
- **DCA 下單保護與 Binance 介面參數對齊**：
  - `BinanceBaseAdapter` / `BinanceRest`：API Key 與 Secret 自動去除頭尾空白字元與換行符號（`strip()`），徹底杜絕複製貼上金鑰引起的認證與簽名錯誤（`-2014` / `-1022`）。
  - `Binance` 合約下單介面：參數支援 `clientOrderId` / `newClientOrderId` 與 `reduceOnly`；DCA 停利（Take-Profit）與停損（Stop-Loss）市價平倉委託自動注入 `reduce_only=True`，杜絕平倉單因市況超額成交導致反向開倉。
  - `run_multiple_grids.py`：新增 `starting_grids` 與 `starting_dca` 啟動防重集合，解決冷啟動階梯式等待（stagger delay）期間熱插拔監控協程重複拉起同一機器人任務的競態條件。
- **環境防線路徑匹配與協調器類型安全加固**：
  - `EnvironmentGuard` (`core/infrastructure/database/guard.py`)：修正生產資料庫路徑判定，嚴格錨定專案根目錄 `data/`，避免外部任意路徑包含 `/data/` 引起誤判。
  - `DcaCoordinator` (`core/services/dca/coordinator/dca_coordinator.py`)：修復 `load_by_bot_id` 回傳值 tuple 解構安全性防護。
- **線上既有庫平滑導入修正（2026-08-31，`docs/live_upgrade_playbook_zh-TW.md` 診斷沙箱實證）**：
  - `DatabaseMigrator` 遷移後補寫 `bot_snapshots`（原只回補 bots/trade_fills/bot_positions，P1-2 穩定 bot_id 恢復主路徑在遷移後首次重啟會落空、被迫走 legacy 啟發式）；僅在無既有快照時寫入，重跑冪等。
  - `DatabaseMigrator` 與 `RuntimeSnapshotRepository.save()` 的 `completed_cycles` 回補新增 `filled_orders.cycles` 來源（舊版 grid 快照無 top-level `cycles`，原實作遷移後 grid 持倉投影恆為 0）。
  - 實證：既有 SQLite 庫**禁止**跑 `alembic upgrade head`（0001 會重複建立既有的 `grid_runtime_snapshots` 等 legacy 表而失敗）；既有庫由應用 `create_all`（checkfirst）只補缺表，Alembic 僅用於全新 PostgreSQL/SQLite。

### Added
- **Phase 3 事件溯源 + 交易所三路對帳（P3-1～P3-4）**：
  - `bot_events` append-only 事件流（per-bot 單調 seq + dedupe_key 冪等；Alembic `0002_bot_events`）：事件型別涵蓋 bot_created/bot_renamed/order_placed/order_cancelled/fill/take_profit/stop_loss/reset/state_transition/reconcile。
  - 事件發射（best-effort，絕不阻斷交易）：grid/DCA 成交、grid 重置檢查點（止盈→take_profit、本金保護/止損→stop_loss、其他→reset）、引擎生命週期、身份審計橋接。
  - `event_fold.py`：事件流摺疊重建投影（fill 數學與 rehydrate 一致；legacy 自動以 trade_fills 合成）；屬性測試證明 fold == 直寫（隨機序列 ×100）。
  - grid 三路對帳（`ExchangeReconciler.reconcile_grid`）：本地 facts ↔ 交易所持倉 ↔ open orders；交易所持倉回補/幽靈持倉歸零/殘單標取消，全部記 `reconcile` 審計事件；接入 `GridCoordinator.start()`（失敗不阻斷啟動）。
  - 稽核回放工具 `scripts/tools/replay_bot.py`（時間線 + 逐欄 diff + 退出碼）與災難恢復工具 `scripts/tools/rebuild_projections.py`（投影由 facts+事件 OCC 安全重建）。
  - 災難恢復 SOP：`docs/phase3_disaster_recovery_zh-TW.md`（PG + SQLite 刪除投影→重建→回放一致，沙箱實證）。

### Added
- **Phase 2 生產化資料層（SQLAlchemy + PostgreSQL + Redis）與稽核修復**：
  - `LedgerStore` 抽象（Protocol）＋ SQLAlchemy Core 實作：`SqliteLedgerStore`（預設）與 `PostgresLedgerStore`（`DATABASE_URL` 切換）；Alembic 納管 schema（`migrations/versions/0001_initial_schema.sql` 語意）。
  - PostgreSQL 真相源：連線池（`GRID_PG_POOL_SIZE`/`GRID_PG_MAX_OVERFLOW`）、`pool_pre_ping`、金額欄 `NUMERIC(38,18)`；一次性遷移 `scripts/migration/sqlite_to_postgres.py`（facts 優先、冪等可重跑、保留 OCC version 與 identity 審計）。
  - Redis（可拋棄層，全路徑 DB fallback）：熱快取 `grid:state:*`（write/read-through，TTL=`GRID_REDIS_STATE_TTL`）、Pub/Sub 事件 `grid:events` → `/ws/grid-events` WebSocket 推送、領導者選舉 `grid:leader:*`（SETNX + Lua 原子續約/釋放，TTL=`GRID_REDIS_LEADER_TTL`）。
  - 前端接入 `/ws/grid-events`：`runtime`/`lifecycle` 事件觸發即時快照刷新（debounce 150ms），頂欄顯示「即時/輪詢」狀態；輪詢仍為權威來源，重連後自動對帳刷新。

### Fixed
- **Phase 2 稽核修復（2026-08-31，見 `docs/phase2_audit_report_20260831_zh-TW.md` 與 `docs/phase2_fixes_worklog_20260831.md`）**：
  - **F1 (P0)**：`postgresql://` URL 自動正規化為 `postgresql+psycopg://`（repo 僅 pin psycopg v3，bare URL 原會因找不到 psycopg2 崩潰）；`is_postgres_url()` 接受驅動限定形式，不再靜默回落 SQLite；Alembic `env.py` 同步正規化。
  - **F2 (P1)**：PG 測試隔離——非預設 ledger 路徑映射至獨立 PG schema（與 SQLite 檔案同級隔離），PG 上模組級全套 DB 測試 38/38 綠；`scripts/ci.sh` 新增 `PG_TEST_URL` 可選 PG 測試段。
  - **F3 (P1)**：PG 模式下 `LedgerRepository._connect()`/`_ensure_schema(conn)` 明確拒絕（原會靜默讀取本機 SQLite 幽靈資料源）。
  - **F4 (P2)**：`GRID_REDIS_STATE_TTL`/`GRID_REDIS_LEADER_TTL` 實際接線（原文件宣稱生效但程式從未讀取）。
  - **F5 (P2)**：Redis Pub/Sub 訂閱自動重連（指數退避、上限 30s），Redis 重啟後事件推送自癒；API 關閉流程不再洩漏 listener traceback。
  - **F7 (P2)**：領導者選舉 fail-open 升級為 ERROR log；續約/釋放改 Lua compare-and-act 原子腳本；renew 迴圈追蹤連續失敗與所有權喪失並嘗試重取。
  - **F8 (P3)**：遷移保留來源 OCC `version`、搬運 `bot_identity_events` 並去重。
  - **F9 (P3)**：`rich` 補 pin（`core/services/grid/__init__.py` 硬依賴）。
  - 回歸：SQLite 391/391、PG 38 模組全綠、Redis 演練 15/15、前端 98 tests + lint 0 errors + build 通過。

### Fixed
- **穩定機器人身份，根除「小改動/重開即歸零」(P1-1 / P1-2)**：
  - 新增 `core/infrastructure/identity.py`：帳本 key `bot_id` 由 YAML `metadata.bot_id`（UUID hex）提供，與檔名解耦；缺漏時退回檔名 stem（legacy 完全向後相容）。
  - 身份分層：**UI/命令/訂單命名空間身份維持檔名 stem**（不變，確保 DCA LIVE `client_order_id` 命名空間連續）；**帳本持久化 key** 由 `resolve_bot_id`（metadata.bot_id）統一解析（`RuntimeSnapshotRepository.save`、grid fills、重啟恢復）。
  - `GridYAMLGenerator.generate()` 與 `DcaYAMLGenerator.generate()` 寫入 `metadata.bot_id`，並支援 `existing_bot_id` 供編輯/重建流程保留既有身份（改價格區間/重建不再漂移）；create 路由與 schema 支援 `bot_id` 傳遞。
  - `LedgerRepository` 新增 `load_snapshot_by_bot_id()` / `load_all_snapshots_by_bot_id()`；grid 與 DCA 重啟恢復改以 **bot_id 為 key** 查詢 `bot_snapshots`（config_path/symbol 啟發式僅作 legacy fallback）。
  - 新增 `scripts/migration/backfill_bot_ids.py`（冪等、原子寫入、`--dry-run`）為既有 YAML 補 `metadata.bot_id`。
  - 新增 `tests/test_bot_identity_stability.py`、`tests/test_backfill_bot_ids.py`。

- **快照單調防護，防止恢復失敗後以 0 值覆寫累計計數 (P1-4)**：
  - `LedgerRepository.save_snapshot` 在嚴格遞增計數器（cycles/buy/sell/fees）倒退時拒絕覆寫；合法重置（止盈/本金保護/價格脫離）以一次性 `_monotonic_bypass` 旗標豁免。
  - 新增 `tests/test_monotonic_guard.py`（5 案例）。

- **grid 成交逐筆寫入不可變成交流水 (P1-3)**：
  - `PositionTrackerImpl` 接受 `bot_id`，每筆成交 append 至 `trade_fills`（role=`grid`，以 client/order id 冪等；帳本失敗不阻斷交易）。
  - 新增 `tests/test_grid_fill_ledger.py`（3 案例）。

- **帳本身份可審計，未註冊 bot 拒絕寫入 (P1-6)**：
  - 移除 `bots` 表 `unknown` placeholder；`record_fill` / `update_position_occ` 在 bot 未註冊時拋 `LedgerIntegrityError`，杜絕「無主成交/持倉」污染帳本。
  - 新增 `bot_identity_events` 審計表（`created` / `config_linked`），`register_or_update_bot` 寫入身份軌跡，`get_identity_events` 供稽核。
  - `PositionTrackerImpl` 首次寫成交前先以 config 註冊 bot。
  - 新增 `tests/test_identity_audit.py`（5 案例）。

- **非同步批量寫入走完整 save 路徑 (P1-5)**：
  - `AsyncDatabaseWriter` 預設改走 `RuntimeSnapshotRepository.save` 完整落盤（bot 註冊 + fills + OCC 持倉 + snapshot），杜絕 routing 後漏失 DCA 成交抽取。
  - 新增 bounded retry 佇列（`max_retries`，重試失敗後捨棄並告警）、`async_write_enabled()` env gate（`GRID_DB_ASYNC_WRITE`，預設開）、`is_running()`。
  - `control_plane.publish_runtime` 於 writer 已啟動且啟用時 enqueue，否則同步 save；命令 promotion 保持同步。
  - `run_multiple_grids.main` 啟動後 `start()`、關閉時 `stop()` 最終 flush。
  - 新增 `tests/test_async_writer_full_save.py`（3 案例）。

- **Phase 1 Gate 重現驗證 (P1-Gate)**：
  - 新增 `tests/test_phase1_gate_repro.py`（3 案例）：改價格區間/重建後重啟，以穩定 `bot_id` 恢復累計計數與盈虧不歸 0；恢復失敗以 0 值回報時單調防護拒寫。

---

## [0.2.0-beta] — 2026-08-31

### Security & Safety
- **環境邊界安全防線與測試資料庫隔離 (Environment Guard & Test Isolation)**：
  - 新增 `EnvironmentGuard` (`core/infrastructure/database/guard.py`)，自動偵測執行環境（`APP_ENV`、`GRID_ENV`、`PYTEST_CURRENT_TEST`）。
  - 阻絕測試環境以任何形式開啟或寫入正式生產庫（`data/web_ui_runtime.sqlite3`、`data/grid_audit.db`），於測試執行時自動將連線安全重定向至獨立測試暫存區。
  - 新增全域測試配置 `tests/conftest.py`，於每個單元測試執行前後自動重置全庫 repository 單例，徹底阻絕跨測試污染。

### Fixed
- **雙軌自癒對帳引擎與強制止盈防護 (Exchange-as-Truth Reconciler & TP Enforcement)**：
  - 實作 `ExchangeReconciler` (`core/services/reconciler/exchange_reconciler.py`) 並接入 `DcaCoordinator`。
  - 啟動與對帳時自動進行「本地帳本 ⟷ 交易所真實持倉 ⟷ 在世掛單」三方校準。當本地快照為空但交易所存在在世持倉時，自動回補持倉均價、切換至 `AVERAGING` 狀態。
  - 檢測到在世持倉且無活躍 TP 單時，**立即發動 Take-Profit 原子補掛**，根除持倉裸奔與漏止盈風險。

### Added
- **不可變成交流水帳本與 OCC 樂觀鎖 (Immutable Ledger & OCC)**：
  - 實作正規化關聯資料庫架構 (`schema.sql`、`models.py`)，定義 `bots`（UUID 解耦檔案路徑）、`trade_fills`（Append-Only 成交流水）與 `bot_positions`（含 `version` 樂觀併發控制）。
  - 實作 `LedgerRepository` (`core/infrastructure/database/ledger_repository.py`)，提供不可變流水寫入、樂觀鎖持倉更新（衝突時拒絕覆蓋）與流水重算均價功能。
  - 升級 `RuntimeSnapshotRepository` 為 Facade 適配器，維持既有 API Contract 100% 向後相容。
- **非同步批量寫入與資料庫平滑遷移 (Async Batching & Migrator)**：
  - 實作 `AsyncDatabaseWriter` (`core/infrastructure/database/async_writer.py`)，以記憶體隊列匯聚高頻寫入為 2~3 秒批量事務，降低 80% 磁碟 I/O，消除 SQLite 鎖定競爭。
  - 實作 `DatabaseMigrator` (`core/infrastructure/database/migrator.py`)，全自動將既有機器人快照平滑遷移至關聯表。
- **單元測試套件擴充**：
  - 新增 `tests/test_database_guard.py`（環境隔離防線驗證）。
  - 新增 `tests/test_ledger_repository.py`（不可變流水與 OCC 樂觀鎖驗證）。
  - 新增 `tests/test_exchange_reconciler.py`（在世持倉自癒回補與 TP 強制部署驗證）。
  - 新增 `tests/test_database_migrator_and_writer.py`（資料庫遷移與非同步批量寫入驗證）。

---

## [0.1.0-beta] — 2026-08-31

### Fixed
- **網格收益精準計算與理論乘法公式廢除**：
  - 修正 `completed_cycles` 僅在單筆撮合獲利 `lot_profit > 0` 時累加；在持倉均價下方之賣單（或做空均價上方買單）如實記錄虧損並累加 `loss_cycles`。
  - 徹底移除 `cycles * grid_interval * order_amount` 粗暴理論補算公式，毛利與已實現損益嚴格依真實配對流水累積，杜絕虛擬收益灌水。
  - 修正交易勝負指標與狀態同步，勝率依實際 `win_trades / loss_trades` 計算，移除強制全勝假設。
  - 修復 `GridCoordinator.recover_old_orders()` 重啟時變數引用與快照匹配問題，確保全自動無損還原歷史累計數據。

- **DCA 機器人抗重啟狀態自動恢復（State Recovery）**：
  - 實作 `DcaCoordinator.recover_state_from_snapshot()`，重啟時自動自 SQLite 載入累計輪數 (`cycles`)、已實現盈虧 (`realized_profit`)、手續費與成交歷史清單 (`fills`)。
  - 重啟前處於持倉狀態（`AVERAGING` / `TRIGGERING`）時，自動恢復持倉數量、均價、成本、入場價、方向鎖與狀態機。

- **在世訂單自動接管（Order Takeover）與命名空間隔離**：
  - 實作 `DcaCoordinator.reconcile_startup_orders()`，掃描交易所掛單並依 `dca_{bot_id_hash8}_{leg}_{slot}` 命名空間自動接管 `_active` 白名單，識別 `BASE` / `SAFETY` / `TAKE_PROFIT` 角色，重啟後無縫延續監控。
  - 確認網格與 DCA 機器人之訂單命名空間與掛單記憶體白名單機制，同幣種其他來源（如手動單或其他策略）訂單皆被精確過濾排除。

### Added
- 新增 `tests/services/dca/test_dca_snapshot_recovery.py` 單元測試，覆蓋 DCA 快照恢復與掛單接管。
- 新增負收益撮合與回退公式移除之網格單元測試。全庫 420 項單元與契約測試 100% 通過。

---

## [0.0.5] — 2026-08-27

### Added
- **DCA 即時圖表與回測/掃描子系統 (Phase C1–C8)**：
  - 引入 `lightweight-charts@^5.2.1` 封裝受控圖表元件 (`DcaChart.tsx`)，支援即時 K 線、DCA 訂單與成交標記、均價線與多時框切換（5m/15m/1h/4h/1d）。
  - 實作確定性回放撮合交易所 (`ReplayExchange`) 與回放驅動核心 (`ReplayDriver`)，保證零未來函數與同源執行器驗證（BP-1/BP-2）。
  - 新增歷史回測 (`POST /api/v1/dca/backtest`) 與多參數網格並行掃描 (`POST /api/v1/dca/backtest/sweep`) 端點，支援預算上限與超時守衛。
  - 實作 K 線 SQLite 快取儲存層 (`dca_klines`) 與分頁回填機制 (`KlineService`)，支援 PAPER 確定性合成 K 線。

### Changed
- 後端模組匯入路徑全庫統一收斂為 `web_ui.backend.*` 套件拼法，並新增契約測試防止單例分岔。

---

## [0.0.4] — 2026-08-26

### Added
- **DCA 機器人全功能生命週期 (Phase 2–7)**：
  - 實作主協調器 (`DcaCoordinator`) 狀態機（`ARMED` → `TRIGGERING` → `AVERAGING` → `COMPLETED` → `COOLING` / `DEGRADED`）。
  - 實作下單執行器 (`DcaOrderExecutor`)，整合雙熔斷守衛（BinanceRest + GridEngine）、最小名目金額預檢與精度修約。
  - 實作 DCA 四步建立精靈 (`DcaForm.tsx`)，支援指標設定（RSI/MACD/BB/Price Change）與即時階梯試算預覽 (`DcaLadderPreview.tsx`)。
  - 實作 DCA 詳情工作台 (`DcaDetail.tsx`)，提供階梯狀態表、訂單、成交、事件時間線、指標與設定六大分頁。
  - 實作監督者多進程排程 (`run_multiple_grids.py`)，支援 5 秒熱插拔啟動/停止與獨立 adapter 連線池。

---

## [0.0.3] — 2026-08-25

### Added
- **三支柱收益與全帳戶持倉隔離架構 (Three-Pillar Profit & Position Isolation)**：
  - 建立 `grid_position`（網格專屬持倉）與 `account_position`（全帳戶合約持倉）雙軌分離，防止手動交易與外部策略干擾網格成本。
  - 前端與後端支援「策略純淨收益 (Pure Net PnL)」指標，精確計算套利毛利扣除浮動損益與手續費。
- **DCA 領域模型與階梯數學核心 (Phase 1)**：
  - 建立高精度 Decimal 階梯試算引擎 (`ladder.py`)、四種技術指標純函式引擎 (`indicators/`) 與入場評估器 (`EntryEvaluator`)。
- **雙軌稽核自動化標準 (Dual-Stage Audit Standard)**：
  - 建立宏觀架構 (Graphify) 與微觀行級代碼審查 (Open Code Review / `ocr`) 雙軌自動化審查標準。

---

## [0.0.2] — 2026-08-14

### Added
- **AI 智能網格推薦與回測引擎**：
  - 新增 `GET /api/v1/market/recommendation/{symbol}` 端點，依歷史 OHLCV 與 ATR 動態計算推薦價格區間、格數與預估年化 (APR)。
  - 建立策略精靈 (`GridForm.tsx`) 整合 AI 推薦卡片，支援一鍵帶入參數並即時預覽回測數據。
- **全市場熱門代幣波動度與年化排行榜**：
  - 新增 `GET /api/v1/market/rankings` 端點與 `MarketRankings.tsx` 頁面，具備 15 分鐘後端快取防護。
- **控制面板清單表格視圖 (Table View)**：
  - 新增 `GridTable.tsx`，Dashboard 支援卡片視圖與表格視圖無縫切換。

### Changed
- Web UI 全面升級為暗黑交易終端風格，改善手機版 Drawer 導航與轉場動畫。

---

## [0.0.1] — 2026-08-13

### Added
- **Web UI 與網格引擎核心整合**：
  - 建立前後端分離架構（FastAPI + React TypeScript + Vite），支援同源 API 代理與 API Token 認證。
  - 支援 LIVE（實盤）與 PAPER（模擬盤）雙軌隔離運行，LIVE 模式具備 feature flag 與 typed confirmation 安全保護。
  - 實作 SQLite 持久化快照儲存庫 (`RuntimeSnapshotRepository`) 與命令稽核日誌 (`GridCommandRepository`)。
  - 實作多網格引擎 (`run_multiple_grids.py`) 熱插拔調度與機器人生命週期狀態機。
  - 實作公開健康檢查端點 `GET /healthz` 與 systemd 服務模板。
