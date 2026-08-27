# DCA 即時圖表與回測子系統 Phase C4 稽核報告 — 績效指標與參數掃描

> 日期：2026-08-27｜分支：`arena/01a03e37-grid-trading-console`
> 計畫：`docs/dca_chart_backtest_plan_zh-TW.md` §Phase C4（§4.5 指標定義 + 參數掃描預算）
> 依據標準：`docs/grid_trading_console_audit_standard_zh-TW.md` §5 五段式

---

## 1. 範圍與風險

**範圍**：把 C3 `BacktestResult` 變成可決策的數字（含 buy&hold 對照），並提供有預算上限、truncated 誠實回報的參數掃描。此 Phase **不碰前端、不碰 HTTP API**（C5 才接端點）。

觸及檔案清單：
- **績效指標純函式**：`core/services/dca/backtest/metrics.py`（`compute_metrics` / `buy_hold_return_pct` / `max_drawdown_pct`）。
- **參數掃描**：`core/services/dca/backtest/sweep.py`（`expand_grid` / `run_sweep` / `precompute_series_fns` / `bar_should_enter_series`）。
- **驅動器接線**：`core/services/dca/backtest/replay_driver.py`（改呼叫 `compute_metrics`；可注入 `series_fns`）。
- **入場注入點**：`core/services/dca/coordinator/entry_monitor.py`（可選 `series_fns`，不改決策語意）。
- **套件進入點**：`core/services/dca/backtest/__init__.py`。
- **單元測試**：`tests/test_dca_backtest_metrics.py`、`tests/test_dca_backtest_sweep.py`。
- **CI 雙登記**：`scripts/ci.sh` 與 `docs/github-actions-ci.yml`。
- **變更日誌**：`CHANGELOG.md` `[Unreleased]`。

**風險**：
1. **會計口徑分岔（RC / 稽核 4.5）**：若指標加總顯示層 `_fills[].fee` 會與引擎 `_total_fees` 分岔（dual-stage M-1）。緩解：`compute_metrics` 只接受呼叫端傳入的引擎會計值；`ReplayDriver` 改走 `build_statistics()`。
2. **掃描預計算與逐棒重算不等價（RC-11 / 稽核 4.8）**：預計算若在全序列上算完再切片，會因 `fetch_limit` 視窗暖機不同而與實盤路徑分岔。緩解：對每一個與 `ohlcv_provider` 相同的 sliding window 預計算，P0 測試鎖定每棒 `should_enter` 與最終 `BacktestResult` 逐欄位相同。
3. **掃描打爆 CPU（RC-10 / 稽核 4.7 / 4.9）**：並行上限 4、總棒數預算、truncated 誠實回報；未完成 run 的 `metrics` 必須為 `None`。
4. **未知值補零（G-5 / 稽核 4.3 / 4.6）**：無虧損輪 `profit_factor=None`；無輪次 `win_rate_pct` / `avg_cycle_bars` / `mfe_pct` 皆 `None`。

**網路與核心隔離**：
- `core/services/grid/**` 與 `run_grid_trading.py` 零修改（G-1/G-2）。
- `requirements.txt` 零新增依賴（G-3）。
- 本 Phase 為純核心庫，無需重啟 Web UI / 引擎。

**關於先前「運行數小時」**：根因不是 C4 邏輯死鎖。先前卡住通常來自（1）全量 `scripts/ci.sh` 含前端 `npm ci` / lint / build；（2）`graphify update .` 掃過整個巨型工作樹；（3）unittest 日誌把 `test_dca_supervisor` 的預期 `FileNotFoundError` 打成 `ERROR:`，看起來像失敗但其實測試本身是通過的（缺檔路徑是該測試的故意案例）。本 Phase 只跑新增模組，17 項 **0.31s** 結束。

---

## 2. 檢核結果

