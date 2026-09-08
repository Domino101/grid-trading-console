# 交易系統架構演進與技術棧重構執行手冊 (AI-Agent Ready Architecture Evolution Roadmap)

> **版本**：v2.0.0 (AI-Agent Operational & Handoff Edition)  
> **更新日期**：2026-09-06  
> **狀態**：正式執行中 (Active & Execution Ready)  
> **目標系統**：`grid-trading-console` (多網格 + DCA 交易終端)  
> **適用對象**：AI Agent 每輪調用、人類工程師協同開發、跨輪次工程交接

---

## 1. 執行摘要與架構願景 (Executive Summary & Architectural North Star)

本專案經過多次業務功能擴充（多網格單進程併發、DCA 多方向馬丁策略、回測工作台、Web UI 雙向控制），已由原型的個人腳本演變為管理實時資產的量化交易系統。

然而，現有架構存在深層的**「原型擴張阻抗不匹配」**：
1. **資料層承載錯誤**：將高頻時序心跳快照（每 2s 5~10KB）強行全量寫入單一 SQLite 檔案，引發磁碟 I/O 壅塞、全表掃描鎖庫與資料庫膨脹。
2. **單線程 GIL 瓶頸**：所有策略在單一 Python 進程共用 Asyncio 事件循環，任何 CPU 密集計算（JSON 序列化、CCXT 市場解析、技術指標運算）皆會導致微凍結，衝擊訂單撮合延遲。
3. **控制面脆弱**：依賴本地檔案輪詢（`active_grids.json` / `active_dca.json`），缺乏即時事件驅動與分散式鎖保護。
4. **安全與權限風險**：全系統以 `root` 身分執行，API 金鑰純文字落地，缺少安全隔離。

本手冊基於**「漸進式重構（Strangler Fig Pattern）」**原則，全面將工作拆解為 **P0 至 P3** 四大階段。每個任務均設計為 **AI Agent 單輪或雙輪可獨立閉環執行的「派工卡 (Dispatch Card)」**，並內建嚴格的前置上下文、不可動搖的約束守則、精確的驗證指令與交接 Checkpoint。

```
[P0 階段：核心瓶頸止血與資料層減負] (最高優先級 · 1-2 週)
   ├── P0-1: 歷史快照寫入限頻與事件驅動落盤 (消除 SQLite 鎖庫與磁碟暴漲)
   ├── P0-2: PostgreSQL 全量雙軌切換與 SQLite 彈性降級 (生產持久層標準化)
   └── P0-3: Redis L1 快照快取接管 Web UI 讀模型 (讀寫徹底分離，DB 零查詢)

[P1 階段：控制面即時化、日誌治理與進程隔離] (3-4 週)
   ├── P1-1: Redis Streams / PubSub 取代檔案輪詢 (指令延遲 5s → <10ms)
   ├── P1-2: PM2 日誌輪轉與磁碟空間自動維護 (防止 log 塞爆磁碟)
   └── P1-3: 主進程監督者與多進程 Worker 隔離架構 (解除 Fate Sharing 與 GIL)

[P2 階段：安全沙箱體系與專用適配器輕量化] (3-4 週)
   ├── P2-1: 系統特權降階 (crypto-runner) 與 Systemd 沙箱加固 (最小特權原則)
   ├── P2-2: API 金鑰本機對稱加密儲存 (AES-256-GCM Vault)
   └── P2-3: Binance 核心交易對專用原生 WS/REST 輕量適配器 (繞過 CCXT 開銷)

[P3 階段：高性能多語言交易核心演進] (長期架構演化)
   ├── P3-1: 控制面 (Python) 與 撮合執行面 (Go/Rust) IPC 協議設計 (Protobuf / UDS)
   └── P3-2: Go 撮合與風控微引擎 PoC 原型驗證與基準壓力測試
```

---

## 2. AI Agent 執行協議與交接標準作業規範 (Agent Orchestration & Handoff SOP)

為避免多輪次 AI 調用過程中的「語境遺忘」、「盲目修改」與「缺乏驗證」，所有被調用的 AI Agent 必須嚴格遵守本章節之規範。

### 2.1 Agent 調用指令藍圖 (Turn Dispatch Prompt Blueprint)
當使用者或前一輪 Agent 指派下一輪工作時，請使用標準化 Prompt 格式：
```markdown
【AI AGENT 派工指令】
- 目標任務：[填寫任務 ID，例如 P0-1]
- 參考文檔：docs/architecture_evolution_roadmap_zh-TW.md
- 要求：
  1. 嚴格遵守該任務卡之「不可動搖的約束守則 (Invariants)」。
  2. 按照步驟完成代碼修改，不得跨任務修改無關模組。
  3. 執行該任務列出之「驗證命令清單」，輸出完整測試結果。
  4. 若修改影響活躍服務，必須依 AGENTS.md 重啟並實機驗證。
  5. 於回答末尾產出「標準交接 Checkpoint (Handoff Ledger)」。
```

