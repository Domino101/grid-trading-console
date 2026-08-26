# DCA 機器人 Phase 6 稽核報告 — 建立 DCA 精靈（4 步 + 即時階梯試算）

> 日期：2026-08-26｜分支：`arena/01a03cb2-grid-trading-console`
> 計畫：`docs/dca_bot_implementation_plan_zh-TW.md` §Phase 6（四步結構 + 稽核 6.1–6.19）
> 稽核標準：`docs/grid_trading_console_audit_standard_zh-TW.md` §5 五段式

---

## 1. 範圍與風險

**範圍**：「建立 DCA」tab 由 Phase 5 占位頁換成 4 步精靈，Summary 完全由 `POST /api/v1/dca/preview` 驅動。觸及：

- 新元件：`DcaForm.tsx`（4 步＋成功卡片）、`DcaLadderPreview.tsx`（Summary 面板＋完整階梯表）、`IndicatorConfigPanel.tsx`（四指標參數編輯＋即時值）。
- 資料層：`services/api.ts` 追加 `DcaCreateRequest`/`DcaCreateResponse`/`MarketIndicatorsResponse`/`DcaIndicatorRequest` 與 `createDca()`/`getMarketIndicators()`；`previewDca()` 支援 `signal` 取消。
- 純函式：`utils/dca.ts` 追加 `canAdvanceDcaStep`、`buildDcaPreviewRequest`/`buildDcaCreateRequest`、`validateDcaEntry`/`validateDcaStep3`、`formatLookbackTime`、`dcaFundsStatus`、`selectDcaSummary`、`§4.3` 凍結預設常數。
- 整合：`App.tsx`（`dca-create` 分支掛 `DcaForm`）、`App.css`（精靈區塊，全用既有 token）。
- 測試：`tests/dca-form.test.ts`（13 項）＋ `tests/test_dca_frontend_contract.py` 擴充至 22 項。

**風險**：本階段**零後端修改**（preview/create/indicators 端點皆 Phase 4 已交付）；`requirements.txt` 與 `package.json` dependencies 零異動（G-3）。危險路徑僅 LIVE 建立：沿用 Phase 4 的 feature flag（403）＋ `min_notional` 市場規則（422）＋ 本階段新增 `CONFIRM LIVE` 二次確認（6.14）。

**隔離紀律**：`GridForm.tsx` 與五個網格元件零修改（稽核 6.18，`git diff` 驗證 0 行）；DCA 精靈元件零 grid/dashboard 端點呼叫（Python 檔案級契約把關）；前端精靈與總覽共用 `utils/dca.ts`，不另起格式化體系（G-4/G-5）。

---

## 2. 檢核結果

