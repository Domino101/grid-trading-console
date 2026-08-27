# DCA 圖表與回測 Phase C8 工作紀錄（端到端驗收、文件與稽核）

> 日期：2026-08-27
> 分支：`arena/01a04361-grid-trading-console`
> 計畫：`docs/dca_chart_backtest_plan_zh-TW.md` §5 Phase C8
> 前置：C1–C7 已完成（C7 收尾 commit `d2c4066`）

---

## 1. 這次做了什麼

C8 的名義任務是「E2E + 文件」，但實際的主戲是**把 CI 從 17 條紅燈變成全綠**——
而那 17 條紅燈不是測試寫壞，是一個**真的後端缺陷**。

| 項目 | 內容 |
|---|---|
| 修復 F-1（P0） | `web_ui/backend/__init__.py`：把 `auth` 與 `web_ui.backend.auth` 這類雙拼法映射到同一模組物件 |
| 修復 F-2（P2） | `/chart` 的 `warnings` 重複附加「使用合成 K 線，非真實歷史」 |
| 修復 F-3（P1） | `tests/test_dca_http_contract.py` 覆寫全域後不還原（C7 工作紀錄列的待清理項） |
| 新增 E2E | `tests/test_dca_chart_backtest_e2e.py`（13 項，覆蓋稽核 8.1–8.6） |
| 文件 | 使用者手冊、最終稽核報告、`dca_bot_user_guide` 新章節、`config/dca/README.md`、`README.md`、`CHANGELOG.md` |
| CI 雙登記 | `scripts/ci.sh` + `docs/github-actions-ci.yml` |

---

## 2. F-1：一個模組被載入兩次，代價是四組單例分岔

### 2.1 現象

C0 依賴補齊後跑 `bash scripts/ci.sh`：

```
Ran 320 tests … FAILED (failures=15, errors=2)
AssertionError: 401 != 201 : {"detail":"Invalid or missing API token"}     ×14
AssertionError: 401 != 400                                                 ×1（klines 端點）
AssertionError: 'live' != 'degraded'                                       ×1（dashboard 快照）
```

單獨跑也一樣紅，所以不是測試順序問題。

### 2.2 根因

```python
# web_ui/backend/main.py
from auth import token_matches, verify_token      # 頂層拼法（uvicorn 從 backend 目錄啟動）

# web_ui/backend/routes/dca.py（C5 起改為絕對匯入）
from web_ui.backend.auth import verify_token      # 套件拼法
```

`web_ui/backend` 既在 `sys.path` 上、又是可匯入的（namespace）套件，於是 Python **把同一個檔案執行兩次**，產生兩個模組物件。實測：

```
auth is web_ui.backend.auth ? False
runtime_repository same? False
['web_ui.backend', 'web_ui.backend.audit', 'web_ui.backend.auth', ... 12 個]
```

後果不只是測試：

* `auth._API_TOKEN` 有兩份 → 測試把其中一份改成 `contract-token`，DCA 路由讀的是另一份 → 401。
* `RuntimeSnapshotRepository._instance` 有兩份 → 測試把一份指向暫存 DB，`GridStateStore` 卻經另一份連到**真實**的 `data/web_ui_runtime.sqlite3` → dashboard 測試讀到真資料而回報 `live`。
* 正式環境同樣有兩個 SQLite 連線、兩份 `GridStateStore` 記憶體快取、兩個 `AuditLogger` 檔案控制代碼。目前靠「token 檔案存在」與「兩份都指向同一 DB 路徑」矇混過去，是一顆計時炸彈。

### 2.3 修法與取捨

三個選項：

| 方案 | 缺點 |
|---|---|
| A. 全庫改用頂層拼法 | 違反 C7 建立的絕對匯入契約測試；且引擎從專案根啟動時需要套件拼法 |
| B. 全庫改用套件拼法（含 `main.py` 與 5 個 routes） | 15+ 檔案的機械式改動，動到運行中後端的每一條匯入路徑，風險面大 |
| **C. 匯入別名（採用）** | 需要一個 meta-path finder，稍具「魔法」；以嚴格守衛與大段註解補償 |

採 C，並把「魔法」限制到最小：只有在**對應拼法的模組已載入**、且**其 `__file__` 正是 backend 目錄下的同一個檔案**時才建立別名。第三方套件叫 `auth`、專案別處有 `models` 都不會被攔截。

```
（修復後）auth same? True  routes.dca same? True  repo same? True  schemas same? True
```

兩種載入順序（先頂層／先套件）都驗過。

---

## 3. E2E 測試：13 項，走完整條鏈路

`tests/test_dca_chart_backtest_e2e.py`：