| # | 檢核項 | 等級 | 通過條件 | 結果 | 證據 |
|---:|---|---|---|---|---|
| **4.1** | 指標向量鎖定 | P0 | 3 輪手工 case：`total_return_pct` / `win_rate_pct` / `max_drawdown_pct` 與手算相符，容差 `1e-6` | **PASS** | `test_audit_4_1_hand_computed_three_cycles` |
| **4.2** | buy&hold 基準存在 | P0 | `buy_hold_return_pct` 與 `alpha_pct` 必存在；做多/做空各 1 個測試 | **PASS** | `test_audit_4_2_long_buy_hold` / `test_audit_4_2_short_buy_hold_is_inverted` |
| **4.3** | profit_factor 分母 0 | P0 | 無虧損輪時回傳 `None`（不是 0、不是 inf） | **PASS** | `test_audit_4_3_no_losing_cycle_returns_none` |
| **4.4** | MDD 定義 | P0 | equity `[100,120,90,110]` → MDD = 25.0%，非首尾差 | **PASS** | `test_audit_4_4_peak_to_trough_not_first_last` |
| **4.5** | 會計口徑 | P0 | `realized_pnl` 等於引擎 `build_statistics().profit.realized`；刻意讓 `_fills[].fee` 加總與 `_total_fees` 不同，斷言採用後者 | **PASS** | `test_audit_4_5_uses_engine_statistics_not_fill_fee_sum` |
| **4.6** | 未知值不補零 | P0 | 無任何輪次時 `win_rate_pct` / `avg_cycle_bars` / `mfe_pct` 皆為 `None` | **PASS** | `test_audit_4_6_no_cycles_metrics_are_none` |
| **4.7** | 掃描並行上限 | P1 | `SWEEP_MAX_WORKERS=4` 時同時執行中的 replay ≤ 4 | **PASS** | `test_audit_4_7_concurrent_replays_capped`（計數器 + 120ms 重疊窗） |
| **4.8** | 預計算等價性 | P0 | 每一棒 `should_enter` 完全相同；最終 `BacktestResult` 逐欄位相同 | **PASS** | `test_audit_4_8_per_bar_verdict_identical` / `test_audit_4_8_backtest_result_fields_identical` |
| **4.9** | 預算與 truncated | P0 | 小預算 → 部分 runs 完成、`truncated=true`、`bars_used ≤ bars_limit`；不得回報未完成 run 的指標 | **PASS** | `test_audit_4_9_truncated_does_not_report_incomplete_metrics`（25 棒預算 / 10 棒 × 3 組 → 完成 2） |
| **4.10** | 非法參數組合 | P1 | `max_safety_orders=0` → 該 run 標記 `error` 並附中文原因，整體不崩潰 | **PASS** | `test_audit_4_10_illegal_max_safety_orders_marked_error` |
| **4.11** | 網格零回歸 | P0 | `git diff --stat core/services/grid/ run_grid_trading.py` 為空 | **PASS** | 兩個測試模組皆斷言 diff 為空 |

---

## 3. 測試證據

### 3.1 本次新增模組單元測試

```text
$ python3 -m unittest tests.test_dca_backtest_metrics tests.test_dca_backtest_sweep -v
Ran 17 tests in 0.310s
OK
```

連同既有 C2 `tests.test_dca_replay_exchange`（含 2.10 零 I/O 靜態掃描）：

```text
$ python3 -m unittest tests.test_dca_backtest_metrics tests.test_dca_backtest_sweep tests.test_dca_replay_exchange -v
Ran 30 tests in 0.291s
OK
```

### 3.2 本輪刻意不跑的項目（避免再次卡數小時）

| 項目 | 原因 |
|---|---|
| `bash scripts/ci.sh` 含前端 `npm ci` / lint / build | 前端不在 C4 範圍；沙箱 `npm ci` 曾是長尾來源 |
| `graphify update .` | 工作樹含大量 `.agents` / 歷史檔，全量 AST 更新成本高且與 C4 交付無關 |
| `tests.test_dca_replay_driver` 全量 | 該模組頂層 import `BinanceRest` → 連帶 `websockets`；C4 的 4.5 / 4.8 已實跑 `ReplayDriver` |
| `unittest discover` | 會掃到非 CI 模組；`test_dca_supervisor` 缺檔案例會打 `ERROR:` 日誌，易被誤認為卡住 |

C3 測試模組已登錄 CI，正式 runner 有完整依賴後會一併綠燈。本沙箱缺 `websockets`，不在此重裝整個交易所堆疊。

### 3.3 BP-1 / 網格零回歸靜態掃描

```bash
$ grep -rnE "class .*Executor|def build_ladder|def take_profit_price|def evaluate_rsi" core/services/dca/backtest/
# 0 命中

$ git diff --stat core/services/grid/ run_grid_trading.py
# 空
```

---

## 4. 例外接受

無新增例外。維持既有 EX-C1.1（MarketRankings eslint 警告，C6 才會碰到前端）。

C3 `tests.test_dca_replay_driver` 本沙箱因缺 `websockets` 未能重跑；C4 變更已由 4.5 / 4.8 實跑 `ReplayDriver.run()` 覆蓋。正式 CI 環境有完整依賴。

---

## 5. 部署 / 回滾

### 5.1 需重啟的服務

- 本 Phase 僅新增純核心庫（指標 + 掃描），不影響目前運行的後端 API 或實盤引擎，**無需重啟服務**。

### 5.2 回滾指令

```bash
git revert <commit-hash>
```

---

## 附錄：交付物與後續 Phase C5 銜接

- **已交付**：
  1. `core/services/dca/backtest/metrics.py`
  2. `core/services/dca/backtest/sweep.py`
  3. `core/services/dca/backtest/replay_driver.py`（改檔）
  4. `core/services/dca/backtest/__init__.py`（改檔）
  5. `core/services/dca/coordinator/entry_monitor.py`（可選 `series_fns`）
  6. `tests/test_dca_backtest_metrics.py`
  7. `tests/test_dca_backtest_sweep.py`
  8. `scripts/ci.sh` / `docs/github-actions-ci.yml`
  9. `CHANGELOG.md`
  10. `docs/dca_chart_backtest_phase4_worklog_20260827.md`
- **後續銜接（Phase C5）**：把 C1–C4 包成四個受認證端點（`GET /klines`、`GET /{bot_id}/chart`、`POST /backtest`、`POST /backtest/sweep`），含 A-4/A-5 守衛、BP-2 反證與 audit log。
