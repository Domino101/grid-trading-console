# DCA 圖表與回測 Phase C9 工作紀錄（後續收尾 + 整體功能稽核）

> 日期：2026-08-27
> 分支：`arena/01a044c7-grid-trading-console`
> 前置：Phase C1–C8 已完成（C8 收尾 commit `7b319ba`，CI 333 tests OK）
> 依據：`docs/dca_chart_backtest_plan_zh-TW.md` §6.2 收尾清單、C8 工作紀錄 §7「留給後續的事」
> 性質：非新增功能，純**收尾重構** + **整體稽核**；不修改任何交易決策邏輯

---

## 第一段 — 範圍與風險

### 1.1 本次做了什麼

C8 工作紀錄 §7 明確列出三件「留給後續的事」，本次執行第 1、2 件並稽核整體功能（第 3 件為 v2 範圍，僅文件化）：

| # | C8 遺留事項 | 本次處置 | 狀態 |
|---|---|---|---|
| 1 | **匯入拼法收斂為一種**：全庫統一為 `web_ui.backend.*`，契約測試禁止頂層拼法，`web_ui/backend/__init__.py` 的 finder 退場 | 完成：機械式收斂 30 個檔案 + 新增契約測試 + 移除 meta-path finder | ✅ |
| 2 | `test_dca_backtest_http_contract._restore_global_state()` 的雙模組迴圈已成冗餘 | 完成：簡化為套件拼法直取，刪除雙名迴圈 | ✅ |
| 3 | walk-forward / 樣本內外切分為 v2 項目 | 維持 v2（計畫 §8 排除項）；無程式動作，本紀錄重申 | ✅（文件化） |

### 1.2 收斂的工程內容（為什麼值得做）

C8 的 meta-path 別名 finder 是「症狀級」緩解：兩種拼法仍是壞味道，finder 本身帶魔法，且只要再有人寫一行 `from auth import …`，模組級單例（`auth._API_TOKEN`、SQLite repository、`GridStateStore`、`AuditLogger`）就會再次分岔。本次把根因除掉：

1. **全庫統一為單一拼法**（`web_ui/backend/**` 15 檔、`run_multiple_grids.py` 1 檔、`tests/**` 14 檔）：`from auth import …` → `from web_ui.backend.auth import …`、`import routes.dca` → `from web_ui.backend.routes import dca`、`patch("routes.market._lookup_market_from_ccxt_sync")` → `patch("web_ui.backend.routes.market._lookup_market_from_ccxt_sync")` 等。
2. **移除僅為頂層拼法服務的 `sys.path` 注入**：`run_multiple_grids.py` 的 `web_ui/backend` 插入、`grid_recommendation.py`、`test_dca_kline_repository.py` 等測試的 `_backend_dir` 插入。
3. **`web_ui/backend/__init__.py` 退場為空套件標記**：刪除 `_DualSpellingAliasFinder` / `_AliasLoader` / meta_path 安裝，僅保留約定說明 docstring。
4. **新增 `tests/test_backend_import_contract.py`（4 項）** 把關：
   - grep 契約：全 repo（backend + 引擎 + tests）不得出現頂層拼法 import（自動剝離 docstring/註解，prose 不誤報）；
   - `patch(...)` 目標必須為套件拼法；
   - 執行期單載入檢查：每個 backend 模組檔在 `sys.modules` 恰好一個模組物件（不依賴測試順序）；
   - `web_ui.backend.main` 可作為 uvicorn/測試入口匯入。
5. **清理冗餘防護**：`_restore_global_state()` 雙名迴圈刪除；e2e `test_8_6` 改寫為 `__file__` 計數的單載入斷言（不再 import 兩種拼法來比較）。

### 1.3 風險評估

| 風險 | 等級 | 評估 |
|---|---|---|
| 收斂破壞啟動路徑（`start_web_ui.sh` 以 `python3 main.py` 從 backend 目錄啟動） | P0 | `main.py` 開頭已將專案根加入 `sys.path`，套件拼法 `web_ui.backend.*` 可解析；live 驗證實測啟動成功（見第三段 §3.3） |
| 收斂破壞引擎入口（`run_multiple_grids.py` 由 PM2 從專案根啟動） | P0 | 引擎只依賴專案根（已在第 22 行加入 `sys.path`）；`from web_ui.backend.control_plane import …` 實測可匯入（見第三段 §3.4） |
| finder 退場後測試間單例分岔復發 | P0 | 契約測試的執行期單載入檢查會在任何頂層拼法復現時立刻紅燈；本次已在整包 CI 順序下驗證 |
| 契約 grep 誤報（docstring/註解含範例文字） | P2 | 剝離 `"""…"""` / `'''…'''` 與 `#` 註解後才比對；突變測試確認 6 種違規拼法全攔截、5 種合法/prose 不誤報 |

---

