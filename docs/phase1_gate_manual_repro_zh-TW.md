# Phase 1 Gate — 手工重現驗證指南（歸 0 復現與確認修復）

> 目標：驗證「小改動 / 重開機器人 → 已開啟的 grid 與 DCA 交易紀錄歸 0」已根除。
> 本指南對應計畫 `docs/data_layer_resilience_plan_zh-TW.md` §4「Phase 1 驗收總則」。

## 0. 自動化驗證（已完成，可直接執行）

資料層已用自動化測試覆蓋同一情境，可重複執行：

```bash
./scripts/ci.sh                                   # 完整回歸（含前端）
# 或只跑資料層 + Gate 情境：
python3 -m unittest \
  tests.test_bot_identity_stability \
  tests.test_backfill_bot_ids \
  tests.test_identity_audit \
  tests.test_monotonic_guard \
  tests.test_grid_fill_ledger \
  tests.test_async_writer_full_save \
  tests.test_phase1_gate_repro
```

`tests/test_phase1_gate_repro.py` 的三個案例即是本指南第 2 節的資料層縮影：

1. grid 改價格區間（檔名變）→ 沿用既有 `bot_id` → 重啟後 `cycles` / `realized` **不歸 0**。
2. DCA 重建（timestamp 檔名變）→ 沿用既有 `bot_id` → 重啟後 `cycles` / `realized` / `fills` **不歸 0**。
3. 恢復失敗的新引擎以 0 值回報 → **單調防護拒寫**，累積計數不被覆寫。

## 1. 前置

- Python 3.11+ 虛擬環境（`source venv/bin/activate`）、`.env` 已設定。
- 引擎與 Web UI 依 `README.md` 啟動（引擎 `python run_multiple_grids.py`；後端 `web_ui/backend/main.py`）。
- 回填已執行：`python scripts/migration/backfill_bot_ids.py`（既有 YAML 已補 `metadata.bot_id`）。

## 2. 手動重現步驟（本機，需 PAPER 行情連線）

### 2.1 備份

```bash
cp data/web_ui_runtime.sqlite3 /tmp/runtime_before.sqlite3
```

### 2.2 建立並啟動 1 台 grid（PAPER）＋ 1 台 DCA（PAPER）

用 Web UI 建立（`account_mode: paper`，選窄價格區間/低 `grid_interval` 易成交）：

- grid：`SOL/USDC:USDC`、neutral、`lower=100`、`upper=160`、`grid_interval=0.5`、`order_amount` 符合最小名目。
- DCA：`ETH/USDC:USDC`、long、`base_order_amount` 符合最小名目、`take_profit_percent=1.0`。

記下兩台的 `bot_id`（建立成功回應 / 詳情頁皆顯示）：

```bash
# grid bot_id 範例
grep -A2 '^metadata:' config/grid/*.yaml | head
# DCA bot_id 範例
grep -A2 '^metadata:' config/dca/*.yaml | head
```

### 2.3 產生成交並記錄基線

啟動引擎，等 1–3 分鐘讓 PAPER 產生數筆成交。在 UI 記下（或查 SQL）：

```bash
sqlite3 data/web_ui_runtime.sqlite3 \
  "SELECT b.bot_id, count(f.fill_id) AS fills
   FROM bots b LEFT JOIN trade_fills f ON f.bot_id=b.bot_id
   WHERE b.bot_id IN ('<grid_bot_id>','<dca_bot_id>')
   GROUP BY b.bot_id;"
```

記下此時兩台的 `cycles` / `realized`（基線）。

### 2.4 停止引擎（保留資料）

Ctrl-C 或 `./scripts/stop_all_grids.sh`。引擎關閉時 `AsyncDatabaseWriter.stop()` 會最終 flush，最後一批快照落盤。

### 2.5 「小改動」