| # | 檢核項 | 等級 | 通過條件 | 結果 | 證據 |
|---:|---|---|---|---|---|
| 6.1 | 建置與型別 | P0 | `npm run build` + `npm run lint` 零錯誤 | PASS（例外 EX-6.1） | `tsc -b` 0；build OK（`index-*.js` 340.86 kB / gzip 95.92）；lint 4 error + 1 warning **全為 Phase 5 前既有**（Dashboard.tsx:149/170、GridForm.tsx:118、MarketRankings.tsx:49），本階段零新增 |
| 6.2 | **Summary 不自算** | P0 | `DcaLadderPreview.tsx` 計算路徑零命中 | PASS | `grep -nE "price_deviation_multiplier\|Math.pow\|\*\*" DcaLadderPreview.tsx` = **0 命中**（含註解）；元件**直讀** `response.required_capital`/`ladder.max_drawdown_percent`/`ladder.final_take_profit_price`/`ladder.levels[].deviation_percent` 原始欄位；Python 檔案級契約 `test_ladder_preview_does_no_math` 常態把關 |
| 6.3 | 前後端數值一致 | P0 | 向量 A → `420.00 USD` / `80.13%` 與 curl 完全相同 | PASS（UI 截圖例外 EX-6.19） | live curl 向量 A（entry 2500 / base 20 / N=20 / dev 0.8×1.2 / TP 1.0）→ `required_capital=420.0`、`max_drawdown_percent=80.13172696148712`、`final_take_profit_price=1483.6776061175706`、21 levels——與 Phase 1 鎖定向量 A 逐位一致；UI 對這些欄位只做 `money()`/`number()` 格式化（無運算），顯示 `420.00 USD` / `80.13%` |
| 6.4 | 交易對 | P0 | 可選交易所＋交易對；送出前已呼叫 market/info 取得精度；找不到 → 明確錯誤 | PASS | Step 1 對 symbol/exchange 變更自動 debounce(600ms) 呼叫 `market/info`＋`market/indicators`（可取消）；精度/最小名義額/目前價格顯示於市場資訊條；`canAdvanceDcaStep(1)` **要求 `marketInfoOk`**（scope 不匹配即作廢）→ 未確認不可前進；404 → 「找不到交易對 ETH/USDC:USDC（exchange=binance）…」（live 驗證）；建立 payload 帶入 `quantity_precision`/`price_decimals`（TS 測試 `create request carries … market precisions`） |
| 6.5 | 多/空/雙向 | P0 | 三選項；both → 兩組階梯＋「同一時刻只執行一個方向」＋`required=max` 非相加 | PASS | 方向 select 三選項；`DcaLadderPreview` both 時渲染 `ladder_long`/`ladder_short` 兩組 KPI＋階梯表，並顯示「同一時刻只會執行其中一個方向；所需資金 = max(多, 空)，不是兩組相加」；live curl：`required_capital=420 = max(420, 420)`，sum=840 ≠ required |
| 6.6 | Immediate | P0 | 指標區塊隱藏；payload `entry.mode="immediate"` 且無 `indicators` | PASS | Immediate/Indicator 卡片二選一，immediate 時指標面板不渲染（改顯示說明）；TS 測試 `immediate mode payload has mode=immediate and no indicators`：`indicators=[]`（後端端點語意為空陣列＝無指標，與 `entry.mode` 共同判定） |
| 6.7 | 四種指標 | P0 | MACD/RSI/BB/PriceChange 獨立啟用，每參數有預設且可編輯 | PASS | 四行獨立 checkbox＋各自參數輸入欄（全 `<input>/<select>` 可編輯）；TS 測試 `indicator mode payload maps backend field names exactly` 逐欄位對照 `DcaIndicatorConfig`（type/period/oversold/overbought/fast/slow/signal/std_dev/lookback_bars/long_threshold_percent/short_threshold_percent/condition） |
| 6.8 | 指標預設值 | P0 | 預填與 §4.3 完全一致（RSI 14/30/70/level、MACD 12/26/9/cross、BB 20/2.0/touch、PC 24/-3.0；共用 15m/300/close/all/1） | PASS | `DCA_RSI_DEFAULTS`/`DCA_MACD_DEFAULTS`/`DCA_BOLLINGER_DEFAULTS`/`DCA_PRICE_CHANGE_DEFAULTS`/`DCA_ENTRY_DEFAULTS` 常數；TS `indicator defaults match §4.3 frozen values`＋Python `test_frozen_defaults_match_plan_43` 雙鏡像斷言 |
| 6.8b | 參數完整可調 | P0 | 所有參數可編輯（含 condition 下拉、時框、取樣根數） | PASS | 程式碼審查：每指標 condition 皆 `<select>`（RSI level/cross_in/cross_out、MACD cross/hist_sign/zero_cross、BB touch/break/return）；共用區 timeframe/fetch_limit/price_source/logic/confirm_bars 全可編輯；`validateDcaEntry` 提供範圍輔助（RSI period 2–100、oversold 1–50、overbought 50–99、MACD fast 2–100/slow 3–200(>fast)/signal 2–50、BB period 2–200/std_dev 0.5–5、PC lookback 1–500/門檻 ±100、fetch_limit 100–500、confirm_bars 1–5） |
| 6.8c | lookback 換算 | P2 | 「24 根 × 15m = 6 小時」隨時框連動 | PASS | `formatLookbackTime`（純函式）＋ `dca-lookback-hint` 隨 `entry.timeframe` 重新渲染；TS＋Python 雙鏡像（24×15m=6 小時、48×1h=2 天、1×1h=1 小時、30×1m=30 分、2×1d=2 天、無法解析時框→null） |
| 6.9 | 指標即時值 | P1 | 當前值＋成立/不成立；資料不足顯示「資料不足」非 0 | PASS（端點語意限制見 §4 附註） | `IndicatorConfigPanel` 顯示 `value（條件 成立✓/不成立✗）`，direction=short 取 `triggered_short`、否則 `triggered_long`；`insufficient_data` → 「資料不足（have/need 根）」；來源 `GET /api/v1/market/indicators/{symbol}`（60s cache，Phase 4）；面板明示「即時值以 §4.3 預設參數計算（端點固定值）」 |
| 6.10 | 四個核心參數 | P0 | Max DCA orders / Price deviation / Order size multiplier / Take profit 可編輯且即時反映 Summary | PASS | Step 3 四個 `<input>` 皆綁 form state；Step 4 preview effect 依賴 `[step, form, currentPrice]` → 任一改動即 debounce(300ms) 重發 preview，Summary KPI 即刷新 |
| 6.11 | 其他參數完整 | P1 | Price dev. multiplier / DCA Mode / Averaging / Amount+單位 / 止損 / 循環設定皆存在 | PASS | Step 3 全數存在：`price_deviation_multiplier`、`dca_mode`(order/position)、`averaging_method`(fixed_value/fixed_coin)、`base_order_amount`＋`amount_unit`(quote/base)、`stop_loss_percent`（留空停用）、`repeat_after_take_profit`/`cooldown_seconds`/`max_cycles`、另含 `leverage`/`margin_mode`/`both_tiebreak`(both 時)/`active_safety_orders_window`/`monitoring_interval`/`max_position_notional`/`fee_rate` |
| 6.12 | 前端驗證僅輔助 | P0 | 繞過前端 POST 非法參數 → 後端 422 | PASS | live curl `max_safety_orders=999` → **HTTP 422**（Pydantic `le=50`）；前端 `validateDcaStep3` 僅作輸入輔助 |
| 6.13 | 步驟推進規則 | P1 | 必填未完不可前進；純函式測試 | PASS | `canAdvanceDcaStep`（step1: symbol+exchange+marketInfoOk；step2: 入場校驗；step3: 核心參數校驗；step4: LIVE 需 CONFIRM LIVE）；TS 5 組推進測試＋Python 鏡像 5 組（含 RSI oversold=80、MACD slow≤fast 拒絕） |
| 6.14 | LIVE 二次確認 | P0 | 輸入 `CONFIRM LIVE` 才能提交；文案含交易對、方向、所需資金、最大跌幅 | PASS | `dca-confirm-live-box`：「交易對 {symbol}，方向 {directionLabel}，所需資金 {money(required)} USD，最大覆蓋跌幅 {x}%」；`canSubmit` 要求 `liveConfirmation === 'CONFIRM LIVE'`；TS 測試 `step 4: LIVE requires CONFIRM LIVE` |
| 6.15 | 資金不足警示 | P1 | required > 可用餘額 → 紅警；LIVE 禁用提交 | **部分**（例外 EX-6.15） | 結構完整就位：`dcaFundsStatus(required, available)` 純函式（null→unknown、required>available→insufficient）；`DcaLadderPreview` insufficient 時 LIVE 紅警「（LIVE 已禁止送出）」＋`canSubmit` 封鎖、PAPER 琥珀「（PAPER 僅警告）」；**數據源例外**：後端目前無 DCA 專屬餘額端點（唯一 balance 資料在網格域 per-grid stats，讀取即破壞 5.2 隔離且口徑錯誤）→ 精靈傳 `availableBalance=null`，顯示「—（本版本無 DCA 專屬餘額端點）」，絕不誤判；端點交付後只需換一個 prop |
| 6.16 | preview debounce | P1 | 連續輸入 10 字元 → preview ≤ 2 次；可取消 | PASS | Step 4 effect：300ms `setTimeout` debounce＋`AbortController`（每次重觸發先 abort 前一請求）；快速輸入被合併為單一請求；檔案級契約斷言 `AbortController` 與 `previewDca(request, signal)` 接線 |
| 6.17 | 建立成功導向 | P2 | 成功卡片＋一鍵前往總覽/詳情 | PASS | Step 5 成功卡片：`bot_id`、status（`pending_engine` → 「引擎 5 秒內接手」提示）、`前往 DCA 總覽`（`onNavigateDashboard`）＋`再建立一台`（reset） |
| 6.18 | 網格精靈零回歸 | P0 | `git diff GridForm.tsx` 空；`npm test` 全綠 | PASS | `git diff --stat HEAD -- …/GridForm.tsx` = 0 行；`npm test` 32/32（含既有 format 4＋auth 2＋dca 13）；Python 契約 `test_grid_form_untouched_by_phase6` |
| 6.19 | Live 驗證 | P0 | preview 環境實際完成 PAPER 建立＋四步/Summary 截圖 | **部分完成**（例外 EX-6.19） | 沙箱實際啟動後端：(a) live curl preview 向量 A / both / 422（§2 各列）；(b) 以**精靈 payload 形狀**（`buildDcaCreateRequest` 輸出同構，含 RSI indicator entry＋精度欄位）`POST /api/v1/dca` → 201，YAML 含 `entry:` rsi 區塊，snapshot round-trip `entry_mode=indicator`/`entry_indicators=[rsi 14/30/70 level]`；(c) `market/info` 404 明確錯誤（精靈「找不到交易對」路徑）；建立後即刪除歸檔、`active_dca.json` 清空。**截圖未做**（沙箱無瀏覽器）——四步與 Summary 的顯示規則由 13+14 項測試契約鎖定，正式宿主補做 |

