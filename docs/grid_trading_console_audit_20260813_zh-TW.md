# 網格交易總控台大改版稽核報告與修改計畫

> 稽核日期：2026-08-13  
> 對照文件：`docs/grid_trading_console_upgrade_plan_zh-TW.md`、`docs/grid_trading_console_audit_standard_zh-TW.md`、`CHANGELOG.md`  
> 範圍：`web_ui/`、`run_multiple_grids.py`、`run_grid_trading.py`、`core/services/grid/`、`tests/`、部署腳本  
> 模式：PAPER 標籤存在，但引擎沒有模擬盤實作；任何「啟動」在有金鑰時都會打真實交易所  
> 結論：**未達 LIVE 放行門檻；PAPER 也不可作為安全驗收環境。先修 P0，再談畫面與 AI。**

---

## 1. 範圍與風險摘要

本輪大改版目標是把 Web UI 從「YAML 編輯器 + 進程清單」升級成可操作的多策略總控台：單一 snapshot、命令狀態機、SQLite 持久化、三步建立流程、策略詳情與 systemd 部署。

對照升級計畫，**讀模型與前端骨架已落地約 40–50%**；對照稽核標準，**資金安全與資料真實性仍有阻斷項**。最大風險不是畫面不夠像演示圖，而是：

1. UI 上的 PAPER 不會阻止實盤下單。
2. UI 建立的新策略，執行引擎預設不會接手。
3. Dashboard 宣稱的 heartbeat / freshness，在正式部署路徑上幾乎收不到引擎資料。
4. 舊版無認證 API 仍可啟動進程、讀取 `config/`。

**本輪不得開啟 `GRID_LIVE_TRADING_ENABLED`，也不得把 PAPER 當成隔離資金的沙盒。**

> 實作狀態（同日）：Phase A + B 已落地。PAPER start 409、舊 API 需認證、引擎直寫 SQLite、熱插拔接受新 YAML、DELETE 守門。Phase C（真模擬盤）尚未開始。

---

## 2. 本輪實際交付（通過項）

這些是真的有做、而且方向正確的部分：

| 項目 | 證據 | 評語 |
|---|---|---|
| 同源相對 API + Vite proxy | `web_ui/frontend/src/services/api.ts`、`vite.config.ts` | 遠端 preview 不再打瀏覽器 localhost。符合 Phase 0。 |
| 批次 snapshot | `GET /api/v1/dashboard/snapshot`、`Dashboard.tsx` 15s 單請求 | N+1 stats 輪詢已從總覽移除。 |
| 未知值不以 0 偽裝 | snapshot 金額欄可為 `null`，UI 顯示 `—` | 總覽符合資料語意原則。 |
| 命令 journal + 冪等 | `command_repository.py`、`Idempotency-Key` | start/stop 有 accepted → pending_engine → running/stopped/failed/timed_out。 |
| SQLite runtime / history | `runtime_repository.py`、7 天歷史 | API 重啟可恢復最後快照。 |
| `GRID_LIFECYCLE` 訊號 | `run_multiple_grids.py` print JSON | 引擎側有機器可讀 running/stopped/failed。 |
| LIVE feature flag | `GRID_LIVE_TRADING_ENABLED` 預設 false | 建立 LIVE YAML 有後端閘門。 |
| 三步建立表單 + CONFIRM LIVE | `GridForm.tsx` | UX 方向正確；後端仍是唯一驗證者。 |
| `/healthz` + systemd 模板 | `main.py`、`scripts/deployment/crypto-webui.service` | 部署面有起點。 |
| GridDetail lazy load | `App.tsx` | 圖表不進首屏 bundle。 |
| 前端刪除鈕在 running/starting 時 disabled | `Dashboard.tsx` | UI 有防護，但後端沒有對齊。 |

---

## 3. 稽核檢核表

標記：`PASS` / `FAIL` / `PARTIAL` / `N/A`。

### A. 資料正確性與時效

