# DCA 即時圖表與回測子系統 實作計畫

> 建立日期：2026-08-26
> 分支：`arena/01a03da6-grid-trading-console`（本 session 固定分支）
> 前置：DCA 機器人 Phase 1–7 已完成（commit `62e3ba4`），目前 PAPER 實機測試中
> 範圍：`core/services/dca/backtest/`、`web_ui/backend/`、`web_ui/frontend/`、`docs/`
> 依據標準：`docs/grid_trading_console_audit_standard_zh-TW.md`（P0/P1/P2/P3 與 A–G 檢核表）
> 上游計畫：`docs/dca_bot_implementation_plan_zh-TW.md` §8「明確排除項」第 1–2 列（回測、TradingView 圖表）由本計畫接手
> 決策凍結：D-6 ～ D-11 已於 2026-08-26 由使用者確認（見 §10）

---

## 0. 執行摘要

本計畫在既有 DCA 子系統之上，新增兩個能力，且**不修改任何已凍結的交易決策邏輯**：

1. **即時圖表**：DCA 詳情頁新增「圖表」分頁，呈現 K 線 + 本機器人實際下單/成交/止盈標記 + 階梯/均價/止盈價格線。
2. **回測**：對「既有 DCA 設定」或「建立精靈中尚未送出的參數」跑歷史回放，在同一張圖上標記每一筆虛擬下單與收益，並輸出績效指標與參數掃描比較。

### 0.1 本計畫最重要的兩個可驗證性質

| # | 性質 | 工程定義（可機器證偽） | 稽核位置 |
|---|---|---|---|
| **BP-1** | **回測與實盤走同一套決策程式碼** | 回測路徑必須实例化真正的 `DcaCoordinator` 與真正的 `DcaOrderExecutor`；`grep -rn "class.*Executor" core/services/dca/backtest/` 必須 0 命中（不得存在第二套執行器或第二套階梯數學） | C3 稽核 3.1 / C3 稽核 3.4 |
| **BP-2** | **回測永遠不會下真單** | 回測請求處理期間，交易所寫入方法（`create_order`/`cancel_order`）呼叫次數必須為 0；`config/active_dca.json` 內容不得變動 | C5 稽核 5.2 / 5.3 |

BP-1 是上游計畫 G-4「前端自行計算階梯數學 → 前後端必然分岔」這條 P0 教訓在回測域的延伸：如果回測用另一套模擬器，使用者會依一個與實盤行為不同的數字去決定真金白銀。BP-2 則是把上游計畫 G-8（armed 不得下單）的精神延伸到回測域——回測是一個唯讀功能。

### 0.2 為什麼這個子系統「工作量近乎等同整個 DCA 計畫」

上游計畫 §8 的估算（「回測工作量約等於 Phase 1–3 總和」）在本計畫的盤點後得到證實，原因不是撮合本身，而是三件被低估的事：

1. **無未来函数（lookahead bias）的證明成本**：回測引擎必須能被證明「第 i 棒的決策只看得到 ≤ i 的資料」。這需要前視注入測試（把未來棒改成極端值，斷言決策不變），而非只靠程式碼審查。
2. **K 線資料層是全新的一層**：DCA 目前完全沒有歷史資料儲存（`routes/dca.py` 沒有 history 端點，而網格有 `routes/grids.py:343`）。回測需要數千根 K 棒、需要回填、需要快取、需要權重預算，這是 C1 整個 Phase。
3. **圖表是新的前端技術棧**：既有 `recharts` 沒有 K 線/蠟燭圖、沒有原生縮放平移（`GridDetail.tsx:261-300` 只用 `AreaChart` 畫單一條線）。要畫「K 線 + 十幾種標記 + 多條價格線 + 可縮放」，必須引入 lightweight-charts（決策 D-6）。

因此本計畫切為 **8 個 Phase（C1–C8）**，與上游 7 個 Phase 同量級。

---

## 1. 需求對照表（使用者原始需求 → 規格條目）

| # | 使用者需求 | 規格條目 | 對應 Phase |
|---|---|---|---|
| 1 | 「DCA 策略生成即時圖表」 | `GET /api/v1/dca/{bot_id}/chart`（組合 K 線 + runtime 快照標記）；`DcaDetail.tsx` 新增「圖表」分頁 | C1, C5, C6 |
| 2 | 「以及做回測」 | `POST /api/v1/dca/backtest`（單一設定回放）＋ `POST /api/v1/dca/backtest/sweep`（參數掃描，決策 D-8） | C2–C5, C7 |
| 3 | 「在圖表中顯示下單…標記」 | `marks[]`：`order_placed` / `base_filled` / `safety_filled` / `take_profit_filled` / `stop_loss_filled` / `skipped`，含 shape/color/position/文字（§4.7 凍結表） | C6, C7 |
| 4 | 「…及收益標記」 | 輪次邊界垂直線 + TP 標記文字含本輪實現報酬% + equity 曲線（可切換第二窗格） | C4, C6, C7 |
| 5 | 「在前端」 | 全部數字由 API 提供，前端零計算（延續 G-4）；`DcaBacktestResult.tsx` / `DcaSweepTable.tsx` | C6, C7 |
| 6（隱含） | 回測結果必須可信 | BP-1 同源 + 無未来函数 + 保守同棒順序 + 簡化事項全揭露（§4.3 / §4.5 / D-11） | C2–C5, C7 |

---

## 2. 現況盤點（本計畫必須複用而非重寫的資產）

以下每一列的行號都在本次盤點中實際讀取驗證。

| 既有資產 | 位置（已驗證） | 本計畫複用方式 |
|---|---|---|
| **`DcaCoordinator.step()` 可外部逐步驅動** | `core/services/dca/coordinator/dca_coordinator.py:190` | 回放驅動器逐棒呼叫 `await coord.step()`，**完全不需改狀態機**，也不會觸發 `asyncio.sleep` |
| **`begin()` DRAFT→ARMED 不建背景 task** | 同檔 `:102` | 回放不啟動 `_monitor_loop()`，避免背景 task 汙染回測 |
| **可注入時鐘 `time_fn`** | 同檔 `:57`（`time_fn: Callable[[], float] = time.monotonic`），實際用於 cooldown `:316` / `:320` | 回放注入「當前棒 unix 秒」虛擬時鐘 → `cooldown_seconds` 以棒時間計算 |
| **可注入 `ohlcv_provider`** | `core/services/dca/coordinator/entry_monitor.py:21`（`OhlcvProvider = Callable[[str, str, int], Awaitable[List[float]]]`） | 回放注入「截至第 i 棒的 closes 切片」→ 指標在歷史序列上評估 |
| **`EntryEvaluator(series_fns=...)` 逐棒判定注入點** | `core/services/dca/entry/evaluator.py:26`（`TriggerSeriesFn`）、`:48-49`（`series_fns` 參數）；`core/services/dca/indicators/__init__.py:64`（`indicator_trigger_series`） | 參數掃描時預先計算整段觸發序列，避免每棒重算指標（**須有等價性 P0 測試**，稽核 4.8） |
| **`IDcaOrderExecutor` 狹窄介面** | `core/services/dca/interfaces/dca_executor.py:45` | 回放只需提供一個 duck-typing 交易所，其餘風控閘門由真 executor 執行 |
| **真 `DcaOrderExecutor` 含熔斷/最小名目/quantize/client-id 命名空間** | `core/services/dca/execution/dca_order_executor.py:66-104`（`circuit_probe` 可注入）、`:109 _client_id`、`:137 _check_min_notional` | 把 `ReplayExchange` 餵給**真** executor → 回測同樣受最小名目與 quantize 約束，且 client-id 命名空間可辨識 |
| **既有 `FakeExchange` duck-typing 契約** | `tests/services/dca/fake_exchange.py:23`（`create_order`/`cancel_order`/`get_open_orders`/`get_order`/`get_ticker`） | `ReplayExchange` 沿用同一五方法契約，只把「觸價即成交」換成「依 K 棒 high/low + 無未来函数規則撮合」 |
| **`ladder.py` 純函式（實盤/preview 同源）** | `core/services/dca/ladder.py:144 build_ladder`、`:223 take_profit_price`、`:241 stop_loss_price`、`:256 required_capital` | 回測階梯與實盤同一份程式碼（BP-1 的一部分） |
| **`_fills` 成交歷史（含手續費與累積均價）** | `dca_coordinator.py:581-616`，欄位：`client_order_id/role/side/slot_index/price/quantity/notional/fee/occurred_at/cumulative_avg_price` | 即時圖表標記的**現成資料源**；回測 marks 直接由此投影 |
| **引擎會計值 `_realized_profit` / `_total_fees`** | `dca_coordinator.py:88-89`，於 `build_statistics()` 輸出 | 回測績效指標的 realized/fees **必須取此值**，不得加總顯示層 `_fills.fee`（對齊 `docs/dca_bot_dual_stage_audit_20260826.md` M-1 告誡） |
| **詳情快照已進 runtime payload** | `dca_coordinator.py:785 open_orders_snapshot` / `:808 fills_snapshot` / `:812 ladder_slots_snapshot`；`run_multiple_grids.py:589-591` 發布 | **即時圖表不需要改引擎**：下單/收益標記的資料已在心跳裡 |
| **共享 `OhlcvCache`（防 stampede + TTL）** | `core/services/dca/market_data/ohlcv_cache.py:73`（`timeframe_ttl`）、`OhlcvSnapshot(closes, synthetic, stale, as_of, fetch_limit)` | K 線服務沿用「合成/陳舊」標記語意，回測資料源誠實標示 |
| **PAPER 確定性合成 K 線** | `core/adapters/exchanges/adapters/paper.py:249-286`（seed by symbol、`raw_data={'synthetic': True}`） | PAPER 回測的 K 線來源，同 symbol 兩次完全相同 → 回測可在無網路環境重現 |
| **ccxt 同步抓取模式** | `web_ui/backend/routes/market.py:333 _fetch_ohlcv_sync`、`web_ui/backend/grid_recommendation.py:48`（含 `:USDC` 後綴退除 fallback） | K 線回填沿用同一模式與同一 fallback，不新寫抓取層 |
| **網格回測參考實作** | `web_ui/backend/grid_recommendation.py:81 _simulate_grid_backtest` | **僅取模式不取程式碼**：輕量撮合迴圈 + APR 上限防禦（`min(apr, 99999)`）；DCA 語意（單向階梯 + 單一 TP）完全不同，直接複用會錯 |
| **SQLite 執行期儲存與汰換** | `web_ui/backend/runtime_repository.py:48 _ensure_schema`（`grid_runtime_snapshots` / `grid_runtime_history`，7 天汰換於 `:87`）、`lifecycle.connect_sqlite` | 新增 `dca_klines` 表沿用同一連線helper與全參數化查詢（rule-04） |
| **生命週期純函式（data_status 語意）** | `web_ui/backend/lifecycle.py`（`data_status_from_runtime` / `finite_number`） | K 線與回測端點的 `data_status` 沿用同一套 `live/paper/stale/unavailable` 語意 |
| **稽核日誌** | `web_ui/backend/audit.py` | 新增 action `run_dca_backtest`（誰、何時、對哪個設定跑了回測） |
| **認證 / CORS / 同源 proxy** | `web_ui/backend/auth.py:verify_token`、`web_ui/frontend/vite.config.ts`（`/api` proxy、`allowedHosts: true`） | 全部新端點沿用 `Depends(verify_token)`；前端一律相對路徑 |
| **前端既有分頁與輪詢節奏** | `web_ui/frontend/src/App.tsx:15 AppTab`、`DcaDetail.tsx:43 TabType`（六分頁）、`:169` 手動重整（**目前無自動輪詢**）、`DcaDashboard.tsx:30 REFRESH_MS=15_000` | 圖表分頁輪詢策略必須明示（決策 D-10） |
| **前端設計系統** | `web_ui/frontend/src/App.css`（2538 行 token 化樣式）、`KpiCard` / `DcaStatusBadge` | 圖表面板沿用既有 token，禁止新增平行樣式系統 |
| **前端契約測試模式** | `tests/test_dca_frontend_contract.py`（檔案級 grep 契約）、`web_ui/frontend/tests/dca.test.ts`（node --test 純函式） | 新元件沿用同一雙重契約（Python grep + TS 純函式） |
| **CI 雙登記** | `scripts/ci.sh`（22 個 unittest 模組）＋ `docs/github-actions-ci.yml:35-46` | 每個新測試模組必須**同時**登記兩處 |