## 第二段 — 檢核結果

### 2.1 本計畫全域規則（G-1 ～ G-18）抽核

| 規則 | 結果 | 證據 |
|---|---|---|
| G-1/G-2/G-18 網格零變更 | ✅ | `git diff --stat core/services/grid/ run_grid_trading.py web_ui/frontend/src/components/GridDetail.tsx` 為空（本次收斂亦未觸碰） |
| G-3 不新增 Python 依賴 | ✅ | `requirements.txt` 未變更 |
| G-4 前端零計算 | ✅ | C7 驗收指令 grep（`DcaBacktest*.tsx` + `DcaSweepTable.tsx` 查 `required_capital/price_deviation_percent/take_profit_price`）0 命中；`tests.test_dca_frontend_contract` 41 項 OK |
| G-5 未知不得為 0 | ✅ | live 驗證 `profit_factor=null`（非 0）；既有契約測試覆蓋 |
| G-11/G-12 回測不下單、不寫熱插拔檔 | ✅ | BP-2 行為測試（mock 寫入計數 0 + `active_dca.json` 位元組比對）綠燈；`core/services/dca/backtest/` 僅 docstring 提及 `active_dca.json`（約定說明），無程式碼觸及 |
| G-13 不重寫階梯/指標數學 | ✅ | BP-1 grep：`class .*Executor` / `build_ladder` / `take_profit_price` / `evaluate_rsi` / `compute_macd` 於 `core/services/dca/backtest/` 0 命中 |
| G-15 不以牆鐘決定標記時間 | ✅ | E2E 斷言全部標記 ts 對齊 15m 棒時間網格（`test_8_1`） |
| G-16 合成資料必須標示 | ✅ | live 驗證 klines/chart 皆 `source=paper_synthetic` + warnings |
| G-17 新依賴隔離 | ✅ | `grep -rl lightweight-charts src/` 恰 1 檔（`DcaChart.tsx`）；build 維持獨立 chunk（gzip 57.93 kB） |

### 2.2 收尾重構自身的檢核

| # | 檢核項 | 等級 | 通過條件 | 結果 |
|---|---|---|---|---|
| 9.1 | 全庫單一拼法 | P0 | 契約 grep 對 `web_ui/backend/**`、`run_multiple_grids.py`、`tests/**` 0 命中 | ✅（`test_backend_import_contract` 第 1 項） |
| 9.2 | patch 目標套件化 | P0 | `patch("routes.…")` 等 0 命中 | ✅（第 2 項） |
| 9.3 | 單載入（finder 退場後） | P0 | 每個 backend 模組檔在 `sys.modules` 恰好 1 個模組物件（含整包 CI 執行順序） | ✅（第 3 項 + e2e `test_8_6`） |
| 9.4 | 入口可匯入 | P0 | `web_ui.backend.main` 可匯入；`main.py` / `run_multiple_grids.py` 可執行 | ✅（第 4 項 + live 驗證） |
| 9.5 | 引擎零行為變更 | P0 | `run_multiple_grids.py` 僅改 import 行；import 實測 OK | ✅ |
| 9.6 | C8 修復不因 finder 退場回歸 | P0 | 整包 CI 337 tests OK（C8 為 333）；17 條 C8 前紅燈維持綠 | ✅ |
| 9.7 | 契約測試本身會紅 | P0 | 突變：6 種違規拼法全數攔截；5 種合法拼法/prose 不誤報 | ✅（§3.5） |
| 9.8 | CI 雙登記 | P1 | `scripts/ci.sh` 與 `docs/github-actions-ci.yml` 皆含 `tests.test_backend_import_contract` | ✅ |
| 9.9 | 網格零回歸 | P0 | `git diff --stat core/services/grid/ run_grid_trading.py GridDetail.tsx` 為空 | ✅ |
| 9.10 | LIVE 維持關閉 | P0 | `live_trading_enabled()` 為 False；`.env` 無變更 | ✅ |
| 9.11 | 憑證掃描 | P0 | `git diff` 無真實憑證；唯一命中為測試檔刻意使用的 `"SECRET_VALUE"` 佔位字串（斷言不洩密用） | ✅ |

### 2.3 整體功能稽核（C1–C8 代表性項目複查）

除收尾重構外，本次以「稽核整體功能」為名對整個子系統做了複查，重點項目全數通過：

