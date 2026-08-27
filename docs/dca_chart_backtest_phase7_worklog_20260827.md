# Phase C7 — 前端回測工作台（回測面板 + 結果 + 掃描比較）Worklog

日期：2026-08-27
分支：`arena/01a04324-grid-trading-console`
計畫依據：`docs/dca_chart_backtest_plan_zh-TW.md` § Phase C7

---

## 1. 目標與範圍

讓使用者能對**既有機器人**或**精靈中尚未送出的 draft 參數**執行歷史回測與參數掃描，
並在同一張圖上看到虛擬下單標記與權益曲線。

範圍內：
- 回測面板（時框 / 棒數 / 初始資金 / 執行 + 常駐簡化事項揭露）
- 回測結果（指標卡 + 複用 C6 的 `DcaChart` + 輪次分頁表）
- 參數掃描比較表（排序 / 後端最優列高亮 / truncated 誠實提示）
- 精靈 Summary 步驟的「先回測看看」

範圍外（維持 E-6.2 隔離）：**未改動 `DcaChart.tsx`**。C6 已預留 `equity` prop 與第二窗格，C7 直接傳入。

---

## 2. 交付物

| 檔案 | 狀態 | 說明 |
|---|---|---|
| `src/components/DcaBacktestPanel.tsx` | 新增 | 參數輸入 + 執行/取消；`BACKTEST_DISCLOSURES` 常駐；執行中禁用 + `Loader2` spinner |
| `src/components/DcaBacktestResult.tsx` | 新增 | 8 張 MetricCard、lazy `DcaChart`、warnings 全量渲染、輪次表 `CYCLE_PAGE_SIZE=50` |
| `src/components/DcaSweepTable.tsx` | 新增 | 5 個可排序指標欄、後端 `best` 高亮列、truncated 警示 |
| `src/utils/dca.ts` | 修改 | append C7 純函式區塊（格式化 / 錯誤文案 / 排序），**不做金融計算** |
| `src/components/DcaDetail.tsx` | 修改 | `TabType` +`'backtest'`；回測分頁（單次回測 + 參數掃描兩區） |
| `src/components/DcaForm.tsx` | 修改 | Summary 步驟「先回測看看」，送 draft |
| `src/services/api.ts` | 修改 | C7 契約型別 + `runDcaBacktest` / `runDcaSweep` |
| `src/App.css` | 修改 | C7 樣式（沿用既有 `--*` token，未新增 token） |
| `tests/dca-backtest.test.ts` | 新增 | 30 條測試 |
| `package.json` | 修改 | test script 登記新測試檔 |

`scripts/ci.sh` 無需修改：其前端段呼叫 `npm test`，新檔已透過 `package.json` 納入。

---

## 3. 稽核結果（7.1 – 7.10）

| # | 項目 | P | 結果 | 佐證 |
|---|---|---|---|---|
| 7.1 | 零本地計算 grep 0 命中 | P0 | ✅ | `grep -E 'required_capital\|price_deviation\|take_profit_price'` 於三個 C7 元件 = 0；並由測試 `7.1 backtest components never recompute...` 自動把關 |
| 7.2 | warnings 全部渲染、條目數 = API 數 | P0 | ✅ | 實測 API 回 4 條；元件 `warnings.map` 全量；測試斷言無 `slice`/`filter` 截斷 |
| 7.3 | `total_return_pct` 與 `buy_hold_return_pct` 並排 | P0 | ✅ | 兩張卡並排 + `alpha_pct`；alpha<0 走警示色 |
| 7.4 | truncated 顯示已完成/總組數且含「已完成範圍內」 | P0 | ✅ | `sweepTruncatedText(true,6,24)` → 含 `6/24`、`已完成範圍內`、`並非全域最佳` |
| 7.5 | 執行中禁用 + spinner、切分頁 abort | P1 | ✅ | 面板 4 處 `disabled={running}` + `Loader2 spin`；`btAbortRef`/`swAbortRef` 於離開分頁與卸載時 abort |
| 7.6 | 400/408/429/unavailable 皆有中文訊息且不渲染空結果圖 | P0 | ✅ | `backtestErrorText` 6 種錯誤碼文案互異；渲染守衛 `!btError && btResult` |
| 7.7 | `profit_factor=null` → 顯示「—」而非 0 | P0 | ✅ | 實測 API 確實回 `null`；`backtestNumberText(null)==='—'`、`(0)==='0.00'` 分離驗證 |
| 7.8 | 輪次表 >50 筆分頁 | P1 | ✅ | `CYCLE_PAGE_SIZE=50` + `dca-pagination` |
| 7.9 | 「先回測看看」送 draft 而非已建立 bot | P1 | ✅ | 複用 `buildDcaPreviewRequest(form, currentPrice)` 組 `draft`；e2e 實測 draft 路徑回傳與 bot 路徑**不同**的指標 |
| 7.10 | build 0 error、lint 不新增 error | P0 | ✅ | build ✓；lint 0 error / 1 warning（`MarketRankings.tsx`，既有且無關） |

