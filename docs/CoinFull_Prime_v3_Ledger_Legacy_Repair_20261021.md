# CoinFull Prime legacy ledger 修復紀錄（2026-10-21）

- 基線：0.3.13（`fa90734`）
- 修復版本：0.3.14
- 範圍：只修 CoinFull Prime 的 legacy L5/L6 與 L2 orphan event，不動 grid/DCA
- 工具：`tools/coinfull_legacy_repair.py`（預設 dry-run，`--apply` 才寫入）
- SQLite 備份：`data/backups/web_ui_runtime_pre_coinfull_legacy_repair_20261021T125900Z.sqlite3`

## 修復前

| 發現 | 數量 | 內容 |
|---|---:|---|
| L6 | 14 | 舊 `daily_rebalance` 事件缺 `dedupe_key`（12 筆 `coinfull_prime` + 2 筆 `coinfull_prime:paper`） |
| L5 | 1 個 bot / 26 fills | `coinfull_prime:paper` 有 `trade_fills`，但沒有 canonical `fill` 事件 |
| L2 | 140 個 bot_id | 其中 `coinfull_prime` 佔 12 筆 orphan events |

## 修復動作

1. **L6**：`StrategyLedger.backfill_legacy_event_dedupe_keys("coinfull_prime")`
   - 只更新 `dedupe_key IS NULL` 的可重跑事件。
   - key 格式：`legacy:{event_type}:{bot_id}:{event_id}`，唯一且重跑冪等。
2. **L5**：`StrategyLedger.backfill_missing_fill_events("coinfull_prime:paper")`
   - 由 `trade_fills` 建立缺少的 `fill` 事件。
   - 以 payload 的 `fill_id` 去重，重跑為 no-op。
3. **L2**：`StrategyLedger.ensure_bot("coinfull_prime", ..., status="archived")`
   - 只註冊 legacy aggregate bot 身分，不改寫事件。

## 修復後

| 發現 | 修復前 | 修復後 |
|---|---:|---:|
| CoinFull Prime L6 | 14 | 0 |
| CoinFull Prime L5 | 26 fills 無事件 | 0（已補 26 筆 fill 事件） |
| 全域 L2 | 140 bot_id | 139 bot_id（後續 CI/測試寫入會再變動，非固定值） |
| CoinFull Prime L2 | 12 筆 orphan events | 0 |

全域 ledger audit 仍是 UNHEALTHY：
- L2 139：其他 legacy/test bot IDs，未在本工具範圍。
- L13 660：非 CoinFull Prime 的重複 `client_order_id`。
- L8 38：其他有成交但缺部位/籃子/快照承載的機器人。

## 冪等驗證

套用後再次執行 `python3 tools/coinfull_legacy_repair.py`：

```
L6 dedupe gaps (coinfull_prime*): 0
L5 fill event gaps: {}
L2 legacy bot registered: True
```

## 後續建議

1. 若要繼續清理全域 L2，先分類「測試 bot ID」與「真實 legacy bot」，不要批次註冊測試 ID。
2. L13 需先確認是重複入帳還是同一 `client_order_id` 的正常多筆成交；不要直接刪 fill。
3. L8 需依 `STRATEGY_ACCOUNTING_CONTRACT` 補承載表，不能用刪事件的方式讓 audit 變綠。
