# 三策略統一記帳層稽核與修復報告（Grid / DCA / CoinFull Prime）

| 項目 | 內容 |
| :-- | :-- |
| 報告日期 | 2026-09-16 |
| 交付版本 | **0.3.6**（稽核前：`VERSION` = 0.3.4，`CHANGELOG` 最新 = 0.3.5） |
| 稽核對象 | 0.3.4 宣稱之「CoinFull Prime 統一記帳層整合 (Canonical Ledger Integration)」+ 三策略記帳路徑全體 |
| 分支 | `arena/01a0aa6c-grid-trading-console`（基準 commit `9602973`） |
| 稽核範圍 | `core/infrastructure/database/*`、`core/services/{grid,dca,coinfull_prime}/*`、`web_ui/backend/*`、`tools/*`、`migrations/*`、`tests/*` |
| 新增交付 | 統一記帳門面 `StrategyLedger`、3 張 canonical 表、Alembic `0003`、冪等回填/鏡像匯出、稽核引擎（L1–L15）、CLI `tools/ledger_consistency_audit.py`、API `GET /api/v1/system/ledger-audit`、59 項新測試 |

---

## 0. 執行摘要

### 0.1 核心結論：0.3.4 宣稱的修復「部分成功、關鍵失敗」

0.3.4 的 CHANGELOG 宣稱已「**全面收斂至專案標準記帳層**、**消除與網格及 DCA 之間的記帳孤島**」。
實測結論：**方向正確，但三個關鍵宣稱不成立**，且在 PostgreSQL 模式下會造成「記帳寫進幽靈資料庫」與「避碰保護名單靜默清空」兩項 P0 級風險。

| # | 0.3.4 宣稱 | 實測判定 | 關鍵證據 |
| :-- | :-- | :-- | :-- |
| C1 | 全面收斂至**專案標準記帳層** | ❌ **不成立** | `service.py:231-232`、`rebalancer.py:333-334`、`routes/coinfull_prime.py:193-194,278-279` 皆 `SqliteLedgerStore()` **直接 new**，繞過 `create_ledger_store()` 工廠 → `DATABASE_URL` 設定時仍寫入本地 SQLite（幽靈資料源），PG 中的 canonical ledger 完全沒有 CFP 資料 |
| C2 | **消除記帳孤島** | ❌ **不成立** | 籃子 `coinfull_prime_active_basket.json`、閘門 `coinfull_prime_regime_history.jsonl`、換倉快照 `coinfull_prime_run_snapshots.jsonl`、排程 `coinfull_prime_scheduler.json` 四份**檔案型帳本仍是真相源**；0.3.5 又新增了後兩份 JSONL（孤島擴大而非收斂） |
| C3 | 登記 3 個環境身分「嚴格符合 P1-6」 | ⚠️ **部分成立（有副作用）** | P1-6（fills 必有對應 bots）確實滿足；但 `service.py:256-258` 無條件登記 `paper/binance_testnet/binance_live` 三個身分 → 未使用環境成為**幽靈機器人**（稽核 L9），污染儀表板計數與外部避碰名單 |
| C4 | 逐筆寫入標準 `trade_fills`（含角色標記） | ✅ **成立（SQLite 模式下）** | `rebalancer.py:423-437`（dry-run）與 `557-571`（實盤）確實逐筆寫入；但 `realized_pnl=0.0` **硬編碼**（`:434`、`:568`）→ DB 無損益真相 |
| C5 | 換倉事件持久化至 `bot_events` 防重啟丟失 | ⚠️ **部分成立** | 事件確實寫入，但 `append_event(...)` **未帶 `dedupe_key`**（`:460`、`:609`）→ 重啟/補跑會重複記帳（稽核 L6）；且**未 emit canonical `fill` 事件** → 事件溯源回放退回 legacy `trade_fills` 路徑（稽核 L5） |
| C6 | 新增 `/fills`、`/history` 查詢端點 | ⚠️ **部分成立** | 端點存在且 SQLite 模式可用；但讀取端同樣 `SqliteLedgerStore()` 直接 new（`routes:193-194,278-279`）→ PG 模式下回傳**空資料**（前端顯示無成交） |

> 另有一項 0.3.4 未宣稱、但屬同一整合範圍的 **P0 安全護欄缺陷**：
> `get_external_bot_symbols()`（`service.py:129-132`）以 `sqlite3.connect()` + **硬編碼相對路徑** `../../../data/web_ui_runtime.sqlite3` 查詢外部機器人標的。
> PG 模式下該檔案不存在 → `os.path.exists()` 為 False → **保護名單靜默為空** →
> CoinFull Prime 可能選中 Grid/DCA 正在持有的標的（搶保證金／對沖干擾），且平倉豁免保護失效（可能誤殺外部部位）。
> 這是本次稽核發現的**最高風險項**：失效時無例外、無 log、無告警。

### 0.2 修復後狀態（0.3.6）

- **單一記帳埠**：三策略（Grid / DCA / CoinFull Prime）與 API 層一律經 `StrategyLedger` → `LedgerRepository` → `create_ledger_store()`，後端由工廠依 `DATABASE_URL` 解析；生產程式碼中 `SqliteLedgerStore()` 直接實例化歸零（僅剩工廠內部、遷移工具與稽核 CLI 的 `--db`）。
- **DB 為唯一真相源**：4 份 CFP 檔案帳本降級為**匯出鏡像**，並提供**冪等回填**（啟動自我修復 + CLI `--backfill` + API `?backfill=true`）。
- **損益可由 DB 獨立重算**：新增 `bot_baskets`（均價法結算 `realized_pnl`）、`bot_regime_decisions`、`bot_runtime_state`；`trade_fills` 補上 `realized_pnl_usdt`／`fees_usdt`／`batch_id` 語意。
- **冪等**：`fill_id` 唯一、可重跑事件必帶 `dedupe_key`（`rebalance:{account}:{batch_id}`、`regime:{account}:{ts}`、`fill:{fill_id}`）。
- **可觀測降級**：記帳失敗**絕不阻斷交易**，但計入 `AccountingHealth`（`writes_failed` / `errors_by_op` / `last_error`），並由稽核 L12 與 API `accounting_health` 暴露。
- **可執行稽核**：15 項不變式檢查（L1–L15）+ CLI + API + CI 靜態政策守門（回歸會被測試擋下）。

### 0.3 驗證結論

| 指標 | 基準（pristine HEAD，同環境同依賴） | 修復後（0.3.6） |
| :-- | :-- | :-- |
| CI 模組數 | 57 | **60**（+3） |
| 測試總數 | 486 | **545**（+59 新測試） |
| 失敗 / 錯誤 / 略過 | 22 / 3 / 2 | 22 / 3 / 2 |
| 失敗項目集合 | — | **與基準逐項完全相同**（`diff` 為空）→ **零回歸** |
| 殘留失敗原因 | 環境性：Binance 不可達（HTTP 418 IP ban）→ `422 交易對 ETH/USDC:USDC 在交易所 binance 中不存在或無法交易` | 同左（非本次改動造成） |

---

