# DCA 即時圖表與回測子系統 最終稽核報告（C1–C8）

> 日期：2026-08-27
> 範圍：`core/services/dca/backtest/`、`web_ui/backend/`（K 線與回測端點）、`web_ui/frontend/`（圖表與回測工作台）
> 依據：`docs/dca_chart_backtest_plan_zh-TW.md`、`docs/grid_trading_console_audit_standard_zh-TW.md`
> 分支：`arena/01a04361-grid-trading-console`
> 驗收環境：PAPER，`GRID_LIVE_TRADING_ENABLED` 未開啟

---

## 第一段 — 範圍與風險

### 1.1 本子系統交付了什麼

| 層 | 交付物 | 行數 |
|---|---:|---:|
| 回測核心 | `core/services/dca/backtest/`（`replay_exchange` / `replay_driver` / `metrics` / `sweep` / `models` / `candles`） | ~1,600 |
| 資料層 | `web_ui/backend/kline_repository.py` + `kline_service.py`（SQLite 快取、分頁回填、權重預算、PAPER 合成） | 760 |
| API | `GET /market/klines/{symbol}`、`GET /dca/{bot_id}/chart`、`POST /dca/backtest`、`POST /dca/backtest/sweep` | — |
| 前端 | `DcaChart.tsx` + `utils/dcaChart.ts` + `DcaBacktestPanel/Result` + `DcaSweepTable` | ~1,150 |
| 測試 | 7 個 Python 模組 + 2 個 TS 模組 | ~2,600 |

### 1.2 兩條承重性質

| # | 性質 | 本次驗證方式 | 結果 |
|---|---|---|---|
| **BP-1** | 回測與實盤走**同一套**決策程式碼 | `grep -rnE "class .*Executor\|def build_ladder\|def take_profit_price" core/services/dca/backtest/` 必須 0 命中；回放路徑實例化真 `DcaCoordinator` + 真 `DcaOrderExecutor` | ✅ 0 命中 |
| **BP-2** | 回測**永遠不下真單** | E2E 以 `patch.object(PaperExchangeAdapter, "create_order"/"cancel_order")` 計數；`config/active_dca.json` 位元組比對 | ✅ 0 次呼叫、位元組相同 |

### 1.3 殘餘風險（使用者必須知道）

| 風險 | 等級 | 狀態 |
|---|---|---|
| PAPER 合成 K 線不是真實歷史，報酬數字只能相對比較 | P0（誤信） | 已緩解：`source=paper_synthetic` → API `warnings` + UI 角標 + 手冊 §1.3 |
| 六條撮合簡化（不部分成交、無滑價模型、未套最小名目…）使回測偏樂觀 | P0（誤信） | 已緩解：`warnings` 強制非空且全量渲染；面板常駐揭露 |
| 參數掃描的「最優」在 `truncated=true` 時只是已完成範圍內最優 | P1 | 已緩解：文案含「已完成範圍內」，並顯示 N/M |
| 掃描結果過度擬合單一歷史段 | P1 | 文件化（手冊 §3），無程式手段 |
| 回測不涵蓋資金費率與 VIP 費率階梯 | P2 | 已揭露於手冊 §2.3 |

---

## 第二段 — 檢核結果

### 2.1 Phase C8 稽核表（計畫 §5 Phase C8）

| # | 檢核項 | 等級 | 結果 | 證據 |
|---|---|---|---|---|
| 8.1 | PAPER 全鏈路 | P0 | ✅ | `tests/test_dca_chart_backtest_e2e.py` 建立→klines→chart→backtest（3 輪完成）→sweep（3 組）→delete 全部 200 |
| 8.2 | 實機 live 驗證 | P0 | ✅ | 見第三段 §3.3：對 `127.0.0.1:8001` 執行中實例逐一 curl，含重啟後複驗 |
| 8.3 | 網格零回歸 | P0 | ✅ | `git diff --stat core/services/grid/ run_grid_trading.py GridDetail.tsx` 為空（並已固化為測試 `test_8_3`） |
| 8.4 | LIVE 維持關閉 | P0 | ✅ | `live_trading_enabled()` 為 False；`account_mode=live` 建立請求回 403 |
| 8.5 | 憑證掃描 | P0 | ✅ | 回測/圖表相關原始碼掃描 0 命中（`test_8_5`）；`git diff` 未觸及 `.env` |
| 8.6 | CI 全綠 | P0 | ✅ | `bash scripts/ci.sh` → Python **333 tests OK**、前端 **90/90**、lint 0 error、build 成功 |
| 8.7 | 五段式報告 | P1 | ✅ | 本文件 |
| 8.8 | 文件可操作 | P1 | ✅ | `docs/dca_chart_backtest_user_guide_zh-TW.md`（標記表、六條簡化、alpha 讀法、掃描用法、錯誤碼表） |

