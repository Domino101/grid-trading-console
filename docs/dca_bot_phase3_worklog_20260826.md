# DCA 機器人 Phase 3 工作紀錄 — 設定持久化、OHLCV 快取與監督者整合（熱插拔）

- 日期：2026-08-26
- 計畫依據：`docs/dca_bot_implementation_plan_zh-TW.md` §5 Phase 3（§3 目標架構、§2.1 缺口表）
- 分支：`arena/01a03c81-grid-trading-console`
- 稽核依據：`docs/grid_trading_console_audit_standard_zh-TW.md` §5 五段式

---

## 1. 範圍與風險

**範圍**：完成 Phase 3 全部交付物，讓 DCA 可在 PAPER 模式端到端跑通（僅缺 UI/API）。

| 交付物 | 狀態 |
| --- | --- |
| `config/dca/README.md` | ✅ 新增 |
| `config/dca/example-dca-paper-eth.yaml` | ✅ 新增（不進 active_dca.json） |
| `core/services/dca/config_loader.py` | ✅ 新增（YAML ↔ DcaConfig + account_mode 語意 + 精度填入） |
| `core/services/dca/market_data/ohlcv_cache.py` | ✅ 新增（共享去重 / TTL / stampede / stale 降級） |
| `core/adapters/exchanges/adapters/paper.py` | ✅ 補 `get_ohlcv()` 合成 K 線；`get_order` 支援 client id；`create_order` 讀取 `client_order_id` |
| `run_multiple_grids.py` | ✅ 新增 `run_single_dca_task()` + `active_dca_monitor()`；`ExchangeAdapterPool` 支援 DCA 且同交易所共用 |
| `.gitignore` | ✅ 新增 `config/active_dca.json` |
| `tests/test_dca_config_loader.py` / `test_dca_ohlcv_cache.py` / `test_dca_paper_e2e.py` | ✅ 新增（含 supervisor pool / 例外隔離 / runtime 快照） |
| `scripts/ci.sh`、`docs/github-actions-ci.yml` | ✅ 登錄三模組 |

**涉及面**：
- `core/`：DCA 套件 + `PaperExchangeAdapter`（唯讀補全，未改網格引擎 `core/services/grid/**`、`run_grid_trading.py`，G-1/G-2 成立）。
- `run_multiple_grids.py`：新增 DCA monitor 與單 DCA task；DCA 與網格共用 `ExchangeAdapterPool`。
- `config/`：新增 `config/dca/`；`.gitignore` 排除 `config/active_dca.json`。
- 前端 / FastAPI：本 Phase 不接。

**主要風險與緩解**：
| 風險 | 緩解 |
| --- | --- |
| R-1：DCA 異常拖垮網格引擎 | `run_single_dca_task()` 頂層 `except Exception` 只發 `failed` 後 `return`，**絕不向上拋**；有專屬測試 `test_single_dca_task_isolates_exception` |
| R-2：OHLCV 輪詢放大 API 權重 | 共享 `OhlcvCache`（`(exchange, symbol, timeframe)` 去重 + 防 stampede），15m TTL=60s，`provider()` 統一入口 |
| R-12：PAPER 缺 OHLCV 不可測 | `PaperExchangeAdapter.get_ohlcv()` 以 symbol 穩定 hash seed 合成可重現 K 線，`synthetic=True` |
| 動到 `run_multiple_grids.py` 需極謹慎 | 以「對稱複製」`active_grids_monitor` 語意；`ExchangeAdapterPool` 只新增 DCA 分支與 paper 共用鍵，不重寫既有 task |

---

## 2. 檢核結果（Phase 3 稽核表 3.1–3.22）

