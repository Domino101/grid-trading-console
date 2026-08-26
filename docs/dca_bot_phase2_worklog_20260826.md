# DCA 機器人 Phase 2 工作紀錄 — 執行引擎與狀態機（armed → triggering → averaging → completed）

- 日期：2026-08-26
- 計畫依據：`docs/dca_bot_implementation_plan_zh-TW.md` §6 Phase 2
- 提交：`9dc0f18`（Phase 1 重建）、`83ed606`（Phase 2）
- 分支：`arena/01a039a6-grid-trading-console`

## 1. 範圍與風險

本 Phase 把 Phase 1 的純函式核心接上交易所抽象層，實作完整狀態機迴圈。
依計畫前置條件：不接 FastAPI / YAML / 前端 / 真實交易所與 DB，全部以
FakeExchange 確定性驅動。

交付物：

| 路徑 | 內容 |
| --- | --- |
| `core/services/dca/interfaces/` | `IDcaOrderExecutor` 抽象介面、`OrderOutcome`/`OutcomeKind` 四態分類 |
| `core/services/dca/execution/dca_order_executor.py` | 包裝 ExchangeInterface：quantize、min_notional 拒送、熔斷雙源守衛、命名空間 client id |
| `core/services/dca/coordinator/dca_coordinator.py` | 主狀態機：armed/triggering/averaging/degraded/completed/cooling 迴圈、成交漏斗、窗口遞補/急跌重對齊/曝險上限、止損監控、start/stop/pause/resume |
| `core/services/dca/coordinator/{entry_monitor,take_profit_manager,direction_lock}.py` | 入場輪詢、TP 原子重掛（cancel-then-place）、both 單向互斥鎖 |
| `core/services/dca/models/dca_state.py` | 新增 `DEGRADED` 狀態與 `averaging ⇄ degraded` 合法轉移（稽核 2.6） |
| `tests/test_dca_{executor,coordinator}.py` + `tests/services/dca/fake_exchange.py` | 40 項測試（稽核 2.1–2.21）＋確定性交易所囈語 |
| `scripts/ci.sh`、`docs/github-actions-ci.yml` | 兩模組登入 CI（無回退、全量延伸） |

主要風險與緩解：

- **R-3 雙 TP 超賣**：TP 重掛嚴格 cancel-then-place；cancel 成功但 place 失敗 → `degraded` 逐輪重試，絕不靜默無 TP（稽核 2.6 以故障注入鎖定）。
- **熔斷期誤下單**：執行器在下單前查 `BinanceRest._is_adapter_circuit_open()` 與 `GridEngineImpl._shared_api_circuit_until` 雙來源（唯讀，不修改 grid 檔，G-1 成立）；熔斷中 `triggering` 延後而非 `failed`。
- **task 洩漏（Phase 3 熱插拔前置）**：`start()` 建立的 task 全註冊 `self._tasks`，`stop()` 逐一 cancel + await；測試斷言協調器持有 task 數歸 0。
- **急跌穿透窗口**：偵測下張計畫價高於現價（做空相反）即以現價重新對齊剩餘槽位並記錄 `window_realigned`；槽位配額按 index 計入、不重置，安全單下單總次數恆 ≤ N（INV-3）。

## 2. 檢核結果（Phase 2 稽核表 2.1–2.22）

全部 22 項通過；映射如下（✓ = 有專屬測試斷言）：

