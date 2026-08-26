# DCA 機器人 Phase 1 工作紀錄 — 領域模型與指標引擎（純函式核心，零 I/O）

> 執行日期：2026-08-25
> 分支：`arena/01a039a6-grid-trading-console`（由 `arena/01a0386c-grid-trading-console` @ `4e7a6b2` 分支）
> 依據文件：`docs/dca_bot_implementation_plan_zh-TW.md` Phase 1（§5）與稽核標準 1.1–1.18
> 依據稽核標準：`docs/grid_trading_console_audit_standard_zh-TW.md` §5 五段式格式

---

## 1. 範圍與風險

**範圍**：建立 `core/services/dca/` 純函式核心——領域模型（DcaConfig / DcaState / DcaOrder / DcaStatistics）、四種技術指標（RSI / MACD / Bollinger / Price Change）、入場評估器（EntryEvaluator）、階梯數學（ladder.py），以及三個 unittest 測試模組與計畫 §11 的三支獨立驗證腳本。

**涉及面**：
- mode：僅 PAPER 語意層（本 Phase 不含任何下單路徑；LIVE feature flag 閘門屬 Phase 4）。
- 交易所：不觸碰任何交易所程式碼；`VALID_EXCHANGES` 沿用 `yaml_generator.py`，做空能力表以 module-level 常數定義（前哨防護，P3 接 YAML 層時生效）。
- mutation：僅新增檔案 + `scripts/ci.sh`、`docs/github-actions-ci.yml`、`CHANGELOG.md` 三處既有檔追項。
- 資料來源：無（純函式；所有輸入由呼叫方注入）。

**風險與緩解**：
| 風險 | 緩解 |
|---|---|
| 數學分岔（float 累積誤差 / off-by-one）導致基礎層錯誤而全案疊錯 | §4.5 鎖定向量 A/B + §4.3 指標鎖定向量以 1e-6 容差凍結；Price Change 同時斷言 lookback=24/23 兩個值鎖死索引語意；IIR 收斂以 300 vs 301 / 50 vs 2500 雙向斷言 |
| 新套件拖垮網格（G-1/G-2） | 全新目錄、零既有檔修改；grep 驗證 `core/services/grid/`、`run_grid_trading.py` diff 為空 |
| 測試不進 CI 而成為廢測試 | 三模組登入 `scripts/ci.sh` 與 `docs/github-actions-ci.yml`，全量 117 tests 通過 |

---

## 2. 檢核結果（Phase 1 稽核表 1.1–1.18）