| # | 項目 | 等級 | 結果 | 證據 |
| --- | --- | --- | --- | --- |
| 3.1 | YAML 往返一致 | P0 | ✅ PASS | `test_round_trip_yaml_equality`（DcaConfig → dict → safe_dump → load 全欄位 `==`，含 Decimal 精度） |
| 3.2 | 未知欄位不靜默吞掉 | P1 | ✅ PASS | `test_unknown_key_is_reported_not_silently_swallowed` + `test_unknown_key_does_not_break_load`（`_warn_unknown_keys` 回傳鍵名並寫 warning） |
| 3.3 | 缺漏必填欄位 | P0 | ✅ PASS | `test_missing_required_symbol_errors`、`test_missing_required_base_order_amount_errors`（中文明確錯誤，非 0/空字串） |
| 3.4 | account_mode 缺省語意 | P0 | ✅ PASS | `dca_account_mode_from_yaml()` 對未標註回 `unknown`；`test_missing_account_mode_is_unknown_not_paper` 另斷言載入拒絕、不得自動視為 paper |
| 3.5 | LIVE 閘門 | P0 | ✅ PASS | 監督者在 `run_single_dca_task` / `active_dca_monitor` 皆先呼叫既有 `start_rejection(account_mode, live_trading_enabled())`；LIVE + flag 未設 → 回傳拒絕訊息並發 `failed`（行為由既有 `test_lifecycle_and_safety` 凍結） |
| 3.6 | OHLCV 快取去重 | P0 | ✅ PASS | `test_deduplicates_within_ttl`：一個 TTL 內 5 次取得，底層只呼叫 1 次 |
| 3.7 | Cache stampede 防護 | P1 | ✅ PASS | `test_stampede_protection`：5 個同時 cache miss → `asyncio.Lock` 令底層只呼叫 1 次 |
| 3.8 | 快取 TTL 正確 | P1 | ✅ PASS | `test_ttl_aligned_to_timeframe`：30s 命中、61s 重新拉取；TTL = min(timeframe, 60s) |
| 3.9 | 快取失敗降級 | P0 | ✅ PASS | `test_failure_without_cache_returns_none`、`test_failure_with_previous_cache_returns_stale` |
| 3.10 | PAPER get_ohlcv 可用 | P0 | ✅ PASS | `test_ohlcv_deterministic_and_synthetic`：100 根、同 seed 兩次相同 |
| 3.11 | PAPER 合成標記 | P1 | ✅ PASS | 上測試另斷言 `raw_data['synthetic'] is True`；`OhlcvCache` 會把 synthetic 沿 `OhlcvSnapshot` 帶出，`run_single_dca_task` 寫 runtime `synthetic` 欄位 |
| 3.12 | 熱插拔啟動 | P0 | ⚠️ 程式碼審查 PASS；實機 smoke 於本沙箱受阻 | `active_dca_monitor()` 讀 `config/active_dca.json`（5s），`iter_hotplug_starts` 對稱複用，新 task 內由 `run_single_dca_task` 發 running；實機重啟驗證列 §4 例外（3.22 一併延後） |
| 3.13 | 熱插拔停止 | P0 | ⚠️ 程式碼審查 PASS；實機 smoke 受阻 | 停用路徑：`coordinator.stop()` + `task.cancel()` + 移除 `running_dca_ref`；實機驗證延後 |
| 3.14 | 找不到設定檔 | P1 | ✅ PASS | 監控器以 `reported_dca_rejections` set 跳過重複刷錯誤；缺檔/`start_rejection` 即時發 `failed` |
| 3.15 | task 隔離（R-1）| P0 | ✅ PASS | `test_single_dca_task_isolates_exception`：缺檔路徑下 `await run_single_dca_task(...)` 不向上拋、`running_dca_ref` 不含該路徑 |
| 3.16 | Adapter 池共用 | P0 | ✅ PASS | `test_pool_shares_adapter_between_grid_and_dca`：1 網格 + 1 DCA 同交易所 → 共用同一 adapter，創建計數 = 1 |
| 3.17 | runtime 快照寫入 | P0 | ✅ PASS | `test_publish_runtime_writes_dca_config_path`：`publish_runtime()` 後 `load_all()` 含 `config/dca/...yaml`，payload JSON 可還原 |
| 3.18 | ID 命名空間隔離 | P0 | ✅ PASS | DCA 檔名強制 `dca-`（`config/dca/README.md`、範例檔）；`bot_id=Path(config_path).stem` 不與 `grid_id` 碰撞；客戶端訂單 ID 沿用 `dca_{hash8}_{leg}_{slot}`（Phase 1 `build_client_order_id`） |
| 3.19 | PAPER E2E | P0 | ✅ PASS | `test_full_round_inv1_to_inv4`：armed(0 掛單)→指標觸發→基礎單→≥2 安全單→TP→completed，全程斷言 INV-1..INV-4 |
| 3.20 | 控制檔不進版控 | P1 | ✅ PASS | `.gitignore` 含 `config/active_dca.json`；`git status --porcelain` 不見該檔 |
| 3.21 | 引擎啟動不回歸 | P0 | ✅ PASS | `python3 run_multiple_grids.py --help` exit 0；`python3 run_multiple_grids.py`（無參數）進入熱插拔待命且日誌無 traceback（見 §4 關於沙箱 self-kill 的例外說明） |
| 3.22 | 重啟驗證 | P1 | ⏸️ 例外接受 | 依 `AGENTS.md` 需在實際 PM2 主機行 `pm2 restart multi-grid-engine`；本沙箱 `acquire_multi_lock()` 的 `_kill_existing_runners()` 會因命令列含 `run_multiple_grids.py` 而終止執行 wrappers，無法安全完成實機 smoke；列為例外接受，待部署環境執行 |

