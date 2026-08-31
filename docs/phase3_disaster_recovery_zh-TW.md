# Phase 3 災難恢復 SOP（facts + 事件完整重建）

> 對應計畫：`docs/data_layer_resilience_plan_zh-TW.md` §6 P3-4
> 驗收：刪除全部投影表後，可由 facts（`trade_fills` + `bot_events`）完整重建。
> 前置閱讀：`docs/phase2_production_ops_zh-TW.md`（備份/PITR）

---

## 1. 資料分類（真相源 vs 投影）

| 分類 | 資料表 | 遺失時 |
|---|---|---|
| **事實（不可丟）** | `bots`、`trade_fills`、`bot_events`、`bot_identity_events`、`bot_positions`（OCC 現值） | 從備份/交易所回補；`bot_positions` 可由 facts 重建 |
| **投影（可重建）** | `bot_positions`（可由 facts 重算）、`bot_snapshots`、`bot_snapshot_history`、`grid_runtime_snapshots`、`grid_runtime_history` | 由 `rebuild_projections.py` + 引擎心跳自動再生 |

**恢復優先序（ADR-4）**：LIVE 時 `交易所 > 本地 facts > 投影`；PAPER 時 `本地 facts > 投影`。

## 2. 標準恢復流程（投影損毀/誤刪）

```bash
# 0) 停止寫入（避免重建期間心跳覆寫）
./scripts/stop_all_grids.sh   # 或 systemctl stop crypto-webui

# 1) 評估損壞範圍（可先以回放工具核對每個 bot）
python scripts/tools/replay_bot.py --bot-id <bot_id>          # 退出碼 2=投影與 facts 不一致

# 2) 重建投影（先 dry-run 檢視差異，再 --execute 寫入）
python scripts/tools/rebuild_projections.py                   # 全部 bot、dry-run
python scripts/tools/rebuild_projections.py --execute         # 全部 bot、實際建立
python scripts/tools/rebuild_projections.py --bot-id X --execute

# 3) 驗證（每個 bot 的回放應為「一致」）
python scripts/tools/replay_bot.py --bot-id <bot_id>

# 4) 重啟；bot_snapshots / grid_runtime_* 由引擎心跳自動再生
./scripts/start_all_grids.sh
```

- `bot_positions` 由 facts（事件流 + trade_fills）摺疊重建（OCC 安全寫入）。
- `bot_snapshots` / `grid_runtime_*` 為顯示投影，**不需要**重建——引擎第一個 heartbeat 即重寫。

## 3. PostgreSQL 完整災難（資料目錄遺失）

1. 以 Phase 2 的 base backup + WAL 歸檔 PITR 還原至最近時間點（`docs/phase2_production_ops_zh-TW.md` §5）。
2. 執行 §2 的投影重建（facts 若有缺漏，以交易所為真相源：啟動後 Phase 3 三路對帳會自動回補持倉並記 `reconcile` 審計事件）。
3. 對帳驗證：引擎啟動日誌應出現「🔍 三路對帳完成」，審計事件可由 `replay_bot.py` 的事件分佈檢視。

## 4. 演練證據（2026-08-31，沙箱實證）

**PostgreSQL 16.2**（`postgresql://grid:grid@127.0.0.1:5432/grid`）：

```text
1) 種子：4 次 heartbeat（含 1 次止盈重置 bypass + take_profit 檢查點事件）
   → pre-DR replay：events={bot_created:1, fill:4, take_profit:1}，重建投影 qty=0.5、fees=0.01
2) DELETE bot_positions / bot_snapshots / bot_snapshot_history / grid_runtime_snapshots / grid_runtime_history
   → 倖存：bots=1、trade_fills=4、bot_events=6
3) rebuild_projections.py --bot-id dr3-bot --execute → status=rebuilt
4) replay_bot.py → ✅ 一致（重建投影 == 現存投影）
```

**SQLite**（/tmp/dr3_sqlite.sqlite3）：同流程 `rebuilt`、投影恢復成功。

## 5. 退出碼與健康判讀（replay_bot.py）

| 退出碼 | 意義 | 處置 |
|---|---|---|
| 0 | 回放與投影一致 | 無需動作 |
| 2 | 投影與 facts 有差異 | 執行 rebuild_projections --execute |
| 3 | bot 不存在 | 確認 bot_id |

> **注意**：直接以 heartbeat stats 寫入的投影本就可能與 facts 摺疊結果有微差
> （stats 為引擎回報值、facts 為逐筆成交）——以 `rebuild_projections.py` 對齊後，
> 兩者即收斂；長期方向（計畫 §6 P3-1）以事件流為 canonical。