### 2.2 不可動搖的五大工程鐵律 (Five Sacred Guardrails)
1. **既有 SQLite 嚴禁執行 `alembic upgrade head`**：
   - 既有 SQLite 資料庫含有歷史動態表，直接跑 Alembic 會引發表衝突或資料損毀。Alembic 僅可用於全新建立的空白庫。既有資料庫欄位補丁一律透過 `core/infrastructure/database/migrator.py` 的檢查語句或 `CREATE TABLE IF NOT EXISTS`。
2. **帳本雙寫向後相容性 (Dual-Write Invariant)**：
   - 在資料層遷移完成且所有外部依賴升級前，`grid_runtime_*`（Legacy 表）與 `bot_*`（Canonical Ledger 表）的雙寫邏輯不可逕自拔除，避免破壞現有報表與工具。
3. **快照單調計數器保護 (Monotonic Protection)**：
   - 快照覆寫時，`completed_cycles`、`realized_pnl`、`filled_count`、`fees` 等累計業務事實嚴禁倒退。非法覆寫必須被拒絕並報警。
4. **活躍服務變更必須重啟並實機驗證 (Mandatory Restart & Live Verification)**：
   - 凡修改影響 Web UI 後端（port 8001）、交易引擎或 PM2 服務者，不可只看靜態代碼，必須檢查背景進程、執行安全重啟，並透過 `curl` 實機確認 HTTP/WebSocket 響應正確。
5. **知識圖譜即時更新 (Graphify Maintenance)**：
   - 任何涉及代碼結構變更的任務完成後，必須執行 `graphify update .` 保持圖譜新鮮度，避免後續 Agent 讀取過期拓撲。

### 2.3 標準交接 Checkpoint 模板 (Handoff Ledger Template)
每輪 Agent 結束工作時，必須在回答結尾輸出此區塊，作為下一輪交接的依據：

```markdown
---
### 📋 AI AGENT 交接 Checkpoint (Handoff Ledger)
- **當前任務 ID**：[例如 P0-1]
- **執行狀態**：[DONE / PARTIAL / BLOCKED]
- **修改檔案清單**：
  - `path/to/modified_file_1.py`
  - `path/to/modified_file_2.py`
- **關鍵變更摘要**：[1-2 句話說明核心改動邏輯]
- **驗證命令執行結果**：
  - `pytest / unittest ...`：[PASS / N failed]
  - `curl / lsof / db query`：[實機驗證輸出摘錄]
- **遺留風險或待辦事項**：[若有未完事項或潛在 edge case 請具體標註，無則寫「無」]
- **下一輪推薦任務**：[例如 P0-2]
---
```

---

## 3. 全局任務優先級與輪次排程總表 (Master Schedule Matrix)

| 優先級 | 任務 ID | 任務名稱 | 預估輪次 | 關鍵影響模組 | 風險等級 | 核心收益 |
| :---: | :---: | :--- | :---: | :--- | :---: | :--- |
| **P0** | **P0-1** | 歷史快照寫入限頻與事件驅動落盤 | 1 輪 | `ledger_store.py`, `async_writer.py` | 低 | 降低 98% DB 寫入量，消除 SQLite 鎖庫與磁碟狂飆 |
| **P0** | **P0-2** | PostgreSQL 全量雙軌切換與 SQLite 彈性降級 | 1 輪 | `ledger_store.py`, `scripts/migration/` | 中 | 啟用標準關聯庫，高併發讀寫徹底解脫鎖庫 |
| **P0** | **P0-3** | Redis L1 快照快取接管 Web UI 讀模型 | 1 輪 | `redis_support.py`, `web_ui/backend/` | 低 | Web UI 查詢零 DB 負載，前端卡片響應 < 1ms |
| **P1** | **P1-1** | Redis Streams / PubSub 取代檔案輪詢 | 1~2 輪 | `run_multiple_grids.py`, `web_ui/backend/` | 中 | 控制指令延遲由 5s 降至 < 10ms，消除檔案競爭 |
| **P1** | **P1-2** | PM2 日誌輪轉與磁碟空間自動維護 | 0.5 輪 | PM2 configs, shell scripts | 低 | 鎖定單日誌上限 20MB，避免磁碟空間靜默耗盡 |
| **P1** | **P1-3** | 主進程監督者與多進程 Worker 隔離架構 | 2 輪 | `run_multiple_grids.py`, Worker Pool | 高 | 解除 Fate Sharing 與 GIL 限制，單機器人崩潰不連坐 |
| **P2** | **P2-1** | 系統特權降階與 Systemd 沙箱配置 | 1 輪 | `scripts/deployment/`, systemd | 中 | 脫離 root 特權執行，實施路徑保護與沙箱隔離 |
| **P2** | **P2-2** | API 金鑰本機對稱加密儲存 (AES-256-GCM) | 1 輪 | `config/`, YAML loader, key vault | 中 | 敏感 Secret 落地加密，防範伺服器被侵入憑證外洩 |
| **P2** | **P2-3** | Binance 核心交易對專用原生 WS/REST 適配器 | 2 輪 | `core/adapters/exchanges/` | 中 | 旁路 CCXT 龐大開銷，取價與下單延遲降低 60% |
| **P3** | **P3-1** | 控制面與 執行面 IPC 通訊協議設計 | 1 輪 | `proto/`, IPC Client/Server | 低 | 定義語言中立之高效二進位撮合介面 |
| **P3** | **P3-2** | Go 撮合與風控微引擎 PoC 原型驗證 | 2 輪 | `trading-engine-go/` (PoC) | 低 | 驗證百級併發微秒撮合可行性，作為次世代核心基石 |

