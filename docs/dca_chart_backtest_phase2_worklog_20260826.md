# DCA 即時圖表與回測子系統 Phase C2 稽核報告 — `ReplayExchange` 撮合器

> 日期：2026-08-26｜分支：`arena/01a03e16-grid-trading-console`
> 計畫：`docs/dca_chart_backtest_plan_zh-TW.md` §Phase C2（§4.3 撮合規則 R-1～R-13 + 稽核 2.1–2.13）
> 依據標準：`docs/grid_trading_console_audit_standard_zh-TW.md` §5 五段式

---

## 1. 範圍與風險

**範圍**：為 DCA 回測子系統實作純核心、確定性、零 I/O、全 Decimal 的 K 線撮合交易所 `ReplayExchange`，並以單元測試逐條鎖定 13 條撮合規則（R-1 至 R-13）。本階段不接 Coordinator，以直接呼叫 duck-typing 介面測試。

觸及檔案清單：
- **套件進入點**：`core/services/dca/backtest/__init__.py`。
- **K 棒模型**：`core/services/dca/backtest/candles.py`（`Candle` 凍結 dataclass、`make_candle`、`to_candles`、高低價合規性驗證）。
- **撮合器**：`core/services/dca/backtest/replay_exchange.py`（`ReplayExchange`、`ReplayOrderRecord`、`advance(bar_index)`、5 個 duck-typing 方法）。
- **單元測試**：`tests/test_dca_replay_exchange.py`（13 項單元測試覆蓋 2.1–2.13 全部檢核項）。
- **CI 雙登記**：`scripts/ci.sh` 與 `docs/github-actions-ci.yml` 同步登錄。
- **變更日誌**：`CHANGELOG.md` 於 `[Unreleased]` 更新 Phase C2 條目。

**風險**：
1. **未来函数（Lookahead Bias，RC-1）**：若在當前棒決策送出的訂單允許在同一棒撮合成交，會導致基礎單 100% 秒成交並嚴重高估策略獲利。緩解：嚴格實作 R-1，訂單記錄 `placed_bar=i`，`advance(j)` 僅撮合 `placed_bar < j` 的在世訂單（2.1 測試鎖定）。
2. **同棒多單撮合順序套利（RC-6）**：同一根震盪棒若既可成交安全單又可成交止盈單，順序顛倒會高估獲利。緩解：嚴格實作 R-4 保守順序（不利優先：做多先買進安全單，後賣出止盈單；2.5 測試鎖定）。
3. **浮點數精度漂移（K-3 / 2.11）**：計算過程若使用 float 會造成累積捨入誤差。緩解：`replay_exchange.py` 全計算路徑 100% 純 Decimal（grep 0 命中）。
4. **副作用與 I/O 汙染（2.10）**：回測撮合器若意外引入檔案或網路 I/O 會降低效能並破壞確定性。緩解：零 I/O、零 `asyncio.sleep`、零 `datetime.now()`（grep 0 命中）。

**網路與核心隔離**：
- `core/services/grid/**` 與 `run_grid_trading.py` 零修改（G-1/G-2，`git diff` 為空）。
- `requirements.txt` 零新增依賴（G-3）。

---

## 2. 檢核結果