| 檢核 | 結果 | 證據 |
|---|---|---|
| PNL/餘額有 source 與 as_of | PARTIAL | snapshot 有 `last_updated_at`，無 `source`；stats/summary 沒有對齊。 |
| live / stale / unavailable 清楚區分 | PARTIAL | 總覽有；詳情頁用舊 stats，未知值回 0。 |
| KPI 與卡片同一讀模型 | PARTIAL | Dashboard 用 snapshot；`/grids`、`/summary`、`/stats` 仍把 `active_grids.json` 當 running，並用 0 補洞。 |
| LIVE / PAPER 不得混算 | FAIL | aggregate 是單一加總；沒有 `account_mode` 分帳。更嚴重：PAPER 標籤與真實下單無關。 |
| 金額用 Decimal / 字串傳遞 | FAIL | schema 與前端皆為 JS/Python `float`。 |
| stale 時禁止顯示「正常運行」 | PARTIAL | 總覽要求 heartbeat 才標 running；系統健康與 list API 仍看 desired state。 |
| 斷線保留最後有效資料並標時間 | PARTIAL | SQLite 可恢復；但新部署幾乎寫不進資料。 |

### B. 指令、訂單與生命週期

| 檢核 | 結果 | 證據 |
|---|---|---|
| 明確 transition | PARTIAL | start/stop 有命令狀態；create/delete 沒有進入同一狀態機。 |
| YAML / active state / 引擎啟動不可混為成功 | FAIL | `create_grid(auto_start=true)` 寫 `active_grids.json` 即回 `created_and_started`，且不建 command。 |
| stop 是否撤單/平倉語意清楚 | FAIL | UI 只有「停止」；後端呼叫 coordinator.stop()，未拆 pause / cancel / close。 |
| command_id + idempotency + actor/reason | PARTIAL | start/stop 有；delete/create 沒有 idempotency。冪等索引是 `(action, key)` 而非 `(grid_id, action, key)`。 |
| 重送不重複下單 | FAIL | 熱插拔對**新 YAML** 無效，見 P0-2；舊路徑則可能雙重啟動（PM2 單網格 + multi-grid 同時在 `ecosystem.config.js`）。 |
| 刪除僅允許已停止且無未完成命令 | FAIL | 後端 DELETE 可對 running 策略先改 false 再立刻歸檔 YAML。 |
| UI 只在引擎確認後標 running | PARTIAL | 總覽意圖正確；資料平面收不到 heartbeat 時會永遠 starting，或誤用殘留 stats。 |

### C. 風控與權限

| 檢核 | 結果 | 證據 |
|---|---|---|
| Live 預設關閉 | PASS | feature flag 預設 false。 |
| PAPER 獨立驗收 | FAIL | 引擎不讀 `account_mode`，沒有 paper adapter。 |
| 下單前後端驗證精度 / min notional | PARTIAL | LIVE 建立時有 market rule；PAPER 與 start 路徑沒有；`check_min_investment` 例外時回 True。 |
| 高風險操作 typed confirmation | PARTIAL | 建立 LIVE 有 `CONFIRM LIVE`；停止/刪除仍是 `window.confirm`。 |
| CORS 不得 `*` + credentials | FAIL | `main.py` 仍是 `allow_origins=["*"]` + `allow_credentials=True`。 |
| Token 不進 log / URL；短效安全機制 | FAIL | ephemeral token 完整寫入 log；前端放 localStorage；無登入畫面。 |
| RBAC viewer / operator / admin | FAIL | 單一共享 Bearer。 |
| 稽核不可覆寫、敏感資訊遮罩 | FAIL | audit 寫入完整 params；交易所設定檔含金鑰形欄位。 |

### D. API 與狀態儲存

| 檢核 | 結果 | 證據 |
|---|---|---|
| Dashboard 單一 batch | PASS | 總覽已改 snapshot。 |
| 每個 read endpoint 有 schema 與 null 行為 | PARTIAL | snapshot 有；legacy stats 仍回 0。 |
| 狀態不只存在 API 記憶體 | PARTIAL | 有 SQLite；寫入仰賴 stdout 解析，且新 UI 不會啟動 tailer。 |
| config / desired / command 原子性 | FAIL | `active_grids.json` 直接 overwrite；create auto_start 半成功只記 warning。 |
| 外部呼叫 timeout / rate limit / 僅冪等重試 | PARTIAL | SafetyGuard 有寫操作 60s/3 次；ccxt `load_markets` 無明確 timeout。 |
| API 不回傳 secret | FAIL | 未認證 `GET /api/config/{path}` 可讀 `config/exchanges/*`。 |

