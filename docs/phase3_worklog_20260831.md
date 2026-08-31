# Phase 3 工作日誌（事件溯源 + 三路對帳）

> 日期：2026-08-31｜分支：`arena/01a056af-grid-trading-console`
> 對應計畫：`docs/data_layer_resilience_plan_zh-TW.md` §6（P3-1～P3-4）
> 前置：Phase 2 Gate 已於同日通過（`docs/phase2_audit_report_20260831_zh-TW.md` 附錄 B）

---

## P3-1：`bot_events` append-only 事件流

**Schema / 儲存**
- 新表 `bot_events(id, bot_id, seq, event_type, dedupe_key, payload JSON, created_at)`：
  - Core Table（`ledger_store.py`）+ `schema.sql` + Alembic `0002_bot_events`（PG 部分唯一索引以 driver SQL 建立）。
  - `(bot_id, seq)` 唯一（跨進程衝突以 bounded retry 重取 max(seq)）；`(bot_id, dedupe_key)` 部分唯一索引提供冪等重送。
- `LedgerStore.append_event()` / `get_events()`；`LedgerRepository` facade 透傳。
- 事件型別（計畫 §6 全九種支援）：`bot_created / bot_renamed / order_placed / order_cancelled / fill / take_profit / stop_loss / reset / state_transition` + `reconcile`（P3-2 審計）。

**發射點（全部 try/except 包裹，事件擷取絕不阻斷交易）**
| 事件 | 發射點 |
|---|---|
| `fill` | grid：`PositionTrackerImpl._persist_fill_to_ledger`（`fill:{fill_id}` 冪等）；DCA：`RuntimeSnapshotRepository.save` fills 迴圈 |
| `bot_created` / `bot_renamed` | `register_or_update_bot` 橋接 identity 審計（同一事務寫入） |
| `take_profit` / `stop_loss` / `reset` | `GridResetManager._generic_reset_workflow` 末尾檢查點（reset_type 語意映射；checkpoint 記錄重置後 tracker 權威計數） |
| `state_transition` | `control_plane.publish_lifecycle`（grid/DCA 共用） |
| `order_placed` / `order_cancelled` | 型別與 fold 語意已支援（不影響持倉投影）；下單路徑接線列為後續（見 §已知事項） |
| `reconcile` | P3-2 對帳審計 |

**摺疊（fold）**：`core/infrastructure/database/event_fold.py`
- `build_timeline()`：事件流優先；legacy（無 fill 事件）自動以 `trade_fills` 合成 fill 事件並按時間歸併。
- `fold_timeline()`：fill 數學與 `rehydrate_position_from_fills` 完全一致；reset/take_profit/stop_loss 執行合法歸零 + checkpoint 覆寫。
- 屬性測試（P3 Gate）：隨機 fill 序列 ×50 → fold == rehydrate；隨機生命週期序列 ×50 → fold == 直寫模擬（`tests/test_event_fold.py`）。

## P3-2：grid 三路對帳

- `ExchangeReconciler.reconcile_grid()` 由「只呼叫 recover_old_orders」升級為真三路比對（本地 facts ↔ 交易所 positions ↔ open orders）：
  - 情境 A：本地虛擬持倉=0、交易所>0 → `sync_recovered_position()` 回補 + `reconcile` 審計。
  - 情境 A'：本地>0、交易所=0（幽靈持倉）→ 按交易所真值歸零 + 審計。
  - 情境 B：本地 PENDING 掛單交易所已無 → `mark_cancelled()` + 審計（在世單不受影響；格位重建交由既有健康檢查/補單機制）。
  - 交易所查詢失敗 → 跳過比對（fail-safe，不誤傷本地狀態）。
- 接線：`GridCoordinator.start()` 於恢復後執行一次（步驟 2.5，失敗不阻斷啟動）。
- `GridCoordinator` 新增 `bot_id` 屬性（resolve_bot_id，與 tracker/恢復路徑一致）。
- Grid 核心改動全為附加式（+44 行，零交易邏輯變更；既有 `test_audit_*_grid_zero_regression` 防護測試於 commit 後恢復綠）。

## P3-3：稽核與回放工具

- `scripts/tools/replay_bot.py --bot-id X [--db] [--format text|json]`：
  重建時間線 → 摺疊 → 與 `bot_positions` 逐欄 diff；退出碼 0/2/3（一致/差異/不存在）。
- 事件分佈統計（events_by_type）供稽核檢視。

## P3-4：備份/災難恢復演練

- `scripts/tools/rebuild_projections.py [--bot-id] [--execute]`：投影由 facts+事件 OCC 安全重建；dry-run 預設。
- SOP 文件：`docs/phase3_disaster_recovery_zh-TW.md`。
- 演練（沙箱實證）：
  - PG 16.2：種子生命週期（4 心跳 + 止盈檢查點）→ **刪除全部投影表** → rebuild → replay ✅ 一致。
  - SQLite：同流程 ✅。
  - Alembic `0002` 於 PG 實跑升級成功（0001 → 0002）。

---

## 驗收（Phase 3 Gate 對照）

| Gate 準則 | 證據 | 結果 |
|---|---|---|
| 事件重放與直寫投影一致（屬性測試） | `tests/test_event_fold.py`：100 條隨機序列 fold==rehydrate/直寫模擬 | ✅ |
| grid + DCA 三路對帳自癒演練 | `tests/test_grid_reconcile.py`：情境 A/A'/B + fail-safe；DCA 既有 `reconcile_dca` 回歸全綠 | ✅ |
| 災難恢復 SOP 演練 | PG + SQLite 刪除投影 → 重建 → 回放一致（§P3-4） | ✅ |

**回歸**：SQLite 410 tests（commit 後含 6 個 grid 零改動防護恢復綠）；新測試 19 個（事件流 7、fold 屬性 3、對帳 4、工具 5）。

## 已知事項 / 後續

1. `order_placed`/`order_cancelled` 的下單路徑發射點（DCA executor / grid order_operations）待接線——fold 與 API 已支援，屬增益觀測。
2. heartbeat stats 寫入的投影與 facts 摺疊可能存在微差（引擎回報值 vs 逐筆成交）——`rebuild_projections.py` 對齊後收斂；長期以事件為 canonical（計畫方向）。
3. `GridCommandRepository` 仍為 SQLite（Phase 2 已知事項，Phase 3+ 一併遷移）。