### 2.1 已知缺口（必須在對應 Phase 補齊）

| 缺口 | 證據 | 影響 | 補齊 Phase |
|---|---|---|---|
| DCA 無任何歷史/時序端點 | `routes/dca.py` 端點清單無 `history`；對比 `routes/grids.py:343 GET /{grid_id}/history` | 即時圖表無價格資料源 | C1 + C5 |
| 無 K 線持久層 | 全庫無 klines 表；`OhlcvCache` 僅記憶體、TTL ≤ 60s（`ohlcv_cache.py:40 MAX_TTL_SECONDS`） | 回測每次重抓 → Binance 權重與 IP BAN 風險（重現 `docs/gemini_legacy_reports/` 事故的路徑） | C1 |
| `recharts` 無 K 線/蠟燭圖、無原生縮放平移 | `package.json` 依賴僅 `react` / `react-dom` / `lucide-react` / `recharts@^3.8.1`；`GridDetail.tsx:261-300` 僅 `AreaChart` | 無法畫「K 線 + 多標記 + 可縮放」 | C6（決策 D-6） |
| `_fills.occurred_at` 用牆鐘 | `dca_coordinator.py:590`：`datetime.now(timezone.utc).timestamp()` | 回放時標記時間是「跑回測的當下」而非「棒時間」，圖上標記會全部擠在最右側 | C3（驅動器**後置蓋章**，唯顯示層，不改引擎） |
| 實盤路徑 `min_notional` 預設 0 | `run_multiple_grids.py:548`：`DcaOrderExecutor(config, bot_id, adapter)` 未傳 `min_notional` | 回測若擅自注入 min_notional 會與實盤不一致（假精確） | C3（回測預設 0 與實盤一致，UI 必須明示此簡化） |
| 本沙箱缺 Python 依賴（環境缺口，非程式缺陷） | 原 `python3 -m unittest tests.test_dca_ladder` → `ImportError: No module named 'injector'`；隨後逐一暴露 `ccxt`/`yaml`/`fastapi`/`websockets`/`eth_account`/`rich`。全部補齊後 `SKIP_FRONTEND=1 bash scripts/ci.sh` → `Ran 234 tests ... OK` | 無法跑 CI 就無法驗收 | **C0 前置（已完成，見 §5.0）** |

---

## 3. 目標架構

```text
┌─ React Console ────────────────────────────────────────────────────────────┐
│  DcaDetail.tsx  分頁：階梯 │ 訂單 │ 成交 │ 事件 │ 指標 │ 設定 │★圖表 │★回測 │
│    ├─ DcaChart.tsx        lightweight-charts 封裝（K線+標記+價格線+equity） │
│    ├─ DcaBacktestPanel.tsx   期間/時框/初始資金/簡化事項 → 執行回測         │
│    ├─ DcaBacktestResult.tsx  指標卡 + 結果圖（同一 DcaChart）+ 輪次表       │
│    ├─ DcaSweepTable.tsx      參數掃描比較表（排序 / 最優列 / truncated）    │
│    ├─ utils/dcaChart.ts      純函式：candles→series、fills→markers、lines   │
│    └─ services/api.ts        getDcaChart / getKlines / runDcaBacktest /     │
│                              runDcaSweep（同源 /api，相對路徑）             │
└──────────────────────────────┬─────────────────────────────────────────────┘
                               │ Vite proxy → :8001
┌─ FastAPI ────────────────────┴─────────────────────────────────────────────┐
│  routes/market.py   GET /klines/{symbol}          ← KlineService           │
│  routes/dca.py      GET  /{bot_id}/chart          ← klines + runtime 快照  │
│                     POST /backtest                ← BacktestRunner（執行緒）│
│                     POST /backtest/sweep          ← SweepRunner（預算上限）│
│  kline_service.py   快取優先 → 缺棒回填（ccxt threadpool）→ PAPER 合成     │
│  kline_repository.py  dca_klines 表（全參數化 SQL、保留期汰換）            │
│  schemas.py         Kline*/Backtest*/Sweep*/DcaChart* 凍結契約             │
│  audit.py           action: run_dca_backtest                               │
└──────────────────────────────┬─────────────────────────────────────────────┘
                               │ SQLite data/web_ui_runtime.sqlite3
┌─ core/services/dca/backtest/ ┴─────────────────────────────────────────────┐
│  candles.py        棒索引/切片/缺棒檢查（純函式，零 I/O）                  │
│  replay_exchange.py ReplayExchange：依 K棒 high/low 撮合，無未来函数        │
│  replay_driver.py   ★ ReplayDriver：真 DcaCoordinator + 真 DcaOrderExecutor │
│                     注入 time_fn(棒時間) / ohlcv_provider(≤i切片) /         │
│                          circuit_probe(lambda: False)                       │
│  metrics.py         ROI / buy&hold / alpha / 勝率 / MDD / MFE / profit_factor│
│  sweep.py           參數網格展開 + 預算上限 + truncated 誠實回報            │
│  models.py          BacktestRequest/Result/TradeMark/EquityPoint/CycleRecord│
└────────────────────────────────────────────────────────────────────────────┘
        ▲ 唯一進入點：BacktestRunner（threadpool）── 絕不接觸 active_dca.json、
          絕不呼叫真實 adapter 的寫入方法（BP-2）
```

### 3.1 為什麼回測複用 `DcaCoordinator` 而不是另寫快速模擬器（決策 D-7）

**決策：回測 = 真 `DcaCoordinator` + 真 `DcaOrderExecutor` + `ReplayExchange`。**

盤點後確認这条路在技術上幾乎是免費的，因為 Phase 2 的設計已经把注入點全部留好：

| 回放需要 | 既有注入點 | 行號 |
|---|---|---|
| 逐棒推進，不要 sleep | `step()` 已與 `_monitor_loop()` 共用 | `dca_coordinator.py:190` / `:208` |
| 不要背景 task | `begin()` 只轉狀態不建 task | `:102` |
| cooldown 走棒時間 | `time_fn` 可注入 | `:58` / `:316` |
| 指標吃歷史序列 | `ohlcv_provider` 可注入 | `entry_monitor.py:21` |
| 不要讀實盤熔斷 | `circuit_probe` 可注入 | `dca_order_executor.py:66,104` |
| 掃描時不重算指標 | `EntryEvaluator(series_fns=...)` | `evaluator.py:26,48-49` |

代價與緩解：

- **代價 1：速度**。每棒呼叫 `step()`，指標若每棒重算為 O(bars × window)。緩解：掃描模式預先計算整段觸發序列（`indicator_trigger_series`），以 `series_fns` 注入；**必須有等價性 P0 測試**證明預計算與逐棒重算在每一棒的 verdict 完全相同（稽核 4.8）。
- **代價 2：`asyncio` 開銷**。`step()` 是 async，需 `asyncio.run()` 包一層；一個回測一個事件迴圈，掃描時以 threadpool 並行（上限見稽核 4.7）。
- **代價 3：牆鐘時間戳**。`_apply_fill_effects` 的 `occurred_at` 與 `_transition` 的 `updated_at` 用 `datetime.now()`（`:590` / `:705`），不可注入。緩解：驅動器在每棒結束後 diff `fills_snapshot()` 的新增筆數，把**顯示層副本**的 `occurred_at` 蓋章為該棒 unix 秒——不改引擎、不改 `_fills` 原物件（稽核 3.9）。

**保留退路**：若未來需要 tick 級或部分成交撮合，`ReplayExchange` 是唯一需要重寫的檔案，`ReplayDriver` 與 `metrics.py` 不受影響。

> **此主張已實測驗證，非推論**：以 duck-typing 的 `ReplayExchange` + 真 `DcaOrderExecutor` + 真 `DcaCoordinator` 跑通完整循環（`triggering → averaging → completed`，cycles=1、零網路呼叫、INV-1/INV-2 每棒成立、引擎自身的 `window_realigned` 邊界路徑照常觸發）。完整輸出見 §11.4 證據一。

### 3.2 圖表選型與 rule-07 豁免（決策 D-6）

**決策：前端新增 `lightweight-charts`（TradingView 開源，Apache-2.0）。**

rule-07（ponytail）第 4 階要求「已安裝的依賴能解決就用它」。此處 `recharts@3.8.1` **不能**解決，理由具體且可驗證：

| 需求 | recharts 3.8.1 | lightweight-charts |
|---|---|---|
| 蠟燭圖（OHLC） | 無原生 series，需自訂 SVG shape 手刻蠟燭體與影線 | 原生 `addCandlestickSeries()` |
| 買賣標記（十幾種 shape/color/位置） | `ReferenceDot` 逐一 absolutely positioned，大量標記時效能差 | 原生 `series.setMarkers()`（arrowUp/arrowDown/circle/square/cross + aboveBar/belowBar/inBar） |
| 階梯/均價/止盈水平線（動態增刪） | `ReferenceLine` 需重新 render 整棵樹 | 原生 `createPriceLine()` / `removePriceLine()` |
| 縮放平移（數千根 K 棒） | 無原生支援，需自寫 wheel/drag + 視窗化資料 | 原生 `timeScale()`（含 `setVisibleRange`、`fitContent`） |
| 十字光標與 OHLC 讀數 | 需自訂 tooltip 定位 | 原生 crosshair + `subscribeCrosshairMove` |

以 recharts 手刻上述全部，預估程式碼量與長期維護成本**高於**引入一個 45KB gzip 的專用函式庫，且結果仍缺縮放平移。故此為 rule-07「第 4 階不成立 → 第 6 階：最小可行新增」的正當案例。

**豁免條件（寫進計畫，供稽核比對）**：

- E-6.1 只用於 DCA 圖表；**不得**回頭改寫網格頁 `GridDetail.tsx`（避免動到運行中的實盤 UI，延續 G-1 精神）。
- E-6.2 全部圖表邏輯必須封裝在 `DcaChart.tsx` 一個檔案内；其他元件不得 `import` lightweight-charts（檔案級契約測試把關）。
- E-6.3 資料轉換（candles→series、fills→markers）必須落在 `utils/dcaChart.ts` 純函式，可被 node --test 覆蓋。
- E-6.4 記錄 bundle 增量（gzip 前後對照）於 C6 稽核報告。
- E-6.5 `package-lock.json` 必須一併提交（可重現安裝）。

---

## 4. 資料契約規格（凍結）

### 4.1 K 線儲存（`dca_klines` 表）

```sql
CREATE TABLE IF NOT EXISTS dca_klines (
    exchange   TEXT    NOT NULL,
    symbol     TEXT    NOT NULL,
    timeframe  TEXT    NOT NULL,
    ts         INTEGER NOT NULL,          -- 棒起點 unix 秒（UTC，與交易所一致）
    open       TEXT    NOT NULL,          -- TEXT 保存十進位字串，避免 float 精度漂移
    high       TEXT    NOT NULL,
    low        TEXT    NOT NULL,
    close      TEXT    NOT NULL,
    volume     TEXT    NOT NULL,
    synthetic  INTEGER NOT NULL DEFAULT 0,-- 1 = PAPER 合成，絕不得標示為真實
    fetched_at REAL    NOT NULL,
    PRIMARY KEY (exchange, symbol, timeframe, ts)
);
CREATE INDEX IF NOT EXISTS idx_klines_lookup
    ON dca_klines(exchange, symbol, timeframe, ts DESC);
```

規則：

- **K-1** 全部查詢參數化（rule-04）；`grep -n "execute(f\"\|execute(\".*%s\|+ *symbol" web_ui/backend/kline_repository.py` 必須 0 命中。
- **K-2** upsert 冪等：同一 `(exchange, symbol, timeframe, ts)` 重複寫入不得產生重複列，且不得把 `synthetic=1` 的列升級為 `synthetic=0`（合成資料不得汙染真實資料；反之真實資料可覆寫合成）。
- **K-3** 價格以 TEXT 存十進位字串，讀出時轉 `Decimal`（延續上游計畫「float 僅限 API/UI 邊界」）。
- **K-4** 保留期：預設 180 天（可 env 調整），`prune()` 於每次回填後執行；`DELETE ... WHERE fetched_at < ?`。
- **K-5** 遷移流程（rule-04）：備份 `data/web_ui_runtime.sqlite3` → dry run（`EXPLAIN QUERY PLAN`）→ execute → verify（`PRAGMA integrity_check` + 列數比對）。此表為**新增**，不改既有兩表 schema，故為零風險遷移。