### E. 前端品質、效能與可用性

| 檢核 | 結果 | 證據 |
|---|---|---|
| 同源相對 URL | PASS | `/api` + Vite proxy。 |
| 請求可取消、不重疊、hidden 降頻、backoff | PARTIAL | 有 in-flight 與 hidden skip；無 AbortController、無 backoff。 |
| loading / empty / error / stale 可區分 | PARTIAL | 總覽有；詳情弱；無權限態沒有（401 只變「無法讀取」）。 |
| 每卡獨立 action state | PASS | `actionGridId`。 |
| 鍵盤 / ARIA / 對比 | PARTIAL | 有部分 aria；側欄仍大量 inline style；危險狀態仍偏顏色。 |
| RWD 無水平溢出 | PARTIAL | 有 900/640 breakpoint；側欄固定 260px，手機會擠。 |
| 長清單分頁 / 圖表區間載入 | FAIL | 掃描 `config/grid/*.yaml`（目前 41 份）一次全畫出。 |

### F. 可觀測性、部署與恢復

| 檢核 | 結果 | 證據 |
|---|---|---|
| 健康檢查涵蓋 API / store / engine / connector | PARTIAL | `/healthz` 只探 SQLite；`/system/health` 把 desired running 當真實 running。 |
| structured logs + correlation id + metrics | FAIL | 無 Prometheus；無 command success rate。 |
| systemd restart / 日誌輪替 / artifact version | PARTIAL | 有 unit 模板；`start_web_ui.sh` 仍用 nohup；PM2 設定寫死 `/root/...`。 |
| 回滾不遺失 command/audit | PARTIAL | 檔案在 `data/`；無遷移/備份演練文件落地。 |
| 斷線 / 限流演練 | FAIL | 無自動化，無記錄。 |

### G. 測試與驗收證據

| 檢核 | 結果 | 證據 |
|---|---|---|
| Unit：schema / transition / repository | PARTIAL | repo 測試通過；無 transition 表測試。 |
| Contract：OpenAPI ↔ 前端型別 | FAIL | 手寫 TS interface；無 codegen。 |
| Integration：API → engine → snapshot | FAIL | E2E 只操作 repository，不啟動引擎、不下單、不打 HTTP。 |
| PAPER E2E create→start→heartbeat→stop | FAIL | 名稱是 e2e，實際是記憶體/SQLite 腳本。 |
| Regression：並發 start、restart、stale、401 | FAIL | 無。 |
| CI lint/build/pytest | FAIL | 無 `.github/workflows`。本環境：repo 測試 4 過；snapshot 2 ERROR（未裝 FastAPI 卻沒 skip）；HTTP contract 3 skip。 |

### 量化目標

| 指標 | 現況 | LIVE 門檻 |
|---|---|---|
| 20 網格 refresh 請求數 | 總覽 1 次（達標） | 1 |
| 指令重複執行 | 熱插拔新檔不執行；舊路徑可能雙開 | 0 |
| stale 誤標 LIVE/running | list/summary 會；snapshot 較嚴 | 0 |
| PAPER 與實盤隔離 | 不存在 | 必須 |
| PAPER E2E | 未覆蓋真實路徑 | 100% |

---

## 4. 發現清單（依嚴重度）

### P0 — 阻斷，未修不得當正式操作台

#### P0-1　`account_mode: paper` 是標籤，不是模擬盤

- YAML 與 UI 可選 PAPER/LIVE，建立 LIVE 還有 feature flag。
- `GridConfig`、`create_grid_config()` **完全不讀** `account_mode`。
- 啟動後一律走真實 adapter；有 API 金鑰就會掛真實單。
- 使用者（或後續代理人）會以為 PAPER 已驗收，實際上在實盤下單。

**修法：** 在引擎落地真正的 paper 路徑之前，UI/API 必須把 PAPER 顯示成「未接模擬盤／僅存設定」，並拒絕 start；或實作 paper adapter / dry-run 並由 `create_grid_config` 強制讀取 mode。過渡期較安全的做法是：**禁止 start，只允許建立 YAML。**

