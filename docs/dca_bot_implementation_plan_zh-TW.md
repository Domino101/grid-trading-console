# DCA 機器人（Dollar-Cost Averaging Bot）實作計畫

> 建立日期：2026-08-25
> 分支：`arena/01a0386c-grid-trading-console`
> 範圍：`core/services/dca/`、`run_multiple_grids.py`（監督者）、`web_ui/backend/`、`web_ui/frontend/`、`config/dca/`
> 依據標準：`docs/grid_trading_console_audit_standard_zh-TW.md`（P0/P1/P2/P3 等級與 A–G 檢核表）
> 參考概念圖：使用者提供之「Create DCA Bot」截圖（第三方平台 UI，僅取產品模型，不複製數值與命名）

---

## 0. 執行摘要

本計畫在既有網格總控台上，新增一個**與網格完全隔離的 DCA 策略子系統**，並以獨立分頁呈現。

DCA 與網格的核心差異，也是本專案所有設計決策的出發點：

| 面向 | 網格 (Grid) | DCA |
|---|---|---|
| 啟動後行為 | 立即在區間內鋪滿雙向掛單 | **啟動後不掛任何單**，進入 `armed` 待命 |
| 掛單觸發 | 啟動即掛 | 僅在**入場條件成立**後才下基礎單 + 安全單 |
| 訂單結構 | 對稱網格層級 | 單向遞減（做多）/遞增（做空）**安全單階梯** |
| 獲利方式 | 區間往返套利 | 攤平成本後，以**單一止盈單**一次性了結 |
| 監控需求 | 價格 + 訂單 | 價格 + 訂單 + **技術指標（MACD/RSI/BB/Price Change）** |
| 生命週期 | 長期常駐 | 循環：待命 → 觸發 → 攤平 → 止盈 → （可選）回到待命 |

**「平常不顯示」的工程定義（本計畫最重要的可驗證性質）**：
機器人處於 `armed` 狀態時，交易所端 `get_open_orders(symbol)` 對該機器人的 client-id 命名空間**必須回傳 0 筆**。UI 仍會在 DCA 分頁顯示這台機器人與其「監控中」狀態，但**不存在任何真實掛單**。此性質在 Phase 2 與 Phase 7 均有專屬稽核項目（P0）。

全案切為 **7 個 Phase**，每個 Phase 依「單一 AI Agent 對話可完成」的工作量切分（詳見 §9 工作量校準），且每個 Phase 都附**獨立、可執行、可證偽的稽核標準**。未通過稽核的 Phase 不得進入下一個 Phase。

---

## 1. 需求對照表（使用者原始需求 → 規格條目）

| # | 使用者需求 | 規格條目 | 對應 Phase |
|---|---|---|---|
| 1 | 選擇交易對 | `exchange` + `symbol`，沿用 `GET /api/v1/market/info/{symbol}` 精度/最小名目校驗 | P4, P6 |
| 2 | 多 / 空 / 雙向 | `direction: long \| short \| both`；`both` 展開為兩條獨立 leg（§4.4） | P1, P2, P6 |
| 3A | 入場條件：從開單開始起算 | `entry.mode = immediate` | P1, P2 |
| 3B | 入場條件：符合特定指標 | `entry.mode = indicator`，支援 MACD / RSI / BB / Price Change，**四者皆有預設值且可自行更動** | P1, P2, P6 |
| 4 | max dca orders | `max_safety_orders` | P1 |
| 4 | price deviation | `price_deviation_percent`（+ `price_deviation_multiplier`） | P1 |
| 4 | order size multiplier | `order_size_multiplier` | P1 |
| 4 | take profit target (%) | `take_profit_percent`（基準 = 攤平後均價） | P1, P2 |
| 4 | 「等」其他參數 | 概念圖另含 `amount_per_trade`、`dca_mode`、`averaging_method`；本案另補 `stop_loss_percent`、`repeat_after_take_profit`、`cooldown_seconds`（§4.2 完整欄位表） | P1 |
| 5 | 功能全數做完要在前端完整顯示 | DCA 總覽 + 卡片 + 表格 + 建立精靈 + 詳情工作台（含階梯試算與指標面板） | P5, P6, P7 |
| 6 | 與網格功能分開一個分頁 | 側邊欄新增「DCA 機器人」分頁；資料面完全隔離（獨立 config 目錄、獨立控制檔、獨立 snapshot 端點） | P4, P5 |

---

## 2. 現況盤點（本計畫可直接複用的既有資產）

盤點自 commit `98a779e`，以下資產**必須複用而非重寫**，這是控制工作量與風險的關鍵：

| 既有資產 | 位置 | DCA 複用方式 |
|---|---|---|
| 交易所抽象層 | `core/adapters/exchanges/interface.py`（`create_order` / `cancel_order` / `get_open_orders` / `get_ohlcv` / `get_ticker`） | 直接複用，**不新增交易所程式碼** |
| PAPER 模擬盤 | `core/adapters/exchanges/adapters/paper.py` | 直接複用；**需補 `get_ohlcv()`**（目前回傳 `[]`，Phase 3 補合成 K 線） |
| 熔斷器 / API 限速 | `grid_engine_impl.py` 的 `read_persisted_circuit_state()` / `_is_circuit_open()` / `BinanceRest._is_adapter_circuit_open()` | 共用同一熔斷狀態；DCA 熔斷期間禁止下單 |
| 共享 Adapter 池 | `run_multiple_grids.py:ExchangeAdapterPool` | **關鍵**：DCA 掛進同一進程同一池，避免第二套 WS 連線與限速器 |
| 熱插拔監控器 | `run_multiple_grids.py:active_grids_monitor()` | 複製語意，新增 `active_dca_monitor()` 讀 `config/active_dca.json` |
| 執行期快照持久化 | `web_ui/backend/runtime_repository.py`（以 `config_path` 為主鍵） | **零 schema 變更直接複用**：DCA 路徑為 `config/dca/*.yaml`，與網格天然不衝突 |
| 命令 / 冪等 / 事件 | `web_ui/backend/command_repository.py`（以 `grid_id = Path(config_path).stem` 為鍵） | 同上零變更複用；DCA 檔名強制 `dca-` 前綴保證 ID 命名空間不碰撞 |
| 引擎↔API 橋接 | `web_ui/backend/control_plane.py`（`publish_runtime` / `publish_lifecycle`） | 直接複用 |
| 生命週期純函式 | `web_ui/backend/lifecycle.py`（`start_rejection` / `lifecycle_status` / `data_status_from_runtime` / `finite_number`） | 直接複用，DCA 沿用同一套 `live/paper` 閘門與 stale 語意 |
| 安全護欄 | `web_ui/backend/safety_guard.py` | 擴充：新增 DCA 併發上限與 DCA 最小投入檢查 |
| 稽核日誌 | `web_ui/backend/audit.py` | 直接複用，`action` 新增 `create_dca` / `start_dca` / `stop_dca` / `delete_dca` |
| 認證 / CORS | `web_ui/backend/auth.py` / `lifecycle.cors_settings` | 直接複用 |
| 前端 API 客戶端 | `web_ui/frontend/src/services/api.ts`（同源 `/api`） | 擴充 DCA 型別與方法 |
| 前端設計系統 | `App.css`（1790 行 token 化樣式）、`KpiCard` / `ConfirmDialog` / `TopBar` | 直接複用，**禁止新增平行樣式系統** |
| 指標運算 | 無（`pandas` 已在 `requirements.txt` 但目前未被 web/core 使用） | **Phase 1 以純 stdlib 實作**，不引入新依賴（rule-07 ponytail） |

### 2.1 已知缺口（必須在對應 Phase 補齊）

| 缺口 | 影響 | 補齊 Phase |
|---|---|---|
| `PaperExchangeAdapter.get_ohlcv()` 回傳 `[]` | PAPER 模式下 indicator 入場條件永遠無法評估 → E2E 不可測 | Phase 3 |
| 無 K 線快取層 | 每個 DCA 每輪拉 OHLCV → N 台機器人線性放大 REST 權重，可能觸發 Binance IP BAN | Phase 3（共享 `OhlcvCache`，同 symbol+timeframe 去重） |
| `safety_guard.MAX_CONCURRENT_GRIDS = 5` 只涵蓋網格 | DCA 可繞過總量上限 | Phase 4 |
| `scripts/ci.sh` 未包含 `tests/services/grid/*`（pytest 風格） | 新測試若寫成 pytest 風格會不進 CI | Phase 1（DCA 測試一律 unittest 風格並登記進 `ci.sh`） |
| 前端 `App.tsx` tab 型別為字面量聯集 | 新增分頁需同步改型別，否則 `tsc -b` 失敗 | Phase 5 |

---

## 3. 目標架構

```text
┌─ React Console ─────────────────────────────────────────────────────┐
│  側邊欄：策略總覽 │ 策略市場 │ 建立策略 │ ★DCA 機器人 │ ★建立 DCA    │
│    ├─ DcaDashboard.tsx   KPI + 機器人卡片/表格 + 狀態徽章            │
│    ├─ DcaForm.tsx        4 步精靈（General/Entry/DCA/Summary）       │
│    ├─ DcaDetail.tsx      階梯表 + 訂單 + 成交 + 事件 + 指標面板      │
│    └─ api.ts             同源 /api/v1/dca/*                          │
└───────────────────────────┬──────────────────────────────────────────┘
                            │ 同源 REST（Vite proxy → 8001）
┌─ FastAPI /api/v1/dca ─────┴──────────────────────────────────────────┐
│  routes/dca.py        CRUD + start/stop/delete + snapshot            │
│  routes/dca.py        POST /preview（階梯試算，純函式，零外部呼叫）  │
│  routes/market.py     GET /indicators/{symbol}（指標即時值，含快取）  │
│  dca_yaml_generator.py  JSON → config/dca/*.yaml（含驗證）           │
│  safety_guard.py      併發上限 / 最小投入 / 熔斷 / 目錄可寫          │
│  ─ 複用 ─ auth / audit / runtime_repository / command_repository     │
└───────────────────────────┬──────────────────────────────────────────┘
                            │ SQLite（data/web_ui_runtime.sqlite3）
┌─ 監督者進程 run_multiple_grids.py（單一進程，共享 Adapter 池）───────┐
│  active_grids_monitor()   既有：config/active_grids.json             │
│  active_dca_monitor()     新增：config/active_dca.json（5s 熱插拔）  │
│  ExchangeAdapterPool      ★兩者共用 → 不增加 WS 連線與 API 權重      │
│  OhlcvCache               ★新增：同 symbol+tf 去重，TTL 對齊 K 線週期│
└───────────────────────────┬──────────────────────────────────────────┘
                            │
┌─ core/services/dca/ ──────┴──────────────────────────────────────────┐
│  models/       DcaConfig / DcaState / DcaOrder / DcaStatistics       │
│  indicators/   macd.py rsi.py bollinger.py price_change.py（純函式） │
│  entry/        EntryEvaluator（immediate / indicator + all/any 邏輯）│
│  ladder.py     安全單階梯 + 均價 + 止盈價 + 所需資金（純函式）       │
│  execution/    DcaOrderExecutor（包裝 ExchangeInterface + 熔斷守衛） │
│  coordinator/  DcaCoordinator（狀態機主迴圈）                        │
└──────────────────────────────────────────────────────────────────────┘
```

### 3.1 為什麼 DCA 掛在既有監督者進程而非新開 PM2 App

**決策：DCA 由 `run_multiple_grids.py` 監督，不新增 PM2 app。**

理由（依風險排序）：

