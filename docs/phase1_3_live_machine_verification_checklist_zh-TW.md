# Phase 1–3 實機運行驗證稽核清單（沙箱 → 實機交接）

> 日期：2026-08-31｜分支：`arena/01a056af-grid-trading-console`
> 對象：**實機運行 AI / 操作者**——在真實機器上（真實行情、真實重啟、真實交易所連線）逐項驗證
> 範圍：Phase 1（語意止血）、Phase 2（PostgreSQL + Redis）、Phase 3（事件溯源 + 三路對帳）
> 稽核標準：`docs/grid_trading_console_audit_standard_zh-TW.md`（P0/P1/P2 分級、放行規則）
> 前置文件：`docs/data_layer_resilience_plan_zh-TW.md`、`docs/phase2_production_ops_zh-TW.md`、`docs/phase3_disaster_recovery_zh-TW.md`、`docs/phase1_gate_manual_repro_zh-TW.md`

---

## 0. 沙盒已驗證 vs 實機必須重驗（風險地圖）

| 項目 | 沙盒狀態 | 實機風險 | 清單段落 |
|---|---|---|---|
| 單元/整合測試（SQLite 410、PG 410） | ✅ 全綠 | 低（重跑確認環境一致即可） | §1.1 |
| PG/Redis 元件行為（降級、選舉、事件） | ✅ 實機 PG16/Redis6.2 演練 | 中（實機版本/防火牆/權限不同） | §2 |
| 歸 0 重現（改區間/重建 → 重啟不歸 0） | ⚠️ 僅自動化測試覆蓋資料層 | **高——需 PAPER 真實行情全流程** | §1.3 |
| 真實交易所成交 → fill/事件入帳 | ❌ 沙箱無真實交易所 | **高** | §1.4、§3.1 |
| 引擎/長時間運行穩定性（心跳、鎖、記憶體） | ❌ 僅短時熱待命 | **高** | §1.5 |
| 重啟/停電/斷網恢復 | ❌ 沙箱未演練真實重啟 | **高** | §1.5、§3.4 |
| PM2/systemd 正式部署（`start_web_ui.sh` 本身非正式） | ❌ | 高 | §4 |
| LIVE 下單 | ❌ **一律未測** | **最高——必須 PAPER 全過後才准開** | §5 |

**總則**：以下清單按「先 PAPER、後 LIVE」兩階段放行。任一 P0/P1 失敗即停止並回報，不得跳過。

---

## 1. 實機 PAPER 驗證 — Phase 1 核心（歸 0 根除）

### 1.1 環境一致性檢查（前置）

- [ ] `git log --oneline -5` 確認在含 Phase 3 的最新 commit（`198dc5d` 之後）。
- [ ] `source venv/bin/activate && python -m unittest $(sed -n '/^TEST_MODULES=(/,/^)/p' scripts/ci.sh | grep "tests\." | tr -d ' ' | tr '\n' ' ')` → **410 tests OK**（實機 Python 版本/依賴與沙箱可能不同，必須重跑）。
- [ ] `python -c "import sqlalchemy, alembic, redis, psycopg; print('deps ok')"` → 無 ImportError（若缺 `rich`/`psycopg` 等請 `pip install -r requirements.txt`，F9 之後 requirements 已含 rich）。
- [ ] `./scripts/ci.sh`（含前端 npm test/lint/build）全綠。
- [ ] 確認 `.env` 中 `GRID_LIVE_TRADING_ENABLED` **未設定或為 false**（PAPER 階段的硬前提）。

### 1.2 身份穩定性（P1-1）

- [ ] 盤點既有 YAML：`python scripts/migration/backfill_bot_ids.py --dry-run` → 列出缺 `metadata.bot_id` 的檔案；實跑（無 dry-run）後再 dry-run 應為 0 缺漏（冪等）。
- [ ] 隨機抽 3 個既有 YAML，核對 `metadata.bot_id` 值 == 檔名 stem（legacy 對接語意），並確認檔案註解/權限未被破壞。
- [ ] DB `bots` 表中**不得**存在 `strategy_type='unknown'` 列：`sqlite3 data/web_ui_runtime.sqlite3 "SELECT count(*) FROM bots WHERE strategy_type='unknown';"` → 0（PG 模式用 store API 或 psql 等價查詢）。

### 1.3 歸 0 重現（P1-1/P1-2/P1-4）——**本清單最高優先**

依 `docs/phase1_gate_manual_repro_zh-TW.md` §2 完整執行（PAPER 行情）：