- **grid**：改價格區間。兩種合法方式任選：
  - **A（推薦，保留身份）**：Web UI「設定檔編輯器」（`PUT /api/config/{path}`）改 `lower_price`/`upper_price` 數值，**保留檔案內 `metadata.bot_id`**。若要連檔名一起改，改完後把檔名同步改成含新區間，並**保留 `metadata.bot_id` 不變**。
  - **B（API，保留身份）**：`POST /api/v1/grids` 帶 `"bot_id": "<原 grid bot_id>"` 與新區間，後端會沿用既有身份（`existing_bot_id`）。
- **DCA**：重建（`POST /api/v1/dca` 帶 `"bot_id": "<原 dca bot_id>"`），或前端重建流程沿用既有 `bot_id`；新檔名會含新 timestamp，但 `metadata.bot_id` 不變。

> ⚠️ 語意提醒：若「刪除後從引導表單重新建立」且**未帶舊 `bot_id`**，會被視為「全新機器人」並配發新身份——這是正確的新 bot 語意，不是歸 0。要「編輯並保留歷史」請走上述 A/B 路徑。

### 2.6 重啟並驗證「不歸 0」

重新啟動引擎，等 1–2 個 heartbeat（UI 狀態回到 `running`）：

```bash
sqlite3 data/web_ui_runtime.sqlite3 \
  "SELECT bot_id, json_extract(payload, '$.filled_orders.cycles') AS cycles,
          json_extract(payload, '$.profit.realized') AS realized
   FROM bot_snapshots
   WHERE bot_id IN ('<grid_bot_id>','<dca_bot_id>');"
```

或直接在 UI 查看兩台的累計成交 / 盈虧。

**預期結果**：`cycles` / `realized` / 成交筆數與 2.3 基線**一致或遞增**（成交繼續累加），**不得歸 0**。

### 2.7 反向驗證（單調防護）

手動以「倒退值」寫入，確認被拒：

```bash
# 以 0 值 payload 嘗試覆寫（應被 MonotonicGuard 拒絕並記 warn log）
python3 - <<'PY'
from web_ui.backend.runtime_repository import RuntimeSnapshotRepository
repo = RuntimeSnapshotRepository.instance()
repo.save("config/grid/<grid_bot_id>.yaml",
          {"bot_id": "<grid_bot_id>",
           "filled_orders": {"buy": 0, "sell": 0, "cycles": 0},
           "profit": {"realized": 0.0, "fees": 0.0}},
          updated_at=None)
payload, _ = repo.load_by_bot_id("<grid_bot_id>")
print("cycles after rejected write:", payload["filled_orders"]["cycles"])  # 應為基線值，非 0
PY
```

## 3. 身分不變的直接證據

```bash
# 小改動前後，bot_id 必須一致（帳本 key 不漂移）
grep -A1 '^metadata:' config/grid/<原檔>.yaml
grep -A1 '^metadata:' config/grid/<新檔>.yaml
# 兩者 bot_id 相同；若檔名內含區間，檔名可不同但 bot_id 必須相同
```

## 4. 失敗時的除錯

| 現象 | 可能原因 | 查法 |
|---|---|---|
| 重啟後仍歸 0 | 走了「刪除＋重建」且未帶舊 `bot_id` | 用 2.5 的 A/B 路徑重做 |
| 重啟後恢復到舊值但不再增加 | 引擎未熱插拔新檔 | 確認 `config/active_grids.json` 含新 path；看引擎 log |
| warn log「拒絕覆寫快照」 | 新引擎恢復未完成即回報 0 | 屬**預期防護**；確認恢復完成後數值回升 |
| trade_fills 有 `unknown` bot | 舊資料（Phase 1 前寫入） | 現行寫入已拒絕未註冊 bot；舊列可人工 remap |

## 5. 判定準則

以下**全部**成立才視為 Phase 1 Gate 通過：

- [x] `./scripts/ci.sh` 全綠（資料層 379/379）。
- [x] 自動化重現 `tests/test_phase1_gate_repro.py` 3/3。
- [ ] 本機手動重現（2.2–2.6）：grid 改區間 + DCA 重建後重啟，計數與盈虧不歸 0。
- [ ] 反向驗證（2.7）：0 值倒退寫入被拒。
