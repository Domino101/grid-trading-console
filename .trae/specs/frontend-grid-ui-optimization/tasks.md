# Tasks

- [x] Task 1: 診斷當前前端架構與 Hermes API 相容性
  - [x] SubTask 1.1: 檢查 `web_ui/backend/routes/grids.py` 提供的 API 結構與回傳格式。
  - [x] SubTask 1.2: 盤點前端 `web_ui/frontend/src/services/api.ts` 需新增的端點（CRUD、Start/Stop）。

- [x] Task 2: 實作 Web UI 持久化服務 (Systemd / PM2)
  - [x] SubTask 2.1: 建立 `crypto-webui.service` systemd unit 檔案或配置 PM2，設定工作目錄為 `web_ui/backend`。
  - [x] SubTask 2.2: 確保服務包含前端靜態資源的正確掛載（若 FastAPI 使用 StaticFiles），並設定 `Restart=always`。

- [x] Task 3: 重構前端 API 服務層 (`api.ts`)
  - [x] SubTask 3.1: 新增 `createGrid`, `getGrids`, `getGridStats`, `startGrid`, `stopGrid`, `deleteGrid` 等方法。
  - [x] SubTask 3.2: 定義對應的 TypeScript Interface (e.g., `GridInfo`, `GridStats`)。

- [x] Task 4: 開發結構化網格參數設定介面 (Grid Creation Form)
  - [x] SubTask 4.1: 取代原有的純文字 Configs 元件，改為包含幣對、價格上下限、網格數量、模式等欄位的表單。
  - [x] SubTask 4.2: 串接 `createGrid` API，並提供表單驗證與成功/錯誤提示。

- [x] Task 5: 升級即時監控儀表板 (Dashboard)
  - [x] SubTask 5.1: 整合 `getGrids` 與 `getGridStats`，在 Dashboard 顯示每個網格的獨立卡片。
  - [x] SubTask 5.2: 在卡片中精準呈現 PNL、交易次數、價格區間、每格大小等資訊。
  - [x] SubTask 5.3: 在卡片上實作獨立的 Start / Stop / Delete 按鈕。

# Task Dependencies
- [Task 3] depends on [Task 1]
- [Task 4] depends on [Task 3]
- [Task 5] depends on [Task 3]