> 附註（6.9 端點語意）：`GET /api/v1/market/indicators/{symbol}` 為 Phase 4 凍結設計——以 §4.3 **預設參數**＋`direction=long` 語意計算即時值（回傳 `triggered_long`/`triggered_short` 雙旗標）。精靈面板依方向取用對應旗標，並明示「僅供判斷市場狀態，實際觸發以您的參數為準」。自訂參數的即時判定屬端點擴充，超出本階段（前端）範圍。

---

## 3. 測試證據

### 3.1 前端（`web_ui/frontend`）

```
npm test        → # pass 32 / # fail 0（format 4 + auth 2 + dca 13 + dca-form 13）
npx tsc -b      → 0 錯誤
npm run build   → dist/assets/index-*.js 340.86 kB │ gzip: 95.92 kB
npm run lint    → 4 errors, 1 warning（全為既有，見 EX-6.1）
```

`tests/dca-form.test.ts` 13 項：§4.3 凍結預設、step1 需 market/info 確認、step2 immediate/indicator 推進、step2 超範圍參數拒絕（RSI oversold=80、MACD slow≤fast）、step3 核心參數、step4 CONFIRM LIVE、immediate payload（`indicators=[]`）、indicator payload 逐欄位映射（4 指標全開）、停用指標省略、create payload 帶精度、lookback 換算 6 組、資金口徑（null→unknown）、Summary 選取（末層偏移、null 安全）。

