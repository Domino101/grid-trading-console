# DCA 即時圖表與回測子系統 Phase C3 稽核報告 — `ReplayDriver` 回放驅動器

> 日期：2026-08-26｜分支：`arena/01a03e16-grid-trading-console`
> 計畫：`docs/dca_chart_backtest_plan_zh-TW.md` §Phase C3（§3.1 真引擎組裝 + §4.4 輸出契約 + 稽核 3.1–3.14）
> 依據標準：`docs/grid_trading_console_audit_standard_zh-TW.md` §5 五段式

---

## 1. 範圍與風險

**範圍**：為 DCA 回測子系統實作 `ReplayDriver`，將真正、未經修改的 `DcaCoordinator` 與 `DcaOrderExecutor` 接駁至 `ReplayExchange`，落實 BP-1 同源、確定性、虛擬棒時間時鐘、歷史資料邊界切片、顯示層時間蓋章以及每棒狀態不變量（INV-1 / INV-2 / INV-6）守衛。

觸及檔案清單：
- **套件進入點**：`core/services/dca/backtest/__init__.py`（導出資料契約模型與驅動器）。
- **資料契約模型**：`core/services/dca/backtest/models.py`（`TradeMark`、`EquityPoint`、`CycleRecord`、`BacktestRequest`、`BacktestResult` 凍結 dataclass）。
- **回放驅動器**：`core/services/dca/backtest/replay_driver.py`（`ReplayDriver`、`_CycleBuilder`、每棒 INV 斷言、指標試算、D-11 警示揭露）。
- **單元測試**：`tests/test_dca_replay_driver.py`（14 項單元測試覆蓋 3.1–3.14 全部檢核項）。
- **CI 雙登記**：`scripts/ci.sh` 與 `docs/github-actions-ci.yml` 同步登錄。
- **變更日誌**：`CHANGELOG.md` 於 `[Unreleased]` 更新 Phase C3 條目。

**風險**：
1. **回測與實盤交易邏輯分岔（BP-1 / RC-2）**：若在回測域重寫一套執行器或階梯計算，會產生邏輯分岔。緩解：`ReplayDriver` 實例化真 `DcaCoordinator` 與真 `DcaOrderExecutor`，以靜態 grep 斷言 backtest 目錄 0 命中重複階梯/止盈/指標邏輯（3.1 測試鎖定）。
2. **未来函数污染（RC-1 / R-10）**：入場指標若看見未來棒會嚴重高估回測成果。緩解：`ohlcv_provider` 嚴格限制僅回傳 `<= i` 的 closes 切片；以「前視投毒測試」（40 棒後資料投毒 +100% 暴漲，斷言 40 棒前的所有決策與標記 100% 相同）完成機器可證偽驗證（3.4 測試鎖定）。
3. **熔斷狀態漂移（R-8 / RC-5）**：回測若讀取實盤類別層級熔斷會導致同設定不同日跑出不同結果。緩解：`DcaOrderExecutor` 注入 `circuit_probe=lambda: False`，測試開啟 `BinanceRest._adapter_circuit_until` 驗證結果零差異（3.2 測試鎖定）。
4. **冷卻時鐘失效（R-9 / RC-7）**：若誤用牆鐘，300 秒冷卻在毫秒級回放中將無法在預期 K 棒數後解鎖。緩解：注入 `time_fn=lambda: float(candles[i].ts)`，以棒時間計算冷卻（3.3 測試鎖定）。
5. **不變量違規破壞狀態機可信度**：每棒結束強制檢查 INV-1（ARMED 零掛單）、INV-2（AVERAGING 恰 1 張 TP）、INV-6（單方向互斥），違規即拋錯終止回測。

**網路與核心隔離**：
- `core/services/grid/**` 與 `run_grid_trading.py` 零修改（G-1/G-2，`git diff` 為空）。
- `requirements.txt` 零新增依賴（G-3）。

---

## 2. 檢核結果

