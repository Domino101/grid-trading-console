# 線上系統無痛導入 v0.2.0-beta（Phase 1–3）指導方針

> 對象：**現場維運 AI / 操作者**——在正在運行的實機上執行本次 branch（Phase 1–3 已完成的版本）導入。
> 目標：回答三個問題——①舊資料如何轉換、②能否近零停機、③依診斷結果給出可照做的執行方針。
> 診斷方式：**靜態逐檔審查 + 沙箱實證**（本文件作者以舊版 v0.9.x 結構的真實資料型態建立模擬庫，實際跑過完整升級路徑與回滾，實證結果標記為「✅沙箱實證」）。
> 前置閱讀：`docs/phase2_production_ops_zh-TW.md`、`docs/phase3_disaster_recovery_zh-TW.md`、`docs/phase1_gate_manual_repro_zh-TW.md`、`docs/phase1_3_live_machine_verification_checklist_zh-TW.md`（導入**完成後**的 PAPER/LIVE 驗證清單）。
> 版本要求：**務必使用本 branch 最新 HEAD**（含本次導入前修復：migrator 補寫 `bot_snapshots`、`filled_orders.cycles` 回補；見 `CHANGELOG.md` Unreleased）。

---

## 0. 診斷總結（一次看完）

### 0.1 新舊資料庫差異

| | 舊版（線上，v0.9.x） | 新版（本 branch，v0.2.0-beta / Phase 1–3） |
|---|---|---|
| 帳本模型 | `grid_runtime_snapshots` 單表 JSON blob（key=`config_path`） | 正規化：`bots` / `trade_fills`（append-only）/ `bot_positions`（OCC version）/ `bot_snapshots` / `bot_snapshot_history` / `bot_identity_events` / `bot_events`（事件流） |
| 身份 | 檔名 stem 隱含身份 → 改檔名=歸零 bug | 穩定 `bot_id`（YAML `metadata.bot_id`，缺漏時退回檔名 stem＝**與舊帳本完全相容**） |
| 後端 | 原生 sqlite3 直連 | SQLAlchemy Core `LedgerStore`（SQLite 預設；設 `DATABASE_URL` 自動切 PostgreSQL） |
| 選配 | 無 | Redis 熱快取/事件推送/領導者選舉（未設定自動關閉） |
| 舊表 | `grid_runtime_snapshots` / `grid_runtime_history` / `grid_commands` / `grid_command_events` / `grid_events` / `dca_klines` / `audit_log` | **全部保留**，且 `grid_runtime_snapshots`/`grid_runtime_history` 成為「legacy 相容表」——新程式碼**持續雙寫**（✅沙箱實證） |

### 0.2 舊資料轉換對照（`DatabaseMigrator`）

| 舊欄位（snapshot payload） | 新表 | 規則 |
|---|---|---|
| payload 整體 | `bots` + `bot_snapshots` | `bot_id` = payload.bot_id，缺漏→檔名 stem（= 舊身份語意）；snapshot 原樣保留 |
| `position.{quantity,average_cost,total_cost}`、`grid_position` | `bot_positions` | quantity 取 position.quantity，缺漏→grid_position |
| `cycles`（DCA 頂層）／`filled_orders.cycles`（grid） | `bot_positions.completed_cycles` | 兩來源都認（本次已修復，✅沙箱實證：grid 舊快照遷移後 cycles=4 正確） |
| `profit.{realized,grid_profit,fees}` | `bot_positions.realized_profit/total_fees` | — |
| `fills[]`（DCA 舊快照有逐筆清單） | `trade_fills` | fill_id/client_order_id 冪等，重跑不重複 |
| `grid_runtime_history`（7 天） | 不動，原地保留 | dashboard 歷史圖讀同一張表，無縫延續 |

**無法轉換（誠實界限）**：舊版 grid 快照沒有逐筆成交紀錄 → grid 的 `trade_fills` 從導入後才開始累積（DCA 有 fills 可回補）；`bot_events` 事件流從導入起點開始（遷移時產生合成 `bot_created`）；`bot_snapshot_history` 從遷移快照開始（舊歷史仍在 `grid_runtime_history` 可查）。UI 顯示的累計 cycles/盈虧/持倉**全部保留**。

### 0.3 三個關鍵診斷發現（沙箱實證，照做就不會踩）

