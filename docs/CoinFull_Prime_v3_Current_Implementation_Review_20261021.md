# CoinFull Prime v3 現行實作審閱（0.3.13）

> 2026-10-21 P0 修復：A-1～A-5 已於同日修復並補回歸測試，見 [P0 Remediation Worklog](CoinFull_Prime_v3_P0_Remediation_20261021.md)。P1（C-1～C-7）亦已完成，見 [P1 Remediation Worklog](CoinFull_Prime_v3_P1_Remediation_20261021.md)。本文保留為修復前審閱快照。

- 審閱日期：2026-10-21
- 審閱基線：`fa90734`（VERSION 0.3.13）
- 主要依據：`docs/CoinFull_Prime_v3_Engineering_Spec_and_Optimization_Master.md`（SPEC-CFP-V3-20261018）、`docs/CoinFull_Prime_v3_Audit_Report_Generated_20261018.md`、`docs/CoinFull_Prime_03_NoTrade_Trigger_Spec.md`、`docs/CoinFull_Prime_04_Position_Sizing_Timing.md`
- 方法：文件演進比對、靜態行級審閱、目標測試、ledger 一致性稽核。未連線交易所，未重跑歷史回測。

## 0. 結論

主架構已符合 v3 Opt C/D 的核心精神：Top 5 多頭、BTC 50% 基準空頭、每週四 UTC 00:05 換倉、-12% 週內硬止損、3 因子風險閘門、Opt D 的成交額與 ATR 初篩。2026-10-18 稽核的 P0 項目（`log_only=true`、`top_n=79`、`settlements=3`、ATR 未接線）已在 0.3.12 修復，四項不再成立。

但「生產就緒」仍不成立。現行版本有 5 個直接影響資金或記帳不變式的缺口，集中在停止事件與籃子結算，而不是選幣模型：

| 層面 | 判定 | 說明 |
|---|---|---|
| 選幣與配置 | 符合 | 7/14/30 動能、9 次費率平滑、0.6/0.4 權重、Top 5、BTC 50% |
| Opt D | 部分符合 | 成交額與 ATR 已接線；ATR 對缺資料 fail-open、回補會放寬；RESTRICTED_TAGS 無配置 |
| 風險閘門 | 部分符合 | 3 因子、pts=0 全平、log_only=false 已到位；`degraded` 仍會換倉 |
| 週內止損 | 功能存在，安全語意不足 | dry-run 或 adapter 缺失時仍結算籃子；與換倉無互斥 |
| 統一記帳 | 部分符合 | 走 `StrategyLedger`；但名單同步可跳過 `apply_basket_fill` 關閉成分，資金費率未入帳 |
| 營運就緒 | 不符合 | ledger audit 紅燈；本機無交易進程；排程停止會同時停止止損守護 |

## 1. 演進脈絡

| 階段 | 文件／版本 | 核心變化 | 結果 |
|---|---|---|---|
| v1 稽核 | 2026-09-16 `CoinFull_Prime_Audit_Report_2026-09-16.md` | 每日換倉、19 幣、做空 Bottom 5 小幣 | 重建績效 -76.4%，空頭端負 Alpha |
| v2 優化 | 2026-09-16/17 `CoinFull_Prime_01`～`04`、`75d_Backtest` | 改每週換倉、多時距動能、費率平滑、風險閘門 | 週轉與費用大降；但空頭小幣仍累虧 -284U |
| v3 重構 | 2026-10-18 `..._v3_Engineering_Spec...Master.md`、`..._v3_Comparative_Backtest_Report.md` | Opt A/B/C/D：BTC 基準對沖、-12% 硬止損、Opt D 質量過濾 | 回測 ROI 70.57%、MaxDD -3.40%、Sharpe 6.27 |
| 0.3.10 | `84bb5e6` | 實作 benchmark/dual hedge、止損 daemon、自適應 UI | 主鏈路落地 |
| 0.3.11 | `f3851d1` | 換倉鎖、翻倉拆單、1x 槓桿預設、資金規模互斥、dry-run 籃子隔離 | 修 MC-1/2/3/4 的主要路徑 |
| 0.3.12 | `0d98f9c` | top_n 80、結算 9 次、log_only=false、ATR/成交量/標籤過濾、D-09 回補復檢 | 修掉 2026-10-18 稽核的 P0 |
| 0.3.13 | `4408f42`、`b6f62ce`、`fa90734` | 版本對齊、網格幽靈單、公開 repo 安全 | CoinFull Prime 無新變更 |

