# DCA 圖表回測 Phase C6 — 工作日誌

**Phase**: C6 — 前端圖表基礎（lightweight-charts 封裝 + 即時圖表）
**日期**: 2026-08-27
**分支**: `arena/01a04324-grid-trading-console`
**計畫文件**: `docs/dca_chart_backtest_plan_zh-TW.md` §5 Phase C6
**前置 Phase**: C1（K 線層）| C2（ReplayExchange）| C3（ReplayDriver）| C4（指標與掃描）| C5（後端 API）

---

## 1. 範圍與風險

### 1.1 本 Phase 交付範圍

| 檔案 | 類型 | 說明 |
|---|---|---|
| `web_ui/frontend/package.json` | 改 | +`lightweight-charts@^5.2.1`（D-6）；test script 登記新測試 |
| `web_ui/frontend/package-lock.json` | 改 | E-6.5：可重現安裝，一併提交 |
| `web_ui/frontend/src/components/DcaChart.tsx` | **新** | 唯一允許 import 圖表函式庫的檔案（E-6.2） |
| `web_ui/frontend/src/utils/dcaChart.ts` | **新** | 純函式：candles→series、marks→markers、lines→priceLines（E-6.3） |
| `web_ui/frontend/src/services/api.ts` | 改 | +K 線/圖表型別 + `getKlines()` / `getDcaChart()` |
| `web_ui/frontend/src/components/DcaDetail.tsx` | 改 | `TabType` +`'chart'`，新分頁 + 15s 輪詢 + lazy chunk |
| `web_ui/frontend/src/App.css` | 改 | 12 個 `--dca-*` token（全部指向既有語意色）+ 面板樣式 + RWD |
| `web_ui/frontend/tests/dca-chart.test.ts` | **新** | 27 項純函式測試 |
| `tests/test_dca_frontend_contract.py` | 改 | +`DcaChartSourceContractTests`（13 項檔案級契約） |
| `web_ui/backend/routes/dca.py` | 改 | **計畫外必要修復**：`/chart` 端點 500（見 §4） |
| `CHANGELOG.md` | 改 | `[Unreleased]` Added + Fixed |

### 1.2 風險處置

| 風險 | 等級 | 本 Phase 處置 | 證據位置 |
|---|---|---|---|
| **RC-12** 圖表記憶體洩漏 | P1 | cleanup 呼叫 `chart.remove()` + `observer.disconnect()`；price line 以 ref 保存並在 props 變動時 `removePriceLine()` 後重建 | §2 稽核 6.4 |
| **RC-13** 新依賴擴散到其他頁面 | P2 | 契約測試以 `os.walk` 掃描整棵 `src/`，斷言命中集合**恰好等於** `{components/DcaChart.tsx}` | §2 稽核 6.7 |
| G-4 前端自算數學 | P0 | 圖表兩檔剝除註解後 grep 六個禁用符號 0 命中 | §2 稽核 6.2 |
| G-5 未知顯示為 0 | P0 | `price=null` 不畫線、`realized_pct=null` 省略百分比；同時鎖定「真實 0 仍要顯示」 | §2 稽核 6.3 |
| 首屏被新依賴拖慢 | P1 | `lazy()` 動態匯入 → 獨立 chunk；實測 `index-*.js` **反而 −0.31 kB** | §2 稽核 6.10 |

### 1.3 明確未做（留給 C7）

回測工作台三元件（`DcaBacktestPanel` / `DcaBacktestResult` / `DcaSweepTable`）不在本 Phase。`DcaChart` 已預留 `equity` prop 與第二窗格實作，C7 直接傳入即可，無需再動本檔。

---

## 2. 檢核結果（Phase C6 稽核標準 12 項）