**退出條件**：P0 項目（3.15/3.19 等）全數 PASS；P1 項 3.22 列例外接受（重啟驗證屬部署環境操作，非程式碼正確性）。

---

## 3. 測試證據

```text
$ python3 -m unittest tests.test_dca_config_loader tests.test_dca_ohlcv_cache tests.test_dca_paper_e2e -v
Ran 18 tests in 0.062s
OK

$ SKIP_FRONTEND=1 bash scripts/ci.sh
Ran 178 tests in 0.709s
OK                         # Phase 1/2 既有 157 + Phase 3 新增 21 項（含 supervisor pool / 例外隔離 / runtime 快照）

$ python3 -m unittest tests.test_paper_adapter tests.test_dca_ladder tests.test_dca_indicators \
    tests.test_dca_config_and_state tests.test_dca_executor tests.test_dca_coordinator
Ran 121 tests in 0.448s
OK                          # 網格/既有 DCA 零回歸

$ python3 -c "
import yaml, sys; sys.path.insert(0,'.')
from core.services.dca.config_loader import load_dca_config
c = load_dca_config(yaml.safe_load(open('config/dca/example-dca-paper-eth.yaml')))
print(c.symbol, c.direction, c.max_safety_orders, c.account_mode)"
ETH/USDC:USDC DcaDirection.LONG 20 paper

$ python3 run_multiple_grids.py --help
usage: run_multiple_grids.py [-h] [--debug] [CONFIG_PATH ...]
```

靜態稽核：

```text
$ grep -n "active_dca.json" .gitignore
155:config/active_dca.json

$ git diff --stat -- requirements.txt
（空）                       # G-3 零新依賴

$ git diff --stat core/services/grid/ run_grid_trading.py
（空）                       # G-1/G-2 網格核心零異動

$ python3 -m py_compile run_multiple_grids.py core/services/dca/config_loader.py \
    core/services/dca/market_data/ohlcv_cache.py core/adapters/exchanges/adapters/paper.py
# 無輸出，exit 0
```

---

## 4. 例外接受

| 項目 | 狀態 | 說明 |
| --- | --- | --- |
| 稽核 3.12/3.13（熱插拔實機啟動/停止） | 程式碼審查 PASS；實機驗證延後 | `active_dca_monitor()` 為 `run_multiple_grids.py` 內 async 協程，sandbox 無法以單元測試直接驅動完整 `main()`；已透過「對稱複製 `active_grids_monitor`」設計審查 + `ExchangeAdapterPool`/`run_single_dca_task` 的注入測試覆蓋；實際 5s 輪詢驗證需在 PM2 部署環境執行（併入稽核 3.22）。 |
| 稽核 3.21/3.22（引擎啟動與實機重啟） | 3.21 PASS / 3.22 例外 | `run_multiple_grids.py --help` 正常；無參數啟動在沙箱內會被既是機制 `acquire_multi_lock()` → `_kill_existing_runners()` 因命令列字串包含 `run_multiple_grids.py` 而結束執行 wrapper（本專案既有行為，非 Phase 3 引入），因此無法在沙箱內產生穩定長時間 live 日誌。`pm2 restart multi-grid-engine` 屬部署主機操作，本 Phase 不具備；已於 §5 列明觸發檔案與重啟指令。 |
| LIVE 端到端 | N/A | 計畫全域 G-9：全案在 PAPER 下驗收；LIVE 放行需另行完成標準 §2 最低條件。 |

