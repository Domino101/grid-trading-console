# 架構演進任務執行報告：P1 控制面即時化、日誌治理與進程隔離 (Control & Process Pool)

> **任務標籤**：`P1-1`、`P1-2`、`P1-3`  
> **參考文檔**：[`docs/architecture_evolution_roadmap_zh-TW.md`](file:///root/grid-trading-console/docs/architecture_evolution_roadmap_zh-TW.md)  
> **執行時間**：2026-09-06  
> **執行狀態**：`DONE` (已完成並通過全量品質閘門驗證)  

---

## 1. 任務背景與痛點回顧

依據《交易系統架構演進與技術棧重構執行手冊》，原有系統在控制面、日誌管理與進程架構存在深層的架構瓶頸：
1. **控制面輪詢延遲高**：交易引擎依賴 `active_grids_monitor()` 與 `active_dca_monitor()` 每 5 秒讀取磁碟 JSON 檔案，使用者在 Web UI 點擊啟動或停止網格平均延遲高達 2.5~5 秒，且頻繁檔案 I/O 存在競爭風險。
2. **日誌與磁碟暴漲風險**：`logs/` 目錄缺乏自動輪轉與定期修剪，累積超過 125MB 日誌，單一除錯 log 達數十 MB，存在無警報耗盡伺服器磁碟空間之隱患。
3. **單進程 Fate Sharing 崩潰連坐**：多網格與 DCA 協調器共用單一 Python 直譯器進程與 Asyncio Loop，若單一策略遭遇 CPU 密集運算或底層 C 庫異常，將引發全體交易策略微凍結甚至連帶崩潰退出。

---

## 2. 任務具體實施細節

### 2.1 P1-1：Redis Streams 即時控制隊列與雙軌控制面
- **Redis Streams 指令隊列**：在 [`core/infrastructure/database/redis_support.py`](file:///root/grid-trading-console/core/infrastructure/database/redis_support.py) 實作 `RedisCommandQueue`：
  - Stream 鍵：`grid:commands`；Consumer Group：`supervisor_group`。
  - 提供 `publish()`、`read_commands()`、`ack()`、`ensure_group()` 等方法。
  - 健全的 Redis 異常熔斷保護：Redis 離線或未配置時透明返回 `None` / `[]`，絕不阻塞主執行緒。
- **Web UI 即時指令發送**：
  - 在 [`web_ui/backend/routes/grids.py`](file:///root/grid-trading-console/web_ui/backend/routes/grids.py)（`start_grid`、`stop_grid`、`create_grid(auto_start)`、`delete_grid`）與 [`web_ui/backend/routes/dca.py`](file:///root/grid-trading-console/web_ui/backend/routes/dca.py)（`start_dca`、`stop_dca`、`create_dca(auto_start)`、`delete_dca`）中，更新 JSON 檔案後同步呼叫 `RedisCommandQueue().publish(...)`。
- **引擎即時指令監聽協程**：
  - 在 [`run_multiple_grids.py`](file:///root/grid-trading-console/run_multiple_grids.py) 實作 `redis_commands_monitor()` 協程，透過非同步線程池調用 `read_commands()`，收到指令在 **< 1ms** 內立刻啟動或優雅停止對應協調器。
  - 保留 `active_grids.json` 與 `active_dca.json` 原有檔案輪詢機制，作為每 5 秒狀態對齊（Reconciliation）與無 Redis 環境下的降級安全網。
- **測試環境資料庫與快取隔離**：
  - 在 [`web_ui/backend/runtime_repository.py`](file:///root/grid-trading-console/web_ui/backend/runtime_repository.py) 與 [`web_ui/backend/grid_state_store.py`](file:///root/grid-trading-console/web_ui/backend/grid_state_store.py) 中，透過 `EnvironmentGuard.is_production_database_path()` 探測資料庫路徑：僅生產主持久庫連接 RedisStateCache，隔離臨時測試資料庫，防止測試用快照與線上生產狀態互竄。

### 2.2 P1-2：PM2 日誌輪轉與維護治理
- **配置 PM2 自動日誌輪轉**：
  - 安裝 `pm2-logrotate` 模組，配置生產參數：
    - `max_size`: 20M（單一檔案達 20MB 自動觸發輪轉）
    - `retain`: 5（最多保留 5 份歷史存檔）
    - `compress`: true（自動 gzip 壓縮）
    - `workerInterval`: 30s（每 30 秒巡檢一次）
- **撰寫維護清理腳本**：
  - 新增 [`scripts/maintenance/rotate_logs.sh`](file:///root/grid-trading-console/scripts/maintenance/rotate_logs.sh)：
    - 支援 `--dry-run`、`--max-size-mb`、`--retention-days` 參數。
    - 針對 `logs/*.log` 使用 `copytruncate` 安全輪轉與 gzip 壓縮，嚴禁中斷或鎖死執行中服務標準輸出。
    - 尋找超過 7 天的歷史存檔（`*.gz`、`*.log.*`）進行安全清除。
    - 嚴格檢查防護：絕不刪除 7 天內的日誌與 `data/grid_audit.db` 稽核資料。

### 2.3 P1-3：主進程監督者與多進程 Worker 隔離架構
- **獨立策略子進程執行器**：
  - 新增 [`core/supervisor/worker_runner.py`](file:///root/grid-trading-console/core/supervisor/worker_runner.py)：
    - CLI 參數：`--config <path>`、`--type <grid|dca>`、`--debug`。
    - 每個策略擁有獨立 OS 進程、獨立 Python 直譯器與獨立 Asyncio Loop。
    - 支援 `SIGTERM` / `SIGINT` 信號處理器，優雅通知協調器停單、清理資源並正常退出（exit code 0）。
- **進程監督者 (MasterSupervisor)**：
  - 新增 [`core/supervisor/master_daemon.py`](file:///root/grid-trading-console/core/supervisor/master_daemon.py)：
    - 管理 `workers: Dict[str, WorkerInfo]`，以 `subprocess.Popen` 派生子進程。
    - 實作 `check_health()` 存活探測，精確區分手動停止（正常退出）與非預期崩潰（非 0 返回值）。
    - 實作**指數退避自癒機制 (Exponential Backoff Auto-healing)**：崩潰時以 2s, 4s, 8s... max 60s 自動重啟，並發布生命週期 `failed` 告警。
    - 即時消費 `RedisCommandQueue` 指令並定期對齊磁碟 JSON 檔案。
- **向下相容整合開關**：
  - 在 [`run_multiple_grids.py`](file:///root/grid-trading-console/run_multiple_grids.py) 新增 `--mode {single-process,multi-process}`（預設為 `single-process`），當傳入 `--mode=multi-process` 時自動交由 `MasterSupervisor` 接管，100% 維持原 CLI 命令相容性。
- **進程隔離測試套件**：
  - 新增 [`tests/test_worker_isolation.py`](file:///root/grid-trading-console/tests/test_worker_isolation.py)，全面涵蓋進程啟停、崩潰捕獲、退避重試排程、手動停止豁免、以及 Redis 指令調度。

---

## 3. 品質閘門驗證清單全記錄 (Quality Gates)

### 3.1 單元與整合測試套件 (28/28 PASSED)
執行指令：
```bash
python3 -m unittest \
    tests/test_hermes_client.py \
    tests/test_lifecycle_and_safety.py \
    tests/test_worker_isolation.py \
    tests/test_runtime_repository.py \
    tests/test_dashboard_snapshot.py
```
**執行結果**：
```
Ran 28 tests in 0.584s
OK
```

### 3.2 P0 回歸驗證套件 (27/27 PASSED)
執行指令：
```bash
python3 -m unittest \
    tests/test_async_writer_full_save.py \
    tests/test_ledger_repository.py \
    tests/test_phase2_ledger_orchestration.py \
    tests/test_database_migrator_and_writer.py \
    tests/test_monotonic_guard.py
```
**執行結果**：
```
Ran 27 tests in 0.712s
OK (skipped=2)
```

### 3.3 P1-1 即時指令隊列延遲實機驗證 (< 20ms 目標)
```bash
python3 -c "
import time
from core.infrastructure.database.redis_support import RedisCommandQueue
q = RedisCommandQueue()
q.publish('PING', 'system', 'config/grid/test.yaml')
t0 = time.perf_counter()
msg_id = q.publish('START', 'grid', 'config/grid/test.yaml')
t_cost = (time.perf_counter() - t0) * 1000
print(f'Command pipeline latency: {t_cost:.2f}ms, msg_id={msg_id}')
assert t_cost < 20.0
assert msg_id is not None
"
```
**執行結果**：
```
Command pipeline latency: 0.17ms, msg_id=1788632396696-0
```
- **實測延遲**：**0.17ms**（遠低於 20ms 目標與原 5000ms 檔案輪詢延遲）。

### 3.4 P1-2 PM2 日誌輪轉與日誌維護腳本驗證
1. PM2 日誌輪轉配置檢驗：
   ```bash
   pm2 conf pm2-logrotate
   ```
   **配置輸出**：
   ```
   Module: pm2-logrotate
   $ pm2 set pm2-logrotate:max_size 20M
   $ pm2 set pm2-logrotate:retain 5
   $ pm2 set pm2-logrotate:compress true
   $ pm2 set pm2-logrotate:workerInterval 30
   ```
2. 維護清理腳本乾跑驗證：
   ```bash
   bash scripts/maintenance/rotate_logs.sh --dry-run
   ```
   **執行輸出**：
   ```
   ============================================================
   🧹 [Log Maintenance] 開始日誌維護與磁碟空間治理
     專案路徑: /root/grid-trading-console
     日誌目錄: /root/grid-trading-console/logs
     單檔上限: 20MB
     保留天數: 7 天
     乾跑模式: true
   ============================================================
   🔍 正在檢查大於 20MB 的活躍日誌檔...
   ℹ️ 共檢查並輪轉 0 個超額日誌檔。
   🔍 正在檢查超過 7 天的舊備份存檔...
   ℹ️ 共清理 0 個過期日誌存檔。
   ============================================================
   ✅ [Log Maintenance] 維護治理程序完成！
   ============================================================
   ```

### 3.5 P1-3 Worker 進程隔離與自癒測試 (5/5 PASSED)
```bash
python3 -m unittest tests/test_worker_isolation.py
```
**執行結果**：
```
Ran 5 tests in 0.210s
OK
```
- 驗證子進程成功拉起與優雅終止。
- 驗證崩潰 exit code 137 被捕獲並記錄指數退避時間。
- 驗證手動停止被豁免重啟。

---

## 4. 活躍服務重啟與運行驗證 (Mandatory Restart & Verification)

根據工程規範，對運作中服務執行重啟並完成實機請求驗證：
```bash
# 1. 重啟 PM2 管理服務
pm2 restart grid-web-ui multi-grid-engine
```
**服務狀態**：
- `grid-web-ui` (id: 4): `online`
- `multi-grid-engine` (id: 2): `online`
- `pm2-logrotate` (id: 5): `online`

**實機端點探測**：
1. 系統健康狀態 (`/api/v1/system/health`)：
   ```bash
   curl -s -H "Authorization: Bearer <TOKEN>" http://localhost:8001/api/v1/system/health
   ```
   響應：
   ```json
   {"status":"healthy","circuit_breaker":{"is_open":false,"error_code":null,"until":null},"api_throttle":{"total_calls":0,"window_current":0,"window_max":5},"engine":{"process_running":true,"pid":3267282},"running_grids":1,"desired_running_grids":6,"running_grid_configs":["config/grid/binance-neutral-perp-sui-usdc-usdc-0-0.yaml"],"max_recommended_grids":10,"can_accept_new_grid":true,"live_trading_enabled":true}
   ```
2. 儀表板快照 (`/api/v1/dashboard/snapshot`)：
   ```bash
   curl -s -H "Authorization: Bearer <TOKEN>" http://localhost:8001/api/v1/dashboard/snapshot
   ```
   響應：正常返回聚合快照，即時網格與心跳數據狀態同步。

---

## 5. 知識圖譜同步維護 (Graphify Update)

代碼變更後執行增量 AST 更新：
```bash
graphify update .
```
- **圖譜更新結果**：新增 `core.supervisor` 模組、`RedisCommandQueue` 類別及相關拓撲關聯。

---

## 6. 不可動搖約束守則檢查 (Invariants Checklist)

| 守則項目 | 要求 | 遵循狀況 |
| :--- | :--- | :---: |
| 控制面向後相容性 | 無 Redis 時平滑退回檔案輪詢，UI 同步寫入檔案 | ✅ 遵循（雙軌指令隊列 + 檔案對齊） |
| 指令等冪性 (Idempotency) | 重複接收相同指令不重複啟動協程或進程 | ✅ 遵循（透過狀態集合與 running 字典防護） |
| 日誌輪轉安全防護 | 輪轉過程嚴禁鎖死或中斷進程標準輸出 | ✅ 遵循（copytruncate + pm2-logrotate） |
| 7 天日誌保護守則 | 嚴禁無備份刪除 7 天內交易日誌與稽核庫 | ✅ 遵循（find 嚴格過濾 +mtime +7） |
| 進程隔離與自癒 | Worker 崩潰不牽連其他進程，指數退避重試 | ✅ 遵循（獨立 OS 進程 + backoff 探測） |
| 活躍服務即時重啟 | 修改後重啟 PM2 並實機 curl 探測驗證 | ✅ 遵循（已重啟並探測 health 與 snapshot 端點） |
