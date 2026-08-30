# Changelog

本專案的重要變更記錄於此檔案，遵循 [Semantic Versioning](https://semver.org/lang/zh-TW/) 與 [Keep a Changelog](https://keepachangelog.com/zh-TW/1.1.0/) 規範。
詳細歷史工程記錄請參閱 [CHANGELOG_LEGACY.md](file:///root/grid-trading-console/CHANGELOG_LEGACY.md)。

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