#### P0-2　UI 新建策略不會被引擎熱插拔啟動

`run_multiple_grids.py` 的 monitor：

```text
is_enabled and path not in running_grids_ref and path in valid_paths
```

`valid_paths` 只在行程啟動時由 CLI / 內建五個預設檔決定。UI `POST /grids` 寫出的新 YAML **永遠不在 valid_paths**。  
結果：Dashboard 按啟動 → `active_grids.json=true` → 命令停在 `pending_engine` → 90 秒 `timed_out` → 交易所沒有新策略。

**修法：** monitor 應接受 `active_grids.json` 裡任何存在的 `config/grid/*.yaml`；新檔加入後動態載入。同時拿掉「啟動時把 CLI 清單強制寫成 enabled」的副作用。

#### P0-3　沒有可靠的引擎 → API 資料平面

設計文件寫「引擎是 source of truth，不要靠 log 算 PNL」。實作卻是：

- 引擎 `print([STATS_JSON])` / `print([GRID_LIFECYCLE])`
- `ProcessManager` 只在「自己拉起的進程」或「有人連上 `/ws/logs/{pid}` 才開始 tail PM2」時解析
- 新 Dashboard **不會**開 WS、也不會在 API 啟動時掛上 multi-grid log
- systemd 的 `crypto-webui` 與 `run_multiple_grids` 是兩個行程

正式路徑上 snapshot 會長期 `unavailable`，命令無法被確認。SQLite 只是把「偶爾刮到的 log」存下來。

**修法：** 引擎直接寫 `RuntimeSnapshotRepository`（或 localhost 內部 ingest API）。Log 解析最多當除錯備援，不能當唯一通道。

#### P0-4　舊 API 無認證，可讀設定、可啟動/停止進程

仍掛在 `main.py` 且無 `verify_token`：

- `GET /api/scripts`（且 `FileManager` 沒有 `get_scripts`，呼叫即炸）
- `GET /api/configs`、`GET /api/config/{path}`、`PUT /api/config/{path}`
- `GET /api/processes`、`POST /api/process/start`、`POST /api/process/stop/{pid}`
- `WS /ws/logs/{pid}`

`GET /api/config/config/exchanges/...` 可讀交易所設定。`PUT` 呼叫不存在的 `save_config`（實際方法是 `write_config`），寫入會 500，但讀取已足夠危險。

**修法：** 立即對這些端點加認證，或直接移除並讓前端只走 `/api/v1`。`file_manager` 路徑檢查改為 `realpath` + 目錄分隔符，禁止讀 `config/exchanges`。

#### P0-5　倉庫內有金鑰形欄位

`config/exchanges/` 中 backpack / hyperliquid / lighter / o1exchange 設定含非空的 `api_key` / `private_key` / `wallet_address`（binance 為空字串）。  
本報告不複製任何值。

**修法：** 視為已外洩輪替；改為環境變數或未追蹤的 secrets 檔；從 git 歷史移除；確認 `.gitignore`。Web UI 任何回應都不得回傳這些欄位。

#### P0-6　後端允許刪除仍在 desired-running 的策略

前端按鈕有擋，API 沒有：DELETE 先設 false、立刻從 active map 移除、清 snapshot、rename YAML。引擎若尚未停完，稽核與恢復會斷。

**修法：** DELETE 僅接受 `stopped` 且無 `pending_engine` 命令；否則 409。歸檔與清 runtime 必須等 `GRID_LIFECYCLE stopped`。

### P1 — 正式上線前必須修