- [ ] 1.1 備份：`cp data/web_ui_runtime.sqlite3 /tmp/runtime_before.sqlite3`（PG 模式先 `pg_dump`，見 §2.3）。
- [ ] 1.2 建立 1 台 grid（窄區間易成交）+ 1 台 DCA（PAPER），記下兩台 `bot_id`（建立回應/UI 詳情）。
- [ ] 1.3 讓引擎運行至產生 **≥3 筆成交**，記錄基線：`cycles / realized / fees / fills 筆數`（UI 或 SQL）。
- [ ] 1.4 停止引擎（Ctrl-C / stop_all_grids），確認 log 有最終 flush（AsyncWriter stop）。
- [ ] 1.5 小改動：grid 改價格區間（保留 `metadata.bot_id`）；DCA 帶原 `bot_id` 重建。
- [ ] 1.6 重啟引擎，等 2 個 heartbeat：
  - [ ] `bot_id` 前後一致（`grep -A1 '^metadata:' config/...`）。
  - [ ] `cycles / realized / fills` **等於或遞增於基線，不得歸 0**。
  - [ ] 刪除 `grid_runtime_snapshots` legacy 表內容後重啟 → 恢復結果不變（證明讀的是 facts，PG 模式跳過此項改由 §3.2 replay 證明）。
- [ ] 1.7 反向驗證（單調防護）：以 0 值 payload 寫入（指南 §2.7 腳本）→ 被拒且 log 出現「⚠️ [MonotonicGuard] 拒絕覆寫快照」。
- [ ] 1.8 合法重置不被擋死：PAPER 網格觸發一次止盈重置 → 計數合法歸零、log 出現 `take_profit` 事件（`SELECT * FROM bot_events WHERE event_type='take_profit'`）。

**判定**：1.3 全部成立 → Phase 1 實機 Gate 通過。任何一項歸 0 = **P0 回報**，附 `git rev-parse HEAD`、`.env`（去密）、相關 SQL 與 log。

### 1.4 真實成交入帳（P1-3 + P3-1）

- [ ] PAPER 成交後：`SELECT role, count(*) FROM trade_fills GROUP BY role;` → 有 `grid`（與 DCA 的 base/safety/tp）列。
- [ ] 同一成交重複 heartbeat 不產生重複列（`trade_fills` 總數 == UI 成交筆數）。
- [ ] `SELECT event_type, count(*) FROM bot_events GROUP BY event_type;` → 出現 `fill`（筆數 == trade_fills）、`bot_created`、`state_transition`；重置後出現 `take_profit`/`stop_loss`/`reset`。

### 1.5 長時間運行與真實重啟（P1-5 + 實機特有）

- [ ] 引擎連續運行 **≥2 小時**（PAPER、多 bot）：無 `database is locked`、無未處理 traceback、記憶體無持續上升（`ps -o rss` 抽樣）。
- [ ] 運行中 `kill -TERM` 引擎進程 → 最後一批快照落盤（重啟後 `bot_snapshots.updated_at` == 停止前最後心跳時間）。
- [ ] **模擬斷電**：`kill -9` 引擎 → 重啟後累計計數不歸 0（單調防護 + facts 恢復）；API 同法重啟 → dashboard 正常、`/healthz` OK。
- [ ] **斷網 5 分鐘**再恢復：引擎行情恢復、心跳恢復；UI stale 標記正確消失（`stale` 閾值 180s）。
- [ ] AsyncWriter 生效：`GRID_DB_ASYNC_WRITE=1` 下，心跳高頻期 SQLite 寫入無鎖錯誤（log 無 `OperationalError`）。

---

## 2. 實機 PAPER 驗證 — Phase 2（PostgreSQL + Redis）

> **既有系統導入請先讀**：若本機是舊版（v0.9.x，JSON 快照 schema）在線上運行，
> 先依 `docs/live_upgrade_playbook_zh-TW.md` 執行近零停機導入（備份→回填→DatabaseMigrator→驗證閘→回滾），
> 再回到本清單做 PAPER/LIVE 驗證。**既有 SQLite 庫禁止 `alembic upgrade head`**（0001 會與既有 legacy 表衝突）；
> 本節 §2.1 的 Alembic 僅適用於全新 PostgreSQL 庫。

### 2.1 部署（依 `docs/phase2_production_ops_zh-TW.md` §3）

- [ ] `CREATE ROLE grid LOGIN PASSWORD '...'; CREATE DATABASE grid OWNER grid;`（密碼用強密碼，勿照抄文件範例）。
- [ ] `DATABASE_URL=postgresql://...:5432/grid alembic upgrade head` → 成功（會自動正規化為 `+psycopg` 驅動）；`alembic current` → `0002_bot_events (head)`。
- [ ] 確認 PG 伺服器啟用 WAL 歸檔或雲端 PITR（ops §5），並**實際執行一次** `pg_dump grid > backup_$(date +%F).sql` 成功。

### 2.2 SQLite → PG 遷移（若有既有資料）