| 領域 | 複查項目 | 結果 |
|---|---|---|
| 資料層 C1 | `dca_klines` 參數化查詢、upsert 冪等、合成/真實隔離、缺棒偵測、PAPER 確定性、例外誠實（`stale`/`unavailable` 絕不 `live`） | ✅（`test_dca_kline_repository` 12 項 + live klines 回應） |
| 撮合 C2 | R-1 無未来函数、R-2/R-3 限價向量、R-4 同棒保守順序、R-5 市價跳空、零 I/O | ✅（`test_dca_replay_exchange` 13 項） |
| 驅動 C3 | BP-1 真引擎、R-8 熔斷固定、R-9 棒時間時鐘、前視注入、確定性、標記 ts 為棒時間、INV 守衛 | ✅（`test_dca_replay_driver` 14 項） |
| 指標/掃描 C4 | 指標向量鎖定、buy&hold 基準、profit_factor 分母 0 → None、MDD、預計算等價性、預算/truncated | ✅（`test_dca_backtest_metrics` 8 項 + `test_dca_backtest_sweep` 9 項） |
| API C5 | 四端點認證 401、A-4 上限守衛、A-6 warnings 四條、A-7 稽核日誌、threadpool 不阻塞 | ✅（`test_dca_backtest_http_contract` 16 項 + live 驗證） |
| 前端 C6/C7 | 標記語意表、記憶體回收、armed 語意、合成角標、buy&hold 並排、truncated 誠實、未知值「—」 | ✅（前端 90/90、`dca-chart.test.ts` + `dca-backtest.test.ts`） |
| E2E C8 | 全鏈路 200、BP-2 反證、網格零回歸、LIVE 關閉、憑證掃描、單例一致性 | ✅（`test_dca_chart_backtest_e2e` 13 項） |

---

## 第三段 — 測試證據

### 3.1 CI（Python，收尾後）

```text
$ SKIP_FRONTEND=1 bash scripts/ci.sh
Ran 337 tests in 14.999s
OK
```

對照：C8 基線 **333**；本次 **+4**（`tests.test_backend_import_contract` 4 項）。無新增失敗、無既有失敗。

非 CI 成員亦驗證（收斂後仍綠）：`python3 -m unittest tests.test_hermes_api tests.test_hermes_client` → `Ran 52 tests … OK`。

### 3.2 CI（前端）

```text
$ npm test      → # tests 90 / # pass 90 / # fail 0
$ npm run lint  → ✖ 1 problem (0 errors, 1 warning)   # 既有 MarketRankings.tsx exhaustive-deps（EX-7.1）
$ npm run build → ✓ built in 757ms
dist/assets/index-CCRCK97h.js        374.25 kB │ gzip: 105.59 kB
dist/assets/DcaChart-Dshx5rJ2.js     178.11 kB │ gzip:  57.93 kB   ← lightweight-charts 獨立 chunk（與 C8 相同）
dist/assets/GridDetail-BD78o0R1.js   348.00 kB │ gzip: 102.21 kB   ← 網格頁未受影響
```

### 3.3 實機 live 驗證（AGENTS.md rule-05）

啟動：`cd web_ui/backend && GRID_API_TOKEN=audit-live-token-20260827 python3 main.py`（`0.0.0.0:8001`，前端 dist 已建置）。逐一 curl 執行中實例：

| 端點 | 結果 |
|---|---|
| `GET /healthz` | **200**（public probe） |
| `GET /dca/test/chart`（無 token） | **401** |
| `POST /api/v1/dca`（PAPER） | **201**，`bot_id=dca-binance-long-eth-usdc-usdc-20260827200109` |
| `GET /market/klines/ETH/USDC:USDC?account_mode=paper&timeframe=15m&limit=100` | 200，`candles=100`、`source=paper_synthetic`、`data_status=paper`、`gaps=0`、`warnings=['使用合成 K 線，非真實歷史']` |
| `GET /dca/{bot}/chart?timeframe=15m&limit=100` | 200，`candles=100`、`marks=0`（未啟動＝零掛單，INV-1）、`lines=5`、`data_status=paper`、warnings 1 條 |
| `POST /dca/backtest`（600 棒） | 200，`marks=21`、`cycles=5`、`cycles_completed=4`、`total_return_pct=-1.6155`、`buy_hold_return_pct=-12.6509`、`alpha_pct=+11.0353`、`profit_factor=null`（G-5）、`win_rate_pct=80.0`、`max_drawdown_pct=1.8703`、`bars_per_second=13601.25`、warnings 4 條齊備 |
| `POST /dca/backtest/sweep`（TP 0.5/1.0/1.5，400 棒） | 200，`runs=3`、`truncated=false`、`budget.bars_used=1200/300000`、best `{metric: total_return_pct, value: -1.0794, params:{take_profit_percent:0.5}}` |
| `DELETE /dca/{bot}` | 200，YAML 已歸檔、`active_dca.json` 回復 `{}` |
| `GET /`（前端 dist） | **200 text/html** |

稽核日誌（A-7）：`data/grid_audit.db` 的 `audit_log` 含 `create_dca` ×1、`run_dca_backtest` ×1、`run_dca_sweep` ×1。

驗證後清理測試產物（歸檔 YAML 已移除、`active_dca.json` 為 `{}`、`data/` 與 `config/dca/archived/` 皆 gitignore）；`git status` 僅含本次提交的變更。