1. **API 權重是硬約束**。`config/grid/defaults.yaml` 已把單網格 REST 消耗鎖在 90 次/小時以維持在 Binance 6000 weight/min 之下。第二個進程 = 第二套 `ExchangeAdapterPool` = 第二組 WS 連線 + 獨立限速器，兩者互相不可見，是重現 `docs/gemini_legacy_reports/diagnosis_binance_ip_ban_and_apikey_20260503.md` 所記錄 IP BAN 事故的最快路徑。
2. **熔斷器需要單一真相**。`BinanceRest._adapter_circuit_until` 是類別層級狀態，跨進程不共享。分進程會出現「網格已熔斷但 DCA 仍在打 API」。
3. **rule-07 ponytail**：一個既有的監督迴圈 + 一個新的 monitor 協程，遠少於新進程 + 新鎖檔 + 新 PM2 條目 + 新啟動腳本。

**代價與緩解**：DCA 缺陷可能拖垮網格引擎。緩解措施為 Phase 3 的強制項——`run_single_dca_task()` 的頂層 `try/except` 必須捕捉所有非 `CancelledError` 例外，發 `failed` lifecycle 後**只結束自己這條 task**，不得向上傳播。此為 Phase 3 的 P0 稽核項。

保留退路：若未來 DCA 數量成長到需要獨立擴容，`DcaCoordinator` 與監督邏輯之間只透過 `config/active_dca.json` + `publish_runtime()` 耦合，抽成獨立進程是機械式改動。

---

## 4. 資料契約規格（凍結）

### 4.1 識別碼與命名空間

| 項目 | 規則 | 目的 |
|---|---|---|
| 設定檔目錄 | `config/dca/` | 與 `config/grid/` 隔離 |
| 檔名格式 | `dca-{exchange}-{direction}-{symbol}-{yyyymmddHHMMSS}.yaml` | `dca-` 前綴保證 `bot_id` 與 `grid_id` 永不碰撞 |
| `bot_id` | `Path(config_path).stem` | 與既有 `grid_id` 推導規則一致，可直接複用 command/runtime repository |
| 控制檔 | `config/active_dca.json`（`{config_path: bool}`） | 與 `config/active_grids.json` 對稱；已被 `.gitignore` 的 `data/` 規則外，需**新增 `config/active_dca.json` 到 `.gitignore`** |
| Leg（雙向模式） | `bot_id` + `#long` / `#short` 後綴，僅存在於 runtime 與 UI，不產生第二個 YAML | 使用者心智模型是「一台雙向機器人」 |
| 客戶端訂單 ID | `dca_{bot_id_hash8}_{leg}_{slot}`（`slot`: `base` / `so01`..`soNN` / `tp` / `sl`） | 使「armed 狀態下該命名空間掛單數 = 0」成為可機器驗證的斷言 |

### 4.2 `DcaConfig` 完整欄位表

> 所有金額欄位在核心層一律使用 `Decimal`；`float` 僅允許出現在 API 邊界與 UI。

**A. 基礎**

| 欄位 | 型別 | 預設 | 約束 | 說明 |
|---|---|---|---|---|
| `exchange` | str | — | `binance`/`lighter`/`backpack`/`hyperliquid` | 沿用 `yaml_generator.VALID_EXCHANGES` |
| `symbol` | str | — | 非空 | 如 `ETH/USDC:USDC` |
| `direction` | enum | `long` | `long`/`short`/`both` | `both` 展開為兩條 leg |
| `account_mode` | enum | `paper` | `paper`/`live` | LIVE 需 `GRID_LIVE_TRADING_ENABLED=true` |
| `leverage` | int | 10 | 1–125 | 合約適用 |
| `margin_mode` | enum | `isolated` | `cross`/`isolated` | |
| `quantity_precision` | int? | null | 0–10 | null 時由 `load_markets()` 自動填入 |
| `price_decimals` | int? | null | 0–10 | 同上 |
| `fee_rate` | Decimal | `0.0004` | ≥ 0 | 試算與淨利用 |

**B. 入場（Entry）**

| 欄位 | 型別 | 預設 | 約束 | 說明 |
|---|---|---|---|---|
| `entry.mode` | enum | `immediate` | `immediate`/`indicator` | 對應概念圖 Bot start condition |
| `entry.logic` | enum | `all` | `all`/`any` | 多指標組合邏輯（AND/OR） |
| `entry.timeframe` | str | `15m` | `1m`/`5m`/`15m`/`1h`/`4h`/`1d` | **所有指標共用同一 timeframe**（避免 N 個 timeframe × N 台機器人的 OHLCV 放大） |
| `entry.indicators[]` | list | `[]` | `mode=indicator` 時至少 1 個 | 見 §4.3 |
| `entry.confirm_bars` | int | 1 | 1–5 | 條件需連續成立幾根**已收線** K 棒才觸發（抗插針） |
| `entry.max_wait_seconds` | int? | null | > 0 或 null | 待命逾時自動停止；null = 無限等待 |

**C. DCA 階梯**

| 欄位 | 型別 | 預設 | 約束 | 概念圖對應 |
|---|---|---|---|---|
| `base_order_amount` | Decimal | — | > 0 | Amount per trade |
| `amount_unit` | enum | `quote` | `quote`(USD)/`base`(幣) | 概念圖的 USD 下拉 |
| `dca_mode` | enum | `order` | `order`/`position` | DCA Mode: Order / Position averaging |
| `averaging_method` | enum | `fixed_value` | `fixed_value`/`fixed_coin` | Averaging method |
| `max_safety_orders` | int | 20 | 1–50 | **Max DCA orders** |
| `price_deviation_percent` | Decimal | `0.8` | > 0, ≤ 50 | **Price deviation** |
| `price_deviation_multiplier` | Decimal | `1.2` | ≥ 1.0, ≤ 3.0 | **Price dev. multiplier** |
| `order_size_multiplier` | Decimal | `1.0` | ≥ 1.0, ≤ 3.0 | **Order size multiplier** |

**D. 出場與風控**

| 欄位 | 型別 | 預設 | 約束 | 說明 |
|---|---|---|---|---|
| `take_profit_percent` | Decimal | `1.0` | > 0, ≤ 100 | **Take profit target (%)**，基準 = 當前攤平均價 |
| `take_profit_order_type` | enum | `limit` | `limit`/`market` | `limit` 為 maker，預設 |
| `stop_loss_percent` | Decimal? | null | > 0 或 null | 以最終均價為基準；null = 停用 |
| `max_position_notional` | Decimal? | null | > 0 或 null | 硬性曝險上限，超過即拒絕再攤平 |
| `repeat_after_take_profit` | bool | `false` | — | 止盈後是否回到 `armed` 重新循環 |
| `cooldown_seconds` | int | 300 | ≥ 0 | 止盈後回到 `armed` 前的冷卻 |
| `max_cycles` | int? | null | > 0 或 null | 循環次數上限 |

**E. 運行參數**

| 欄位 | 型別 | 預設 | 說明 |
|---|---|---|---|
| `monitoring_interval` | int | 15（秒） | 主迴圈間隔 |
| `indicator_refresh_interval` | int | 60（秒） | 指標重算間隔（受 `OhlcvCache` 節流保護） |
| `order_health_check_interval` | int | 300（秒） | 掛單與交易所對帳間隔 |

### 4.3 指標規格與預設值（使用者要求「有預設值可自行更動」）

四種指標**全部支援 long 與 short 兩套語意**；`direction=both` 時 long leg 用 long 語意、short leg 用 short 語意。

| 指標 | 參數 | 預設 | Long 觸發條件（預設） | Short 觸發條件（預設） |
|---|---|---|---|---|
| **RSI** | `period` | 14 | `RSI ≤ oversold` | `RSI ≥ overbought` |
| | `oversold` | 30 | | |
| | `overbought` | 70 | | |
| **MACD** | `fast` / `slow` / `signal` | 12 / 26 / 9 | `histogram` 由負轉正（黃金交叉） | `histogram` 由正轉負（死亡交叉） |
| | `condition` | `cross` | 可選 `cross` / `above_zero` / `below_zero` | |
| **Bollinger Bands** | `period` / `std_dev` | 20 / 2.0 | `close ≤ lower_band` | `close ≥ upper_band` |
| | `condition` | `touch_lower`(long) / `touch_upper`(short) | 可選 `touch` / `break_and_return` | |
| **Price Change** | `lookback_bars` | 24 | `change% ≤ threshold`（跌破） | `change% ≥ threshold`（漲破） |
| | `threshold_percent` | -3.0（long）/ +3.0（short） | | |

> **Price Change 的索引定義（必須逐字實作，此處極易 off-by-one）**：
> `change% = (close[i] − close[i − lookback_bars]) / close[i − lookback_bars] × 100`
> 即 `lookback_bars=24` 是「往回**第 24 根**」（`close[119]` 對 `close[95]`），**不是** Python 的 `close[-24]`（那是往回第 23 根）。
> 本計畫在撰寫階段以兩份獨立實作交叉驗證時，正是在此處捕獲一次 off-by-one；Phase 1 測試必須同時斷言 `lookback=24` 與 `lookback=1` 兩組值以鎖死此語意。

**運算約定（凍結，Phase 1 必須完全遵守）**：

1. **只使用已收線 K 棒**。最後一根未收線 K 棒一律丟棄，否則指標會在同一根 K 棒內反覆抖動導致重複觸發。
2. RSI 使用 **Wilder 平滑**（非簡單平均）；首值為前 `period` 根的算術平均。
3. MACD 的 EMA 種子值為前 `period` 根的 SMA；signal 線在 MACD line 有效值序列上再做 EMA。
4. Bollinger 使用**母體標準差**（除以 `n`，非 `n-1`）。
5. 資料不足（有效值為 `None`）時，該指標評估結果為 **`False` 且標記 `insufficient_data`**，絕不可視為 `True`。

### 4.4 `direction = both` 的語意

`both` **不是**淨部位對沖，而是**兩條完全獨立的 leg**：

- Long leg：向下攤平，止盈在均價上方。
- Short leg：向上攤平，止盈在均價下方。
- 兩條 leg 各有獨立的 `DcaState`、獨立的入場評估、獨立的階梯與止盈單。
- **P0 前置條件**：交易所帳戶必須為 hedge / dual-position 模式。若 `get_positions()` 顯示為 one-way 模式，建立時即拒絕（HTTP 422），不得在執行期才失敗。
- `max_position_notional` 分別套用於每條 leg。
- 資金試算 = long leg 所需資金 + short leg 所需資金（不做淨額抵銷）。

### 4.5 階梯數學（凍結公式 + 鎖定測試向量）

設：`E` = 入場價、`b` = `price_deviation_percent/100`、`m` = `price_deviation_multiplier`、`s` = `order_size_multiplier`、`N` = `max_safety_orders`、`V₀` = 基礎單名目金額。

**步進偏移**（第 i 張安全單，i = 1..N）：

```
dev_i = b × m^(i-1)
```

**安全單價格**（做多；做空把 `(1 − dev)` 換成 `(1 + dev)`）：

```
dca_mode = order     : P_i = P_{i-1} × (1 − dev_i)        , P_0 = E
dca_mode = position  : P_i = avg_{i-1} × (1 − dev_i)      , avg_0 = E
```

**下單量**：

```
averaging_method = fixed_value : V_i = V₀ × s^i        ; Q_i = V_i / P_i
averaging_method = fixed_coin  : Q_i = (V₀/E) × s^i    ; V_i = Q_i × P_i
```

**均價 / 止盈價 / 所需資金 / 最大覆蓋跌幅**：

```
avg_k          = Σ_{j=0..k} V_j  /  Σ_{j=0..k} Q_j
take_profit_k  = avg_k × (1 + tp/100)          （做空為 × (1 − tp/100)）
required       = Σ_{j=0..N} V_j
max_drawdown%  = (E − P_N) / E × 100           （做空為 (P_N − E) / E × 100）
```

**鎖定測試向量（Phase 1 必須逐位對上，容差 1e-6）**

向量 A — `E=2500, b=0.8%, m=1.2, s=1.0, N=20, V₀=20 USD, dca_mode=order, averaging_method=fixed_value`：