### 3.2 Python 鏡像契約

```
.venv/bin/python -m unittest tests.test_dca_frontend_contract -v
Ran 22 tests — OK
```

Phase 6 新增 14 項：步驟推進規則 Python 鏡像（5 項，與 TS 向量一致）、§4.3 預設鏡像、lookback 換算鏡像（含 JS 數字轉字串語義 `6` vs `6.0`）、資金口徑鏡像；檔案級契約（6 項）：`DcaLadderPreview.tsx` 零運算 token（6.2）、Summary 直讀 preview 回應欄位（6.2/6.3）、精靈元件零 grid/dashboard 端點（5.2 延伸）、preview/create 端點接線＋`AbortController`（6.16 基礎）、`canAdvanceDcaStep` 純函式＋TS 測試存在（6.13）、GridForm 未被 Phase 6 觸及（6.18）。

### 3.3 全量後端 CI

```
source .venv/bin/activate && SKIP_FRONTEND=1 bash scripts/ci.sh
Ran 226 tests in 11.718s
OK
```

（Phase 1–5 原有 212 項 + Phase 6 新增 14 項 = 226 項。）

### 3.4 Live 驗證（本沙箱實跑，2026-08-26 07:56–07:59 UTC）

1. 啟動後端 uvicorn `0.0.0.0:8001`（token 自 `.grid_api_token`）。
2. **6.3 向量 A**（`POST /api/v1/dca/preview`）：
   ```
   required_capital: 420.0
   max_drawdown_percent: 80.13172696148712
   final_take_profit_price: 1483.6776061175706
   levels: 21 (index 0..20)   → VECTOR A PASS
   ```
