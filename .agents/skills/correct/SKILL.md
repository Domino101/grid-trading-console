---
name: correct
description: "Find the mistakes agents keep repeating in this repo and make each one impossible. Try architecture first, then types, then a lint whose error names the fix, then a test, and write docs last. Use for /correct or whenever the human operator corrects you."
---

# Correct (重複錯誤結構化消滅與規則固化)

當工程師在專案中重複糾正 AI Agent 犯下的相同錯誤時，**不要僅僅道歉或在對話中承諾「下次會注意」**。修改專案的結構與檢查機制，讓下一次 Agent 根本無法再犯。

假定任何未來的貢獻者（包含 AI Agent）都只會看到它打開的少數檔案、抄襲最近的範例、並選擇能通過編譯的最短路徑。設計程式庫架構，讓「局部看起來正確的寫法」在全域也是正確的。

---

## 1. 錯誤分類 (Identify Mistake Classes)
- 閱讀近期的 Git Commits、Reverts、Review 評論、以及解釋 workaround 的註解。
- 將錯誤歸納為類別（一旦同一模式發生過兩次，即構成一個類別）。
- 例如：
  - *繞過統一記帳直連 SQLite*（曾發生過 $\rightarrow$ 透過 `tests/test_accounting_path_policy.py` 靜態掃描阻斷）。
  - *未經許可改動網格核心*（曾發生過 $\rightarrow$ 透過 `tests/_grid_guard.py` 白名單阻斷）。
  - *多執行緒下換倉導致幽靈持倉*（透過冪等鍵與唯一寫入者消滅）。

---

## 2. 由最高生效層級消滅錯誤 (The Hierarchy of Elimination)

按照下列優先順序由高至低消滅該類別錯誤：

1. **架構消除（Architecture）**：
   - 給予每個狀態唯一的持有者（Single Owner），每個任務唯一的支援路徑。
   - 隱藏內部實作，讓錯誤的 import 直接失敗。
   - 刪除舊的相容層與死碼，避免 Agent 誤抄。
2. **型別與靜態檢查強制（Types & Linter）**：
   - 讓錯誤狀態在型別系統中「無法被建構（Unrepresentable）」。
   - 若程式碼仍能執行，增加 Lint 或 CI 靜態檢查（如 AST 分析），且錯誤訊息**必須直接指出應該使用的正確函式或檔案**。
3. **行為回歸測試（Behavioral Tests）**：
   - 撰寫測試，證明當該錯誤發生時測試必然爆紅（Fail loud），修復後必然綠燈。
4. **文件與 Agent Rules（Docs & Rules）**：
   - **放最後**。只有在需要高度人為價值判斷時才寫入文件或 Prompt。當 Agent 跳過文字時沒有任何機制會阻擋它，因此文字規則防禦力最低。

---

## 3. 維護規則對應表 (Keep the Enforcement Table)

當被使用者糾正時，立即執行修正，並在專案規則檔（如 `AGENTS.md`）維護「規則 ↔ 守門機制」對照表：

| 規則（Rule） | 強制守門機制（Enforcement Mechanism） | 守門層級 |
|---|---|---|
| 單一記帳路徑（禁止直連 SQLite） | `tests/test_accounting_path_policy.py` | 靜態 AST 檢查 (Level 2) |
| 網格核心改動限制 | `tests/_grid_guard.py` 白名單 | CI 單元測試 (Level 3) |
| 重平衡事件不重複觸發 | DB `bot_events` 表 `dedupe_key` 唯一約束 | 架構與資料庫 (Level 1) |

若發現規則早已列在文件中，但沒有任何機制在守護它，這屬於「重複犯錯」，必須在同一批變更中補上最高層級的強制機制。