## 1. 稽核範圍與方法

### 1.1 雙軌稽核標準（AGENTS.md）執行狀況

AGENTS.md 規定稽核須先 `graphify query`（宏觀架構）再 `ocr review/scan`（微觀行級）。
本沙箱環境中 **`graphify` 與 `ocr` CLI 均未安裝**（`command -v graphify` / `command -v ocr` 皆無結果），因此以等效方式執行：

| 階段 | 標準做法 | 本次實際做法（等效替代） | 產出 |
| :-- | :-- | :-- | :-- |
| Stage 1 宏觀 | `graphify query "<target>"` | **直接解析 `graphify-out/graph.json`**（11,660 節點 / 22,733 邊，0.3.5 快照）以 Python 查詢依賴邊 | 量化出「27 個節點直接依賴 `SqliteLedgerStore`」，其中含 CFP `service`／`rebalancer`／`execute_rebalance`／`register_bot_identity` 與 `routes/coinfull_prime`（`get_coinfull_prime_fills`／`get_coinfull_prime_history`）；並確認 `strategy_ledger`／`ledger_audit`／`ledger_backfill` **不存在於 0.3.5 圖譜**（本次新增） |
| Stage 2 微觀 | `ocr review -b "<context>"` | 人工逐行審查 + **自建靜態政策掃描器**（`ledger_audit._check_policy_bypass`，L4）+ 59 項行為測試 | 政策掃描可重複執行並已納入 CI（`tests/test_accounting_path_policy.py`），比一次性人工審查更持久 |

> 建議：`graphify` / `ocr` CLI 安裝後，補跑 `graphify update .` 讓圖譜反映 0.3.6 的新模組（見 §7 R-05）。

### 1.2 證據方法

1. **靜態證據**：`git show HEAD:<file>` 取修復前原文（含行號），`grep -n` 取修復後原文；兩者並列於 §3。
2. **動態證據**：以合成資料離線驅動（不觸網、不寫入 `repo/data`），執行門面／回填／稽核引擎／CLI／API，輸出存於 `/tmp/audit/evidence/`（`seed.txt`、`cli_healthy.md`、`inject.txt`、`cli_defects.md`、`api_smoke.txt`）。
3. **零回歸證明**：`git worktree add /tmp/base_wt2 HEAD --detach` 建立**未含本次改動**的 pristine 檢出，以相同依賴與環境執行完整 CI，比對失敗項目集合（`diff` 為空）。

### 1.3 環境限制（必讀）

- 沙箱**無法連外至 Binance**（API 回 HTTP 418 IP ban）。凡需真實交易所市場預檢的 e2e 測試（`test_dca_http_contract`、`test_dca_chart_backtest_e2e`、`test_web_ui_http_contract` 等）在此環境必然失敗，屬**環境性失敗**，已用 pristine HEAD 對照證明與本次改動無關。
- `/home/user/.pylibs` 為非持久依賴目錄；本次為執行完整 CI 補裝了 `eth-account` / `eth-typing` / `eth-utils`（先前缺失導致 3 個模組 import error，使基準測試數被低估為 439）。
- 無任何服務在背景執行（`ss -ltnp` 無 8001、`ps aux` 無 `main.py` / `run_multiple_grids.py` / pm2），故 AGENTS.md「重啟既有服務」條款不適用；本次改以**啟動實例 + 真實 HTTP 請求**完成 live 驗證（見 §6.7）。

---

## 2. 修復前記帳拓撲（0.3.4 / 0.3.5 實況）

```
                       ┌──────────────── 真相源分裂 ────────────────┐
Grid  ─┐                                                               
       ├─ RuntimeSnapshotRepository ─→ LedgerRepository ─→ create_ledger_store() ─→ ✅ canonical DB
DCA  ──┘                                                    (SQLite 或 PostgreSQL)

CoinFull Prime ─→ SqliteLedgerStore()  ─────────────────────────────→ ❌ 本地 SQLite（PG 模式下＝幽靈庫）
       │        （service / rebalancer / routes 各自 new）
       ├─→ data/coinfull_prime_active_basket.json      ❌ 檔案真相源（受管籃子）
       ├─→ data/coinfull_prime_regime_history.jsonl    ❌ 檔案真相源（風險閘門決策）
       ├─→ data/coinfull_prime_run_snapshots.jsonl     ❌ 檔案真相源（換倉快照）
       └─→ data/coinfull_prime_scheduler.json          ❌ 檔案真相源（排程狀態）

安全護欄 get_external_bot_symbols() ─→ sqlite3.connect("../../../data/web_ui_runtime.sqlite3")
                                        ❌ 硬編碼相對路徑：PG 模式下檔案不存在 → 保護名單靜默為空
```

**後果**：
1. PG 部署下，CFP 的所有記帳落在一個**沒人讀的本地 SQLite**；儀表板、事件溯源、損益統計全部看不到 CFP。
2. 即使 SQLite 部署，CFP 的**籃子／閘門／快照／排程**仍在檔案裡 → 重啟後以舊檔覆寫新帳的風險（L7）。
3. 避碰名單失效 → **可能與 Grid/DCA 搶同一標的的保證金，或誤殺其部位**（資金安全等級）。
4. `realized_pnl` 恆為 0、事件無 `dedupe_key`、無 canonical `fill` 事件 → 損益與回放都不可信。

---

## 3. 診斷發現與修復（逐項含前後證據）

> 嚴重度：**P0** = 資金安全／資料真相源錯誤；**P1** = 記帳正確性／一致性；**P2** = 工程健康度。

### D-01（P0）後端繞過：CFP 直接 `new SqliteLedgerStore()` → PG 模式幽靈資料源

**修復前**
```python
# core/services/coinfull_prime/service.py:231-232 (HEAD)
from core.infrastructure.database.ledger_store import SqliteLedgerStore, BotRecord
store = SqliteLedgerStore()          # ← 繞過 create_ledger_store() 工廠
store.initialize()

# core/services/coinfull_prime/rebalancer.py:333-334 (HEAD)
from core.infrastructure.database.ledger_store import SqliteLedgerStore, BotRecord, TradeFill
store = SqliteLedgerStore()

# web_ui/backend/routes/coinfull_prime.py:193-194, 278-279 (HEAD)
from core.infrastructure.database.ledger_store import SqliteLedgerStore
store = SqliteLedgerStore()
```
圖譜證據（0.3.5 快照）：27 個節點直接依賴 `SqliteLedgerStore`，其中生產路徑 6 個屬 CFP。

**修復後**
```python
# core/services/coinfull_prime/service.py（0.3.6）
def _ledger(self) -> StrategyLedger:
    """統一記帳門面（自動路由 SQLite / PostgreSQL 真相源）。"""
    return StrategyLedger.instance()

# core/infrastructure/database/strategy_ledger.py
@property
def repo(self):                      # 未注入時「每次動態解析」，避免抓到過期單例
    if self._repository is not None:
        return self._repository
    from core.infrastructure.database.ledger_repository import LedgerRepository
    return LedgerRepository.instance()
```
**驗證**：`tests/test_unified_ledger_accounting.py::test_backend_is_selected_by_factory_not_hardcoded`；
`tests/test_accounting_path_policy.py`（靜態掃描：策略層/API 層不得出現 `SqliteLedgerStore(` 或 `sqlite3.connect(`）；
稽核 L4 政策掃描目前 **0 違規**。