| 項目 | 期望值 |
|---|---|
| `dev_1` / `dev_20` | `0.800000%` / `25.558400%` |
| `P_1` | `2480.000000` |
| `P_5` | `2354.614306` |
| `P_20` | `496.706826` |
| `avg_20` | `1468.987729` |
| `take_profit_20`（tp=1%） | `1483.677606` |
| `required` | `420.00` |
| `max_drawdown%` | `80.131727` |

> 以上為 `getcontext().prec ≥ 20` 的 `Decimal` 全精度結果，經 prec = 20/24/28/34/40/50 交叉驗證完全穩定。**以 `float` 實作會在小數第 5–6 位分岔而無法通過 `1e-6` 容差**——這正是稽核項 1.14（Decimal 純度）存在的原因。

向量 B — `E=2500, b=1.0%, m=1.0, s=1.5, N=5, V₀=20 USD, dca_mode=order, averaging_method=fixed_value`：

| 項目 | 期望值 |
|---|---|
| `P_5` | `2377.475125` |
| `avg_5` | `2411.443413` |
| `required` | `415.625` |
| `max_drawdown%` | `4.900995` |

> **關於概念圖上的 Summary 數值**：概念圖顯示 `Max price drop covered 79.97%` / `Max averaging order price deviation 28.25%` / `Required 286.99 USD`。逆算顯示該平台採「fixed coin amount + order averaging」且偏移索引由 1 起算（該組合我方公式得 `86.11% / 30.67% / 289.77`），其餘差異來自該平台未公開的內部四捨五入。**本專案不追隨第三方的未定義約定**，改以上述明確公式 + 鎖定向量為唯一真相；前端 Summary 面板的數值**必須**由後端 `POST /api/v1/dca/preview` 回傳，禁止前端另算一份（否則必然出現前後端不一致，屬 P0）。

### 4.6 狀態機（凍結）

```
                       ┌──────────────────────────────────────┐
                       ▼                                      │
draft ──start──► armed ──條件成立──► triggering ──基礎單成交──► averaging
                  │  ▲                    │                     │  │
                  │  │                    │下單失敗              │  │安全單成交
                  │  │cooldown 結束        ▼                     │  └──► averaging（重掛 TP）
                  │  └──── cooling ◄── completed ◄──止盈成交─────┘
                  │                        ▲
                  │                        │ stop_loss 成交 / max_cycles 達成
                  ├──stop──► stopping ──► stopped
                  └──致命錯誤──► failed
```

| 狀態 | 交易所掛單 | 持倉 | UI 徽章 | 語意 |
|---|---|---|---|---|
| `armed` | **0 筆（P0 不變式）** | 0 | 🟡 監控中 | 評估入場條件，不下任何單 |
| `triggering` | 0→1 | 0→有 | 🔵 進場中 | 條件成立，正在下基礎單 |
| `averaging` | 安全單 N 筆 + TP 1 筆 | 有 | 🟢 攤平中 | 主要運行狀態 |
| `completed` | 0 | 0 | ⚪ 本輪完成 | 止盈或止損成交 |
| `cooling` | 0 | 0 | 🕐 冷卻中 | `repeat_after_take_profit=true` 時的過渡 |
| `stopping` | 撤單中 | 不變 | 🟠 停止中 | 命令已受理，引擎執行中 |
| `stopped` | 0 | **預設不平倉**（與網格一致） | ⚫ 已停止 | |
| `failed` | 盡力撤單 | 不變 | 🔴 失敗 | 需人工介入 |

**不變式（每一條都是 P0 稽核項）**：

- **INV-1**：`armed` / `cooling` / `stopped` 狀態下，該 bot client-id 命名空間的交易所掛單數 = 0。
- **INV-2**：`averaging` 狀態下，TP 掛單**恰好 1 筆**，且其價格 = `avg_current × (1 ± tp/100)`（依精度 quantize 後比對）。
- **INV-3**：已下的安全單張數 ≤ `max_safety_orders`；任何情況下不得超發。
- **INV-4**：`Σ 已下單名目` ≤ `required`（由設定推導的上界），且若設 `max_position_notional`，`持倉名目 ≤ max_position_notional`。
- **INV-5**：狀態轉移只能沿上圖箭頭；任何非法轉移必須拋例外並轉 `failed`，不得靜默修正。

### 4.7 API 契約

| 方法 | 路徑 | 用途 | 對應 Phase |
|---|---|---|---|
| `GET` | `/api/v1/dca` | 列出所有 DCA 機器人 | P4 |
| `POST` | `/api/v1/dca` | 建立（寫 YAML；`auto_start` 觸發熱插拔） | P4 |
| `GET` | `/api/v1/dca/snapshot` | **批次快照**（總覽唯一資料來源，禁止 N+1） | P4 |
| `POST` | `/api/v1/dca/preview` | 階梯試算（純函式，零外部呼叫，無需 bot 存在） | P4 |
| `GET` | `/api/v1/dca/{bot_id}` | 單台設定 | P4 |
| `GET` | `/api/v1/dca/{bot_id}/stats` | 執行期統計 | P4 |
| `GET` | `/api/v1/dca/{bot_id}/ladder` | 當前實際階梯（含已成交/待成交狀態） | P4 |
| `GET` | `/api/v1/dca/{bot_id}/orders` | 掛單 | P4 |
| `GET` | `/api/v1/dca/{bot_id}/fills` | 成交 | P4 |
| `GET` | `/api/v1/dca/{bot_id}/events` | 生命週期事件 | P4 |
| `POST` | `/api/v1/dca/{bot_id}/start` | 啟動（→ `armed`），支援 `Idempotency-Key` | P4 |
| `POST` | `/api/v1/dca/{bot_id}/stop` | 停止（撤單、**預設不平倉**） | P4 |
| `DELETE` | `/api/v1/dca/{bot_id}` | 刪除（僅限 `stopped` 且無未完成命令） | P4 |
| `GET` | `/api/v1/market/indicators/{symbol}` | 指標即時值（供精靈預覽 + 詳情面板） | P4 |

**回應語意**（沿用網格既有標準，不得放寬）：
- 未知數值一律 `null`，**禁止以 `0` 代替**。
- 每個回應含 `data_status: live | stale | unavailable` 與 `last_updated_at`。
- 所有 mutation 回傳 `command_id`；`accepted` ≠ 引擎已確認。
- LIVE 建立/啟動的 feature flag 閘門與網格完全一致。

---

## 5. 分期執行計畫

> **通用規則（每個 Phase 都適用）**
> - 開工前：`git status` 必須乾淨（除既有 `graphify-out/` 髒檔）。
> - 收工前：`bash scripts/ci.sh`（若沙箱缺 FastAPI/Node，至少 `SKIP_FRONTEND=1 python3 -m unittest ...` 必須綠燈，並在報告中註明 skip 原因）。
> - 收工前：依 `AGENTS.md` 的「Service Restart & Live Verification」——若動到 `core/`、`web_ui/`、`run_*.py`、`config/`，必須明確列出需重啟的服務與指令。
> - 收工時：更新 `CHANGELOG.md` 的 `[Unreleased]` 區塊；**不得**修改 `main`。
> - 每個 Phase 的產出報告必須依 `docs/grid_trading_console_audit_standard_zh-TW.md` §5 的五段式格式（範圍與風險 / 檢核結果 / 測試證據 / 例外接受 / 部署回滾）。

---

### Phase 1 — 領域模型與指標引擎（純函式核心，零 I/O）

**目標**：把所有「可以在沒有交易所、沒有網路、沒有 FastAPI 的情況下算出來」的邏輯全部做完並鎖死。這是整個系統正確性的地基，也是唯一能做到 100% 確定性測試的一層。

**交付物**

```
core/services/dca/__init__.py
core/services/dca/models/__init__.py
core/services/dca/models/dca_config.py        # DcaConfig / DcaDirection / DcaMode / AveragingMethod / EntryMode + __post_init__ 驗證
core/services/dca/models/dca_state.py         # DcaState / DcaStatus / SafetyOrderSlot + 合法轉移表
core/services/dca/models/dca_order.py         # DcaOrder / DcaOrderRole(base|safety|take_profit|stop_loss)
core/services/dca/models/dca_statistics.py    # to_display_dict()（欄位與 GridStatistics 對齊，供 runtime_repository 複用）
core/services/dca/indicators/__init__.py
core/services/dca/indicators/base.py          # IndicatorResult(value, triggered, insufficient_data, as_of)
core/services/dca/indicators/rsi.py           # Wilder
core/services/dca/indicators/macd.py
core/services/dca/indicators/bollinger.py     # 母體標準差
core/services/dca/indicators/price_change.py
core/services/dca/entry/__init__.py
core/services/dca/entry/evaluator.py          # EntryEvaluator(immediate/indicator, all/any, confirm_bars)
core/services/dca/ladder.py                   # build_ladder() / required_capital() / take_profit_price()
tests/services/dca/__init__.py
tests/test_dca_ladder.py                      # unittest 風格（進 ci.sh）
tests/test_dca_indicators.py
tests/test_dca_config_and_state.py
scripts/ci.sh                                 # 追加三個新測試模組
```

**實作要點**

- `ladder.py` 是**純函式**：輸入 `DcaConfig` + 入場價，輸出 `list[LadderLevel]`，不碰任何外部狀態。這讓後端 `POST /preview` 與引擎實際下單共用同一份程式碼——前後端數值不一致的問題從結構上被消滅。
- 指標模組**不引入 pandas/numpy/ta-lib**（rule-07）。四個指標各約 30–50 行純 stdlib，且能被 `python3 -c` 直接驗證。
- 所有 `Decimal` 運算需設定 `getcontext().prec ≥ 28`，並在輸出前依 `price_decimals` / `quantity_precision` quantize（`ROUND_DOWN` 用於數量、`ROUND_HALF_EVEN` 用於價格）。
- `DcaState` 的轉移表以 `dict[DcaStatus, set[DcaStatus]]` 明確列出，`transition_to()` 遇非法轉移拋 `InvalidDcaTransition`。

**驗收指令**

```bash
python3 -m unittest tests.test_dca_ladder tests.test_dca_indicators tests.test_dca_config_and_state -v
python3 -c "import core.services.dca as d; print('import ok')"
git diff --stat -- requirements.txt          # 必須為空
```

#### Phase 1 稽核標準