| 稽核 | 項目 | 結果 | 對應測試 |
| --- | --- | --- | --- |
| 2.1 | INV-1：armed 20 輪交易所 0 掛單（create 0 次） | ✓ | `test_inv1_armed_never_places_orders`（全實管線：EntryMonitor + PriceChange 指標 + 注入 K 線） |
| 2.2 | 觸發後下一輪恰好 1 次 create_order（role=base，qty=V₀/E quantize） | ✓ | `test_trigger_places_exactly_one_base_next_round` |
| 2.3 | 基礎單成交 → min(window,N)+1 TP（預設 5+1=6） | ✓ | `test_default_window_plus_one_tp` |
| 2.3b | 活躍窗口遞補：逐層成交後窗口維持 5 筆 + 1 TP | ✓ | `test_window_replenishes_after_each_fill` |
| 2.3c | 急跌穿透：以現價重對齊＋`window_realigned`，跳過層不重複計入配額 | ✓ | `test_flash_crash_realigned_below_market` |
| 2.4 | INV-2：TP 恰好 1 筆且價格=凍結公式（quantize 後） | ✓ | `test_inv2_single_tp_at_formula_price` |
| 2.5 | 安全單成交 → TP 原子重掛（cancel 先於 create），新價對應新均價 | ✓ | `test_tp_replaced_atomically_after_safety_fill` |
| 2.6 | TP cancel 成功但 place 失敗 → degraded、下一輪自動重試成功、期間不得回報正常 | ✓ | `test_tp_place_failure_degrades_then_recovers` |
| 2.7 | INV-3：穿透全部階梯，safety 下單總次數 == N，絕不第 N+1 張 | ✓ | `test_inv3_never_places_more_than_n_safeties` |
| 2.8 | 曝險上限觸及停止攤平＋`position_cap_reached`（只記一次） | ✓ | `test_position_cap_stops_averaging_with_event` |
| 2.9 | TP 成交 → completed：殘餘安全單撤銷、掛單數歸 0 | ✓ | `test_tp_fill_completes_round_and_clears_all_orders` |
| 2.10 | repeat_after_take_profit 循環語意（cooling → armed、active_direction=None；false → stopped） | ✓ | `test_repeat_cycle_cooling_then_rearmed_direction_free`、`test_repeat_disabled_stops_after_completed` |
| 2.11 | max_cycles=2 第 2 輪止盈後 stopped | ✓ | `test_max_cycles_stops_after_nth_round` |
| 2.12 | stop()：撤單、不平倉（無 market 單）、stopped、publish_lifecycle('stopped') | ✓ | `test_stop_cancels_orders_keeps_position_publishes_stopped` |
| 2.13 | stop() 三次：cancel 不重複、不拋例外 | ✓ | `test_stop_is_idempotent` |
| 2.14 | 熔斷守衛：triggering 延後（不下單、非 failed）；關閉後自動續行 | ✓ | `test_triggering_defers_under_circuit_then_proceeds`＋執行器 3 項 |
| 2.15 | 精度 quantize＋低於 min_notional 拒送交易所並記錄 | ✓ | 執行器層 `test_min_notional_rejects_without_exchange_call` 等＋協調器 `test_safety_below_min_notional_skipped_not_sent` |
| 2.16 | 成交 WS+REST 雙路 order_id 去重（均價只更新一次） | ✓ | `test_ws_and_rest_duplicate_fill_applied_once` |
| 2.17 | INV-6：both 互斥；同根雙向成立 → tiebreak＋`both_directions_matched`；鎖定期間無反向單 | ✓ | `test_both_directions_matched_locks_by_tiebreak` |
| 2.17b | 跨輪換向：第 1 輪 long → cooling → armed(None) → 第 2 輪 short；無殘留掛單 | ✓ | `test_both_can_reverse_direction_next_round` |
| 2.18 | 狀態轉移覆蓋（triggering 下單失敗 → failed＋發布；armed 直停路徑） | ✓ | `test_base_order_error_transitions_to_failed`＋Phase 1 狀態機測試 |
| 2.19 | start() 後 stop()：協調器持有 task 數歸 0 | ✓ | `test_start_tasks_are_joined_on_stop` |
| 2.20 | 不阻塞 event loop：`grep time.sleep core/services/dca` == 0 | ✓ | `test_no_blocking_sleep_in_dca_package` |
| 2.21 | 日誌規範：`grep print( core/services/dca` == 0 | ✓ | `test_no_print_calls_in_dca_package` |
| 2.22 | 網格既有測試零回歸 | ✓ | 全量 CI 157 項全綠（見 §3） |

## 3. 測試證據

```
$ python3 -m unittest tests.test_dca_coordinator tests.test_dca_executor
Ran 40 tests ... OK

$ python3 -m unittest discover -s tests -p "test_dca_*.py"
Ran 117 tests (Phase 1: 77 + Phase 2: 40) ... OK

$ SKIP_FRONTEND=1 bash scripts/ci.sh
Ran 157 tests ... OK    （稽核 2.22：網格既有全部測試零回歸）
```

