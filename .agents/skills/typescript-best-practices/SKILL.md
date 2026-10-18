---
name: typescript-best-practices
description: "TypeScript and React 19 best practices for web_ui/frontend. Emphasizes discriminated unions, branded types, boundary validation, and avoiding arbitrary type casts."
paths: ["web_ui/frontend/**/*.ts", "web_ui/frontend/**/*.tsx"]
---

# TypeScript & React Best Practices (前端型別紀律與工程規範)

本技能適用於 `web_ui/frontend` 中所有 `.ts` 與 `.tsx` 檔案，旨在將型別系統作為系統正確性的第一道防線。

---

## 核心規則對照表

| 規範項目 | 具體要求 |
|---|---|
| **可辨識聯合 (Discriminated Unions)** | 使用具備字面量標籤（如 `kind: "grid" \| "dca" \| "basket"`）的可辨識聯合型別，嚴禁大量可選欄位（Optional Bag）的鬆散物件。 |
| **品牌型別 (Branded Types)** | 為領域關鍵識別字加上品牌型別（如 `type BotId = string & { readonly __brand: "BotId" }`），防止在多個 ID 傳參時混淆。 |
| **邊界驗證 (Boundary Validation)** | 從後端 API 或 WebSocket 進來的外部資料一律標為 `unknown`，在接收邊界透過 Schema（如 Zod）解析後再進入狀態管理。內部嚴禁使用 `any`。 |
| **嚴禁隨意轉型 (No arbitrary `as`)** | 每一個 `as SomeType` 都是潛在的運行態崩潰。先使用型別守衛（Type Guards）縮減型別，只有驗證確認後才允許轉型。 |
| **窮舉性檢查 (Exhaustiveness Checking)** | 在 `switch (action.kind)` 的 `default` 分支中寫入 `const _exhaustive: never = action;`，確保當新增 Action 或狀態時，編譯器能自動標出漏掉的處理分支。 |
| **優先使用 `satisfies`** | 使用 `satisfies` 驗證物件字面量符合介面，同時保留精確的字面量型別，而非使用 `as` 擴大型別。 |
| **物件參數勝於位置參數** | 函式參數超過 2 個時，一律封裝為單一物件傳遞（`{ botId, symbol, amount }`），提升自解釋性與重構安全性。 |
| **拒絕過度 Mocking** | 元件測試使用真實的 React Testing Library 與 Vitest，避免 Mock 掉內部狀態機制。 |