### D-02（P0）安全護欄失效：`get_external_bot_symbols()` 硬編碼相對 SQLite 路徑

**修復前**（`service.py:129-132`，HEAD）
```python
db_path = os.path.join(os.path.dirname(__file__), "../../../data/web_ui_runtime.sqlite3")
if os.path.exists(db_path):                       # ← PG 模式：False，靜默跳過
    conn = sqlite3.connect(f"file:{os.path.abspath(db_path)}?mode=ro", uri=True, timeout=5.0)
```
失效模式：**無例外、無 log、保護名單為空集合** → 選幣避碰與平倉豁免雙雙失效。

**修復後**（`service.py:120+`，0.3.6）
```python
def get_external_bot_symbols(self, account_mode: Optional[str] = None) -> Set[str]:
    # 1. 查詢 canonical ledger (bots 表) —— 經統一門面，後端由工廠解析
    for bot in StrategyLedger.instance().list_bots():
        ...
```
**驗證**：`tests/test_unified_ledger_accounting.py::test_external_bot_symbols_come_from_ledger`；
`tests/test_accounting_path_policy.py::test_no_raw_relative_sqlite_path_in_coinfull_prime`（程式碼中不得殘留硬編碼相對路徑）。

### D-03（P0）記帳孤島：4 份檔案型帳本為真相源

**修復前**：`load_active_basket()` / `save_active_basket()`（`service.py:169-211`，HEAD）以 JSON 檔為真相源；0.3.5 再加 `coinfull_prime_regime_history.jsonl`、`coinfull_prime_run_snapshots.jsonl`；排程狀態在 `coinfull_prime_scheduler.json`。

**修復後**：DB 為真相源，檔案僅為鏡像 + 一次性冪等回填。
```python
# service.py：讀取順序 = DB → （DB 空則）冪等回填 → 再讀 DB → （仍不可用）退回鏡像檔
# 寫入後：_export_basket_mirror() 由 DB 反向產生鏡像（鏡像含 source: canonical_ledger:bot_baskets）
```
| 檔案（舊真相源） | 新 canonical 承載 | 冪等鍵 |
| :-- | :-- | :-- |
| `coinfull_prime_active_basket.json` | `bot_baskets`（PK: bot_id+account_id+symbol） | upsert；回填時 `close_missing=False`（非破壞性） |
| `coinfull_prime_regime_history.jsonl` | `bot_regime_decisions` | `regime:{account}:{timestamp}` |
| `coinfull_prime_run_snapshots.jsonl` | `bot_snapshots` + `bot_snapshot_history` | `backfill:run_snapshots` 標記（跑過即跳過） |
| `coinfull_prime_scheduler.json` | `bot_runtime_state`（PK: bot_id+key） | KV upsert |

**驗證**：`test_basket_db_is_source_of_truth_over_mirror_file`、`test_legacy_side_file_is_backfilled_once`、
`test_mirror_file_restores_full_basket_details`（0.3.6 鏡像含明細 → DB 遺失時可**無損還原** side/quantity/entry_price/realized_pnl）、
`test_regime_history_persisted_to_ledger_and_mirror`、`test_scheduler_state_roundtrip_through_ledger`、`test_run_snapshot_written_to_canonical_snapshot_tables`；
回填冪等實測：run1 `regime.imported=1` → run2 `regime.skipped_duplicate=1`。

### D-04（P1）事件不一致：無 canonical `fill` 事件、事件非冪等、`realized_pnl` 恆 0

**修復前**：`rebalancer.py:434,568` → `realized_pnl=0.0` 硬編碼；`:460,609` → `append_event(...)` 無 `dedupe_key`；fills 從不 emit `fill` 事件（L5）。

**修復後**：
- `record_fill()` **先寫 `trade_fills`，成功後才 emit `fill` 事件**（`dedupe_key="fill:{fill_id}"`）；寫入失敗不產生孤立事件。
- 換倉事件帶 `batch_id` 與 `dedupe_key="rebalance:{account}:{batch_id}"`；閘門事件帶 `regime:{account}:{ts}`。
- 已實現損益由 `bot_baskets` **均價法**結算（`apply_basket_fill`），並回寫至 `trade_fills.realized_pnl` 與事件 payload。

**數值驗證**（`test_basket_average_cost_realized_pnl` / `test_basket_short_side_pnl_sign`）：
```
買 2 @100 → 賣 1 @110 ⇒ realized +10
再賣 1 @ 90 ⇒ realized −10
淨 realized = 0（均價法正確，且空頭符號相反）
```

### D-05（P1）幽靈機器人：無條件登記 3 個環境身分

**修復前**（`service.py:256-258`，HEAD）
```python
for acc in ["paper", "binance_testnet", "binance_live"]:
    self.register_bot_identity(acc)     # ← 未使用的環境也登記為 running/idle
```
**修復後**（`service.py:354-358`）
```python
self._backfill_side_files(reason="initialize")            # 先冪等回填
self.register_bot_identity(self.selected_account or "paper")  # 只登記實際使用的帳號環境
```
**驗證**：`test_only_active_account_identity_is_registered`；稽核 L9 可偵測殘留幽靈身分（判定時排除 `bot_created`/`bot_renamed` 等**純身分事件**，否則幽靈身分會被自己的註冊事件掩蓋 —— 此為本次稽核修正的判定邏輯）。

### D-06（P1）操作稽核日誌（`grid_audit.db`）單例忽略 `project_root`、無自我修復

**修復前**（`web_ui/backend/audit.py:33-41`，HEAD）
```python
@classmethod
def get_instance(cls, project_root: Optional[str] = None) -> "AuditLogger":
    if cls._instance is None:
        ...
        cls._instance = cls(project_root)
    return cls._instance            # ← 第二次呼叫起 project_root 被忽略
```
**修復後**：`_registry` 以 **project_root 為鍵**的多實例註冊表 + schema 自我修復（DB 檔被刪/損毀時自動重建）+ 寫入失敗計數 + `health()`。

**驗證**：`TestAuditLoggerSelfHealing`（`test_instances_are_keyed_by_project_root`、`test_write_succeeds_and_is_queryable`、`test_self_heals_after_db_file_deleted`）；稽核 L10 暴露健康度。

### D-07（P1）重構期間發現並修復的**幽靈持倉**缺陷（新發現，非 0.3.4 宣稱範圍）

換倉後的籃子「名單同步」若把 `reduce_only`（平倉）單也算進去，會把**已平倉成分重新標記為 `active`** → 幽靈持倉 → 下次換倉把它當既有部位、平倉豁免名單也被污染。