### 4.2 K 線服務（`KlineService`）

```text
get_candles(exchange, symbol, timeframe, limit, until=None)
  → KlineResult(candles, source, data_status, gaps, oldest_ts)

source ∈ {"cache", "exchange", "paper_synthetic", "cache+exchange"}
data_status ∈ {"live", "paper", "stale", "unavailable"}   # 沿用 lifecycle 語意
```

規則：

- **S-1 快取優先**：先查 `dca_klines`；缺棒才回填（gap detection 以 `timeframe` 秒數等距檢查，缺棒數 > 0 才打 API）。
- **S-2 分頁回填**：Binance klines 單次上限 1500 根；回填以 `since` 向前分頁，單次請求 `limit ≤ 1500`。
- **S-3 權重預算**：單次 `get_candles` 最多 `KLINE_MAX_FETCH_PAGES`（預設 8）次底層請求；超過則回傳部分資料 + `data_status="stale"` + `warnings=["僅回填部分歷史"]`，**不得**靜默截斷。
- **S-4 PAPER**：`account_mode=paper` 或 exchange 無真實憑證時，走 `PaperExchangeAdapter.get_ohlcv()`（`paper.py:249`）→ `source="paper_synthetic"`、`synthetic=1`。UI 必須顯示「合成資料」徽章。
- **S-5 例外處理**：底層拋錯（含 429）→ 回傳既有快取 + `data_status="stale"`；無快取 → `data_status="unavailable"` 且 `candles=[]`。**禁止**回傳 HTTP 200 + 空陣列 + `data_status="live"`（那是說謊）。
- **S-6 執行緒**：ccxt 同步呼叫一律 `run_in_executor` / `run_in_threadpool`（沿用 `routes/market.py:322` 模式），不得阻塞事件迴圈。

### 4.3 回放撮合規則（凍結 — 本計畫可信度的核心）

`ReplayExchange` 必須實作以下規則，逐條皆有專屬測試。**任何未在此列出的撮合行為都必須在實作時補進本表，不得私自決定。**

| # | 規則 | 定義 | 理由 |
|---|---|---|---|
| **R-1** | **無未来函数（訂單可見性）** | 在棒 `i` 的 `step()` 中送出的訂單記 `placed_bar=i`；撮合只對棒 `j > i` 進行 | 棒 `i` 收線後才決策、才送單，實盤不可能在收線的同一瞬間以該棒價格成交。基礎單尤其關鍵：`_step_triggering` 以 `last_price()` 下**限價單**（`dca_coordinator.py:251-257`），回放中 `last_price()` = 棒 `i` 收盤，而 `low_i ≤ close_i` 恆成立 → 允許同棒成交等於「基礎單必定秒成交」。**實測後果**：跳空路徑下同棒撮合憑空創造部位並回報 realized 0.2004，實盤則為 0 成交（§11.4 證據二）。注意影響為路徑依賴：單調路徑上差異可為 0，故稽核 2.1 以結構斷言而非報酬差異判定 |
| **R-2** | 限價**買**單成交 | 條件 `low_j ≤ price`；成交價 `= min(open_j, price)` | 跳空向下穿越時實際以更優的開盤價成交 |
| **R-3** | 限價**賣**單成交 | 條件 `high_j ≥ price`；成交價 `= max(open_j, price)` | 跳空向上穿越時實際以更優的開盤價成交 |
| **R-4** | **同棒多單順序（保守原則）** | 同一根棒內先結算「對當前部位不利」的單：做多 → 先安全單買進、後止盈賣出；做空相反。同類多張時，做多安全單由高價到低價依序成交 | 避免「同一根棒既攤平又止盈」高估獲利。此為本計畫最重要的保守性假設 |
| **R-5** | 市價單（止損） | 於觸發棒以 `open_j` 成交；若 `open_j` 已穿越觸發價，以 `open_j`（更差價）成交 = 內建跳空滑價 | 止損在實盤必然有滑價；以開盤價成交是標準保守近似 |
| **R-6** | **不模擬部分成交** | 整張成交（v1 簡化，決策 D-11） | 部分成交需要深度/成交量模型，屬 tick 級回測範圍（§8 排除）。**必須在 API `warnings` 與 UI 同時揭露** |
| **R-7** | 手續費 | 一律沿用引擎既有路徑 `config.fee_rate`（`dca_coordinator.py:580` 計費、`:603` 累加 `_total_fees`）；回測**不得**另算一套 | 避免雙重扣費與口徑分歧（對齊 dual-stage 稽核 M-1） |
| **R-8** | 熔斷 | `circuit_probe` 必須注入 `lambda: False` | 否則回測結果會隨實盤熔斷狀態漂移，同一設定今天明天跑出不同結果 |
| **R-9** | 時鐘 | `time_fn` 注入「當前棒 unix 秒」 | `cooldown_seconds`（`dca_coordinator.py:316/320`）以棒時間計算，否則 300 秒冷卻在毫秒級回放中永遠不成立 → 循環輪數被嚴重高估 |
| **R-10** | 指標資料邊界 | 第 `i` 棒的 `ohlcv_provider` 只回傳 `closes[max(0, i-fetch_limit+1) : i+1]`，且 `last_bar_closed=True` | 與 `EntryMonitor.evaluate()`（`entry_monitor.py:35-64`）語意一致 |
| **R-11** | 確定性 | 同 config + 同 bars → 兩次執行輸出（含浮點序列化後字串）完全相同 | 可重現是回測的最低門檻 |
| **R-12** | 缺棒 | 時間軸以實際取得的棒為準，**不得插值補齊**；`equity` 點數 = 棒數；缺棒數回报於 `warnings` | 插值會製造不存在的價格路徑 |
| **R-13** | client-id 命名空間 | `ReplayExchange` 紀錄的 client-id 必須可辨識為回測（沿用真 executor 的 `_client_id()` 即可，因回測永不觸及真實交易所） | 若未來誤接真實 adapter，命名空間可作為最後一道辨識 |

### 4.4 `ReplayDriver` 輸出契約

```python
@dataclass(frozen=True)
class TradeMark:
    ts: int                    # 棒 unix 秒（R-1 後置蓋章）
    price: float               # 成交/掛單價
    kind: str                  # order_placed|base_filled|safety_filled|
                               # take_profit_filled|stop_loss_filled|skipped
    role: str                  # base|safety|take_profit|stop_loss
    side: str                  # buy|sell
    quantity: Optional[float]  # 未知 → None（G-5，禁止 0）
    notional: Optional[float]
    slot_index: Optional[int]
    cycle_index: int           # 第幾輪（1-based）
    realized_pct: Optional[float]  # 僅 TP/SL：本輪實現報酬%（收益標記）

@dataclass(frozen=True)
class EquityPoint:
    ts: int
    equity: float              # initial_capital + realized + unrealized − fees
    position_quantity: float
    average_cost: Optional[float]   # 無部位 → None（G-5）
    status: str                # 該棒結束時的 DcaStatus.value

@dataclass(frozen=True)
class CycleRecord:
    index: int
    direction: str
    entry_ts: Optional[int]
    exit_ts: Optional[int]
    entry_price: Optional[float]
    average_cost: Optional[float]
    exit_price: Optional[float]
    safety_filled: int
    exit_reason: str           # take_profit|stop_loss|max_cycles|end_of_data|running
    realized_pnl: Optional[float]
    realized_pct: Optional[float]
    mfe_pct: Optional[float]   # 最大有利偏移
    mae_pct: Optional[float]   # 最大不利偏移
    bars: int

@dataclass(frozen=True)
class BacktestResult:
    bars: int
    timeframe: str
    first_ts: Optional[int]
    last_ts: Optional[int]
    metrics: dict              # §4.5，未知值一律 None
    equity: List[EquityPoint]
    marks: List[TradeMark]
    cycles: List[CycleRecord]
    data_status: str           # live|paper|stale|unavailable
    source: str                # cache|exchange|paper_synthetic
    warnings: List[str]        # 簡化事項揭露（D-11），不得為空
    bars_per_second: float     # 效能基準（掃描預算依據）
```

### 4.5 績效指標定義（凍結）

| 指標 | 定義 | 注意 |
|---|---|---|
| `initial_capital` | 請求指定；預設 = `required_capital(config, entry_price)`（`ladder.py:256`） | 必須 > 0，否則 400 |
| `final_equity` | `initial_capital + realized − fees + unrealized_at_end` | unrealized 以最後一根收盤評價 |
| `total_return_pct` | `(final_equity − initial_capital) / initial_capital × 100` | |
| **`buy_hold_return_pct`** | `(close_last − close_first) / close_first × 100`；做空顯示反向基準並註記 | **P0**：DCA 若跑不贏買入持有，必須讓使用者一眼看到 |
| `alpha_pct` | `total_return_pct − buy_hold_return_pct` | |
| `realized_pnl` / `total_fees` | **取自引擎會計**（`build_statistics()` 的 `realized_profit` / `total_fees`） | **禁止**加總 `_fills[].fee`（顯示層，dual-stage M-1） |
| `cycles_completed` | `state.cycle_count`（`dca_state.py:176`，`COMPLETED` 時 +1） | |
| `win_rate_pct` | 以 cycle 為單位：`exit_reason=take_profit` 為勝；`stop_loss`/`max_cycles`/`end_of_data` 為負；分母 0 → `None` | 不得以「筆」為單位（一輪含多張安全單） |
| `max_drawdown_pct` | equity 曲線「歷史峰值 → 其後谷底」最大跌幅% | 不得用首尾兩點 |
| `exposure_pct` | 持倉棒數 / 總棒數 × 100 | |
| `avg_cycle_bars` | 各輪 `bars` 平均；無輪次 → `None` | |
| `mfe_pct` / `mae_pct` | 各輪 MFE/MAE 平均 | |
| `profit_factor` | 獲利輪總盈餘 / 虧損輪總虧損；分母 0 → **`None`（禁止 0，G-5）** | |
| `bars_per_second` | 實測吞吐 | 掃描預算與 C3 稽核 3.11 依據 |

### 4.6 API 契約（新增端點）

| 方法 | 路徑 | 用途 | 關鍵回應欄位 |
|---|---|---|---|
| GET | `/api/v1/market/klines/{symbol}` | K 線（圖表與回測共用） | `exchange, timeframe, candles[{t,o,h,l,c,v}], source, data_status, gaps, warnings` |
| GET | `/api/v1/dca/{bot_id}/chart` | 即時圖表組裝 | `candles[], marks[], lines[{kind,price}], current_price, data_status, warnings` |
| POST | `/api/v1/dca/backtest` | 單一設定回放 | `result: BacktestResult`（§4.4） |
| POST | `/api/v1/dca/backtest/sweep` | 參數掃描 | `runs[{params, metrics}], best_by, truncated, budget{bars_used, bars_limit, runs_limit}` |

規則：

- **A-1** 全部端點 `Depends(verify_token)`；無 token → 401。
- **A-2** `lines[].kind ∈ {entry, average_cost, take_profit, next_safety, stop_loss}`；`price=null` 時前端不得畫在 0（G-5）。
- **A-3** `POST /backtest` 接受兩種來源：`{"bot_id": "dca-xxx"}`（讀既有 YAML）或 `{"draft": {...}}`（精靈 JSON，沿用 `dca_yaml_generator.py` 的同一套驗證）。兩者都不得寫檔。
- **A-4** 上限守衛：`bars ≤ 20000`、`timeframe ∈ {1m,5m,15m,30m,1h,4h,1d}`（對齊 `ohlcv_cache.py:30 TIMEFRAME_SECONDS`）、請求體 ≤ 64KB。超過 → 400 + 中文訊息。
- **A-5** 執行時間守衛：單次回測 wall-clock 上限（預設 60s），逾時 → 202/408 語意明確的錯誤回應 + `data_status="unavailable"`，不得回傳半套結果當作成功。
- **A-6** `warnings` 必含（不得為空）：`"回測不模擬部分成交"`、`"未套用交易所最小名目限制"`（若 min_notional=0）、`"歷史績效不代表未來"`；PAPER/合成資料另加 `"使用合成 K 線，非真實歷史"`。
- **A-7** 稽核日誌：每次回測寫一筆 `action="run_dca_backtest"`，含 bot_id 或 draft 指紋、bars、timeframe、耗時。
- **A-8** 前端型別全部集中在 `services/api.ts`，元件不得自行宣告 API 形狀（延續既有契約測試）。

