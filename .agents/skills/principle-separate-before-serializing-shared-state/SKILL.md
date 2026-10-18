---
name: principle-separate-before-serializing-shared-state
description: "Apply when concurrent actors (trading loops, rebalancer, reconciler) might write to shared state. Eliminate sharing first; serialize structurally only when one shared writer is a real invariant."
---

# Separate Before Serializing Shared State (先隔離共享狀態，再考慮序列化)

當並行執行的交易程序（如：多網格引擎、DCA 執行緒、定時再平衡任務、對帳修復 Reconciler）需要存取可變狀態時，**第一優先是消除共享**。若不可避免共享，必須透過結構化機制（資料庫交易、單一寫入者角色、鎖）進行嚴格序列化。

口頭約定或文件指引絕不能作為並發控制手段。

---

## 為什麼這在交易控制台中至關重要？
並行寫入共享狀態（如兩個執行緒同時更新同一個機器人的記憶體持倉字典，或併發寫入沒有交易鎖的 SQLite）會引發難以重現的競爭條件（Race Conditions），進而造成重複下單、部位計算錯誤或資料庫鎖死（Database Locked）。

---

## 實踐步驟

1. **識別共享的可變狀態**：
   - 多個協程/進程同時讀寫的記憶體物件、資料庫表或暫存檔案。
2. **預設動作：徹底消除共享寫入目標**：
   - 詢問自己：*這兩個角色真的需要修改同一份物件嗎？還是他們只是在各自發布獨立的事實？*
   - 為每個角色賦予獨立的目錄、獨立的狀態 Key 或專屬的事件串流，僅在最後的彙總/讀取端點進行聚合。
3. **若共享寫入是不變式（Real Invariant），採用結構化序列化**：
   - **單一寫入埠**：如本專案要求所有記帳寫入一律通過 `StrategyLedger`。
   - **明確結算權**：如籃子持倉平倉只由 `apply_basket_fill()` 擁有結算權，名單同步程序不得覆寫持倉。
   - **樂觀鎖 / 資料庫原子操作**：以資料庫原子更新（`UPDATE ... WHERE version = x`）取代應用層脆弱的記憶體鎖。