| # | 檢核項 | 等級 | 通過條件（可證偽） |
|---|---|---|---|
| 1.1 | 階梯向量 A 全數對上 | P0 | `dev_1/dev_20/P_1/P_5/P_20/avg_20/tp_20/required/max_drawdown` 九個值與 §4.5 表格相符，容差 `1e-6` |
| 1.2 | 階梯向量 B 全數對上 | P0 | 四個值相符，容差 `1e-6` |
| 1.3 | 四種 `dca_mode × averaging_method` 組合皆有測試 | P0 | 4 個組合各至少 1 個測試，且 `required` 為單調不減 |
| 1.4 | 做空階梯對稱性 | P0 | 同參數下 short 的 `P_i` 相對 `E` 的偏移量 = long 的鏡像（容差 `1e-9`） |
| 1.5 | 指標鎖定向量 | P0 | 以 `f(i) = round(100 + 10·sin(i/7) + 0.15·i, 4), i=0..119` 為輸入，最後一根（i=119）：`RSI(14)=21.977841`、`MACD line=-2.240581`、`signal=-0.784542`、`hist=-1.456039`、`BB(20,2) lower=105.193225 / mid=116.876210 / upper=128.559195`、`priceChange(24)=-11.782277`；容差 `1e-6`。（此組向量已由兩份獨立實作交叉驗證） |
| 1.5b | Price Change 索引語意 | P0 | `lookback=24` → `-11.782277`（`close[119]` vs `close[95]`）；`lookback=23` → `-12.373066`。兩者皆須斷言，以鎖死 off-by-one |
| 1.6 | 指標暖機期正確 | P0 | 首個非 `None` 索引：RSI=14、MACD line=25、MACD signal=33、BB=19；暖機期內 `insufficient_data=True` 且 `triggered=False` |
| 1.7 | 資料不足不得誤判 | P0 | 傳入長度 < `period` 的序列，`triggered` 必為 `False`（不可為 `True`，不可拋例外） |
| 1.8 | 未收線 K 棒被丟棄 | P0 | 傳入 `n` 根與 `n+1` 根（最後一根未收線）產生相同指標值 |
| 1.9 | `entry.logic` AND/OR | P1 | `all`：任一指標 `False` → 不觸發；`any`：任一 `True` → 觸發；空指標清單 + `mode=indicator` → 設定驗證即報錯 |
| 1.10 | `confirm_bars` 抗插針 | P1 | `confirm_bars=3` 時，條件僅成立 2 根不得觸發；連續 3 根才觸發 |
| 1.11 | `immediate` 模式 | P0 | `mode=immediate` 時 `should_enter()` 恆為 `True` 且**完全不呼叫任何指標函式**（以 mock 斷言呼叫次數為 0） |
| 1.12 | 狀態機非法轉移 | P0 | `armed → completed`、`stopped → averaging` 等非法轉移拋 `InvalidDcaTransition`；合法轉移全數覆蓋 |
| 1.13 | 設定驗證邊界 | P0 | `max_safety_orders=0`、`price_deviation_percent=0`、`order_size_multiplier=0.9`、`take_profit_percent=0`、`leverage=200`、`direction=both` 但交易所不支援 → 全部產生明確中文錯誤訊息 |
| 1.14 | Decimal 純度 | P1 | `grep -n "float(" core/services/dca/ladder.py` 在計算路徑上為 0 命中（僅允許出現在 `to_display_dict()` 序列化） |
| 1.15 | 零新依賴 | P1 | `git diff requirements.txt` 為空；`grep -rn "import pandas\|import numpy\|import talib" core/services/dca/` 為 0 命中 |
| 1.16 | 零 I/O | P0 | `grep -rnE "requests\|aiohttp\|open\(|sqlite3\|asyncio" core/services/dca/{models,indicators,entry,ladder.py}` 為 0 命中 |
| 1.17 | 測試進 CI | P1 | `scripts/ci.sh` 已含三個新模組，且 `bash scripts/ci.sh` 的 Python 段落綠燈 |
| 1.18 | 網格回歸 | P0 | `python3 -m unittest tests.test_lifecycle_and_safety tests.test_paper_adapter tests.test_command_repository tests.test_runtime_repository` 全綠；`git diff --stat core/services/grid/` 為空 |

**退出條件**：18 項全 pass，或未 pass 項目全為 P2/P3 並記錄於例外接受表。

---

### Phase 2 — DCA 執行引擎與狀態機（含 armed 不掛單不變式）

**目標**：把 Phase 1 的純函式接上交易所抽象層，實作完整的 `armed → triggering → averaging → completed` 迴圈。此 Phase 仍**不接 FastAPI、不接 YAML、不接前端**，全部以 `FakeExchange` 驅動測試。

**交付物**

```
core/services/dca/interfaces/__init__.py
core/services/dca/interfaces/dca_executor.py      # IDcaOrderExecutor 抽象
core/services/dca/execution/__init__.py
core/services/dca/execution/dca_order_executor.py # 包裝 ExchangeInterface：下單/撤單/查單 + 熔斷守衛 + 精度 quantize + 最小名目檢查
core/services/dca/coordinator/__init__.py
core/services/dca/coordinator/dca_coordinator.py  # 主狀態機（start/stop/主迴圈/成交回調）
core/services/dca/coordinator/entry_monitor.py    # armed 期間的指標輪詢（含 OHLCV 取得介面，實作在 P3）
core/services/dca/coordinator/take_profit_manager.py # TP 掛單的建立/取消/重掛
core/services/dca/coordinator/leg_manager.py      # direction=both 的雙 leg 協調
tests/test_dca_coordinator.py                     # FakeExchange 驅動的狀態機測試
tests/test_dca_executor.py
scripts/ci.sh                                     # 追加兩個模組
```

**實作要點**

- **熔斷共用**：`DcaOrderExecutor` 下單前必須查 `BinanceRest._is_adapter_circuit_open()` 與 `GridEngineImpl._shared_api_circuit_until`。熔斷開啟時，`armed` 允許繼續（不打 API 的話），但 `triggering` 必須延後而非失敗。
- **TP 重掛的原子性**：安全單成交 → 均價改變 → 舊 TP 必須先 `cancel` 成功再 `place` 新 TP。順序反了會出現「兩張 TP 同時掛著」（超賣風險，P0）。若 `cancel` 成功但 `place` 失敗，狀態機必須進入 `degraded` 並在下一輪重試，**不可靜默留在無 TP 狀態**。
- **成交事件來源**：優先使用 WS 推送（`subscribe_order_updates`），REST 對帳作為 backup（間隔 `order_health_check_interval`）。兩者都要能觸發同一個 `_on_order_filled()`，且需去重（以 `order_id` 為鍵）。
- **task 隔離**：`DcaCoordinator.start()` 內建立的所有背景 task 都要註冊到 `self._tasks`，`stop()` 時逐一 cancel 並 `await` 完成，避免 Phase 3 熱插拔時洩漏 task。
- 借鑑 `CHANGELOG` 記錄的網格既有坑：`pause()/resume()` 必須是 `async def`；監控迴圈的終止條件用 `while self._running` 而非 `while not self._paused`（否則暫停期間 task 死亡，恢復後不會醒）。

**驗收指令**

```bash
python3 -m unittest tests.test_dca_coordinator tests.test_dca_executor -v
python3 -m unittest discover -s tests -p "test_dca_*.py" -v
```

#### Phase 2 稽核標準

| # | 檢核項 | 等級 | 通過條件（可證偽） |
|---|---|---|---|
| 2.1 | **INV-1 armed 不掛單** | P0 | 啟動 bot → `entry.mode=indicator` 且條件不成立 → 迴圈跑滿 20 輪 → `FakeExchange.create_order` 呼叫次數 **= 0**，`get_open_orders()` = 0 筆 |
| 2.2 | 觸發後才下基礎單 | P0 | 指標轉為成立 → 下一輪內恰好 1 次 `create_order`，`role=base`，數量 = `V₀/E` quantize 後值 |
| 2.3 | 基礎單成交 → 掛安全單 + TP | P0 | 基礎單 filled 事件後，掛單總數 = `min(active_window, N)` 安全單 + **恰好 1 筆 TP** |
| 2.4 | **INV-2 TP 唯一且價格正確** | P0 | 任意時刻 `role=take_profit` 的活躍掛單數 ≤ 1；其價格 == `ladder.take_profit_price(avg_current)` quantize 後值 |
| 2.5 | 安全單成交 → TP 重掛 | P0 | 每次安全單 filled，觀察到 `cancel(old_tp)` 先於 `create(new_tp)`；新 TP 價格對應新均價 |
| 2.6 | TP cancel 成功但 place 失敗 | P0 | 注入 `create_order` 例外 → 狀態轉 `degraded`，下一輪自動重試成功；期間不得回報 `running/正常` |
| 2.7 | **INV-3 不超發安全單** | P0 | 連續下跌穿透全部階梯 → `role=safety` 的下單總次數 **== `max_safety_orders`**，第 N+1 次不發生 |
| 2.8 | **INV-4 曝險上限** | P0 | 設 `max_position_notional` 低於階梯總額 → 觸及上限後停止再攤平，並記錄 `position_cap_reached` 事件 |
| 2.9 | TP 成交 → completed | P0 | TP filled → 狀態 `completed`，所有殘餘安全單被撤銷（`cancel_all` 或逐筆），掛單數歸 0 |
| 2.10 | `repeat_after_take_profit` 循環 | P1 | `true` + `cooldown_seconds=0` → `completed → cooling → armed`；`false` → `completed → stopped` |
| 2.11 | `max_cycles` 上限 | P1 | `max_cycles=2` → 第 2 輪止盈後進 `stopped`，不再回 `armed` |
| 2.12 | 停止語意 | P0 | `stop()` → 所有掛單撤銷、**持倉不動**（與網格一致）、狀態 `stopped`、`publish_lifecycle('stopped')` |
| 2.13 | 停止的冪等性 | P0 | 連續呼叫 `stop()` 三次 → `cancel` 不重複發送，不拋例外 |
| 2.14 | 熔斷守衛 | P0 | 模擬熔斷開啟 → `triggering` 延後（不下單、不轉 `failed`）；熔斷關閉後自動續行 |
| 2.15 | 精度與最小名目 | P0 | 價格依 `price_decimals`、數量依 `quantity_precision` quantize；低於 `min_notional` 的安全單被拒絕並記錄，不送交易所 |
| 2.16 | 成交事件去重 | P0 | 同一 `order_id` 由 WS 與 REST 各回報一次 → `_on_order_filled` 業務邏輯只執行一次（均價只更新一次） |
| 2.17 | `direction=both` 雙 leg 獨立 | P0 | long leg 觸發不影響 short leg 狀態；兩 leg 的 client-id 命名空間不重疊；兩 leg 各自有 1 筆 TP |
| 2.18 | 狀態轉移全覆蓋 | P1 | §4.6 圖上每一條箭頭都有對應測試；非法轉移拋例外 |
| 2.19 | task 無洩漏 | P1 | `start()` 後 `stop()`，`asyncio.all_tasks()` 中屬於該 coordinator 的 task 數歸 0 |
| 2.20 | 不阻塞 event loop | P1 | `grep -n "time.sleep" core/services/dca/` 為 0 命中；所有等待用 `await asyncio.sleep` |
| 2.21 | 日誌規範 | P1 | `grep -rn "print(" core/services/dca/` 為 0 命中（rule-03）；所有日誌含 symbol + bot_id + action + result |
| 2.22 | 網格回歸 | P0 | `git diff --stat core/services/grid/ run_grid_trading.py` 為空；Phase 1 全部測試仍綠 |

---

### Phase 3 — 設定持久化、OHLCV 快取與監督者整合（熱插拔）

**目標**：讓 DCA 能被 YAML 定義、被 `config/active_dca.json` 熱插拔控制、在共享 Adapter 池中運行、並把執行期狀態寫回 SQLite 供 API 讀取。此 Phase 完成後，**DCA 已可在 PAPER 模式端到端跑通（僅缺 UI）**。

**交付物**

```
config/dca/README.md                            # 設定檔說明與範例
config/dca/example-dca-paper-eth.yaml           # PAPER 範例（不進 active_dca.json）
core/services/dca/config_loader.py              # YAML dict → DcaConfig（分層：程式預設 ← defaults ← 個別 YAML）
core/services/dca/market_data/__init__.py
core/services/dca/market_data/ohlcv_cache.py    # 共享快取：key=(exchange,symbol,timeframe)，TTL 對齊 K 線週期
core/adapters/exchanges/adapters/paper.py       # 補 get_ohlcv()：以隨機遊走合成 K 線，可 seed
run_multiple_grids.py                           # 新增 run_single_dca_task() + active_dca_monitor()
.gitignore                                      # 新增 config/active_dca.json
tests/test_dca_config_loader.py
tests/test_dca_ohlcv_cache.py
tests/test_dca_paper_e2e.py                     # PAPER 端到端：armed → 觸發 → 攤平 → 止盈
scripts/ci.sh
```

**實作要點**