| # | 檢核項 | 等級 | 結果 | 證據 |
|---|---|---|---|---|
| 6.1 | 標記語意一致 | P0 | ✅ | `DCA_MARK_SEMANTICS` 逐行對應 §4.7；TS 測試 `6.1 mark semantics table matches the frozen §4.7 spec` 以六元組陣列整批斷言 shape/position/colorVar；做空翻轉、TP/SL 方向無關各有獨立測試 |
| 6.2 | 前端零計算 | P0 | ✅ | `test_c6_2_chart_files_do_no_math`：剝除註解後對 `DcaChart.tsx` / `dcaChart.ts` grep `price_deviation_multiplier` / `price_deviation_percent` / `required_capital` / `take_profit_price` / `Math.pow` / `**` 全部 0 命中 |
| 6.3 | 未知不畫 0 | P0 | ✅ | `toPriceLineSpecs` 對 null/undefined/NaN 一律丟棄（測試斷言 5 條線只剩 1 條）；`markerText` 對 `realized_pct=null` 回 `'TP'` 而非 `'TP +0.00%'`；**同時鎖定 `chartPercentText(0) === '+0.00%'`**，確保「未知」與「零」不被混為一談 |
| 6.4 | 記憶體回收 | P0 | ✅ | `test_c6_4_chart_cleanup_removes_chart` 斷言 `chart.remove()` / `removePriceLine(` / `observer.disconnect()` 皆存在；price line effect 另有 cleanup 清空 ref |
| 6.5 | 輪詢與取消 | P1 | ✅ | effect 首行 `if (activeTab !== 'chart') return;`；cleanup `clearTimeout` + `clearInterval(timer)` + `chartAbortRef.current?.abort()`。`CHART_REFRESH_MS = 15_000` 與 `DcaDashboard.tsx` 同節奏（契約測試比對兩檔） |
| 6.6 | armed 語意 | P0 | ✅ | `filterMarksForStatus(marks, 'armed')` 濾除全部 `order_placed`；TS 測試另做 round-trip 斷言「armed + order_placed → 圖上 0 個 marker」；UI 同步顯示「監控中 · 交易所零掛單」 |
| 6.7 | 依賴隔離 | P0 | ✅ | `grep -rl 'lightweight-charts' src/` → **1**（`components/DcaChart.tsx`）。`git diff --stat ... GridDetail.tsx` 為**空**；契約另斷言網格頁仍含 `recharts` 且不含 `DcaChart` |
| 6.8 | 資料不足誠實 | P0 | ✅ | `chartDataNotice`：`unavailable` → 「無法取得 K 線資料」；`live` 但 0 棒 → 「資料不足，尚無可繪製的 K 棒」。UI 以文字取代圖表，不渲染空 canvas |
| 6.9 | 合成資料標示 | P0 | ✅ | `source=paper_synthetic` → 角標「合成資料」（實機回應確為此值）；`stale` → 「陳舊資料」 |
| 6.10 | bundle 增量與 chunk 隔離 | P1 | ✅ | 見 §3.3 對照表。`DcaChart-*.js` 為**獨立 chunk**（gzip 57.93 kB），`index-*.js` gzip **101.58 → 101.27 kB（−0.31）** |
| 6.11 | 建置與 lint | P0 | ✅ | `npm run build` 成功；`npm run lint` **0 error / 1 warning**（既有 `MarketRankings` EX-7.1），與基線完全一致 |
| 6.12 | RWD / 可及性 | P2 | ✅ | 圖表容器 `role="img"` + `aria-label`（含交易對與時框）；分頁鈕 `role="tab"` + `aria-selected`；時框鈕 `aria-pressed`；`@media (max-width: 640px)` 工具列縱向堆疊 |

**退出條件**：12 項全 pass。

### 2.1 BP-1 / BP-2 契約複驗（計畫 §11.2）

```text
✅ BP-1 契約通過        （backtest/ 無第二套執行器或階梯數學）
✅ 前端零計算通過        （圖表兩檔 0 命中）
✅ E-6.2 隔離通過        （grep -rl 命中數 = 1）
網格零回歸：git diff --stat core/services/grid/ run_grid_trading.py GridDetail.tsx → 空
```

---

## 3. 測試證據（全部為實際執行輸出）

### 3.1 前端測試

```text
$ npm test
# tests 63
# pass 63
# fail 0
```

基線 36 項 + C6 新增 27 項 = 63，**零失敗**。

### 3.2 後端 Python 契約

```text
$ python3 -m unittest tests.test_dca_frontend_contract
Ran 41 tests in 0.007s
OK
```

基線 28 項 + C6 新增 13 項 = 41。

### 3.3 建置與 bundle 對照（稽核 6.10）

```text
$ npm run build
dist/index.html                        0.55 kB │ gzip:   0.33 kB
dist/assets/index-*.css               45.84 kB │ gzip:   8.44 kB
dist/assets/jsx-runtime-*.js           8.53 kB │ gzip:   3.26 kB
dist/assets/DcaChart-*.js            178.12 kB │ gzip:  57.93 kB   ← 新獨立 chunk
dist/assets/GridDetail-*.js          348.00 kB │ gzip: 102.20 kB
dist/assets/index-*.js               360.47 kB │ gzip: 101.27 kB
✓ built in 766ms
```

| 產物 | §5.0 基線 gzip | C6 後 gzip | 增量 | 判讀 |
|---|---:|---:|---:|---|
| `index-*.js`（首屏） | 101.58 kB | **101.27 kB** | **−0.31** | 未受影響（拆出 jsx-runtime 後略減） |
| `GridDetail-*.js` | 102.20 kB | **102.20 kB** | **0.00** | 網格頁完全未動（E-6.1） |
| CSS | 8.16 kB | **8.44 kB** | +0.28 | 圖表面板樣式 |
| `DcaChart-*.js` | — | **57.93 kB** | 新增 | **僅開啟圖表分頁時載入** |

