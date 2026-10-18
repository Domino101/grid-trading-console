---
name: principle-make-operations-idempotent
description: "Apply when designing trading commands, lifecycle loops, rebalances, or accounting migrations that run amid crashes and retries. Converge to the same end state regardless of prior partial runs."
---

# Make Operations Idempotent (操作冪等性架構原則)

設計所有會改變系統狀態的運作（下單重試、部位再平衡、排程執行、資料庫回填、事件寫入），使其**無論執行一次、重複執行兩次、或是前一次執行到一半崩潰重啟，最終系統狀態都精確收斂至相同的正確結果**。

---

## 為什麼這在量化交易中是生死線？
在即時交易與分散式環境中，網路超時、交易所 502、進程崩潰、以及 Redis/WebSocket 斷線重連是日常常態。若不具備冪等性，一次超時重試就會導致雙重下單（Double Spending）、重複扣款、或記帳不平衡。

---

## 核心設計理念

1. **從現有真實狀態衍生，而非從「上次想要做什麼」推導**：
   - 每次執行先讀取當前真實狀態（例如已成交部位、DB 中的 `trade_fills`），與目標狀態比較，**僅套用差異部分**。
   - 這樣即使步驟重複執行，也會因為「無剩餘差異」而安全略過。
2. **唯一業務冪等鍵（Stable Dedupe Key）**：
   - 訂單成交使用交易所訂單 ID（如 Binance `orderId`），**嚴禁使用本地毫秒時間戳作為主鍵**。
   - 可重跑的週期事件（如 `daily_rebalance`、`regime_gate` 觸發）**必帶** `dedupe_key`。
3. **安全自癒殘留狀態**：
   - 上一次崩潰留下的未完成記錄、臨時檔案或鎖，必須能被下一次執行辨識為過期（Stale）並自動清理或覆蓋。

---

## 自我檢驗 3 問 (The Idempotency Test)
1. **若這段程式碼連續執行兩次會發生什麼事？**
2. **若前一次執行在任意一行崩潰（如網路中斷），下一次重跑會如何？**
3. **重新執行是否必然收斂至同一個終端狀態？**

若任何一個答案是「取決於留下了什麼殘留狀態」，則該操作必須重構並引入對帳與差異對齊步驟（Reconciliation Step）。