1. **雙重讀模型**：`/grids`、`/stats`、`/summary`、`/system/health` 仍把 `active_grids.json=true` 當成 running，未知值填 0。GridDetail 走這條舊路，會把「尚未同步」畫成 $0。
2. **LIVE/PAPER KPI 混算**；41 份既有 YAML 全部進總覽，沒有「已註冊策略」與「磁碟歷史檔」的邊界。
3. **CORS `*` + credentials**；Bearer 放 localStorage；模組載入時把臨時 token 打進 log；前端沒有 token 輸入，未設 localStorage 時總覽永遠失敗。
4. **`create_grid(auto_start)` 不建 command**，卻回 `created_and_started`。schema 預設 `auto_start=true`（表單預設 false，Hermes/API 呼叫者仍會踩雷）。
5. **`ecosystem.config.js` 同時跑 `grid-sol-usdc` 與 `multi-grid-engine`**，同一份 SOL 配置可能雙開。路徑寫死 `/root/crypto-trading-open`。
6. **SQLite 每次操作新連線、無 WAL、無 timeout、無 busy_timeout**；command / runtime 共用一檔，audit 另檔，無備份策略。
7. **冪等鍵全域 `(action, key)`**，不同策略重用同一 key 會撞 unique index 或串錯命令。
8. **停止語意不清**：使用者不知道會不會撤單、會不會平倉。
9. **SafetyGuard**：建立草稿也要求引擎 PID 存活；`check_min_investment` 例外放行；`check_can_create_grid` 被 start 複用，語意錯誤。
10. **測試名實不符**：`test_paper_grid_lifecycle_e2e` 不碰 HTTP/引擎；`test_dashboard_snapshot` 在未裝 FastAPI 時 ERROR 而非 skip；無前端測試、無 CI。
11. **`start_web_ui.sh` 仍是 nohup**，與部署清單衝突。
12. **金額全程 float**，對帳門檻做不到 0 差異。

### P2 — 可在 PAPER 旗標後迭代

- 詳情沒有訂單簿 / 成交 / 持倉表，只有命令時間線與簡化曲線。
- 建立表單缺 follow / martingale / backpack / 止損止盈；預估資金用「前 10 格」經驗式。
- 無 SSE；無 AbortController；無系統健康 TopBar；側欄不 RWD。
- `App.tsx` 大量 inline style；Google Fonts 外連。
- audit `total` 只是本頁筆數，不是全表 count。
- README 仍是無關的下載落地頁，會誤導操作者。
- `.DS_Store` 進版控。

### P3

- 文案中英混用（Dashboard / Create Grid）。
- Vite 預設 favicon / hero 資產還在。
- `dashboard-enhancement` skill 仍寫 Flask/`web_ui/app.py`，與現況不符。

---

## 5. 根因：兩套控制平面沒接起來

```text
  瀏覽器 ──相對 /api──► FastAPI (snapshot / commands / YAML)
                              │
                              │ 只寫 active_grids.json
                              ▼
                     run_multiple_grids.py
                     valid_paths = 啟動當下的 CLI 清單
                              │
                              │ print STATS / LIFECYCLE
                              ▼
                     ？沒有保證的讀取者
                     ProcessManager 只在舊路徑或 WS tail 才解析
```

升級計畫要的是「引擎事件 → 持久化 read model → UI」。  
現況是「UI 寫 desired state，引擎未必執行；引擎寫 stdout，API 未必看見」。

這不是視覺問題，是控制回路斷開。

---

## 6. 修改計畫

原則：先讓「建立 / 啟動 / 看見真實狀態 / 停止」在**不誤導、不下錯單**的前提下閉環，再補畫面與 AI。  
PAPER 在引擎未實作前，**不得**再被畫成可啟動的安全模式。

### Phase A — 止血（0.5–1 天）必須先做

目標：任何人打開這個 UI，都不會以為 PAPER 是沙盒，也不會從舊 API 被遠端開單。

| # | 工作 | 檔案 | 驗收 |
|---|---|---|---|
| A1 | 認證或移除 `/api/scripts`、`/api/configs`、`/api/config`、`/api/process*`、未認證 WS | `main.py` | 無 token 一律 401 |
| A2 | `file_manager` 禁止讀寫 `config/exchanges`；路徑用 `realpath` + 前綴加 `os.sep` | `file_manager.py` | 讀交易所設定 403 |
| A3 | CORS 改環境變數白名單，生產禁止 `*`+credentials | `main.py` | 單測覆蓋 |
| A4 | 停止把 ephemeral token 寫進 log | `auth.py` | log 無 token |
| A5 | UI 明顯標示：「目前沒有模擬盤；PAPER 只是標籤；啟動即實盤」；PAPER 策略禁用 Start，直到 P0-1 完成 | `Dashboard.tsx` `GridForm.tsx` snapshot | 無法從 UI 啟動 paper 標籤策略 |
| A6 | 後端 PAPER start 回 409（在 paper adapter 完成前） | `routes/grids.py` | contract test |
| A7 | DELETE 僅允許 stopped + 無 pending 命令 | `routes/grids.py` | 409 + 測試 |
| A8 | 金鑰輪替與移出 git（獨立事故單，不在 UI PR 混做） | `config/exchanges/*` | 倉庫無非空密鑰 |