結論：新依賴**未進入首屏**，符合稽核 6.10 的 chunk 隔離要求。

### 3.4 Lint

```text
$ npm run lint
  MarketRankings.tsx  52:6  warning  ... react-hooks/exhaustive-deps
✖ 1 problem (0 errors, 1 warning)
```

與 §5.0 基線逐字一致（既有 EX-7.1，非本 Phase 引入）。

> 過程記錄：初版輪詢 effect 在 body 內同步呼叫 `loadChart()`，觸發 `react-hooks/set-state-in-effect` **error**。已改為沿用本檔既有 `load` 的 `setTimeout(0)` 延後模式修正，非以停用規則的方式繞過。

### 3.5 全量後端 CI 與零回歸判定

沙箱依 §5.0 先行 `pip install --break-system-packages injector pyyaml ccxt fastapi uvicorn httpx websockets eth_account rich`。

```text
$ SKIP_FRONTEND=1 bash scripts/ci.sh      # C6 之後
Ran 306 tests in 12.153s
FAILED (failures=15, errors=3)

$ git stash -u && SKIP_FRONTEND=1 bash scripts/ci.sh   # C6 之前（同一沙箱）
Ran 292 tests in 12.021s
FAILED (failures=16, errors=3)
```

**逐項 diff（比對 `FAIL:`/`ERROR:` 名稱集合）**：

```text
=== 新增的失敗（本 Phase 造成）===
（空）
=== 修好的 ===
FAIL: tests.test_dca_backtest_http_contract...test_5_9_frontend_zero_calculation_grep
=== 既有失敗數: 25 → 現在: 24 ===
```

**判讀（誠實揭露）**：分支基線在本沙箱**本來就是紅的**（24 項既有失敗，多為 C1/C5 遺留，如 `test_audit_1_12_http_endpoint_contract` 期望 400 實得 401 的認證順序問題）。這些**不是** C6 造成，本 Phase 也未擅自修改 C1/C5 的測試去粉飾。C6 的可驗證結論是：**新增失敗 0 項，且順帶讓 C5 的 `test_5_9_frontend_zero_calculation_grep` 由紅轉綠**（該測試原本就在等 `utils/dcaChart.ts` 出現）。

> 註：§5.0 記錄的「234 全綠」基線是 C1 之前的狀態；C1–C5 之後基線已劣化，建議 C8 專門收斂這 24 項。

### 3.6 Live 實機驗證（AGENTS.md / rule-05）

因本 Phase 動到 `web_ui/`，依規定實際起服務驗證。

**重啟指令**：

```bash
# 後端
cd web_ui/backend && GRID_API_TOKEN=<token> python3 -m uvicorn main:app --host 0.0.0.0 --port 8001
# 前端（開發）
cd web_ui/frontend && npm run dev -- --host 0.0.0.0 --port 5173
# 生產部署則為 npm run build 後交付 dist/
```

**驗證一：`/chart` 端點直連**

```text
$ curl -H "Authorization: Bearer ***" \
    "http://127.0.0.1:8001/api/v1/dca/example-dca-paper-eth/chart?timeframe=15m&limit=50"

bot_id      : example-dca-paper-eth
candles     : 50   第一根: {'t': 1699999200, 'o': 544.61, 'h': 546.6491, 'l': 544.3121, 'c': 545.8428, 'v': 443.7787}
marks       : 0
lines       : [entry=None, average_cost=None, take_profit=None, next_safety=None, stop_loss=None]
current_px  : None
data_status : paper | source: paper_synthetic
warnings    : ['使用合成 K 線，非真實歷史', '使用合成 K 線，非真實歷史']
```

此回應正好是稽核 6.3 / 6.9 的真實案例：五條價格線 `price` 全為 `null`（該 bot 未運行、無部位）→ 前端**一條線都不畫**，而非畫五條 0 元線；`source=paper_synthetic` → 角標「合成資料」。

**驗證二：同源 proxy 路徑（前端實際走的路徑）**

```text
$ curl -o /dev/null -w "%{http_code}" http://127.0.0.1:5173/api/v1/dca/.../chart?...
200
$ proxy candles: 5
```

瀏覽器 → Vite dev server → 後端 8001 全鏈路通，前端程式碼全部使用相對路徑（契約測試斷言 `api.ts` 無 `localhost` / `127.0.0.1`）。

---

## 4. 例外與接受事項

