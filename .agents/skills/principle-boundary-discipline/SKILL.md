---
name: principle-boundary-discipline
description: "Concentrate validation, type narrowing, and error handling at system boundaries (REST, WS, CLI, external exchange APIs). Trust internal types unconditionally and keep business logic in pure functions."
---

# Boundary Discipline (邊界防禦與內部純粹原則)

將資料驗證、型別收斂與防禦性錯誤處理**嚴格集中在系統邊界**；系統內部則無條件信任型別，交易業務邏輯一律實作為純函式（Pure Functions）。

---

## 為什麼需要邊界防禦？
散落在業務深處的各類 `if x is not None` 或格式檢查是雜音，不但降低程式碼可讀性，還會給人一種「已經做過驗證」的虛假安全感。

---

## 架構分層模式

1. **系統邊界層（System Boundaries）**：
   - 範疇：FastAPI 端點輸入、CLI 參數、設定檔解析、WebSocket 接收器、外部交易所 API 回傳。
   - 職責：**防禦、解析與型別收斂**。將非結構化資料（如未定型 JSON、字串）轉換為強型別 Domain 物件（Dataclass / Pydantic 模型）。若資料不合規，立即在邊界阻斷並回報清晰錯誤。
2. **核心領域層（Internal Core）**：
   - 範疇：網格計算器、DCA 階梯引擎、風控規則、投資組合權重再平衡。
   - 職責：**信任內部型別**。不進行重複的防禦性檢查。
   - 交易邏輯盡量以**純函式**表達（輸入行情與配置，輸出訂單計畫），不相依外部框架（如 FastAPI 或資料庫連線），便於快速編寫單元測試。

---

## 檢驗準則
- **「這筆資料現在是否正在跨越系統邊界？」** 若否，在此處重複進行型別或空值檢查是冗餘的。
- **「這段運算邏輯是否能抽成純函式，讓外部框架只負責呼叫？」** 若是，立即抽離。