1. **既有 SQLite 庫禁止跑 `alembic upgrade head`**。實證：0001 migration 會重複 `CREATE TABLE grid_runtime_snapshots` → `table already exists` 失敗。既有庫由應用啟動時 `create_all(checkfirst)` 只補缺表（✅沙箱實證：新表 8 張自動補齊、舊表不動）。**Alembic 只用在全新 PostgreSQL（或全新 SQLite）**。
2. **`DatabaseMigrator` 已修復補寫 `bot_snapshots`**：否則遷移後引擎重啟，P1-2 穩定 bot_id 恢復主路徑（`load_by_bot_id`）查無快照，只能靠 legacy config_path/symbol 啟發式兜底（改檔名場景有恢復失敗風險）。
3. **grid 的 cycles 在 `filled_orders.cycles`**：migrator 與 heartbeat 寫入路徑已同步修復，否則 `bot_positions.completed_cycles` 對 grid 恆為 0（投影失真，會污染 Phase 3 replay 對帳）。

---

## 1. 停機評估：近零停機**可以**，完全零停機**不建議**

| 元件 | 停機量 | 說明 |
|---|---|---|
| Web UI / API（`grid-web-ui`） | **≈ 秒級** | 滾動重啟即可。舊引擎寫入的 legacy 表新 API 照讀（✅沙箱實證：`load_all()` 讀 `grid_runtime_snapshots`），**API 先升級、引擎後升級**期間 dashboard 不中斷、資料不歸零。stale 標記門檻 180 秒，秒級重啟對使用者不可見。 |
| 交易引擎（`multi-grid-engine`） | **1–5 分鐘（一次性）** | 引擎是唯一必須暫停的元件。原因：①同一批 bot 不能雙開（下單衝突）；②引擎持有記憶體市場狀態與掛單白名單，單一寫入者；③遷移需要乾淨寫入邊界。優雅停止（SIGTERM）會做最終 flush，**不會**撤交易所掛單——掛單留在盤上（LIVE 期間若成交，重啟後由掛單接管＋三路對帳收斂，見 §7）。PAPER 的撮合器隨引擎暫停，無此風險。 |
| 資料庫（SQLite 檔） | 0 | 遷移是**增量補表＋回補**，不鎖庫、不搬檔、不刪任何舊表（✅沙箱實證）。WAL 模式，migrator 短交易批次。 |
| 前端 | 0（使用者端） | 預先 build，切換時已是新 dist。 |

**為什麼不追求引擎零停機**：雙引擎並行（舊引擎繼續下單 + 新引擎接管）會產生真實的雙重下單/雙寫競態風險，得不償失；且引擎冷啟動自帶狀態恢復（快照 + 掛單接管 + 三路對帳），1–5 分鐘是合理代價。**結論：近零停機可行，定義為「UI 秒級、引擎單次短暫交易暫停」**。

---

## 2. 導入策略總覽：三個窗口、兩條路徑

```
W0 準備窗口（不影響線上）──備份/建新 venv/測試/前端 build/DB 副本乾跑
W1 切換窗口（引擎暫停 1–5 分鐘）──SQLite 原地升級＋代碼切換＋驗證閘
W2 強化窗口（可選、可數日後）──PostgreSQL + Redis 真源切換（需第二次引擎暫停）
```

- **路徑 A（建議首發）**：只做 W0+W1，維持單機 SQLite。行為與 Phase 1 前完全相容、回滾最快。
- **路徑 B（建議 LIVE 前完成）**：W0+W1+W2，切 PostgreSQL 真相源 + Redis。具備 PITR 備份、防雙開、事件推送與三路對帳的完整韌性（`docs/phase2_production_ops_zh-TW.md`）。

**回滾設計**：舊 venv 不動、舊代碼留一份（git 或備份目錄）、DB 備份三個時間點。新版只增表不刪表且 legacy 雙寫保留 → 回滾 = 指回舊 venv + 還原 DB 備份，分鐘級完成。

---

## 3. W0：準備窗口（線上照常運行，零影響）

### 3.1 現場盤點（先弄清楚實際部署型態）