| # | 事項 | 等級 | 說明與處置 |
|---|---|---|---|
| EX-C6.1 | **計畫外修改 `web_ui/backend/routes/dca.py`** | — | C5 遺留缺陷：`/chart` 端點內 `from schemas import KlineCandle`（函式內、非套件路徑）建立的類別與 `response_model` 的 `web_ui.backend.schemas.KlineCandle` 是兩個不同類別物件，pydantic 對每根 K 棒回報 `model_type` 錯誤 → **端點 100% 500**。C6 若不修，圖表分頁永遠是空的，稽核 6.8/6.9 無從驗證。修正範圍嚴格最小化：改為頂端統一匯入，並刪除同段落建構後即被丟棄的死碼（誤用 `DcaChartMark` 表示 K 棒）。已記入 CHANGELOG `Fixed`。 |
| EX-C6.2 | `'cross'` 形狀於函式庫邊界降級為 `square` | P2 | lightweight-charts v5 的 `SeriesMarkerShape` 僅支援 `circle`/`square`/`arrowUp`/`arrowDown`，無 `cross`。純函式表**維持 §4.7 原始語意值 `'cross'`**（契約可驗證），僅在 `DcaChart.tsx` 的 `toLibShape()` 單一邊界降級，語意由文字「略過」承載。 |
| EX-C6.3 | 沙箱後端 CI 基線非全綠（24 項既有失敗） | P1 | 非本 Phase 造成，已以前後名稱集合 diff 證明新增失敗為 0（§3.5）。不在 C6 擅改他 Phase 測試；建議 C8 專項收斂。 |
| EX-C6.4 | 記憶體「切換 20 次 canvas 數不增長」未做瀏覽器實測 | P1 | 沙箱無 headless 瀏覽器與 DOM 測試框架（前端測試為 `node --test` 純函式）。改以程式碼證據把關：契約測試斷言 `chart.remove()` / `removePriceLine()` / `observer.disconnect()` 存在，且 price line effect 具 cleanup。此為稽核 6.4 允許的「測試或程式碼審查證據」二擇一。 |
| EX-C6.5 | 沙箱 `npm audit` 有既有告警 | P3 | 安裝 `lightweight-charts` 時輸出，屬既有 devDependencies 鏈，未因本 Phase 新增。 |

---

## 5. 部署與回滾

### 5.1 部署步驟

```bash
git pull origin arena/01a04324-grid-trading-console
cd web_ui/frontend && npm ci          # package-lock.json 已含 lightweight-charts（E-6.5）
npm run build                          # 產出 dist/
# 後端（本 Phase 有 routes/dca.py 修復，必須重啟）
pm2 restart grid-web-api || (cd web_ui/backend && python3 -m uvicorn main:app --host 0.0.0.0 --port 8001)
```

### 5.2 部署後驗收（30 秒）

```bash
curl -H "Authorization: Bearer $GRID_API_TOKEN" \
  "http://127.0.0.1:8001/api/v1/dca/<bot_id>/chart?timeframe=15m&limit=50" | head -c 200
# 期望：200 + candles 非空；若 500 表示 routes/dca.py 未更新或服務未重啟
```

UI 驗收：DCA 大盤 → 任一機器人 → 「圖表」分頁 → 應見 K 線與時框切換；`armed` 機器人應見「監控中 · 交易所零掛單」且圖上零掛單標記。

### 5.3 回滾

| 情境 | 動作 | 影響 |
|---|---|---|
| 圖表分頁異常，其餘正常 | 前端回滾至前一版 `dist/`（`git revert` C6 commit 後 `npm ci && npm run build`） | 僅失去圖表分頁；六個既有分頁與網格頁**不受影響**（E-6.1 隔離的實際收益） |
| 需連 `/chart` 修復一併回滾 | `git revert` 後重啟後端 | `/chart` 回到 500 狀態（回滾前的既有行為） |
| 只想關閉圖表分頁 | 移除 `DcaDetail.tsx` 的 chart 分頁鈕 | 最小侵入；`DcaChart` chunk 不再被載入 |

**回滾風險評估：低**。C6 為純前端新增分頁 + 一處後端 500 修復；未觸及交易決策邏輯、未觸及 `config/active_dca.json` 熱插拔檔、未觸及 `core/services/grid/`。

---

## 6. 下一步（C7 銜接）

C7 需要的前置已全部就緒：

- `DcaChart` 的 `equity` prop 與第二窗格已實作並測試 → 回測結果圖直接複用，**不需再改 `DcaChart.tsx`**（E-6.2 隔離得以維持）。
- `toMarkerSpecs` / `toPriceLineSpecs` 對回測 `marks[]`（同 §4.7 kind 集合）可直接套用。
- C5 的 `/backtest` 與 `/backtest/sweep` 端點需比照 EX-C6.1 先做一次實機 `curl` 驗證，避免同類「契約測試只測 401、實際 500」的盲區。

---

## 附錄：未執行項目

- `graphify update .`：沙箱無此 CLI（`which graphify` 無結果），無法執行。依計畫 §5「不得靜默略過」原則於此記錄，需在具備該工具的環境補跑。
