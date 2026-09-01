# 網格完整回測功能 — 實作計畫（zh-TW）

版本：0.1（規劃稿）
日期：2026-09-01
狀態：待開發（已確認主要決策）

---

## 1. 目標

在 Web 總控台左側新增「網格回測」分頁，提供策略市場 / 既有網格設定 / 自訂參數的**完整歷史回測**，讓使用者依：

- **初始資金**
- **開啟時間（可指定開始/結束日期）**
- **收益曲線（equity curve）**
- **爆倉風險（逐倉保證金 + 維持保證金 + 清算價）**

判斷「這個網格策略在目前資金與開倉時間下是否會爆倉 / 是否值得開」。

回測一律為**唯讀**：不下真單、不寫 `config/active_grids.json`、不碰交易所私鑰。

---

## 2. 已確認決策

| 決策 | 選擇 |
|---|---|
| 回測引擎 | **專用資金感知模擬器**（`GridBacktestSimulator`），不直接驅動真實 `GridCoordinator/GridEngineImpl` |
| 風險範圍 | **完整逐倉保證金 / 爆倉模擬**（以交易所規則差異標示近似值），另外輸出權益曲線、最大回撤、資金燒損 |
| 回測窗口 | **開始/結束日期選擇器 + 預設週期**（7D / 30D / 180D / 1Y / 自訂 UTC 日期） |
| 參數來源 | **全支援**：左側新分頁、策略市場每行「回測」、既有 `config/grid/*.yaml`、手動/精靈參數 |
| 資金模型 | **使用者給總資金，系統分配**：輸入 `initial_capital` + 每格金額/佔比，算出可掛滿幾格；不足明確警示 |
| 附加能力 | **單次 + 參數掃描 + 壓力情境爆倉檢查** |

---

## 3. 現況診斷摘要

### 3.1 策略市場目前的「回測」不是完整回測

- 後端：`web_ui/backend/grid_recommendation.py::_simulate_grid_backtest()`
- 只做輕量虛擬撮合：統計價格穿越掛單線的買/賣次數、完整循環數，再由 `(interval% - 手續費) × cycles/hour × 8760` 推年化 APR。
- **缺少**：
  - `initial_capital`、每格下單量/名目金額
  - 逐筆手續費扣帳、已實現 / 未實現盈虧
  - 權益曲線、最大回撤、資金燒損
  - 槓桿、保證金佔用、維持保證金、清算價、爆倉時間點
  - 開倉日期與任意日期窗口
  - 「價格脫離網格區間」、單向趨勢、資金不足以掛滿格數等風險
- 前端：`MarketRankings.tsx` 只渲染 `estimated_apr`、格數、上下限，沒有權益曲線或風險指標。

### 3.2 目前存在兩套互不完全一致的「虛擬網格」

1. `web_ui/backend/grid_recommendation.py::_simulate_grid_backtest()`（市場排行用）
2. `grid_volatility_scanner/models/virtual_grid.py`（波動掃描器用，雙邊掛單，但仍無資金會計）

本計畫採用新的 `GridBacktestSimulator` 作為第三個、但唯一具備資金/風險會計的網格回測核心；`_simulate_grid_backtest` 維持在市場排行內，僅作為「快速篩選年化」用途，並在 UI 文案中標明「快速年化估計，非完整回測」。

---

## 4. 架構總覽

```
Frontend (網格回測分頁 / 市場排行「回測」按鈕)
        │  POST /api/v1/grid-backtest/run|sweep|stress
        ▼
web_ui/backend/routes/grid_backtest.py   (FASTAPI routers + 稽核 + 上限守衛)
        │
        ▼
web_ui/backend/grid_backtest_service.py  (參數正規化、既有YAML讀取、Kline 取得、排程執行)
        │
        ▼
core/services/grid/backtest/
    ├─ models.py       資料契約（GridBacktestRequest/Result/Equity...）
    ├─ allocation.py   資金→每格金額分配
    ├─ simulator.py    資金感知格線撮合（核心）
    ├─ risk.py         逐倉保證金/清算/回撤/爆倉風險（純函式）
    ├─ stress.py       壓力情境（歷史最差/單向 -15/-30/-50%）
    ├─ sweep.py        參數掃描
    └─ replay_driver.py 日期窗口解算 + 逐 K 棒驅動
        │
        ├── 共用 KlineService / KlineRepository（歷史 K 線）
        └── 共用 metrics（抽取自 DCA metrics，見 §5）
```

