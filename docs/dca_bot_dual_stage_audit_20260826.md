# DCA 機器人雙軌稽核工作流報告（Phase 1–7 全案）

> 日期：2026-08-26｜分支：`arena/01a03d63-grid-trading-console`
> 依 `AGENTS.md` §Dual-Stage Audit Standard：Stage 1 宏觀（graphify）→ Stage 2 微觀（line-level review）→ 合併結構化報告。
> 對照：`docs/dca_bot_audit_report_zh-TW.md`（五段式最終稽核）、`docs/dca_bot_phase7_worklog_20260826.md`。

---

## Stage 0：工具就緒

| 工具 | 狀態 | 備註 |
|---|---|---|
| graphify | ✅ 已安裝並更新圖譜 | `pip install graphifyy --break-system-packages`；`graphify update .` 後圖譜 8272 nodes / 15783 edges / 432 communities（含全部 DCA 檔案，先前圖譜未涵蓋 Phase 7 新檔） |
| ocr (open-code-review) | ⚠️ CLI 不可安裝 | 上游為 alibaba/open-code-review 之 Go 二進位，本沙箱無 `go`、pip/npm 皆無對應套件。改採該 skill 之 **Delegation Mode**（host agent 依其嚴重度分級手動執行 line-level review pass） |

---

## Stage 1：宏觀架構（graphify）

### 1.1 DcaCoordinator 影響半徑（`graphify explain "DcaCoordinator"`）

- **唯一進入點**：`run_single_dca_task()`（`run_multiple_grids.py:L549`）→ 確認 DCA 任務由監督者以獨立 task 承載，符合 Phase 3 P0「task 隔離」不變式。
- **上游呼叫者**：僅 `run_single_dca_task` 與測試（`build_coordinator`、`test_full_round_inv1_to_inv4`、`_drive_to_averaging`）→ 無網格核心對 DcaCoordinator 的反向依賴。
- **下游使用**：`EntryMonitor` / `DirectionLock` / `TakeProfitManager`（皆 DCA 命名空間內），狀態轉移經 `_transition()` / `_fail()`。
- 共 49 條連結、29 條集中於 `dca_coordinator.py` 自身 → 內聚高、跨層外溢低。

### 1.2 詳情快照資料流（`graphify query`）

- `DcaCoordinator.open_orders_snapshot()/fills_snapshot()/ladder_slots_snapshot()`（L779+）
  → `run_multiple_grids.py` 心跳 `publish_runtime`
  → `RuntimeSnapshotRepository`（`web_ui/backend/runtime_repository.py:L19`）
  → `routes/dca.py` 之 orders/fills/ladder 端點
  → 前端 `services/api.ts` → `DcaDetail.tsx`。
- `DcaDetail.tsx` → `RuntimeSnapshotRepository` 之 `graphify path` 回「無靜態路徑」→ 符合預期：前後端以 HTTP 邊界解耦，無跨語言靜態呼叫邊，資料契約由 `test_dca_frontend_contract.py` / `test_dca_http_contract.py` 保證。

### 1.3 網格核心邊界（回歸確認）

- `TakeProfitManager`（DCA，`core/services/dca/coordinator/take_profit_manager.py`）與網格 `take_profit/take_profit_manager.py` 為**不同 community**、互不連通 → DCA TP 邏輯未污染網格 TP。
- `git diff --stat core/services/grid/ run_grid_trading.py` 為空 → 宏觀與 diff 雙重確認 7.14 網格零回歸。

**Stage 1 結論**：模組拓撲符合計畫分層（DCA 命名空間自足、經監督者單點接入、經 HTTP 邊界向前端投影），無非預期跨層依賴或對網格核心的侵入。

---

## Stage 2：微觀行級審查（Delegation Mode，依 ocr 嚴重度分級）

審查範圍：本輪 diff 之全部 DCA 相關手寫原始碼（排除 `graphify-out/` 生成物）。

### Critical / High
- **無**。逐項確認：
  - `open_orders_snapshot`/`fills_snapshot`/`ladder_slots_snapshot` 皆為唯讀導出，不改狀態、不下單 → 無新增下單/撤單路徑，不影響 armed 零掛單不變式。
  - 心跳快照導出以 `try/except` 包覆（`run_multiple_grids.py`），失敗僅 warning、不阻斷 `publish_runtime` → 無因詳情功能導致引擎心跳中斷之風險。
  - 所有引用之模型屬性/方法（`fee_rate`、`is_active_on_exchange`、`is_filled`、`average_fill_price`、`filled_safety_count`、`SlotStatus.*`、slot `actual_price`/`filled_price`）均經 grep 確認存在，無 AttributeError 隱患。

### Medium
- **M-1（已緩解）**：`_apply_fill_effects` 為顯示層 `_fills` 另算 `fee = price*qty*fee_rate`。此值僅供詳情「成交」tab 顯示，與 `_realize_profit`/`_total_fees` 的實際會計路徑分離，無雙重扣費風險。已於程式碼註解標明「顯示層」。建議未來若前端要加總手續費，應改用引擎會計值而非顯示值以免口徑分歧。
- **M-2（設計決策）**：`routes/dca.py` orders/fills 端點在 `runtime is None` 時回 `available:false`，但 runtime 存在卻無對應欄位時回 `available:true` + 空陣列。此區分刻意（armed 零掛單須顯示為「可用且為空」而非「不可用」），已由 `test_orders_endpoint_unavailable_without_runtime` 與 armed 語意測試雙向覆蓋。

### Low
- **L-1**：`ladder_slots_snapshot` 對 base slot 以字面 `"base"`/`"safety"` 角色字串、狀態字串硬編碼；與前端 `dcaSlotStatusMeta` 的 key 對應由 `test_dca_frontend_contract.py` 契約測試保證，但兩端字面量無共享來源。可接受（跨語言邊界常態），列為 P2 觀察。

### 邊界/安全複核
- **憑證外洩**：`git diff` grep `API_KEY|SECRET|TOKEN|PRIVATE` 僅命中 `.gitignore` 之 `.grid_api_token` 條目與 CSS 設計 token 註解，無實際憑證 → 7.18 PASS。
- **金額型別**：核心層 `_fills`/快照在**顯示邊界**才轉 `float`（符合 §6.1「float 僅限 API/UI 邊界」）；核心會計仍為 `Decimal`。
- **新依賴**：無（純 stdlib + 既有型別）。

**Stage 2 結論**：無 Critical/High；2 項 Medium 皆為已緩解的設計決策並有測試覆蓋；Low 1 項列為觀察。

---

## 合併結論

| 面向 | 結果 |
|---|---|
| 宏觀拓撲（Stage 1） | 分層正確、DCA 自足、網格零侵入 |
| 微觀行級（Stage 2） | 0 Critical/High；2 Medium 已緩解；1 Low 觀察 |
| P0 稽核（7.1–7.21） | 全數 PASS（詳見 `dca_bot_audit_report_zh-TW.md` §2） |
| CI | `bash scripts/ci.sh` EXIT 0（backend 234 + 前端 test/lint/build 全綠） |
| 網格回歸 | `git diff` 網格核心為空 |
| LIVE | 維持關閉 |

**放行建議**：Phase 7 及全案 Phase 1–7 通過稽核，可合入 PAPER 環境。LIVE 放行需另依稽核標準 §2 完成前置條件，不在本輪範圍。