**放行：** 未完成 A 不得給第三人使用此 UI。

### Phase B — 接上控制回路（2–3 天）本輪真正該補的核心

目標：create → start → 引擎真的跑 → snapshot 看到非零/非假資料 → stop 被確認。

| # | 工作 | 說明 |
|---|---|---|
| B1 | 引擎直寫 runtime SQLite | `run_multiple_grids` 在 `get_statistics()` 後呼叫 `RuntimeSnapshotRepository.save`；lifecycle 呼叫 `GridCommandRepository.update_status`。不再依賴 Web UI 刮 log。 |
| B2 | 熱插拔讀全部 active yaml | 拿掉 `path in valid_paths`；檔案存在且 enabled 即可 `create_task`。新策略 5–10 秒內應出現 lifecycle running。 |
| B3 | 共享 adapter 按 exchange 建立 | 新策略若交易所與啟動時第一份 YAML 不同，不可硬套同一個 adapter。至少按 exchange_id 分池，未知交易所拒絕 start。 |
| B4 | create 與 start 分離 | `auto_start` 預設 false。若為 true，必須建立 start command，回 `pending_engine`，禁止 `created_and_started`。 |
| B5 | 單一 lifecycle 讀模型 | `/grids`、`/stats`、`/summary`、`/system/health`、GridDetail 全部改吃 snapshot/command 語意；未知值 null，desired state 不得叫 running。 |
| B6 | `active_grids.json` 原子寫入 | temp file + `os.replace`；寫入失敗回滾 command。 |
| B7 | 冪等鍵加上 grid_id | unique `(grid_id, action, idempotency_key)`。 |
| B8 | SQLite WAL + timeout | `PRAGMA journal_mode=WAL; busy_timeout=5000`；healthz 檢查可寫。 |

**驗收（本機、無實盤）：**

1. 建立 PAPER 標籤策略 → 只能存檔，不能 start（A5/A6）或走真正 paper adapter（若已做 C1）。
2. 用一份**不會下單**的假引擎 / fixture 驗證：start → SQLite 出現 snapshot → command=running → UI live。
3. 再開一份新 YAML（非啟動 CLI 清單內）→ monitor 真的啟動 task。
4. stop → lifecycle stopped → 才允許 delete。
5. 重啟 FastAPI → 上一筆 snapshot 仍在，且標 stale。

### Phase C — 讓 PAPER 名實相符（2–4 天，可與 B 部分重疊）

沒有這段，所有「PAPER E2E」都是假的。

| # | 工作 | 說明 |
|---|---|---|
| C1 | `GridConfig.account_mode` | `create_grid_config` 必讀，缺省 `paper`。 |
| C2 | PaperExchangeAdapter 或 engine dry-run | 吃行情（公開 ticker），模擬掛單/成交/持倉/PNL，**零私有 REST 下單**。 |
| C3 | LIVE start 雙閘門 | YAML `account_mode=live` **且** `GRID_LIVE_TRADING_ENABLED=true` **且** 市場規則通過，否則 403。 |
| C4 | Snapshot 分帳 | `aggregate_live` / `aggregate_paper`；總覽 KPI 不得混加。 |
| C5 | 金額契約 | 後端用 Decimal，JSON 用字串；前端只格式化。 |

**驗收：** PAPER start 在斷開 API 金鑰或使用只讀金鑰時仍能跑完 create→heartbeat→stop；LIVE 預設 403。

### Phase D — 前端工作台補完（2–3 天）

B/C 完成後再做，避免在假資料上雕畫面。