- [ ] 遷移前備份 SQLite（§1.3 的備份存在且可讀）。
- [ ] `python scripts/migration/sqlite_to_postgres.py --sqlite data/web_ui_runtime.sqlite3 --dry-run` → 記錄 counts。
- [ ] 實跑後以 psql 核對：`bots / trade_fills / bot_positions / bot_events` 數量與 dry-run 一致；**重跑一次** → 數量不變（冪等）。
- [ ] 遷移後抽 1 個 bot：`SELECT quantity, completed_cycles, version FROM bot_positions WHERE bot_id='<x>';` → version 與來源一致（F8 修復）。
- [ ] 切換 `.env` 的 `DATABASE_URL` → 重啟 API/引擎 → dashboard 資料與遷移前一致、`/healthz` OK。

### 2.3 Redis 三件套（P2-3/4/5）

- [ ] `REDIS_URL=redis://...:6379/0` 設定後啟動；`redis-cli keys 'grid:*'` 出現 `grid:state:*`（引擎心跳後）。
- [ ] TTL 生效（F4）：`redis-cli ttl grid:state:<bot_id>` → ≒ `GRID_REDIS_STATE_TTL`（±5s）；引擎啟動第二個同 bot 實例被拒時 `ttl grid:leader:<bot_id>` ≒ `GRID_REDIS_LEADER_TTL`。
- [ ] **防雙開實演**：同一 YAML 起第二個引擎進程 → 拒絕啟動，log「領導權已由另一引擎持有」，lifecycle 記 `failed: leader election conflict`。
- [ ] **停機演練**：`systemctl stop redis`（或 kill）→ 服務不中斷：dashboard 仍可讀（DB fallback）、引擎心跳正常寫入、log 出現降級 WARNING；leader fail-open 出現 **ERROR** log（F7）。
- [ ] Redis 恢復後：**不需重啟 API**，`/ws/grid-events` 事件恢復推送（F5 自癒實證）；讀路徑自動回 Redis。
- [ ] API 重啟後 dashboard 立即有資料（Redis 熱快取命中，不必等引擎心跳）。

### 2.4 事件推送與雙環境（P2-4/P2-6）

- [ ] 前端 TopBar 顯示「事件: 即時」；引擎產生成交 → UI 卡片 **15 秒輪詢間隔內**更新（事件驅動刷新）。
- [ ] 瀏覽器 DevTools → WS → `/ws/grid-events` 連線存在；故意改壞 token → 4401 拒絕。
- [ ] 雙環境啟動：`DATABASE_URL`/`REDIS_URL` 清空重啟 → 一切正常（SQLite 模式）；再設回 → PG+Redis 模式正常。各留 log 證據。
- [ ] **回滾演練**：PG 模式運行中，清空兩個 env → 重啟 → 回 SQLite 模式，資料與 PG 模式心跳前基線一致（PG 原檔未被破壞）。

---

## 3. 實機 PAPER 驗證 — Phase 3（事件溯源 + 三路對帳）

### 3.1 真實成交的事件捕獲

- [ ] §1.4 的 `bot_events` 檢查通過後：`python scripts/tools/replay_bot.py --bot-id <paper_grid_bot>` → 狀態「✅ 一致」（或差異僅為 §SOP §5 所述 heartbeat stats 微差 → 執行 `rebuild_projections.py --execute` 後變一致）。

### 3.2 三路對帳（P3-2，PAPER）

- [ ] **情境 A 實演**：PAPER grid 運行中，手動在 DB 將本地投影歸零（`UPDATE bot_positions SET quantity=0 WHERE bot_id='<x>';`）→ 重啟引擎（觸發 start() 對帳）→ log「🔍 三路對帳完成」且 `rehydrated=True`、本地持倉回補為交易所值；`bot_events` 出現 `reconcile: position_rehydrated`。
- [ ] **情境 B 實演**：人為刪除交易所一張掛單（PAPER 交易所介面手動撤單）→ 下一次對帳/健康檢查 → 本地該單標記 cancelled、log/事件有 `stale_local_order_cancelled`。
- [ ] 對帳失敗不傷本體：暫時封鎖交易所 API（斷網）→ 啟動引擎 → 對帳跳過（fail-safe），引擎仍正常啟動運行。

### 3.3 回放一致性（日常稽核工具）

- [ ] 對 3 個運行中 bot 各跑 `replay_bot.py` → 一致或已知微差；將輸出存 `logs/replay_$(date +%F).txt`。

### 3.4 災難恢復演練（P3-4，依 `docs/phase3_disaster_recovery_zh-TW.md`）

- [ ] PG 備份還原演練：以 §2.1 的 `pg_dump` 還原到臨時 DB → `replay_bot.py` 抽查一致。
- [ ] 投影刪除重建：停止寫入 → `DELETE FROM bot_positions;` → `rebuild_projections.py --execute` → `replay_bot.py` 一致 → 重啟後 UI 正常。
- [ ] （可選進階）刪除 `bot_snapshots`/`grid_runtime_*` 全部內容 → 引擎第一個心跳後 UI 自動恢復（投影可再生驗證）。