```bash
pm2 list                                   # PM2 部署：multi-grid-engine / grid-volatility-scanner / grid-web-ui
systemctl status crypto-webui              # 或 systemd 部署（API）
ls -la data/ config/grid config/dca
du -h data/web_ui_runtime.sqlite3 data/grid_audit.db
sqlite3 data/web_ui_runtime.sqlite3 ".tables"        # 記錄舊表清單（應含 grid_runtime_snapshots）
git -C /path/to/project log --oneline -5 && git status   # 記錄目前線上版本與本地位改
grep -n "GRID_LIVE_TRADING_ENABLED\|DATABASE_URL\|REDIS_URL" .env   # 記錄資料層開關現況
```

> 輸出全部存進「導入證據紀錄」（附錄 C 範本）。現場若為 PM2 以外的啟動方式（nohup 舊腳本等），先改用 `ecosystem.config.js`/systemd（`docs/web_ui_deployment_and_release_checklist_zh-TW.md`），再往下走。

### 3.2 備份（三個時間點，缺一不可）

```bash
# T0 備份：升級前
mkdir -p backup_$(date +%F)
sqlite3 data/web_ui_runtime.sqlite3 ".backup backup_$(date +%F)/web_ui_runtime_T0.sqlite3"
cp data/grid_audit.db backup_$(date +%F)/grid_audit_T0.db
tar czf backup_$(date +%F)/config_T0.tgz config/grid config/dca
cp .env backup_$(date +%F)/env_T0   && chmod 600 backup_$(date +%F)/env_T0
cp -a /path/to/project backup_$(date +%F)/code_T0     # 或記錄 git commit 備查
```

### 3.3 建**全新** venv（舊 venv 保持不動＝回滾根基）

```bash
cd /path/to/project
python3 -m venv venv_new
./venv_new/bin/pip install -r requirements.txt        # 新版含 rich、psycopg[binary] 等
./venv_new/bin/python -c "import sqlalchemy, alembic, redis, psycopg, rich, yaml, fastapi; print('deps ok')"
```

### 3.4 在沙箱/暫存目錄驗證新代碼（不在線上庫執行）

```bash
git fetch origin && git checkout <本 branch 最新 commit>   # 或先解包到暫存目錄測試
./scripts/ci.sh                                        # 應全綠（含前端 npm test/lint/build）
cd web_ui/frontend && npm ci && npm run build          # 產出 dist
```

### 3.5 資料轉換乾跑（對 DB 副本，不碰線上檔）

```bash
cp data/web_ui_runtime.sqlite3 /tmp/runtime_dryrun.sqlite3
python3 -c "from core.infrastructure.database.migrator import DatabaseMigrator; print(DatabaseMigrator.migrate('/tmp/runtime_dryrun.sqlite3'))"
# 預期輸出格式：{'bots_migrated': N, 'positions_migrated': N, 'fills_migrated': M}
# 再跑一次 → 數值不變（冪等，✅沙箱實證）
sqlite3 /tmp/runtime_dryrun.sqlite3 "SELECT bot_id,strategy_type,symbol,account_mode FROM bots;"
sqlite3 /tmp/runtime_dryrun.sqlite3 "SELECT bot_id,quantity,completed_cycles,realized_profit,version FROM bot_positions;"
# 人工比對：completed_cycles/realized 與 UI 現值一致；grid 不得為 0（§0.3 發現 3 已修復）
```

### 3.6 YAML 身份回填乾跑（不寫入）

```bash
python3 scripts/migration/backfill_bot_ids.py --dry-run
# 列出所有缺 metadata.bot_id 的檔案；確認「檔名 stem = 將回填值」與舊身份一致
```

### 3.7 回滾演練（W0 就先證明能回）

1. 記錄 T0 備份的 checksum：`sha256sum backup_*/web_ui_runtime_T0.sqlite3`。
2. 在**副本**上：跑 migrator → 還原 T0 檔 → 用舊 venv 舊代碼跑 `sqlite3 ... ".tables"` + 開 API 讀一次 → 一切如舊。
3. 確認 PM2/systemd 配置有「舊 venv 路徑」與「新 venv 路徑」兩組可切（見 3.8）。

### 3.8 準備切換用的進程配置

PM2 部署：在 `ecosystem.config.js` 中以 `PYTHON_BIN` 或 env 指到新 venv（兩組配置各存一份）；systemd 部署：準備第二個 unit 檔（`ExecStart` 指 `venv_new`）。**先寫好、不要臨場改。**

> W0 完成判準：三份備份 + checksum 齊全；新 venv 測試全綠；乾跑遷移數值與 UI 現值一致；回滾路徑演練成功。