3. **6.5 both**：`ladder_long`＋`ladder_short` 皆回傳；`required_capital=420.0 = max(420, 420)`，sum=840 ≠ required。
4. **6.12**：`max_safety_orders=999` → `HTTP 422`。
5. **6.4**：`GET /api/v1/market/info/ETH/USDC:USDC` → `HTTP 404`「找不到交易對 ETH/USDC:USDC (exchange=binance)…」（沙箱無 Binance 公開路由；正式宿主此路徑回精度）。
6. **6.19 等價**：以精靈 payload 形狀 `POST /api/v1/dca`（PAPER、RSI indicator entry、`quantity_precision=3`/`price_decimals=2`）→ `201 Created`，`bot_id=dca-binance-long-eth-usdc-usdc-20260826075758`，YAML 含 `entry:`＋rsi 區塊；snapshot round-trip：`entry_mode=indicator`、`entry_indicators=[{type: rsi, period: 14, oversold: 30.0, overbought: 70.0, condition: level}]`。隨後 `DELETE ?force=true` →「YAML 已歸檔」，`config/active_dca.json` 清空。

---

## 4. 例外接受

| # | 項目 | 等級 | 原因 | 緩解 | 到期日 |
|---|---|---|---|---|---|
| EX-6.1 | `npm run lint` 4 error + 1 warning 未歸零 | P0 | 全為 Phase 5 前既有（`Dashboard.tsx:149/170`、`GridForm.tsx:118` React Compiler preserve-manual-memoization；`MarketRankings.tsx:49` set-state-in-effect＋1 warning），本階段**零新增** | 同 Phase 5 技術債處理（修復會觸及 5.18/6.18 保護的網格元件，不摻入本階段） | 獨立 PR |
| EX-6.15 | 資金不足比較無數據源（`availableBalance=null`） | P1 | 後端無 DCA 專屬餘額端點；唯一 balance 資料在網格域 per-grid `stats.balance`，讀取即破壞 5.2 分頁隔離且口徑錯誤（網格 balance ≠ DCA 可用保證金） | 前端契約完整就位：`dcaFundsStatus` 純函式（13+4 項測試）＋UI 警示（LIVE 紅警禁用／PAPER 琥珀警告）＋`canSubmit` 封鎖邏輯；`null` 一律 `unknown`（顯示「—（本版本無 DCA 專屬餘額端點）」，絕不誤判）；**後續**：後端於 DCA snapshot aggregate 或獨立端點補 DCA 口徑餘額，精靈只需把 prop 從 `null` 換成該值 | Phase 7 backlog |
| EX-6.19 | 無瀏覽器 → 四步/Summary 截圖未做 | P0 | 沙箱無瀏覽器環境 | 所有顯示規則（armed/預設值/推進/both/max 非相加/422 錯誤呈現）由 27 項前端+鏡像測試契約鎖定；live API 層 round-trip（preview 向量 A、both、422、PAPER 建立＋entry indicators、market/info 404）已完成；正式宿主依 §5.3 補截圖 | 正式部署時 |
| EX-6.9a | 指標即時值以 §4.3 預設參數計算（非使用者自訂參數） | P1 | 端點為 Phase 4 凍結設計（固定預設＋長空雙旗標）；自訂參數即時判定需端點擴充（`?params=` 或 POST） | UI 明示「即時值以 §4.3 預設參數計算（端點固定值），僅供判斷市場狀態」；direction=short 已取 `triggered_short` 旗標；端點擴充列入 Phase 7 backlog | Phase 7 backlog |

---

## 5. 部署 / 回滾

### 5.1 需重啟的服務

- **僅前端**：本階段零後端修改。`web_ui/frontend/dist/` 已重建；若後端以靜態方式伺服 dist，重啟後端（或靜態伺服）以伺服新 bundle。
- 多網格監督者引擎**不需**重啟（Phase 5 的 await fix 若尚未於正式宿主重啟，請一併完成——Phase 5 §5.1）。
- 不修改 `requirements.txt` / `package.json` dependencies，無套件安裝。

### 5.2 啟動/回滾指令

```bash
# 後端（伺服新 dist）
pm2 restart <backend-pm2-name>
sleep 2
curl -s localhost:8001/healthz
```