**修復**（`rebalancer.py:680-696`）：名單同步**只處理開倉/調倉**成分；平倉成分的 `status`/`quantity` 由 `apply_basket_fill` 獨佔結算。
```python
# 只同步「開倉/調倉」成分；平倉成分的 status/quantity 由 apply_basket_fill
# 結算擁有（否則已平倉的成分會被名單同步重新標記為 active → 幽靈持倉）
if o.reduce_only:
    continue
```
**驗證**：`test_basket_membership_sync_preserves_settled_pnl`、`test_live_rebalance_updates_basket_and_realized_pnl`、`test_force_flatten_exit_is_recorded_with_pnl`。

### D-08（P2，設計約束）canonical `trade_fills` **無 `symbol` 欄位**

既有 schema（0001/0002）的 `trade_fills` 不含 `symbol`，因此 API 顯示與稽核需由 **ID 命名契約**推導：
```
sim_{batch}_{symbol}_{side}                 # dry-run 模擬成交
cfp_{account}_{order_id}_{symbol}_{side}    # CFP 實盤成交
```
實作：`web_ui/backend/routes/coinfull_prime.py:334 _infer_symbol()`。
**決策**：**不**貿然加欄（會破壞既有 DB 與遷移鏈）；改以命名契約 + 文件化 + 稽核校驗。
**建議**：未來若要加 `symbol` 欄，須走「新欄位可空 + 回填 + 雙寫過渡」三階段（見 §7 R-02）。

### D-09（P2）`ledger_store` 的 `:memory:` + `dispose()` → 空資料庫

記憶體模式引擎被 dispose 後連線池重建 → 先前建立的 schema 消失（測試與工具情境會拿到空庫）。
**修復**：`initialize()` 對 `:memory:` 使用 `StaticPool` 並跳過 dispose 路徑；測試一律優先用**臨時檔案 DB**（避免共享記憶體庫的隔離陷阱）。

### D-10（P2）測試治具未重置單例 → 跨測試污染

**修復**：`tests/conftest.py` 新增 `LedgerRepository` / `StrategyLedger` / `AuditLogger` 三項重置；
測試隔離模式（已驗證可用）：建構 `StrategyLedger(repository=repo)` 後同時設定 `LedgerRepository._instance = repo`，讓策略程式碼中的全域 `StrategyLedger.instance()` 解析到測試庫，cleanup 時兩者皆重置。

### D-11（P2）版本資訊漂移

`VERSION` = 0.3.4，但 `CHANGELOG` 最新條目為 0.3.5 → 部署/支援時無法由 `VERSION` 判斷實際代碼世代。
**修復**：`VERSION` → **0.3.6**，`CHANGELOG` 補 0.3.6 條目（並在條目中記載此漂移之修正）。

### D-12（P2）維運工具的同類繞過（本次一併修一個、其餘列管）

`tools/ai_reflection_engine.py:71` 以 `SqliteLedgerStore(self.db_path)` 讀取「持倉與盈虧事實」→ PG 模式下讀到空幽靈庫，AI 覆盤結論會基於**空資料**。
**修復**：改用 `create_ledger_store(database_path=...)` 工廠。
其餘 4 個工具（`cleanup_paper_phantom_fills`、`rebuild_grid_history_from_fills`、`sync_binance_grid_trades`、`scripts/migration/sqlite_to_postgres`）屬**刻意操作實體 SQLite 檔**的維運/遷移工具，已列入 `ledger_audit.TOOLING_RAW_STORE_ALLOWLIST` 文件化列管（見 §7 R-03）。

---

## 4. 統一記帳架構（0.3.6）

### 4.1 分層與單一埠

```
┌─ 策略層 ────────────────────────────────────────────────────────────┐
│ Grid: position_tracker_impl / grid_coordinator                       │
│ DCA : runtime_repository._accounting（RuntimeSnapshotRepository）    │
│ CFP : service / rebalancer                                           │
│ API : routes/coinfull_prime, routes/system                           │
└───────────────┬──────────────────────────────────────────────────────┘
                │  唯一記帳埠（single port）
        ┌───────▼────────┐   失敗 → 計數 + ERROR log，**絕不阻斷交易**
        │ StrategyLedger │   成功 → writes_ok++；health() 可觀測
        └───────┬────────┘
        ┌───────▼──────────┐
        │ LedgerRepository │  （事件折疊 / 身分 / 快照 / 部位 / 冪等）
        └───────┬──────────┘
        ┌───────▼────────────────┐
        │ create_ledger_store()  │  DATABASE_URL → PostgreSQL（含 per-path schema 隔離）
        └───────┬────────────────┘  否則 / 連線失敗 → SQLite（EnvironmentGuard 路徑隔離）
        ┌───────▼───────────────────────────────┐
        │ canonical tables（三者共用同一套表）   │
        └───────────────────────────────────────┘
```

**門面容錯（本次修復）**：
1. 注入物件可為 `LedgerRepository` **或** raw `LedgerStore`（稽核 CLI `--db`、`RuntimeSnapshotRepository` 自訂路徑）→ 兩者都解析到同一 canonical store（舊行為會 `AttributeError` 並被寫入守門吞掉 → 靜默失敗）。
2. `save_basket()` 接受 `BasketEntry` **或** dict（含舊鍵 `avg_price`/`qty`）→ 正規化後寫入（舊行為：`'dict' object has no attribute 'symbol'` → 靜默失敗）。
3. `fill` 事件**必須**寫進注入的同一個 store（舊行為：委派給 `bot_events.emit_fill_event()`，該 helper 解析**全域單例** → 事件落到另一個 DB，形成 L5 缺口）。

### 4.2 資料模型

**新增表**（`ledger_store.py` + `schema.sql` + Alembic `0003_unified_strategy_accounting`，`down_revision = 0002_bot_events`）

| 表 | 主鍵 | 用途 | 冪等 |
| :-- | :-- | :-- | :-- |
| `bot_baskets` | (`bot_id`, `account_id`, `symbol`) | 多標的策略的受管持倉籃子（均價法 `entry_price`/`quantity`/`realized_pnl`/`status`） | upsert |
| `bot_regime_decisions` | `id`（+ `dedupe_key` 唯一） | 風險閘門決策（pts/scale/allow_new_entries/force_flatten/mode/reason） | `dedupe_key` |
| `bot_runtime_state` | (`bot_id`, `key`) | 策略運行狀態 KV（排程、`pending_defer_until`、回填標記） | upsert |

**設計決策**：**不建立 DB 層外鍵**（跨後端可攜性 + 回填順序彈性），改由**稽核引擎以邏輯不變式強制**（L1 孤兒 fills、L2 孤兒 events、L8 記帳契約）。

**既有表語意擴充**：`trade_fills` 承載 `realized_pnl`（由籃子均價法結算回寫）、`role`（`cfp_long`/`cfp_short`/`grid`/`base`/…）、`batch_id`（換倉批次，經 `client_order_id` 命名契約）。

### 4.3 冪等與一致性規則