### 2.2 跨 Phase 全域規則（G-1 ～ G-18）

| 規則 | 結果 | 說明 |
|---|---|---|
| G-1/G-2/G-18 網格零變更 | ✅ | 已固化為自動化測試 |
| G-3 不新增 Python 依賴 | ✅ | `requirements.txt` 未變更；後端全走 stdlib 與既有依賴 |
| G-4 前端零計算 | ✅ | grep 契約 0 命中（`test_dca_frontend_contract` 41 項） |
| G-5 未知不得為 0 | ✅ | `profit_factor=null → 「—」`、`lines[].price=null → 不畫線` |
| G-11/G-12 回測不下單、不寫熱插拔檔 | ✅ | E2E 兩項測試 |
| G-13 不重寫階梯/指標數學 | ✅ | grep 契約 |
| G-15 不以牆鐘決定標記時間 | ✅ | E2E 斷言全部標記 ts 對齊 15m 棒時間網格 |
| G-16 合成資料必須標示 | ✅ | klines / chart / backtest 三處 `warnings` |
| G-17 新依賴隔離 | ✅ | `lightweight-charts` 只被 `DcaChart.tsx` import，且落在獨立 chunk（gzip 57.93 kB，首屏不載） |

### 2.3 本次（C8）發現並修復的缺陷

| # | 缺陷 | 等級 | 影響 | 修復 |
|---|---|---|---|---|
| **F-1** | 後端模組被以兩種拼法（`auth` 與 `web_ui.backend.auth`）**各載入一次**，模組級單例出現兩份 | **P0** | 兩個 API token、兩個 SQLite repository 連線、兩份 `GridStateStore` 快取、兩個 `AuditLogger`；CI 有 **17 條**測試因此紅燈（15 條 `401 Invalid or missing API token`、1 條讀到真實 runtime 資料庫、2 條連鎖 ERROR） | 新增 `web_ui/backend/__init__.py`：以 meta-path 別名把兩種拼法映射到同一模組物件（僅在「檔案路徑相同」時才別名，不會誤攔第三方套件） |
| **F-2** | `GET /dca/{bot_id}/chart` 對 PAPER 無條件附加「使用合成 K 線，非真實歷史」，與 `KlineService` 已放入的同一條重複 | P2 | 前端全量渲染 `warnings`，畫面上出現兩行相同警告 | 改為去重後附加；E2E 增加「warnings 不得重複」斷言 |
| **F-3** | `tests/test_dca_http_contract.py` 覆寫模組級全域後從不還原，污染外溢到後續測試模組 | P1 | 測試結果與執行順序相關（C7 工作紀錄已標記待清理） | 改用 `addCleanup` 自動還原全部 7 個全域 |

---

## 第三段 — 測試證據

### 3.1 CI（Python）

```
$ SKIP_FRONTEND=1 bash scripts/ci.sh
Ran 333 tests in 14.206s
OK
```

C8 前基線：`Ran 320 tests … FAILED (failures=15, errors=2)`。
本次 **+13 個新測試**（`tests.test_dca_chart_backtest_e2e`）且 **17 條既有紅燈全部轉綠**（根因 F-1）。

本子系統各模組：

| 模組 | 測試數 |
|---|---:|
| `test_dca_kline_repository` | 12 |
| `test_dca_replay_exchange` | 13 |
| `test_dca_replay_driver` | 14 |
| `test_dca_backtest_metrics` | 8 |
| `test_dca_backtest_sweep` | 9 |
| `test_dca_backtest_http_contract` | 16 |
| `test_dca_chart_backtest_e2e`（C8 新增） | 13 |
| `test_dca_frontend_contract` | 41 |

