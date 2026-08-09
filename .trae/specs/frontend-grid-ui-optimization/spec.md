# Frontend Grid UI Optimization Spec

## Why
目前的前端（Web UI）缺乏結構化的參數設置介面，僅能透過純文字編輯 YAML 檔案；且網格管理與監控仍依賴舊版的 `process_manager` 與日誌解析，未能使用已開發的 Hermes Grid API v1 來獲取精確的即時收益（PNL）、交易次數、價格區間等資訊。此外，Web UI 的啟動目前依賴 `nohup`，缺乏系統級的持久化運行保障。為了提升用戶體驗與系統穩定性，需進行前端重構與部署優化。

## What Changes
- **BREAKING**: 前端將棄用舊版的 `/api/processes` 與日誌解析模式，全面遷移至 Hermes Grid API v1 (`/api/v1/grids/*`)。
- 新增結構化的「創建網格」表單介面（包含幣對、價格上下限、網格數量等參數）。
- 新增/更新 Dashboard，以卡片或數據圖表精確展示各網格的即時狀態（PNL、交易次數、價格區間、單格利潤等）。
- 為 Web UI（包含前端靜態資源與 FastAPI 後端）建立 `systemd` 服務或 PM2 配置，確保開機自啟與崩潰重啟。
- 支援網格的分別開啟、停止（暫停通常需依賴停止並保留狀態，未來由後端完整支援，前端先實作 Start/Stop 介面）。

## Impact
- Affected specs: 網格參數設定、網格狀態監控、系統服務持久化。
- Affected code: 
  - `web_ui/frontend/src/services/api.ts`
  - `web_ui/frontend/src/components/Configs.tsx` (將轉為 GridForm)
  - `web_ui/frontend/src/components/Dashboard.tsx`
  - 新增 `web_ui.service` (systemd) 或更新 ecosystem config。

## ADDED Requirements
### Requirement: Structured Grid Creation Form
The system SHALL provide a UI form to input grid parameters and create new grids via POST `/api/v1/grids`.

#### Scenario: Success case
- **WHEN** user fills out the grid parameters (e.g., ETH/USDC, 1800-3100, 100 grids) and submits
- **THEN** a new grid is created, saved, and appears in the dashboard.

### Requirement: Real-time Grid Monitoring
The system SHALL display real-time statistics for each grid using GET `/api/v1/grids` and `/api/v1/grids/{id}`.

#### Scenario: Success case
- **WHEN** user views the dashboard
- **THEN** the UI displays precise PNL, trade counts, and grid range details fetched from the Hermes API.

### Requirement: Persistent UI Service
The Web UI SHALL run as a persistent background service managed by the OS.

#### Scenario: Success case
- **WHEN** the server reboots or the process crashes
- **THEN** the Web UI service restarts automatically.

## MODIFIED Requirements
### Requirement: Grid Lifecycle Management
Modify the existing start/stop logic to use POST `/api/v1/grids/{id}/start` and `/api/v1/grids/{id}/stop` instead of the generic process starter.

## REMOVED Requirements
### Requirement: Raw YAML Editor for Configs
**Reason**: Error-prone and poor user experience.
**Migration**: Replaced by the structured creation form (though advanced users might still modify files manually, the primary UI will be form-based).