關鍵事實：最新的一份 CoinFull Prime 稽核報告（2026-10-18）在 0.3.12 之前產生，因此其中 D-03（ATR 未接線）、D-04（tags 缺失）、D-08（log_only）已部分或全部過時。本報告以現行程式碼為準，不重複列入已修項目。

## 2. 與最新報告的逐項對照

| Master Spec 要求 | 現行實作 | 判定 | 證據 |
|---|---|---|---|
| 多頭 Top 5，每檔 10% 名義 | `long_count=5`、`0.5/5` | 符合 | `quant_engine.py:589-595` |
| 空頭 BTC 50% | `hedge.mode=benchmark`、weight 0.50 | 符合 | `quant_engine.py:600-633` |
| 每週四 UTC 00:05 | `rebalance_days=7 / weekday=3 / minute=5` | 符合 | `config...yaml:86-93`、`service.py:1213-1245` |
| 動能 0.5R7+0.3R14+0.2R30 | 同式，含 overheat penalty | 符合 | `quant_engine.py:324-341` |
| 費率 9 次平滑 | `settlements=9` | 符合 | `config...yaml:49-51` |
| Score=0.6Z(Mom)+0.4Z(-Fund) | 同式 | 符合 | `config...yaml:32-36` |
| 3 因子閘門、pts=0 全平 | 3 因子、flatten、`log_only=false` | 符合 | `regime_filter.py:93-203`、`config...yaml:101-104` |
| 資料缺漏禁止調倉 | 只記 `degraded`，仍繼續換倉 | 不符合 | `service.py:799-886` |
| Opt D 成交額 50M | 已生效 | 符合 | `quant_engine.py:544-548` |
| Opt D ATR 20% | 已接線，但缺值放行、回補放寬 | 部分符合 | `quant_engine.py:549-553`、`:579-583` |
| Opt D RESTRICTED_TAGS | 程式支援精確 symbol 黑名單，配置無此欄位 | 不符合 | `quant_engine.py:102-104`、`config...yaml:14-29` |
| -12% 市價 reduceOnly 止損 | 有，但 dry-run/adapter 缺失仍結算 | 部分符合 | `rebalancer.py:828-947` |
| 止損狀態持久化 | `bot_baskets` + `apply_basket_fill` | 符合 | `ledger_store.py:1096-1160` |
| 統一記帳 | 全走 `StrategyLedger` | 符合 | `tests.test_accounting_path_policy` 12/12 |
| 資金費率 Carry 可追溯 | 只當因子，未入帳 | 不符合 | `service.py:1029-1099` 無 equity/funding |
| 交易摩擦扣減 | 實盤記交易所 fee，無滑價估算 | 部分符合 | `rebalancer.py:631-674` |

## 3. Act on（阻斷發布項）

### A-1 停止事件在 dry-run 或 adapter 缺失時仍關閉帳本部位

**證據**

- `core/services/coinfull_prime/rebalancer.py:863`：只有在 `not dry_run and exchange_adapter` 時才送出平倉單。
- `rebalancer.py:884-895`：不論是否有送單，接著呼叫 `ledger.apply_basket_fill(..., reduce_only=True)`。
- `rebalancer.py:938-947`：回傳 `status="filled"`。
- `service.py:1422-1428`：daemon 以 `dry_run=self.scheduler_dry_run` 呼叫。
- `service.py:1559-1560`：`binance_live` 在未顯式指定 `dry_run` 時，預設 `scheduler_dry_run=True`。
- `tests/test_coinfull_prime_rebalance.py:336-357`：測試明確以 `dry_run=True` 驗證籃子被清空，等於把此行為固定成契約。

**影響**

`binance_live` 若以預設 dry-run 啟動排程，止損觸發時不會送出任何交易所訂單，但 `bot_baskets` 會把該多頭標記為 closed、寫入 `trade_fills` 與 `stop_loss` 事件。交易所的真實部位仍在，之後 `load_basket_entries(active_only=True)` 看不到它，止損守護也不會再監控，形成無人管理的裸倉。adapter 連線失敗但 `dry_run=False` 時，同樣會走到這條路徑。

**修法**