### 3.2 CI（前端）

```
$ npm test   → # tests 90 / # pass 90 / # fail 0
$ npm run lint → ✖ 1 problem (0 errors, 1 warning)     # warning 為既有 MarketRankings.tsx
$ npm run build → ✓ built in 702ms
dist/assets/index-CCRCK97h.js        374.25 kB │ gzip: 105.59 kB
dist/assets/DcaChart-Dshx5rJ2.js     178.11 kB │ gzip:  57.93 kB   ← lightweight-charts 獨立 chunk
dist/assets/GridDetail-BD78o0R1.js   348.00 kB │ gzip: 102.21 kB   ← 網格頁未受影響
```

### 3.3 實機 live 驗證（AGENTS.md / rule-05）

啟動：`cd web_ui/backend && GRID_API_TOKEN=… python main.py`（`0.0.0.0:8001`，前端 dist 已建置）。
修改 `routes/dca.py` 後**重新啟動**並複驗（下表為重啟後的輸出）。

| 端點 | 結果 |
|---|---|
| `POST /api/v1/dca`（PAPER） | 201，`bot_id=dca-binance-long-eth-usdc-usdc-20260827133223`，`auto_start=false` |
| `GET /api/v1/market/klines/ETH/USDC:USDC?…&account_mode=paper` | 200，`source=paper_synthetic`、`data_status=paper`、`gaps=0`、`warnings=['使用合成 K 線，非真實歷史']` |
| `GET /api/v1/dca/{bot}/chart?timeframe=15m&limit=100` | 200，`candles=100`、`marks=0`（未啟動＝零掛單，符合 INV-1）、`warnings` **去重後 1 條**（修復前為 2 條） |
| `POST /api/v1/dca/backtest`（600 棒） | 200，`marks=14`、`cycles=4`、`cycles_completed=3`、`total_return_pct=-5.29`、`buy_hold_return_pct=-12.65`、`alpha_pct=+7.36`、`profit_factor=null`、`bars_per_second=21901.9`、四條 warnings 齊備 |
| `POST /api/v1/dca/backtest/sweep`（TP 1.0/1.5/2.0） | 200，`runs=3`、`truncated=false`、`budget.bars_used=1200/5000`、`best={metric: total_return_pct, value: -2.214, params:{take_profit_percent:1.0}}` |
| `DELETE /api/v1/dca/{bot}` | 200，YAML 已歸檔，`active_dca.json` 條目移除 |
| 無 token 存取 `/chart` | 401 |

驗證後工作區無殘留檔案（`git status --short` 僅列出本次要提交的變更）。

### 3.4 突變測試（證明測試會紅，而不是只會綠）

| 突變 | 預期 | 實際 |
|---|---|---|
| 停用 `web_ui/backend/__init__.py` 的別名安裝 | `test_8_6` FAIL | ✅ FAIL |
| 對 `core/services/grid/__init__.py` 加一行 | `test_8_3` FAIL | ✅ FAIL |
| 同時從 `replay_driver.py` 與 `routes/dca.py` 移除「歷史績效不代表未來」 | `test_8_1_d` FAIL | ✅ FAIL |
| 還原 `/chart` warnings 去重 | `test_8_1_c` FAIL | ✅ FAIL |
| 把 `ReplayExchange` 撮合條件改為 `placed_bar <= bar_index`（引入未来函数） | `test_dca_replay_exchange` FAIL | ✅ FAIL（E2E 不負責此項，見下方誠實補充） |

**誠實補充**：最初 E2E 曾寫入一條「棒 i 下的單不得在棒 i 成交」的斷言，實測對上述未来函数突變**沒有反應**——因為回測結果依規格只投影成交、不含 `order_placed` 標記，該斷言的迴圈永遠是空的（恆綠）。已將其移除，改為「回測標記種類必須是成交類且不得出現 `order_placed`」＋「相同輸入兩次結果逐欄位相同」兩條**會紅**的斷言；未来函数的把關留在它真正該在的地方（`tests/test_dca_replay_exchange.py` 的 R-1 向量與 `test_dca_replay_driver.py` 的前視注入測試）。