```
建立 PAPER 機器人 → klines → chart → backtest → sweep → delete
＋ BP-2（不下真單、不動 active_dca.json）
＋ 8.3 網格零回歸、8.4 LIVE 關閉、8.5 憑證掃描、8.6 單例一致性
```

刻意做的三件事：

1. **把「不小心連網」變成紅燈**：`KlineService.fetch_fn` 換成會 `raise` 的 stub，任何真實抓取立即失敗，而不是變慢或偶發。
2. **每次寫操作換一個 `source`**：`SafetyGuard` 對同一 source 60 秒內只允許 3 次寫操作。第一版共用 `source=c8-e2e`，結果 8.4 收到 429 而不是它要驗的 403——測試在驗一個假的東西。
3. **收尾必須乾淨**：`tearDownClass` 還原 `active_dca.json` 位元組並刪除所有本檔建立的 YAML；跑完 `git status` 無殘留。

---

## 4. 突變測試（附一條失敗的自我檢討）

| 突變 | 結果 |
|---|---|
| 停用模組別名安裝 | `test_8_6` **FAIL** ✓ |
| 對 `core/services/grid/` 加一行 | `test_8_3` **FAIL** ✓ |
| 移除「歷史績效不代表未來」（driver + route 兩處） | `test_8_1_d` **FAIL** ✓ |
| 還原 `/chart` warnings 去重 | `test_8_1_c` **FAIL** ✓ |
| `ReplayExchange` 改成同棒撮合（未来函数） | `test_dca_replay_exchange` **FAIL** ✓ |

**寫壞的那一條**：E2E 原本有一條「棒 i 下的單不得在棒 i 成交」的斷言，對同棒撮合突變**毫無反應**。原因是回測結果依規格只投影成交事件、不含 `order_placed` 標記（計畫 §4.7：該標記「僅即時圖」），所以那個迴圈的 `placed` 字典永遠是空的——一條恆綠的裝飾品。

已刪掉，改成兩條會紅的：

* 回測標記種類必須全屬成交類，且**不得**出現 `order_placed`（這才是真正的規格）；
* 同一請求連跑兩次，除 `bars_per_second` 外逐欄位相同（牆鐘或亂數洩漏會在此現形）。

未来函数的把關本來就在 `test_dca_replay_exchange`（R-1 向量）與 `test_dca_replay_driver`（前視注入），E2E 不必也不該重複。

---

## 5. 實機驗證（AGENTS.md rule-05）

`python main.py`（沙箱無 venv，`start_web_ui.sh` 硬編碼 `venv/bin/python3`）→ `0.0.0.0:8001`。
改完 `routes/dca.py` 後**重啟並複驗**：

| 端點 | 重點輸出 |
|---|---|
| `POST /api/v1/dca` | 201，`auto_start=false` |
| `GET /market/klines/…?account_mode=paper` | `paper_synthetic` / `paper` / `gaps=0` |
| `GET /dca/{bot}/chart` | `candles=100`、`marks=0`、`warnings` **1 條**（修復前 2 條） |
| `POST /dca/backtest`（600 棒） | `cycles_completed=3`、`total_return -5.29%`、`buy&hold -12.65%`、`alpha +7.36%`、`profit_factor=null` |
| `POST /dca/backtest/sweep`（3 組） | `truncated=false`、`bars_used=1200/5000`、best `take_profit_percent=1.0` |
| `DELETE /dca/{bot}` | 200，YAML 歸檔、登記移除 |
| 無 token | 401 |

`alpha=+7.36%` 而 `total_return=-5.29%` 正是手冊 §2.2 強調的情境：策略虧錢但明顯優於買入持有。只看總報酬會做出錯誤決策。

---

## 6. 驗收數字（before / after）

```
Python  BEFORE: Ran 320 tests … FAILED (failures=15, errors=2)
        AFTER : Ran 333 tests … OK

前端    90/90 pass、lint 0 error（既有 1 warning）、build 成功
        DcaChart chunk gzip 57.93 kB（獨立 chunk，首屏不載）

新增失敗（回歸）: 無
修好的失敗      : 17 條（全部源自 F-1）
```

---

## 7. 留給後續的事

1. **匯入拼法應收斂為一種**。別名解決了症狀與立即風險，但雙拼法本身仍是壞味道；建議獨立一次「全庫統一為 `web_ui.backend.*`」的機械式重構，並在契約測試中禁止頂層拼法。屆時 `web_ui/backend/__init__.py` 的 finder 可以退場。
2. **`test_dca_backtest_http_contract._restore_global_state()` 的雙模組迴圈已成冗餘**（兩個名字現在指向同一物件）。留著無害，但等第 1 點完成後可一併清掉。
3. **walk-forward / 樣本內外切分**仍是 v2 項目（計畫 §8）；目前只能以文件警示過度擬合。