---

## 5. 可復用與需要抽取的元件

### 直接復用

- `web_ui/backend/kline_service.py` + `web_ui/backend/kline_repository.py`
  - DCA 已使用的 OHLCV cache-first / paginated backfill / status honesty。
  - 網格回測直接使用同一份 `dca_klines`（或新增同 schema 的表），不會增加交易所 API 壓力。
- `web_ui/backend/schemas.py` 中 `KlineCandle`、`BacktestEquityPoint` 概念。
- `web_ui/backend/audit.py::AuditLogger`（稽核日誌）
- `web_ui/backend/auth.py::verify_token`
- 前端：
  - `DcaChart`（lightweight-charts）可作為 `GridBacktestChart` 的畫布開發基底。
  - `DcaBacktestPanel` / `DcaBacktestResult` 的 UI pattern、`backtestPercentText` 等純函式。
  - `api.ts`、`router.ts`、`App.tsx` 的導航模式。
- `run_grid_trading.py::create_grid_config()`：既有 YAML → `GridConfig`，可抽成共用 loader。
- `core/services/grid/terminal_ui.py::_calculate_long_liquidation()/short`：爆倉價公式，抽成 `risk.py` 共用純函式。

### 抽取共用模組（避免 Grid 依賴 DCA）

- 將 DCA 的**通用績效指標**抽到 `core/services/backtest/metrics.py`（純函式）：
  - `max_drawdown_pct`
  - `buy_hold_return_pct`
  - `compute_metrics`（保留 DCA 相容包裝或 re-export，避免 break DCA 測試）。
- `KlineService.get_candles()` 增加可選 `since` 參數（支援任意開始日期），原參數行為不變。
- 爆倉公式抽到 `core/services/grid/backtest/risk.py`，`terminal_ui.py` 可改成呼叫共用函式（選擇性，不強制）。

---

## 6. 資金感知網格模擬器（核心）

### 6.1 支援範圍（v1）

- Grid type：`LONG` / `SHORT` / `NEUTRAL`。
- 固定價格區間 `lower_price` / `upper_price`。
- 格數與間距：`grid_count` 或 `grid_interval`（沿用 `GridConfig.__post_init__` 規則）。
- 每格金額：使用者給總資金 + 每格金額/佔比；系統計算可掛滿格數與所需資金。
- 手續費：`fee_rate`（預設取 YAML / 後端 profile）。
- 槓桿 / 保證金：`leverage`、`margin_mode=isolated`（v1 只支援 isolated；cross 標示「未支援」）。
- 簡單止損 / 本金保護 / 止盈可納入 v1 之後；v1 先標示「未納入模擬」的警告。

### 6.2 撮合模型

- 輸入：`List[Candle]`（OHLCV），逐根 bar 驅動。
- 狀態：
  - `cash`（USDT 餘額）
  - `position_qty`（帶正負號）
  - `avg_cost`
  - `inventory`：每條格線掛單狀態（pending buy / pending sell / empty），與 `grid_volatility_scanner` 的雙邊掛單模型一致但加入資金。
  - `realized_pnl`、`total_fees`、`filled_buy_count`、`filled_sell_count`、`completed_cycles`
- 每根 bar：
  1. 以 bar 的 `low` / `high` 依序撮合（保守順序：先盤中低點買單，再高點賣單；以同一格一次最多觸發一張，避免重複計數）。
  2. 成交時：扣/加 `cash`、更新 `position_qty`/`avg_cost`、扣 `fee = notional × fee_rate`、更新反向掛單。
  3. 計算期末 `equity = cash + position_qty × close + unrealized`（空頭方向 `(avg_cost - close) × |qty|`）。
  4. 紀錄 `GridEquityPoint { ts, equity, position_qty, avg_cost, margin_ratio, liquidation_price, status }`。