- **`OhlcvCache` 是本 Phase 的風險核心**。10 台 DCA 監控同一 `ETH/USDC:USDC 15m`，若各自拉 OHLCV = 10× REST 權重。快取以 `(exchange, symbol, timeframe)` 去重，TTL = `min(timeframe_seconds, 60)`，並用 `asyncio.Lock` 防止 cache stampede（多個 task 同時 miss 時只有一個真的去拉）。
- **PAPER 的 `get_ohlcv()`**：以 `symbol` 字串 hash 為 seed 的確定性隨機遊走生成，使 PAPER E2E 可重現。必須明確標記 `synthetic: true`，避免被誤認為真實行情。
- **task 隔離（P0）**：`run_single_dca_task()` 的頂層 `except Exception` 必須吞掉所有非 `CancelledError` 例外，發 `publish_lifecycle(path, 'failed', detail)` 後 return，**絕不向上拋**。這是「DCA 不得拖垮網格引擎」的唯一保證。
- `active_dca_monitor()` 與 `active_grids_monitor()` 邏輯對稱：5 秒輪詢、`iter_hotplug_starts()` 複用、被停用即 `await coordinator.stop()` + `task.cancel()`、找不到檔案或被 `start_rejection()` 擋下時即時發 `failed`（不靜默逾時）。
- 統計上報複用 `control_plane.publish_runtime(config_path, stats)`；`DcaStatistics.to_display_dict()` 需與 `GridStatistics` 的鍵名對齊（`profit` / `balance` / `time` / `filled_orders` / `pending_orders`），額外增加 `dca` 專屬區塊（`state`、`entry`、`ladder`、`cycles`）。

**驗收指令**

```bash
python3 -m unittest tests.test_dca_config_loader tests.test_dca_ohlcv_cache tests.test_dca_paper_e2e -v
python3 -c "
import yaml, sys; sys.path.insert(0,'.')
from core.services.dca.config_loader import load_dca_config
c = load_dca_config(yaml.safe_load(open('config/dca/example-dca-paper-eth.yaml')))
print(c.symbol, c.direction, c.max_safety_orders, c.account_mode)"
python3 run_multiple_grids.py --help                 # 不得因 import 錯誤而崩潰
grep -n "active_dca.json" .gitignore
```

#### Phase 3 稽核標準

| # | 檢核項 | 等級 | 通過條件 |
|---|---|---|---|
| 3.1 | YAML 往返一致 | P0 | `DcaConfig → YAML → load_dca_config()` 後所有欄位（含 `Decimal` 精度）完全相等 |
| 3.2 | 未知欄位不靜默吞掉 | P1 | YAML 含未定義鍵 → 載入時 warning 日誌明確列出鍵名 |
| 3.3 | 缺漏必填欄位 | P0 | 缺 `symbol` / `base_order_amount` → 明確中文錯誤，不使用 `0` 或空字串預設 |
| 3.4 | `account_mode` 缺省語意 | P0 | 未標 `account_mode` 的 YAML 被 `account_mode_from_yaml()` 判為 `unknown`（= legacy 實盤），**不得**自動視為 `paper` |
| 3.5 | LIVE 閘門 | P0 | `account_mode: live` + `GRID_LIVE_TRADING_ENABLED` 未設 → `start_rejection()` 回傳拒絕訊息，task 不啟動並發 `failed` |
| 3.6 | OHLCV 快取去重 | P0 | 5 台 bot 同 symbol+timeframe，一個 TTL 週期內 `get_ohlcv` 對交易所的實際呼叫次數 **= 1** |
| 3.7 | Cache stampede 防護 | P1 | 5 個 task 同時 cache miss → 底層呼叫仍為 1 次（`asyncio.Lock` 生效） |
| 3.8 | 快取 TTL 正確 | P1 | `15m` timeframe 的 TTL ≤ 60s；過期後重新拉取 |
| 3.9 | 快取失敗降級 | P0 | 交易所拋例外 → 回傳上一份快取並標記 `stale=True`；**無快取時回傳 `None`，指標評估為 `insufficient_data`，不得觸發入場** |
| 3.10 | PAPER `get_ohlcv` 可用 | P0 | `PaperExchangeAdapter.get_ohlcv('ETH/USDC:USDC','15m',limit=100)` 回傳 100 根，且同 seed 兩次呼叫結果相同 |
| 3.11 | PAPER 合成標記 | P1 | 回傳資料含 `synthetic` 標記，且 API 層會把它反映到 `data_status` |
| 3.12 | 熱插拔啟動 | P0 | 寫入 `config/active_dca.json` 的 `{path: true}` → 5 秒內 coordinator 進入 `armed`，`publish_lifecycle('running')` |
| 3.13 | 熱插拔停止 | P0 | 改為 `false` → 5 秒內撤單、狀態 `stopped`、task 結束、`running_dca_ref` 移除該項 |
| 3.14 | 找不到設定檔 | P1 | `active_dca.json` 指向不存在路徑 → 即時發 `failed` 事件（不靜默逾時），且同一路徑不重複刷錯誤日誌 |
| 3.15 | **task 隔離（DCA 不拖垮網格）** | P0 | 注入 `run_single_dca_task` 內部例外 → 該 task 發 `failed` 後結束，`active_grids_monitor()` 與所有網格 task 仍存活；主迴圈不退出 |
| 3.16 | Adapter 池共用 | P0 | 1 個網格 + 1 個 DCA 同交易所 → `ExchangeAdapterPool` 內該交易所的 adapter 實例數 **= 1** |
| 3.17 | runtime 快照寫入 | P0 | `publish_runtime()` 後，`RuntimeSnapshotRepository.load_all()` 含該 `config/dca/*.yaml` 鍵，`payload` 可 JSON 還原 |
| 3.18 | ID 命名空間隔離 | P0 | 同名 symbol 的網格與 DCA 並存 → `bot_id` 與 `grid_id` 不相等；`command_repository` 查詢互不干擾 |
| 3.19 | PAPER E2E | P0 | 一支測試完成 `armed（0 掛單）→ 指標觸發 → 基礎單成交 → ≥2 張安全單成交 → TP 成交 → completed`，全程斷言 INV-1..INV-4 |
| 3.20 | 控制檔不進版控 | P1 | `config/active_dca.json` 已在 `.gitignore`；`git status --porcelain` 不出現該檔 |
| 3.21 | 引擎啟動不回歸 | P0 | `python3 run_multiple_grids.py`（無參數）能進入熱插拔待命且不啟動任何策略；日誌無 traceback |
| 3.22 | 重啟驗證 | P1 | 依 `AGENTS.md` 規則，報告中列出 `pm2 restart multi-grid-engine` 並附重啟後日誌片段證據 |

---

### Phase 4 — 後端 API 層（CRUD + 生命週期 + snapshot + preview + 指標端點）

**目標**：把 Phase 3 的引擎能力以 REST 契約暴露，成為前端唯一資料來源。

**交付物**

```
web_ui/backend/dca_yaml_generator.py        # JSON → config/dca/*.yaml（含 validate_params）
web_ui/backend/routes/dca.py                # 全部 DCA 端點（§4.7）
web_ui/backend/schemas.py                   # 追加 Dca* Pydantic 模型
web_ui/backend/safety_guard.py              # 追加 MAX_CONCURRENT_DCA_BOTS / check_can_create_dca()
web_ui/backend/routes/market.py             # 追加 GET /indicators/{symbol}
web_ui/backend/main.py                      # include_router(dca_router)
tests/test_dca_http_contract.py             # FastAPI TestClient（缺 FastAPI 時 skip）
tests/test_dca_snapshot.py
tests/test_dca_yaml_generator.py
scripts/ci.sh
```

**實作要點**

- `POST /api/v1/dca/preview` **必須**直接呼叫 `core.services.dca.ladder`，不得複製一份計算邏輯。這是前後端數值一致性的唯一保證。
- `GET /api/v1/dca/snapshot` 走與 `routes/dashboard.py` 相同的模式：一次讀 `active_dca.json` → 逐檔讀 YAML → 讀 `GridStateStore` → 組讀模型 → `aggregate`。**禁止 N+1**。
- `GET /api/v1/market/indicators/{symbol}` 需有 60 秒記憶體快取（比照 `_market_cache`），且 `ccxt` 同步呼叫要 `run_in_executor` 包裝（比照 `_lookup_market_from_ccxt_sync`），避免阻塞 event loop。
- 安全護欄擴充：`MAX_CONCURRENT_DCA_BOTS`（預設 5），且**總量檢查需涵蓋網格 + DCA**（`active_grids + active_dca ≤ 10`），否則兩個上限各自為政等於沒有上限。
- LIVE 建立需比照 `create_grid`：feature flag → `_lookup_market_from_ccxt_sync` 市場規則校驗 → `min_notional` 檢查（對**每一張安全單**檢查，不只基礎單，因為 `order_size_multiplier < 1` 時尾端安全單可能低於最小名目——雖然目前約束 ≥ 1.0，仍需防禦）。
- `direction=both` 建立時需查 `get_positions()` 確認 hedge 模式，否則 422。

**驗收指令**

```bash
python3 -m unittest tests.test_dca_http_contract tests.test_dca_snapshot tests.test_dca_yaml_generator -v
# 對真實運行實例做 live verification（AGENTS.md 強制）
lsof -i :8001 || pm2 list
curl -s -H "Authorization: Bearer $GRID_API_TOKEN" localhost:8001/api/v1/dca/snapshot | head -c 400
curl -s -X POST -H "Authorization: Bearer $GRID_API_TOKEN" -H 'Content-Type: application/json' \
  -d '{"symbol":"ETH/USDC:USDC","exchange":"binance","direction":"long","base_order_amount":20,
       "max_safety_orders":20,"price_deviation_percent":0.8,"price_deviation_multiplier":1.2,
       "order_size_multiplier":1.0,"take_profit_percent":1.0,"entry_price":2500}' \
  localhost:8001/api/v1/dca/preview
curl -s localhost:8001/api/v1/dca/snapshot -o /dev/null -w '%{http_code}\n'   # 無 token 必須 401
```

#### Phase 4 稽核標準

