# Changelog

本專案的重要變更記錄於此檔案，格式參考 [Keep a Changelog](https://keepachangelog.com/zh-TW/1.1.0/)。

## [Unreleased] — 2026-08-13

### Added

- 新增批次總覽 API：`GET /api/v1/dashboard/snapshot`，以單一快照回傳全域 KPI 與策略卡片資料，移除 Dashboard 對每張卡片的 N+1 stats 輪詢模式。
- 新增 `live`、`stale`、`unavailable` 與 `degraded` 資料品質標示；未知 runtime 數值維持 `null`，不再以零值偽裝真實帳戶資料。
- 新增 SQLite runtime snapshot 與 7 天歷史資料保存，支援 API 重啟後恢復最後快照，以及策略詳情頁 24H / 7D 淨盈虧曲線。
- 新增持久化 grid lifecycle command journal：command id、來源、原因、狀態、時間與 idempotency key。
- 新增 command API：
  - `GET /api/v1/commands/{command_id}`
  - `GET /api/v1/commands?grid_id=...`
  - `GET /api/v1/commands/events?grid_id=...`
- 引擎新增機器可讀的 `GRID_LIFECYCLE` running/stopped/failed 訊號；ProcessManager 會將訊號寫入 command 與 engine event timeline。
- 新增策略詳情工作台：網格/風控摘要、資產與收益、PNL 曲線、命令稽核時間線與引擎事件。
- 新增三步式建立策略流程：市場、網格、風控確認；支援 PAPER/LIVE、交易對規則讀取、格數/格距換算、名義金額提示與 LIVE typed confirmation。
- YAML 新增 `account_mode: paper | live`。
- 新增 LIVE feature flag：`GRID_LIVE_TRADING_ENABLED=true` 才允許建立 LIVE 策略。
- LIVE 建立前新增後端 market rule 驗證：交易對存在性、min notional、價格精度、數量精度；驗證證據寫入 audit params。
- 新增公開、無敏感資訊的 `GET /healthz` readiness probe。
- 新增 systemd service 模板：`scripts/deployment/crypto-webui.service`。
- 新增部署與 release 稽核清單：`docs/web_ui_deployment_and_release_checklist_zh-TW.md`。
- 新增 runtime repository、command repository、PAPER lifecycle acceptance 與 HTTP contract tests。

### Changed

- 瀏覽器端 API 改為同源相對 URL；Vite 開發環境以可設定 proxy 轉發 `/api` 與 `/ws`，不再讓遠端瀏覽器請求 localhost。
- Dashboard 改為顯示 `starting`、`stopping` 與 command failure/timeout，而非把 `active_grids.json` 的 desired state 直接視為策略已運行。
- Start / Stop 現在使用 idempotency key，並以 `accepted → pending_engine → running/stopped/failed/timed_out` 呈現實際命令進度。
- GridDetail 使用 lazy loading，避免圖表依賴增加 Dashboard 首屏 bundle。
- 正式部署建議從 `nohup` 流程遷移至 systemd service 與 `/healthz` 健康檢查。

### Security

- LIVE 策略建立預設關閉，必須由部署端明確開啟 feature flag。
- LIVE 建立需經 server-side market rule 驗證；前端驗證僅作為使用體驗輔助。
- 建立策略 UI 對 LIVE 模式要求輸入 `CONFIRM LIVE`。
- 操作命令、引擎事件與 market validation evidence 可持久化稽核。

### Testing

- `tests/test_runtime_repository.py`：runtime snapshot/history persistence。
- `tests/test_command_repository.py`：command、idempotency、timeout 與 engine event persistence。
- `tests/test_paper_grid_lifecycle_e2e.py`：PAPER create/start/engine confirmation/restart recovery/stop/audit durable flow。
- `tests/test_web_ui_http_contract.py`：health probe、authenticated command/event API 與 PAPER YAML contract。

> HTTP contract tests 需要先安裝 `requirements.txt` 中的 FastAPI 相依套件；最小 sandbox 環境會安全 skip，CI/release 環境必須完整執行。