| # | 檢核項 | 等級 | 通過條件 | 結果 | 證據 |
|---:|---|---|---|---|---|
| **2.1** | **R-1 無未来函数** | P0 | 棒 i 送出的限價單，即使棒 i 的 low/high 已穿越，不得在棒 i 成交；棒 i+1 才成交 | **PASS** | `test_audit_2_1_r1_no_lookahead_bias` 斷言 bar 0 advance 回傳 0 筆，bar 1 advance 才成交 |
| **2.2** | **R-2 買單向量** | P0 | `price=100`、棒 `low=99` → 成交於 100；`low=101` → 不成交 | **PASS** | `test_audit_2_2_r2_buy_limit_matching_vector` 斷言 99 成交、101 未成交 |
| **2.3** | **R-3 賣單向量** | P0 | `price=110`、棒 `high=111` → 成交於 110；`high=109` → 不成交 | **PASS** | `test_audit_2_3_r3_sell_limit_matching_vector` 斷言 111 成交、109 未成交 |
| **2.4** | **跳空成交價** | P0 | 買單 `price=100`、棒 `open=95` → 成交價 95；賣單 `price=110`、`open=115` → 成交價 115 | **PASS** | `test_audit_2_4_gap_opening_prices` 斷言買單 min(open, price)=95，賣單 max(open, price)=115 |
| **2.5** | **R-4 同棒保守順序** | P0 | 做多、同一棒同時可成交「安全單買 100」與「TP 賣 105」→ 必須先結算安全單 | **PASS** | `test_audit_2_5_r4_conservative_same_bar_sequence` 斷言安全單在 TP 單前被結算 |
| **2.6** | **R-4 同類排序** | P1 | 做多三張安全單（100/98/96）同棒皆可成交 → 依 100→98→96 順序 | **PASS** | `test_audit_2_6_r4_same_type_safety_order_sorting` 斷言成交序列嚴格依 98 -> 95 -> 92 (高到低) |
| **2.7** | **R-5 市價單跳空** | P0 | 止損觸發棒 `open` 已穿越 → 以 `open` 成交（更差價），非以觸發價 | **PASS** | `test_audit_2_7_r5_market_orders_fill_at_open` 斷言市價單以開盤價 85 成交 |
| **2.8** | **duck-typing 契約完整** | P0 | 五方法齊備且簽名與 `FakeExchange` 相容（以 `DcaOrderExecutor` 實接不拋 `AttributeError`） | **PASS** | `test_audit_2_8_duck_typing_compatibility_with_executor` 實接 DcaOrderExecutor 5 個操作全數通過 |
| **2.9** | **撤單語意** | P0 | `cancel_order` 後該單不再參與後續棒撮合；撤已成交單回傳 False/error | **PASS** | `test_audit_2_9_cancel_order_semantics` 斷言撤單成功且後續棒不觸發成交 |
| **2.10** | **零 I/O** | P0 | `grep -rnE "open\(|sqlite3|requests|aiohttp|asyncio\.sleep|datetime\.now" core/services/dca/backtest/` 0 命中 | **PASS** | 靜態掃描 0 命中（見 §3.3） |
| **2.11** | **Decimal 純度** | P1 | `grep -n "float(" core/services/dca/backtest/replay_exchange.py` 在計算路徑 0 命中 | **PASS** | 靜態掃描 0 命中（見 §3.4） |
| **2.12** | **R-6 不部分成交且文件明示** | P1 | 模組 docstring 含「不模擬部分成交」；成交後 `filled_quantity == quantity` | **PASS** | `test_audit_2_12_no_partial_fills_and_docstring` 斷言整張成交且 docstring 包含警示 |
| **2.13** | **網格零回歸** | P0 | `git diff --stat core/services/grid/ run_grid_trading.py` 為空 | **PASS** | `git diff` 輸出為空 |

---

## 3. 測試證據

### 3.1 本次新增模組單元測試