### 3.4 引擎入口檢查

```text
$ python3 -m py_compile run_multiple_grids.py                       → OK
$ python3 -c "from web_ui.backend.control_plane import publish_lifecycle, publish_runtime; …"  → OK
$ python3 -c "from web_ui.backend.main import app"                  → app OK（模擬 uvicorn 套件入口）
```

### 3.5 突變測試（證明新契約會紅）

| 突變 | 結果 |
|---|---|
| 新增 `from auth import verify_token` | ✅ 契約 grep 攔截 |
| 新增 `import auth` / `import routes.dca as …` | ✅ 攔截 |
| 新增 `patch("routes.market._lookup_market_from_ccxt_sync")` / `patch("safety_guard.SafetyGuard._read_active_grids")` | ✅ 攔截 |
| 合法拼法 `from web_ui.backend.auth import …` / docstring 範例 / `#` 註解 | ✅ 不誤報 |

另以「停用契約模組」類比 C8 突變法：若 `tests/test_backend_import_contract` 不存在，9.1/9.3 即無防護——故其 4 項已全數納入 `scripts/ci.sh` 與 `docs/github-actions-ci.yml`。

---

## 第四段 — 例外接受

| # | 項目 | 等級 | 決定 | 理由 |
|---|---|---|---|---|
| E-9.1 | 前端 lint 遺留 1 個 warning（`MarketRankings.tsx` exhaustive-deps） | P2 | **接受** | 既有問題（EX-7.1），與本計畫無關；修改需動網格頁行為 |
| E-9.2 | `graphify update .` 無法執行 | P2 | **接受** | 沙箱未安裝 graphify CLI（`which graphify` 無結果）；本次為純機械式 import 收斂，`graphify-out/` 既有內容不受影響 |
| E-9.3 | 引擎（`pm2 restart multi-grid-engine`）未重啟實測 | P2 | **接受** | 沙箱無 pm2 常駐實例；已以 `python3 -c` 驗證引擎新 import 路徑可解析、`py_compile` 通過 |
| E-9.4 | walk-forward / 樣本內外切分維持 v2 | P1 | **接受** | 計畫 §8 明確排除項（D-8 決策）；C8 工作紀錄 §7 第 3 項僅要求文件化，本紀錄即為文件化 |
| E-9.5 | `_restore_global_state` 保留（未整個刪除） | P2 | **接受** | 函式仍有意義（`test_dca_http_contract` 等測試仍會覆寫全域，本檔需自我防護）；僅刪除已冗餘的雙模組迴圈 |

---

## 第五段 — 部署與回滾

### 5.1 變更清單（C9 收尾）

```
新增  tests/test_backend_import_contract.py        契約測試 4 項（單拼法 + 單載入）
新增  docs/dca_chart_backtest_phase9_worklog_20260827.md   本紀錄
修改  web_ui/backend/**（15 檔）                   import 收斂為 web_ui.backend.*；
                                                    __init__.py 移除 meta-path finder
修改  run_multiple_grids.py                         import 收斂；移除 _backend_dir sys.path 注入
修改  tests/**（14 檔）                             import 收斂 + patch 目標套件化；
                                                    e2e test_8_6 改寫；_restore_global_state 簡化
修改  scripts/ci.sh, docs/github-actions-ci.yml     登記 tests.test_backend_import_contract
修改  CHANGELOG.md                                 [Unreleased] 補 C9 收尾條目
```

### 5.2 部署步驟

```bash
git pull
cd web_ui/frontend && npm ci && npm run build   # 前端無變更，可沿用既有 dist
bash start_web_ui.sh                             # 後端必須重啟（載入新的 __init__.py 與 import）
pm2 restart multi-grid-engine                    # 引擎 import 路徑變更，建議重啟（無行為差異）
```

### 5.3 回滾

```bash
git revert <commit>
# 重啟後端與引擎
```

回滾風險極低：本次是純 import 拼法收斂，**零 schema、零設定格式、零交易行為變更**。回滾後唯一差異是回到「頂層拼法 + meta-path 別名 finder」的 C8 狀態——功能照常運作，僅保留舊的雙拼法壞味道與 17 條測試的隱憂（由 C8 的 finder 繼續緩解）。

### 5.4 上線後觀察點

1. `logs/web_ui.log` 是否出現 `[Auth] GRID_API_TOKEN 未設定` **兩次**（若出現，代表有程式碼又用頂層拼法 import，契約測試應先於此發現）。
2. 全庫新增 import 時一律使用 `web_ui.backend.*`；CI 的 `tests.test_backend_import_contract` 會把關。
3. 若未來要執行 C8 工作紀錄 §7 第 1 點的「契約測試中禁止頂層拼法」之完整化，本紀錄 9.1/9.2 已落地。