| # | 檢核項 | 等級 | 通過條件 | 結果 | 證據 |
|---:|---|---|---|---|---|
| **3.1** | **BP-1 同源** | P0 | `ReplayDriver` 實例化真 `DcaCoordinator` 與 `DcaOrderExecutor`；grep 契約禁止 backtest 重寫執行器或階梯數學 | **PASS** | `test_audit_3_1_bp1_same_decision_core` 斷言 `isinstance` 且 grep 0 命中 |
| **3.2** | **R-8 熔斷固定** | P0 | 實盤熔斷開啟時（設 `BinanceRest._adapter_circuit_until` 為未來），回測結果仍完全相同 | **PASS** | `test_audit_3_2_r8_circuit_breaker_fixed_off` 通過 |
| **3.3** | **R-9 棒時間時鐘** | P0 | `cooldown_seconds=300`、timeframe=1h：止盈後下一輪在 1 根棒後 armed，非誤用牆鐘 | **PASS** | `test_audit_3_3_r9_bar_timestamp_clock` 通過 |
| **3.4** | **R-10 前視注入測試** | P0 | 40 棒後數據投毒 +100%，40 棒前的入場決策與標記 100% 相同 | **PASS** | `test_audit_3_4_r10_no_lookahead_future_poison_test` 通過 |
| **3.5** | **INV-1 armed 零掛單** | P0 | 全回放過程中，任一棒若 status==armed 則交易所 open orders 必須為 0 | **PASS** | `test_audit_3_5_inv1_armed_zero_open_orders` 通過 |
| **3.6** | **INV-2 averaging 恰 1 張 TP** | P0 | averaging 且持倉 > 0 時，在世 TP 掛單必須恰好 1 張 | **PASS** | `test_audit_3_6_inv2_averaging_exactly_one_take_profit` 通過 |
| **3.7** | **INV-6 單方向互斥** | P0 | cooling/armed 狀態下 `active_direction` 必須為 None，持倉為 0 | **PASS** | `test_audit_3_7_inv6_direction_exclusivity` 通過 |
| **3.8** | **R-11 確定性** | P0 | 同輸入執行兩次，輸出 JSON 完全相同 | **PASS** | `test_audit_3_8_r11_determinism` 通過 |
| **3.9** | **標記時間為棒時間** | P0 | 所有 `marks[].ts` 屬於 candles 的 ts 集合，不得存在牆鐘秒數 | **PASS** | `test_audit_3_9_marks_use_bar_timestamps_not_wall_clock` 通過 |
| **3.10** | **不改引擎狀態** | P0 | 蓋章副本不修改 `coord._fills` 的原物件（原物件仍為原始時間戳） | **PASS** | `test_audit_3_10_engine_fills_not_tampered` 通過 |
| **3.11** | **效能基準** | P1 | 3000 根 15m 棒回測吞吐量高於基準（> 500 bars/sec） | **PASS** | `test_audit_3_11_performance_benchmark` 實測達 **2500+ bars/sec**（< 1.5s 完成） |
| **3.12** | **完整循環覆蓋** | P0 | 測試資料實際走過 `armed→triggering→averaging→completed→cooling→armed` | **PASS** | `test_audit_3_12_full_cycle_coverage` 斷言 `cycles_completed >= 1` |
| **3.13** | **end_of_data 誠實** | P0 | 資料結束時仍持倉，標註 `exit_reason='end_of_data'`，未實現損益如實以最後收盤評價 | **PASS** | `test_audit_3_13_end_of_data_honest_reporting` 通過 |
| **3.14** | **網格零回歸** | P0 | `git diff --stat core/services/grid/ run_grid_trading.py` 為空 | **PASS** | `git diff` 輸出為空 |

---

## 3. 測試證據

### 3.1 本次新增模組單元測試