### 4.7 圖表標記語意（凍結）

| kind | shape | color token | position | 文字 |
|---|---|---|---|---|
| `base_filled` | `arrowUp`（做多）/ `arrowDown`（做空） | `--dca-long` / `--dca-short` | belowBar / aboveBar | `B @ 價格` |
| `safety_filled` | `circle` | 藍（做多）/ 橘（做空） | belowBar / aboveBar | `S{slot}` |
| `take_profit_filled` | `arrowUp` | 綠 | belowBar | `TP +x.xx%` |
| `stop_loss_filled` | `arrowDown` | 紅 | aboveBar | `SL −x.xx%` |
| `order_placed`（僅即時圖，未成交在世掛單） | `square`（空心） | 灰 | inBar | — |
| `skipped`（急跌穿透，`SlotStatus.SKIPPED`） | `cross` | 橘 | inBar | `略過` |
| 輪次邊界 | 垂直虛線（`createPriceLine` 不適用 → 用 `timeScale` 標記或獨立 series） | 灰 | — | `第 n 輪` |

價格線（`lines[]`）：

| kind | 樣式 | 說明 |
|---|---|---|
| `entry` | 灰虛線 | 本輪基礎單成交價；armed/cooling 為 `null`（不得畫 0） |
| `average_cost` | 藍實線 | 攤平均價；無部位為 `null` |
| `take_profit` | 綠虛線 | 當前 TP 價 |
| `next_safety` | 橘虛線 | 下一張將成交的安全單價（`dca_statistics.py:39`） |
| `stop_loss` | 紅虛線 | 未設定為 `null` |

---

## 5. 分期執行計畫

> **通用規則（每個 Phase 都適用，沿用上游計畫 §5）**
> - 開工前：`git status` 乾淨（除既有 `graphify-out/` 髒檔）。
> - 收工前：`bash scripts/ci.sh` 全綠；若環境缺件，至少 `SKIP_FRONTEND=1 python3 -m unittest <新模組>` 綠燈並註明原因。
> - 新測試模組必須**同時**登記 `scripts/ci.sh` 與 `docs/github-actions-ci.yml`（現況：`ci.sh:9-29`、`github-actions-ci.yml:35-46`）。
> - 收工前依 `AGENTS.md`「Service Restart & Live Verification」：動到 `core/`、`web_ui/`、`run_*.py`、`config/` 必須列出重啟指令並做 live 驗證（rule-05）。
> - 收工時更新 `CHANGELOG.md` 的 `[Unreleased]`；**不得**改 `main`（rule-08）。
> - 每個 Phase 產出五段式報告（範圍與風險 / 檢核結果 / 測試證據 / 例外接受 / 部署回滾），存為 `docs/dca_chart_backtest_phase{N}_worklog_YYYYMMDD.md`。
> - 每個 Phase 結束執行 `graphify update .`（AGENTS.md）。

### 5.0 C0 — 前置環境與基線（非 Phase，已於 2026-08-26 實際執行完成）

本沙箱盤點時實測：`python3 -m unittest tests.test_dca_ladder` 原為 `ImportError: No module named 'injector'`。逐一補齊後全綠。**實際需要的安裝序列**（沙箱為 PEP 668 externally-managed，需 `--break-system-packages`；正式環境用 `requirements.txt`）：

```bash
pip install --break-system-packages injector pyyaml ccxt fastapi uvicorn httpx websockets eth_account rich
cd web_ui/frontend && npm ci        # 沙箱目前無 node_modules
```

**實測基線（C0 完成後，全部實際執行）**：

```text
$ SKIP_FRONTEND=1 bash scripts/ci.sh
Ran 234 tests in 11.728s
OK                       # exit code 0

$ cd web_ui/frontend && npm ci && npm test && npm run lint && npm run build
# tests 36   # pass 36   # fail 0
lint : ✖ 1 problem (0 errors, 1 warning)     ← MarketRankings exhaustive-deps（既有 EX-7.1）
build: ✓ built in 697ms
  dist/index.html                       0.47 kB │ gzip:   0.30 kB
  dist/assets/index-BATfRx3W.css       44.14 kB │ gzip:   8.16 kB
  dist/assets/GridDetail-BxJsLF7h.js  347.97 kB │ gzip: 102.20 kB
  dist/assets/index-BW98R3KP.js       361.01 kB │ gzip: 101.58 kB
```

234 與 36 分別與 `docs/dca_bot_phase7_worklog_20260826.md` §3 記錄的「backend 234」「前端 36 項」一致 → 分支基線確認綠燈，本計畫的「零回歸」判定以此為準。

**bundle 基線（C6 稽核 6.10 的對照基準）**：`index-*.js` gzip **101.58 kB**、`GridDetail-*.js` gzip **102.20 kB**、CSS gzip **8.16 kB**。C6 引入 lightweight-charts 後必須回報增量；`GridDetail` chunk 為獨立 chunk（動態匯入），故 DCA 圖表新依賴不應影響網格頁載入——C6 需確認 `DcaChart` 同樣落在獨立 chunk 而非灌進 `index-*.js`。

**注意事項（供後續 Phase 引用）**：

- 沙箱裝到的 `ccxt` 為 **4.5.75**，而 `requirements.txt:25` 鎖定 `ccxt==4.4.30`。C1 的 K 線回填若觸及 ccxt 版本相關行為差異，必須以 `requirements.txt` 的版本為準重測，並在報告中註明沙箱版本。
- 前端段（`npm test` / `npm run lint` / `npm run build`）在沙箱需先 `npm ci`；C6/C7 之前必须先完成，否則前端稽核項無法執行。
- 若某 Phase 無法跑前端段，報告必須依上游計畫 §5 通用規則註明 skip 原因，不得靜默略過。

**C0 通過條件**：`SKIP_FRONTEND=1 bash scripts/ci.sh` 退出碼 0（已達成）；基線數字 234 記錄於此。

---

### Phase C1 — K 線歷史資料層（SQLite 快取 + 回填 + 汰換）

**目標**：讓「取一段歷史 K 線」變成一個可靠、可快取、權重受控、誠實回報的操作。此 Phase **不碰前端、不碰回測**。

**交付物**

```
web_ui/backend/kline_repository.py       # dca_klines schema / upsert / range / gaps / prune（全參數化）
web_ui/backend/kline_service.py          # 快取優先 + 分頁回填 + PAPER 合成 + source/data_status
web_ui/backend/schemas.py                # +KlineCandle / KlinesResponse（改檔）
web_ui/backend/routes/market.py          # +GET /klines/{symbol:path}（改檔）
tests/test_dca_kline_repository.py       # unittest 風格
scripts/ci.sh                            # 登記（改檔）
docs/github-actions-ci.yml               # 登記（改檔）
```

**實作要點**

- 價格以 TEXT 存十進位字串（K-3）；讀出轉 `Decimal`，序列化到 API 邊界才轉 `float`。
- 回填以 `timeframe` 秒數做等距 gap detection；只在缺棒時打 API（S-1）。
- 沿用 `grid_recommendation._fetch_ohlcv_sync` 的 symbol 正規化與 `:USDC` fallback，**不要**重寫第二套。
- PAPER 走 `PaperExchangeAdapter.get_ohlcv()`（`paper.py:249`）→ 確定性、`synthetic=1`。
- 所有例外路徑回 `data_status="stale"/"unavailable"`，絕不 200+空+live（S-5）。

**驗收指令**

```bash
python3 -m unittest tests.test_dca_kline_repository -v
python3 -c "import sys; sys.path.insert(0,'web_ui/backend'); import kline_repository, kline_service; print('import ok')"
grep -nE 'execute\(f"|execute\(".*%|\+ *symbol' web_ui/backend/kline_repository.py   # 必須 0 命中
git diff --stat core/services/grid/ run_grid_trading.py                              # 必須為空
```

#### Phase C1 稽核標準

| # | 檢核項 | 等級 | 通過條件（可證偽） |
|---|---|---|---|
| 1.1 | 參數化查詢 | P0 | 上述 grep 0 命中；所有 `execute()` 皆用 `?` 佔位 |
| 1.2 | upsert 冪等 | P0 | 同 key 寫入 3 次後列數 = 1，且值為最後一次 |
| 1.3 | 合成不得汙染真實 | P0 | 先寫 `synthetic=1` 再寫 `synthetic=0` → 結果 0；反向寫入（真→合成）→ 仍為 0（真實不被降級） |
| 1.4 | 缺棒偵測 | P0 | 給定等距 10 棒移除中間 2 棒，`gaps()` 回傳缺 2；無缺棒時回 0 |
| 1.5 | 回填只在缺棒時打 API | P0 | 以 mock fetch 計數：完整快取下 fetch 呼叫次數 = 0 |
| 1.6 | 分頁上限 | P0 | 請求 20000 根 1m 且全 miss → 底層呼叫次數 ≤ `KLINE_MAX_FETCH_PAGES`，且 `warnings` 非空 |
| 1.7 | PAPER 確定性 | P0 | 同 symbol 同 timeframe 兩次 `get_candles` → candles 逐值相同，`source="paper_synthetic"` |
| 1.8 | 例外誠實 | P0 | mock fetch 拋 `429` → 有快取時 `data_status="stale"` 且回傳快取；無快取時 `"unavailable"` 且 `candles=[]`；**不得**出現 `"live"` |
| 1.9 | 保留期汰換 | P1 | `prune()` 後 `fetched_at` 超過 180 天的列為 0；未過期列數不變 |
| 1.10 | 不阻塞事件迴圈 | P1 | `routes/market.py` 新端點以 threadpool 執行同步抓取（grep `run_in_threadpool`/`run_in_executor` ≥ 1） |
| 1.11 | 遷移流程 | P1 | 報告中附備份指令、`PRAGMA integrity_check` 輸出、既有兩表列數遷移前後相同 |
| 1.12 | 網格零回歸 | P0 | `git diff --stat core/services/grid/ run_grid_trading.py` 為空；`tests.test_dashboard_snapshot tests.test_web_ui_http_contract` 全綠 |
| 1.13 | CI 雙登記 | P1 | `scripts/ci.sh` 與 `docs/github-actions-ci.yml` 皆含 `tests.test_dca_kline_repository` |

**退出條件**：13 項全 pass，或未 pass 項全為 P2/P3 且記錄於例外接受表。

---

### Phase C2 — `ReplayExchange` 撮合器（凍結規則 R-1 ～ R-13）

**目標**：把「依 K 棒撮合」這件事做成一個純核心、零 I/O、可獨立鎖定向量的元件。此 Phase **不接 Coordinator**，以直接呼叫五個 duck-typing 方法測試。

**交付物**

```
core/services/dca/backtest/__init__.py
core/services/dca/backtest/candles.py          # Candle dataclass / 切片 / 等距檢查（純函式）
core/services/dca/backtest/replay_exchange.py  # ReplayExchange（create/cancel/get_open_orders/get_order/get_ticker + advance(bar)）
tests/test_dca_replay_exchange.py
scripts/ci.sh                                  # 登記（改檔）
docs/github-actions-ci.yml                     # 登記（改檔）
```

**實作要點**

- 介面 = `FakeExchange`（`tests/services/dca/fake_exchange.py:23`）的五個方法 + 一個回放專屬 `advance(bar_index)`（推進一根棒並結算該棒成交）。
- `create_order()` **不立即成交**，只登記 `{placed_bar: current_bar, ...}`（R-1）。
- `advance()` 內依 R-4 排序後依 R-2/R-3 判定，成交價依跳空規則。
- `get_ticker()` 回傳**當前棒收盤**（供 `last_price()`，即基礎單的限價基準）。
- 全程 `Decimal`；只有回傳給 API 的邊界才轉 `float`。
- 零 I/O、零 `asyncio.sleep`、零 `datetime.now()`。