- 無未來函數：只使用 `<= i` 的資料；bar i 的訂單由 bar i+1 撮合（與 DCA ReplayDriver R-1 一致）。

### 6.3 資金分配（allocation.py）

- 輸入：`initial_capital`、`grid_count`、可設定之每格金額或 `per_grid_capital_percent`。
- 計算 `required_capital`：
  - v1 規則（可調整）：`每格保證金需求 = order_amount × price × (1 / leverage) × 安全係數`
  - `total_required = 每格需求 × 可容納格數`
- 輸出：
  - 建議 `order_amount`、`filled_grid_count`（可掛滿格數）
  - `insufficient_capital` 訊息：資金不足掛滿 N 格，只能掛 M 格。
- 若資金不足，回測**仍可執行**，但 UI 顯示「資金只夠 M 格，實際掛單 M 格」並列入 warnings。

### 6.4 爆倉 / 風險（risk.py）

採用 `terminal_ui` 既有公式並擴充為逐 bar 計算：

- 逐倉（isolated）淨權益：
  `equity = 帳戶權益 + 未實現盈虧（扣除手續費）`
- 維持保證金（maintenance margin）：v1 使用可配置 `maintenance_margin_rate`（預設 0.5%），並標示「近似值，依交易所規則而定」。
- 爆倉門檻（近似）：
  - 多頭：`liq_price ≈ avg_cost - (equity / position_qty)`
  - 空頭：`liq_price ≈ avg_cost + (equity / |position_qty|)`
  - 若 `equity <= 0` 或 `min(liq_price 距離歷史 low/high) <= 0` → 判定觸發爆倉。
- 每個 bar 輸出 `margin_ratio`、`liq_price`、`liq_distance_pct`。
- 風險指標：
  - `max_drawdown_pct`、`min_equity`、`max_margin_used_pct`
  - `liquidation_occurred`、`liquidation_ts`、`liquidation_price`
  - `worst_drawdown_episode`（開始/結束時間與幅度）

### 6.5 壓力情境（stress.py）

- 單向移動：以開始價為基準，一次性模擬 `-15% / -30% / -50%` 或 `+15% / +30% / +50%`。
- 輸出：該情境下是否爆倉、最低資金、最大持倉、所需追加資金。
- 歷史最差窗口：在回測期間滑動找「最大累積單向跌幅」，以該段 K 線重播。
- 這些情境在 UI 用獨立卡片呈現，不混入主回測結果。

### 6.6 參數掃描（sweep.py）

- 複用 DCA sweep 模式。
- 掃描軸（v1）：`lower_price / upper_price`、`grid_count`、`grid_interval`、`per_grid_amount`、`leverage`。
- 預算：`max_total_bars`（預設 300000）、`max_workers`（4）、`runs_limit`。
- 輸出：每組 run 的 `metrics` + `risk_metrics` + `budget` + `best`。

---

## 7. 後端 API 設計

新增 route：`web_ui/backend/routes/grid_backtest.py`，prefix `/api/v1/grid-backtest`，掛在 `main.py`。

### 7.1 請求模型（schemas 追加）

```
GridBacktestSource
├─ source_type: "draft" | "market_ranking" | "existing_yaml"
├─ exchange, symbol, grid_type, lower_price, upper_price,
│  grid_count / grid_interval, per_grid_amount, leverage,
│  fee_rate, maintenance_margin_rate, account_mode
```

```
POST /api/v1/grid-backtest/run
{
  "source": { "source_type": "market_ranking", ... },
  "start_ts": 1725000000,   // UTC 秒
  "end_ts": 1730000000,
  "timeframe": "1h",
  "initial_capital": "10000",
  "per_grid_amount": null,   // 或 per_grid_capital_percent
  "risk": { "leverage": 10, "margin_mode": "isolated", "maintenance_margin_rate": 0.005 }
}
→ GridBacktestResponse { result, risk_metrics, allocation, warnings }
```

```
POST /api/v1/grid-backtest/sweep
POST /api/v1/grid-backtest/stress
GET  /api/v1/grid-backtest/existing-configs   // 列出 config/grid/ 可回測的 YAML（metadata/基本摘要）
```