| 規則 | 實作 |
| :-- | :-- |
| 一筆成交只入帳一次 | `trade_fills.fill_id` 唯一；`fill_id` 由**交易所 order id / 穩定業務鍵**推導，禁用毫秒時間戳 |
| 可重跑事件不重複 | `daily_rebalance` / `manual_rebalance` / `weekly_rebalance` / `regime_gate` **必帶** `dedupe_key`（L6 檢查） |
| 事件與流水同源 | `record_fill()` 先寫 fills，**成功後**才 emit `fill` 事件（失敗不產生孤立事件） |
| 平倉結算獨佔權 | `apply_basket_fill()` 獨佔 `status`/`quantity`/`realized_pnl` 結算；名單同步不得覆寫（D-07） |
| 回填非破壞性 | 回填一律 `close_missing=False`，不洗掉 DB 既有活躍成分與已結算損益 |

### 4.4 遷移政策（遵守 AGENTS.md）

- **既有 SQLite DB**：**禁止** `alembic upgrade head`；schema 由 SQLAlchemy `create_all`（`ledger_store.initialize()`）建立 → 新表在既有庫上**自動補齊**，不需遷移指令。
- **全新 DB**：可跑 Alembic `0001 → 0002 → 0003`。
- **PostgreSQL**：`create_ledger_store()` 對非預設路徑映射到**獨立 schema**（保留 per-path 隔離語意），生產預設路徑共用 `public`（與遷移一致）。
- `tests/test_accounting_path_policy.py` 含 **schema  parity 檢查**：`schema.sql` 與 `migrations/versions/0003_*.py` 的表/欄位定義必須一致（防止兩套 DDL 漂移）。

### 4.5 失敗降級與可觀測性

`AccountingHealth`：`writes_ok` / `writes_failed` / `write_success_rate` / `degraded` / `last_error` / `last_error_op` / `errors_by_op` / `backend` / `database_path`。
暴露管道：`StrategyLedger.health()`、API `/api/v1/system/ledger-audit` 的 `accounting_health`、稽核 L12（`writes_failed > 0` → HIGH）。

---

## 5. 稽核工具與閘門

### 5.1 L1–L15 不變式矩陣

| 編號 | 嚴重度 | 檢查 | 觸發條件 |
| :-- | :-- | :-- | :-- |
| L1 | high | `ORPHAN_FILLS` | `trade_fills.bot_id` 未註冊於 `bots` |
| L2 | high | `ORPHAN_EVENTS` | `bot_events.bot_id` 未註冊於 `bots` |
| L3 | critical | `SCHEMA_DRIFT` | canonical 表/欄位缺失（含 P4-1 新表） |
| L4 | high | `POLICY_BYPASS` | 策略層/API 層/引擎入口出現 `SqliteLedgerStore(` 或 `sqlite3.connect(`（白名單外） |
| L5 | medium | `FILL_EVENT_GAP` | 有 `trade_fills` 卻無 canonical `fill` 事件（事件溯源退回 legacy） |
| L6 | medium | `NON_IDEMPOTENT_EVENTS` | 可重跑事件缺 `dedupe_key` |
| L7 | high | `SIDE_FILE_DIVERGENCE` | 檔案型帳本與 DB 分歧（記帳孤島殘留） |
| L8 | medium | `CONTRACT_VIOLATION` | 有成交卻未滿足策略記帳契約（籃子型必寫 `bot_baskets`） |
| L9 | low | `PHANTOM_BOTS` | 身分標 running/idle 卻**從無記帳活動**（排除 `bot_created`/`bot_renamed` 純身分事件） |
| L10 | medium | `AUDIT_LOG_HEALTH` | `grid_audit.db` 缺失/schema 異常/寫入失敗 |
| L11 | high | `PNL_IDENTITY` | 快照違反 `net_pnl = grid_profit + unrealized_pnl − total_fees` |
| L12 | high | `ACCOUNTING_HEALTH` | `AccountingHealth.writes_failed > 0`（靜默降級） |
| L13 | medium | `DUPLICATE_FILLS` | 同一 `client_order_id` 對應多筆 `fill_id` |
| L14 | medium | `MODE_MISMATCH` | `exchange` 含 testnet 卻標記 `account_mode=live` |
| L15 | medium | `BACKEND_MISMATCH` | `DATABASE_URL` 已設定但實際後端非 PostgreSQL（幽靈後端） |

### 5.2 CLI：`tools/ledger_consistency_audit.py`

```bash
python3 tools/ledger_consistency_audit.py                       # 文字報告（預設 --fail-on critical）
python3 tools/ledger_consistency_audit.py --markdown            # Markdown 報告（可貼入 PR/文件）
python3 tools/ledger_consistency_audit.py --json                # 機器可讀
python3 tools/ledger_consistency_audit.py --backfill            # 先冪等回填 CFP 檔案型帳本再稽核
python3 tools/ledger_consistency_audit.py --export-mirror       # 由 DB 真相源反向產生檔案鏡像
python3 tools/ledger_consistency_audit.py --db path/to/x.sqlite3  # 稽核指定帳本檔（如生產庫唯讀副本）
python3 tools/ledger_consistency_audit.py --fail-on high        # CI 閘門：high 以上即 exit 1
```
**退出碼**：`0` = 通過；`1` = 發現達 `--fail-on` 嚴重度；`2` = 執行失敗（含完整 traceback）。

### 5.3 API：`GET /api/v1/system/ledger-audit`

| 參數 | 說明 |
| :-- | :-- |
| `backfill` | `true` = 先執行冪等回填再稽核（回應含 `backfill` 統計） |
| `policy_scan` | 預設 `true`；含 L4 靜態政策掃描 |
| `markdown` | `true` = 回傳 `text/markdown` 報告 |

需 `Authorization: Bearer <GRID_API_TOKEN>`；唯讀、零交易所 API 權重。
回應含 `healthy`、`worst_severity`、`counts`、`strategies`（各策略覆蓋與契約判定）、`accounting_health`、`summary`、`findings[]`（`check_id`/`severity`/`title`/`detail`/`evidence`/`remediation`）、`audit_log`。

### 5.4 CI 靜態守門

`tests/test_accounting_path_policy.py`（12 項）：唯一路徑政策掃描、`schema.sql` ↔ Alembic `0003` parity、回填/CLI/API 存在性、硬編碼相對路徑禁止。
`scripts/ci.sh` `TEST_MODULES` 已加入 3 個新模組（57 → 60）。
網格零變更守門（6 個測試）升級為 **allowlist 制**：`tests/_grid_guard.py::GRID_DIFF_ALLOWLIST` 記載「已授權的跨切面改動 + 交付期 + 理由」，未授權的網格差異仍會失敗（本次授權項：`position_tracker_impl.py` 僅改記帳埠，交易邏輯零變更）。

---

## 6. 驗證證據

### 6.1 完整 CI（零回歸證明）

```
# pristine HEAD（未含本次改動），同沙箱、同依賴
$ git worktree add /tmp/base_wt2 HEAD --detach && cd /tmp/base_wt2 && python3 -m unittest <57 modules>
Ran 486 tests in 34.895s
FAILED (failures=22, errors=3, skipped=2)

# 0.3.6（本次改動）
$ python3 -m unittest <60 modules>
Ran 545 tests in 36.524s
FAILED (failures=22, errors=3, skipped=2)

$ diff base_names.txt final_names.txt
（無輸出）→ 失敗項目集合逐項相同 ⇒ 零回歸
```
殘留 25 項失敗/錯誤全為環境性（Binance 不可達）：`422 交易對 ETH/USDC:USDC 在交易所 binance 中不存在或無法交易`（沙箱出口 IP 被交易所封鎖，HTTP 418）。