**驗收指令**

```bash
python3 -m unittest tests.test_dca_replay_exchange -v
grep -rnE "open\(|sqlite3|requests|aiohttp|asyncio\.sleep|datetime\.now" core/services/dca/backtest/   # 0 命中
python3 -c "from core.services.dca.backtest.replay_exchange import ReplayExchange; print('ok')"
```

#### Phase C2 稽核標準

| # | 檢核項 | 等級 | 通過條件 |
|---|---|---|---|
| 2.1 | **R-1 無未来函数** | P0 | 棒 i 送出的限價單，即使棒 i 的 low/high 已穿越，**不得**在棒 i 成交；棒 i+1 才成交 |
| 2.2 | R-2 買單向量 | P0 | `price=100`、棒 `low=99` → 成交於 100；`low=101` → 不成交 |
| 2.3 | R-3 賣單向量 | P0 | `price=110`、棒 `high=111` → 成交於 110；`high=109` → 不成交 |
| 2.4 | 跳空成交價 | P0 | 買單 `price=100`、棒 `open=95` → 成交價 95（非 100）；賣單 `price=110`、`open=115` → 成交價 115 |
| 2.5 | **R-4 同棒保守順序** | P0 | 做多、同一棒同時可成交「安全單買 100」與「TP 賣 105」→ 必須先結算安全單；斷言成交序列順序 |
| 2.6 | R-4 同類排序 | P1 | 做多三張安全單（100/98/96）同棒皆可成交 → 依 100→98→96 順序 |
| 2.7 | R-5 市價單跳空 | P0 | 止損觸發棒 `open` 已穿越 → 以 `open` 成交（更差價），非以觸發價 |
| 2.8 | duck-typing 契約完整 | P0 | 五方法齊備且簽名與 `FakeExchange` 相容（以 `DcaOrderExecutor` 實接不拋 `AttributeError`） |
| 2.9 | 撤單語意 | P0 | `cancel_order` 後該單不再參與後續棒撮合；撤已成交單回傳 False |
| 2.10 | 零 I/O | P0 | 上述 grep 0 命中 |
| 2.11 | Decimal 純度 | P1 | `grep -n "float(" core/services/dca/backtest/replay_exchange.py` 在計算路徑 0 命中 |
| 2.12 | R-6 不部分成交且文件明示 | P1 | 模組 docstring 含「不模擬部分成交」；成交後 `filled_quantity == quantity` |
| 2.13 | 網格零回歸 | P0 | `git diff --stat core/services/grid/ run_grid_trading.py` 為空 |

---

### Phase C3 — `ReplayDriver`：接真 Coordinator + 真 Executor（確定性 + INV 守衛）

**目標**：證明 BP-1。把 C2 的撮合器接到**真的** `DcaCoordinator` 與**真的** `DcaOrderExecutor`，跑出一段完整的 `armed → triggering → averaging → completed → cooling → armed` 循環，並輸出 §4.4 的 marks / equity / cycles。

**交付物**

```
core/services/dca/backtest/models.py        # BacktestRequest/Result/TradeMark/EquityPoint/CycleRecord
core/services/dca/backtest/replay_driver.py # ReplayDriver.run(config, candles, initial_capital) -> BacktestResult
tests/test_dca_replay_driver.py
scripts/ci.sh                               # 登記（改檔）
docs/github-actions-ci.yml                  # 登記（改檔）
```

**實作要點（逐條對應注入點）**

```python
# 虛擬時鐘：回傳「當前棒 unix 秒」（R-9）
clock = lambda: candles[driver.i].ts
# 指標資料邊界：只給 ≤ i 的 closes（R-10）
async def provider(symbol, tf, limit, _i=None):
    return [float(c.close) for c in candles[max(0, i - limit + 1): i + 1]]
# 熔斷必須固定關閉（R-8）
executor = DcaOrderExecutor(config, bot_id, replay_exchange,
                            min_notional=Decimal("0"),          # 與 run_multiple_grids.py:548 一致
                            circuit_probe=lambda: False)
coord = DcaCoordinator(config, bot_id, executor,
                       ohlcv_provider=provider, time_fn=clock)
coord.begin()                                    # DRAFT→ARMED，不建背景 task
for i, bar in enumerate(candles):
    replay_exchange.set_bar(i)
    await coord.step()                           # 決策（只看 ≤ i）
    replay_exchange.advance(i + 1)               # 撮合（只看 i+1，R-1）
    driver.collect(i)                            # 收 equity / diff fills → 蓋章 ts
```

- **標記時間蓋章（R-1 / §3.1 代價 3）**：每棒結束 diff `fills_snapshot()` 長度，把新增列**複製**後改 `occurred_at` 為該棒 `ts`；不得修改 `_fills` 原物件。
- **INV 守衛**：驅動器在每棒結束後斷言 INV-1/2/6（見稽核 3.5–3.7）；違反即拋錯並終止回測（回測寧可失敗也不給錯數字）。
- **`warnings`**：固定包含 D-11 三條簡化揭露 + 缺棒數 + 合成資料標示。

**驗收指令**

```bash
python3 -m unittest tests.test_dca_replay_driver -v
# BP-1 反證：backtest 目錄不得存在第二套執行器或階梯數學
grep -rn "class .*Executor\|def build_ladder\|def take_profit_price" core/services/dca/backtest/   # 0 命中
# BP-2 反證：backtest 不得觸及交易所寫入或 active_dca
grep -rn "active_dca\|create_order(" core/services/dca/backtest/ | grep -v replay_exchange   # 0 命中
```

#### Phase C3 稽核標準

| # | 檢核項 | 等級 | 通過條件 |
|---|---|---|---|
| 3.1 | **BP-1 同源** | P0 | `ReplayDriver` 實例化 `DcaCoordinator` 與 `DcaOrderExecutor`（測試以 `isinstance` 斷言）；上述 grep 0 命中 |
| 3.2 | **R-8 熔斷固定** | P0 | 測試先開啟實盤熔斷狀態（設 `BinanceRest._adapter_circuit_until` 為未來），回測結果仍與未開啟時完全相同 |
| 3.3 | R-9 棒時間時鐘 | P0 | `cooldown_seconds=300`、timeframe=1h：止盈後下一輪必須在 ≥ 1 根棒後才 armed；若誤用牆鐘則會立即重進（測試斷言兩者差異） |
| 3.4 | **R-10 無未来函数（前視注入）** | P0 | 把第 `i+1..n` 棒全部改成極端值（+50%），第 `i` 棒的入場決策與標記不得改變 |
| 3.5 | INV-1 armed 零掛單 | P0 | 全回放過程中，任一棒結束時若 `status==armed` 則 `replay_exchange` 在世掛單數 = 0 |
| 3.6 | INV-2 averaging 恰好 1 張 TP | P0 | 任一棒結束時若 `status==averaging` 且持倉 > 0，則 role=take_profit 的在世掛單恰好 1 |
| 3.7 | INV-6 單方向互斥 | P0 | 任一棒不得同時存在 long 與 short 的在世掛單；`active_direction` 於 armed/cooling 為 `None` |
| 3.8 | R-11 確定性 | P0 | 同輸入執行兩次，`json.dumps(result, sort_keys=True)` 字串完全相同 |
| 3.9 | 標記時間為棒時間 | P0 | 所有 `marks[].ts` ∈ candles 的 ts 集合；且**不存在**任何 mark 的 ts 等於回測執行當下牆鐘 |
| 3.10 | 不改引擎狀態 | P0 | 蓋章後 `coord.fills_snapshot()` 的 `occurred_at` 仍為原牆鐘值（證明只改副本） |
| 3.11 | 效能基準 | P1 | 3000 根 15m 棒、20 張安全單、RSI 入場：完成時間記錄於報告；`bars_per_second` 回報於 result |
| 3.12 | 完整循環覆蓋 | P0 | 測試資料必須實際走過 `armed→triggering→averaging→completed→cooling→armed` 至少 1 次（斷言 `cycles_completed ≥ 1`） |
| 3.13 | end_of_data 誠實 | P0 | 資料結束時仍持倉 → `exit_reason="end_of_data"`，`unrealized` 以最後收盤評價，**不得**隱匿為已實現 |
| 3.14 | 網格零回歸 | P0 | `git diff --stat core/services/grid/ run_grid_trading.py` 為空；`tests.test_dca_coordinator tests.test_dca_paper_e2e` 全綠 |

---

### Phase C4 — 績效指標與參數掃描（純函式）

**目標**：把 `BacktestResult` 變成可決策的數字（含 buy&hold 對照），並提供有預算上限的參數掃描。

**交付物**

```
core/services/dca/backtest/metrics.py   # compute_metrics(result, initial_capital, candles) -> dict
core/services/dca/backtest/sweep.py     # expand_grid() / run_sweep()（預算上限 + truncated）
tests/test_dca_backtest_metrics.py
tests/test_dca_backtest_sweep.py
scripts/ci.sh                           # 登記（改檔）
docs/github-actions-ci.yml              # 登記（改檔）
```

**實作要點**

- 指標計算為純函式，輸入 `BacktestResult` + candles，輸出 §4.5 全部欄位；未知一律 `None`。
- `realized_pnl` / `total_fees` **必須**取自 `build_statistics()` 的引擎會計值（不得加總 `_fills[].fee`）。
- 掃描加速：對固定 `entry` 設定，預先以 `indicator_trigger_series()`（`indicators/__init__.py:64`）算出整段逐棒布林序列，經 `EntryEvaluator(series_fns=...)` 注入，避免每棒重算指標。**必須有等價性測試**（稽核 4.8）。
- 掃描以 `ThreadPoolExecutor` 並行，上限 `SWEEP_MAX_WORKERS`（預設 4）；總棒數預算 `SWEEP_MAX_TOTAL_BARS`（預設 300000）；超預算 → 停止並回 `truncated=true` + 已完成 runs。

**驗收指令**

```bash
python3 -m unittest tests.test_dca_backtest_metrics tests.test_dca_backtest_sweep -v
```

#### Phase C4 稽核標準

| # | 檢核項 | 等級 | 通過條件 |
|---|---|---|---|
| 4.1 | 指標向量鎖定 | P0 | 一個手工可驗算的小型 case（3 輪、已知 realized/fees）→ `total_return_pct`/`win_rate_pct`/`max_drawdown_pct` 與手算相符，容差 `1e-6` |
| 4.2 | **buy&hold 基準存在** | P0 | `buy_hold_return_pct` 與 `alpha_pct` 必存在；做多/做空語意各 1 個測試 |
| 4.3 | profit_factor 分母 0 | P0 | 無虧損輪時回傳 `None`（**不是 0**、不是 `inf`） |
| 4.4 | MDD 定義 | P0 | equity `[100,120,90,110]` → MDD = 25.0%（120→90），非首尾差 |
| 4.5 | 會計口徑 | P0 | `realized_pnl` 等於引擎 `build_statistics().profit.realized`；測試刻意讓 `_fills[].fee` 加總與 `_total_fees` 不同，斷言採用後者 |
| 4.6 | 未知值不補零 | P0 | 無任何輪次時 `win_rate_pct`/`avg_cycle_bars`/`mfe_pct` 皆為 `None`（G-5） |
| 4.7 | 掃描並行上限 | P1 | `SWEEP_MAX_WORKERS=4` 時同時執行中的 replay ≤ 4（以計數器斷言） |
| 4.8 | **預計算等價性** | P0 | 同一 config 與 bars：「每棒重算指標」與「預先計算觸發序列」兩種路徑，**每一棒**的 `should_enter` verdict 完全相同；且最終 `BacktestResult` 逐欄位相同 |
| 4.9 | 預算與 truncated | P0 | 設定小預算 → 部分 runs 完成、`truncated=true`、`budget.bars_used ≤ bars_limit`；**不得**回報未完成 run 的指標 |
| 4.10 | 非法參數組合 | P1 | 掃描範圍含 `max_safety_orders=0` 等非法值 → 該 run 標記 `error` 並附中文原因，整體不崩潰 |
| 4.11 | 網格零回歸 | P0 | `git diff --stat core/services/grid/ run_grid_trading.py` 為空 |

---

### Phase C5 — 後端 API 層（klines / chart / backtest / sweep）