### 7.2 守衛

- `start_ts < end_ts`、`bars <= 20000`、`timeframe ∈ {1m,5m,15m,30m,1h,4h,1d}`。
- `initial_capital > 0`。
- `leverage ∈ [1,20]`、`margin_mode ∈ {isolated}`。
- 回測執行採 `run_in_executor` 或 `asyncio.wait_for`（>60-120s 回 408），比照 DCA。
- 每次呼叫 `AuditLogger.log(action="run_grid_backtest", ...)`。
- 交易為唯讀：不初始化 engine / 不下單 / 不連私有 WS。

---

## 8. 前端設計

### 8.1 路由 / 側欄

- `utils/router.ts` 增加 `'grid-backtest'` tab。
- `App.tsx` 側欄新增按鈕「網格回測」（icon e.g. `History` / `Gauge` / `FlaskConical`）。
- 從市場排行每行增加第二顆按鈕「回測」：把 `MarketRankingItem` 帶入 `GridBacktestPage` 並 prefilled。

### 8.2 元件

- `GridBacktestPage.tsx`：主頁，整合來源選擇、設定、執行、結果。
- `GridBacktestPanel.tsx`：來源選取（market ranking / existing yaml / manual）、交易對、日期範圍、時框、初始資金、格數/間距、每格金額、槓桿 / margin、執行。
- `GridBacktestResult.tsx`：指標卡（總報酬、買入持有、Alpha、最大回撤、已實現損益、手續費、最低資金、爆倉/未爆倉）、風險卡片、警示。
- `GridBacktestChart.tsx`：K 線 + 成交標記 + **權益曲線 + 爆倉/清算價線**（以 `DcaChart` 為基底抽離，維持 E-6.2 依賴隔離）。
- `GridStressResult.tsx`：壓力情境卡。
- `GridSweepResult.tsx`：掃描表格 / 熱圖。
- `utils/gridBacktest.ts`：純顯示輔助函式（零金融計算）。

### 8.3 UI 揭露

- 執行前即顯示「近似爆倉公式 / 未納入 funding rate、滑價、部分成交 / 交易所維持保證金不同」等簡化事項。
- 回測結束後全量渲染 `result.warnings`。
- 若用**合成 K 線**（paper mode）或 **stale 快取**，資料狀態與來源要標示。

---

## 9. 測試計畫

### 後端

- `tests/test_grid_backtest_simulator.py`
  - 一筆買入→賣出完成循環，`realized_pnl = (sell-buy)*qty - fees`。
  - 單向下跌不成交 / 脫離區間時 equity 變化符合預期。
  - `allocation`：資金不足 → 只能掛 M 格。
  - 無未來函數 / 確定性（同輸入兩次一致）。
- `tests/test_grid_backtest_risk_liquidation.py`
  - 爆倉公式（多、空）與 `terminal_ui` 參考值一致。
  - 資金充足不爆倉；資金不足 + 極端行情觸發爆倉。
  - margin ratio / liq distance 正確。
- `tests/test_grid_backtest_http_contract.py`
  - `/run`、`/sweep`、`/stress` 的 response schema、驗證、401、時限。
  - 確保 `create_order/cancel_order` 未被呼叫（唯讀）。
- 既有 `tests.test_dca_*` 保持綠（抽 metrics 時需回歸）。

### 前端

- `web_ui/frontend/tests/grid-backtest*.test.ts`
  - router tab、panel 驗證、結果無 NaN / null 不補 0、warnings 全渲染、市場排行「回測」帶入參數。

---

## 10. 實作階段

| Phase | 內容 | 驗收 |
|---|---|---|
| P0 | 抽取共用 `core/services/backtest/metrics.py`、`KlineService.since`、爆倉公式；DCA 回歸綠 | 現有 CI 不破 |
| P1 | `core/services/grid/backtest/` 核心：models、allocation、simulator、risk | 單元測試通過 |
| P2 | 後端 route + schema + service + `existing-configs` 清單 + guard/audit | HTTP contract 測試 |
| P3 | 前端「網格回測」分頁 + 市場排行「回測」入口 + 結果/圖表 | 手動 + 前端測試 |
| P4 | sweep + stress 完整版 | 前端 + 後端測試 |
| P5 | 文件、CHANGELOG、稽核報告 | 文件集完成 |