| # | 檢核項 | 等級 | 通過條件 |
|---|---|---|---|
| 4.1 | 全端點需認證 | P0 | 逐一測試 §4.7 每個端點，無 `Authorization` → 401；錯誤 token → 401 |
| 4.2 | preview 與引擎同源 | P0 | `POST /preview` 的 `levels[]` 與 `core.services.dca.ladder.build_ladder()` 直接呼叫結果逐值相等；`grep -c "price_deviation_multiplier" web_ui/backend/routes/dca.py` 顯示未重寫公式 |
| 4.3 | preview 零外部呼叫 | P1 | preview 期間 mock `ccxt` 全部拋例外，端點仍回 200 |
| 4.4 | preview 對上鎖定向量 | P0 | 以向量 A 參數呼叫 → `required=420.00`、`max_drawdown_percent=80.131727`、`take_profit_price=1483.677606`（容差 1e-6） |
| 4.5 | snapshot 無 N+1 | P0 | 建立 10 台 bot → `GET /snapshot` 一次呼叫回傳全部；以計數器斷言 `RuntimeSnapshotRepository.load_all` 呼叫 ≤ 2 次 |
| 4.6 | 未知值為 null | P0 | 無 runtime 的 bot → `current_price/avg_price/unrealized_pnl` 皆為 `null`，**不是 `0`**；`data_status="unavailable"` |
| 4.7 | LIVE/PAPER 不混算 | P0 | 1 台 LIVE + 1 台 PAPER → `aggregate` 只含 LIVE，`aggregate_paper` 另計（比照 `aggregate_read_models`） |
| 4.8 | 建立→YAML 落地 | P0 | `POST /dca` 201 → `config/dca/dca-*.yaml` 存在且可被 `load_dca_config()` 解析 |
| 4.9 | `auto_start=false` 預設 | P0 | 未指定 `auto_start` → `active_dca.json` 中該項為 `false` 或不存在；引擎不啟動 |
| 4.10 | 冪等鍵 | P0 | 同 `Idempotency-Key` 呼叫 `start` 兩次 → 回傳同一 `command_id`，`active_dca.json` 只寫一次 |
| 4.11 | 刪除限制 | P0 | 對 `running` 或有 `pending_engine` 命令的 bot 呼叫 DELETE → 409 且附明確原因 |
| 4.12 | LIVE feature flag | P0 | `GRID_LIVE_TRADING_ENABLED` 未設時 `POST /dca {account_mode:"live"}` → 403 |
| 4.13 | 市場規則校驗 | P0 | LIVE 建立時，`min_notional` 對基礎單**與每一張安全單**都檢查；不通過 → 422 並列出違規的 slot |
| 4.14 | `both` hedge 檢查 | P0 | one-way 模式帳戶建立 `direction=both` → 422，訊息說明需開啟雙向持倉 |
| 4.15 | 併發總量上限 | P0 | 網格 + DCA 合計達上限後再建 → 400/429 並說明目前佔用；**不得**只檢查 DCA 自己的數量 |
| 4.16 | 寫入頻率限制 | P1 | 短時間連續建立 → 429（複用 `WriteRateLimiter`） |
| 4.17 | 稽核日誌 | P0 | 每次 create/start/stop/delete 都在 `audit_log` 產生一筆，含 `source`/`params`/`result`；失敗也要記錄 |
| 4.18 | 稽核不洩密 | P0 | `audit_log.params_json` 與所有 API 回應中不出現 API key / secret / token（以 grep 斷言） |
| 4.19 | 指標端點快取 | P1 | 連續 3 次 `GET /market/indicators/{symbol}` → 底層 `fetch_ohlcv` 只呼叫 1 次；第 2、3 次 `cached=true` |
| 4.20 | 指標端點不阻塞 | P1 | `ccxt` 同步呼叫以 `run_in_executor` 包裝（程式碼審查 + 並發 5 請求的回應時間不線性累加） |
| 4.21 | Pydantic 契約完整 | P1 | 所有端點有 `response_model`；`/openapi.json` 可生成且含全部 `Dca*` schema |
| 4.22 | 路徑穿越防護 | P0 | `bot_id` 含 `../` 或 `/` → 400/404，不得讀寫 `config/dca/` 以外路徑 |
| 4.23 | 網格 API 回歸 | P0 | `python3 -m unittest tests.test_web_ui_http_contract tests.test_dashboard_snapshot` 全綠 |
| 4.24 | Live 驗證 | P0 | 依 `AGENTS.md`：重啟後端 → 對運行中的 8001 實際 curl → 附回應片段為證據 |

---

### Phase 5 — 前端 DCA 分頁（總覽 + 卡片 + 表格）

**目標**：新增與網格並列的獨立分頁，以 snapshot 單次輪詢驅動，完整呈現 DCA 專屬狀態。

**交付物**

```
web_ui/frontend/src/services/api.ts             # 追加 Dca* 型別與 api.getDcaSnapshot/startDca/stopDca/deleteDca/previewDca
web_ui/frontend/src/App.tsx                     # tab 型別擴充 + 側邊欄「DCA 機器人」+ 「建立 DCA」
web_ui/frontend/src/components/DcaDashboard.tsx # KPI + 篩選 + 卡片/表格切換
web_ui/frontend/src/components/DcaCard.tsx      # 單台機器人卡片
web_ui/frontend/src/components/DcaTable.tsx     # 表格視圖
web_ui/frontend/src/components/DcaStatusBadge.tsx # 狀態徽章（7 種狀態）
web_ui/frontend/src/utils/dca.ts                # 純函式：進度計算、狀態文案、階梯摘要
web_ui/frontend/src/App.css                     # 追加 DCA 專屬樣式（沿用既有 CSS 變數）
web_ui/frontend/tests/dca.test.ts               # node --test（比照 format.test.ts）
tests/test_dca_frontend_contract.py             # Python 端鏡像契約（比照 test_frontend_format_contract.py）
```

**DCA 卡片必須顯示的欄位**（缺一即為資訊不完整）：

| 區塊 | 欄位 |
|---|---|
| 標頭 | symbol、exchange、方向徽章（多/空/雙向）、LIVE/PAPER、狀態徽章、資料新鮮度 |
| 入場 | 入場模式（立即/指標）、啟用的指標與當前值、距離觸發的差距、`armed` 已等待時長 |
| 階梯 | 已成交安全單 / 上限（如 `3 / 20`）、進度條、下一張安全單價格與距離% |
| 部位 | 均價、當前價、持倉量、持倉名目、未實現盈虧、ROI% |
| 出場 | 止盈價、距止盈%、止損價（若啟用）、本輪已完成循環數 |
| 操作 | 詳情、啟動/停止、刪除（危險操作需 `ConfirmDialog` 二次確認） |

**實作要點**

- `armed` 狀態的卡片必須明確顯示「**尚未掛單，監控中**」，這是使用者最容易誤解的一點（以為壞了）。
- 輪詢沿用 `Dashboard.tsx` 既有模式：`inFlight` guard、`AbortController`、`nextPollDelay(failures)` 退避、頁面隱藏時降頻。
- **禁止**新增第二套設計 token；顏色一律用 `var(--accent-primary)` / `var(--text-muted)` 等既有變數。
- 所有金額格式化走 `utils/format.ts` 的 `money()` / `number()`，未知值顯示 `—`。

**驗收指令**

```bash
cd web_ui/frontend && npm test && npm run lint && npm run build
cd ../.. && python3 -m unittest tests.test_dca_frontend_contract -v
```

#### Phase 5 稽核標準

| # | 檢核項 | 等級 | 通過條件 |
|---|---|---|---|
| 5.1 | 建置與型別 | P0 | `npm run build`（含 `tsc -b`）零錯誤；`npm run lint` 零 error |
| 5.2 | 分頁完全隔離 | P0 | DCA 分頁不呼叫任何 `/api/v1/grids/*` 或 `/api/v1/dashboard/*`；網格分頁不呼叫 `/api/v1/dca/*`（以程式碼審查 + Network 面板證據） |
| 5.3 | 同源相對 URL | P0 | `grep -n "localhost\|127.0.0.1\|http://" src/services/api.ts` 在瀏覽器路徑上為 0 命中 |
| 5.4 | 單次 snapshot | P0 | 10 台 bot 的一次刷新 → 恰好 1 次 `/api/v1/dca/snapshot` 請求（+1 次 health），**不是 10 次** |
| 5.5 | 請求不重疊 | P1 | 快速切換分頁 → 前一請求被 `abort`，無 race 導致的錯誤資料 |
| 5.6 | 退避重試 | P1 | 後端關閉 → 輪詢間隔依 `nextPollDelay` 遞增至上限，不狂打 |
| 5.7 | `armed` 文案正確 | P0 | `armed` 卡片顯示「監控中 · 尚未掛單」，且**不顯示**任何掛單數字或止盈價（避免誤導） |
| 5.8 | 未知值顯示 `—` | P0 | `data_status=unavailable` 的卡片，所有金額欄位顯示 `—`，**不顯示 `$0.00`** |
| 5.9 | LIVE/PAPER 可辨識 | P0 | 每張卡片與 KPI 區都有明確 LIVE/PAPER 標記，顏色 + 文字雙重編碼（不只靠顏色） |
| 5.10 | stale 標示 | P0 | 資料過期 → 顯示「資料過期」與最後同步時間，**不得**顯示「運行正常」 |
| 5.11 | 七種狀態皆有徽章 | P1 | `armed/triggering/averaging/completed/cooling/stopping/stopped/failed` 各有不同視覺與中文文案 |
| 5.12 | 危險操作二次確認 | P0 | 停止與刪除都走 `ConfirmDialog`，確認文案包含 bot 名稱、mode、以及「撤單但不平倉」的明確說明 |
| 5.13 | 每卡獨立 action state | P1 | 對一台 bot 操作時，其他卡片按鈕不被鎖住 |
| 5.14 | RWD | P1 | 1440px 三/四欄、768px 兩欄、375px 單欄，皆無水平溢出；關鍵狀態不被隱藏 |
| 5.15 | 無障礙 | P1 | 所有互動元素可鍵盤到達、有可見 focus、有 `aria-label`；狀態不只以顏色傳達 |
| 5.16 | 純函式有測試 | P1 | `utils/dca.ts` 的進度計算、狀態文案、距觸發% 皆有 `node --test` 覆蓋 |
| 5.17 | 前後端契約鏡像 | P1 | `tests/test_dca_frontend_contract.py` 以 Python 重現前端格式化規則並斷言一致（比照既有 `test_frontend_format_contract.py`） |
| 5.18 | 網格 UI 零回歸 | P0 | `git diff web_ui/frontend/src/components/{Dashboard,GridCard,GridTable,GridDetail,GridForm}.tsx` 僅允許 0 行變更（`App.tsx` 除外）；既有 `npm test` 全綠 |
| 5.19 | 樣式不分岔 | P2 | 新增 CSS 只使用既有 `--*` 變數；`grep -c "#[0-9a-fA-F]\{6\}"` 新增區塊命中數 ≤ 既有平均水準 |
| 5.20 | Live 驗證 | P0 | 啟動 dev server（`0.0.0.0`）+ 後端，於 preview 實際操作截圖：`armed` 卡片、`averaging` 卡片、停止確認框 |

---

### Phase 6 — 建立 DCA 機器人精靈（4 步 + 即時階梯試算）

**目標**：實作對照概念圖的建立流程，並以後端 preview 驅動 Summary 面板。

**交付物**

```
web_ui/frontend/src/components/DcaForm.tsx          # 4 步精靈
web_ui/frontend/src/components/DcaLadderPreview.tsx # Summary 面板 + 完整階梯表
web_ui/frontend/src/components/IndicatorConfigPanel.tsx # 四種指標的參數編輯
web_ui/frontend/src/App.tsx                          # 掛載「建立 DCA」分頁
web_ui/frontend/src/App.css
web_ui/frontend/tests/dca-form.test.ts               # 步驟推進規則（純函式）
tests/test_dca_frontend_contract.py                  # 追加步驟推進規則鏡像
```

**四步結構（對照概念圖）**

| 步驟 | 概念圖對應 | 內容 |
|---|---|---|
| 1. General | Exchange account / Pair / Direction | 交易所、交易對（含 `market/info` 精度查詢）、方向（多/空/雙向）、PAPER/LIVE |
| 2. Entry | Bot start condition | `Immediate` / `Indicator` 二選一；選 Indicator 後展開四種指標的啟用開關 + 參數 + AND/OR + timeframe + `confirm_bars`；顯示各指標**當前即時值**與是否成立 |
| 3. DCA | Amount / DCA Mode / Averaging method / Max DCA orders / Price deviation / Order size multiplier / Price dev. multiplier | 全部 §4.2-C 欄位 + 止盈% + 止損% + 循環設定 |
| 4. Summary | Summary / Required | 由 `POST /preview` 回傳：最大覆蓋跌幅、最大安全單偏移、所需資金、完整階梯表、與可用餘額比對；LIVE 需輸入 `CONFIRM LIVE` |

**實作要點**

- Summary 的每一個數字**必須**來自 `POST /api/v1/dca/preview` 的回應欄位，**禁止**在前端做任何階梯數學。這一條是 Phase 6 的核心稽核項。
- preview 請求需 debounce（300ms）並可取消，避免每次按鍵都打後端。
- 「Required 超過可用餘額」時比照概念圖顯示紅色警示，並**禁用**建立按鈕（LIVE 模式）；PAPER 模式僅警告。
- 指標當前值面板呼叫 `GET /api/v1/market/indicators/{symbol}`，顯示 `RSI=28.4（≤30 成立 ✓）` 這種可讀格式。

**驗收指令**

```bash
cd web_ui/frontend && npm test && npm run lint && npm run build
cd ../.. && python3 -m unittest tests.test_dca_frontend_contract -v
```

#### Phase 6 稽核標準

