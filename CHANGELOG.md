# Changelog

本專案的重要變更記錄於此檔案，遵循 [Semantic Versioning](https://semver.org/lang/zh-TW/) 與 [Keep a Changelog](https://keepachangelog.com/zh-TW/1.1.0/) 規範。
詳細歷史工程記錄請參閱 [CHANGELOG_LEGACY.md](file:///root/grid-trading-console/CHANGELOG_LEGACY.md)。

---

## [Unreleased]

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