所有突變後皆以 `git diff --stat` 確認原始碼完整還原。

---

## 第四段 — 例外接受

| # | 項目 | 等級 | 決定 | 理由 |
|---|---|---|---|---|
| E-1 | 前端 lint 遺留 1 個 warning（`MarketRankings.tsx` exhaustive-deps） | P2 | **接受** | 既有問題，與本計畫無關；修改需動到網格相關頁面的行為 |
| E-2 | 回測結果不含 `order_placed` 標記 | P2 | **接受（符合規格）** | 計畫 §4.7 明定該標記「僅即時圖」；已在使用者手冊 §1.2 說明原因 |
| E-3 | 沙箱以 `pip install --break-system-packages` 補齊依賴而非 venv | P2 | **接受** | 環境限制；`requirements.txt` 未變更（G-3 未違反） |
| E-4 | 實機驗證以 `python main.py` 啟動而非 `bash start_web_ui.sh` | P2 | **接受** | 該腳本硬編碼 `venv/bin/python3`，沙箱無 venv；啟動的是同一支 `main.py`、同一個 0.0.0.0:8001 |
| E-5 | 引擎（`pm2 restart multi-grid-engine`）未重啟驗證 | P2 | **接受** | 本次改動不涉及引擎執行路徑（`run_multiple_grids.py` 零變更）；沙箱亦無 pm2 常駐實例 |
| E-6 | 掃描過度擬合僅以文件警示，無程式強制 | P1 | **接受** | v1 範圍不含 walk-forward（計畫 §8 明確排除項） |

---

## 第五段 — 部署與回滾

### 5.1 變更清單（C8）

```
新增  web_ui/backend/__init__.py                      模組別名（修復 F-1）
新增  tests/test_dca_chart_backtest_e2e.py            E2E 13 項
新增  docs/dca_chart_backtest_user_guide_zh-TW.md     使用者手冊
新增  docs/dca_chart_backtest_audit_report_zh-TW.md   本報告
新增  docs/dca_chart_backtest_phase8_worklog_20260827.md
修改  web_ui/backend/routes/dca.py                    /chart warnings 去重（F-2）
修改  tests/test_dca_http_contract.py                 全域狀態 addCleanup 還原（F-3）
修改  scripts/ci.sh, docs/github-actions-ci.yml       登記 E2E 模組
修改  README.md, config/dca/README.md,
      docs/dca_bot_user_guide_zh-TW.md, CHANGELOG.md  文件
```

### 5.2 部署步驟

```bash
git pull                                  # 取得本分支變更
cd web_ui/frontend && npm ci && npm run build
bash start_web_ui.sh                      # 或重啟既有後端進程（載入 web_ui/backend/__init__.py）
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8001/api/v1/dca/<bot_id>/chart?timeframe=15m&limit=100" | head -c 200
```

**後端必須重啟**：`web_ui/backend/__init__.py` 只在進程啟動時安裝模組別名。引擎（`run_multiple_grids.py`）無須重啟，其執行路徑未變更；若已在運行，重啟亦安全。

### 5.3 回滾

```bash
git revert <commit>          # 或 git checkout <前一版> -- web_ui/backend/__init__.py web_ui/backend/routes/dca.py
# 重啟後端
```

回滾風險極低：

* `web_ui/backend/__init__.py` 移除後系統回到「模組載入兩次」的舊行為——**功能仍可運作**（先前即如此），只是單例分岔與 17 條測試紅燈會回歸。
* `/chart` 去重回滾只會讓警告重複顯示一次，不影響數據。
* 無資料庫 schema 變更、無設定檔格式變更、無交易行為變更。

### 5.4 上線後觀察點

1. `logs/web_ui.log` 是否出現 `[Auth] GRID_API_TOKEN 未設定` **兩次**（若出現，代表別名未生效，模組仍被載入兩次）。
2. `/api/v1/dca/{bot_id}/chart` 的 `warnings` 是否出現重複條目。
3. 回測端點的 p95 耗時（`bars_per_second` 在本機合成資料下約 2 萬棒/秒；真實資料量大時應仍遠低於 60 秒上限）。
4. `data/web_ui_runtime.sqlite3` 的 `dca_klines` 表大小（180 天保留期，每次回填後 prune）。
