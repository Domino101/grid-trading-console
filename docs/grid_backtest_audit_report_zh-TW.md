# 網格 / DCA 回測子系統 稽核報告

> 日期：2026-09-01
> 範圍：`core/services/backtest/`、`core/services/grid/backtest/`、`web_ui/backend/`（grid-backtest API）、`web_ui/frontend/`（回測分頁）
> 依據：`docs/grid_backtest_implementation_plan_zh-TW.md`
> 分支：`arena/01a05bd8-grid-trading-console`
> 驗收環境：PAPER，`GRID_LIVE_TRADING_ENABLED` 未開啟

---

## 第一段 — 範圍與風險

### 1.1 交付物

| 層 | 交付物 | 說明 |
|---|---|---|
| 通用績效 | `core/services/backtest/metrics.py` | 總報酬、買入持有、Alpha、最大回撤、勝率、獲利因子純函式 |
| 網格回測核心 | `core/services/grid/backtest/` | `models`、`allocation`、`simulator`、`risk`、`stress` |
| 網格 API | `web_ui/backend/routes/grid_backtest.py`、`grid_backtest_service.py`、`schemas.py` | `/api/v1/grid-backtest/{run,stress,sweep}` |
| DCA 整合 | `web_ui/frontend/src/components/GridBacktestPage.tsx` | 直接呼叫既有 `/api/v1/dca/backtest` + 既有 `DcaBacktestPanel/Result` |
| 前端 | `GridBacktestPage.tsx`、`api.ts`、`router.ts`、`App.tsx`、`MarketRankings.tsx` | 左側分頁 + 市場排行「回測」按鈕 |
| 測試 | `tests/services/grid/backtest/`（21 項）、`tests/test_grid_backtest_http_contract.py`（6 項）、前端 99 pass | 見第二段 |

### 1.2 承重性質

| # | 性質 | 驗證方式 | 結果 |
|---|---|---|---|
| **BT-1** | 網格回測具備**資金 / 保證金 / 權益會計**，不再只是年化估算 | `simulator.py` 逐 bar 更新 cash/margin/equity/realized/fees；測試斷言 cycle PnL | ✅ |
| **BT-2** | 網格回測**唯讀**，不下真單 | 不初始化 engine / adapter；HTTP contract 用 patch 驗證 `GridBacktestSimulator` 只被呼叫一次，無交易所寫入 | ✅ |
| **BT-3** | DCA 回測**與實盤同源**，含指標實際驅動 | 複用 `/api/v1/dca/backtest`，該路徑實例化真 `DcaCoordinator + DcaOrderExecutor + EntryMonitor` | ✅ |
| **BT-4** | 資金不足 / 爆倉都**誠實揭露** | allocation 顯示只能掛 M 格；爆倉顯示 `additional_capital_to_prevent_liquidation` | ✅ |
| **BT-5** | 既有 DCA / 網格零回歸 | `tests.services.grid.test_coordinator_stats_and_fills`、`tests.test_dca_ladder`、前端既有 99 tests 全綠 | ✅ |

### 1.3 殘餘風險（使用者必須知道）

| 風險 | 等級 | 狀態 |
|---|---|---|
| 網格保證金/清算為近似，交易所規則（funding、強平費、價格精度）不同 | P0（誤信） | 已緩解：UI / warnings / 手冊 §2.4 明確標示 |
| 網格不模擬部分成交、滑價、follow/scalping/martingale/止盈止損 | P1 | 已緩解：warnings + 手冊 |
| PAPER 合成 K 線非真實歷史 | P0（誤信） | 已標示 source/status；手冊 §4 |
| DCA 掃描/回測過度擬合單一歷史段 | P2 | 文件化 |
| NEUTRAL v1 以做多循環近似 | P2 | warnings + 手冊 |

---

## 第二段 — 檢核結果

### 2.1 測試結果

| 測試群 | 數量 | 結果 |
|---|---:|---|
| `tests/services/grid/backtest/test_allocation.py` | 6 | ✅ |
| `tests/services/grid/backtest/test_risk.py` | 7 | ✅ |
| `tests/services/grid/backtest/test_simulator.py` | 5 | ✅ |
| `tests/services/grid/backtest/test_stress.py` | 3 | ✅ |
| `tests/test_grid_backtest_http_contract.py` | 6 | ✅ |
| 前端 `npm test` | 99 | ✅ |
| 前端 `npm run lint` | 0 error（1 既有 warning） | ✅ |
| 前端 `npm run build` | 成功 | ✅ |
| `tests.services.grid.test_coordinator_stats_and_fills` | 2 | ✅ |
| `tests.test_dca_ladder` | 18 | ✅ |

### 2.2 驗證重點

| 項目 | 結果 |
|---|---|
| 完成一筆循環的 realized PnL 正確 | ✅ |
| 穿越偵測不會誤成交起始價上方/下方格線 | ✅ |
| 資金不足仍可執行並寫入 warning | ✅ |
| 爆倉情境會 flag + 輸出追加資金 | ✅ |
| 壓力情境能指出 -50% 觸發爆倉 | ✅ |
| `/run` `/stress` `/sweep` 未帶 token 回 401 | ✅ |
| `/run` 回傳 metrics/risk/equity/allocation/warnings | ✅ |
| 缺 `per_grid_notional / per_grid_capital_percent` 回 400/422 | ✅ |
| 無交易所寫入呼叫 | ✅ |
| DCA `direction=both` + indicator draft 通過後端 `validate_params` | ✅ |

---

## 第三段 — 不變量與防呆

| 規則 | 狀態 |
|---|---|
| 回測不寫 `config/active_grids.json` / `config/active_dca.json` | ✅（未觸及相關寫路徑） |
| 網格回測不初始化 `GridEngineImpl`（避免 WS/REST 副作用） | ✅ |
| 前端不自行做金融計算（金額/報酬直讀 API） | ✅（P3/P4 僅顯示與格式化） |
| 未知值顯示 `—`，不以 0 冒充 | ✅（沿用 `money/null` 處理） |
| 回測上限守衛：網格 bars ≤ 20000、DCA bars ≤ 20000、掃描預算 | ✅ |
| 每格金額與每格佔比皆支援 | ✅ |

---

## 第四段 — 已知限制（待後續改善）

1. 網格回測核心為**專用資金感知模擬器**，非與實盤 `GridCoordinator/GridEngineImpl` 同源；若需 100% 對齊實盤，需另做真引擎回放（高成本）。
2. 網格 v1 未模擬 follow / scalping / martingale / 止盈止損 / funding / 滑價 / 部分成交。
3. DCA 模式目前整合到統一入口，但未提供 DCA 專用的 sweep UI（既有 DCA 詳情頁仍可自行使用 `DcaSweepTable`）。
4. 壓力情境使用線性單向合成 K 線，非真實歷史最差走勢。

---

## 第五段 — 結論

P0–P4 已達成使用者需求的「左側新增回測功能」：
- 網格回測補齊初始資金 / 開啟時間 / 收益曲線 / 爆倉風險 / 需追加資金。
- DCA 單向 / 雙向（含指標）整合在同一入口，並複用既有真實引擎與元件。
- 市場排行保留「一鍵開格」，新增「回測」快速帶入。

P5 完成文件：使用手冊 `docs/grid_backtest_user_guide_zh-TW.md`、本稽核報告、`CHANGELOG.md` 更新。