---

## 4. W1：切換窗口（引擎暫停 1–5 分鐘）

> 建議時段：流動性正常但非使用者活躍時段。全程逐行記錄時間戳與輸出（附錄 C）。

### 4.1 暫停交易引擎（唯一必須的服務中斷，起點）

```bash
pm2 stop multi-grid-engine        # 優雅停止：最終 flush 落盤、斷開 adapter、釋放鎖檔
# 確認 log 末尾出現「多網格併發系統已安全退出」且無 traceback
# 確認掛單行為：引擎停止不會撤單（交易所掛單仍在盤上，重啟後接管）
```

> 此刻 dashboard 仍可讀（API 還在、legacy 表仍在），但 180 秒後會標 stale——**所以 4.2–4.5 要一氣呵成**。

### 4.2 身份回填（實跑）

```bash
python3 scripts/migration/backfill_bot_ids.py
python3 scripts/migration/backfill_bot_ids.py --dry-run   # 重跑應為 0 缺漏（冪等）
```

### 4.3 資料轉換（對線上庫，實跑）

```bash
cp data/web_ui_runtime.sqlite3 backup_$(date +%F)/web_ui_runtime_T1_premigrate.sqlite3   # T1 備份
python3 -c "from core.infrastructure.database.migrator import DatabaseMigrator; print(DatabaseMigrator.migrate())"
# 與 §3.5 乾跑數值一致；秒級完成（小庫）
```

> 若曾誤設 `DATABASE_URL`：migrator 是 SQLite 專用（本窗口不應設定該 env）。SQLite→PG 的搬遷在 W2 用 `sqlite_to_postgres.py`。

### 4.4 切換代碼與 venv

```bash
cd /path/to/project
git fetch origin && git checkout <本 branch 最新 commit>   # 或 rsync 新版代碼
# 確認新代碼到位：git log --oneline -3；含 §0 的三項修復
# 前端 dist 已於 W0 build 完成（web_ui/frontend/dist）
```

### 4.5 重啟服務（先 API、後引擎）

```bash
# PM2：切換 ecosystem 配置至新 venv 後
pm2 restart grid-web-ui           # API 秒級上線
curl --fail http://127.0.0.1:8001/healthz
# 引擎（無 YAML args 即熱插拔待命，由 UI/active_grids 自動拉起既有 bot）
pm2 start multi-grid-engine
pm2 logs multi-grid-engine --lines 50
```

### 4.6 驗證閘（W1 全數通過才算導入成功；任一項不符 → 執行 §6 回滾）

| # | 檢查 | 指令／位置 | 預期 |
|---|---|---|---|
| G1 | 新表存在、舊表未動 | `sqlite3 data/web_ui_runtime.sqlite3 ".tables"` | 新增 bots/trade_fills/bot_positions/bot_snapshots/bot_identity_events/bot_events；舊 7 表仍在 |
| G2 | bot 數正確 | `SELECT count(*) FROM bots;` | = 既有策略數 |
| G3 | grid cycles 非 0 | `SELECT bot_id,completed_cycles FROM bot_positions;` | grid 與 UI 顯示一致、不為 0 |
| G4 | DCA fills 回補 | `SELECT count(*) FROM trade_fills;` | ≥ DCA 歷史成交筆數 |
| G5 | 快照主路徑可命中 | `SELECT bot_id,config_path,version FROM bot_snapshots;` | 每 bot 一列 |
| G6 | dashboard 資料連續 | UI 刷新 + `curl -H "Authorization: Bearer $GRID_API_TOKEN" http://127.0.0.1:8001/api/v1/...` | cycles/realized/持倉與停機前基線一致，**不歸零** |
| G7 | 引擎恢復 log | `pm2 logs multi-grid-engine` | 「已從快照恢復歷史數據…」+ 掛單接管 +「🔍 三路對帳完成」 |
| G8 | 心跳雙寫 | 等 1–2 個 heartbeat 後查 `grid_runtime_snapshots.updated_at` | 時間戳持續更新（legacy 雙寫正常） |
| G9 | 事件流啟用 | `SELECT event_type,count(*) FROM bot_events GROUP BY event_type;` | 出現 bot_created、fill（新成交） |
| G10 | 無鎖錯誤 | `pm2 logs` / `logs/web_ui.log` | 無 `database is locked`、無 traceback |