**目標**：把 C1–C4 的能力包成四個受認證、受守衛、誠實回報的端點，並鎖死前端契約。

**交付物**

```
web_ui/backend/schemas.py            # +KlineCandle/KlinesResponse/DcaChartResponse/BacktestRequest/BacktestResponse/SweepRequest/SweepResponse（改檔）
web_ui/backend/routes/market.py      # 接 KlineService（改檔）
web_ui/backend/routes/dca.py         # +GET /{bot_id}/chart、+POST /backtest、+POST /backtest/sweep（改檔）
web_ui/backend/dca_yaml_generator.py # 抽出「draft JSON → DcaConfig」共用驗證（回測不落檔）（改檔）
web_ui/backend/audit.py              # action: run_dca_backtest（改檔）
tests/test_dca_backtest_http_contract.py
scripts/ci.sh                        # 登記（改檔）
docs/github-actions-ci.yml           # 登記（改檔）
```

**實作要點**

- `GET /{bot_id}/chart` = `KlineService.get_candles()` + 既有 runtime 快照（`fills` / `ladder_slots` / `open_orders`，見 `run_multiple_grids.py:589-591`）投影為 `marks[]` + `lines[]`。**引擎零變更**。
- 回測以 `run_in_threadpool` 執行（`asyncio.run()` 在執行緒內），並套 A-5 wall-clock 上限。
- 上限守衛（A-4）在 pydantic 模型層就做，避免進到運算才發現。
- `draft` 來源必須走與建立機器人**同一套**驗證函式，否則回測會接受實盤不接受的參數（假精確）。

**驗收指令**

```bash
python3 -m unittest tests.test_dca_backtest_http_contract -v
# BP-2 live 反證：跑一次回測，確認 active_dca.json 未變、交易所寫入為 0
sha256sum config/active_dca.json   # 回測前後比對
```

#### Phase C5 稽核標準

| # | 檢核項 | 等級 | 通過條件 |
|---|---|---|---|
| 5.1 | 認證 | P0 | 四個端點無 token → 401；有 token → 200 |
| 5.2 | **BP-2 零交易所寫入** | P0 | 回測端點測試以 mock exchange 計數 `create_order`/`cancel_order` = 0 |
| 5.3 | **BP-2 零副作用** | P0 | 回測前後 `config/active_dca.json` 位元組相同；`config/dca/` 無新檔 |
| 5.4 | 上限守衛 | P0 | `bars=20001` → 400 中文訊息；`timeframe="2m"` → 400；請求體 > 64KB → 413/400 |
| 5.5 | 逾時守衛 | P1 | mock 一個超慢回測 → 回明確逾時錯誤，不回傳半套結果 |
| 5.6 | data_status 誠實 | P0 | 合成資料 → `"paper"`；抓取失敗有快取 → `"stale"`；無資料 → `"unavailable"` 且 `candles=[]`；三者皆不得為 `"live"` |
| 5.7 | warnings 揭露 | P0 | 任何回測回應 `warnings` 含 §A-6 三條；合成資料另含合成標示 |
| 5.8 | 契約測試 | P0 | `test_dca_backtest_http_contract.py` 覆蓋前端所需全部欄位（逐一斷言 key 存在與型別） |
| 5.9 | 前端零計算契约 | P1 | Python 端 grep 契約：`DcaChart.tsx`/`DcaBacktestResult.tsx` 不得含階梯或指標數學（延续 `test_dca_frontend_contract.py` 模式） |
| 5.10 | 稽核日誌 | P1 | 跑一次回測 → audit 表新增一筆 `run_dca_backtest` |
| 5.11 | 不阻塞事件迴圈 | P1 | 回測以 threadpool 執行（grep 證據）；同時 `/api/v1/dca/snapshot` 仍可在回測中回應 |
| 5.12 | LIVE 閘門不受影響 | P0 | `GRID_LIVE_TRADING_ENABLED` 關閉時回測仍可用（回測與 live 無關），但回應必含「歷史模擬」標示 |
| 5.13 | 網格零回歸 | P0 | `git diff --stat core/services/grid/ run_grid_trading.py` 為空；`tests.test_web_ui_http_contract tests.test_dca_http_contract` 全綠 |

---

### Phase C6 — 前端圖表基礎（lightweight-charts 封裝 + 即時圖表）

**目標**：DCA 詳情頁新增「圖表」分頁，看到 K 線 + 本機器人實際下單/成交/止盈標記 + 階梯/均價/止盈價格線。

**交付物**

```
web_ui/frontend/package.json                       # +lightweight-charts（決策 D-6 / E-6.5 lock 一併提交）（改檔）
web_ui/frontend/src/components/DcaChart.tsx         # 唯一允許 import lightweight-charts 的檔案
web_ui/frontend/src/utils/dcaChart.ts               # 純函式：candles→series、fills→markers、lines→priceLines
web_ui/frontend/src/services/api.ts                 # +KlineCandle/DcaChartResponse 型別 + getKlines/getDcaChart（改檔）
web_ui/frontend/src/components/DcaDetail.tsx        # TabType +'chart'，新分頁（改檔）
web_ui/frontend/src/App.css                         # 圖表面板樣式（沿用既有 token）（改檔）
web_ui/frontend/tests/dca-chart.test.ts
web_ui/frontend/package.json                        # test script 登記新測試（改檔）
```

**實作要點**

- `DcaChart` 為受控元件：props = `{candles, marks, lines, equity?, height, liveLastPrice?}`；**不做任何資料抓取**。
- **即時最後一根**（決策 D-10）：已收線 K 棒來自 `/chart`；最後一根以既有 `stats.current_price` 在客戶端更新 `close`，**不新增 WS、不新增輪詢端點**。
- 輪詢：圖表分頁啟用時 15s（對齊 `DcaDashboard.tsx:30 REFRESH_MS`），切離分頁/元件卸載即清除 timer 並 `abortRef.current?.abort()`。
- **記憶體**：`useEffect` cleanup 必須 `chart.remove()`；price line 以 ref 保存並在 props 變動時 `removePriceLine()` 後重建。
- 深色主題沿用 `App.css` 既有 token（背景/網格/文字色），不得硬編碼新色票。
- `armed` 狀態時圖上顯示「監控中 · 交易所零掛單」提示條，且不得顯示任何 `order_placed` 標記（此時本應為空）。

**驗收指令**

```bash
cd web_ui/frontend && npm ci && npm test && npm run lint && npm run build
grep -rn "lightweight-charts" src/ --include=*.tsx --include=*.ts   # 只能命中 DcaChart.tsx
```

#### Phase C6 稽核標準

| # | 檢核項 | 等級 | 通過條件 |
|---|---|---|---|
| 6.1 | 標記語意一致 | P0 | `dcaChart.ts` 的 kind→shape/color/position 對照表與 §4.7 逐行相符（TS 測試斷言） |
| 6.2 | 前端零計算 | P0 | `DcaChart.tsx`/`dcaChart.ts` 不含階梯、均價、ROI 數學（grep 契約，延续 G-4） |
| 6.3 | 未知不畫 0 | P0 | `lines[].price=null` 時不產生 price line；`marks[].quantity=null` 時文字省略數量（不得顯示 0） |
| 6.4 | 記憶體回收 | P0 | 元件卸載呼叫 `chart.remove()`（測試或程式碼審查證據）；切換分頁 20 次後 DOM 中 canvas 數不增長 |
| 6.5 | 輪詢與取消 | P1 | 切離分頁後無新增請求（AbortController + clearInterval 證據） |
| 6.6 | armed 語意 | P0 | `status=armed` → 顯示監控中提示，且 `order_placed` 標記數 = 0 |
| 6.7 | 依賴隔離 | P0 | 上述 grep 只命中 `DcaChart.tsx`；網格元件檔零變更（`git diff --stat web_ui/frontend/src/components/GridDetail.tsx` 為空，E-6.1） |
| 6.8 | 資料不足誠實 | P0 | `data_status=unavailable` → 顯示「資料不足／無法取得 K 線」，不得顯示空白圖表讓人誤以為無波動 |
| 6.9 | 合成資料標示 | P0 | `source=paper_synthetic` → 圖上角標「合成資料」 |
| 6.10 | bundle 增量與 chunk 隔離 | P1 | 報告記錄 `npm run build` 前後 gzip 對照（基線見 §5.0：index gzip 101.58 kB）；lightweight-charts 必須落在**獨立 chunk**（如 `GridDetail-*.js` 的既有模式），不得灌進 `index-*.js` 拖慢首屏 |
| 6.11 | 建置與 lint | P0 | `npm run build` 0 error；`npm run lint` 不新增 error（既有 1 warning 可留） |
| 6.12 | RWD / 可及性 | P2 | 375px 寬可用；圖表容器有 `aria-label`；鍵盤可切換分頁 |

---

### Phase C7 — 前端回測工作台（回測面板 + 結果 + 掃描比較）

**目標**：使用者能對「既有機器人」或「精靈中尚未送出的參數」跑回測與參數掃描，並在同一張圖上看到虛擬下單與收益標記。

**交付物**

```
web_ui/frontend/src/components/DcaBacktestPanel.tsx    # 期間/時框/初始資金/執行（含簡化事項揭露）
web_ui/frontend/src/components/DcaBacktestResult.tsx   # 指標卡 + 結果圖（複用 DcaChart）+ 輪次表
web_ui/frontend/src/components/DcaSweepTable.tsx       # 掃描比較表（排序/最優列/truncated 提示）
web_ui/frontend/src/utils/dca.ts                       # +回測純函式（期間選項、指標格式化）（改檔）
web_ui/frontend/src/components/DcaDetail.tsx           # TabType +'backtest'（改檔）
web_ui/frontend/src/components/DcaForm.tsx             # Summary 步驟加「先回測看看」（改檔）
web_ui/frontend/src/services/api.ts                    # +runDcaBacktest/runDcaSweep 型別與方法（改檔）
web_ui/frontend/tests/dca-backtest.test.ts
web_ui/frontend/package.json                           # test script 登記（改檔）
```

**實作要點**

- 全部數字直讀 API（G-4）；`utils/dca.ts` 只做格式化（小數位、%、時間），不做金融計算。
- **簡化事項必須顯眼**：面板底部常駐列出 §A-6 的 `warnings`（不部分成交 / 未套用最小名目 / 歷史不代表未來 / 合成資料）。
- **buy&hold 對照必須顯示**：指標卡中 `total_return_pct` 與 `buy_hold_return_pct` 並排，`alpha_pct` 為負時以警示色呈現。
- 掃描表：以 `best_by` 排序，最優列高亮；`truncated=true` 時頂部明確顯示「因預算限制僅完成 N/M 組」。
- 長表格分頁（沿用 `DcaDetail.tsx` 的 `PAGE_SIZE=50` 模式）。
- 執行中 loading + 可取消（AbortController）；錯誤（429 / 資料不足 / 逾時）以 `notice-error` 呈現，絕不顯示為正常。

**驗收指令**

```bash
cd web_ui/frontend && npm test && npm run lint && npm run build
grep -rn "required_capital\|price_deviation\|take_profit_price" src/components/DcaBacktest*.tsx src/components/DcaSweepTable.tsx   # 0 命中（零計算）
```

#### Phase C7 稽核標準

| # | 檢核項 | 等級 | 通過條件 |
|---|---|---|---|
| 7.1 | 零本地計算 | P0 | 上述 grep 0 命中；所有數值來自 API 欄位 |
| 7.2 | 簡化揭露 | P0 | 回測結果頁必定渲染 `warnings` 全部條目（測試斷言條目數 = API 回傳數） |
| 7.3 | buy&hold 對照 | P0 | 結果頁同時存在 `total_return_pct` 與 `buy_hold_return_pct` 兩個顯示元素；缺任一即失敗 |
| 7.4 | truncated 誠實 | P0 | `truncated=true` → 顯示已完成/總組數；不得顯示「最佳參數」為全域最佳（文案需含「已完成範圍內」） |
| 7.5 | 執行中與取消 | P1 | 執行中按鈕禁用 + spinner；切換分頁會 abort |
| 7.6 | 錯誤路徑 | P0 | 400 / 408 / 429 / `data_status=unavailable` 四種皆有對應中文訊息，且不渲染空結果圖 |
| 7.7 | 未知值不補零 | P0 | `metrics.profit_factor=null` → 顯示「—」而非 0 |
| 7.8 | 長列表 | P1 | 輪次表 > 50 筆時分頁（沿用既有模式） |
| 7.9 | 精靈整合 | P1 | Summary 的「先回測看看」送出的是**表單當前參數**（draft），不是已建立的 bot |
| 7.10 | 建置與 lint | P0 | `npm run build` 0 error；lint 不新增 error |