---

## 11. 風險與限制（誠實揭露）

- **近似爆倉**：交易所維持保證金、強平規則、資金費率、成交流動性、價格精度皆不同；v1 用可配置 `maintenance_margin_rate`，結果標示「近似，非交易所保證」。
- **不模擬**：部分成交、資金費率（funding）、滑價、調倉 / 風控、價格移動（follow）、剝頭皮（scalping）、馬丁（martingale）在 v1 可能不納入或僅部分納入。
- **K 線路徑假設**：同一根 bar 只有 high/low/close，撮合假設最低點先於最高點（或設定保守策略），不會 100% 等同 tick 級實盤。
- **既有 YAML 支援度**：先支援 LONG/SHORT/NEUTRAL 固定區間；複雜 grid type 會以 warning 標示不支援。
- **效能**：`bar ≤ 20000` 可回測；sweep/stress 需預算守衛避免 API / 執行資源過載。

---

## 12. 待實作前的二次確認（非阻塞，但實作時會影響實作範圍）

1. 每格金額分配規則：以「每格固定金額」或「每格佔總資金比例」優先？建議兩者都做，預設比例制。
2. 市場行情的「一鍵開格」保留，新增按鈕不會取代它。
3. 回測是否要把「未納入馬丁/剝頭皮/價格移動」的既有 YAML 直接標示 unsupported，還是先只回測基本區間？建議 v1 只回測基本區間，詳細模式放後續。

---

## 13. 額外功能評估（2026-09-01 補充）

### 13.1 新分頁同時回測 DCA 單向 / 雙向 + 指標回測

**工作量：中低。** 以下能力**現在已存在**，不需要重寫：

- DCA 方向：`core/services/dca/models/dca_config.py::DcaDirection` 已支援 `long / short / both`。
- DCA 完整回測：`POST /api/v1/dca/backtest` 已複用真 `DcaCoordinator + DcaOrderExecutor + EntryMonitor`，因此**指標回測是實際判定，不是假算**。
- 前端：`DcaBacktestPanel / DcaBacktestResult / DcaChart`、既有詳情頁回測分頁皆可複用。

新增工作僅是「整合」：

1. 在統一回測分頁加策略類型切換：`grid | dca-long | dca-short | dca-both`。
2. DCA 模式呼叫現有 `/api/v1/dca/backtest`（`draft` 帶指標設定）或新增薄包裝端點 `/api/v1/grid-backtest/dca`。
3. 來源支援：市場排行（僅網格）、既有 `config/dca/*.yaml`、DCA 精靈 `draft`、手動 DCA 參數。
4. 前端 DCA 模式直接掛載具備指標設定與結果的既有元件；避免重造 DCA 撮合/指標數學。

**結論：不是「新引擎」，是「統一入口 + 來源整合」，預估約 1 個 phase 的工作量。**

### 13.2 爆倉後補救提示（需追加資金 `XXX` 才能防爆倉）

**工作量：低。** 風險模組本來就會輸出逐 bar 的 `equity / position_qty / avg_cost / liquidation_price / liq_distance_pct`；補救提示只需：

- 在 `risk.py` 增加 `compute_additional_capital_to_prevent_liquidation()`：掃描最嚴重 bar，計算使 `liq_price` 離開歷史最低/最高觸發點的**最低追加資金**（以 isolated 保證金口徑）。
- 輸出 `risk_metrics.additional_capital_to_prevent_liquidation`、`additional_capital_before_liquidation_ts`、`additional_capital_required_now`（若已爆倉）。
- UI 在「爆倉風險」卡新增警示：`若初始資金再增加 $X，最差情境仍可維持權益 > 0 / 不會觸發清算`，同時顯示「目前距清算價 `-Y%`」。

**結論：約在 P1 風險模組多 1–2 個函式 + P3 一個警示卡，不需改核心架構。**

### 13.3 對 Phase 的影響