| # | 項目 | 等級 | 結果 | 證據 |
|---|---|---|---|---|
| 1.1 | 階梯向量 A 全數對上 | P0 | ✅ PASS | `tests/test_dca_ladder.py::LadderVectorTests::test_vector_a_all_nine_values`（9 值 @1e-6） |
| 1.2 | 階梯向量 B 全數對上 | P0 | ✅ PASS | `test_vector_b_all_four_values`（4 值 @1e-6） |
| 1.3 | 四種組合皆有測試且 required 單調不減 | P0 | ✅ PASS | `LadderCombosTests` 4 組合 × `_cumulative_monotonic_and_required` + `test_position_mode_diverges_from_order_mode_at_deeper_levels` |
| 1.4 | 做空階梯對稱性 | P0 | ✅ PASS | `test_short_ladder_is_step_mirror_of_long`：逐層步進偏移鏡像 @1e-9（步進因子 (1±dev_i) 為 §4.5 凍結公式；多層累積乘積因 (1±a)(1±b) 不天然對稱，故稽核口徑鎖定於逐層步進偏移 = 公式直接推論） |
| 1.5 | 指標鎖定向量 | P0 | ✅ PASS | `LockedVectorTests`（RSI 21.977841 / MACD line −2.240581 / signal −0.784542 / hist −1.456039 / BB 105.193225 / 116.876210 / 128.559195） |
| 1.5b | Price Change 索引語意 | P0 | ✅ PASS | `test_price_change_locked_and_off_by_one` 同時斷言 24→−11.782277(close[119]/close[95]) 與 23→−12.373066 |
| 1.5c | IIR 收斂 | P0 | ✅ PASS | `ConvergenceTests`：300 vs 301 < 1e-6；50 vs 2500 > 1e-2 反向斷言 |
| 1.5d | 判定方式全覆蓋 | P1 | ✅ PASS | `RsiConditionTests` / `MacdConditionTests` / `BollingerConditionTests`：各 condition × long/short，皆含反向不觸發負向斷言 |
| 1.5e | 指標參數預設值 | P0 | ✅ PASS | `DefaultsTests`（RSI 14/30/70/level、MACD 12/26/9/cross、BB 20/2.0/touch、PC 24/∓3.0、timeframe 15m、fetch_limit 300、close） |
| 1.6 | 暖機期正確 | P0 | ✅ PASS | `WarmupTests`：RSI=14 / MACD line=25 / signal=33 / BB=19 / PC=24；暖機內 insufficient_data=True 且 triggered=False |
| 1.7 | 資料不足不得誤判 | P0 | ✅ PASS | `InsufficientDataTests`：空/1 根/3 根 × 4 指標 × 雙方向，全部 triggered=False、不拋例外 |
| 1.8 | 未收線 K 棒被丟棄 | P0 | ✅ PASS | `UnclosedBarTests`：n 根全收線 vs n+1 根尾棒未收線，value/triggered/details 完全一致 |
| 1.9 | entry.logic AND/OR | P1 | ✅ PASS | `EntryEvaluatorTests`：all 任一 False 不觸發、any 任一 True 觸發；空指標 + indicator mode 於 EntryConfig 拋中文錯誤 |
| 1.10 | confirm_bars 抗插針 | P1 | ✅ PASS | `test_confirm_bars_requires_consecutive_holds`（2/3 根對比 + 間斷毛刺案例） |
| 1.11 | immediate 模式 | P0 | ✅ PASS | `test_immediate_always_true_and_calls_no_indicator`：MagicMock 斷言 call_count == 0，輸入空序列亦恆 True |
| 1.12 | 狀態機非法轉移 | P0 | ✅ PASS | `StateMachineTests`：10 組非法轉移拋 InvalidDcaTransition；合法路徑全覆蓋含自迴圈、terminal 狀態無出邊 |
| 1.13 | 設定驗證邊界 | P0 | ✅ PASS | `ConfigValidationTests`：max_safety_orders=0 / price_deviation=0 / size_multiplier=0.9 / tp=0 / leverage=200 / both+不支援做空交易所，全部中文明確錯誤 |
| 1.14 | Decimal 純度 | P1 | ✅ PASS | `grep -n "float(" core/services/dca/ladder.py` → 0 命中；`test_no_float_calls_in_ladder_source` 於測試內固化；`test_all_money_fields_are_decimal` |
| 1.15 | 零新依賴 | P1 | ✅ PASS | `git diff requirements.txt` 空；`grep -rn "import pandas\|import numpy\|import talib" core/services/dca/` → 0 命中 |
| 1.16 | 零 I/O | P0 | ✅ PASS | `grep -rnE "requests\|aiohttp\|open\|sqlite3\|asyncio" core/services/dca/{models,indicators,entry,ladder.py}` → 0 命中 |
| 1.17 | 測試進 CI | P1 | ✅ PASS | `scripts/ci.sh` 與 `docs/github-actions-ci.yml` 已登錄三模組；`SKIP_FRONTEND=1 bash scripts/ci.sh` → 117 tests OK |
| 1.18 | 網格回歸 | P0 | ✅ PASS | 四模組 21 tests OK；`git diff --stat core/services/grid/ run_grid_trading.py` 為空 |

**退出條件**：18 項全數 PASS（無例外接受項目）。

---

## 3. 測試證據

環境：Python 3.11.2（沙箱）；`scripts/ci.sh` 以 `SKIP_FRONTEND=1` 執行（本 Phase 未動 `web_ui/`，前端不涉入；Phase 5 起補前端 gate）。

```text
$ python3 -m unittest tests.test_dca_ladder tests.test_dca_indicators tests.test_dca_config_and_state -v
Ran 77 tests in 0.086s
OK

$ SKIP_FRONTEND=1 bash scripts/ci.sh
Ran 117 tests in 1.228s
OK                      # 既有 40 tests + 新增 77 tests

$ python3 -c "import core.services.dca as d; print('import ok')"
import ok

$ git diff --stat -- requirements.txt
（空）

$ python3 scripts/verify_dca_vectors.py      # §11 獨立參考實作交叉驗證
DCA ladder vector A: PASS
DCA ladder vector B: PASS
$ python3 scripts/verify_dca_indicators.py
DCA indicator vectors: PASS
$ python3 scripts/verify_dca_convergence.py
DCA indicator convergence (fetch_limit=300): PASS
```

grep 稽核輸出節錄：

```text
$ grep -n "float(" core/services/dca/ladder.py              → exit 1（0 命中）
$ grep -rn "import pandas\|import numpy\|import talib" core/services/dca/  → exit 1（0 命中）
$ grep -rnE "requests\|aiohttp\|open\(|sqlite3\|asyncio" core/services/dca/{models,indicators,entry,ladder.py}  → exit 1（0 命中）
$ git diff --stat core/services/grid/ run_grid_trading.py   → （空）
```