工作階段中四個一度失敗並修正的點（皆測試側問題，核心邏輯無更動）：

1. INV-1 測試原以 stub 闢徑 → 改為全實管線（真 EntryMonitor +
   `PriceChangeConfig(long_threshold_percent=-99)` 絕不成立門檻＋平底 K 線注入）。
2. 曝險上限計口徑原只算已成交成本 → 補入「已掛在交易所的安全單名目」，
   否則窗口會把掛單名目超過 cap（測試先紅後綠）。
3. max_cycles 測試因注入時鐘與冷卻計算起點競態卡住 → 改 cooldown=0 確定性流程。
4. 靜態掃描把協調器 docstring 自身的「time.sleep」字樣當違規 → 改為「同步阻塞 sleep」表述。

## 4. 例外接受

1. **Commit精炼度**：`models/dca_state.py`（Phase 1 產物）內含本 Phase 啟用的
   `DEGRADED` 狀態，隨 Phase 1 commit 一併交付；CHANGELOG 與 CI 登入檔同含
   Phase 1/2 內容並隨 Phase 2 commit 歸檔。git 歷史仍以兩筆 commit 區分 Phase。
2. **稽核 2.15 協調器層以白箱調升 `min_notional` 模擬**：`fixed_value` 模式下安全單
   名目恆 ≥ 基礎單名目，常態參數無法得到「基礎單通過而安全單被拒」；拒絕路徑在
   執行器層另有直接黑箱測試覆蓋。
3. **WS 成交推送：** Phase 2 測試以手工呼叫 `coordinator.handle_ws_fill()` 模擬；
   真實 `WsFillListener` 為 Phase 3 交付物，兩路已在 `_fill_funnel` 匯流並經
   order_id 去重驗證。
4. `EntryMonitor` 的 OHLCV provider 介面已定（`async (symbol, timeframe, limit) -> list[float]`），
   正式實作由 Phase 3 的共享 `OhlcvCache` 提供。

## 5. 部署 / 回滾

- 本 Phase 無任何執行期入口改動：不接 API / YAML / 前端；`core/services/grid/`、
  `run_grid_trading.py` 零異動（G-1/G-2）；requirements.txt 零新依賴（G-3）。
- 回滾：`git revert 83ed606`（及必要時 `9dc0f18`）即可，既有 CI 測試不受影響。
- 未來啟用路徑（Phase 3）：監督者建構 `DcaCoordinator` 並注入真實
  ExchangeAdapterPool 實例與 OhlcvCache，接 `--paper` 模式（G-9 PAPER only）。

## 附錄 A — 交付物清單（Phase 2）

新增（10）：
`core/services/dca/interfaces/__init__.py`、`interfaces/dca_executor.py`、
`execution/__init__.py`、`execution/dca_order_executor.py`、
`coordinator/__init__.py`、`coordinator/dca_coordinator.py`、
`coordinator/entry_monitor.py`、`coordinator/take_profit_manager.py`、
`coordinator/direction_lock.py`、`tests/services/dca/{__init__,fake_exchange}.py`、
`tests/test_dca_executor.py`、`tests/test_dca_coordinator.py`

修改（4）：
`core/services/dca/models/dca_state.py`（DEGRADED）、`CHANGELOG.md`、
`scripts/ci.sh`、`docs/github-actions-ci.yml`

## 附錄 B — 下一 Phase 銜接點（Phase 3：機器人監督者與熱插拔）

- `DcaCoordinator(config, bot_id, executor, entry_monitor=EntryMonitor(config, ohlcv_cache), lifecycle_publisher=...)`
- OHLCV provider 介面：`OhlcvProvider = async (symbol, timeframe, limit) -> list[float]`
- WS 成交：`await coordinator.handle_ws_fill(client_order_id, price, quantity)`
- lifecycle publisher 接 `runtime_repository`；`build_statistics(last_price)` 為顯示層唯一入口
- 熔斷雙源已由執行器預設探針唯讀接通，無須重寫