---

### Phase C8 — 端到端驗收、文件與稽核報告

**目標**：PAPER 實機跑通全流程，產出使用者文件與五段式稽核報告。

**交付物**

```
tests/test_dca_chart_backtest_e2e.py                 # PAPER：K線→回測→標記→掃描 全链路
docs/dca_chart_backtest_user_guide_zh-TW.md          # 使用者手冊
docs/dca_chart_backtest_audit_report_zh-TW.md        # 五段式最終稽核
docs/dca_bot_user_guide_zh-TW.md                     # 補「圖表與回測」章節（改檔）
config/dca/README.md                                 # 補新端點（改檔）
README.md                                            # 補功能說明（改檔）
CHANGELOG.md                                         # [Unreleased] 逐 Phase 補齊（改檔）
scripts/ci.sh + docs/github-actions-ci.yml           # 登記 E2E（改檔）
```

#### Phase C8 稽核標準

| # | 檢核項 | 等級 | 通過條件 |
|---|---|---|---|
| 8.1 | PAPER 全链路 | P0 | 建立 DCA → armed → `/chart`（合成 K 線 + 標記結構正確）→ `/backtest`（≥1 輪完成）→ `/sweep`（≥2 組完成）全部 200 且欄位齊備 |
| 8.2 | 實機 live 驗證 | P0 | 依 `AGENTS.md`：重啟 backend（`bash start_web_ui.sh`）與引擎（`pm2 restart multi-grid-engine`），對**執行中實例** curl 四個端點並附回應 |
| 8.3 | 網格零回歸 | P0 | `git diff --stat core/services/grid/ run_grid_trading.py web_ui/frontend/src/components/GridDetail.tsx` 為空 |
| 8.4 | LIVE 維持關閉 | P0 | `GRID_LIVE_TRADING_ENABLED` 未開啟；`.env` 無變更 |
| 8.5 | 憑證掃描 | P0 | `git diff` grep `API_KEY\|SECRET\|TOKEN\|PRIVATE` 僅命中 `.gitignore` 與 CSS token 註解 |
| 8.6 | CI 全綠 | P0 | `bash scripts/ci.sh` 退出碼 0，測試數 ≥ C0 基線 + 本計畫新增 |
| 8.7 | 五段式報告 | P1 | 範圍與風險 / 檢核結果 / 測試證據 / 例外接受 / 部署回滾 五段齊備 |
| 8.8 | 文件可操作 | P1 | 使用者手冊含：如何看圖表標記、回測的六條簡化事項、buy&hold 怎麼讀、掃描怎麼用 |

---

## 6. 跨 Phase 的全域規則

### 6.1 絕對禁止事項（延續上游 G-1 ～ G-10，新增 G-11 ～ G-18）

| # | 禁止事項 | 理由 |
|---|---|---|
| G-1 | 修改 `core/services/grid/**` 的任何行為 | 網格是運行中的實盤系統 |
| G-2 | 修改 `run_grid_trading.py` | 同上 |
| G-3 | 在 `requirements.txt` 新增 Python 依賴 | rule-07；本計畫後端全部 stdlib + 既有依賴 |
| G-4 | 前端自行計算階梯/指標/回測數學 | 前後端必然分岔（P0） |
| G-5 | 以 `0` 表示未知數值 | 稽核標準 A 節硬性規定 |
| G-6 | 用 `print()` 做 runtime 輸出 | rule-03 |
| G-7 | 字串拼接 SQL | rule-04 |
| G-8 | 在 `armed` 狀態下送出任何交易所寫入請求 | INV-1 |
| G-9 | 開啟 `GRID_LIVE_TRADING_ENABLED` | 全案在 PAPER 下驗收 |
| G-10 | 直接 commit 到 `main` | rule-08；本 session 固定 `arena/01a03da6-grid-trading-console` |
| **G-11** | 回測路徑呼叫真實 adapter 的 `create_order`/`cancel_order` | BP-2；回測是唯讀功能 |
| **G-12** | 回測寫入 `config/active_dca.json` 或 `config/dca/*.yaml` | BP-2；回測不得產生可被熱插拔監控器拾取的副作用 |
| **G-13** | 在 `core/services/dca/backtest/` 重寫階梯/均價/止盈/指標數學 | BP-1；必須 import `ladder.py` 與 `indicators/` |
| **G-14** | 回測讀取實盤熔斷狀態（`BinanceRest._adapter_circuit_until` 等） | R-8；結果會隨實盤狀態漂移 |
| **G-15** | 在回放中使用 `datetime.now()` 決定任何決策或標記時間 | R-1/R-9；決策只能用棒資料，標記只能用棒時間 |
| **G-16** | 把 `synthetic=1` 的 K 線標示為真實歷史 | S-4；合成資料誤認是上游計畫明列的風險 |
| **G-17** | 在 `DcaChart.tsx` 以外的檔案 import lightweight-charts | E-6.2；隔離新依賴的擴散 |
| **G-18** | 改寫網格頁圖表（`GridDetail.tsx`）為 lightweight-charts | E-6.1；不得動運行中的實盤 UI |

### 6.2 每個 Phase 的收尾檢查清單

```
[ ] 該 Phase 稽核表逐項 pass / fail / N/A，fail 項有例外接受紀錄
[ ] 新測試已同時加入 scripts/ci.sh 與 docs/github-actions-ci.yml
[ ] bash scripts/ci.sh 綠燈（或註明環境缺件與 skip 原因）
[ ] 既有測試零回歸（對比 C0 基線數字）
[ ] git diff --stat 檢查無意外檔案；grid 核心與 GridDetail.tsx 零變更
[ ] CHANGELOG.md [Unreleased] 已更新
[ ] 若動到 core/ web_ui/ run_*.py config/ → 明示重啟指令並做 live 驗證（AGENTS.md / rule-05）
[ ] graphify update .
[ ] 產出五段式報告 docs/dca_chart_backtest_phase{N}_worklog_YYYYMMDD.md
[ ] 提交至 arena/01a03da6-grid-trading-console
```

---

## 7. 風險登錄簿

| ID | 風險 | 等級 | Phase | 緩解措施 | 驗證方式 |
|---|---|---|---|---|---|
| RC-1 | **未来函数**導致回測報酬虛高，使用者據此加碼實盤 | P0 | C2/C3 | R-1 訂單次棒才撮合 + R-10 資料邊界 + 前視注入測試。**實測**：跳空路徑下同棒撮合憑空創造部位並回報 realized 0.2004，而實盤為 0 成交（§11.4 證據二） | 稽核 2.1 / 3.4 |
| RC-2 | 回測與實盤行為分岔（第二套邏輯） | P0 | C3 | 強制真 `DcaCoordinator` + 真 `DcaOrderExecutor`；grep 契約禁止第二套數學 | 稽核 3.1、G-13 |
| RC-3 | K 線回填放大 API 權重 → IP BAN（重現 `docs/gemini_legacy_reports/` 事故） | P0 | C1 | 快取優先 + 分頁上限 + 權重預算 + 缺棒才抓 | 稽核 1.5 / 1.6 |
| RC-4 | 回測意外下真單或汙染熱插拔 | P0 | C5 | BP-2：mock 計數 + `active_dca.json` 位元組比對 | 稽核 5.2 / 5.3 |
| RC-5 | 回測結果隨實盤熔斷狀態漂移（同設定不同結果） | P0 | C3 | R-8 注入 `lambda: False` | 稽核 3.2 |
| RC-6 | 同棒既攤平又止盈 → 高估獲利 | P0 | C2 | R-4 保守順序 | 稽核 2.5 |
| RC-7 | cooldown 用牆鐘 → 循環輪數暴增 | P0 | C3 | R-9 棒時間時鐘 | 稽核 3.3 |
| RC-8 | 未揭露簡化（不部分成交/無滑價模型）→ 使用者誤信精度 | P0 | C5/C7 | A-6 warnings 強制非空 + UI 常駐揭露 | 稽核 5.7 / 7.2 |
| RC-9 | 只顯示 DCA ROI、不顯示 buy&hold → 誤判策略有效性 | P0 | C4/C7 | buy&hold + alpha 為必要指標與必要顯示元素 | 稽核 4.2 / 7.3 |
| RC-10 | 參數掃描打爆 CPU / 請求堆積 | P1 | C4/C5 | 並行上限 + 棒數預算 + truncated 誠實回報 | 稽核 4.7 / 4.9 |
| RC-11 | 掃描預計算指標與逐棒重算不等價 → 掃描結果與單次回測矛盾 | P0 | C4 | 等價性測試（每棒 verdict 相同 + 結果逐欄位相同） | 稽核 4.8 |
| RC-12 | 圖表記憶體洩漏（chart 未 remove） | P1 | C6 | cleanup 必須 `chart.remove()`；切換 20 次 canvas 數不增 | 稽核 6.4 |
| RC-13 | 新依賴（lightweight-charts）擴散到其他頁面 | P2 | C6 | E-6.2 + grep 契約 + G-17 | 稽核 6.7 |
| RC-14 | K 線表膨脹撐爆磁碟 | P1 | C1 | 180 天保留期 + 每次回填後 prune | 稽核 1.9 |
| RC-15 | 回測長任務阻塞事件迴圈 → 儀表板卡死 | P1 | C5 | threadpool + wall-clock 上限 | 稽核 5.11 |
| RC-16 | 沙箱缺依賴導致「未驗證即宣稱完成」 | P1 | C0 | C0 前置安裝 + 基線記錄；報告必附實際輸出 | §5.0 |

---

## 8. 明確排除項（本計畫 v1 不做）

| 項目 | 理由 | 後續 |
|---|---|---|
| 部分成交 / 訂單簿深度撮合 | 需要 volume/depth 模型與大量 tick 資料；R-6 已明列簡化 | v2（需另建 tick 儲存） |
| 滑價模型（除跳空外） | 需要成交機率和流動性假設，易淪為假精確 | v2 |
| tick / 逐筆成交級回測 | 資料量與撮合複雜度為另一個量級 | v2 |
| Walk-forward / 樣本內外切分 / 蒙地卡羅 | 使用者選擇 D-8 為「單一回放 + 參數掃描」 | v2（若需要，C4 的 sweep 已是前置） |
| 多交易所 / 跨所套利回測 | 超出 DCA 範圍 | 另案 |
| TradingView 官方 widget 嵌入 | 授權與外部依賴評估 | 不做（已用開源 lightweight-charts 達成同等能力） |
| 即時 WS K 線推送 | D-10：以「已收線 K 棒 + snapshot 補最後一根」達成，成本遠低 | 若有需求再評估 |
| 回測結果持久化 / 排行榜 / 策略市場 | 需要新的儲存與比較模型 | v2 |
| AI 參數推薦 | 需要先累積足量回測樣本 | v2 |
| 改寫網格頁圖表 | E-6.1 / G-18：不得動運行中的實盤 UI | 不做 |

---

## 9. 工作量校準（為何切成 8 個 Phase）

沿用上游計畫 §9 的約束：單一 Phase ≤ ~1200 行有效程式碼、≤ ~12 個檔案、留出預算跑測試與稽核。