### 驗收指令輸出

```
npm test   → # tests 90  # pass 90  # fail 0
npm run lint → 0 errors, 1 warning（既有）
npm run build → ✓ built，0 error
npx tsc -b --noEmit → clean
```

Bundle（lazy 分割維持）：
```
DcaSweepTable-*.js        4.24 kB │ gzip:  1.72 kB
DcaBacktestResult-*.js    6.05 kB │ gzip:  2.38 kB
DcaChart-*.js           178.11 kB │ gzip: 57.93 kB   ← 與 C6 相同，未污染
index-*.js              374.25 kB │ gzip:105.59 kB   ← +1.11 kB gzip
GridDetail-*.js         348.00 kB │ gzip:102.21 kB   ← 不變
```

Python 契約：`tests/test_dca_frontend_contract.py` **41/41 通過**。

---

## 4. 端點實機驗證（非僅靜態檢查）

透過 Vite proxy → 後端 :8001 實打：

- `POST /v1/dca/backtest`（bot_id 路徑）→ 200；warnings 4 條、equity 300、marks 4、cycles 2；
  `total_return_pct 0.0524` / `buy_hold −4.2121` / `alpha +4.2645`；**`profit_factor: null`**（7.7 真實案例）。
- `POST /v1/dca/backtest` （draft 路徑）→ 200，回傳 `total_return_pct −2.6456`，與 bot 路徑不同 → 證明 7.9 送的確實是表單參數。
- `POST /v1/dca/backtest/sweep` 3×2 → 200；`runs 6`、`truncated false`、
  `best {metric: total_return_pct, value: 0.0372, params:{take_profit_percent:1.0, max_safety_orders:2}}`。
- 錯誤路徑：`timeframe=7m` → **400**；不存在 bot → **404**；`bars=20001` → **422**（pydantic `le` 先於 route 守衛）。
  422 已納入 `backtestErrorText`。

---

## 5. 問題與決策

1. **`DcaSweepTable` 一度成為孤兒元件。**
   元件寫完後，`grep -rn "DcaSweepTable"` 顯示除自身外**零引用** — 掃描功能等於沒交付。
   已在 `DcaDetail` 回測分頁補上完整掃描區（兩條掃描軸輸入、`best_by` 選擇、組數預覽、取消鈕）並 lazy 載入。
   *教訓：新元件必須驗證「被誰 render」，編譯通過不代表已接線。*

2. **測試抓到一個真實的斷言錯誤。**
   7.3 契約測試最初以 `metrics.total_return_pct` 比對原始碼而失敗，因元件將 `result.metrics` 取別名為 `m`。
   已改為 `\b(metrics|m)\.` 並追加 `alpha_pct < 0` 警示色斷言。此為測試本身的缺陷，非產品缺陷。

3. **沿用 C1–C6 既有地雷的解法**，未重蹈：
   - 一律 `web_ui.backend.*` 絕對匯入（`from schemas import` 會造成 pydantic 類別不一致 → 500）。
   - grep 契約先 `stripComments` 再比對，而非改寫原始碼措辭。
   - 不使用 v5 已移除的 `addCandlestickSeries` / `setMarkers`。

4. **CSS 未新增設計 token。** C7 樣式全部復用既有 `--bg-input`/`--trading-green`/`--accent-amber` 等；
   已逐一驗證 15 個引用 token 皆存在於 `App.css`/`index.css`，無未定義變數。

### 遺留事項（不屬 C7 範圍）

- `/chart` 的 warnings 重複輸出「使用合成 K 線，非真實歷史」×2，尚未去重。
- `tests/test_dca_backtest_http_contract.py` 在此沙箱有 9 個 `setUpClass` ERROR
  （fixture 用了 `exchange: paper`，非合法值）。已用 `git stash` 比對確認**改動前後完全相同**，
  屬既有基線問題，非 C7 回歸。