> W1 完成判準：G1–G10 全綠 → 進入「導入後 PAPER 驗證」（`docs/phase1_3_live_machine_verification_checklist_zh-TW.md` §1–§3 逐項執行）。**該清單全過之前，`GRID_LIVE_TRADING_ENABLED` 必須維持 false。**

---

## 5. W2：PostgreSQL + Redis 強化窗口（可選、建議 LIVE 前）

> 前置：W1 已穩定運行數日且 PAPER 驗證清單 §1–§3 全綠。此窗口需要第二次引擎暫停（同 W1 規模）。

```bash
# 5.1 建置 PG（依 docs/phase2_production_ops_zh-TW.md §3；密碼用強密碼）
psql -U postgres -c "CREATE ROLE grid LOGIN PASSWORD '...';"
psql -U postgres -c "CREATE DATABASE grid OWNER grid;"
# 5.2 全新 PG 庫跑 Alembic（全新庫才可跑；既有 SQLite 禁跑，見 §0.3）
DATABASE_URL=postgresql://grid:...@localhost:5432/grid ./venv_new/bin/python -m alembic upgrade head
DATABASE_URL=postgresql://grid:...@localhost:5432/grid ./venv_new/bin/python -m alembic current   # → 0002_bot_events (head)
# 5.3 停引擎 → SQLite→PG 一次性搬遷（facts 優先、冪等）
pm2 stop multi-grid-engine
./venv_new/bin/python scripts/migration/sqlite_to_postgres.py --sqlite data/web_ui_runtime.sqlite3 --postgres-url "postgresql://grid:...@localhost:5432/grid" --dry-run
./venv_new/bin/python scripts/migration/sqlite_to_postgres.py --sqlite data/web_ui_runtime.sqlite3 --postgres-url "postgresql://grid:...@localhost:5432/grid"
# 重跑一次 → bots/fills 數不變（冪等）；psql 核對 bots/trade_fills/bot_positions 數量與 version
# 5.4 切 env → 重啟（先 API 後引擎；同 §4.5）
#   .env: DATABASE_URL=postgresql://...   REDIS_URL=redis://localhost:6379/0
# 5.5 Redis 三件套演練：TTL、防雙開（第二引擎實例被拒）、停機降級、恢復自癒、/ws/grid-events
# 5.6 投影重建與回放（三路對帳工具）
./venv_new/bin/python scripts/tools/replay_bot.py --bot-id <bot_id>          # 退出碼 0
./venv_new/bin/python scripts/tools/rebuild_projections.py                   # dry-run 對照
```

**W2 回滾**：`.env` 清空 `DATABASE_URL`/`REDIS_URL` → 重啟 → 自動回 SQLite 單機模式（原 SQLite 檔未被破壞，ops 文件 §7；實機演練過再上 LIVE）。

---

## 6. 回滾 SOP（任何窗口）

**觸發條件**：任一驗證閘失敗、資料不連續（歸零/錯值）、`database is locked` 持續、API/引擎起不來、LIVE 帳本與交易所不符。

```bash
# 1) 停新服務
pm2 stop multi-grid-engine grid-web-ui      # 或 systemctl stop crypto-webui
# 2) 還原 T0（或 T1）備份（先再備一份「事故後現場」）
sqlite3 data/web_ui_runtime.sqlite3 ".backup backup_$(date +%F)/web_ui_runtime_INCIDENT.sqlite3"
cp backup_$(date +%F)/web_ui_runtime_T0.sqlite3 data/web_ui_runtime.sqlite3
# 3) 回舊代碼 + 舊 venv（git checkout <舊 commit>；ecosystem 指回舊 venv）
# 4) 起舊服務、跑舊版驗證（dashboard 與基線一致）
pm2 start ecosystem.config.js && curl --fail http://127.0.0.1:8001/healthz
```

**為什麼回滾乾淨**：新版只「新增」表、legacy 表雙寫保留、從不刪舊表/舊資料；舊代碼完全不認識新表，還原 DB 備份後即回到原狀。若已切 PG（W2），回滾＝清空 `DATABASE_URL`/`REDIS_URL` 回 SQLite（PG 端資料不刪，留作證據）。

---

## 7. LIVE 特別注意事項