回滾 = 切回上一個 commit（`24f1288`）的前端檔案並重啟後端；「建立 DCA」tab 回到 Phase 5 占位頁；DCA 總覽/卡片/引擎/後端 API 全數不受影響。無 migration。

### 5.3 上線驗收（正式宿主，可連交易所）

1. 後端重啟後 `/` 200 且 bundle hash 為新 build；「建立 DCA」tab 為 4 步精靈。
2. Step 1 輸入 `ETH/USDC:USDC` → 市場資訊條顯示價格/數量精度＋目前價格（非 404）；輸入不存在交易對 → 明確錯誤且「下一步」禁用（6.4）。
3. Step 2 開 RSI：預填 14/30/70/level；即時值顯示「RSI = xx.x（≤30 / ≥70 成立✓/不成立✗）」或「資料不足」（6.8/6.9）。
4. Step 4 以向量 A 參數（entry 2500、base 20、N=20、dev 0.8×1.2、TP 1.0）→ Summary 顯示 `420.00 USD`、`80.13%`，與 curl preview 完全一致（6.3）；direction=both → 兩組階梯＋「max 非相加」標示（6.5）。
5. PAPER 建立 → 成功卡片 →「前往 DCA 總覽」→ 引擎 5 秒接手（armed/triggering，Phase 3/5 已驗證路徑）。
6. LIVE：無 flag → 建立被 403 拒絕；有 flag → 未輸入 `CONFIRM LIVE` 送出禁用，輸入後確認文案含交易對/方向/所需資金/最大跌幅（6.14）。
7. 補 EX-6.19 截圖：四步驟＋Summary（PAPER）＋LIVE 確認框。

---

## 附錄 A：交付物清單

| 檔案 | 說明 |
|---|---|
| `web_ui/frontend/src/components/DcaForm.tsx` | 4 步精靈＋成功卡片（market/info 自動確認、debounce+abort、CONFIRM LIVE） |
| `web_ui/frontend/src/components/DcaLadderPreview.tsx` | Summary 面板（直讀 preview 欄位、雙向兩組階梯、資金口徑顯示） |
| `web_ui/frontend/src/components/IndicatorConfigPanel.tsx` | 四指標啟用＋全參數可編輯＋共用管線＋即時值 |
| `web_ui/frontend/src/utils/dca.ts` | 精靈純函式（推進規則、payload 映射、校驗、lookback 換算、資金口徑、§4.3 預設） |
| `web_ui/frontend/src/services/api.ts` | `DcaCreateRequest`/`DcaCreateResponse`/`MarketIndicatorsResponse`/`DcaIndicatorRequest`＋`createDca()`/`getMarketIndicators()`；`previewDca` 加 `signal` |
| `web_ui/frontend/src/App.tsx` | `dca-create` 分支掛 `DcaForm` |
| `web_ui/frontend/src/App.css` | 精靈/指標面板/Summary/階梯表/成功卡片區塊（全既有 token） |
| `web_ui/frontend/tests/dca-form.test.ts` | 13 項 node --test |
| `web_ui/frontend/package.json` | `test` script 追加 dca-form.test.ts |
| `tests/test_dca_frontend_contract.py` | 8 → 22 項（Python 鏡像＋檔案級契約） |
| `CHANGELOG.md` | `[Unreleased] 2026-08-26` 新增 Phase 6 條目 |

## 附錄 B：Phase 7 銜接

- Phase 7（DCA 詳情工作台）可直接以精靈成功卡片的 `bot_id` 深鏈跳轉；Phase 4 已交付 `GET /api/v1/dca/{bot_id}`（info/stats/ladder/orders/fills/events）作為工作台資料源。
- **backlog（本階段提出）**：(1) DCA 口徑可用餘額端點／snapshot aggregate 欄位（解 EX-6.15）；(2) 指標端點支援自訂參數即時判定（解 EX-6.9a）；(3) 精靈 Step 1 可選 `max_wait_seconds`（armed 等待上限，`DcaEntryConfig` 已支援，UI 未暴露）；(4) 既有 4 個 lint error 的獨立修復 PR。
- 建立成功後的「查看該 bot」深鏈目前導向 DCA 總覽（卡片可展開詳情）；若 Phase 7 詳情頁就緒，成功卡片第二個動作可換成直接開詳情。