**環境缺口與處置**：沙箱 Python 採 PEP 668 外部管理且預設未安裝 `injector` / `ccxt` / `fastapi` 等（皆為 `requirements.txt` 既有依賴，非本次新增）。已以 `pip install --user --break-system-packages` 補齊 `injector==0.21.0`、CI 依賴組（fastapi 0.104.1 等）與 `ccxt`，方使 `python3 -c "import core.services.dca"` 與完整 CI 段落可執行。此為沙箱環境補件，專案依賴清單零異動。

---

## 4. 例外接受

無。18 項稽核（含 9 項 P0）全數通過，無 P1/P2 未修項目。

---

## 5. 部署 / 回滾

- **Feature flag**：本 Phase 不含任何運行期入口；新增程式不被任何既有模組 import（`run_multiple_grids.py` / `web_ui/backend` 皆未接線，接線為 Phase 3/4），`requirements.txt` 無異動。
- **服務重啟**：不需要。僅新增獨立套件與測試，無活動服務載入本套件；經檢查沙箱無 8001/PM2 服務在跑（若有，亦無需重啟，因無程式共用路徑變更）。
- **資料庫 migration**：無。
- **Health checks**：`python3 -c "import core.services.dca as d"` + `SKIP_FRONTEND=1 bash scripts/ci.sh` 綠燈即為本 Phase 健康檢查。
- **回滾步驟**：`git revert` 本 Phase commit 即完整移除；因無既有檔行為變更，回滾無連鎖影響。

---

## 附錄 A — 交付物清單（18 新檔 + 4 改檔）

```
core/services/dca/__init__.py
core/services/dca/ladder.py                      # build_ladder / required_capital / take_profit_price / stop_loss_price
core/services/dca/models/__init__.py
core/services/dca/models/dca_config.py           # DcaConfig / EntryConfig / 4 個 Enum / DcaConfigError（全中文驗證）
core/services/dca/models/dca_state.py            # DcaState / DcaStatus / SafetyOrderSlot / LEGAL_TRANSITIONS / InvalidDcaTransition
core/services/dca/models/dca_order.py            # DcaOrder / DcaOrderRole / build_client_order_id（dca_{hash8}_{leg}_{slot}）
core/services/dca/models/dca_statistics.py       # DcaStatistics.to_display_dict()（對齊 GridStatistics，未知值 None）
core/services/dca/indicators/__init__.py         # evaluate_indicator / indicator_trigger_series 分派
core/services/dca/indicators/base.py             # IndicatorResult / drop_unclosed / first_valid_index
core/services/dca/indicators/config.py           # §4.3 預設值唯一真相來源
core/services/dca/indicators/rsi.py              # Wilder
core/services/dca/indicators/macd.py
core/services/dca/indicators/bollinger.py        # 母體標準差
core/services/dca/indicators/price_change.py     # 鎖定索引語意
core/services/dca/entry/__init__.py
core/services/dca/entry/evaluator.py             # EntryEvaluator / EntryVerdict
tests/services/dca/__init__.py
tests/test_dca_ladder.py                         # 稽核 1.1/1.2/1.3/1.4/1.14（26 tests）
tests/test_dca_indicators.py                     # 稽核 1.5/1.5b/1.5c/1.5d/1.5e/1.6/1.7/1.8（31 tests）
tests/test_dca_config_and_state.py               # 稽核 1.9/1.10/1.11/1.12/1.13（20 tests）
scripts/verify_dca_vectors.py                    # §11 附錄落地（獨立參考實作）
scripts/verify_dca_indicators.py
scripts/verify_dca_convergence.py
scripts/ci.sh                                    # +3 測試模組（改）
docs/github-actions-ci.yml                       # +3 測試模組（改，與 ci.sh 同步）
CHANGELOG.md                                     # [Unreleased] 追項（改）
docs/dca_bot_phase1_worklog_20260825.md          # 本文件（新）
```

## 附錄 B — 下一 Phase 銜接點

- Phase 2 直接使用 `ladder.build_ladder()` 產生 `SafetyOrderSlot` 階梯，經 `DcaState.init_slots()` 初始化虛擬階梯；活躍窗口（D-4）遞補邏輯尚未實作。
- `EntryEvaluator` 的 `series_fns` 注入點與 `evaluate_indicators()` 已為 Phase 3 的 OhlcvCache 管線預留（closes 由呼叫方注入，本套件不拉取）。
- `build_client_order_id()` 命名空間為 Phase 2 INV-1「armed 不掛單」機器驗證斷言的計數鍵。
