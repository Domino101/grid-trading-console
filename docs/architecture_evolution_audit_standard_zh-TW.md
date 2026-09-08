# 交易系統架構演進全階段稽核標準規範書 (P0–P3 Modular Audit Standard & Adversarial Playbook)

> **版本**：v2.0.0 (Adversarial Red-Team & Anti-Momentum Edition)  
> **制定日期**：2026-09-06  
> **狀態**：正式生效 (Active & Authoritative)  
> **權威依據**：
> - 核心路線圖：[`docs/architecture_evolution_roadmap_zh-TW.md`](file:///root/grid-trading-console/docs/architecture_evolution_roadmap_zh-TW.md)
> - 執行工作日誌：
>   - [`docs/architecture_evolution_p0_worklog_20260906.md`](file:///root/grid-trading-console/docs/architecture_evolution_p0_worklog_20260906.md) (P0 階段)
>   - [`docs/architecture_evolution_p1_worklog_20260906.md`](file:///root/grid-trading-console/docs/architecture_evolution_p1_worklog_20260906.md) (P1 階段)
>   - [`docs/architecture_evolution_p2_worklog_20260906.md`](file:///root/grid-trading-console/docs/architecture_evolution_p2_worklog_20260906.md) (P2 階段)
>   - [`docs/architecture_evolution_p3_worklog_20260906.md`](file:///root/grid-trading-console/docs/architecture_evolution_p3_worklog_20260906.md) (P3 階段)
> - 稽核底層準則：[`AGENTS.md`](file:///root/grid-trading-console/AGENTS.md) (雙軌稽核標準、服務重啟與運行驗證標準)

---

## 1. 稽核員行為核心禁令與工作準則 (Core Auditor Prohibitions & Adversarial Protocol)

> [!CAUTION]
> **稽核不是複誦開發者的承諾，亦非重跑綠色測試的蓋章儀式。**  
> 凡參與本專案之 AI Agent 稽核員或人類工程師，必須徹底摒棄開發者視角，**嚴格以「對抗性質疑者（Adversarial Auditor）」身分進入系統**。你的唯一任務是：**「想盡一切辦法讓現有系統崩潰、出錯或暴露邊界死角」**。

### 1.1 【絕對禁止行為 (Banned Actions)】
1. **嚴禁直接執行或依賴開發階段已存在的現成測試腳本**（如倉庫中既有的 `tests/test_*.py` 或原有 CI 流程）作為合格驗證依據。**單純重跑原有測試者，一律判定為「無效稽核（Null Audit）」**。
2. **嚴禁以「測試全數通過（All Tests Passed）」作為稽核合格的背書**。綠色測試僅代表「系統在開發者預期路徑下未拋出異常」，不能證明系統具備抗脆弱性。
3. **嚴禁表面掃描與假性合規**：不得僅以「檔案存在」、「函數宣告完備」或「API 回應 200 OK」作結，必須有實質的邊界注入與毀損性實測數據。

---

### 1.2 【強制執行規範 (Mandatory Audit Protocol)】

```
┌────────────────────────────────────────────────────────────────────────┐
│               對抗性質疑者工作流 (Adversarial Audit Workflow)           │
├────────────────────────────────────────────────────────────────────────┤
│ 1. 盲測與獨立撰寫 (Black-box & Independent Testing)                    │
│    忽視既有 tests/ 檔案 ➔ 僅依據原始需求規格獨立撰寫全新破壞性測試案例 │
├────────────────────────────────────────────────────────────────────────┤
│ 2. 專注於開發者盲區 (Edge & Adversarial Injections)                    │
│    邊界值 (極大/極小/負數/Null) ➔ 併發與競態 (死鎖/競態) ➔ 狀態異常 (斷網/斷電)│
├────────────────────────────────────────────────────────────────────────┤
│ 3. 代碼靜態對抗分析 (Semantic Code Review)                             │
│    逐行抓捕：邏輯死角 ➔ 吞例外掩飾錯誤 (`except: pass`) ➔ 狀態機遺漏狀態 │
├────────────────────────────────────────────────────────────────────────┤
│ 4. 強制脆弱點產出 (Vulnerability Mandate)                              │
│    若實測未崩潰 (0 Bug)，報告必須強制揭露「至少 3 項潛在架構脆弱點或風險」│
└────────────────────────────────────────────────────────────────────────┘
```

1. **盲測與獨立撰寫 (Black-box & Independent Testing)**：
   - 稽核員必須主動忽視現有的測試檔案，**僅根據原始需求規格（PRD / Architecture Roadmap）獨立編寫全新、具破壞性、未包含在既有套件中的測試案例**。
2. **專注於開發者盲區 (Edge & Adversarial Cases)**：
   - **邊界條件 (Boundary Injections)**：針對極大值（如浮點溢位、百萬級持倉）、極小值、負數（如負價格、負保證金）、Null/None、型別錯亂（如傳入字串、物件）設計注入測資。
   - **併發與競態條件 (Concurrency & Race Conditions)**：針對同一資源之高頻併發寫入、非同步死鎖、Task 飢餓、Redis PEL 阻塞進行高壓競爭模擬。
   - **狀態異常 (Fault Injections)**：針對網路斷線、交易所回傳非預期錯誤碼、資料庫斷線重連、Master Key 竄改、非預期被 OS `SIGKILL` 等場景設計主動故障注入。
3. **代碼靜態對抗分析 (Semantic Code Review)**：
   - 不只看代碼能不能跑，必須執行微觀逐行走查：
     - 是否存在過度寬鬆的例外捕捉（例如 `except Exception: pass`、`except: return None`）來掩飾底層崩潰？
     - 狀態機轉換（如 `draft → starting → running → stopping → stopped`）是否存在無法復原的幽靈狀態？
     - 是否有未釋放的檔案描述子 (fd)、連線池洩漏或 Goroutine 洩漏？
4. **產出硬性門檻 (Mandatory Audit Output Rule)**：
   - 稽核報告第 1 項必須明確列出：**「本次稽核獨立設計的 N 組全新破壞性測試案例與邊界假設」**。
   - **硬性守則**：若全數測試未造成崩潰（發現 0 個缺陷），**稽核員必須在報告中提出至少 3 個深層潛在架構脆弱點或長程風險評估**，否則該稽核報告視為無效，不予驗收放行！

---

## 2. 執行綱領與分段稽核架構 (Executive Audit Architecture)

### 2.1 為什麼必須實施分段模組化稽核？
本專案歷經 P0 至 P3 四大階段之深度架構演進，涵蓋資料持久層限頻、PostgreSQL 雙軌切換、Redis L1 讀模型快取、Redis Streams 即時控制隊列、PM2 日誌輪轉、多進程 Worker 隔離自癒、Systemd 沙箱特權降階、AES-256-GCM 本機金鑰保險箱、Binance 原生 WS/REST 輕量適配器、Protobuf 二進位 IPC 協議、以及 Go 原生微秒撮合與 39ns 風控微引擎。

若由單一稽核員於單一輪次中嘗試全盤對抗審查，將必然遭遇：
1. **上下文視窗飽和（Context Window Exhaustion）**：超過 30 個核心模組與 10,000+ 知識圖譜節點將擠爆模型工作記憶，導致代碼走查流於形式。
2. **對抗動量衰竭（Auditor Fatigue & Momentum）**：為求迅速產出而放棄深度邊界推演，退化為開發者視角。
3. **破壞性壓測指令超時中斷**：高併發競態模擬、故障注入與微核心基準測試若堆疊在單輪執行，極易超時中斷。

因此，本手冊制定嚴格的**「階段性模組規劃編號（Modular Audit ID System）」**。後續稽核員每次調用**僅聚焦執行一個規劃編號**，在該單元完成「全新測資撰寫 ➔ 對抗性注入 ➔ 語意走查 ➔ 產出脆弱點」閉環後，方可遞進至下一單元。

---

### 2.2 全域規劃編號總覽表 (Master Audit Schedule Matrix)

全案共劃分為 **6 大稽核階段（Stage 0 至 Stage 5）**，合計 **15 個獨立對抗審查單元**：

| 階段代號 | 稽核規劃編號 | 稽核模組與主題 | 關鍵對抗審查目標 | 預估輪次 | 阻斷等級 |
| :--- | :--- | :--- | :--- | :---: | :---: |
| **Stage 0** | `AUDIT-S0-META-01` | 全局治理與五大不可動搖鐵律合規 | SQLite 禁跑 Alembic 實體防線、帳本雙寫相容、單調計數器防護 | 0.5 輪 | **P0 阻斷** |
| **Stage 0** | `AUDIT-S0-META-02` | Git 變更樹、檔案拓撲與依賴安全審查 | 工作目錄純淨度、依賴版本鎖定、零明文敏感 Secret 落地 | 0.5 輪 | **P1 高** |
| **Stage 1** | `AUDIT-S1-P0-01` | 歷史快照寫入限頻與事件驅動落盤 | 300s 抽樣節流閥、重大事件強制寫入、清理低頻防護之併發邊界 | 1 輪 | **P0 阻斷** |
| **Stage 1** | `AUDIT-S1-P0-02` | PostgreSQL 連接池、自動降級與雙軌遷移 | 連接池耗盡模擬、斷線瞬時降級 SQLite、遷移乾跑資料無損性 | 1 輪 | **P0 阻斷** |
| **Stage 1** | `AUDIT-S1-P0-03` | Redis L1 快照快取讀模型與環境庫隔離 | 讀寫分離、Redis 崩潰/畸形資料熔斷降級、測試庫快取穿透隔離 | 1 輪 | **P1 高** |
| **Stage 2** | `AUDIT-S2-P1-01` | Redis Streams 即時指令隊列與檔案降級 | 畸形訊息注入、重放攻擊防禦、指令等冪性、5s 檔案輪詢兜底 | 1 輪 | **P0 阻斷** |
| **Stage 2** | `AUDIT-S2-P1-02` | PM2 日誌自動輪轉與磁碟空間自動維護 | 巨量日誌並發寫入時 copytruncate 安全性、7 天過期修剪防護 | 0.5 輪 | **P1 高** |
| **Stage 2** | `AUDIT-S2-P1-03` | 主進程監督者與多進程 Worker 隔離自癒 | 惡意 SIGKILL (137) 捕獲、連續崩潰指數退避極限、狀態機死鎖防禦 | 1 輪 | **P0 阻斷** |
| **Stage 3** | `AUDIT-S3-P2-01` | 系統特權降階與 Systemd 沙箱配置 | `crypto-runner` 權限逃逸攻擊、目錄 770 邊界、Systemd 檔案沙箱 | 1 輪 | **P1 高** |
| **Stage 3** | `AUDIT-S3-P2-02` | 本機 AES-256-GCM Vault 金鑰保險箱 | 篡改密文/缺失 Key 致命熔斷防禦、日誌脫敏死角審查、Nonce 碰撞防護 | 1 輪 | **P0 阻斷** |
| **Stage 3** | `AUDIT-S3-P2-03` | Binance 原生 WS/REST 輕量高速適配器 | 畸形深度/行情注入、Keep-Alive 斷線重連風暴、簽名運算極限壓測 | 1 輪 | **P1 高** |
| **Stage 4** | `AUDIT-S4-P3-01` | 控制面與執行面 Protobuf 二進位 IPC 協議 | 畸形 4B 長度前綴 (超大/負數)、二進位 Payload 截斷、UDS 斷線重試 | 1 輪 | **P1 高** |
| **Stage 4** | `AUDIT-S4-P3-02` | Go 原生記憶體撮合微引擎與 39ns 風控防線 | 價格時間優先 (FIFO) 破壞性驗證、胖手指極限攔截、併發競態 (`-race`) | 1 輪 | **P0 阻斷** |
| **Stage 5** | `AUDIT-S5-GATE-01`| 全量整合驗證與實機服務在線探測 | 線上服務活體狀態檢驗、健康探測 HTTP 200 OK、零回歸審查 | 0.5 輪 | **P0 阻斷** |
| **Stage 5** | `AUDIT-S5-GATE-02`| 8 大量化架構驗收指標全面對齊驗收 | CPU < 0.5%、記憶體 < 30MB、QPS > 5 萬、撮合 P99 < 500μs | 0.5 輪 | **P0 阻斷** |
| **Stage 5** | `AUDIT-S5-GATE-03`| 四大極端災難復原演練 (DR & Self-Healing) | DB 斷線降級、Redis 崩潰退避、金鑰竄改熔斷、全損成交對帳重建 | 1 輪 | **P0 阻斷** |

---

### 2.3 稽核等級定義與實盤放行裁決樹 (Severity & Release Gates)

- **P0 阻斷 (Blocker)**：
  - **定義**：凡涉及資金安全漏洞、可能導致錯誤下單/超額下單、金鑰明文洩漏、未經遮蔽輸出、資料庫單調計數器倒退、單進程連鎖崩潰未隔離、或存在靜默掩飾例外（Silent Failure）之缺陷。
  - **處置**：**一票否決**。未完全修復並通過獨立破壞性測試前，嚴禁合併主幹，嚴禁宣告演進完成。
- **P1 高 (Critical)**：
  - **定義**：架構指標未達標（如記憶體超出門檻、延遲未達指定百分位）、日誌缺乏輪轉可能導致磁碟耗盡、降級回退未完全實現需手動介入、或沙箱配置未達最小特權原則。
  - **處置**：阻斷正式發布；若有緊急原因需放行，必須開立風險記錄與明確補償機制。
- **P2 中 (Major)**：
  - **定義**：局部邊界錯誤處理未美化、日誌等級偏高或文案模糊、非關鍵輔助腳本缺乏說明文件。
  - **處置**：記錄至下一迭代修復清單。
- **P3 低 (Minor)**：
  - **定義**：代碼排版、註解錯字、微小格式差異。
  - **處置**：例行維護修補。

---

## 3. 雙軌稽核自動化工作流程 (Dual-Stage Adversarial Protocol)

依據本專案規範，所有稽核員在執行任何規劃單元時，必須遵循雙軌作業法：

```
[稽核任務分派 (Dispatch)]
       │
       ▼
┌────────────────────────────────────────────────────────┐
│ Stage 1: Macro Architecture Review (宏觀架構拓撲稽核)   │
│ - 執行 `graphify query` / `graphify explain`          │
│ - 檢視呼叫拓撲、依賴鏈路、資料流向與影響半徑            │
└────────────────────────────────────────────────────────┘
       │
       ▼
┌────────────────────────────────────────────────────────┐
│ Stage 2: Micro Adversarial Inspection (微觀對抗性破壞審查)│
│ - 忽視現有測試，獨立設計全新破壞性邊界測資               │
│ - 逐行語意走查：檢查邏輯死角與吞例外掩飾錯誤之代碼       │
│ - 執行該單元專屬的「對抗性驗證破壞腳本」                 │
│ - 若未發現 Bug，必須強制提出至少 3 項潛在架構脆弱點     │
└────────────────────────────────────────────────────────┘
       │
       ▼
[產出標準對抗稽核裁決卡 (Adversarial Verdict Card)]
```

---

## 4. 稽核員調用協議與標準模板 (Auditor Invocation Protocol)

### 4.1 對抗性稽核調用指令藍圖 (Turn Dispatch Prompt Blueprint)
當需要指派下一輪 AI 稽核員執行特定章節時，請使用標準化 Prompt：

```markdown
【AI AGENT 對抗性稽核派工指令】
- 目標稽核編號：[填寫規劃編號，例如 AUDIT-S1-P0-01]
- 參考標準文件：docs/architecture_evolution_audit_standard_zh-TW.md
- 稽核角色宣告：你是「對抗性質疑者（Adversarial Auditor）」，目標是想辦法讓系統崩潰。
- 強制遵循禁令：
  1. 嚴禁執行或依賴既有的 test_*.py 檔案；重跑現成測試視為無效稽核。
  2. 嚴禁以「測試全數通過」作為背書。
- 具體執行要求：
  1. 執行「雙軌稽核 Stage 1」：使用 graphify 檢查模組拓撲與依賴半徑。
  2. 執行「雙軌稽核 Stage 2」：
     - 獨立撰寫至少 2~3 組全新的破壞性測資（涵蓋極端邊界、併發競態、故障注入）。
     - 逐行走查核心代碼，專門抓捕邏輯死角與 `except Exception: pass`。
  3. 產出報告中必須包含「全新測試案例清單」。若未發現問題，必須強制列出「至少 3 項架構脆弱點」。
  4. 於回答末尾產出「標準對抗稽核裁決卡 (Adversarial Verdict Card)」。
```

---

### 4.2 標準對抗稽核裁決卡模板 (Adversarial Verdict Card Template)
每位稽核員完成審查後，必須在報告結尾輸出：

```markdown
---
### 🛡️ AI 對抗性稽核裁決卡 (Adversarial Verdict Card)
- **稽核編號**：[例如 AUDIT-S1-P0-01]
- **審查模組**：[模組名稱與檔案路徑]
- **裁決結果**：[PASS (抗脆弱合格) / CONDITIONAL PASS (有條件合格) / FAIL (阻斷不合格)]
- **1. 本次稽核獨立設計之全新破壞性測試案例與邊界假設**：
  - [Case 1: 說明測試名稱、邊界假設、注入數據與破壞目標]
  - [Case 2: 說明併發/異常注入場景與預期行為]
- **2. 不可動搖守則與防線對抗結果**：
  - 守則 1: [✅ 成功抵禦破壞 / ❌ 破防 (說明具體破防過程)]
  - 守則 2: [✅ 成功抵禦破壞 / ❌ 破防]
- **3. 代碼語意與例外遮蔽審查 (Semantic & Silent Failure Inspection)**：
  - 逐行審查結果：[是否發現 `except: pass`、偽裝 200、或未處理狀態]
- **4. 發現之缺陷清單 或 強制揭露之架構脆弱點 (Defects or Fragility Points)**：
  - 脆弱點/缺陷 1: [說明代碼行號、觸發條件與長程潛在風險]
  - 脆弱點/缺陷 2: [說明代碼行號、觸發條件與長程潛在風險]
  - 脆弱點/缺陷 3: [說明代碼行號、觸發條件與長程潛在風險]
- **下一階段推薦派工編號**：[例如 AUDIT-S1-P0-02]
---
```

---

## 5. 階段性模組化對抗稽核詳細規範卡 (Adversarial Audit Cards)

---

### [STAGE 0] 全局治理與前置規範稽核 (Meta Governance)

#### 稽核編號：`AUDIT-S0-META-01`
- **稽核主題**：不可動搖五大工程鐵律遵循性與破壞性防線對抗
- **審查範圍與目標檔案**：
  - [`core/infrastructure/database/ledger_store.py`](file:///root/grid-trading-console/core/infrastructure/database/ledger_store.py)
  - [`core/infrastructure/database/migrator.py`](file:///root/grid-trading-console/core/infrastructure/database/migrator.py)
  - [`migrations/versions/`](file:///root/grid-trading-console/migrations/versions/)
  - [`AGENTS.md`](file:///root/grid-trading-console/AGENTS.md)
- **不可動搖約束守則 (Invariants)**：
  1. **SQLite 禁跑 Alembic 實體防線**：嚴禁對既有 SQLite `data/web_ui_runtime.sqlite3` 執行 `alembic upgrade head`，所有表結構變更必須具備 `CREATE TABLE IF NOT EXISTS` 或 SQLite pragma 容錯。
  2. **雙寫向後相容**：`grid_runtime_*`（舊投影表）與 `bot_*`（新 Canonical 表）必須維持雙寫，不可截斷舊版工具。
  3. **快照單調計數器防護**：`completed_cycles`、`realized_pnl`、`filled_count`、`fees` 嚴禁倒退。
- **對抗性質疑者盲區分析**：
  - *開發者盲區 1*：單調防護是否只檢查了單一欄位？若傳入負數、NaN 或包含特殊型別（如字串數字 `"100"`），是否會引發型別轉換例外而繞過檢查？
  - *開發者盲區 2*：若外部惡意傳入 `_monotonic_bypass=True`，系統是否無條件放行？誰有權力 bypass？
  - *開發者盲區 3*：代碼中是否有人使用 `except Exception: pass` 偷偷忽略了資料庫鎖衝突？
- **對抗性破壞驗證腳本 (全新獨立撰寫範例)**：
  ```bash
  python3 -c "
  import os, time
  from core.infrastructure.database.ledger_store import SqliteLedgerStore
  test_db = 'data/test_audit_s0_adversarial.sqlite3'
  os.remove(test_db) if os.path.exists(test_db) else None
  s = SqliteLedgerStore(test_db)

  # 破壞性注入 1: 正常寫入初始快照
  s.save_snapshot('bot_adv', 'config/adv.yaml', {'completed_cycles': 10, 'realized_pnl': 50.0}, time.time())

  # 破壞性注入 2: 嘗試注入倒退數值 (cycles: 9, pnl: -10)
  s.save_snapshot('bot_adv', 'config/adv.yaml', {'completed_cycles': 9, 'realized_pnl': -10.0}, time.time())
  snap = s.load_snapshot('bot_adv')
  assert snap['completed_cycles'] == 10, f'單調計數防線破防! cycles 倒退為 {snap[\"completed_cycles\"]}'
  assert snap['realized_pnl'] == 50.0, f'單調計數防線破防! pnl 倒退為 {snap[\"realized_pnl\"]}'

  # 破壞性注入 3: 注入極端非法型別 (NaN, None, 字串型別數字) 觀察是否導致資料庫死鎖或崩潰
  try:
      s.save_snapshot('bot_adv', 'config/adv.yaml', {'completed_cycles': 'INVALID_INT', 'realized_pnl': None}, time.time())
      print('非法型別注入已被防禦機制安全吸收或攔截')
  except Exception as e:
      print(f'捕獲非法型別例外: {e}')

  os.remove(test_db)
  print('AUDIT-S0-META-01 對抗性破壞測試完成')
  "
  ```
- **量化驗收門檻 (DoD)**：
  - 單調計數器面對逆向數據注入抵禦率 **100%**。
  - 全工程靜態掃描零未授權之 `alembic` 侵入調用。

---

#### 稽核編號：`AUDIT-S0-META-02`
- **稽核主題**：Git 變更樹、檔案拓撲與依賴安全對抗審查
- **審查範圍與目標檔案**：專案根目錄、`config/`、`requirements.txt`、`trading-core-go/go.mod`
- **對抗性質疑者盲區分析**：
  - *開發者盲區 1*：是否有未被 `.gitignore` 排除的臨時私鑰、測試憑證或 `.env` 暫存檔被 stage？
  - *開發者盲區 2*：新增的 Go 或 Python 依賴是否存在間接依賴漏洞或未指定明確版本？
- **對抗性破壞驗證腳本**：
  ```bash
  # 深度掃描全代碼庫中的潛在明文金鑰特徵
  python3 -c "
  import glob, re
  patterns = [re.compile(r'api_secret[\"\'\s:=]+[A-Za-z0-9]{20,}'), re.compile(r'private_key[\"\'\s:=]+[A-Za-z0-9]{32,}')]
  findings = []
  for f in glob.glob('config/**/*.yaml', recursive=True) + glob.glob('core/**/*.py', recursive=True):
      content = open(f, errors='ignore').read()
      for p in patterns:
          if p.search(content) and 'enc:gcm:' not in content and 'mask_secret' not in content:
              findings.append((f, p.pattern))
  if findings:
      print('❌ 警報: 發現潛在未加密或未遮蔽敏感明文:', findings)
  else:
      print('✅ 全代碼未發現裸露 API Secret 明文')
  "
  ```
- **量化驗收門檻 (DoD)**：
  - 零明文敏感 Secret 落地。

---

### [STAGE 1] P0 核心瓶頸止血與資料層減負稽核 (Data Layer Resilience)

#### 稽核編號：`AUDIT-S1-P0-01`
- **稽核主題**：歷史快照寫入限頻與事件驅動落盤對抗審查
- **審查範圍與目標檔案**：
  - [`core/infrastructure/database/ledger_store.py`](file:///root/grid-trading-console/core/infrastructure/database/ledger_store.py) (L800-L950)
  - [`core/infrastructure/database/async_writer.py`](file:///root/grid-trading-console/core/infrastructure/database/async_writer.py)
- **不可動搖約束守則 (Invariants)**：
  1. `bot_snapshots` (最新單行表) 每次心跳（2s）**必須更新**。
  2. `bot_snapshot_history` 必須受到 `GRID_HISTORY_SNAPSHOT_INTERVAL`（300s）嚴格節流。
  3. 遇重大業務事件（`force_history=True`），歷史表必須立即落盤，嚴禁被節流器吃掉。
- **對抗性質疑者盲區分析**：
  - *開發者盲區 1*：時間回撥（Time Drift）攻擊：若伺服器 NTP 同步導致系統時間回撥 10 秒，`now - last_ts` 將變成負數，節流閥是否會被永久卡死長達數分鐘？
  - *開發者盲區 2*：高併發競態寫入：若有多個協程同時對同一個 `bot_id` 呼叫 `save_snapshot`，節流字典 `_last_history_ts` 是否存在 Race Condition 導致歷史表重複寫入？
  - *開發者盲區 3*：型別模糊：若外部傳入 `force_history="False"`（非空字串在 Python 為 True），系統是否誤判？
- **對抗性破壞驗證腳本 (全新獨立撰寫範例)**：
  ```bash
  python3 -c "
  import time, os
  from core.infrastructure.database.ledger_store import SqliteLedgerStore
  test_db = 'data/test_audit_p0_1_adversarial.sqlite3'
  os.remove(test_db) if os.path.exists(test_db) else None
  s = SqliteLedgerStore(test_db)

  # 破壞測試 1: 模擬極端高頻 100 次併發寫入 (同一秒內)
  for i in range(100):
      s.save_snapshot('adv_bot_1', 'config/test.yaml', {'cycle': i, 'price': 100 + i}, time.time())
  hist = s.load_history('adv_bot_1', limit=200)
  assert len(hist) == 1, f'節流閥在高頻寫入下破防! 歷史表記錄數為 {len(hist)} 筆 (預期 1 筆)'

  # 破壞測試 2: 時間回撥注入 (模擬 NTP 校時倒退 30 秒)
  now = time.time()
  s.save_snapshot('adv_bot_1', 'config/test.yaml', {'cycle': 101, 'price': 200}, now)
  s.save_snapshot('adv_bot_1', 'config/test.yaml', {'cycle': 102, 'price': 201}, now - 30.0)
  # 驗證最新快照是否仍正確更新
  latest = s.load_snapshot('adv_bot_1')
  assert latest['price'] == 201, '最新快照表在時間回撥情境下未能更新!'

  # 破壞測試 3: force_history 邊界測試 (強制落盤)
  s.save_snapshot('adv_bot_1', 'config/test.yaml', {'cycle': 103, 'price': 202}, time.time(), force_history=True)
  hist2 = s.load_history('adv_bot_1', limit=200)
  assert len(hist2) == 2, f'force_history=True 未能強制寫入歷史表! 歷史表記錄數為 {len(hist2)}'

  os.remove(test_db)
  print('AUDIT-S1-P0-01 對抗性破壞測試通過!')
  "
  ```
- **量化驗收門檻 (DoD)**：
  - 常規心跳歷史表寫入頻率壓降 **> 98%**。
  - `force_history=True` 強制穿透率 **100%**。

---

#### 稽核編號：`AUDIT-S1-P0-02`
- **稽核主題**：PostgreSQL 連接池、自動降級與雙軌遷移對抗審查
- **審查範圍與目標檔案**：
  - [`core/infrastructure/database/ledger_store.py`](file:///root/grid-trading-console/core/infrastructure/database/ledger_store.py) (`create_ledger_store`, `PostgresLedgerStore`)
  - [`scripts/migration/sqlite_to_postgres.py`](file:///root/grid-trading-console/scripts/migration/sqlite_to_postgres.py)
- **不可動搖約束守則 (Invariants)**：
  1. 連接池參數必須包含 `pool_size=10`, `max_overflow=20`, `pool_recycle=1800`, `pool_pre_ping=True`。
  2. 若 `DATABASE_URL` 無效或連線超時，**絕對禁止拋出未捕獲例外導致服務崩潰**，必須透明降級至本地 SQLite。
  3. 遷移腳本 `--dry-run` 必須可在無 PostgreSQL 環境下安全執行並精確統計。
- **對抗性質疑者盲區分析**：
  - *開發者盲區 1*：連線超時掛起：若給予一個黑洞 IP（如 `10.255.255.1:5432`），連線探測是否會阻塞主執行緒長達 60 秒導致交易進程假死？
  - *開發者盲區 2*：降級後的 SQLite 是否與原 SQLite 資料庫路徑一致？是否會錯誤建立在 `/tmp` 導致資料永久遺失？
- **對抗性破壞驗證腳本 (全新獨立撰寫範例)**：
  ```bash
  python3 -c "
  import time
  from core.infrastructure.database.ledger_store import create_ledger_store

  # 破壞測試 1: 注入黑洞 URL 與極端無效認證
  t0 = time.perf_counter()
  store = create_ledger_store('postgresql+psycopg://blackhole_user:blackhole_pass@127.0.0.1:54329/non_existent_db')
  t_cost = time.perf_counter() - t0

  assert store.__class__.__name__ == 'SqliteLedgerStore', f'連線失敗未降級為 SQLite，得到 {store.__class__.__name__}'
  print(f'降級耗時: {t_cost:.2f}s (未發生長時間假死掛起)')

  # 破壞測試 2: 驗證降級之 SQLite 具備完整的讀寫持久化能力
  store.save_snapshot('adv_pg_fallback', 'config/fb.yaml', {'status': 'resilient'}, time.time())
  snap = store.load_snapshot('adv_pg_fallback')
  assert snap['status'] == 'resilient', '降級後的 SQLite 無法正常讀寫快照!'
  print('AUDIT-S1-P0-02 對抗性破壞測試通過!')
  "
  ```
- **量化驗收門檻 (DoD)**：
  - 故障 DSN 100% 透明降級至本地生產 SQLite 庫。
  - 無死鎖掛起，無未處理之連線例外穿透。

---

#### 稽核編號：`AUDIT-S1-P0-03`
- **稽核主題**：Redis L1 快照快取讀模型與環境庫隔離對抗審查
- **審查範圍與目標檔案**：
  - [`core/infrastructure/database/redis_support.py`](file:///root/grid-trading-console/core/infrastructure/database/redis_support.py)
  - [`web_ui/backend/runtime_repository.py`](file:///root/grid-trading-console/web_ui/backend/runtime_repository.py)
  - [`web_ui/backend/grid_state_store.py`](file:///root/grid-trading-console/web_ui/backend/grid_state_store.py)
- **不可動搖約束守則 (Invariants)**：
  1. Redis 僅作為 Disposable Cache，離線或 miss 時透明回退 DB，API 嚴禁 500。
  2. 僅生產持久庫允許掛載 Redis 快取；測試臨時庫必須隔離，防止污染線上狀態。
- **對抗性質疑者盲區分析**：
  - *開發者盲區 1*：毒藥快取（Poison Cache）：若 Redis 內的快取資料被手動或第三方寫入非法 JSON 或非字典型別（如整數、字串），讀取時是否會拋出 `TypeError` 導致整個 Web UI 崩潰？
  - *開發者盲區 2*：環境隔離逃逸：若傳入相對路徑 `./data/test.sqlite3`，`is_production_database_path()` 是否能精準判斷而不被欺騙？
- **對抗性破壞驗證腳本 (全新獨立撰寫範例)**：
  ```bash
  python3 -c "
  from core.infrastructure.database.redis_support import RedisStateCache
  from web_ui.backend.runtime_repository import EnvironmentGuard

  # 破壞測試 1: 環境隔離路徑欺騙注入
  assert not EnvironmentGuard.is_production_database_path('/tmp/test_web_ui_runtime.sqlite3')
  assert not EnvironmentGuard.is_production_database_path('data/temp_audit.sqlite3')
  assert EnvironmentGuard.is_production_database_path('data/web_ui_runtime.sqlite3')
  print('環境資料庫路徑探測防禦完好')

  # 破壞測試 2: 毒藥資料注入 Redis (若本機 Redis 有運行)
  cache = RedisStateCache()
  if cache.is_available():
      r = cache._get_client()
      # 惡意注入非 JSON 字串
      r.set('grid:state:poison_bot', 'CORRUPTED_NON_JSON_DATA{{{', ex=60)
      val = cache.get('poison_bot')
      assert val is None, f'毒藥快取未被吞吐吸收，返回了非預期資料: {val}'
      r.delete('grid:state:poison_bot')
      print('Redis 毒藥資料注入防禦測試通過!')
  "
  ```
- **量化驗收門檻 (DoD)**：
  - 畸形快取資料 100% 被安全攔截降級。
  - 快取命中讀取耗時 **< 1ms**。

---

### [STAGE 2] P1 控制面即時化、日誌治理與進程隔離稽核 (Control & Process Pool)

#### 稽核編號：`AUDIT-S2-P1-01`
- **稽核主題**：Redis Streams 即時指令隊列與檔案降級對抗審查
- **審查範圍與目標檔案**：
  - [`core/infrastructure/database/redis_support.py`](file:///root/grid-trading-console/core/infrastructure/database/redis_support.py)
  - [`run_multiple_grids.py`](file:///root/grid-trading-console/run_multiple_grids.py)
  - [`web_ui/backend/routes/grids.py`](file:///root/grid-trading-console/web_ui/backend/routes/grids.py)
- **不可動搖約束守則 (Invariants)**：
  1. 指令派發必須同時寫入 Redis Stream 與本地 JSON，保證無 Redis 環境具備 5 秒檔案輪詢兜底。
  2. 指令消費必須具備等冪性（Idempotency），重複指令不得重複派生協程。
- **對抗性質疑者盲區分析**：
  - *開發者盲區 1*：重放攻擊（Replay Attack）：若網路抖動導致同一條 `command_id` 於 1 秒內連續到達三次，系統是否會重複啟動三組相同的網格？
  - *開發者盲區 2*：畸形 Payload 注入：若有人往 `grid:commands` 寫入缺漏 `action` 或缺漏 `config_path` 的封包，Consumer 是否會拋出未捕獲例外直接終止監聽協程？
- **對抗性破壞驗證腳本 (全新獨立撰寫範例)**：
  ```bash
  python3 -c "
  from core.infrastructure.database.redis_support import RedisCommandQueue
  q = RedisCommandQueue()
  if q.is_available():
      # 破壞測試 1: 注入畸形指令封包
      r = q._client
      r.xadd('grid:commands', {'corrupted_field': 'no_action_here'})
      cmds = q.read_commands(count=10)
      # 驗證隊列消費者讀取時是否穩健跳過或標記錯誤，而不是引發未處理例外
      print(f'讀取 {len(cmds)} 條有效指令，未因畸形封包崩潰')
  "
  ```
- **量化驗收門檻 (DoD)**：
  - 指令調度延遲 **< 10ms (實測 < 1ms)**。
  - 畸形封包不阻斷 Consumer 協程。

---

#### 稽核編號：`AUDIT-S2-P1-02`
- **稽核主題**：PM2 日誌輪轉配置與磁碟空間自動維護對抗審查
- **審查範圍與目標檔案**：
  - [`scripts/maintenance/rotate_logs.sh`](file:///root/grid-trading-console/scripts/maintenance/rotate_logs.sh)
  - PM2 模組 `pm2-logrotate`
- **不可動搖約束守則 (Invariants)**：
  1. 日誌輪轉必須採用 `copytruncate`，嚴禁中斷運作中進程。
  2. 嚴禁無備份刪除 7 天內的日誌；嚴禁誤刪 `data/grid_audit.db`。
- **對抗性質疑者盲區分析**：
  - *開發者盲區 1*：若日誌檔案權限為 `readonly` 或被外部進程 `flock` 鎖定，輪轉腳本是否會掛死？
  - *開發者盲區 2*：`rotate_logs.sh` 的 `find -mtime +7` 是否會誤傷未滿 7 天但時間戳被 `touch` 修改過的日誌？
- **對抗性破壞驗證腳本**：
  ```bash
  # 測試日誌維護腳本對邊界路徑與乾跑模式的保護
  bash scripts/maintenance/rotate_logs.sh --dry-run --retention-days 0
  ```
- **量化驗收門檻 (DoD)**：
  - `pm2 conf pm2-logrotate` 參數精確匹配 `max_size=20M`、`retain=5`、`compress=true`。
  - 清理腳本絕不刪除 `.sqlite3` 與 `.db` 檔案。

---

#### 稽核編號：`AUDIT-S2-P1-03`
- **稽核主題**：主進程監督者與多進程 Worker 隔離及自癒機制對抗審查
- **審查範圍與目標檔案**：
  - [`core/supervisor/master_daemon.py`](file:///root/grid-trading-console/core/supervisor/master_daemon.py)
  - [`core/supervisor/worker_runner.py`](file:///root/grid-trading-console/core/supervisor/worker_runner.py)
- **不可動搖約束守則 (Invariants)**：
  1. 單一 Worker 子進程遭遇 `SIGKILL` (exit code 137) 崩潰時，Master 必須感應並捕獲退出碼，其他 Worker 完全不受干擾（解除 Fate Sharing）。
  2. 對異常崩潰的 Worker 必須啟動指數退避重啟（2s, 4s, 8s... max 60s），手動停止必須豁免。
- **對抗性質疑者盲區分析**：
  - *開發者盲區 1*：重啟風暴（Crash Loop）：若 Worker 設定檔損毀，導致每次啟動在 0.1 秒內立即崩潰，Master 是否會無限制快速 fork 耗盡系統 PID 與記憶體？
  - *開發者盲區 2*：僵屍進程（Zombie Process）：Worker 退出時，Master 是否正確執行 `poll()` 或 `wait()` 釋放進程表項目？
- **對抗性破壞驗證腳本 (全新獨立撰寫範例)**：
  ```bash
  python3 -c "
  from core.supervisor.master_daemon import MasterSupervisor
  import time

  master = MasterSupervisor()
  # 破壞測試 1: 模擬 Worker 惡意頻繁崩潰 (驗證指數退避計算)
  worker_key = 'config/grid/adv_crash.yaml'
  for attempt in range(1, 8):
      delay = master._calculate_backoff(attempt)
      assert delay <= 60.0, f'退避延遲未設上限! 第 {attempt} 次延遲為 {delay}'
      print(f'第 {attempt} 次崩潰退避延遲: {delay:.1f}s')

  # 破壞測試 2: 驗證手動停止的 Worker 不得排程重啟
  master.workers[worker_key] = None
  # 模擬正常手動退出
  "
  ```
- **量化驗收門檻 (DoD)**：
  - 指數退避上限嚴格鎖定在 60s。
  - 子進程異常退出被準確分類，無連鎖反應。

---

### [STAGE 3] P2 安全沙箱體系與專用適配器輕量化稽核 (Security & Adapters)

#### 稽核編號：`AUDIT-S3-P2-01`
- **稽核主題**：低特權帳號與 Systemd 嚴格沙箱配置對抗審查
- **審查範圍與目標檔案**：
  - [`scripts/setup/create_service_user.sh`](file:///root/grid-trading-console/scripts/setup/create_service_user.sh)
  - [`scripts/deployment/crypto-webui.service`](file:///root/grid-trading-console/scripts/deployment/crypto-webui.service)
  - [`scripts/deployment/crypto-engine.service`](file:///root/grid-trading-console/scripts/deployment/crypto-engine.service)
- **不可動搖約束守則 (Invariants)**：
  1. 服務帳號必須為無登入 shell 之專用用戶 `crypto-runner` (`/usr/sbin/nologin`)。
  2. Systemd 必須開啟 `ProtectSystem=strict`、`ProtectHome=true`、`NoNewPrivileges=true`，寫入目錄僅限 `config/`、`data/`、`logs/`。
- **對抗性質疑者盲區分析**：
  - *開發者盲區 1*：符號連結逃逸（Symlink Attack）：若在 `data/` 下建立指向 `/etc/passwd` 的軟連結，沙箱是否能防止任意讀寫？
  - *開發者盲區 2*：環境變數覆寫：Systemd 單元是否保護了環境變數檔案的讀取權限？
- **對抗性破壞驗證腳本**：
  ```bash
  # 語法與沙箱隔離參數合規性對抗檢驗
  systemd-analyze verify scripts/deployment/crypto-webui.service scripts/deployment/crypto-engine.service
  grep -rn "ProtectSystem=strict" scripts/deployment/
  grep -rn "NoNewPrivileges=true" scripts/deployment/
  ```
- **量化驗收門檻 (DoD)**：
  - 沙箱參數 100% 齊備，無特權提升風險。

---

#### 稽核編號：`AUDIT-S3-P2-02`
- **稽核主題**：API 金鑰本機對稱加密儲存 (AES-256-GCM Vault) 對抗審查
- **審查範圍與目標檔案**：
  - [`core/security/vault.py`](file:///root/grid-trading-console/core/security/vault.py)
  - [`tools/encrypt_api_key.py`](file:///root/grid-trading-console/tools/encrypt_api_key.py)
- **不可動搖約束守則 (Invariants)**：
  1. 密文格式必須為 `enc:gcm:<base64(12B nonce + ciphertext + 16B tag)>`。
  2. **安全熔斷 (Invariant 1)**：若檢測到密文但 `GRID_MASTER_KEY` 缺失或密文遭竄改，**必須拋出 `SecurityFatalError` 並拒絕啟動**，嚴禁用空金鑰對交易所下單。
  3. **日誌脫敏 (Invariant 2)**：日誌嚴禁出現解密後明文，必須透過 `mask_secret` 遮蔽。
- **對抗性質疑者盲區分析**：
  - *開發者盲區 1*：密文竄改比特反轉（Bit-Flipping Attack）：篡改密文中的 1 個 byte，AES-GCM 是否 100% 透過驗證 Tag 拋出例外，還是會解密出垃圾明文字串？
  - *開發者盲區 2*：重用 Nonce 攻擊：若連續加密兩次相同的明文，是否產出不同的隨機 Nonce？
  - *開發者盲區 3*：Traceback 洩密：當解密失敗拋出例外時，錯誤訊息或區域變數是否會將 Master Key 或明文印在日誌中？
- **對抗性破壞驗證腳本 (全新獨立撰寫範例)**：
  ```bash
  python3 -c "
  import base64
  from core.security.vault import encrypt_secret, decrypt_secret, VaultDecryptionError, VaultMissingKeyError, mask_secret

  key = '12345678901234567890123456789012' # 32 bytes
  secret = 'MY_SUPER_SECRET_API_KEY_12345'

  # 破壞測試 1: Nonce 獨立性驗證 (相同明文連續加密，密文必須不同)
  c1 = encrypt_secret(secret, key)
  c2 = encrypt_secret(secret, key)
  assert c1 != c2, '致命漏洞: AES-GCM 重複使用了 Nonce!'

  # 破壞測試 2: 密文比特篡改注入
  prefix, b64_payload = c1.split('enc:gcm:')
  raw = bytearray(base64.urlsafe_b64decode(b64_payload))
  raw[-1] ^= 0xFF # 篡改最後 1 個 byte (Auth Tag)
  corrupted_c1 = f'enc:gcm:{base64.urlsafe_b64encode(raw).decode()}'
  try:
      decrypt_secret(corrupted_c1, key)
      raise AssertionError('致命漏洞: 篡改密文未被 AES-GCM Tag 檢測出來!')
  except VaultDecryptionError as e:
      assert secret not in str(e), '例外訊息中洩漏了敏感資訊!'
      print('AES-GCM 篡改防禦成功觸發 VaultDecryptionError')

  # 破壞測試 3: 日誌脫敏遮蔽極限測試
  assert mask_secret('SHORT') == '***'
  assert mask_secret(secret) == 'MY_S...2345'
  print('AUDIT-S3-P2-02 對抗性加密與脫敏測試通過!')
  "
  ```
- **量化驗收門檻 (DoD)**：
  - 篡改密文 100% 被 AESGCM Tag 拒絕。
  - 遺失 Key 或密文毀損 100% 拋出 `SecurityFatalError` 熔斷。

---

#### 稽核編號：`AUDIT-S3-P2-03`
- **稽核主題**：Binance 原生 WS/REST 輕量高速適配器對抗審查
- **審查範圍與目標檔案**：
  - [`core/adapters/exchanges/adapters/binance_fast.py`](file:///root/grid-trading-console/core/adapters/exchanges/adapters/binance_fast.py)
  - [`core/adapters/exchanges/factory.py`](file:///root/grid-trading-console/core/adapters/exchanges/factory.py)
- **不可動搖約束守則 (Invariants)**：
  1. 必須 100% 遵循抽象基類 `ExchangeAdapter` 規範。
  2. 簽名器必須使用原生 CPython C 加速之 `hmac` + `hashlib.sha256`。
- **對抗性質疑者盲區分析**：
  - *開發者盲區 1*：連線洩漏（Connection Leak）：在高頻呼叫下單、撤單時，`aiohttp.ClientSession` 是否有未關閉的 response 或未釋放的連接？
  - *開發者盲區 2*：WebSocket 畸形資料注入：若幣安推送了缺少欄位或價格為 `"NaN"` 的 ticker，適配器是否會拋出未捕獲例外導致協程永久斷線？
- **對抗性破壞驗證腳本 (全新獨立撰寫範例)**：
  ```bash
  python3 -c "
  import asyncio
  from core.adapters.exchanges.adapters.binance_fast import BinanceFastAdapter

  async def test_fast_adapter():
      adapter = BinanceFastAdapter({'apiKey': 'dummy', 'secret': 'dummy'})
      # 破壞測試: 畸形深度行情解析
      corrupted_depth = {'lastUpdateId': 1234, 'bids': [['NaN', '1.0'], ['100.5', 'INVALID']], 'asks': []}
      # 驗證適配器在收到畸形行情時的容錯能力
      await adapter.close()
      print('BinanceFastAdapter 資源正常釋放')

  asyncio.run(test_fast_adapter())
  "
  ```
- **量化驗收門檻 (DoD)**：
  - 單次下單內部簽名耗時 **< 2ms (實測 < 30μs)**。
  - 適配器實例記憶體佔用 **< 40MB (實測 < 2MB)**。

---

### [STAGE 4] P3 高性能多語言交易核心演進稽核 (Polyglot Core & IPC)

#### 稽核編號：`AUDIT-S4-P3-01`
- **稽核主題**：Protobuf 二進位合約、編譯管線與 UDS IPC 客戶端對抗審查
- **審查範圍與目標檔案**：
  - [`proto/trading_core.proto`](file:///root/grid-trading-console/proto/trading_core.proto)
  - [`core/ipc/uds_client.py`](file:///root/grid-trading-console/core/ipc/uds_client.py)
- **不可動搖約束守則 (Invariants)**：
  1. 協議必須採用標準 4-byte big-endian 長度前綴 + Protobuf 二進位 Framing。
  2. 支援同步與非同步 UDS 客戶端，斷線時具備超時與重試保護。
- **對抗性質疑者盲區分析**：
  - *開發者盲區 1*：畸形長度前綴攻擊（Length-Prefix Overflow）：若有人向 Socket 發送 4 個 bytes `0xFFFFFFFF`（聲稱封包大小為 4GB），客戶端是否會嘗試 `read(4294967295)` 導致記憶體 OOM 崩潰？
  - *開發者盲區 2*：半包與黏包（TCP/UDS Fragmentation）：若一個 Protobuf 封包被分成兩段傳輸，Framing 解碼器是否能正確重組還是直接報錯？
- **對抗性破壞驗證腳本 (全新獨立撰寫範例)**：
  ```bash
  python3 -c "
  import struct, socket, os
  from core.ipc.uds_client import TradingCoreClient

  # 破壞測試 1: 測試封包長度上限防禦
  sock_path = '/tmp/test_uds_security.sock'
  if os.path.exists(sock_path): os.remove(sock_path)
  srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
  srv.bind(sock_path)
  srv.listen(1)

  # 模擬客戶端連線後收到畸形 4GB 長度前綴
  srv.close()
  os.remove(sock_path)
  print('UDS 安全防禦邊界驗證完成')
  "
  ```
- **量化驗收門檻 (DoD)**：
  - 協議合約具備明確大小防護，Framing 正確無黏包缺陷。

---

#### 稽核編號：`AUDIT-S4-P3-02`
- **稽核主題**：Go 原生記憶體撮合微引擎、39ns 極速風控與 100 併發壓測驗收對抗審查
- **審查範圍與目標檔案**：
  - [`trading-core-go/engine/orderbook.go`](file:///root/grid-trading-console/trading-core-go/engine/orderbook.go)
  - [`trading-core-go/engine/risk.go`](file:///root/grid-trading-console/trading-core-go/engine/risk.go)
  - [`trading-core-go/server/ipc_server.go`](file:///root/grid-trading-console/trading-core-go/server/ipc_server.go)
  - [`trading-core-go/benchmark_test.go`](file:///root/grid-trading-console/trading-core-go/benchmark_test.go)
- **不可動搖約束守則 (Invariants)**：
  1. 撮合規則必須嚴格遵循價格-時間優先 (FIFO)。
  2. 風控評估必須在撮合前執行；胖手指異常偏離 (> 5%) 或超額下單必須在微秒內立即攔截阻斷。
  3. 在 100 併發高壓下，Go 核心 P99 撮合延遲必須小於 500 微秒 (μs)。
- **對抗性質疑者盲區分析**：
  - *開發者盲區 1*：併發資料競爭（Data Race）：在多個 Goroutine 同時調用下單與撤單時，Go 是否存在未加鎖讀寫（可用 `go test -race` 檢測）？
  - *開發者盲區 2*：自成交（Self-Trade）死鎖：若同一買賣方發出交錯價格單，撮合引擎是否會陷入無限循環？
  - *開發者盲區 3*：浮點數精度損失：在計算部分成交剩餘量時，是否因浮點運算產生微小尾數（如 `0.000000000000001`）導致訂單無法結束？
- **對抗性破壞驗證腳本 (全新獨立撰寫範例)**：
  ```bash
  # 1. 執行 Go 原生資料競爭檢測 (-race)
  cd trading-core-go && go test -race -v ./... && cd ..

  # 2. 執行 100 併發 10 萬筆高壓基準測試 (驗收 P99 與 QPS)
  cd trading-core-go && go run . --benchmark --concurrency 100 --orders 100000 && cd ..
  ```
- **量化驗收門檻 (DoD)**：
  - Go `-race` 測試 **0 Data Race**。
  - 單筆風控評估耗時 **< 100μs (實測 ~39.8 ns)**。
  - P99 撮合延遲 **< 500μs (實測 5.56 ~ 7.13 μs)**。
  - 撮合吞吐量 **> 50,000 QPS (實測 > 300,000 QPS)**。
  - 引擎記憶體佔用 **< 30MB (實測 21~25 MB)**。

---

### [STAGE 5] 全量整合驗收、量化指標對齊與災難復原稽核 (Global Resilience Gate)

#### 稽核編號：`AUDIT-S5-GATE-01`
- **稽核主題**：線上服務活體狀態檢驗與零回歸審查
- **審查範圍與目標檔案**：PM2 服務狀態、FastAPI 端點
- **實體驗證指令集**：
  ```bash
  # 1. 檢查 PM2 服務狀態
  pm2 list
  
  # 2. 實機 API 健康探測 (確保狀態為 live)
  curl -s -H "Authorization: Bearer <TOKEN>" http://localhost:8001/api/v1/system/health
  curl -s -H "Authorization: Bearer <TOKEN>" http://localhost:8001/api/v1/dashboard/snapshot
  ```
- **量化驗收門檻 (DoD)**：
  - PM2 `grid-web-ui` 與 `multi-grid-engine` 均為 `online`。
  - API 回傳 `HTTP 200 OK` 且包含 live 心跳數據。

---

#### 稽核編號：`AUDIT-S5-GATE-02`
- **稽核主題**：8 大技術指標量化達成驗收矩陣審查
- **審查範圍**：全系統關鍵性能指標
- **量化驗收對照矩陣**：

| 關鍵指標 (Key Metrics) | 初始現狀 | P0-P3 目標門檻 | 實測數值 (Actual) | 稽核驗收標準 |
| :--- | :--- | :--- | :--- | :---: |
| **CPU 佔用率 (100 併發)** | 40% ~ 70% | **< 0.5%** | **0.00% ~ 0.05%** | 實測必須低於 0.5% |
| **撮合核心記憶體佔用** | 600MB ~ 1GB | **< 30MB** | **21.46 ~ 24.87 MB** | 實測必須低於 30MB |
| **資料庫每月歷史膨脹** | 5GB ~ 8GB | **< 200MB** | **抽樣降頻 98%** | 300s 限頻生效 |
| **控制指令生效延遲** | 0 ~ 5,000ms | **< 10ms** | **0.17 ms** | 實測必須低於 10ms |
| **單筆風控判定延遲** | 5 ~ 20ms | **< 100μs** | **39.80 ns** | 實測必須低於 100μs |
| **P99 訂單撮合延遲** | 200 ~ 500ms | **< 500μs** | **5.56 ~ 7.13 μs** | 實測必須低於 500μs |
| **撮合核心吞吐量 (QPS)**| 100 ~ 500 筆/秒 | **> 50,000** | **339,834 筆/秒** | 實測必須大於 5 萬 |
| **系統運行特權** | `root` 直接執行 | **低特權沙箱帳號** | **crypto-runner + Systemd** | 嚴格檔案隔離 |

---

#### 稽核編號：`AUDIT-S5-GATE-03`
- **稽核主題**：四大極端災難復原演練 (Disaster Recovery & Resilience)
- **審查項目與情境**：
  1. **資料持久層中斷演練 (DB Outage)**：PostgreSQL 連線失敗，自動退回本地 SQLite，交易進程不中斷。
  2. **通訊佇列崩潰演練 (Redis Crash)**：Redis 離線時，控制面無縫退回 5 秒磁碟檔案輪詢；讀模型無縫退回資料庫查詢，API 零 500 報錯。
  3. **金鑰竄改/主密鑰缺失演練 (Key Vault Failure)**：`GRID_MASTER_KEY` 錯誤或密文字串遭竄改時，Worker 拒絕啟動，拋出 `SecurityFatalError`，杜絕空金鑰毀損單。
  4. **全損狀態對帳重建演練 (Reconciliation Drill)**：透過成交事實表完整重建倉位與快照。

---

## 6. 稽核成果歸檔與簽核流程 (Audit Sign-off Procedure)

當所有階段（Stage 0 至 Stage 5）之稽核員裁決卡均獲判定為 **PASS** 或 **CONDITIONAL PASS**，且每單元均滿足「全新測資清單」與「脆弱點揭露」要求時，總體稽核員方可產出全案簽核報告：

1. 建立 `docs/architecture_evolution_audit_report_YYYYMMDD.md`。
2. 彙整各階段之對抗裁決卡、破壞性注入日誌、量化指標對照表。
3. 彙總所有單元提出之**「潛在架構脆弱點彙整表（Master Fragility Ledger）」**，做為下一代重構之 Backlog。
4. 執行 `graphify update .` 確保最終拓撲圖譜保持同步。
5. 正式簽核並交付主幹合併。

---

*本文件正式歸檔於專案目錄 [`docs/architecture_evolution_audit_standard_zh-TW.md`](file:///root/grid-trading-console/docs/architecture_evolution_audit_standard_zh-TW.md)，作為後續所有 AI 稽核員與工程師執行分段對抗審查、指標驗收與合規裁決之最高權威準則。*
