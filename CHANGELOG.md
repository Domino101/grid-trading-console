# Changelog

本專案的重要變更記錄於此檔案，格式參考 [Keep a Changelog](https://keepachangelog.com/zh-TW/1.1.0/)。

## [Unreleased] — 2026-08-27

### Fixed

- **後端模組被載入兩次導致模組級單例分岔（P0，`web_ui/backend/__init__.py` 新增）**: `web_ui/backend` 既在 `sys.path` 上、又可作為套件匯入，`main.py` 與 grid 路由用 `from auth import …`、DCA/market 路由用 `from web_ui.backend.auth import …`，Python 因而**把同一個檔案執行兩次**，產生兩份 `auth._API_TOKEN`、兩份 `RuntimeSnapshotRepository` / `GridCommandRepository` SQLite 連線、兩份 `GridStateStore` 記憶體快取與兩個 `AuditLogger`。後果：CI 有 **17 條測試紅燈**（15 條 `401 Invalid or missing API token`、`test_dashboard_snapshot` 讀到真實 runtime 資料庫而回報 `live` 而非 `degraded`、2 條連鎖 ERROR），正式環境則是一顆計時炸彈（同一份狀態存在兩份）。修法為 meta-path 匯入別名：兩種拼法映射到同一模組物件，且**只在對應模組已載入且 `__file__` 指向 backend 目錄下同一檔案時**才建立別名，第三方套件與專案他處的同名模組不受影響。**後端需重啟才會生效**。
- **`GET /api/v1/dca/{bot_id}/chart` 的 `warnings` 出現重複條目**: `KlineService` 在 PAPER 路徑已放入「使用合成 K 線，非真實歷史」，端點又無條件再附加一次；前端是全量渲染 `warnings`，畫面直接顯示成兩行（C8 live 驗證實際觀察到）。改為去重後附加，並以 E2E 斷言把關。
- **`tests/test_dca_http_contract.py` 覆寫模組級全域後從不還原**: `_get_project_root` / `SafetyGuard` / `auth._API_TOKEN` / 三個 repository 單例的污染會外溢到後續測試模組，使結果與執行順序相關（C7 工作紀錄列為待清理項）。改用 `addCleanup` 自動還原全部 7 個全域。

### Added

- **DCA 即時圖表與回測子系統 Phase C8 — 端到端驗收、文件與最終稽核**:
  - **E2E 測試（`tests/test_dca_chart_backtest_e2e.py`，13 項）**: 走完整鏈路「建立 PAPER 機器人 → `/market/klines` → `/dca/{bot}/chart` → `/dca/backtest` → `/dca/backtest/sweep` → `DELETE`」，全部硬斷言 200 與欄位齊備（稽核 8.1）。**BP-2 反證**：以 `patch.object(PaperExchangeAdapter, "create_order"/"cancel_order")` 斷言回測期間真實下單方法呼叫次數為 0，並比對 `config/active_dca.json` 位元組不變（G-11/G-12）。另含 8.3 網格零回歸（可用 `GRID_REGRESSION_BASE` 指定基準 commit）、8.4 LIVE 維持關閉（`account_mode=live` → 403）、8.5 憑證掃描、8.6 單例一致性回歸防護。**全程離線**：`KlineService.fetch_fn` 換成會 raise 的 stub，任何真實交易所抓取立即變紅燈而非變慢。
  - **回測結果標記契約**: 斷言回測 `marks` 只含成交類標記且**不得**出現 `order_placed`（計畫 §4.7：該標記「僅即時圖」），並新增「同一請求連跑兩次結果逐欄位相同（`bars_per_second` 除外）」的確定性測試。
  - **文件**: 新增 `docs/dca_chart_backtest_user_guide_zh-TW.md`（標記語意表、三種角標、六條撮合簡化事項、**alpha 怎麼讀**、掃描的過度擬合警告、錯誤碼對照、curl 範例）與 `docs/dca_chart_backtest_audit_report_zh-TW.md`（五段式最終稽核，含突變測試與例外接受）；`README.md`、`config/dca/README.md`、`docs/dca_bot_user_guide_zh-TW.md` 同步補上圖表/回測章節與新端點。
  - **CI 雙登記**: `scripts/ci.sh` 與 `docs/github-actions-ci.yml` 同步登錄 `tests.test_dca_chart_backtest_e2e`。Python **333 tests OK**（C8 前為 320 tests / 15 failures / 2 errors）；前端 **90/90**、lint 0 error、build 成功，`lightweight-charts` 維持在獨立 chunk（gzip 57.93 kB，首屏不載）。
  - **實機驗證**: 重啟後端後對執行中實例逐一 curl 四個端點並記錄回應（見稽核報告第三段）。

- **DCA 即時圖表與回測子系統 Phase C6 — 前端圖表基礎（lightweight-charts 封裝 + DCA 詳情頁「圖表」分頁）**:
  - **新依賴 `lightweight-charts@^5.2.1`（決策 D-6，Apache-2.0）**: `package.json` 與 `package-lock.json` 一併提交（E-6.5）。選型理由見計畫 §3.2：recharts 3.8.1 無原生蠟燭圖、無 markers、無縮放平移，手刻成本高於引入專用函式庫。
  - **圖表元件（`web_ui/frontend/src/components/DcaChart.tsx`）**: 受控元件，props = `{candles, marks, lines, equity, direction, liveLastPrice, height, ariaLabel}`，**不做任何資料抓取**。K 線 + 標記 + 價格線 + 選配 equity 第二窗格；原生縮放平移與十字光標。**E-6.2 依賴隔離**：此為全前端唯一 import 圖表函式庫的檔案（契約測試以整棵 `src/` 掃描把關）。**RC-12 記憶體**：`useEffect` cleanup 呼叫 `chart.remove()` 並 `ResizeObserver.disconnect()`；price line 以 ref 保存，props 變動時先 `removePriceLine()` 再重建。主題色一律以 `getComputedStyle` 讀取既有 CSS token，不硬編碼色票。
  - **純函式層（`web_ui/frontend/src/utils/dcaChart.ts`，E-6.3）**: `toCandleSeries` / `toMarkerSpecs` / `toPriceLineSpecs` / `toEquitySeries` / `markerText` / `chartDataNotice` / `filterMarksForStatus`。輸出「與函式庫無關的中性描述物件」，可被 node --test 完整覆蓋。**§4.7 凍結語意表**（`DCA_MARK_SEMANTICS` / `DCA_LINE_SEMANTICS`）以資料結構落地：kind → shape / color token / position 逐行對應，做空時 base/safety 自動翻轉。
  - **G-5 未知不畫 0（稽核 6.3）**: `lines[].price=null` 不產生任何 price line；`realized_pct=null` 時 TP/SL 文字省略百分比而非顯示 `+0.00%`；`slot_index=null` 顯示 `S` 而非 `S0`。真實 0 與未知嚴格區分（`chartPercentText(0) === '+0.00%'`）。
  - **D-10 即時最後一根**: 已收線 K 棒來自 `/chart` 端點，最後一根以 snapshot `current_price` 在客戶端更新 close 並擴張 high/low；**不新增 WS、不新增輪詢端點**。
  - **詳情頁整合（`DcaDetail.tsx`）**: `TabType` 新增 `'chart'`（第七分頁，六個既有分頁不變）；時框切換 5m/15m/1h/4h/1d；圖表分頁啟用時 15s 輪詢（對齊 `DcaDashboard.tsx` 的 `REFRESH_MS`），**切離分頁/卸載即 `clearInterval` + `AbortController.abort()`**（稽核 6.5）。**稽核 6.10 chunk 隔離**：以 `lazy(() => import('./DcaChart'))` 動態匯入，圖表函式庫落在獨立 chunk。
  - **誠實回報**: `data_status=unavailable` 或零棒 → 顯示「無法取得 K 線資料」/「資料不足」，不畫空白圖讓人誤以為市場無波動（稽核 6.8）；`source=paper_synthetic` → 角標「合成資料」，`stale` → 角標「陳舊資料」（稽核 6.9）；後端 `warnings` 常駐列出。
  - **armed 語意（稽核 6.6）**: `status=armed` 顯示「監控中 · 交易所零掛單」提示條，且 `filterMarksForStatus` 將 `order_placed` 標記過濾為 0，避免與文案自相矛盾。
  - **API 契約（`services/api.ts`）**: 新增 `KlineCandle` / `KlinesResponse` / `DcaChartMark` / `DcaChartLine` / `DcaChartResponse` 型別與 `getKlines()` / `getDcaChart()` 方法，全部走同源相對路徑。
  - **樣式（`App.css`）**: 新增 12 個 `--dca-*` 圖表色 token，**每一個都指向既有語意色**（`var(--trading-green)` 等），不新增平行色票系統；工具列於 ≤640px 改為縱向堆疊（稽核 6.12 RWD）。
  - **測試**: 新增 `web_ui/frontend/tests/dca-chart.test.ts`（27 項純函式測試，覆蓋稽核 6.1 / 6.3 / 6.6 / 6.8 / 6.9）並登記於 `package.json` test script；`tests/test_dca_frontend_contract.py` 新增 `DcaChartSourceContractTests`（13 項檔案級契約，覆蓋 6.2 / 6.4 / 6.5 / 6.6 / 6.7 / 6.10 / 6.12）。前端 **63/63 通過**（基線 36 + 新增 27），Python 契約 **41/41 通過**。
  - **bundle 增量（稽核 6.10，對照 §5.0 基線）**: `index-*.js` gzip 101.58 → **101.27 kB（−0.31）**、`GridDetail-*.js` gzip **102.20 kB（不變）**、CSS gzip 8.16 → **8.44 kB（+0.28）**；新增獨立 `DcaChart-*.js` gzip **57.93 kB**，僅在使用者開啟圖表分頁時載入，**首屏未受影響**。`npm run lint` 0 error（維持既有 1 warning）、`npm run build` 成功。

- **DCA 即時圖表與回測子系統 Phase C5 — 後端 API 層（klines/chart/backtest/sweep 端點 + 契約測試 + 稽核日誌）**:
  - **即時圖表端點（`GET /api/v1/dca/{bot_id}/chart`）**: K 線資料 + runtime 快照標記（基礎單成交/安全單成交/止盈/止損/掛單/略過）+ 價格線（entry/average_cost/take_profit/next_safety/stop_loss）；引擎零變更，純投影（G-4）；K 線來源複用 C1 `KlineService.get_candles()`（含 PAPER 合成）；標記與價格線由 `_fills` / `_orders` / `ladder_slots` 直接投影（§4.7 語意表）。
  - **回測端點（`POST /api/v1/dca/backtest`）**: 單一設定歷史回放，支援 `bot_id`（既有 YAML）或 `draft`（精靈 JSON，走與建立機器人同一套驗證函式）；A-4 上限守衛（bars ≤ 20000、timeframe ∈ {1m,5m,15m,30m,1h,4h,1d}）；A-5 執行時間守衛（60s timeout）；A-6 warnings 強制包含「回測不模擬部分成交」「未套用交易所最小名目限制」「歷史績效不代表未來」三條簡化事項揭露；A-7 稽核日誌（action=run_dca_backtest）。
  - **掃描端點（`POST /api/v1/dca/backtest/sweep`）**: 參數網格展開 + ThreadPool 並行（預設 max_workers=4）+ 總棒數預算（預設 300000）+ truncated 誠實回報；支援 `base_order_amount_range` / `price_deviation_percent_range` / `take_profit_percent_range` / `max_safety_orders_range` 四個可掃描參數；A-5 執行時間守衛（120s timeout）；A-7 稽核日誌（action=run_dca_sweep）。
  - **API 契約模型（`web_ui/backend/schemas.py` 擴充）**: `DcaChartMark` / `DcaChartLine` / `DcaChartResponse`（§4.7 圖表契約）、`BacktestRequest` / `BacktestResponse` / `BacktestMetrics` / `BacktestEquityPoint` / `BacktestTradeMark` / `BacktestCycleRecord`（§4.4/§4.5 回測契約）、`SweepRequest` / `SweepResponse` / `SweepRunResult` / `SweepBudget`（§4.4 掃描契約）。
  - **稽核日誌擴充（`web_ui/backend/audit.py`）**: 新增 `run_dca_backtest` / `run_dca_sweep` 兩個 action 類型（§5.10）。
  - **HTTP 契約測試（`tests/test_dca_backtest_http_contract.py`）**: 5.1 認證（四個端點無 token → 401）、5.4 上限守衛（bars=20001/不支援 timeframe）、5.8 契約（chart/backtest/sweep 回應欄位完整性）、5.7 warnings 揭露、5.13 網格零回歸、5.9 前端零計算 grep 契約、5.11 不阻塞事件迴圈 grep 契約。
  - **CI 雙登記**: `scripts/ci.sh` 與 `docs/github-actions-ci.yml` 同步登錄 `tests.test_dca_backtest_http_contract`。
  - **零回歸保證**: `core/services/grid/` 與 `run_grid_trading.py` 零變更（G-1/G-2，§5.13）。

- **DCA 即時圖表與回測子系統 Phase C4 — 績效指標純函式與參數掃描（預算上限 + truncated + 預計算等價）**:
  - **績效指標（`core/services/dca/backtest/metrics.py`）**: `compute_metrics()` 純函式輸出 §4.5 全部欄位。`realized_pnl` / `total_fees` 必須取自引擎 `build_statistics()`（禁止加總 `fills[].fee`，稽核 4.5）；`buy_hold_return_pct` 與 `alpha_pct` 為必要指標（做空為反向基準，稽核 4.2）；MDD 為峰值→谷底而非首尾差（`[100,120,90,110] → 25%`，稽核 4.4）；無虧損輪時 `profit_factor=None`（不是 0 / inf，稽核 4.3）；無輪次時 `win_rate_pct` / `avg_cycle_bars` / `mfe_pct` 皆為 `None`（G-5，稽核 4.6）。`ReplayDriver` 改呼叫此純函式，避免兩套口徑。
  - **參數掃描（`core/services/dca/backtest/sweep.py`）**: `expand_grid()` 笛卡兒積；`run_sweep()` 以 `ThreadPoolExecutor` 並行（預設 `SWEEP_MAX_WORKERS=4`，同時執行中的 replay ≤ 4，稽核 4.7）；總棒數預算 `SWEEP_MAX_TOTAL_BARS`（預設 300000），超預算 `truncated=true` 且不回報未完成 run 的指標（稽核 4.9）；非法組合（如 `max_safety_orders=0`）標記中文 `error`、整體不崩潰（稽核 4.10）。
  - **掃描加速與等價性（稽核 4.8）**: 固定 entry 時預先以 `indicator_trigger_series` 計算每一個 `fetch_limit` 視窗的觸發序列，經 `EntryEvaluator(series_fns=...)` 注入 `EntryMonitor` / `ReplayDriver`；P0 測試鎖定「每一棒 `should_enter` 完全相同」且最終 `BacktestResult` 逐欄位相同。
  - **測試與驗證**: 新增 `tests/test_dca_backtest_metrics.py`（8 項，稽核 4.1–4.6 / 4.11）與 `tests/test_dca_backtest_sweep.py`（9 項，稽核 4.7–4.10 / 4.8 / 4.11）；已登錄 `scripts/ci.sh` 與 `docs/github-actions-ci.yml`。C4 模組 **17 項全綠（0.31s）**，連同 C2 ReplayExchange 共 30 項全綠。
  - **零回歸保證**: `core/services/grid/` 與 `run_grid_trading.py` 零變更；本 Phase 為純核心庫，無需重啟服務。

- **DCA 即時圖表與回測子系統 Phase C3 — `ReplayDriver` 回放驅動器（真 Coordinator + 真 Executor + 確定性與狀態守衛）**:
  - **資料契約模型（`core/services/dca/backtest/models.py`）**: `TradeMark`（下單/基礎單成交/安全單成交/止盈/止損/略過標記）、`EquityPoint`（每棒權益、持倉量、攤平均價、狀態）、`CycleRecord`（輪次索引、方向、進出場時間/價格、安全單成交數、MFE%/MAE%、持倉棒數、出場原因）、`BacktestRequest`、`BacktestResult`（§4.4 與 §4.5 凍結契約）。
  - **回放驅動核心（`core/services/dca/backtest/replay_driver.py`）**:
    - **BP-1 同源**: 實例化真正的 `DcaCoordinator` 與 `DcaOrderExecutor`，對接 `ReplayExchange`，禁止存在第二套執行器或階梯數學（反證 grep 0 命中）。
    - **R-8 熔斷固定關閉**: 注入 `circuit_probe = lambda: False`，實盤熔斷狀態完全不干擾回測結果。
    - **R-9 棒時間時鐘**: 注入 `time_fn = lambda: float(candles[i].ts)`，`cooldown_seconds` 嚴格依 K 棒時間計算，避免毫秒級回放導致冷卻失效。
    - **R-10 資料邊界無未来函数**: `ohlcv_provider` 僅回傳 `<= i` 的 closes 切片；前視注入測試（40 棒後投毒 +100%）驗證 40 棒前的所有決策與標記 100% 不受未來數據影響。
    - **每棒狀態不變量守衛**:
      - `INV-1`: `status == ARMED` 時交易所在世掛單數必須為 0，`active_direction` 為 None。
      - `INV-2`: `status == AVERAGING` 且持倉 > 0 時，交易所 TP 掛單必須恰好 1 張。
      - `INV-6`: 單方向互斥，禁止同時存在多方向掛單，cooling/armed 下 `active_direction` 為 None。
    - **顯示層時間蓋章**: 每棒 diff `coord.fills_snapshot()` 副本並蓋上棒時間 `bar.ts`，絕不修改 `coord._fills` 原物件（3.10 測試鎖定）。
    - **次棒撮合與漏斗推送**: 棒 `i` 決策送單，棒 `i+1` 依 R-4 保守順序（安全單先、TP 後）撮合並透過 `handle_ws_fill` 推入成交漏斗。
    - **誠實揭露與指標**: 數據結束時仍持倉標註 `exit_reason="end_of_data"`（未實現損益如實以最後收盤評價，3.13）；計算 §4.5 凍結指標（ROI, buy&hold, alpha, MDD, exposure, MFE/MAE, profit_factor）；輸出 D-11 簡化揭露警示。
  - **測試與驗證（`tests/test_dca_replay_driver.py`）**: 新增 14 項單元測試，覆蓋 C3 稽核項 3.1～3.14 全部斷言點；已登錄 `scripts/ci.sh` 與 `docs/github-actions-ci.yml`；全量後端 CI **273 項全綠**（基線 259 + 新增 14），前端測試 36/36 通過，build 與 lint 零新增錯誤。
  - **效能基準**: 3000 根 15m 棒回測吞吐量達 > 2000 bars/sec（遠超 > 500 bars/sec 基準）。
  - **零回歸保證**: `core/services/grid/` 與 `run_grid_trading.py` 零變更。

- **DCA 即時圖表與回測子系統 Phase C2 — `ReplayExchange` 撮合器（凍結規則 R-1 ～ R-13）**:
  - **K 棒模型與序列工具（`core/services/dca/backtest/candles.py`）**: `Candle` 凍結 dataclass（`ts, open, high, low, close, volume`）；`__post_init__` 嚴格校驗高低價合規性；全純函式、純 Decimal 實作（prec=28，零 I/O）。
  - **確定性撮合交易所（`core/services/dca/backtest/replay_exchange.py`）**:
    - **R-1 無未来函数**: 棒 `i` 送出的限價單記 `placed_bar=i`，僅在棒 `j > i` 參與撮合，徹底杜絕當棒秒成交與利潤高估（RC-1）。
    - **R-2 / R-3 限價單撮合與跳空成交價**: 買單條件 `low <= price`，成交價 `= min(open, price)`；賣單條件 `high >= price`，成交價 `= max(open, price)`（跳空以更優的開盤價成交）。
    - **R-4 同棒多單保守順序**: 同棒不利優先原則（做多：安全買單由高到低先結算，再結算止盈賣單；做空相反）。
    - **R-5 市價止損單**: 於觸發棒以 `open` 成交，內建跳空滑價保守估計。
    - **R-6 不模擬部分成交**: 整張成交（v1 簡化），模組 docstring 明確揭露。
    - **duck-typing 契約完全相容**: 實作 `create_order` / `cancel_order` / `get_open_orders` / `get_order` / `get_ticker`，可無縫對接真 `DcaOrderExecutor`。
    - **零 I/O 與 Decimal 純度**: 無任何檔案/網路/時鐘/睡眠呼叫（grep 0 命中），計算路徑 0 `float()`。
  - **測試與驗證（`tests/test_dca_replay_exchange.py`）**: 新增 13 項單元測試，覆蓋 C2 稽核項 2.1～2.13 全部斷言點；已登錄 `scripts/ci.sh` 與 `docs/github-actions-ci.yml`；全量後端 CI **259 項全綠**（基線 246 + 新增 13），前端測試 36/36 通過，build 與 lint 零新增錯誤。
  - **零回歸保證**: `core/services/grid/` 與 `run_grid_trading.py` 零變更。

- **DCA 即時圖表與回測子系統 Phase C1 — K 線歷史資料層（SQLite 快取 + 回填 + 汰換）**:
  - **K 線儲存（`web_ui/backend/kline_repository.py`）**: 新增 `dca_klines` 表（`exchange, symbol, timeframe, ts, open, high, low, close, volume, synthetic, fetched_at`）；TEXT 儲存十進位價格字串防浮點漂移（K-3）；全參數化查詢防 SQL 注入（K-1，grep 0 命中）；冪等 upsert 且真實歷史資料絕不被合成資料降級覆寫（K-2）；等距缺棒偵測 `count_gaps()`；180 天過期資料自動汰換 `prune()`（K-4）。
  - **K 線服務（`web_ui/backend/kline_service.py`）**: 快取優先讀取（S-1）；缺棒時分頁回填（S-2，單頁 ≤ 1000 根）；API 權重預算保護（S-3，最多 `KLINE_MAX_FETCH_PAGES=8` 頁，超額誠實標記 `data_status="stale"` 與 warning）；PAPER 模式呼叫 `PaperExchangeAdapter.get_ohlcv()` 產生可重現確定性合成 K 棒，`source="paper_synthetic"` 且 `synthetic=1`（S-4）；交易所異常/429 誠實降級為 `stale` 或 `unavailable`，絕不謊報 `live`（S-5）；非同步介面包裝 threadpool 執行，保證不阻塞事件迴圈（S-6）。
  - **API 契約與端點（`schemas.py` + `web_ui/backend/routes/market.py`）**: 新增 `GET /api/v1/market/klines/{symbol:path}` 端點；支援 `exchange` / `timeframe` (1m/5m/15m/30m/1h/4h/1d) / `limit` (1~20000) / `until` / `account_mode`；輸出 `KlinesResponse` 契約（`candles[{t,o,h,l,c,v}], source, data_status, gaps, warnings, oldest_ts`）。
  - **測試與驗證（`tests/test_dca_kline_repository.py`）**: 新增 12 項完整單元測試覆蓋 C1 稽核項 1.1～1.13；已登錄 `scripts/ci.sh` 與 `docs/github-actions-ci.yml`；全量後端 CI **246 項全綠**（基線 234 + 新增 12），前端測試 36/36 通過，build 與 lint 零新增錯誤。
  - **零回歸保證**: `core/services/grid/` 與 `run_grid_trading.py` 零變更。

- **DCA 即時圖表與回測子系統 — 完整開發計畫**: 新增 `docs/dca_chart_backtest_plan_zh-TW.md`，接手 `docs/dca_bot_implementation_plan_zh-TW.md` §8 排除項中的「回測」與「TradingView 圖表」。切為 8 個 Phase（C1 K線資料層 / C2 ReplayExchange / C3 ReplayDriver / C4 指標與掃描 / C5 後端 API / C6 圖表基礎 / C7 回測工作台 / C8 E2E 與文件），預估 19 新檔、~6750 行（含 ~2550 行測試）、9–11 次對話，與 DCA 主案同量級。
  - **兩條可證偽性質**: BP-1（回測必須走真 `DcaCoordinator` + 真 `DcaOrderExecutor`，禁止第二套階梯/指標數學）、BP-2（回測零交易所寫入、零 `active_dca.json` 副作用）。
  - **凍結資料契約**: `dca_klines` SQLite 表（TEXT 存十進位、180 天保留期、全參數化）、撮合規則 R-1～R-13（次棒才撮合、跳空以開盤價成交、同棒保守順序、棒時間時鐘、熔斷固定關閉）、績效指標定義（含 buy&hold 與 alpha 為必要指標、未知值一律 `None`）、四個新端點、圖表標記與價格線語意表。
  - **決策凍結 D-6～D-11**（經使用者確認）: lightweight-charts（含 rule-07 豁免條件 E-6.1～E-6.5）、複用 Coordinator 回放、單一回放＋參數掃描、SQLite K 線快取、即時圖表以「已收線 K 棒＋snapshot 補最後一根」免新增 WS、撮合簡化事項必須在 API 與 UI 同時揭露。
  - **全域規則新增 G-11～G-18**、風險登錄簿 RC-1～RC-16、8 個 Phase 共 100+ 項 P0/P1 稽核條件。
  - **POC 實測證據（§11.4）**: 以 throwaway 腳本實跑證明 BP-1 可行（完整循環、零網路呼叫、INV-1/INV-2 每棒成立、引擎 `window_realigned` 邊界路徑照常觸發）；並量化 R-1 為 P0 的依據（跳空路徑下同棒撮合憑空創造部位回報 realized 0.2004，實盤為 0 成交）。
  - 本輪僅產出計畫文件，未變更任何程式碼；`core/`、`web_ui/`、`run_*.py`、`config/` 零變更，無需重啟服務。

- **DCA 機器人 Phase 7 — DCA 詳情工作台、端到端驗收與交付文件**:
  - **詳情工作台（`web_ui/frontend/src/components/DcaDetail.tsx`）**: 六分頁工作台（階梯 / 訂單 / 成交 / 事件 / 指標 / 設定），選取機器人卡片後由 `DcaDashboard` 切換為完整頁面。KPI 摘要與大盤卡片共用同一讀模型（stats + snapshot），確保均價/持倉/盈虧一致（稽核 7.7）；`armed` 狀態明確標示「監控中 · 交易所零掛單」且訂單分頁應為空（稽核 7.1）；成交分頁 50 筆分頁避免長列表卡頓（稽核 7.9）；錯誤路徑（下單被拒、熔斷、429、WS 斷線）以 `notice-error` 明確呈現、絕不顯示為正常（稽核 7.10）。
  - **階梯狀態表（`web_ui/frontend/src/components/DcaLadderTable.tsx`）**: 完整安全單表（slot / 偏移% / 價格 / 數量 / 名目 / 狀態），已成交/掛單中/未觸及三態徽章（＋急跌略過），標出當前價列與均價線圖例（稽核 7.8）；純顯示，零階梯數學（G-4，檔案級契約 grep 0 命中）。
  - **引擎詳情快照（`core/services/dca/coordinator/dca_coordinator.py`）**: 新增 `open_orders_snapshot()` / `fills_snapshot()` / `ladder_slots_snapshot()`；成交歷史 `_fills` 累積跨輪（含手續費與成交後累積均價）；安全單成交後 TP 隨新均價重掛時發 `tp_replaced` 事件（稽核 7.3）。
  - **監督者發布（`run_multiple_grids.py`）**: DCA runtime 心跳追加 `open_orders` / `fills` / `ladder_slots` 快照欄位（導出失敗不阻斷心跳）。
  - **後端端點補實（`web_ui/backend/routes/dca.py`）**: `GET /{id}/orders`、`GET /{id}/fills` 由引擎 runtime 快照回填（無 runtime → `available:false`）；`GET /{id}/ladder` 優先採用引擎實際槽位三態，退回 `build_ladder()` 重算。
  - **API client（`api.ts` 擴充）**: `DcaInfoResponse`/`DcaStatsResponse`/`DcaLadderResponse`/`DcaOrdersResponse`/`DcaFillsResponse`/`DcaEventListResponse`/`DcaLevelView`/`DcaEvent` 型別與 `getDcaBot()`/`getDcaStats()`/`getDcaLadder()`/`getDcaOrders()`/`getDcaFills()`/`listDcaEvents()`。
  - **純函式（`utils/dca.ts` 擴充）**: `dcaSlotStatusMeta`/`DCA_SLOT_STATUS_META`（階梯三態＋略過）、`dcaEventLabel`/`dcaEventTone`（事件時間線文案與語氣）、`dcaOrderRoleLabel`/`dcaSideLabel`。
  - **交付文件**: 新增 `docs/dca_bot_user_guide_zh-TW.md`（使用者操作手冊：四種指標、階梯數學、armed 語意、風險說明）、`docs/dca_bot_audit_report_zh-TW.md`（Phase 1–7 五段式最終稽核報告）；`config/dca/README.md` 補詳情端點；`docs/dca_bot_phase7_worklog_20260826.md` 工作日誌。
  - **測試**: `tests/test_dca_paper_e2e.py` 追加詳情快照 + `tp_replaced` 事件 E2E；`tests/test_dca_http_contract.py` 追加 orders/fills/ladder 讀取 runtime 端點測試；`tests/test_dca_frontend_contract.py` 追加詳情元件檔案級契約（六分頁、端點接線、分頁隔離、零階梯數學、armed 零掛單語意）；`web_ui/frontend/tests/dca.test.ts` 追加 Phase 7 純函式測試。
  - **CI 修復（稽核 7.13 全綠）**: 修正 `Dashboard.tsx`/`GridForm.tsx`/`MarketRankings.tsx` 前既有的 4 項 React Compiler / `set-state-in-effect` lint error（Phase 6 EX-6.1 例外項），`bash scripts/ci.sh` 現 Python + npm test + lint + build 全綠。
  - **預覽環境**: `vite.config.ts` 加 `allowedHosts: true`，讓雲端沙盒/預覽代理主機可連入開發伺服器（僅影響 dev server，生產走 `vite build` 靜態檔）。

- **DCA 機器人 Phase 6 — 建立 DCA 精靈（4 步 + 即時階梯試算）**:
  - **精靈（`DcaForm.tsx`）**: 4 步（General / Entry / DCA / Summary）＋成功卡片；Step 1 自動 market/info 精度確認（404 → 明確「找不到交易對」，未確認不可前進，稽核 6.4）；Step 2 Immediate/Indicator 二選一；Step 3 §4.2-C 全欄位（含止損、循環、雙向 tiebreak）；Step 4 回顧清單＋LIVE `CONFIRM LIVE` 二次確認（文案含交易對、方向、所需資金、最大跌幅，稽核 6.14）；市場資料與 preview 皆 debounce＋`AbortController` 可取消（6.16）。
  - **指標設定（`IndicatorConfigPanel.tsx`）**: RSI/MACD/BB/Price Change 四種獨立啟用，**每個參數皆可編輯**（含 condition 下拉、時框、取樣根數，稽核 6.7/6.8b）；§4.3 凍結預設值預填（6.8）；即時值面板顯示「value（條件 成立✓/不成立✗）」，資料不足顯示「資料不足」而非 0（6.9）；Price Change `lookback_bars` 旁顯示「24 根 × 15m = 6 小時」連動換算（6.8c）。
  - **Summary 面板（`DcaLadderPreview.tsx`）**: 所有數值**直讀** `POST /api/v1/dca/preview` 回應欄位（required_capital / max_drawdown_percent / final_take_profit_price / levels[]…），檔案級零階梯運算（稽核 6.2 grep 0 命中、Python 契約把關）；完整階梯表（基礎單＋20 張安全單）；`direction=both` 同時顯示多空兩組階梯並標示「同一時刻只會執行其中一個方向、required = max 非相加」（6.5）；資金口徑：`available=null` 一律 unknown（本版本無 DCA 專屬餘額端點，EX-6.15），不足時 LIVE 紅警＋禁用送出、PAPER 僅警告（6.15 結構就位）。
  - **純函式（`utils/dca.ts` 擴充）**: `canAdvanceDcaStep`（步驟推進規則，6.13）、`buildDcaPreviewRequest`/`buildDcaCreateRequest`（純映射＋精度帶入，6.4/6.6/6.7）、`validateDcaEntry`/`validateDcaStep3`（前端輔助校驗，後端 422 為最終把關，6.12）、`formatLookbackTime`、`dcaFundsStatus`、`selectDcaSummary`；`§4.3` 凍結預設常數（`DCA_RSI_DEFAULTS` 等）。
  - **API client（`api.ts` 擴充）**: `DcaCreateRequest`/`DcaCreateResponse`/`MarketIndicatorsResponse`/`DcaIndicatorRequest` 型別；`createDca()`；`getMarketIndicators(symbol, exchange, timeframe, limit, signal)`；`previewDca()` 支援 `signal` 取消。
  - **測試**: `web_ui/frontend/tests/dca-form.test.ts`（13 項：§4.3 預設、步驟推進、payload 映射、lookback 換算、資金口徑、Summary 選取）＋ `tests/test_dca_frontend_contract.py` 追加 14 項（Python 鏡像＋檔案級契約：DcaLadderPreview 零運算 token、Summary 直讀 preview 欄位、精靈元件零 grid/dashboard 端點、preview/create 端點接線、GridForm 未被 Phase 6 觸及）。
  - **App 整合（`App.tsx`）**: 「建立 DCA」tab 由占位頁換成 `DcaForm`（成功後一鍵返回 DCA 總覽，6.17）；`App.css` 追加精靈區塊（全用既有 token，零 6-digit hex）。

- **DCA 機器人 Phase 5 — 前端 DCA 分頁（總覽 + 卡片 + 表格）**:
  - **API client 擴充（`web_ui/frontend/src/services/api.ts`）**: `DcaSnapshot`/`DcaBot`/`DcaLadder`/`DcaPosition`/`DcaProfit`/`DcaAggregate`/`DcaActionResponse` 型別與 `getDcaSnapshot()`/`startDca()`/`stopDca()`/`deleteDca()`/`previewDca()`；每次刷新恰好 1 次 `/api/v1/dca/snapshot`（稽核 5.4，無 N+1）；全同源相對 URL（稽核 5.3）。
  - **總覽頁（`DcaDashboard.tsx`）**: LIVE/PAPER 雙 aggregate 不混算、symbol/狀態/關鍵字篩選、卡片/表格切換（`localStorage` 記憶）、10 秒輪詢（`inFlight` guard + `AbortController` + `nextPollDelay` 退避 + 頁面隱藏降頻，稽核 5.5/5.6）、stale 資料橫幅與最後同步時間（稽核 5.10）、每卡獨立 action state（稽核 5.13）。
  - **卡片/表格/徽章（`DcaCard.tsx`/`DcaTable.tsx`/`DcaStatusBadge.tsx`）**: `DCA_STATUS_META` 十狀態（armed/triggering/averaging/completed/cooling/stopping/stopped/failed + running/degraded/starting）中文文案、文字＋顏色雙重編碼（稽核 5.11/5.15）；armed 卡顯示「監控中 · 尚未掛單」且**不顯示**任何掛單數字或止盈價（稽核 5.7）；未知值一律 `—` 絕不 `$0.00`（稽核 5.8）；階梯 `filled / max` 進度條＋下一張安全單價格與距離%；入場區只呈現指標設定（前端零階梯運算）；LIVE/PAPER 文字＋顏色雙重標記（稽核 5.9）；停止/刪除走 `ConfirmDialog`，確認文案含 bot 名稱、mode、與「撤單但不平倉」（稽核 5.12）。
  - **純函式（`utils/dca.ts`）**: `getDcaProgress()`/`getDcaStatusLabel()`/`getDcaStatusDescription()`/`getDcaTriggerDistancePercent()` 等，全部 `undefined`-safe（稽核 5.16）。
  - **App 整合（`App.tsx` + `App.css`）**: tab 型別擴充＋側邊欄「DCA 機器人」＋「建立 DCA」（Phase 6 前占位頁）；網格元件零變更、`git diff` 0 行（稽核 5.18）；DCA 分頁只呼叫 `/api/v1/dca/*` 與共用 `/healthz`、絕不呼叫網格端點，反之亦然（稽核 5.2）；新增 CSS 只用既有 `--*` token（稽核 5.19，新增區塊 6-digit hex = 0）。
  - **測試**: `web_ui/frontend/tests/dca.test.ts`（13 項，node --test）＋ `tests/test_dca_frontend_contract.py`（Python 鏡像：狀態文案/running 集合與後端一致、armed 隱藏掛單唯一、`filled/max` null-safe、距離符號、ROI 不補零、wait 分桶；另斷言 DCA 元件不 import 網格服務、網格元件不含 DCA 端點、api.ts 無硬編碼外部 origin）登錄 `scripts/ci.sh` 與 `docs/github-actions-ci.yml`。

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

### Fixed

- **`GET /api/v1/dca/{bot_id}/chart` 全面 500（C5 遺留，C6 live 驗證發現）**: 端點內以 `from schemas import KlineCandle` 做函式內匯入，該路徑建立的類別與 `response_model` 參照的 `web_ui.backend.schemas.KlineCandle` 為**兩個不同的類別物件**，導致 pydantic 對每一根 K 棒回報 `model_type` 驗證錯誤、整個回應 500。C5 的 HTTP 契約測試以 `TestClient` 在無 token 情境下只斷言 401，故未觸及此路徑。改為由模組頂端統一匯入，並移除同段落中建構後即被丟棄的 `candles` 死碼（誤用 `DcaChartMark` 表示 K 棒）。實機驗證：`curl /chart?timeframe=15m&limit=50` → 200，50 根 K 棒、`data_status=paper`、`source=paper_synthetic`。

- **DCA 熱插拔啟動全面失敗（Phase 5 live 驗證發現）**: `run_multiple_grids.py` 的 `run_single_dca_task()` 誤以 `await auto_fill_dca_precision(...)` 呼叫同步函式（`core/services/dca/config_loader.py`，回傳 `None`），導致**每一筆** DCA 熱插拔啟動都立即崩潰 `object NoneType can't be used in 'await' expression`（Phase 3 稽核只測 standby，paper E2E 又直接呼叫 coordinator，故未觸及）。移除 `await`；新增回歸測試 `tests/test_dca_paper_e2e.py::SupervisorPoolTests::test_run_single_dca_task_reaches_averaging_without_await_none`（真實 `run_single_dca_task` + 真實 PAPER adapter + 真實 YAML：斷言達到 AVERAGING、INV-2 TP 恰 1 張、持倉 > 0）。

### Testing

- 新增 `tests/test_dca_config_loader.py`（稽核 3.1–3.4）、`tests/test_dca_ohlcv_cache.py`（稽核 3.6–3.9/3.11）、`tests/test_dca_paper_e2e.py`（稽核 3.10/3.11/3.19，PAPER 全迴路 armed → 觸發 → 基礎單 → ≥2 安全單 → TP → completed，斷言 INV-1..INV-4）。
- `scripts/ci.sh` 與 `docs/github-actions-ci.yml` 已登錄三個新模組。
- Phase 5 新增 `tests/test_dca_frontend_contract.py`（前後端契約鏡像，8 項）與 `web_ui/frontend/tests/dca.test.ts`（13 項）；全量後端 CI 212 項全綠（含 `run_single_dca_task` 回歸測試），前端 `npm test` 19/19、`tsc -b` 零錯誤。
- Phase 6 新增 `web_ui/frontend/tests/dca-form.test.ts`（13 項）＋ `tests/test_dca_frontend_contract.py` 擴充至 22 項；全量後端 CI **226 項全綠**，前端 `npm test` **32/32**、`tsc -b` 零錯誤、lint 維持既有基線（4 error + 1 warning，零新增）、build 340.86 kB（gzip 95.92）。

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
