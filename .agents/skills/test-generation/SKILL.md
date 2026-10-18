---
name: test-generation
description: "Automated test generation and TDD workflow for grid-trading-console. Follows Test-Driven Development (TDD) and 'Test Behavior, Not Implementation' principle. Use when adding tests, creating regression tests, or fixing bugs."
---

# Test Generation & TDD Workflow (行為驅動測試與 TDD 規範)

本技能指導 `grid-trading-console` 的測試撰寫與自動生成，落實 **TDD（測試驅動開發）** 與 **「測試行為，而非內部實作（Test Behavior, Not Implementation）」** 核心原則。

---

## 1. 核心原則：測試行為，而非內部實作

測試是針對**呼叫方/使用者可觀察到的行為承諾**，而非私有內部呼叫結構。
以兩個假想情境檢驗每一個測試：
1. **破壞行為（Break Behavior）**：若讓程式碼回傳錯誤數值、跳過訂單下單、或產生重複事件，**測試必須立即報警失敗**。若依然通過，說明斷言過於寬鬆或形同虛設。
2. **重構內部（Change Internals）**：保持所有對外觀察結果（API 回傳、DB 寫入、事件廣播）不變，僅重構內部 helper 或私有方法，**測試必須持續綠燈通過**。若測試失敗，說明測試綁死了實作細節，徒增日後維護負擔。

---

## 2. Bug 修復的 TDD 工作流 (Red-Green-Refactor)

修復 Bug 時，凡有本機可快速執行的測試路徑，**必須先讓錯誤可被重現與執行**：
1. **定位問題**：確認預期行為與當前異常路徑。
2. **先寫出會失敗的最小回歸測試**：
   - 在 `tests/` 新增聚焦於該 Bug 的最小測試案例。
   - 斷言應體現規格契約（Contract），而非照抄當前的錯誤行為。
3. **在修復前運行測試**：
   - 執行測試確認其**確實因該 Bug 失敗（Red）**。若直接通過或因無關原因崩潰，修正測試本身。
4. **最小化修復程式碼**：
   - 僅做滿足預期行為所需的最小生產程式碼修改。
5. **重跑回歸測試**：
   - 確認測試成功通過（Green），並執行周邊全套測試防範次生災害。

---

## 3. 本專案測試執行命令

```bash
# 1. 官方整合 CI 測試（Python + 前端建置與測試）
./scripts/ci.sh

# 2. Python unittest 執行單一或目標模組（專案主力規範）
python3 -m unittest tests.test_dca_ladder
python3 -m unittest tests.services.grid.backtest.test_simulator
python3 -m unittest tests.test_accounting_path_policy

# 3. pytest 模式（亦完全支援）
python3 -m pytest tests/services/grid/backtest/test_risk.py -v

# 4. 前端測試 (React 19 + Vitest)
cd web_ui/frontend && npm test
```

---

## 4. 關鍵驗證領域與防線
- **統一記帳不變式**：寫入操作必須符合 `StrategyLedger` 規範，禁止直連 SQLite。
- **網格/DCA 運算邊界**：最小名目價值（min_notional）、價格/數量小數位精度四捨五入、網格間距重疊驗證。
- **風控守門（Safety Gates）**：`paper_trading` 開關強制檢查、熔斷機制、保證金與最大部位限制。
- **交易所 Adapter**：Mock REST 與 WebSocket 酬載，測試斷線自動重連與狀態自癒。
