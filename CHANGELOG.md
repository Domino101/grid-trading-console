# Changelog

本專案的重要變更記錄於此檔案，格式參考 [Keep a Changelog](https://keepachangelog.com/zh-TW/1.1.0/)。

## [Unreleased] — 2026-08-26

### Added

- **DCA 機器人 Phase 4 — 後端 API 層（CRUD + 生命週期 + snapshot + preview + 指標端點）**:
  - **DCA YAML 生成器（`web_ui/backend/dca_yaml_generator.py`）**: API JSON → `config/dca/dca-*.yaml`（`dca:` 區段），產出可被 `load_dca_config()` 解析（稽核 4.8）；`validate_params()` 彙整 `DcaConfig` 全欄位驗證；`is_valid_bot_id()` 提供路徑穿越防護（稽核 4.22）。
  - **DCA REST 路由（`web_ui/backend/routes/dca.py`）**: §4.7 全部端點；`POST /preview` 直接呼叫 `core.services.dca.ladder.build_ladder()`（稽核 4.2），`direction=both` 回傳 `ladder_long`/`ladder_short` 且 `required=max(...)`（稽核 4.14）；`GET /snapshot` 走單次 `active_dca.json → YAML → RuntimeSnapshotRepository.load_all()` 一次讀模型（稽核 4.5）；未知值一律 `null`、`data_status=unavailable`（稽核 4.6）；LIVE/PAPER aggregate 不混算（稽核 4.7）；LIVE 建立比照 grid：feature flag（4.12）→ 市場規則 `min_notional` 對基礎單與每張安全單檢查（4.13）；create/start/stop/delete 皆寫審計日誌且不洩密（4.17/4.18）；冪等鍵（4.10）、刪除 409（4.11）、路徑穿越（4.22）。
  - **安全護欄擴充（`web_ui/backend/safety_guard.py`）**: `MAX_CONCURRENT_DCA_BOTS=5`、`check_can_create_dca()`；`check_max_combined_bots()` 把「網格 + DCA ≤ 10」作為總量上限（稽核 4.15）。
  - **指標端點（`web_ui/backend/routes/market.py`）**: `GET /api/v1/market/indicators/{symbol}` 回傳 RSI / MACD / BB / Price Change 即時值；60 秒記憶體快取（稽核 4.19）＋`run_in_executor` 包同步 ccxt（稽核 4.20）。
  - **契約測試**: `tests/test_dca_yaml_generator.py`、`tests/test_dca_http_contract.py`、`tests/test_dca_snapshot.py`（共 25 項新測試），登錄 `scripts/ci.sh`。

- **DCA 機器人 Phase 3 — 設定持久化、OHLCV 快取與監督者整合（熱插拔）**:
  - **設定載入（`core/services/dca/config_loader.py` + `config/dca/`）**: YAML dict → `DcaConfig`（分層：程式預設 ← defaults ← 個別 YAML）；`dca_account_mode_from_yaml()` 對未標 `account_mode` 判為 `unknown`（不得自動視為 paper，稽核 3.4）；缺 `symbol` / `base_order_amount` 產出明確中文錯誤（稽核 3.3）；未知欄位以 warning 列出（稽核 3.2）；`dca_config_to_dict()` 支援 YAML 往返一致（稽核 3.1）；`auto_fill_dca_precision()` 於執行期由 registry markets 填入缺省精度。新增 `config/dca/README.md` 與 `config/dca/example-dca-paper-eth.yaml`。
  - **共享 OHLCV 快取（`core/services/dca/market_data/ohlcv_cache.py`）**: 以 `(exchange, symbol, timeframe)` 去重；`asyncio.Lock` 防 cache stampede（稽核 3.7）；TTL = min(timeframe, 60s)（稽核 3.8）；交易所失敗時回傳上一份快取並標記 `stale=True`、無快取時回傳 `None`（稽核 3.9）；儲存時丟棄最後一根未收線 K 棒（運算約定 1）；`provider(exchange)` 直接接 `EntryMonitor`。實測 5 台同 symbol+timeframe 一 TTL 內底層呼叫 = 1（稽核 3.6）。
  - **PAPER 合成 K 線（`core/adapters/exchanges/adapters/paper.py`）**: `get_ohlcv()` 以 symbol 穩定 hash 為 seed 的確定性隨機遊走生成可重現 K 線，`raw_data={'synthetic': True}`（稽核 3.10/3.11）；`get_order()` 支援 client_order_id 反查；`create_order` 讀取 `client_order_id` 參數（DCA 命名空間冪等鍵）。
  - **監督者整合（`run_multiple_grids.py`）**: 新增 `run_single_dca_task()` 與 `active_dca_monitor()`；DCA 共用 `ExchangeAdapterPool`（同一交易所 1 個 adapter 實例，稽核 3.16）；`config/active_dca.json` 5s 熱插拔啟動/停止（稽核 3.12/3.13）；找不到設定檔或 `start_rejection` 即時發 `failed` 且同路徑不重複刷錯誤（稽核 3.14）；頂層 `except Exception` 只結束自己這條 task、發 `failed` lifecycle，**絕不向上拋**（R-1，稽核 3.15）；runtime 統計以 `publish_runtime()` 寫入 SQLite（稽核 3.17）；DCA 檔名 `dca-*` 前綴保證 `bot_id` 與 `grid_id` 命名空間隔離（稽核 3.18）。
  - **小修正**: `DcaCoordinator` 在 TP / 止損平倉成交後將 `state.position_quantity` 歸零，使 §4.6「completed 持倉 0」與 runtime 統計一致。
- **DCA 機器人 Phase 2 — 執行引擎與狀態機（armed → triggering → averaging → completed 迴圈）**:
  - **下單執行層（`core/services/dca/interfaces/` + `execution/`）**: `IDcaOrderExecutor` 抽象介面 + `DcaOrderExecutor` 包裝 ExchangeInterface；下單前查 `BinanceRest._is_adapter_circuit_open()` 與 `GridEngineImpl._shared_api_circuit_until` 雙熔斷源（唯讀複用，G-1/G-2 未觸）；熔斷中回 `DEFERRED_CIRCUIT_OPEN` 絕不送交易所寫入；價格 ROUND_HALF_EVEN / 數量 ROUND_DOWN quantize；低於 `min_notional` 直接拒絕並記錄（不送交易所）；失敗分類 `PLACED / REJECTED_MIN_NOTIONAL / DEFERRED_CIRCUIT_OPEN / ERROR`。
  - **主協調器（`core/services/dca/coordinator/`）**: `DcaCoordinator` 驅動完整狀態機；armed 期間交易所零掛單（INV-1）；基礎單成交後建階梯 → TP 先行掛出（倉位即刻受保護）→ min(window, N) 安全單遞補窗口；安全單成交 → 原子重掛 TP（嚴格 cancel-then-place）；急跌穿透窗口時以現價重新對齊剩餘槽位並記錄 `window_realigned`（跳過層不重置配額，INV-3 下單總次數 ≤ N）；曝險上限 `max_position_notional` 以「已成交成本 + 在掛安全單名目 + 本張計畫名目」計口徑，觸及記錄 `position_cap_reached` 且停止再攤平；成交統一漏斗（WS 推送 + REST 對帳雙路，`order_id` 去重）；`max_cycles` / `repeat_after_take_profit` / `cooldown_seconds` 循環語意。
  - **降級狀態（DEGRADED）**: TP cancel 成功但 place 失敗 → 轉 `degraded` 並逐輪自動重試（期間絕不回報正常狀態）；成功後自動回 `averaging`。狀態機新增 `degraded` 合法轉移（`averaging ⇄ degraded`）。
  - **工程約束**: 全部 `async def`（含 `pause()/resume()/stop()`，吸取網格教訓）；監控迴圈以 `while self._running` 終止；`start()` 建立的 task 全註冊 `self._tasks`、`stop()` 逐一 cancel + await（歸零驗證）；`stop()` 撤單不平倉且冪等；套件零 `print(`、零同步 sleep（靜態掃描測試把關）；direction=both 經 `DirectionLock` 單向互斥＋`both_tiebreak`，跨輪可換向（`both_directions_matched` 事件）。
  - **測試**: `tests/test_dca_{executor,coordinator}.py` 共 40 項測試（FakeExchange 確定性驅動，覆蓋計畫 Phase 2 稽核 2.1–2.21 全部斷言點，含 INV-1 全實管線 20 輪零掛單）；登入 `scripts/ci.sh` 與 `docs/github-actions-ci.yml`。全量後端 CI 157 項測試全綠，網格既有測試零回歸（稽核 2.22）。

### Testing

- 新增 `tests/test_dca_config_loader.py`（稽核 3.1–3.4）、`tests/test_dca_ohlcv_cache.py`（稽核 3.6–3.9/3.11）、`tests/test_dca_paper_e2e.py`（稽核 3.10/3.11/3.19，PAPER 全迴路 armed → 觸發 → 基礎單 → ≥2 安全單 → TP → completed，斷言 INV-1..INV-4）。
- `scripts/ci.sh` 與 `docs/github-actions-ci.yml` 已登錄三個新模組。

## [Unreleased] — 2026-08-25

### Added

- **DCA 機器人 Phase 1 — 領域模型與指標引擎（純函式核心，零 I/O）**: 依據 `docs/dca_bot_implementation_plan_zh-TW.md` 建立 `core/services/dca/` 子系統，為後續 6 個 Phase 的正確性地基：
  - **階梯數學（`ladder.py`）**: §4.5 凍結公式全 Decimal 實作（prec=28，localcontext 隔離），含 `build_ladder()` / `required_capital()` / `take_profit_price()` / `stop_loss_price()` 與 `quantize_price`（ROUND_HALF_EVEN）/ `quantize_quantity`（ROUND_DOWN）；鎖定向量 A/B 九個 + 四個值於 1e-6 容差全數對上；direction=both 資金試算依決策 D-2 取 max(long, short)。
  - **指標引擎（`indicators/`）**: RSI（Wilder 平滑）/ MACD（EMA 種子=SMA）/ Bollinger（母體標準差）/ Price Change（凍結索引語意）四指標純 stdlib 實作，鎖定向量（RSI 21.977841、MACD −2.240581/−0.784542/−1.456039、BB 105.193225/116.876210/128.559195、PC −11.782277/−12.373066）於 1e-6 容差對上；未收線 K 棒丟棄、暖機期 `insufficient_data`、IIR 收斂（300 vs 301 根 < 1e-6）皆有測試凍結。
  - **入場評估器（`entry/evaluator.py`）**: `immediate` 模式零指標呼叫（mock 斷言）、`indicator` 模式支援 all/any 組合邏輯與 `confirm_bars`（1–5）抗插針連續確認。
  - **領域模型（`models/`）**: `DcaConfig`（§4.2 欄位表 A–E 全欄位 + 全中文驗證訊息，`__post_init__` 彙整所有錯誤一次拋出）、`DcaState`（§4.6 凍結狀態機轉移表 + `InvalidDcaTransition`，INV-5；active_direction 鎖定語意，INV-6 前置）、`DcaOrder`（§4.1 `dca_{hash8}_{leg}_{slot}` 客戶端 ID 命名空間）、`DcaStatistics`（`to_display_dict()` 對齊 GridStatistics，未知值一律 None）。
  - **測試與驗證**: 三個 unittest 模組（77 項測試）登入 `scripts/ci.sh` 與 `docs/github-actions-ci.yml`；§11 附錄三支獨立驗證腳本落地於 `scripts/verify_dca_{vectors,indicators,convergence}.py` 供稽核現場直接執行。
  - **隔離保證**: 零新依賴（requirements.txt 無異動）、零 I/O（grep 驗證）、`core/services/grid/` 與 `run_grid_trading.py` 零異動（G-1/G-2）。
- **三支柱利潤與全帳戶持倉隔離架構 (Three-Pillar Profit & Position Isolation)**:
  - 引入 `grid_position`（網格專屬持倉）與 `account_position`（交易所全帳戶持倉）雙軌分離，防止手動單或其他策略持倉干擾網格策略的浮動盈虧與成本計算。
  - 引入 `grid_profit`（網格套利已實現毛利）與 `account_unrealized_pnl`（帳戶層級浮動盈虧）多維指標隔離。
  - Web UI 前端 Mini KPI Bar、`GridCard`、`GridTable` 與 `GridDetail` 全面升級顯示「策略純淨收益 (Pure Net PnL)」與網格/帳戶雙持倉數據（格式化升級至 4 位小數精度）。
- **雙軌稽核自動化標準 (Dual-Stage Audit Standard)**:
  - 於 `AGENTS.md` 建立宏觀架構 (Graphify) 與微觀行級 (Open Code Review / `ocr`) 雙軌自動化審查標準，並新增 `open-code-review` 技能。
- **狀態過渡視覺增強**:
  - 前端與後端生命週期支援 `starting` (啟動中) 與 `stopping` (停止中) 專屬狀態徽章與樣式，消除非同步指令在引擎排程中的狀態閃爍。

### Fixed

- **網格收益、持倉與成交監控死鎖修復 (Grid Profit & Position Monitoring Deadlock Fix)**:
  - **Coordinator 非同步/同步調用修復**: 將 `GridCoordinator.pause()` 與 `resume()` 重構為 `async def` 並支援 `auto: bool = False` 參數，徹底修復 `position_monitor` 與 `grid_engine_impl` 調用 `await coordinator.pause(...)` 時因同步回傳 `None` 拋出 `TypeError: object NoneType can't be used in 'await' expression` 的崩潰。
  - **背景監控任務生命週期重構**: 修正 `GridCoordinator._run_monitoring_loop()` 的迴圈終止條件（由 `while not self._paused:` 改為受 `while self._running:` 控制），確保暫停期間監控 Task 保持存活，在網路連線恢復後能自動喚醒與恢復網格調度。
  - **成交事件丟棄修復**: 解決策略暫停死鎖導致 `_on_order_filled()` 靜默丟棄所有交易所成交事件的問題，恢復 `PositionTracker` 與 `GridState` 正常累計成交買賣次數、套利循環格數 (`completed_cycles`)、毛利與淨利。
  - **多網格失敗計數器實例隔離**: 將 `PositionMonitor` 的 `_global_consecutive_failures` 與 `GridEngineImpl` 的 `_global_price_failures` 從類別靜態變數重構為實例屬性，防止單一交易對暫態 API 抖動觸發全域連鎖暫停。
  - **Binance WebSocket 持倉即時緩存**: 在 `binance_websocket.py` 新增 `_position_cache`，於收到 `ACCOUNT_UPDATE` 時解析 `P` 持倉陣列，並在 `binance.py` 導出緩存，使 Web UI 與策略統計能即時顯示幣安全帳戶持倉 (`account_position`)。
  - **反手單參數名稱相容性修復**: 在 `GridStrategyImpl.calculate_reverse_order` 與 `calculate_batch_reverse_orders` 支援 `grid_interval`、`interval` 與 `**kwargs`，徹底修復 `GridCoordinator` 傳入 `interval` 引發 `TypeError` 導致成交後反手單 100% 丟失的致命錯誤。
  - **幣安合約多資產保證金（Multi-Assets）餘額預檢修復**: 在 `_has_sufficient_balance` 與 `_get_cached_balances` 支援統計帳戶全域穩定幣總可用保證金（USDT + USDC），解決 `ETH/USDC:USDC` 交易對在錢包僅有 USDT 時買單遭本地預檢誤判為餘額不足而全數阻斷的問題。
  - **Binance Batch API 批量下單修復**: 導出 `BinanceAdapter.rest` 屬性並修復 `binance_rest.py` 內批量下單調用未定義 `_adjust_min_notional` 的錯誤，打通 `batchOrders` 批量掛單鏈路（單次 REST 請求完成 4 格掛單）。
  - **活躍窗口掛單去重精度修復**: 將 `place_batch_orders` 去重邏輯升級為 `(grid_id, side)` 二元組檢查，避免不同方向的舊訂單阻擋正確方向的目標補單。

### Testing

- 新增單元測試 `test_three_pillar_profit_isolation.py`、`test_fifo_arbitrage_and_window_protection.py`、`test_coordinator_stats_and_fills.py`。
- 總計 127 項 Python 測試 100% 通過（含 Circuit Breaker、Position Tracker、FIFO Arbitrage 等）。
- 前端 TypeScript (`tsc -b`) 與 Vite 生產環境 Bundle 建置 100% 通過。
- 實機熱重啟 `multi-grid-engine` (PM2) 與 `web_ui` 後端，現場驗證無死鎖異常日誌，驗證 `ETH/USDC:USDC` 成功透過 Batch API 掛出 2 買 2 賣對稱活躍窗口掛單（Grid 49/50 買單、Grid 51/52 賣單）。

## [0.3.0] — 2026-08-14

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