1. `dry_run=True`：只寫 `role="simulated_stop_loss"` 的模擬流水，不呼叫 `apply_basket_fill`，不改 `bot_baskets`。
2. `exchange_adapter is None`：直接回傳 `status="failed"`，保持籃子 active，並觸發告警。
3. 只有交易所回報成交（`filled > 0`）才結算；部分成交只結算 `filled`。
4. 移除「以 `dry_run=True` 驗證籃子清空」的測試契約，改成「dry-run 不得改帳本」與「adapter 缺失不得改帳本」兩個回歸測試。

### A-2 名單同步用 `close_missing=True` 關閉未成功平倉的成分

**證據**

- `service.py:912-919`：只要 `rank_result.status=="success"` 且有 `long_list`，就呼叫 `save_active_basket`，完全不看 `rebalance_report.status` 是 `completed`、`partial` 還是 `failed`。
- `service.py:282`：`save_active_basket` 呼叫 `ledger.save_basket(..., close_missing=True)`。
- `ledger_store.py:1049-1062`：`close_missing=True` 會把不在新名單內的既有成分直接設為 `closed`，不經 `apply_basket_fill`。
- `rebalancer.py:700-709`：任一平倉單失敗時，報告是 `partial` 或 `failed`，不是全部成功。
- `rebalancer.py:142-144`：下一輪 `calculate_diff` 對「不在 managed_symbols」的部位一律跳過平倉。

**影響**

舊多頭若因 API 錯誤、精度跳過、未上架市場等原因平倉失敗，交易所仍有真實部位；但 service 已把該成分標為 closed。下一輪它被視為「外部未納管部位」跳過，止損也不再涵蓋，形成永久孤兒倉。這同時違反 AGENTS.md 的 P4-1 規則：「籃子型策略的 status/quantity/realized_pnl 結算只由 `apply_basket_fill()` 擁有；名單同步不得覆寫已結算成分」。

**修法**

1. `save_active_basket` 改 `close_missing=False`，只新增/更新新名單；舊成分的關閉交給 `apply_basket_fill`。
2. 若一定要在名單同步時關閉，必須以「該 symbol 的平倉單已 filled」為前提，不能只看名單差異。
3. 在 `trigger_once` 只有在 `report.status in ("completed","completed_dry_run")` 時才做完整名單同步；`partial/failed` 保留舊成分與 active 狀態，讓重試可以補平。
4. 新增測試：平倉單失敗 → `bot_baskets` 該成分仍 active、quantity 不變；下一輪 `calculate_diff` 仍會產生 reduce-only 平倉單。

### A-3 `degraded` 只記錄，不阻止換倉

**證據**

- `regime_filter.py:104-141`：任一因子缺值時計 0 分並設 `degraded=True`。
- `service.py:814`：只把 `[DEGRADED 資料缺漏]` 印進 log。
- `service.py:870-886`：只要 `force_flatten` 為 false，照常 `fetch_features` 與 `compute_ranks`，沒有檢查 `decision.degraded`。

**影響**

廣度或資金費率歷史抓取失敗時，`pts` 可能仍為 1 或 2，策略會在資料不完整的情況下重新選幣與換倉。這與 Master Spec 守則六「若行情數據丟失，當期禁止調整倉位，保持現有對沖狀態」及 03 號規格 T6「API 連續錯誤／資料缺漏 → 停止下單並告警」直接衝突。

**修法**

1. `degraded=True` 時禁止新倉與加碼，只允許 reduce-only；或直接跳過本次換倉並記錄 `status="degraded_hold"`。
2. 把「缺哪個因子、覆蓋率多少」寫進 `bot_regime_decisions` 與 API，讓前端可告警。
3. 新增測試：`breadth_pct=None` 但 trend/funding 為 true（pts=2）時，不得產生開倉單。

### A-4 對沖標的被外部機器人保護時，空的長倉仍會建立

**證據**

- `rebalancer.py:169-173`：目標清單中若 `_is_protected(sym)`，直接 `continue`，跳過開倉與調倉。
- `service.py:130-202`：`get_external_bot_symbols` 會把活躍 `config/grid`、`config/dca` 的標的納入保護；只要有一支 BTC 網格/DCA，`BTCUSDT` 就會進保護名單。
- `rebalancer.py:696-709`：報告狀態只依 failed/skipped/filled 判定；空頭被跳過不會讓整輪失敗，狀態仍可為 completed。

**影響**