---

## 4. 系統層（部署/可觀測性——稽核標準 §3.F）

- [ ] 以 systemd（`scripts/deployment/crypto-webui.service`）或 PM2 部署 API 與引擎：restart=on-failure、退避、log rotation 生效；`start_web_ui.sh` 僅作開發用途（其自身警告）。
- [ ] 部署後 `systemctl status`/`pm2 status` 健康顯示 running；`curl /healthz` → `{"status":"ok"}`。
- [ ] structured log 落盤且輪替（logrotate 或 PM2 rotate）；log 中**不得**出現 API token/私鑰（抽查 `logs/*.log | grep -i token`）。
- [ ] 時鐘/時區：機器 NTP 同步（`timedatectl`），`stale` 判定與 heartbeat 時間無跨時區錯亂。
- [ ] 磁碟：`data/` 與 PG 所在分割區 ≥20% 剩餘；SQLite WAL 檔未無限成長。

---

## 5. LIVE 前置（實盤放行最低條件——稽核標準 §2）

> **P1–P4 全部完成且無未解 P0/P1 前，禁止設定 `GRID_LIVE_TRADING_ENABLED=true`。**

- [ ] §1–§4 全部勾完，PAPER E2E 通過（建至少運行 **3 天** 無歸 0、無鎖錯、無未處理例外）。
- [ ] `start_rejection` 負向測試：`GRID_LIVE_TRADING_ENABLED` 未設時，`account_mode: live` 的 YAML 嘗試啟動 → 被拒（log 明確「LIVE 未啟用」語意）。
- [ ] LIVE 交易所 API key 權限最小化（僅現貨/合約交易、**無提幣**）、IP 白名單、secret 僅存 `.env`（權限 600，不入 git——`git check-ignore .env` 確認）。
- [ ] LIVE 首次啟動程序（建議順序）：
  1. `GRID_LIVE_TRADING_ENABLED=true` + **單一**最小名義 LIVE grid（PAPER 引擎全部停用）。
  2. 開盤前記錄交易所端持倉/掛單快照；啟動後 5 分鐘內人工核對三路對帳 log 無誤差。
  3. 觀察 ≥24 小時：每筆成交 `trade_fills` 逐筆對帳（筆數==交易所 history）；`replay_bot.py` 一致。
  4. 演練一次 LIVE 重啟恢復（§1.5 同法）→ 計數/持倉與交易所一致。
- [ ] LIVE 期間每日例行：`replay_bot.py` 抽查、`bots_running/stale` 指標、Redis/PG 備份排程確運。

**LIVE 異常回報包**：`git rev-parse HEAD`、`.env`（去密）、相關 `bot_id`、`replay_bot.py` 輸出、log 片段、交易所端截圖/資料。**任何帳本與交易所不一致 → 立即停止該 bot（撤單不 平倉，人工確認後處置），回報後等待修復。**

---

## 6. 判定準則

| 階段 | 放行條件 |
|---|---|
| Phase 1 實機 Gate | §1.3 全部成立 + §1.4/§1.5 通過 |
| Phase 2 實機 Gate | §2 全部成立（含停機演練與回滾演練） |
| Phase 3 實機 Gate | §3 全部成立（含 DR 演練） |
| LIVE 放行 | §5 全部成立 + 無未解 P0/P1 + 例外接受有紀錄（owner/到期日/緩解） |

**證據留存**：每項勾選附指令輸出或 log 片段，彙整為 `docs/live_machine_verification_results_<date>.md`（照 `docs/grid_trading_console_audit_standard_zh-TW.md` §5 五段式），PR 連同結果一併回傳。

---

## 附錄：實機已知注意事項（承 Phase 2/3 工作日誌）

1. **heartbeat stats 與 facts 摺疊的微差**：引擎回報值與逐筆成交摺疊可能有小差異——以 `rebuild_projections.py` 對齊即收斂；`replay_bot.py` 退出碼 2 時先讀差異欄位再決定。
2. **PG 遷移重跑會使 OCC version +1**（值不變）——CAS 語意不受影響，勿因 version 變動誤判。
3. **Redis fail-open**：Redis 不可達時防雙開僅剩進程鎖（設計取捨）；實機務必同時保留 pgrep/進程鎖機制，Redis 常駐健康。
4. **`order_placed`/`order_cancelled` 事件尚未接下單路徑**（P3-1 已知事項）——事件流目前以 fill/lifecycle/reconcile 為主，稽核時以 `trade_fills` + 交易所端為準。
5. **`GridCommandRepository` 仍為 SQLite**——雙 env 切換時命令歷史不隨 PG 遷移（生命週期命令以引擎 heartbeat promotion 為準，不影響帳本）。