```bash
$ python3 -m unittest tests.test_dca_replay_exchange -v
test_audit_2_10_zero_io_static_check (tests.test_dca_replay_exchange.TestDcaReplayExchange.test_audit_2_10_zero_io_static_check)
2.10 P0: backtest package must contain zero I/O, sleep, or wall-clock calls. ... ok
test_audit_2_11_decimal_purity_static_check (tests.test_dca_replay_exchange.TestDcaReplayExchange.test_audit_2_11_decimal_purity_static_check)
2.11 P1: replay_exchange.py calculation paths must not use float(). ... ok
test_audit_2_12_no_partial_fills_and_docstring (tests.test_dca_replay_exchange.TestDcaReplayExchange.test_audit_2_12_no_partial_fills_and_docstring)
2.12 P1: Whole order fills (filled == amount) and docstring contains explicit notice. ... ok
test_audit_2_13_grid_zero_regression (tests.test_dca_replay_exchange.TestDcaReplayExchange.test_audit_2_13_grid_zero_regression)
2.13 P0: Grid trading core and runner files must have zero modifications. ... ok
test_audit_2_1_r1_no_lookahead_bias (tests.test_dca_replay_exchange.TestDcaReplayExchange.test_audit_2_1_r1_no_lookahead_bias)
2.1 P0: Order placed at bar i must NEVER match at bar i, only at bar j > i. ... ok
test_audit_2_2_r2_buy_limit_matching_vector (tests.test_dca_replay_exchange.TestDcaReplayExchange.test_audit_2_2_r2_buy_limit_matching_vector)
2.2 P0: Buy limit low <= price -> fills; low > price -> does not fill. ... ok
test_audit_2_3_r3_sell_limit_matching_vector (tests.test_dca_replay_exchange.TestDcaReplayExchange.test_audit_2_3_r3_sell_limit_matching_vector)
2.3 P0: Sell limit high >= price -> fills; high < price -> does not fill. ... ok
test_audit_2_4_gap_opening_prices (tests.test_dca_replay_exchange.TestDcaReplayExchange.test_audit_2_4_gap_opening_prices)
2.4 P0: Gaps execute at open price (min(open, limit) for buy, max(open, limit) for sell). ... ok
test_audit_2_5_r4_conservative_same_bar_sequence (tests.test_dca_replay_exchange.TestDcaReplayExchange.test_audit_2_5_r4_conservative_same_bar_sequence)
2.5 P0: In the same bar, unfavorable safety orders execute BEFORE take profit orders. ... ok
test_audit_2_6_r4_same_type_safety_order_sorting (tests.test_dca_replay_exchange.TestDcaReplayExchange.test_audit_2_6_r4_same_type_safety_order_sorting)
2.6 P1: Multiple safety buy orders in the same bar fill in high-to-low order. ... ok
test_audit_2_7_r5_market_orders_fill_at_open (tests.test_dca_replay_exchange.TestDcaReplayExchange.test_audit_2_7_r5_market_orders_fill_at_open)
2.7 P0: Market stop loss orders fill at bar open price. ... ok
test_audit_2_8_duck_typing_compatibility_with_executor (tests.test_dca_replay_exchange.TestDcaReplayExchange.test_audit_2_8_duck_typing_compatibility_with_executor)
2.8 P0: ReplayExchange fulfills the full 5-method duck-typing contract of DcaOrderExecutor. ... ok
test_audit_2_9_cancel_order_semantics (tests.test_dca_replay_exchange.TestDcaReplayExchange.test_audit_2_9_cancel_order_semantics)
2.9 P0: Cancelled order no longer matches, cancelling non-open order returns error. ... ok

----------------------------------------------------------------------
Ran 13 tests in 0.013s
OK
```

### 3.2 全量後端 CI 與前端契約測試

```bash
$ bash scripts/ci.sh
----------------------------------------------------------------------
Ran 259 tests in 11.580s
OK

> grid-trading-console@0.0.0 test
# tests 36   # pass 36   # fail 0

> grid-trading-console@0.0.0 lint
✖ 1 problem (0 errors, 1 warning)  (既有 EX-7.1 MarketRankings warning)

> grid-trading-console@0.0.0 build
✓ built in 732ms
```

### 3.3 零 I/O 靜態掃描證據

```bash
$ grep -rnE "open\(|sqlite3|requests|aiohttp|asyncio\.sleep|datetime\.now" core/services/dca/backtest/
# 輸出為空（0 命中，exit code 1）
```

### 3.4 Decimal 純度靜態掃描證據

```bash
$ grep -n "float(" core/services/dca/backtest/replay_exchange.py
# 輸出為空（0 命中，exit code 1）
```

---

## 4. 例外接受

無新增例外。維持既有 EX-C1.1（MarketRankings eslint 警告）。

---

## 5. 部署 / 回滾

### 5.1 需重啟的服務

- 本 Phase 僅新增純撮合核心庫，不影響目前運行的後端 API 或實盤引擎，無需重啟服務。

### 5.2 回滾指令

```bash
git checkout arena/01a03e16-grid-trading-console
git revert <commit-hash>
```

---

## 附錄：交付物與後續 Phase C3 銜接

- **已交付**：
  1. `core/services/dca/backtest/__init__.py`
  2. `core/services/dca/backtest/candles.py`
  3. `core/services/dca/backtest/replay_exchange.py`
  4. `tests/test_dca_replay_exchange.py`
  5. `scripts/ci.sh`
  6. `docs/github-actions-ci.yml`
  7. `CHANGELOG.md`
  8. `docs/dca_chart_backtest_phase2_worklog_20260826.md`
- **後續銜接（Phase C3）**：
  - Phase C3 將實作 `ReplayDriver`（回放驅動器），實例化**真正的** `DcaCoordinator` 與 `DcaOrderExecutor`，注入棒時間時鐘、歷史 closes 切片與關閉的熔斷 probe，驗證 BP-1 同源、前視注入測試與每棒 INV-1/INV-2/INV-6 狀態守衛。