若帳戶同時有 BTC 網格或 DCA，`BTCUSDT` 被視為外部保護，benchmark 空頭被靜默跳過；Top 5 多頭仍照常建立，結果是 50% 多頭暴露、0% 對沖，策略變成方向性多頭。報告狀態仍可能是 `completed`，只有一行 warning。

**修法**

1. 在換倉前檢查 `short_hedge_symbol` 是否落在 `protected_symbols`；若是，整輪 abort（不開多），回傳 `status="hedge_blocked"`，由人工解除衝突。
2. 或讓「本策略的對沖腿」優先於外部保護，但需先確認同一帳戶不會與外部 BTC 機器人互相平倉；在無法保證前，abort 比裸多安全。
3. 新增測試：`protected_symbols={"BTCUSDT"}` 且 benchmark 模式 → 不得產生任何多頭開倉單，且報告狀態為失敗/阻擋。

### A-5 停止排程會同時停止止損守護，但不會平倉

**證據**

- `service.py:1633-1645`：`stop_scheduler` 取消 `_scheduler_task` 與 `_stop_loss_task`。
- `service.py:1345-1367`：止損迴圈只在 `_is_running` 為 true 時運行。
- `service.py:911-920`：停止排程不會呼叫 `force_flatten` 或 `save_active_basket`。

**影響**

使用者從 UI 按「停止排程」後，既有部位仍在交易所，但 -12% 守護同時消失。這是 Opt C 的核心保單被一個常規操作關掉。若之後市場急跌，沒有任何機制截斷左尾。

**修法**

1. 止損 daemon 與排程解耦：只要 `bot_baskets` 有 active 多頭且帳號已連線，就持續運行。
2. 或 `stop_scheduler` 在有 active 部位時拒絕停止，要求先呼叫「平倉並停止」；至少要在 UI 明確警告。
3. 新增測試：有 active 多頭時停止排程 → 止損 task 仍在運行，或 API 回傳 409 要求先平倉。

## 4. Consider（值得評估項）

### C-1 止損與換倉沒有共用互斥鎖

`service.py:704-708` 的 `_rebalance_lock` 只包住 `trigger_once`；`check_and_trigger_stop_losses` 在 `service.py:1422` 直接呼叫 `execute_stop_loss`，不取鎖。每週四 00:05 換倉與每 5 秒止損可能重疊：同一標的同時被換倉單與 reduceOnly 止損單處理，造成其中一方失敗、或帳本與交易所數量不一致。

建議：止損檢查取同一把鎖（或 per-symbol 鎖），換倉期間跳過止損並在換倉後重查；新增併發測試。

### C-2 ATR 與成交量過濾在資料失敗時 fail-open

`quant_engine.py:549-553` 只有在 `atr_pct_map` 非空且該幣有值時才過濾；`atr is None` 直接放行。`quant_engine.py:563-586` 的回補階段還會主動放寬 ATR，只印 warning。成交量在整批 ticker 失敗、`quote_volumes` 為空時也會跳過過濾。

這使 Opt D 在「行情抓取不穩」時自動失效，而那正是妖幣波動最大的時候。建議：ATR 缺值視為不合格（fail-closed），或把覆蓋率不足標成 `degraded` 並禁止換倉；回補階段不要放寬 ATR。

### C-3 RESTRICTED_TAGS 仍等於未實作

`quant_engine.py:102-104` 讀的是 `universe.restricted_tags`，但 `config/coinfull_prime/strategy_config.yaml:14-29` 沒有這個欄位。程式語意是「精確 symbol 黑名單」，不是 Master Spec 的 MEME/AI_AGENT/LOW_LIQ 標籤治理。CHANGELOG 0.3.12 說已消除 D-05，與事實不符。

建議：先建立可維護的 symbol 清單（含審核理由與日期），或接交易所 tag 資料源；若不做，移除「RESTRICTED_TAGS 已實作」的文件宣告，避免審查誤判。

### C-4 資金費率 Carry 沒有入帳，權益曲線也不存在

`service.py:1029-1099` 的 snapshot 只有 basket 名義、realized_pnl、UPnL，沒有 account equity、free margin、funding income。`rebalancer.py:645-674` 只記價格成交的 realized_pnl，沒有資金費率流水。