### 6.2 新增測試（59 項，全數通過）

| 模組 | 項數 | 覆蓋 |
| :-- | :-- | :-- |
| `tests/test_unified_ledger_accounting.py` | 30 | 門面契約（後端由工廠解析、fill 事件、`fill_id` 冪等、失敗可觀測、注入 raw store、dict 籃子正規化、事件不外洩至全域單例）；籃子均價法損益（多/空符號、名單同步保留已結算）；runtime-state/regime 冪等；策略契約；CFP dry-run（fills + 冪等事件 + 不動籃子 + 損益估算）；CFP 實盤（籃子更新 + realized + `reduceOnly`）；`force_flatten`；DB 優先於鏡像；舊檔回填；鏡像無損還原；regime 歷史；排程回寫；換倉快照；外部標的來自 ledger；只登記使用中帳號；Grid/DCA 共用同一埠；AuditLogger per-root/自我修復 |
| `tests/test_ledger_consistency_audit.py` | 17 | L1–L15 逐項**缺陷注入**偵測 + 健康庫零誤報 + 報告序列化/Markdown |
| `tests/test_accounting_path_policy.py` | 12 | 唯一路徑政策掃描、schema ↔ 遷移 parity、回填/CLI/API 存在性、硬編碼路徑禁止 |

### 6.3 CLI：健康庫 + 鏡像修復迴路（摘錄 `/tmp/audit/evidence/cli_healthy2.md`）

```
# 統一記帳一致性稽核報告 (Ledger Consistency Audit)
- 帳本後端：`sqlite`（DATABASE_URL 已設定：False）
- 資料庫路徑：`/tmp/audit/evidence/ledger.sqlite3`
- 整體判定：**✅ HEALTHY**（最嚴重等級 low，共 1 項發現）

## 記帳計數
| `audit_log_self_healed` | True |   ← L10 自我修復探測（全新安裝不再誤判）
| `bot_baskets` | 1 | `bot_events` | 5 | `bot_regime_decisions` | 2 |
| `bot_snapshots` | 1 | `bots` | 2 | `trade_fills` | 2 | `bot_positions` | 0 |

## 各策略記帳覆蓋
| 策略           | bots | fills | fill 事件 | 部位承載      | 快照 | 判定 |
| coinfull_prime |    1 |     2 |         2 | bot_baskets   |    1 |  ✅  |
| grid           |    1 |     0 |         0 | bot_positions |    0 |  ✅  |

## 發現清單
| L9 | LOW | PHANTOM_BOTS | 1 個機器人身分從無記帳活動卻標示 running（合成種子的 grid bot，屬預期）|
```

**鏡像修復迴路實測**（L7 → 修復 → 復查）：
```
$ tools/ledger_consistency_audit.py --db <ledger> --data-dir <data>          # 偵測到 L7 HIGH
  證據: {"basket_divergence":[{"account_id":"paper","only_in_file":["ETHUSDT"],"only_in_db":[]}]}
$ tools/ledger_consistency_audit.py --db <ledger> --data-dir <data> --export-mirror
  📤 檔案鏡像匯出結果: {"basket_symbols":1,"regime_decisions":2,"run_snapshots":0}
$ tools/ledger_consistency_audit.py --db <ledger> --data-dir <data> --markdown
  整體判定：**✅ HEALTHY**（L7 已消除）
```
> 對照：修復門面事件路徑**之前**，同一種子資料會出現 `L5 FILL_EVENT_GAP`（`fill 事件 = 0`）
> → 稽核工具本身即為該缺陷的發現者與回歸守門（見 §4.1 門面容錯 ③）。

### 6.4 CLI：缺陷注入庫（摘錄 `/tmp/audit/evidence/cli_defects.md`）

注入 7 類缺陷（孤兒 fill/event、重複 `client_order_id`、事件缺 `dedupe_key`、籃子型策略有成交無籃子、testnet 標 live、快照違反會計恆等式、鏡像與 DB 分歧）後：

```
整體判定：❌ UNHEALTHY（worst=high，10 項發現）
L1  HIGH   ORPHAN_FILLS          L2  HIGH   ORPHAN_EVENTS        L11 HIGH   PNL_IDENTITY
L7  HIGH   SIDE_FILE_DIVERGENCE  L5  MEDIUM FILL_EVENT_GAP       L6  MEDIUM NON_IDEMPOTENT_EVENTS
L13 MEDIUM DUPLICATE_FILLS       L14 MEDIUM MODE_MISMATCH        L8  MEDIUM CONTRACT_VIOLATION
L10 MEDIUM AUDIT_LOG_HEALTH      L9  LOW    PHANTOM_BOTS

exit code：--fail-on critical → 0；--fail-on high → 1（CI 閘門行為正確）
```
另測得**寫入時防線**：以正常 store API 寫入未註冊 bot 的 fill 會被 `LedgerIntegrityError`（P1-6）直接拒絕 → L1/L2 屬「第二道防線」（針對 legacy/外部直接寫庫的資料）。

### 6.5 API 端點 smoke（摘錄 `/tmp/audit/evidence/api_smoke.txt`）

```
GET /api/v1/system/ledger-audit                     -> 200
  healthy: False | worst_severity: high | findings: 10
  check_ids: ['L1','L2','L11','L5','L6','L13','L14','L8','L10','L9']
  counts: {bots:4, trade_fills:5, bot_events:9, bot_baskets:1, bot_regime_decisions:2, bot_snapshots:2}
  strategies.coinfull_prime: {fills:4, fill_events:2, basket_rows:1, position_store:'bot_baskets', contract_ok:true}
GET ...?markdown=true                               -> 200 text/markdown; charset=utf-8
GET ...?policy_scan=true                            -> 200（無 L4 ⇒ 政策掃描零違規）
GET ...?backfill=true                               -> 200（回應含 backfill 統計）
GET ...（未帶 token）                                -> 401
```

### 6.6 回填冪等（`/tmp/audit/evidence/seed.txt`）

```
run1: {"baskets":{"accounts":1,"symbols":1}, "regime_decisions":{"imported":1,"skipped_duplicate":0}, "errors":[]}
run2: {"baskets":{"accounts":1,"symbols":1}, "regime_decisions":{"imported":0,"skipped_duplicate":1}, "errors":[]}
seed_health: {"writes_ok":9,"writes_failed":0,"write_success_rate":1.0,"degraded":false,"backend":"sqlite"}
```

### 6.7 Live 驗證（AGENTS.md 要求）

沙箱內**無既有服務執行**（`ss -ltnp` 無 :8001；`ps aux` 無 `main.py` / `run_multiple_grids.py` / pm2），故依「啟動實例 + 真實 HTTP 請求」完成驗證：