---

## 4. P0 階段：核心瓶頸止血與資料層減負 (Immediate Bottleneck Mitigation)

### P0-1: 歷史快照寫入限頻與事件驅動落盤
- **任務目標**：禁止每次心跳（2s）無條件全量寫入 `bot_snapshot_history` 與 `grid_runtime_history`。改為「重大業務事件觸發持久化」+「時序快照 5 分鐘 (300s) 抽樣持久化」。最新狀態維持寫入 `bot_snapshots`。
- **優先級 / 預估輪次**：`P0` / 1 輪
- **前置依賴**：無
- **目標檔案清單**：
  - [`core/infrastructure/database/ledger_store.py`](file:///root/grid-trading-console/core/infrastructure/database/ledger_store.py)
  - [`core/infrastructure/database/async_writer.py`](file:///root/grid-trading-console/core/infrastructure/database/async_writer.py)
  - [`tests/test_async_writer_full_save.py`](file:///root/grid-trading-console/tests/test_async_writer_full_save.py)
  - [`tests/test_ledger_repository.py`](file:///root/grid-trading-console/tests/test_ledger_repository.py)
- **前置上下文閱讀 (Pre-flight Context)**：
  - 檢視 `ledger_store.py` 中 `save_snapshot()` 函式（約 L835-L905），理解 `bot_snapshots` (最新單行) 與 `bot_snapshot_history` (歷史累加) 的寫入點。
  - 檢視 `async_writer.py` 的佇列消耗循環。
- **不可動搖的約束守則 (Invariants)**：
  1. `bot_snapshots` (最新快照表) 與 `grid_runtime_snapshots` (legacy 最新快照表) 的 upsert 必須每次心跳保持更新，確保 Web UI 隨時讀取最新持倉與指標。
  2. 唯獨 **歷史表**（`bot_snapshot_history`、`grid_runtime_history`）必須降頻與限流。
  3. 保留累計欄位單調防護 (Monotonic Guard)。
- **具體實施步驟**：
  1. 在 `SqlAlchemyLedgerStore` 內部增加抽樣節流閥機制（如記錄每個 `bot_id` 上次寫入歷史表的時間戳 `_last_history_ts: Dict[str, float]`）。
  2. 設定環境變數 `GRID_HISTORY_SNAPSHOT_INTERVAL`（預設 300.0 秒）。
  3. 修改 `save_snapshot(..., force_history: bool = False)`：
     - 若 `force_history=True`（外部由成交、狀態切換等重大事件調用），或 `timestamp - _last_history_ts >= 300.0`，才執行歷史表 INSERT。
     - 一般常規心跳只更新 `bot_snapshots`，跳過歷史表寫入。
  4. 同步確保 `prune_snapshot_history` 維持低頻觸發（每小時至多 1 次）。
- **驗證命令清單 (Quality Gates)**：
  ```bash
  # 1. 執行帳本與異步寫入單元測試
  python3 -m unittest tests/test_async_writer_full_save.py
  python3 -m unittest tests/test_ledger_repository.py
  python3 -m unittest tests/test_phase2_ledger_orchestration.py
  
  # 2. 驗證 10 秒內連續呼叫 5 次 save_snapshot，歷史表記錄僅增加 1 筆
  python3 -c "
  from core.infrastructure.database.ledger_store import SqliteLedgerStore
  import time
  s = SqliteLedgerStore('data/test_throttle.sqlite3')
  for i in range(5):
      s.save_snapshot('test_bot', 'config/test.yaml', {'cycle': 1, 'price': 100+i}, time.time())
      time.sleep(0.1)
  # 檢查歷史表記錄數
  hist = s.load_history('test_bot', limit=10)
  assert len(hist) == 1, f'歷史表應只有 1 筆，實際得到 {len(hist)} 筆'
  print('Throttling test passed successfully!')
  "
  ```
- **完成定義 (DoD)**：
  - 單元測試 100% 通過。
  - 連續高頻呼叫 `save_snapshot` 時，最新快照表正常更新，但歷史表嚴格維持 300 秒間隔，磁碟與 DB 寫入頻率暴降 95% 以上。
- **回滾與降級指引**：
  - 若有歷史圖表依賴高頻資料點而報警，可將 `GRID_HISTORY_SNAPSHOT_INTERVAL` 透過環境變數臨時縮短至 60 秒，無需改代碼。

---

### P0-2: PostgreSQL 全量雙軌切換與 SQLite 彈性降級
- **任務目標**：啟用已就緒的 `PostgresLedgerStore`，將生產環境主持久庫正式切換為 PostgreSQL，同時保留「Postgres 斷線時自動降級本機 SQLite」的熔斷保護。
- **優先級 / 預估輪次**：`P0` / 1 輪
- **前置依賴**：`P0-1` 完成
- **目標檔案清單**：
  - [`core/infrastructure/database/ledger_store.py`](file:///root/grid-trading-console/core/infrastructure/database/ledger_store.py)
  - [`core/infrastructure/database/migrator.py`](file:///root/grid-trading-console/core/infrastructure/database/migrator.py)
  - [`scripts/migration/sqlite_to_postgres.py`](file:///root/grid-trading-console/scripts/migration/sqlite_to_postgres.py)
  - [`tests/test_database_migrator_and_writer.py`](file:///root/grid-trading-console/tests/test_database_migrator_and_writer.py)
- **前置上下文閱讀**：
  - 閱讀 `scripts/migration/sqlite_to_postgres.py`，確認事實表（`bots`、`trade_fills`、`bot_positions`）與投影表遷移次序。
  - 檢視 `ledger_store.py` 的工廠函式 `create_ledger_store()`。
- **不可動搖的約束守則 (Invariants)**：
  1. 對既有 SQLite 檔案嚴禁執行 `alembic upgrade head`。
  2. PostgreSQL 執行 `alembic upgrade head` 或由 `schema.sql` 建立全新表格。
  3. `DATABASE_URL` 未配置時，全自動平滑維持 SQLite 運行，不可中斷開發者環境。
- **具體實施步驟**：
  1. 檢視並增強 `create_ledger_store(db_url: Optional[str] = None)`：
     - 若 `DATABASE_URL` 是 `postgresql+psycopg://...`，嘗試連線 PostgreSQL。
     - 封裝健全的例外處理：若 PostgreSQL 連線失敗（如超時或驗證失敗），記錄嚴重警告日誌，並自動 fallback 至本機 SQLite `data/web_ui_runtime.sqlite3`，確保交易進程不崩潰。
  2. 驗證 `scripts/migration/sqlite_to_postgres.py` 導出腳本：支援將現有 SQLite 的歷史資料無損複製到 PostgreSQL。
  3. 實作連接池參數最佳化（`pool_size=10`, `max_overflow=20`, `pool_recycle=1800`, `pool_pre_ping=True`）。
- **驗證命令清單 (Quality Gates)**：
  ```bash
  # 1. 執行資料庫遷移與寫入器測試
  python3 -m unittest tests/test_database_migrator_and_writer.py
  python3 -m unittest tests/test_monotonic_guard.py
  
  # 2. 測試 SQLite 到 Postgres 乾跑遷移腳本
  python3 scripts/migration/sqlite_to_postgres.py --sqlite data/web_ui_runtime.sqlite3 --dry-run
  ```
- **完成定義 (DoD)**：
  - 提供安全的連線工廠，當 `DATABASE_URL` 設定無效時能 100% 成功退回本機 SQLite 且拋出告警。
  - 遷移腳本 `--dry-run` 與實際遷移能無損轉移所有 `bots`、`trade_fills` 與最新快照。
- **回滾與降級指引**：
  - 於 `.env` 中移除或註解 `DATABASE_URL`，重啟系統即刻切回 SQLite 運行。

---

### P0-3: Redis L1 快照快取接管 Web UI 讀模型
- **任務目標**：啟用 `RedisStateCache`，所有機器人的即時狀態由交易引擎非同步寫入 Redis，Web UI 後端查詢 `/api/v1/grids` 及 WebSocket 推送 100% 優先讀取 Redis，達到讀寫分離，關聯資料庫在常態下零查詢負載。
- **優先級 / 預估輪次**：`P0` / 1 輪
- **前置依賴**：`P0-1` 完成
- **目標檔案清單**：
  - [`core/infrastructure/database/redis_support.py`](file:///root/grid-trading-console/core/infrastructure/database/redis_support.py)
  - [`web_ui/backend/runtime_repository.py`](file:///root/grid-trading-console/web_ui/backend/runtime_repository.py)
  - [`web_ui/backend/main.py`](file:///root/grid-trading-console/web_ui/backend/main.py)
  - [`tests/test_runtime_repository.py`](file:///root/grid-trading-console/tests/test_runtime_repository.py)
  - [`tests/test_dashboard_snapshot.py`](file:///root/grid-trading-console/tests/test_dashboard_snapshot.py)
- **前置上下文閱讀**：
  - 閱讀 `core/infrastructure/database/redis_support.py` 中 `RedisStateCache`（L98-L178）之實作。
  - 閱讀 `web_ui/backend/runtime_repository.py` 的查詢方式。
- **不可動搖的約束守則 (Invariants)**：
  1. Redis 僅作為 Disposable Cache（可拋棄快取），嚴禁將 Redis 視為唯一的真理來源 (Source of Truth)。
  2. 若 Redis 離線或快取 miss，系統必須無縫回退至資料庫查詢，不可向上層 API 拋出 500。
- **具體實施步驟**：
  1. 在 `runtime_repository.py` 注入 `RedisStateCache`。
  2. 查詢單一機器人最新快照時：先讀 `RedisStateCache.get(config_path, bot_id)`；若存在則直接返回（耗時 < 1ms）；若不存在則查詢資料庫，並回填 Redis。
  3. 查詢所有機器人快照時：呼叫 `RedisStateCache.get_all()`；若快取命中完整，直接回傳。
  4. 引擎端在呼叫 `save_snapshot()` 時，同步非同步寫入 Redis `grid:state:{bot_id}`，TTL 設為 300 秒。
- **驗證命令清單 (Quality Gates)**：
  ```bash
  # 1. 執行 Repository 與 Dashboard 單元測試
  python3 -m unittest tests/test_runtime_repository.py
  python3 -m unittest tests/test_dashboard_snapshot.py
  
  # 2. 驗證 Redis 斷線自動降級
  python3 -c "
  from core.infrastructure.database.redis_support import RedisStateCache
  cache = RedisStateCache()
  # 即使 REDIS_URL 沒開或不存在，也不應崩潰拋例外
  val = cache.get('non_existent_config')
  assert val is None
  print('Redis fallback resilience verified!')
  "
  ```
- **完成定義 (DoD)**：
  - Web UI 後端在 Redis 啟用時，高頻首頁刷新的資料庫查詢次數歸零。
  - Redis 斷線時後端透明降級查詢 DB，API 無任何報錯。
- **回滾與降級指引**：
  - 於環境變數清空 `REDIS_URL`，系統立即純走關聯庫查詢。

---

## 5. P1 階段：控制面即時化、日誌治理與進程隔離 (Control & Process Pool)

### P1-1: Redis Streams / PubSub 取代檔案輪詢控制面
- **任務目標**：徹底廢除 `active_grids_monitor()` 與 `active_dca_monitor()` 每 5 秒讀取磁碟 `config/active_grids.json` 及 `config/active_dca.json` 的低效輪詢機制。改為基於 Redis Streams 的即時指令隊列與 Pub/Sub 事件廣播，同時保留檔案作為持久化備份。
- **優先級 / 預估輪次**：`P1` / 1~2 輪
- **前置依賴**：`P0-3` 完成
- **目標檔案清單**：
  - [`run_multiple_grids.py`](file:///root/grid-trading-console/run_multiple_grids.py)
  - [`core/infrastructure/database/redis_support.py`](file:///root/grid-trading-console/core/infrastructure/database/redis_support.py)
  - [`web_ui/backend/main.py`](file:///root/grid-trading-console/web_ui/backend/main.py)
  - [`tests/test_hermes_client.py`](file:///root/grid-trading-console/tests/test_hermes_client.py)
- **前置上下文閱讀**：
  - 檢視 `run_multiple_grids.py` L819-L880 之 `active_grids_monitor()` 迴圈與 L712-L745 之檔案讀寫。
  - 檢視 `redis_support.py` 中 `RedisEventBus`。
- **不可動搖的約束守則 (Invariants)**：
  1. 向後相容性：Web UI 下發指令時，仍須異步同步寫入 `active_grids.json`，確保在無 Redis 的純本機單機開發環境下仍可啟動。
  2. 指令等冪性 (Idempotency)：重複收到同一網格啟動指令不得重複派生協程。
- **具體實施步驟**：
  1. 在 `redis_support.py` 擴充 `RedisCommandQueue`：
     - 指令格式：`{"command_id": "uuid", "action": "START|STOP|RELOAD", "target_type": "grid|dca", "config_path": "...", "timestamp": ...}`
     - 使用 Redis Stream `grid:commands` 儲存，使用 Consumer Group `supervisor_group`。
  2. 修改 `run_multiple_grids.py`：
     - 將 `active_grids_monitor()` 升級為事件驅動協程：使用 `XREADGROUP` 阻塞等待（block=1000ms），收到啟動/停止指令在 10 毫秒內立刻響應執行對應的 coordinator 啟停。
     - 若未設定 `REDIS_URL`，自動回退到原本的 5 秒檔案輪詢機制。
  3. 修改 Web UI 後端：
     - 在使用者點擊啟動/停止時，直接向 `grid:commands` 發送 Stream 訊息，同時非同步備份 `active_grids.json`。
- **驗證命令清單 (Quality Gates)**：
  ```bash
  # 1. 執行 Hermes 客戶端與生命週期測試
  python3 -m unittest tests/test_hermes_client.py
  python3 -m unittest tests/test_lifecycle_and_safety.py
  
  # 2. 實機發送指令延遲測試（若有 Redis）
  python3 -c "
  import time
  # 模擬指令發佈到消費接收的耗時驗證
  t0 = time.perf_counter()
  # command dispatch logic
  t_cost = (time.perf_counter() - t0) * 1000
  print(f'Command pipeline latency: {t_cost:.2f}ms')
  assert t_cost < 20.0
  "
  ```
- **完成定義 (DoD)**：
  - Web UI 按下停止按鈕後，交易機器人撤單與停止在 100ms 內完成（原為 0~5000ms）。
  - 無 Redis 環境下平滑 fallback 為原有檔案監聽，既有測試無回歸問題。
- **回滾與降級指引**：
  - 在配置中設定 `GRID_FORCE_FILE_MONITOR=1`，強制完全停用 Redis 指令隊列，維持純檔案輪詢。

---

### P1-2: PM2 日誌輪轉與磁碟空間自動維護
- **任務目標**：治理本機日誌增長過速與磁碟被塞爆風險。配置 PM2 日誌自動輪轉（`pm2-logrotate`）與建立過期日誌修剪機制。
- **優先級 / 預估輪次**：`P1` / 0.5 輪
- **前置依賴**：無
- **目標檔案清單**：
  - `scripts/maintenance/rotate_logs.sh` [NEW]
  - `logs/` 目錄治理
- **前置上下文閱讀**：
  - 檢查目前 `logs/` 下檔案大小與 `pm2 list`。
- **不可動搖的約束守則 (Invariants)**：
  1. 輪轉過程嚴禁鎖死或中斷運作中進程的標準輸出。
  2. 嚴禁無備份刪除 7 天內的交易稽核日誌。
- **具體實施步驟**：
  1. 安裝並配置 `pm2-logrotate`：
     ```bash
     pm2 install pm2-logrotate
     pm2 set pm2-logrotate:max_size 20M
     pm2 set pm2-logrotate:retain 5
     pm2 set pm2-logrotate:compress true
     ```
  2. 撰寫 `scripts/maintenance/rotate_logs.sh`，針對專案內部的 `logs/*.log` 與 `data/grid_audit.db` 提供每週自動整理與 gzip 壓縮。
- **驗證命令清單 (Quality Gates)**：
  ```bash
  # 檢查 PM2 日誌輪轉配置
  pm2 conf pm2-logrotate
  # 測試日誌修剪腳本乾跑
  bash scripts/maintenance/rotate_logs.sh --dry-run
  ```
- **完成定義 (DoD)**：
  - 單檔日誌超過 20MB 自動輪轉，最多保留 5 份壓縮存檔，磁碟使用量嚴格受控。

---

### P1-3: 主進程監督者與多進程 Worker 隔離架構 (Worker Pool)
- **任務目標**：消除單進程多網格的連帶崩潰風險（Fate Sharing），將多網格架構升級為「Master 監督進程 + Worker 子進程池」。每個子進程擁有獨立的 Python 進程與 Asyncio Loop，徹底釋放多核心 CPU 並實現程序自癒。
- **優先級 / 預估輪次**：`P1` / 2 輪
- **前置依賴**：`P1-1` 完成
- **目標檔案清單**：
  - `core/supervisor/master_daemon.py` [NEW]
  - `core/supervisor/worker_runner.py` [NEW]
  - [`run_multiple_grids.py`](file:///root/grid-trading-console/run_multiple_grids.py)
  - `tests/test_worker_isolation.py` [NEW]
- **前置上下文閱讀**：
  - 檢視 `run_multiple_grids.py` 中 `run_single_grid_task()`（L600-L700）。
- **不可動搖的約束守則 (Invariants)**：
  1. 單一 Worker 崩潰退出時，Master 必須捕獲 exit code，發出告警，並進行帶指數退避（Backoff）的自動重啟，其餘 Worker 進程不得受到任何干擾。
  2. 保持現有 CLI 命令介面向下相容。
- **具體實施步驟**：
  1. 建立 `core/supervisor/worker_runner.py`：
     - 作為子進程入口，僅接受單一策略設定檔路徑（`--config config/grid/...yaml`）。
     - 獨立執行 Asyncio Loop 與交易所連線。
  2. 建立 `core/supervisor/master_daemon.py`：
     - 管理子進程生命週期（使用 `multiprocessing` 或 `subprocess.Popen`）。
     - 監聽 Redis 指令；當收到特定策略啟動/停止時，啟動或終止對應的子進程。
     - 實作心跳健康檢查：監控 Worker 進程存活狀態，若異常退出且非使用者手動停止，依策略進行重拉。
  3. `run_multiple_grids.py` 提供 `--mode=multi-process` 開關切換，確保穩定演進。
- **驗證命令清單 (Quality Gates)**：
  ```bash
  # 1. 執行進程隔離測試
  python3 -m unittest tests/test_worker_isolation.py
  
  # 2. 模擬 Worker 異常 crash 測試
  python3 -c "
  # 驗證 Master 在子進程 SIGKILL 後能在 5 秒內感應並記錄異常狀態
  print('Process resilience test passed!')
  "
  ```
- **完成定義 (DoD)**：
  - 10 隻機器人分散於獨立 OS 進程中，CPU 負載自然分配至多核心。
  - 手動 `kill -9` 其中一個 Worker 進程，其他 9 隻機器人交易掛單完全不受影響，Master 正確記錄故障並嘗試自癒。
- **回滾與降級指引**：
  - 執行時不帶 `--mode=multi-process`，即可完全無縫維持原有單進程 Asyncio 併發模式。

---

## 6. P2 階段：安全沙箱體系與專用適配器輕量化 (Security & Adapters)

### P2-1: 系統特權降階與 Systemd 沙箱配置
- **任務目標**：將系統由 `root` 特權執行降階為低特權系統帳號 `crypto-runner`，並透過 Systemd 配置嚴格的檔案系統沙箱。
- **優先級 / 預估輪次**：`P2` / 1 輪
- **前置依賴**：無
- **目標檔案清單**：
  - [`scripts/deployment/crypto-webui.service`](file:///root/grid-trading-console/scripts/deployment/crypto-webui.service)
  - `scripts/deployment/crypto-engine.service` [NEW]
  - `scripts/setup/create_service_user.sh` [NEW]
- **不可動搖的約束守則 (Invariants)**：
  1. 降階後必須確保對 `config/`、`data/`、`logs/` 具有讀寫權限。
  2. 嚴禁開放對系統目錄（`/etc`、`/usr`、`/root`）的寫入權限。
- **具體實施步驟**：
  1. 撰寫 `create_service_user.sh`：建立無登入 shell 的專用系統帳號：
     ```bash
     useradd -r -s /usr/sbin/nologin -d /opt/grid-trading-console crypto-runner
     ```
  2. 更新 Systemd 服務單元：
     - `User=crypto-runner`、`Group=crypto-runner`
     - `ProtectSystem=strict`
     - `ProtectHome=true`
     - `ReadWritePaths=/opt/grid-trading-console/config /opt/grid-trading-console/data /opt/grid-trading-console/logs`
     - `NoNewPrivileges=true`
- **驗證命令清單 (Quality Gates)**：
  ```bash
  # 檢查 Systemd 語法合法性
  systemd-analyze verify scripts/deployment/crypto-webui.service
  ```
- **完成定義 (DoD)**：
  - 服務以 `crypto-runner` 身分運作正常，無法存取或竄改非專案目錄之檔案。

---

### P2-2: API 金鑰本機對稱加密儲存 (AES-256-GCM Vault)
- **任務目標**：廢除純文字存放 API Secret 的做法。在 YAML 設定檔與資料庫中，金鑰與密鑰一律以 AES-256-GCM 密文存儲，由環境變數提供的 Master Key 於運行時在記憶體中即時解密。
- **優先級 / 預估輪次**：`P2` / 1 輪
- **前置依賴**：無
- **目標檔案清單**：
  - `core/security/vault.py` [NEW]
  - `core/security/__init__.py` [NEW]
  - `tests/test_secret_vault.py` [NEW]
- **不可動搖的約束守則 (Invariants)**：
  1. 若 Master Key 缺失或解密失敗，進程必須立即拋出致命例外並拒絕啟動，嚴禁使用空金鑰對交易所下單。
  2. 日誌過濾器必須全面遮蔽（Mask）解密後的明文金鑰。
- **具體實施步驟**：
  1. 實作 `core/security/vault.py`：
     - 提供 `encrypt_secret(plaintext: str, master_key: str) -> str`（輸出 `enc:gcm:...` 格式）。
     - 提供 `decrypt_secret(ciphertext: str, master_key: str) -> str`。
     - 採用 `cryptography.hazmat.primitives.ciphers.aead.AESGCM`。
  2. 設定檔載入器（YAML Loader）在讀取含 `enc:gcm:` 前綴欄位時，呼叫解密函數注入記憶體。
  3. 提供 CLI 輔助工具 `tools/encrypt_api_key.py` 供使用者加密現有金鑰。
- **驗證命令清單 (Quality Gates)**：
  ```bash
  # 執行金鑰加密與解密測試
  python3 -m unittest tests/test_secret_vault.py
  ```
- **完成定義 (DoD)**：
  - YAML 與 DB 內不再出現明文 Secret；加解密往返 100% 正確；解密失敗時程序安全熔斷。

---

### P2-3: Binance 核心交易對專用原生 WS/REST 輕量適配器
- **任務目標**：針對主力交易所（Binance），旁路（Bypass）CCXT 龐大的泛用結構解析。以原生 `websockets` + 原生 C 加速 HMAC 下單簽名器，將下單與行情延遲壓低 60%。
- **優先級 / 預估輪次**：`P2` / 2 輪
- **前置依賴**：`P0-1` 完成
- **目標檔案清單**：
  - `core/adapters/exchanges/adapters/binance_fast.py` [NEW]
  - [`core/adapters/exchanges/adapters/binance_base.py`](file:///root/grid-trading-console/core/adapters/exchanges/adapters/binance_base.py)
  - `tests/test_binance_fast_adapter.py` [NEW]
- **不可動搖的約束守則 (Invariants)**：
  1. 必須嚴格實作既有的 `ExchangeAdapter` 介面協定，對策略層上層完全透明相容。
  2. 長尾交易所（OKX、Hyperliquid、Lighter、Backpack）繼續沿用穩定適配器，不破壞生態。
- **具體實施步驟**：
  1. 行情層：原生 WebSocket 連線直連 `wss://fstream.binance.com/ws/{symbol}@ticker/{symbol}@depth5`。
  2. 下單層：使用預編譯 HMAC-SHA256 簽名，並採用維持持久 Keep-Alive 連線池的 `httpx` 或 `aiohttp`。
  3. 性能測試：比對標準 CCXT 適配器與 Fast 適配器之 Memory Footprint 與 Round-Trip Latency。
- **驗證命令清單 (Quality Gates)**：
  ```bash
  python3 -m unittest tests/test_binance_fast_adapter.py
  ```
- **完成定義 (DoD)**：
  - 單實例記憶體開銷由 150MB 降至 40MB 以下，下單內部準備耗時由 15ms 降至 < 2ms。

---

## 7. P3 階段：高性能多語言交易核心演進 (Polyglot High-Performance Core)

### P3-1: 控制面與 執行面 IPC 通訊協議設計
- **任務目標**：定義語言中立之高速二進位通訊協議（Protobuf + Unix Domain Socket / gRPC），為將執行面遷移至 Go/Rust 奠定合約標準。
- **優先級 / 預估輪次**：`P3` / 1 輪
- **前置依賴**：`P1-3` 完成
- **目標檔案清單**：
  - `proto/trading_core.proto` [NEW]
  - `proto/build_proto.sh` [NEW]
- **具體實施步驟**：
  1. 定義 `OrderRequest`、`OrderResponse`、`OrderBookUpdate`、`PositionState` 等 Protobuf 結構。
  2. 產生 Python 與 Go 之 Stub 檔案。

---

### P3-2: Go 撮合與風控微引擎 PoC 原型驗證
- **任務目標**：建立最小可行之 Go 原型引擎（PoC），驗證記憶體撮合、毫秒風控切斷與原生 Goroutine 高併發之效能表現。
- **優先級 / 預估輪次**：`P3` / 2 輪
- **前置依賴**：`P3-1` 完成
- **目標檔案清單**：
  - `trading-core-go/main.go` [NEW]
  - `trading-core-go/engine/orderbook.go` [NEW]
- **完成定義 (DoD)**：
  - 在 100 併發模擬網格壓力下，CPU 佔用 < 1%，P99 訂單撮合延遲 < 500 微秒 (μs)。

---

## 8. 技術指標對比與量化驗收矩陣 (Target Metrics)

| 關鍵指標 (Key Metrics) | 目前現狀 (Current) | 階段 P0 目標 | 階段 P1 目標 | 階段 P2 目標 | 階段 P3 目標 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **CPU 佔用率 (10+ Bots)** | 40% ~ 70% (單核飽和) | **< 15%** | **< 5% (均攤多核)** | **< 2%** | **< 0.5%** |
| **記憶體佔用 (Footprint)** | ~600MB ~ 1GB | **~400MB** | **~250MB** | **~100MB** | **< 30MB** |
| **資料庫每月增長** | 5GB ~ 8GB (SQLite 膨脹) | **< 200MB (抽樣控頻)**| **< 150MB** | **< 100MB** | **< 50MB** |
| **控制指令生效延遲** | 0 ~ 5,000ms (檔案輪詢) | 0 ~ 5,000ms | **< 10ms (Redis)** | **< 10ms** | **< 1ms (UDS)** |
| **行情撮合/內部開銷** | 50 ~ 200ms (GIL 競爭) | **15 ~ 30ms** | **5 ~ 10ms** | **2 ~ 5ms** | **< 500μs** |
| **單機器人崩潰影響** | 全系統重啟 (Fate Sharing) | 全系統重啟 | **獨立 Worker 自癒** | **獨立 Worker 自癒** | **獨立 Worker 自癒** |
| **運作特權階層** | `root` 執行 | `root` 執行 | `root` 執行 | **低權限沙箱用戶** | **低權限 Docker 容器** |

---

## 9. 系統自癒、異常熔斷與災難恢復 (Self-Healing & Disaster Recovery)

1. **資料持久層中斷 (Database Outage)**：
   - **自癒行為**：若 PostgreSQL 發生暫態斷線或網路抖動，`SqlAlchemyLedgerStore` 自動啟用本地記憶體 buffer，重試失敗後退回本地 `data/web_ui_runtime.sqlite3` 雙寫快取，交易策略不斷單。
2. **通訊佇列斷線 (Redis Crash)**：
   - **降級行為**：若 Redis 伺服器崩潰，`safe_redis` 包裝器保證所有資料庫操作與狀態查詢透明回退為直接查詢關聯資料庫；控制面自動由 Redis Stream 回退為檔案輪詢。
3. **金鑰解密失敗 (Key Vault Failure)**：
   - **熔斷保護**：若 Master Key 不正確或金鑰被竄改，Worker 啟動階段拋出 `SecurityFatalError`，拒絕建立交易所連線，絕不下送任何毀損的假單或空單。
4. **狀態重建協議 (State Reconciliation)**：
   - 遭遇極端重啟時，系統依據 `tools/rebuild_grid_history_from_fills.py` 與 `core/services/reconciler/` 透過交易所真實成交歷史（Facts）重建最新倉位與快照，保證帳本一致性。

---

*本文件歸檔於專案文檔庫 [`docs/architecture_evolution_roadmap_zh-TW.md`](file:///root/grid-trading-console/docs/architecture_evolution_roadmap_zh-TW.md)，為全系統架構升級、AI Agent 派工調用與交接管理之權威依據。*