v3 報告把 Carry（58 天 +40.38U）列為三大收益來源之一；現行帳本無法驗證它。建議接 `/fapi/v1/income?incomeType=FUNDING_FEE`，以穩定鍵寫入 `trade_fills`（role=`funding`）或獨立事件，並在 snapshot 記錄 equity/free margin。這也能讓 03/04 的 T6 回撤規則有資料基礎。

### C-5 缺少持倉 reconciliation

CoinFull Prime 沒有 startup/每日的「交易所持倉 vs `bot_baskets`」對帳。現有 reliability 依賴 ledger 正確；一旦 A-1/A-2 造成幽靈或孤兒，系統不會發現。建議在 `initialize` 與每次換倉後比對 `get_positions` 與 active 成分，將差異寫入 `bot_events`，必要時隔離或平倉。

### C-6 配置檔缺失時會靜默退回 v2 預設

`service.py:349-363` 在檔案不存在或解析失敗時把 `self.config={}`；`CoinFullPrimeQuant` 的預設仍是 `DEFAULT_WEIGHTS`（momentum 0.35/flow 0.35/funding 0.20/liq 0.10）、`hedge_mode="basket"`、`momentum_mode="day"`、`funding_mode="spot"`（`quant_engine.py:46-51, 74, 86, 93`）。只要 scheduler 以 `dry_run=False` 啟動，就會用 v2 對稱小幣空頭交易，而不是 v3 Opt C。

建議：配置缺失或解析失敗時直接拒絕啟動（fail-closed），並把 v3 參數設為程式預設，避免任何裸跑路徑退回舊策略。

### C-7 1.15x 加碼與 1.0x 槓桿的保證金矛盾

`rebalancer.py:187-188` 以 `target_notional * scale` 放大目標；`rebalancer.py:603-609` 送單前把槓桿設為 1x。當 `scale=1.15` 且 `balance_pct=100`（或固定本金等於帳戶權益）時，總名義 115% 約需 115% 保證金，1x 槓桿下可能被拒或只成交部分。UI 預設 80% 可留出空間，但排程可設 100%。

建議：加碼前檢查 free margin / maintenance margin，將 scale 上限壓到可用保證金之內；或明確要求 `balance_pct <= 85` 才允許 1.15x。

## 5. Noted（列入觀察項）

1. `fetch_universe` 是先排除穩定幣/TradFi/外部機器人，再取 top_n（`quant_engine.py:205-215`），與 Master「先取成交額前 80 再排除」不完全等價。實務影響小，但會讓第 81～100 名的幣進入候選。
2. 止損在市價取得失敗時回退到 `quant_engine.latest_prices`（`service.py:1396-1401`）。這個價格是上一次換倉抓的，最長可達 7 天，不是報告所稱的「延遲 5-10 秒」。建議缺即時價就跳過並告警，不要用舊價觸發。
3. 止損的 `fill_id` 使用 `int(now_ts)` 且 `exchange_order_id=None`（`rebalancer.py:902-917`），與 P4-1 的「穩定業務鍵」精神有落差；部分成交也未使用 `res.filled`。
4. rebalance 的 `batch_id` 與 regime 的 `dedupe_key` 都含當次時間（`service.py:731`、`:956`），重試會產生新鍵，事件層不是真正冪等。現行 ledger audit 仍把 14 筆 CoinFull Prime `daily_rebalance` 列為 L6（皆為舊資料）。
5. 03/04 的 T6 組合回撤（-12% 降 0.5x、-18% 全平）與 P4 加碼條件 4-6（成本、維持保證金、60 日 IC）未實作。最新 v3 Master 沒有把它們列為必要項，但若 03/04 仍具規範效力，需補上或明確標為 non-normative。
6. `run_coinfull_prime.py` 的說明仍是「Top 8/Bottom 8」「每日 UTC 00:00」，與現行 Top 5 + 每週四 00:05 不一致；`quant_engine.py:228` 的註解仍寫 79 幣。屬文件漂移，不影響執行。

## 6. Dismissed（已駁回項）