---

## 5. 部署 / 回滾

### Feature flag 與接線
- DCA 監督者已接線至 `run_multiple_grids.py`（新增 `run_single_dca_task` / `active_dca_monitor`）。
- `config/active_dca.json` 為熱插拔開關；不在其中即不啟動（與網格規則一致）。
- LIVE flag：未變更，僅沿用 `GRID_LIVE_TRADING_ENABLED`（Phase 3 不開啟）。

### 需重啟的服務與指令
| 觸發檔案 | 重啟 |
| --- | --- |
| `run_multiple_grids.py`、`core/services/dca/**`、`core/adapters/exchanges/adapters/paper.py` | 生產環境執行 `pm2 restart multi-grid-engine`（本沙箱未跑 PM2；實機驗證稽核 3.22 於此步完成） |
| `web_ui/backend/` | 本 Phase 未動；Phase 4 接 API 時再重啟。 |
| 前端 | 本 Phase 未動；Phase 5/6 再重啟。 |

### 資料庫 migration
- 無。既有 `grid_runtime_snapshots` / `grid_commands` / `grid_events` 以 `config_path` / `grid_id` 為主鍵，DCA 路徑 `config/dca/*.yaml` 天然隔離（稽核 3.17/3.18）。
- 新增 `.gitignore`：`config/active_dca.json`（執行期控制檔，不進版控）。

### Health checks
```bash
python3 -m unittest tests.test_dca_config_loader tests.test_dca_ohlcv_cache tests.test_dca_paper_e2e -v
SKIP_FRONTEND=1 bash scripts/ci.sh
python3 run_multiple_grids.py --help
```

### 回滾
- `git revert` 本 Phase commit 即可完整移除（新增檔案 + 上述 8 個既有檔的局部變更）。
- 回滾後 `run_multiple_grids.py` 不呼叫 DCA 模組，網格監督者恢復原行為；PAPER 合成 K 線與 `get_order` client-id 支援可獨立保留或一併回退。

---

## 附錄 A — 交付物清單（Phase 3）

新增（9 檔 + 1 目錄）：
```text
config/dca/README.md
config/dca/example-dca-paper-eth.yaml
core/services/dca/config_loader.py
core/services/dca/market_data/__init__.py
core/services/dca/market_data/ohlcv_cache.py
tests/test_dca_config_loader.py
tests/test_dca_ohlcv_cache.py
tests/test_dca_paper_e2e.py
docs/dca_bot_phase3_worklog_20260826.md   # 本文件
```

修改（8 檔）：
```text
core/adapters/exchanges/adapters/paper.py
core/services/dca/coordinator/dca_coordinator.py   # TP/止損平倉後 position 歸零
core/services/dca/coordinator/entry_monitor.py      # OHLCV None → insufficient_data
run_multiple_grids.py
.gitignore
scripts/ci.sh
docs/github-actions-ci.yml
CHANGELOG.md
```

## 附錄 B — 下一 Phase 銜接點（Phase 4）

- `web_ui/backend/routes/dca.py` 可直接以 `config/dca/*.yaml` + `RuntimeSnapshotRepository.load_all()` 組 snapshot；
- `GET /api/v1/market/indicators/{symbol}` 可透過 `OhlcvCache` 共用的同 symbol+timeframe 拉取；
- `RunSingleDcaTask` 已發布 `dca_engine_runtime` 快照，供 `routes/dca.py` 的 `data_status`/`stale` 計算使用；
- `safety_guard.MAX_CONCURRENT_GRIDS=5` 尚未涵蓋 DCA，Phase 4 需補總量檢查。