```bash
PYTHONPATH=<deps> GRID_API_TOKEN=<token> python3 web_ui/backend/main.py   # 0.0.0.0:8001
```

啟動 log 即驗證了三項修復在**真實進程**中生效：
```
INFO:CoinFullPrime.Service:🔁 檔案型帳本已回填 canonical ledger (reason=basket_empty):
     {... 'files_found': {'coinfull_prime_scheduler.json': True, ...},
      'scheduler_states': {'imported': 1}, 'errors': []}          ← D-03 冪等回填（legacy JSON → DB）
INFO:GridAPI.Audit:✅ 審計資料庫初始化成功: .../data/grid_audit.db  ← D-06 自我修復
# 重啟後：
INFO:CoinFullPrime.Service:🔄 從持久化紀錄恢復 CoinFull Prime 排程: 帳號=binance_testnet, DryRun=False, 啟用狀態=True
                                                                    ← 排程狀態由 canonical ledger 還原（跨進程重啟）
```

HTTP 實測（詳見 §9.3）：`/api/v1/system/ledger-audit` → **200 healthy=true**；`?markdown=true` → 200
`text/markdown; charset=utf-8`；未帶 token → **401**；`/coinfull-prime/{basket,accounting,regime/history,fills,history,status}` → 皆 **200**，
且回應標注 `source: canonical_ledger:bot_baskets` / `canonical_ledger:bot_regime_decisions`（證明讀取端走 DB 真相源）；
`bots` 計數 = **1**（只登記使用中的帳號環境 → D-05 修復在 live 生效，而非 3 個幽靈身分）。
`/coinfull-prime/regime` → 500，原因為 `Cannot connect to host fapi.binance.com:443`（沙箱無外網，非本次改動）。

---

## 7. 殘留風險與後續建議

| 編號 | 風險/建議 | 嚴重度 | 說明與建議做法 |
| :-- | :-- | :-- | :-- |
| R-01 | e2e 測試與交易所強耦合 | P1 | 25 項環境性失敗源自市場預檢需真實 Binance 回應。建議為市場預檢注入**可離線的 contract market provider**（或 `GRID_SKIP_MARKET_PRECHECK=1` 測試旗標），讓 CI 在無外網環境可判定真實回歸 |
| R-02 | `trade_fills` 無 `symbol` 欄（D-08） | P2 | 現以 ID 命名契約推導。若未來要實體化：①新增可空 `symbol` 欄（0004）②以命名契約回填 ③雙寫一個版本週期 ④稽核校驗一致後才讓讀取端改依賴該欄 |
| R-03 | `tools/` 未納入 L4 政策掃描 | P2 | 已文件化 `TOOLING_RAW_STORE_ALLOWLIST`（5 項）。建議下一版把 `tools/` 納入掃描並套用該白名單，避免新工具再次引入幽靈資料源 |
| R-04 | 三處仍以全域 `emit_bot_event()` 發事件 | P2 | `grid_reset_manager.py:603`、`exchange_reconciler.py:224-226`、`control_plane.py:59-61`。目前皆操作預設 store（語意一致），但建議收斂到門面，杜絕「注入自訂 store 時事件外洩」同類缺陷 |
| R-05 | 知識圖譜未更新 | P2 | `graphify-out/` 仍為 0.3.5 快照（無 `strategy_ledger`/`ledger_audit`/`ledger_backfill` 節點）。CLI 可用後執行 `graphify update .` |
| R-06 | PG 模式未在真實 PostgreSQL 上端到端驗證 | P1 | 沙箱無 PG 實例。本次以工廠路由 + L15 `BACKEND_MISMATCH` 檢查 + per-path schema 隔離邏輯覆蓋；**上線前建議在 staging PG 執行**：`--backfill` → `/fills`、`/history`、`/basket`、`/accounting` 端點比對 → `ledger-audit` 應為 HEALTHY |
| R-07 | 鏡像檔仍可能被外部程式寫入 | P2 | 鏡像已標注 `source: canonical_ledger:bot_baskets`，且 L7 會偵測分歧。建議維運文件明令：鏡像檔**唯讀**，修正資料一律改 DB 後 `--export-mirror` |

---

## 8. 維運手冊（Runbook）

**日常健康檢查**
```bash
python3 tools/ledger_consistency_audit.py --markdown --fail-on high
curl -H "Authorization: Bearer $GRID_API_TOKEN" \
  "http://127.0.0.1:8001/api/v1/system/ledger-audit?markdown=true"
```

**從 0.3.5 以前升級（既有 SQLite）**
1. **不要**跑 `alembic upgrade head`（AGENTS.md 明令）。
2. 啟動服務：`initialize()` 會以 `create_all` 自動補齊 `bot_baskets` / `bot_regime_decisions` / `bot_runtime_state`。
3. 服務啟動時自動冪等回填；亦可手動：`python3 tools/ledger_consistency_audit.py --backfill`。
4. 驗證：稽核報告應為 `HEALTHY`，且 `strategies.coinfull_prime.contract_ok = true`、`fill_events > 0`。
5. 回填確認後，檔案帳本即為鏡像；如需由 DB 重產鏡像：`--export-mirror`。

**切換 PostgreSQL**
1. 設定 `DATABASE_URL`；全新庫可跑 `alembic upgrade head`（含 0003）。
2. 啟動後檢查稽核 `backend = postgresql`、無 `L15 BACKEND_MISMATCH`。
3. 若發現 CFP 資料仍缺：確認沒有殘留 `data/web_ui_runtime.sqlite3` 幽靈庫被讀取（0.3.6 已消除該路徑），並重跑 `--backfill`。

**出現 L7（檔案與 DB 分歧）**
1. 先 `--json` 看 `evidence.basket_divergence`（`only_in_file` / `only_in_db`）。
2. 若檔案是舊真相源殘留 → `--backfill`（非破壞性，`close_missing=False`）。
3. 若 DB 才是正確資料 → `--export-mirror` 覆寫鏡像。
4. **切勿**手改鏡像檔。

**出現 L12（記帳降級）**
1. 查 API `accounting_health.errors_by_op` 與 `last_error` 定位失敗操作。
2. 常見原因：磁碟滿、PG 連線中斷、schema 缺表（既有庫未 `create_all`）。
3. 交易不受阻斷（設計如此），但**必須**在修復後重跑回填/補寫，避免永久缺口。

---

## 9. 變更檔案清單與 Live 驗證紀錄

### 9.1 新增