1. **導入與 LIVE 開關解耦**：本文件全程不涉及開啟 LIVE。`GRID_LIVE_TRADING_ENABLED` 在導入期間及 PAPER 驗證清單全綠前必須是 false。
2. **引擎暫停窗口的市場風險**：SIGTERM 停止不撤交易所掛單；LIVE 情境下掛單在暫停期間仍可能被成交。重啟後依賴「掛單接管（recover_old_orders / reconcile_startup_orders）＋三路對帳（`ExchangeReconciler`）」收斂，`bot_events` 出現 `reconcile` 即為證據。**LIVE 首次導入請排在最不活躍時段並全程盯 log**。
3. **勿混用資料源**：migrator（SQLite 專用）與 `sqlite_to_postgres.py`（SQLite→PG）分工明確；既有 SQLite 庫禁跑 alembic（§0.3 發現 1）。
4. **交易中誤刪/錯改**：不要先刪 YAML 或 SQLite——先停策略、保留現場（稽核證據），再依 `docs/phase3_disaster_recovery_zh-TW.md` 處理。
5. **密鑰衛生**：`.env` 0600；新 venv/備份目錄不入 git；備份檔同樣注意權限。

---

## 附錄 A：快速命令速查

```bash
# 備份/還原
sqlite3 data/web_ui_runtime.sqlite3 ".backup backup_$(date +%F)/web_ui_runtime_T0.sqlite3"
cp backup_$(date +%F)/web_ui_runtime_T0.sqlite3 data/web_ui_runtime.sqlite3

# 轉換（SQLite 原地）
python3 scripts/migration/backfill_bot_ids.py --dry-run && python3 scripts/migration/backfill_bot_ids.py
python3 -c "from core.infrastructure.database.migrator import DatabaseMigrator; print(DatabaseMigrator.migrate())"

# 轉換（SQLite→PG，W2）
./venv_new/bin/python scripts/migration/sqlite_to_postgres.py --sqlite data/web_ui_runtime.sqlite3 --dry-run
./venv_new/bin/python scripts/migration/sqlite_to_postgres.py --sqlite data/web_ui_runtime.sqlite3

# 驗證 SQL（SQLite）
SELECT count(*) FROM bots;                       -- = 策略數
SELECT bot_id,quantity,completed_cycles,realized_profit,version FROM bot_positions;
SELECT count(*) FROM trade_fills;                -- ≥ DCA 歷史成交數
SELECT bot_id,config_path,version FROM bot_snapshots;   -- 每 bot 一列
SELECT event_type,count(*) FROM bot_events GROUP BY event_type;

# 對帳/重建（Phase 3）
./venv_new/bin/python scripts/tools/replay_bot.py --bot-id <bot_id>          # 0=一致 2=差異 3=不存在
./venv_new/bin/python scripts/tools/rebuild_projections.py --execute

# 健康
curl --fail http://127.0.0.1:8001/healthz
pm2 list && pm2 logs multi-grid-engine --lines 50
```

## 附錄 B：可轉換/不可轉換一覽

| 資料 | 導入後狀態 |
|---|---|
| 各 bot 累計 cycles / realized / fees / 持倉 / 均價 | ✅ 保留（bot_positions + bot_snapshots + legacy 表） |
| DCA 歷史逐筆成交 | ✅ 回補進 trade_fills |
| grid 歷史逐筆成交 | ⚠️ 舊版無逐筆紀錄；導入後才開始累積 |
| dashboard 歷史曲線（7 天） | ✅ grid_runtime_history 原地保留 |
| 命令/事件/audit/dca_klines | ✅ 表結構與內容不變 |
| bot_events 事件流 | ⚠️ 自導入起點開始（合成 bot_created） |
| YAML 設定檔 | ✅ 不變（僅補 metadata.bot_id，值=檔名 stem，身份連續） |

## 附錄 C：導入證據紀錄範本

```text
環境：<主機/使用者/PM2 或 systemd>
導入前版本：<git commit> / 導入版本：<本 branch HEAD commit>
T0 備份 checksum：sha256sum ...
W1 時間軸：停引擎 <t1> → 回填 <t2> → 遷移 stats=<...> <t3> → 切代碼 <t4> → API 重啟 <t5> → 引擎重啟 <t6> → 驗證閘 G1-G10 結果
W2（若有）：alembic current=<...>；遷移 stats=<...>；重跑冪等=<...>；Redis 演練=<...>
回滾演練：W0 於副本完成 / 結果
遺留風險與備註：
```