| Phase | 原計畫 | 加入兩項後的調整 |
|---|---|---|
| P1 | 網格 simulator + risk | risk 模組增加 `additional_capital_to_prevent_liquidation` |
| P2 | /run `/sweep` `/stress` | `/dca` 薄包裝端點（或直接對接既有 `/v1/dca/backtest`）；`GridBacktestRequest` 增加 `strategy_type` |
| P3 | 前端網格分頁 | 分頁加入網格/DCA 模式切換，DCA 模式複用既有 DCA 面板/圖表；爆倉追加資金警示卡 |
| P4 | sweep + stress | DCA 模式也可進入 sweep/stress 頁籤（複用既有 DCA sweep） |
| P5 | 文件/稽核 | 每項限制改列「DCA 部分已完整，網格部分為近似」 |

**總體新增工作量估：整體計畫約增加 10–20%，不影響主要架構與時程排序。**

---

## 14. 實作進度（2026-09-01）

| 階段 | 狀態 | 內容 |
|---|---|---|
| P0 | ✅ 完成 | `core/services/backtest/metrics.py` 通用績效純函式；DCA 既有測試回歸未受影響 |
| P1 | ✅ 完成 | `core/services/grid/backtest/`：`models.py`、`allocation.py`、`simulator.py`、`risk.py`、`stress.py`；單元測試 `tests/services/grid/backtest/`（21 項）通過 |
| P2 | ✅ 完成 | `web_ui/backend/grid_backtest_service.py` + `web_ui/backend/routes/grid_backtest.py` + `schemas` + 掛載 `main.py`；HTTP contract 測試（6 項）通過 |
| P3 | ✅ 完成（基礎版） | 前端新增「網格回測」左側分頁；策略市場每行新增「回測」按鈕；回測/壓力/掃描 UI；router/API 型別；前端 build/lint/test 通過 |
| P4 | ✅ 完成（基礎版） | 統一「回測」分頁新增策略類型切換（網格 / DCA 單向 / DCA 雙向）；DCA 模式複用既有 `/api/v1/dca/backtest` 真實引擎與 `DcaBacktestPanel` / `DcaBacktestResult`；支援 RSI/MACD/BB/PriceChange 指標入場、手動 draft 或既有 DCA bot 來源 |
| P5 | ✅ 完成 | `docs/grid_backtest_user_guide_zh-TW.md`、`docs/grid_backtest_audit_report_zh-TW.md`、`CHANGELOG.md` 更新、`README.md` 文件連結 |

已實作重點補充：
- 回測引擎為資金感知 `GridBacktestSimulator`，含逐倉保證金/清算、權益曲線、追加資金防爆倉建議。
- 資金分配支援「每格固定金額」與「每格佔總資金比例」兩種模式（§12 決策 1 完成）。
- 市場排行「一鍵開格」保留，新增第二顆「回測」按鈕（§12 決策 2 完成）。
- 複雜 YAML（follow/scalping/martingale/止盈/止損）統一在 warnings 標示未納入（§12 決策 3 完成）。

測試指令：
```bash
python3 -m unittest \
  tests.services.grid.backtest.test_allocation \
  tests.services.grid.backtest.test_risk \
  tests.services.grid.backtest.test_simulator \
  tests.services.grid.backtest.test_stress \
  tests.test_grid_backtest_http_contract
cd web_ui/frontend && npm test && npm run lint && npm run build
```

P4 已納入驗證：
- 前端 `GridBacktestPage` 新增「網格 / DCA」策略切換；DCA 回測不走自建引擎，直接呼叫既有 `/v1/dca/backtest`（`bot_id` 或 `draft`）。
- DCA `draft` 支援 `direction=long/short/both`、基礎單/安全單/價差/單量/止盈/止損/槓桿/費率，以及 `entry.mode=indicator` 的 RSI/MACD/BB/PriceChange 指標（預設值取自核心 `indicators/config.py`）。
- 「既有 DCA bot」來源透過 `getDcaSnapshot()` 列出可選 bot。
- 前端 `npm run lint` 僅存既有 `MarketRankings` useEffect warning（0 error）；`npm test` 99 pass；build pass。