| Phase | 新檔 | 改檔 | 預估新增 LOC | 主要成本 | 對話量評估 |
|---|---:|---:|---:|---|---|
| C0 環境與基線 | 0 | 0 | 0 | 安裝依賴、記錄基線 | 與 C1 同次 |
| C1 K 線資料層 | 2 | 5 | ~700（含 ~300 測試） | schema + 回填 + 權重預算 + 誠實回報 | 1 次 |
| C2 ReplayExchange | 3 | 2 | ~800（含 ~400 測試） | 13 條撮合規則逐條鎖定向量 | 1 次（飽和） |
| C3 ReplayDriver | 2 | 2 | ~900（含 ~450 測試） | 接真引擎 + 前視注入 + INV 守衛 + 確定性 | 1 次（飽和，可能需第 2 次收尾） |
| C4 指標 + 掃描 | 2 | 2 | ~850（含 ~400 測試） | 指標向量 + 預計算等價性 + 預算 | 1 次 |
| C5 後端 API | 1 | 5 | ~950（含 ~350 測試） | 4 端點 + 守衛 + 契約 + BP-2 反證 | 1 次（飽和） |
| C6 圖表基礎 | 3 | 4 | ~900（含 ~200 測試） | lightweight-charts 封裝 + 記憶體 + 主題 | 1 次 |
| C7 回測工作台 | 3 | 4 | ~950（含 ~200 測試） | 三個新元件 + 揭露 + 掃描表 | 1 次（飽和） |
| C8 E2E + 文件 | 3 | 5 | ~700（含 ~250 測試） | 實機驗證 + 三份文件 | 1 次（可能需 2 次） |

**總計**：約 19 個新檔、~6750 行新程式碼（其中 ~2550 行測試），9–11 次對話。與上游 DCA 主案（49 檔 / ~6300 行 / 7–9 次）同量級——這驗證了最初「工作量近乎等同整個 DCA 機器人計畫」的判斷。

**建議節奏**：

- **第一組（資料與核心，無 UI）**：C1 → C2 → C3 → C4。完成後可用 `python3 -c` 直接對一台既有 DCA 設定跑回測並在終端印出指標，**此時就已經可以開始驗證策略是否合理**，不必等 UI。
- **第二組（橋接）**：C5。
- **第三組（UI）**：C6 → C7 → C8。

每組結束做一次整體回歸（`bash scripts/ci.sh` + `git diff --stat core/services/grid/`）。

---

## 10. 決策紀錄（2026-08-26 由使用者確認，全部凍結）

| # | 問題 | **確認結果** | 已落實於 |
|---|---|---|---|
| **D-6** | 圖表技術選型 | **新增 `lightweight-charts`**（TradingView 開源，Apache-2.0）。理由：K 線、markers、price lines、縮放平移、十字光標皆為原生；recharts 3.8.1 需手刻 SVG 且無縮放平移 | §3.2、豁免條件 E-6.1～E-6.5、G-17/G-18、Phase C6 |
| **D-7** | 回測撮合引擎 | **複用 `DcaCoordinator` + 真 `DcaOrderExecutor` + `ReplayExchange`**。理由：Phase 2 已留好全部注入點（`step()` / `begin()` / `time_fn` / `ohlcv_provider` / `circuit_probe` / `series_fns`），零邏輯分岔 | §3.1、BP-1、G-13、Phase C3 |
| **D-8** | 回測範圍 | **單一設定回放 + 參數掃描**（不含 walk-forward / 樣本內外切分） | §4.4/§4.6（`/backtest` + `/backtest/sweep`）、Phase C4/C7、§8 排除項 |
| **D-9** | K 線資料儲存 | **SQLite `dca_klines` 快取表 + 回填 + 180 天保留期**（沿用 `data/web_ui_runtime.sqlite3`） | §4.1、§4.2、Phase C1 |
| **D-10** | 即時圖表資料源 | **已收線 K 棒來自新 `/klines` 端點；最後一根以既有 snapshot 的 `current_price` 在客戶端補畫；不新增 WS** | §3 架構圖、Phase C6 實作要點 |
| **D-11** | 撮合簡化事項 | **整張成交（不部分成交）、除跳空外無滑價模型、手續費用 `config.fee_rate`**；三者必須在 API `warnings` 與 UI 同時揭露 | R-6/R-7、A-6、稽核 5.7/7.2 |

### 10.1 D-7 對實作的具體影響

因為複用既有引擎，以下項目**不需要做**：

- ❌ 不需要：第二套階梯計算（直接用 `ladder.py`）
- ❌ 不需要：第二套指標計算（直接用 `indicators/`）
- ❌ 不需要：第二套狀態機（`armed/triggering/averaging/completed/cooling` 全部現成）
- ❌ 不需要：第二套風控閘門（最小名目、quantize、client-id 命名空間由真 executor 執行）
- ❌ 不需要：修改 `DcaCoordinator` 任何一行（注入點已足夠；唯一「不夠」的 `datetime.now()` 以顯示層後置蓋章繞過）

**需要做的**只剩三件：`ReplayExchange`（撮合）、`ReplayDriver`（驅動與收集）、`metrics/sweep`（績效）。這是本計畫能把 8 個 Phase 控制在與主案同量級的根本原因。

---

## 11. 附錄：可直接使用的驗證腳本

### 11.1 前視注入測試骨架（C3 稽核 3.4）

```python
# tests/test_dca_replay_driver.py 片段
async def test_no_lookahead_future_bars_do_not_change_decision(self):
    base = make_candles(200, seed=7)          # 確定性合成棒
    r1 = await ReplayDriver(config, base, initial_capital="1000").run()
    poisoned = list(base[:100]) + [c._replace(
        open=c.open * 1.5, high=c.high * 1.5, low=c.low * 1.5, close=c.close * 1.5
    ) for c in base[100:]]
    r2 = await ReplayDriver(config, poisoned, initial_capital="1000").run()
    # 第 100 棒之前的所有標記必須完全相同
    self.assertEqual([m for m in r1.marks if m.ts <= base[99].ts],
                     [m for m in r2.marks if m.ts <= base[99].ts])
```

### 11.2 BP-1 契約檢查（每個 Phase 收尾都要跑）

```bash
# backtest 目錄不得存在第二套執行器 / 階梯 / 指標 / TP 數學
grep -rnE "class .*Executor|def build_ladder|def take_profit_price|def evaluate_rsi|def compute_macd" \
     core/services/dca/backtest/ && echo "❌ 違反 G-13" || echo "✅ BP-1 契約通過"

# backtest 不得觸及熱插拔檔或真實下單
grep -rn "active_dca\|active_grids" core/services/dca/backtest/ web_ui/backend/routes/dca.py \
     | grep -v "^.*#" && echo "❌ 違反 G-12" || echo "✅ BP-2 契約通過"

# 前端零計算
grep -rn "required_capital\|price_deviation_percent\|take_profit_price" \
     web_ui/frontend/src/components/Dca*.tsx web_ui/frontend/src/utils/dcaChart.ts \
     && echo "❌ 違反 G-4" || echo "✅ 前端零計算通過"

# 新依賴隔離
test "$(grep -rl 'lightweight-charts' web_ui/frontend/src/ | wc -l)" -eq 1 \
     && echo "✅ E-6.2 隔離通過" || echo "❌ 違反 G-17"

# 網格零回歸
git diff --stat core/services/grid/ run_grid_trading.py web_ui/frontend/src/components/GridDetail.tsx
```

### 11.3 終端快速回測（C4 完成後即可用，不需等 UI）

```bash
python3 - <<'PY'
import asyncio, json, sys
sys.path.insert(0, 'web_ui/backend')
from core.services.dca.config_loader import load_dca_config
import yaml
cfg = load_dca_config(yaml.safe_load(open('config/dca/example-dca-paper-eth.yaml')))
from core.services.dca.backtest.replay_driver import ReplayDriver
from kline_service import KlineService
candles = asyncio.run(KlineService().get_candles(cfg.exchange, cfg.symbol, '15m', 2000)).candles
res = asyncio.run(ReplayDriver(cfg, candles, initial_capital='1000').run())
print(json.dumps(res.metrics, indent=2, ensure_ascii=False))
print("marks:", len(res.marks), "cycles:", len(res.cycles), "warnings:", res.warnings)
PY
```

### 11.4 POC 實測證據（2026-08-26，計畫撰寫時以 throwaway 腳本實跑）

**目的**：D-7「複用 `DcaCoordinator` 幾乎免費」與 R-1「次棒才撮合」是本計畫兩根承重柱，不能只靠程式碼閱讀宣稱。以下為實際執行輸出（沙箱先 `pip install --break-system-packages injector pyyaml`）。

**證據一：真引擎可被逐棒驅動（BP-1 可行性）**

以 duck-typing 的 `ReplayExchange`（實作 R-1/R-2/R-3/R-4/R-5）+ 真 `DcaOrderExecutor`（`min_notional=0`、`circuit_probe=lambda: False`）+ 真 `DcaCoordinator`（`begin()` + 逐棒 `await coord.step()`），注入 `time_fn`（棒時間）與 `ohlcv_provider`（≤ i 的 closes）：

```text
=== Run A：entry=immediate ===
  走過的狀態      : triggering → averaging → completed
  最終狀態        : completed | cycles = 1
  create/cancel   : 8 / 3 | 網路呼叫: 0
  成交標記數      : 5 | role 分佈: {'safety': 3, 'base': 1, 'take_profit': 1}
  標記 ts 皆為棒時間: True
  引擎 realized/fees: 0.79987328 / 0.064315229624
  INV 違規         : 無            ← 每棒斷言 INV-1（armed 零掛單）與 INV-2（averaging 恰好 1 張 TP）

=== Run B：entry=indicator（真 PriceChange 指標，經注入的 ohlcv_provider）===
  走過的狀態      : armed → triggering → averaging → completed
  最終狀態        : completed | cycles = 1
  create/cancel   : 5 / 3 | 網路呼叫: 0
  成交標記數      : 2 | role 分佈: {'take_profit': 1, 'base': 1}
  引擎 realized/fees: 0.19995324 / 0.016079470704
  INV 違規         : 無
```

已驗證的事實：

1. **`DcaCoordinator` 一行都不用改**即可完成完整循環回放（`begin()` + `step()` + 三個注入點）。
2. **零網路呼叫**，全程離線可重現。
3. `fills_snapshot()` 的顯示層副本可被後置蓋章為棒時間（`標記 ts 皆為棒時間: True`）。
4. 引擎會計值（`realized_profit` / `total_fees`）直接可用，無需另算（§4.5 的口徑要求可行）。
5. 引擎自身的邊界路徑（`window_realigned` 急跌穿透重對齊）在回放中**照常觸發**——這是「複用真引擎」相對「另寫模擬器」的實質收益。
6. INV-1 / INV-2 在回放的每一棒都成立（C3 稽核 3.5/3.6 的守衛可直接落地）。

**證據二：R-1 是 P0 而非風格偏好（决定性命題）**

構造一條「棒 1 收盤 2495 下基礎買單 → 棒 2 跳空高開（low=2510）」的路徑：

```text
次棒撮合（R-1 正確）      最終狀態=triggering  持倉=0.000000  均價=None  realized=0.000000  fills=0
同棒撮合（有未来函数）     最終狀態=stopped     持倉=0.000000  均價=None  realized=0.200400  fills=2
```

同棒撮合**憑空創造了一個實盤不會存在的部位並回報獲利 0.2004**；實盤在該路徑下機器人會停在 `triggering`、零持倉、零成交。這不是「誤差幾個百分點」，而是「有交易 vs 沒有交易」的差異 → RC-1 列為 P0 的依據。

**誠實補充（避免過度宣稱）**：在單調下跌後單調上漲的路徑上，同棒與次棒撮合的 `realized` 完全相同（實測差異 0.0%），因為每張限價單在兩種模式下都以同一限價成交。故 R-1 的 P&L 影響是**路徑依賴**的：單調路徑上可為 0，反轉路徑上無上界。C2 稽核 2.1 因此以「結構斷言」（棒 i 下單不得在棒 i 成交）而非「報酬差異」作為通過條件。

**證據三：設定驗證會在回測入口擋下非法參數**

`fetch_limit=50` 於 `DcaConfig.__post_init__` 被拒：`entry.fetch_limit 必須在 100-500 之間，收到: 50`（`core/services/dca/models/dca_config.py:146`）。這支持 §A-3「回測的 draft 參數必須走與建立機器人同一套驗證」——回測不可能接受實盤不接受的參數。

---

## 12. 版本與變更

| 日期 | 變更 |
|---|---|
| 2026-08-26 | 初版。依 branch `arena/01a03da6-grid-trading-console`（commit `62e3ba4`）實際盤點撰寫；D-6 ～ D-11 經使用者確認凍結。所有 `file:line` 引用經逐條 `sed -n` 驗證；§11.4 為 POC 實跑證據。 |