| # | 檢核項 | 等級 | 通過條件 |
|---|---|---|---|
| 6.1 | 建置與型別 | P0 | `npm run build` + `npm run lint` 零錯誤 |
| 6.2 | **Summary 不自算** | P0 | `grep -nE "price_deviation_multiplier|Math.pow|\*\*" src/components/DcaLadderPreview.tsx` 在計算路徑上為 0 命中；所有數值取自 preview 回應 |
| 6.3 | 前後端數值一致 | P0 | 以向量 A 參數填表 → UI 顯示 `所需資金 420.00 USD`、`最大覆蓋跌幅 80.13%`，與 curl preview 回應完全相同 |
| 6.4 | 需求 1：交易對 | P0 | 可選交易所 + 交易對，且送出前已呼叫 `market/info` 取得精度；找不到交易對 → 明確錯誤 |
| 6.5 | 需求 2：多/空/雙向 | P0 | 三個選項皆可選；選 `both` 時 Summary 顯示兩條 leg 的資金合計；不支援 hedge 時送出得到 422 並在 UI 顯示原因 |
| 6.6 | 需求 3A：Immediate | P0 | 選 `Immediate` → 指標區塊隱藏；送出的 payload `entry.mode="immediate"` 且無 `indicators` |
| 6.7 | 需求 3B：四種指標 | P0 | MACD / RSI / BB / Price Change 四者皆可獨立啟用，**每個參數都有預設值且可編輯**（§4.3 表格逐項核對） |
| 6.8 | 指標預設值正確 | P0 | 開啟每個指標時，欄位預填值與 §4.3 完全一致（RSI 14/30/70、MACD 12/26/9、BB 20/2.0、PriceChange 24/-3.0） |
| 6.9 | 指標即時值 | P1 | 指標面板顯示當前值與「成立/不成立」判定；資料不足時顯示「資料不足」而非 `0` |
| 6.10 | 需求 4：四個核心參數 | P0 | `Max DCA orders`、`Price deviation`、`Order size multiplier`、`Take profit target(%)` 四者皆可編輯，且改動即時反映到 Summary |
| 6.11 | 其他參數完整 | P1 | `Price dev. multiplier`、`DCA Mode`、`Averaging method`、`Amount per trade` + 單位、止損、循環設定皆存在 |
| 6.12 | 前端驗證僅為輔助 | P0 | 繞過前端直接 POST 非法參數（如 `max_safety_orders=999`）→ 後端仍拒絕（422） |
| 6.13 | 步驟推進規則 | P1 | 每步的必填欄位未填完不可前進；規則有純函式測試（比照 `canAdvanceCreateStep`） |
| 6.14 | LIVE 二次確認 | P0 | LIVE 模式需輸入 `CONFIRM LIVE` 才能提交；確認文案含交易對、方向、所需資金、最大跌幅 |
| 6.15 | 資金不足警示 | P1 | `required > 可用餘額` → 紅色警示；LIVE 模式禁用提交按鈕 |
| 6.16 | preview debounce | P1 | 連續輸入 10 個字元 → preview 請求 ≤ 2 次；請求可取消 |
| 6.17 | 建立成功導向 | P2 | 建立成功顯示成功卡片，可一鍵前往 DCA 總覽或該 bot 詳情 |
| 6.18 | 網格精靈零回歸 | P0 | `git diff web_ui/frontend/src/components/GridForm.tsx` 為空；既有 `npm test` 全綠 |
| 6.19 | Live 驗證 | P0 | preview 環境實際完成一次 PAPER 建立，附四個步驟與 Summary 的截圖 |

---

### Phase 7 — DCA 詳情工作台、端到端驗收與交付文件

**目標**：補齊最後的深度視圖，執行完整 PAPER E2E，產出可放行的稽核報告。

**交付物**

```
web_ui/frontend/src/components/DcaDetail.tsx      # 階梯表 / 訂單 / 成交 / 事件 / 指標 / 設定 五個 tab
web_ui/frontend/src/components/DcaLadderTable.tsx # 階梯狀態表（已成交/待成交/未觸及）
web_ui/frontend/src/App.tsx                       # 詳情路由（lazy import，比照 GridDetail）
docs/dca_bot_user_guide_zh-TW.md                  # 使用者操作手冊
docs/dca_bot_audit_report_zh-TW.md                # 最終稽核報告（五段式）
CHANGELOG.md                                      # [Unreleased] 完整條目
README.md                                         # 帳戶模式/測試章節補 DCA
config/dca/README.md                              # 設定檔欄位說明
scripts/ci.sh                                     # 確認全部 DCA 測試已登記
```

**詳情工作台內容**

| Tab | 內容 |
|---|---|
| 階梯 | 完整安全單表：slot、偏移%、價格、數量、名目、狀態（已成交/掛單中/未觸及）、成交時間；標出當前價位置與均價線 |
| 訂單 | 當前交易所掛單（含 role 標記） |
| 成交 | 歷史成交，含手續費與累積均價變化 |
| 事件 | 生命週期事件時間線（觸發、攤平、TP 重掛、止盈、錯誤） |
| 指標 | `armed` 期間的指標歷史與觸發點標記 |
| 設定 | 完整設定唯讀檢視 + YAML 原文 |

**端到端驗收腳本（PAPER）**

```bash
# 1. 建立
curl -X POST .../api/v1/dca -d '{... entry.mode=indicator, RSI<=30 ...}'
# 2. 啟動 → 確認 armed 且交易所零掛單
curl -X POST .../api/v1/dca/{id}/start
curl .../api/v1/dca/{id}/orders     # 期望 orders: []
# 3. 驅動 PAPER 價格下跌至 RSI 觸發
# 4. 確認基礎單 + 安全單 + 恰好 1 筆 TP
# 5. 驅動價格上漲至 TP 成交 → completed
# 6. 停止 + 刪除 → 確認掛單歸零、YAML 移除、審計完整
curl .../api/v1/system/audit?action=create_dca
```

#### Phase 7 稽核標準

| # | 檢核項 | 等級 | 通過條件 |
|---|---|---|---|
| 7.1 | **E2E：armed 零掛單** | P0 | 步驟 2 的 `/orders` 回傳空陣列，且 PAPER adapter 的 `create_order` 呼叫計數為 0 |
| 7.2 | E2E：觸發後掛單正確 | P0 | 基礎單 1 筆 + TP 1 筆；安全單張數符合活躍窗口設定；價格逐張對上 `build_ladder()` |
| 7.3 | E2E：TP 隨均價重掛 | P0 | 每張安全單成交後，事件時間線出現 `tp_replaced`，新價格對應新均價 |
| 7.4 | E2E：止盈完成 | P0 | TP 成交後狀態 `completed`，殘餘掛單全撤，持倉歸零，`realized_pnl > 0` |
| 7.5 | E2E：停止與刪除 | P0 | 停止後掛單 0 筆、持倉不動；刪除後 YAML 移除、`active_dca.json` 移除、runtime 快照清除 |
| 7.6 | E2E：審計可追溯 | P0 | `GET /api/v1/system/audit` 可查到 create/start/stop/delete 四筆，含 `command_id` 對應 |
| 7.7 | 詳情資料一致 | P0 | 詳情頁的均價/持倉/盈虧與 snapshot 卡片完全一致（同一讀模型） |
| 7.8 | 階梯表狀態正確 | P1 | 已成交/掛單中/未觸及三態正確；當前價與均價標線位置正確 |
| 7.9 | 長列表處理 | P1 | 50 張安全單 + 200 筆成交 → 分頁或虛擬化，首屏不卡頓 |
| 7.10 | 錯誤路徑覆蓋 | P0 | 下單被拒、熔斷開啟、WS 斷線、交易所 429 → UI 顯示明確原因，**不顯示為成功或正常** |
| 7.11 | 重啟恢復 | P0 | 重啟後端 → snapshot 仍可讀到最後 runtime；重啟引擎 → `averaging` 中的 bot 能從交易所掛單狀態恢復（或明確標記 `degraded` 要求人工確認） |
| 7.12 | 並發安全 | P1 | 同時對同一 bot 送 start + stop → 命令序列化，最終狀態確定，無孤兒掛單 |
| 7.13 | 完整 CI 綠燈 | P0 | `bash scripts/ci.sh` 全綠（Python + npm test + lint + build） |
| 7.14 | 全域回歸 | P0 | 全部既有測試綠燈；`git diff --stat` 顯示網格核心檔案（`core/services/grid/*`、`run_grid_trading.py`）零變更 |
| 7.15 | 文件完整 | P1 | 使用者手冊涵蓋四種指標、階梯數學、`armed` 語意、風險說明；`config/dca/README.md` 欄位表與 §4.2 一致 |
| 7.16 | CHANGELOG | P1 | `[Unreleased]` 含 Added/Fixed/Testing 三段，列出所有新端點與新元件 |
| 7.17 | 稽核報告 | P0 | `docs/dca_bot_audit_report_zh-TW.md` 依標準 §5 五段式產出，逐項列出 Phase 1–7 稽核結果與證據 |
| 7.18 | 無憑證外洩 | P0 | `git diff` 全文 grep `API_KEY|SECRET|TOKEN|PRIVATE` 無真實值；`config/dca/*.yaml` 不含任何憑證 |
| 7.19 | 產物不進版控 | P1 | `data/`、`logs/`、`config/active_dca.json`、`node_modules/`、`dist/` 皆未被提交 |
| 7.20 | 重啟指令明示 | P1 | 交付訊息明確列出：`pm2 restart multi-grid-engine`、後端重啟、前端 `npm run build`，並說明各自由哪些檔案觸發 |
| 7.21 | LIVE 放行門檻 | P0 | 明確聲明本次交付**不開啟 LIVE**；`GRID_LIVE_TRADING_ENABLED` 維持關閉；LIVE 放行需另行完成標準 §2 的最低條件 |

---

## 6. 跨 Phase 的全域規則（每個 Phase 都要遵守）

### 6.1 絕對禁止事項

| # | 禁止事項 | 理由 |
|---|---|---|
| G-1 | 修改 `core/services/grid/**` 的任何行為 | 網格是運行中的實盤系統；DCA 是新增而非重構 |
| G-2 | 修改 `run_grid_trading.py` | 同上 |
| G-3 | 在 `requirements.txt` 新增依賴 | rule-07；四個指標純 stdlib 可實作 |
| G-4 | 前端自行計算階梯數學 | 前後端必然分岔（P0） |
| G-5 | 以 `0` 表示未知數值 | 稽核標準 A 節硬性規定 |
| G-6 | 用 `print()` 做 runtime 輸出 | rule-03 |
| G-7 | 字串拼接 SQL | rule-04 |
| G-8 | 在 `armed` 狀態下送出任何交易所寫入請求 | INV-1，本功能的定義性質 |
| G-9 | 開啟 `GRID_LIVE_TRADING_ENABLED` | 全案在 PAPER 下驗收 |
| G-10 | 直接 commit 到 `main` | rule-08；本 session 固定於 `arena/01a0386c-grid-trading-console` |

### 6.2 每個 Phase 的收尾檢查清單

```
[ ] 該 Phase 稽核表逐項 pass / fail / N/A，fail 項有例外接受紀錄
[ ] 新測試已加入 scripts/ci.sh
[ ] bash scripts/ci.sh 綠燈（或註明環境缺件與 skip 原因）
[ ] 既有測試零回歸
[ ] git diff --stat 檢查無意外檔案
[ ] CHANGELOG.md [Unreleased] 已更新
[ ] 若動到 core/ web_ui/ run_*.py config/ → 明示重啟指令
[ ] 產出五段式報告（範圍與風險 / 檢核結果 / 測試證據 / 例外接受 / 部署回滾）
[ ] 提交至 arena/01a0386c-grid-trading-console
```

---

## 7. 風險登錄簿