| # | 工作 |
|---|---|
| D1 | Token 登入頁（先維持 Bearer；不要再假設 localStorage 已有值）。401 與「尚未同步」分開。 |
| D2 | TopBar：engine PID、circuit breaker、最後同步、PAPER/LIVE 分帳徽章。 |
| D3 | 策略清單改「已註冊」而非掃描全部 41 個歷史 YAML；archived 永不進總覽。 |
| D4 | GridDetail 改吃 snapshot + history；補 pending orders / fills（有資料才顯示，沒有就「引擎尚未提供」）。 |
| D5 | 停止確認 Dialog：寫明「會取消掛單；預設不平倉」（對齊 coordinator.stop 真實行為）。 |
| D6 | 抽出 TopBar / KpiCard / GridCard / ConfirmDialog；拿掉 App inline style；側欄手機改 Drawer。 |
| D7 | AbortController + 指數退避；圖表維持 lazy。 |

### Phase E — 測試、部署、文件（1–2 天，與 D 平行）

| # | 工作 |
|---|---|
| E1 | snapshot / grids 測試在無 FastAPI 時 skip，有則必跑。 |
| E2 | 真 HTTP contract：未認證舊 API 401、PAPER start 行為、delete 409、snapshot null。 |
| E3 | 熱插拔單測：新 yaml 加入 active_grids 後 monitor 會排程（可用 fake task）。 |
| E4 | 前端 formatter / form validation 單測；之後再加 Playwright。 |
| E5 | `start_web_ui.sh` 改指向 systemd 或至少印出「非正式部署」。 |
| E6 | `ecosystem.config.js` 去掉與 multi-grid 重複的單幣進程；路徑參數化。 |
| E7 | 重寫 README 為真實專案說明；archive 假下載頁。 |
| E8 | 補最小 CI：unittest + `npm run lint` + `npm run build`。 |

### Phase F — 明確不做（本季）

- AI 市況分析自動寫入網格參數。
- Redis / 多實例。
- 完整 RBAC、HttpOnly session（可列下一季；A 階段先擋匿名）。
- 像素級複製演示圖。
- 在 PAPER adapter 完成前開放 LIVE。

---

## 7. 建議的下一個可提交工作包

不要一次做完 D/E 的視覺與 CI。下一個 PR 只做 **Phase A + B1/B2/B4/B5 的最小閉環**：

1. 鎖舊 API、CORS、token log。  
2. PAPER 標籤不可 start（或完成最小 dry-run 再開放）。  
3. 引擎直寫 SQLite；熱插拔接受新 YAML。  
4. create 不再謊稱 started；list/stats/detail 不再把 0 當真實餘額。  
5. DELETE 守門。  
6. 補能在無交易所環境跑的測試。

完成後再評估是否做 C（真 PAPER）或 D（畫面）。

預估：A+B 最小閉環 3–4 個工作日；C 另計 2–4 日。

---

## 8. 例外接受

| 項目 | 決定 | Owner | 到期 |
|---|---|---|---|
| AI 分析區 | 接受延後 | 產品 | 回路閉環之後 |
| HttpOnly session / RBAC | 接受暫緩，先擋匿名 + CORS 白名單 | 後端 | LIVE 放行前必須重開 |
| 金額改字串 Decimal | 接受暫緩到 Phase C | 後端 | LIVE 放行前 |
| Playwright E2E | 接受暫緩 | 前端 | PAPER adapter 之後 |

未列入上表的 P0/P1，**不接受帶病上線**。

---

## 9. 部署 / 回滾備註

- 現況 `start_web_ui.sh`（nohup）不可當正式流程。
- 回滾 UI 時保留 `data/web_ui_runtime.sqlite3` 與 `data/grid_audit.db`。
- 事故時先停策略，不要先刪 YAML / SQLite。
- 在 P0-5 處理完之前，視所有已提交金鑰為洩漏。

---

## 10. 一句話裁決

> 這個大改版把「看起來像總控台」做出來了，但控制回路是斷的，PAPER 是假的。  
> 下一步不是繼續做 AI 或更密的卡片，而是：鎖住舊 API、接上引擎狀態、讓熱插拔真的啟動新策略、在模擬盤存在之前禁止任何「安全模式」暗示。