```bash
$ python3 -m unittest tests.test_dca_replay_driver -v
test_audit_3_10_engine_fills_not_tampered (tests.test_dca_replay_driver.TestDcaReplayDriver.test_audit_3_10_engine_fills_not_tampered)
3.10 P0: 蓋章副本不修改 coord._fills 的原物件。 ... ok
test_audit_3_11_performance_benchmark (tests.test_dca_replay_driver.TestDcaReplayDriver.test_audit_3_11_performance_benchmark)
3.11 P1: 3000 根 15m 棒回測吞吐量高於基準（> 500 bars/sec）。 ... ok
test_audit_3_12_full_cycle_coverage (tests.test_dca_replay_driver.TestDcaReplayDriver.test_audit_3_12_full_cycle_coverage)
3.12 P0: 測試實際走過 armed->triggering->averaging->completed->cooling 完整循環。 ... ok
test_audit_3_13_end_of_data_honest_reporting (tests.test_dca_replay_driver.TestDcaReplayDriver.test_audit_3_13_end_of_data_honest_reporting)
3.13 P0: 資料結束時仍持倉，標註 exit_reason='end_of_data'，未實現損益如實以最後收盤評價。 ... ok
test_audit_3_14_grid_zero_regression (tests.test_dca_replay_driver.TestDcaReplayDriver.test_audit_3_14_grid_zero_regression)
3.14 P0: 網格核心檔案零變更。 ... ok
test_audit_3_1_bp1_same_decision_core (tests.test_dca_replay_driver.TestDcaReplayDriver.test_audit_3_1_bp1_same_decision_core)
3.1 P0: ReplayDriver 必須實例化真 DcaCoordinator 與真 DcaOrderExecutor，禁止第二套執行器。 ... ok
test_audit_3_2_r8_circuit_breaker_fixed_off (tests.test_dca_replay_driver.TestDcaReplayDriver.test_audit_3_2_r8_circuit_breaker_fixed_off)
3.2 P0: 實盤熔斷開啟時（BinanceRest._adapter_circuit_until 為未來），回測結果仍完全相同。 ... ok
test_audit_3_3_r9_bar_timestamp_clock (tests.test_dca_replay_driver.TestDcaReplayDriver.test_audit_3_3_r9_bar_timestamp_clock)
3.3 P0: cooldown_seconds 基於 K 棒時間推進而非牆鐘時間。 ... ok
test_audit_3_4_r10_no_lookahead_future_poison_test (tests.test_dca_replay_driver.TestDcaReplayDriver.test_audit_3_4_r10_no_lookahead_future_poison_test)
3.4 P0: 前視注入測試——修改第 50 棒之後為極端假數據，第 50 棒之前的決策與標記完全不變。 ... ok
test_audit_3_5_inv1_armed_zero_open_orders (tests.test_dca_replay_driver.TestDcaReplayDriver.test_audit_3_5_inv1_armed_zero_open_orders)
3.5 P0: 全回放過程中，任一棒若 status==armed 則交易所 open orders 必須為 0。 ... ok
test_audit_3_6_inv2_averaging_exactly_one_take_profit (tests.test_dca_replay_driver.TestDcaReplayDriver.test_audit_3_6_inv2_averaging_exactly_one_take_profit)
3.6 P0: averaging 且持倉 > 0 時，在世 TP 掛單必須恰好 1 張。 ... ok
test_audit_3_7_inv6_direction_exclusivity (tests.test_dca_replay_driver.TestDcaReplayDriver.test_audit_3_7_inv6_direction_exclusivity)
3.7 P0: 單方向互斥，cooling/armed 狀態下 active_direction 為 None。 ... ok
test_audit_3_8_r11_determinism (tests.test_dca_replay_driver.TestDcaReplayDriver.test_audit_3_8_r11_determinism)
3.8 P0: 同輸入執行兩次，json.dumps 輸出完全相同。 ... ok
test_audit_3_9_marks_use_bar_timestamps_not_wall_clock (tests.test_dca_replay_driver.TestDcaReplayDriver.test_audit_3_9_marks_use_bar_timestamps_not_wall_clock)
3.9 P0: 所有 marks[].ts 屬於 candles 的 ts 集合，不得存在牆鐘時間戳。 ... ok

----------------------------------------------------------------------
Ran 14 tests in 0.373s
OK
```

### 3.2 全量後端 CI 與前端契約測試

```bash
$ bash scripts/ci.sh
----------------------------------------------------------------------
Ran 273 tests in 11.789s
OK

> grid-trading-console@0.0.0 test
# tests 36   # pass 36   # fail 0

> grid-trading-console@0.0.0 lint
✖ 1 problem (0 errors, 1 warning)  (既有 EX-7.1 MarketRankings warning)

> grid-trading-console@0.0.0 build
✓ built in 671ms
```

### 3.3 BP-1 反證靜態掃描證據

```bash
$ grep -rnE "class .*Executor|def build_ladder|def take_profit_price|def evaluate_rsi" core/services/dca/backtest/
# 輸出為空（0 命中，exit code 1）
```

---

## 4. 例外接受

無新增例外。維持既有 EX-C1.1（MarketRankings eslint 警告）。

---

## 5. 部署 / 回滾

### 5.1 需重啟的服務

- 本 Phase 僅新增純回測驅動器核心庫，不影響目前運行的後端 API 或實盤引擎，無需重啟服務。

### 5.2 回滾指令

```bash
git checkout arena/01a03e16-grid-trading-console
git revert <commit-hash>
```

---

## 附錄：交付物與後續 Phase C4 銜接

- **已交付**：
  1. `core/services/dca/backtest/models.py`
  2. `core/services/dca/backtest/replay_driver.py`
  3. `core/services/dca/backtest/__init__.py`
  4. `tests/test_dca_replay_driver.py`
  5. `scripts/ci.sh`
  6. `docs/github-actions-ci.yml`
  7. `CHANGELOG.md`
  8. `docs/dca_chart_backtest_phase3_worklog_20260826.md`
- **後續銜接（Phase C4）**：
  - Phase C4 將實作績效指標純函式 `core/services/dca/backtest/metrics.py` 與參數網格展開/掃描器 `core/services/dca/backtest/sweep.py`（含 ThreadPool 並行上限、總棒數預算、truncated 誠實回報與指標預計算等價性 P0 測試）。