| ID | 風險 | 等級 | 觸發 Phase | 緩解措施 | 驗證方式 |
|---|---|---|---|---|---|
| R-1 | DCA 例外拖垮網格引擎進程 | P0 | P3 | `run_single_dca_task()` 頂層例外隔離，只結束自己 | 稽核項 3.15（注入例外測試） |
| R-2 | OHLCV 輪詢放大 API 權重 → IP BAN | P0 | P3 | 共享 `OhlcvCache`；統一 timeframe；`indicator_refresh_interval ≥ 60s` | 稽核項 3.6/3.7 |
| R-3 | TP 重掛期間出現兩張 TP（超賣） | P0 | P2 | 嚴格 cancel-then-place；失敗轉 `degraded` 重試 | 稽核項 2.4/2.5/2.6 |
| R-4 | `armed` 狀態誤掛單 | P0 | P2 | INV-1 專屬測試；client-id 命名空間可機器驗證 | 稽核項 2.1/7.1 |
| R-5 | 安全單超發 → 曝險失控 | P0 | P2 | `max_safety_orders` 硬上限 + `max_position_notional` | 稽核項 2.7/2.8 |
| R-6 | 前後端試算數字不一致，使用者依錯誤資金決策 | P0 | P4/P6 | Summary 只用後端 preview；共用 `ladder.py` | 稽核項 4.2/6.2/6.3 |
| R-7 | `direction=both` 在 one-way 帳戶造成互相平倉 | P0 | P4 | 建立時查 `get_positions()` 驗證 hedge 模式 | 稽核項 4.14 |
| R-8 | 未收線 K 棒導致重複觸發 | P1 | P1 | 丟棄最後一根 + `confirm_bars` | 稽核項 1.8/1.10 |
| R-9 | 引擎重啟後 `averaging` 中的 bot 狀態遺失 | P1 | P7 | 從交易所掛單重建狀態；無法確認時標 `degraded` 要求人工確認 | 稽核項 7.11 |
| R-10 | DCA 併發數繞過總量上限 | P1 | P4 | 總量檢查涵蓋網格 + DCA | 稽核項 4.15 |
| R-11 | 前端 tab 型別改動導致既有建置失敗 | P2 | P5 | 型別集中定義；`tsc -b` 為 gate | 稽核項 5.1 |
| R-12 | PAPER 缺 OHLCV 導致 indicator 路徑不可測 | P1 | P3 | 補確定性合成 K 線 | 稽核項 3.10 |

---

## 8. 明確排除項（v1 不做）

| 項目 | 理由 | 後續 |
|---|---|---|
| 回測（概念圖的 Backtest / Run backtest 按鈕） | 需要歷史撮合引擎 + 大量 K 線儲存，工作量約等於 Phase 1–3 總和 | 可複用 `grid_recommendation.py:_simulate_grid_backtest()` 的模式，另立計畫 |
| TradingView 圖表嵌入 | 需外部 widget 與授權評估 | 先以 `recharts`（已在依賴中）畫價格 + 階梯線 |
| 現貨 DCA | 現貨需 `SpotReserveManager` 整合，語意與合約不同 | v2 |
| `entry` 支援多 timeframe 混用 | OHLCV 成本線性放大 | 若有需求再評估 |
| Trailing take profit | 增加狀態機複雜度 | v2 |
| DCA 策略市場 / AI 推薦參數 | 需另建回測基礎 | v2 |

---

## 9. 工作量校準（為何切成 7 個 Phase）

以「單一 AI Agent 對話」為單位，約束條件為：新增/修改 **≤ ~1200 行有效程式碼**、**≤ ~12 個檔案**、且必須留出足夠預算跑測試與稽核（工具呼叫約 40–70 次）。

| Phase | 新檔 | 改檔 | 預估新增 LOC | 主要成本 | 對話量評估 |
|---|---:|---:|---:|---|---|
| P1 領域模型與指標 | 16 | 1 | ~900（含 ~350 測試） | 數學精確度、鎖定向量比對 | 1 次（飽和） |
| P2 執行引擎與狀態機 | 8 | 1 | ~1100（含 ~450 測試） | 狀態機 + FakeExchange + 22 項稽核 | 1 次（飽和，若稽核未過需第 2 次收尾） |
| P3 設定/快取/監督者 | 7 | 4 | ~800（含 ~350 測試） | 動到 `run_multiple_grids.py`，需極謹慎 + 實機重啟驗證 | 1 次 |
| P4 後端 API | 4 | 4 | ~950（含 ~350 測試） | 14 個端點 + 24 項稽核 + live curl 驗證 | 1 次（飽和） |
| P5 前端 DCA 分頁 | 6 | 3 | ~900 | React 元件 + CSS + RWD/A11y | 1 次 |
| P6 建立精靈 | 3 | 3 | ~850 | 4 步表單 + 四種指標面板 + preview 整合 | 1 次 |
| P7 詳情 + E2E + 文件 | 5 | 5 | ~800 | 詳情頁 + 完整 E2E + 稽核報告 | 1 次（可能需 2 次：一次實作、一次 E2E/報告） |

**總計**：約 49 個新檔、~6300 行新程式碼（其中 ~1500 行測試），7–9 次對話。

**建議節奏**：P1 → P2 → P3 為一組（核心引擎，完成後 PAPER 可跑通）；P4 為橋接；P5 → P6 → P7 為一組（UI）。每組結束做一次整體回歸。

---

## 10. 開工前需確認的決策點

以下四點會影響 Phase 1 的模型定義，建議在 Phase 1 開工前確認：

| # | 問題 | 建議預設 | 影響範圍 |
|---|---|---|---|
| D-1 | 「平常不顯示」是指**不掛單**（UI 仍顯示機器人與監控狀態），還是**連 UI 卡片都隱藏**至觸發後才出現？ | **不掛單**；UI 顯示「監控中」卡片，並提供「僅顯示已觸發」篩選 | §4.6 狀態機、Phase 5 稽核 5.7 |
| D-2 | `direction=both` 是兩條獨立 leg（各自攤平各自止盈），還是單一淨部位對沖？ | **兩條獨立 leg**（§4.4） | Phase 1/2/4 |
| D-3 | 止盈後預設是否自動循環？ | **否**（`repeat_after_take_profit=false`），避免非預期的無限重複下單 | §4.2-D |
| D-4 | 安全單是**全部預掛**在交易所，還是採「活躍窗口」只掛最近 N 張（比照網格 `active_window_each_side`）？ | **活躍窗口（預設 5 張）**：20 張全掛 × 多台 bot 會撞交易所單一交易對掛單上限，且長期佔用保證金 | Phase 2 稽核 2.3、§4.2-E 需新增 `active_safety_orders_window` 欄位 |

---

## 11. 附錄：Phase 1 可直接使用的驗證腳本

```python
# scripts/verify_dca_vectors.py（Phase 1 交付時一併提供）
# 不依賴任何測試框架，可直接 python3 執行，用於稽核現場快速驗證
from decimal import Decimal as D, getcontext
getcontext().prec = 28


def ladder(E, b, m, s, N, V0, tp):
    """order-averaging + fixed_value，做多"""
    E, b, m, s, V0, tp = (D(str(E)), D(str(b)) / 100, D(str(m)),
                          D(str(s)), D(str(V0)), D(str(tp)) / 100)
    p, tq, tc = E, V0 / E, V0
    rows = [(0, D(0), E, V0, tc / tq)]
    for i in range(1, N + 1):
        dev = b * m ** (i - 1)
        p = p * (1 - dev)
        v = V0 * s ** i
        tq += v / p
        tc += v
        rows.append((i, dev * 100, p, v, tc / tq))
    avg = tc / tq
    return rows, tc, (E - p) / E * 100, avg * (1 + tp)


TOL = D('1e-6')

# 向量 A
rows, required, dd, tpp = ladder(2500, 0.8, 1.2, 1.0, 20, 20, 1.0)
assert abs(rows[1][1] - D('0.8')) < TOL, rows[1][1]
assert abs(rows[20][1] - D('25.5584')) < TOL, rows[20][1]
assert abs(rows[1][2] - D('2480')) < TOL, rows[1][2]
assert abs(rows[5][2] - D('2354.614306')) < TOL, rows[5][2]
assert abs(rows[20][2] - D('496.706826')) < TOL, rows[20][2]
assert abs(rows[20][4] - D('1468.987729')) < TOL, rows[20][4]
assert abs(tpp - D('1483.677606')) < TOL, tpp
assert abs(required - D('420')) < TOL, required
assert abs(dd - D('80.131727')) < TOL, dd
print('DCA ladder vector A: PASS')

# 向量 B
rows, required, dd, tpp = ladder(2500, 1.0, 1.0, 1.5, 5, 20, 1.0)
assert abs(rows[5][2] - D('2377.475125')) < TOL, rows[5][2]
assert abs(rows[5][4] - D('2411.443413')) < TOL, rows[5][4]
assert abs(required - D('415.625')) < TOL, required
assert abs(dd - D('4.900995')) < TOL, dd
print('DCA ladder vector B: PASS')
```

指標鎖定向量的可執行版本：

```python
# scripts/verify_dca_indicators.py（Phase 1 交付時一併提供）
import math
from statistics import mean, pstdev

# 確定性輸入序列（無外部依賴，可完全重現）
V = [round(100 + 10 * math.sin(i / 7.0) + 0.15 * i, 4) for i in range(120)]
TOL = 1e-6


def ema_seq(xs, n):
    """EMA，種子為前 n 根的 SMA。"""
    k = 2 / (n + 1)
    out, e = [], None
    for i, x in enumerate(xs):
        if i < n - 1:
            out.append(None)
        elif i == n - 1:
            e = sum(xs[:n]) / n
            out.append(e)
        else:
            e = x * k + e * (1 - k)
            out.append(e)
    return out


# RSI（Wilder 平滑）
d = [V[i] - V[i - 1] for i in range(1, len(V))]
P = 14
ag = mean(max(x, 0) for x in d[:P])
al = mean(max(-x, 0) for x in d[:P])
for x in d[P:]:
    ag = (ag * (P - 1) + max(x, 0)) / P
    al = (al * (P - 1) + max(-x, 0)) / P
rsi = 100 - 100 / (1 + ag / al)

# MACD(12,26,9)
e12, e26 = ema_seq(V, 12), ema_seq(V, 26)
line = [a - b if (a is not None and b is not None) else None
        for a, b in zip(e12, e26)]
comp = [x for x in line if x is not None]
sig = [None] * (len(V) - len(comp)) + ema_seq(comp, 9)
macd_line, macd_sig = line[-1], sig[-1]
macd_hist = macd_line - macd_sig

# Bollinger(20, 2.0)，母體標準差
w = V[-20:]
mid, sd = mean(w), pstdev(w)
lo, up = mid - 2 * sd, mid + 2 * sd

# Price Change：往回第 24 根（close[119] vs close[95]），非 V[-24]
LB = 24
pc = (V[-1] - V[-1 - LB]) / V[-1 - LB] * 100
pc23 = (V[-1] - V[-1 - 23]) / V[-1 - 23] * 100

for name, got, want in [
    ('RSI(14)', rsi, 21.977841),
    ('MACD line', macd_line, -2.240581),
    ('MACD signal', macd_sig, -0.784542),
    ('MACD hist', macd_hist, -1.456039),
    ('BB lower', lo, 105.193225),
    ('BB mid', mid, 116.876210),
    ('BB upper', up, 128.559195),
    ('priceChange(24)', pc, -11.782277),
    ('priceChange(23)', pc23, -12.373066),
]:
    assert abs(got - want) < TOL, f'{name}: got {got!r}, want {want!r}'

# 暖機期首個有效索引
assert next(i for i, x in enumerate(ema_seq(V, 12)) if x is not None) == 11
assert next(i for i, x in enumerate(line) if x is not None) == 25
assert next(i for i, x in enumerate(sig) if x is not None) == 33
print('DCA indicator vectors: PASS')
```

---

## 12. 版本與變更

| 版本 | 日期 | 變更 |
|---|---|---|
| 1.0 | 2026-08-25 | 初版：架構、資料契約、7 個 Phase 與各自稽核標準、風險登錄簿 |