| 項目 | 駁回理由 |
|---|---|
| 「Mom_blend 與 Spec 不符」 | 0.3.12 已把 Master 改為 0.5R7+0.3R14+0.2R30，實作與文件一致。 |
| 「funding settlements=3」 | 0.3.12 已改 9，並在 Master 註明 3 日平滑。 |
| 「top_n=79」 | 0.3.12 已改 80。 |
| 「log_only=true 導致 T1 失效」 | 0.3.12 已改 false；`service.py:820-825` 只在明確開啟時覆蓋。 |
| 「pts=2 加碼語意錯誤」 | 04 號規格已明確定義 pts=3 過熱不加碼、pts=2 且費率百分位 45-80 才加碼；實作與 04 一致，不列缺陷。保證金矛盾另見 C-7。 |
| 「Opt D 只有 40%」 | 2026-10-18 報告的敘述已過時；成交量與 ATR 已接線。真正未完成的是 tags 與 fail-open，見 C-2/C-3。 |

## 7. 驗證結果

| 檢查 | 結果 |
|---|---|
| `python3 -m unittest tests.test_coinfull_prime_quant tests.test_coinfull_prime_rebalance tests.test_coinfull_prime_regime` | 37 tests OK |
| `python3 -m unittest tests.test_accounting_path_policy` | 12 tests OK |
| `python3 tools/ledger_consistency_audit.py --markdown --fail-on high` | exit 1、UNHEALTHY、5 項發現 |
| ledger 的 CoinFull Prime 覆蓋 | bots/fills/basket 齊備 |
| L6 CoinFull Prime 缺 dedupe | 14 筆 `daily_rebalance`（12 筆 `coinfull_prime` 舊 bot_id + 2 筆 `coinfull_prime:paper`），皆為現行 code path 之前的歷史列 |
| L13 CoinFull Prime 重複 fill | 無 |
| `graphify query` | 環境無 `graphify` 執行檔；改用 `graphify-out/GRAPH_REPORT.md`（commit `fa90734`）確認模組拓撲 |
| `ocr scan` | 失敗：未配置 `OCR_LLM_URL/TOKEN/MODEL`，無法做第二次 LLM 行級掃描 |
| 執行中服務 | 本機無 8001 listener、無交易進程；但 `data/coinfull_prime_scheduler.json` 顯示 `binance_live`、`enabled=true`、`is_running=true`，且 live 籃子有 3 個 `quantity>0` 的 active 成分。若本機就是生產機，則止損守護目前未運行，這是立即的營運風險；若生產在別台，請在該台確認。 |

## 8. 建議修復順序

**P0（上線前，1-2 天）**

1. A-1：止損 dry-run/adapter 缺失不得改帳本；只結算交易所回報的 filled 數量。
2. A-2：`save_active_basket` 改 `close_missing=False`；partial/failed 不同步完整名單；補孤兒倉回歸測試。
3. A-3：`degraded` 時禁止新倉/加碼，或直接 hold。
4. A-4：對沖標的被保護時整輪 abort，不得裸多。
5. A-5：止損 daemon 與排程解耦，或停止排程前強制平倉/明確警告。

**P1（一週內）**

1. C-1：止損與換倉共用互斥鎖。
2. C-2：ATR/成交量 fail-closed，回補不放寬 ATR。
3. C-3：建立 `restricted_tags` 配置與審核紀錄，或修正文件。
4. C-4：接資金費率收入與 equity snapshot。
5. C-5：startup/換倉後 reconciliation。
6. C-6：配置缺失 fail-closed，v3 參數設為預設。
7. C-7：加碼前保證金檢查。

**P2（兩週內）**

1. 止損改用即時 mark price/WebSocket，移除舊價 fallback。
2. 止損 fill_id 改用交易所 order id，支援部分成交。
3. 事件 dedupe 改穩定業務鍵，清理 14 筆舊 L6 資料。
4. 更新 CLI/註解/文件，消除 Top 8、每日 00:00、79 幣等漂移。
5. 決定 03/04 的 T6 與 P4 加碼條件是否仍為規範；若是，補實作與測試。

## 9. 最終判定

**條件不通過（Not production-ready）。**

理由不是選幣模型，而是停止事件與籃子結算的資金安全語意：A-1 會讓 live 部位在帳本上消失，A-2 會讓失敗平倉變成永久孤兒，A-3 會在資料缺漏時照常換倉，A-4 會讓對沖腿消失，A-5 會讓止損守護被排程操作關掉。這五項都與 v3 報告「非對稱隔離、止損持久化、異常降級」的核心精神直接衝突。

若只跑 paper/testnet 且嚴格 dry-run，現行版本可用於策略觀察；若要進 `binance_live`，先完成 P0 五項並補上對應回歸測試，再跑一次 ledger audit 到 HEALTHY。