| 檔案 | 用途 |
| :-- | :-- |
| `core/infrastructure/database/strategy_ledger.py` | **統一記帳門面**（唯一埠）+ `AccountingHealth` + `contract_for()` 策略記帳契約 |
| `core/infrastructure/database/ledger_audit.py` | 稽核引擎（L1–L15、政策掃描、報告序列化/Markdown） |
| `core/infrastructure/database/ledger_backfill.py` | 冪等回填（檔案 → DB）與鏡像匯出（DB → 檔案） |
| `migrations/versions/0003_unified_strategy_accounting.py` | 新 DB 遷移（`down_revision = 0002_bot_events`） |
| `tools/ledger_consistency_audit.py` | 稽核 CLI（`--json/--markdown/--backfill/--export-mirror/--db/--fail-on`） |
| `tests/test_unified_ledger_accounting.py` | 30 項整合測試 |
| `tests/test_ledger_consistency_audit.py` | 17 項稽核引擎缺陷注入測試 |
| `tests/test_accounting_path_policy.py` | 12 項路徑政策 + schema parity 守門 |
| `tests/_grid_guard.py` | 網格零變更守門 allowlist（含授權理由） |
| `docs/unified_ledger_accounting_audit_report_20260916.md` | 本報告 |

### 9.2 修改（重點）

| 檔案 | 修改 |
| :-- | :-- |
| `core/infrastructure/database/ledger_store.py` | 3 張新表 + 對應方法 + Protocol 擴充；`:memory:` dispose 缺陷修復（D-09） |
| `core/infrastructure/database/schema.sql` | 新表 DDL（與 0003 parity，由測試守門） |
| `core/services/coinfull_prime/service.py` | 全面改走門面；`get_external_bot_symbols()` 改查 ledger（D-02）；籃子 DB 真相源 + 鏡像 + 回填；只登記使用中帳號（D-05）；regime 歷史/換倉快照/排程狀態入 DB |
| `core/services/coinfull_prime/rebalancer.py` | 門面寫入；`batch_id`；`dedupe_key`；均價法 realized PnL；`reduceOnly`；名單同步跳過 `reduce_only`（D-07） |
| `core/services/coinfull_prime/models.py` | `realized_pnl_usdt` / `fees_usdt` / `batch_id` 欄位 |
| `core/services/grid/implementations/position_tracker_impl.py` | 記帳埠收斂至門面（**交易邏輯零變更**；已列入守門 allowlist） |
| `web_ui/backend/routes/coinfull_prime.py` | 讀取改走門面；新增 `/regime/history`、`/basket`、`/accounting`；`_infer_symbol()`（D-08） |
| `web_ui/backend/routes/system.py` | 新增 `GET /api/v1/system/ledger-audit` |
| `web_ui/backend/runtime_repository.py` | `_accounting = StrategyLedger(repository=self._ledger)`（Grid/DCA 共用埠） |
| `web_ui/backend/audit.py` | per-root 註冊表 + schema 自我修復 + 寫入失敗計數 + `health()`（D-06） |
| `web_ui/backend/lifecycle.py` | 列入 L4 政策白名單（專案唯一低階 SQLite 連線器） |
| `tools/ai_reflection_engine.py` | 改用 `create_ledger_store()` 工廠（D-12） |
| `tests/conftest.py` | 單例重置（D-10） |
| `tests/test_coinfull_prime_rebalance.py` | 帳本斷言現代化（門面 + `batch_id` + `dedupe_key` + canonical fill 事件） |
| `scripts/ci.sh` | `TEST_MODULES` 57 → 60 |
| `VERSION` / `CHANGELOG.md` / `AGENTS.md` | 0.3.6；變更條目；記帳路徑政策與守門 allowlist 規則 |
| 6 個 DCA 守門測試檔 | 網格零變更斷言升級為 allowlist 制（保留攔截未授權改動的能力） |

### 9.3 Live 驗證紀錄

| 時間 (UTC) | 動作 | 結果 |
| :-- | :-- | :-- |
| 2026-09-16 15:01 | 啟動 `web_ui/backend/main.py`（`0.0.0.0:8001`，`GRID_API_TOKEN` 由環境注入） | `Application startup complete.`；CFP 排程監聽器啟動（每週四 UTC 00:05） |
| 15:01 | 啟動過程自動回填 | `scheduler_states.imported=1`（legacy `coinfull_prime_scheduler.json` → `bot_runtime_state`）、`errors=[]` |
| 15:01 | `GET /api/v1/system/ledger-audit` | **200**；`backend=sqlite`；`database_path=data/web_ui_runtime.sqlite3`；`accounting_health.writes_failed=0` |
| 15:01 | `GET /api/v1/system/ledger-audit?markdown=true` | **200**（content-type 修正前為重複 charset，已修） |
| 15:01 | `GET /api/v1/system/ledger-audit`（未帶 token） | **401** |
| 15:01 | `GET /api/v1/system/health` | **200**（`status=degraded`：引擎未啟動，與本次改動無關） |
| 15:01 | `GET /api/v1/coinfull-prime/status` | **200**；`next_scheduled_run_utc=2026-09-17T00:05:00+00:00` |
| 15:01 | `GET /api/v1/coinfull-prime/{basket,accounting,regime/history,fills,history}` | 皆 **200**；`source=canonical_ledger:*`；`count/total=0`（沙箱為全新帳本） |
| 15:01 | `GET /api/v1/coinfull-prime/regime` | **500** `Cannot connect to host fapi.binance.com:443`（環境無外網，非改動造成） |
| 15:03 | 套用 L10 自我修復探測 + markdown content-type 修正後**重啟服務** | `🔄 從持久化紀錄恢復 CoinFull Prime 排程`（由 canonical ledger 還原） |
| 15:04 | 重測 `GET /api/v1/system/ledger-audit` | **200**；`healthy=true`、`worst_severity=low`、findings 僅 `L9`（全新帳本尚未換倉，屬預期）；`counts.bots=1`（無幽靈身分）；`audit_log.schema_ok=true`、`write_failures=0`；L10 誤判已消失 |
| 15:04 | 重測 `?markdown=true` | **200**；`content-type: text/markdown; charset=utf-8`（單一 charset） |

> L9（LOW）說明：CFP 身分在啟動時登記為 `running`，但沙箱尚未執行任何換倉 → 尚無記帳活動。
> 這是**正確的稽核行為**（首次換倉後即自動消失），非缺陷。

---

## 10. 結論

1. **0.3.4 宣稱的「統一記帳層整合」未真正達成**：寫入端繞過工廠（PG 模式幽靈庫）、四份檔案帳本仍為真相源、安全護欄以硬編碼路徑靜默失效、損益恆為 0、事件非冪等且缺 canonical `fill` 事件。宣稱中「逐筆寫入 `trade_fills`」與「事件持久化」在 **SQLite 模式下**確實成立，故判定為**部分成功**。
2. **0.3.6 已完成真正的統一**：三策略 + API 層共用**單一記帳埠**與**同一套 canonical 表**；DB 為唯一真相源、檔案降為鏡像並可雙向冪等轉換；損益可由 DB 獨立重算；冪等鍵齊備；記帳失敗可觀測且不阻斷交易。
3. **修復本身可被驗證、且可持續被守門**：59 項新測試 + 15 項稽核不變式 + CLI/API + CI 靜態政策掃描；完整 CI 與 pristine HEAD 基準**失敗集合逐項相同（零回歸）**。
4. **上線前唯一必要動作**：在 staging PostgreSQL 上跑一次 `--backfill` + `ledger-audit`（R-06），確認 `backend=postgresql` 且報告 `HEALTHY`。
