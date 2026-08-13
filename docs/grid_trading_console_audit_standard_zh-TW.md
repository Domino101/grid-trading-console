# 網格交易總控台稽核標準

> 版本：1.0｜日期：2026-08-13｜適用範圍：Web UI、FastAPI API、網格引擎整合、部署與操作流程

## 1. 目的與原則

本標準用於每次網格交易總控台的功能開發、部署前審查與事故復盤。交易介面是高風險系統；**畫面美觀、API 回傳 200 或設定檔存在，都不能作為策略已安全運行的證據**。

稽核優先序：

1. 資金與交易安全
2. 狀態、PNL、訂單資料正確性與可追溯性
3. 指令可靠性、可恢復性與可觀測性
4. 效能、可用性與資訊清晰度
5. 視覺一致性與便利性

## 2. 稽核等級與放行規則

| 等級 | 定義 | 放行規則 |
|---|---|---|
| P0 阻斷 | 可能錯誤下單、誤導 LIVE/PAPER、錯報資金/PNL、洩漏憑證或導致操作不可恢復 | 未修正不得部署，不得開啟實盤功能 |
| P1 高 | 會造成狀態不一致、重複執行、重大性能退化或無法追查重要操作 | 預設不得正式上線；例外需風險接受紀錄與限時修復 |
| P2 中 | 功能不完整、局部錯誤處理、RWD/無障礙缺陷 | 可於功能旗標或 PAPER 模式上線，需列入下一個迭代 |
| P3 低 | 文案、視覺、非關鍵便利性問題 | 排入產品 backlog |

**實盤放行的最低條件**：無未解 P0/P1、PAPER E2E 通過、命令與審計可追溯、資料 freshness/來源可見、回滾方案已演練。

## 3. 稽核檢核表

### A. 資料正確性與時效（P0/P1）

- [ ] 每個 PNL、費用、餘額、成交量、持倉均有 `source` 與 `as_of/last_updated_at`。
- [ ] `live`、`stale`、`unavailable` 清楚區分；未知資料不得以 `0`、空綠色狀態或「正常」代替。
- [ ] 總覽 KPI 與各策略卡片使用同一時間點/同一讀模型；總計能與策略明細對帳。
- [ ] LIVE 與 PAPER 資產、PNL、成交量不得混算；模式在每一張策略卡與總覽中可見。
- [ ] 金額採 Decimal 或最小貨幣單位/字串傳遞；格式化不改變原始對帳值。
- [ ] stale 閾值、時鐘來源與時區被定義且有測試；資料過期時禁止顯示「正常運行」。
- [ ] API、引擎或交易所斷線時，UI 保留最後有效資料並標明時間，不製造假即時數據。

### B. 指令、訂單與生命週期（P0）

- [ ] 策略狀態採明確 transition：`draft → starting → running → stopping → stopped`，並處理 `degraded/error/stale`。
- [ ] `auto_start`、寫入 YAML、寫入 active state 與引擎確實啟動不可混為同一成功狀態。
- [ ] Start/stop/cancel/close/delete 各自有語意、權限、確認文案與失敗處理；「停止」是否撤單/平倉必須清楚。
- [ ] 每個 mutation 有 `command_id`、idempotency key、actor/source/reason、accepted/result timestamps。
- [ ] 重送/重點同一操作不會建立重複策略、重複程序或重複下單。
- [ ] 刪除只允許策略已停止、未處理命令已完成，並符合未掛單/未持倉的後端限制。
- [ ] UI 只在引擎 heartbeat/確認後呈現 `LIVE/running`；指令進行中顯示 `starting/stopping`。

### C. 風控與權限（P0/P1）

- [ ] Live 操作預設 feature flag 關閉；PAPER 流程獨立驗收後才可開啟。
- [ ] 下單前後端皆驗證交易對、精度、min notional、價格區間、槓桿、資金與交易所能力。
- [ ] 高風險操作需 typed confirmation 或二次確認；確認內容含策略、mode、撤單/平倉影響。
- [ ] API CORS 使用明確 allowlist，不得為 `*` 搭配 credentials。
- [ ] 認證 token 不記錄於 log、不寫入 URL；採 Secure/HttpOnly session 或等效的短效安全機制。
- [ ] RBAC 至少分 viewer、operator、admin；實盤權限最小化。
- [ ] 稽核事件不可被一般操作覆寫，敏感資訊已遮罩。

### D. API 與狀態儲存（P1）

- [ ] Dashboard 使用單一 batch snapshot 或事件流；不得對 N 張卡片進行 N+1 stats 輪詢。
- [ ] 每個 read endpoint 具 schema、錯誤語意與 contract test；unknown/null 欄位的行為已文件化。
- [ ] 交易/運行狀態不僅存於 API process memory；重啟後可恢復或正確顯示 unavailable。
- [ ] config、desired state、command、runtime snapshot 具原子性或補償流程，避免半成功。
- [ ] 外部交易所呼叫有 timeout、rate limit、circuit breaker；僅安全/冪等操作可 retry。
- [ ] API 回應不得回傳 secret、API key、完整敏感設定。

### E. 前端品質、效能與可用性（P1/P2）

- [ ] 瀏覽器端只用同源相對 URL；開發 proxy 由環境變數設定，沒有硬編碼 localhost 供使用者瀏覽器呼叫。
- [ ] 請求可取消且不重疊；tab hidden 時降頻；失敗採 jitter/backoff，提供最後同步時間。
- [ ] loading、empty、error、stale 與 permission denied 有不同且可理解的狀態。
- [ ] 每張卡片有獨立 action state；操作一張策略不鎖住所有策略。
- [ ] 支援鍵盤焦點、可見 focus、語意化按鈕/label、ARIA 與足夠色彩對比；不只以顏色傳遞風險。
- [ ] 1440px、平板、手機無水平溢出；關鍵 Live/Paper/status 不因 RWD 被隱藏。
- [ ] 長清單採 pagination/virtualization；圖表按區間載入並進行 downsampling。

### F. 可觀測性、部署與恢復（P1）

- [ ] 健康檢查涵蓋 API、state store、engine heartbeat、connector；健康燈不可僅看 Web process 存活。
- [ ] 有 structured logs、correlation id；metrics 至少包含 API latency/error、command success、connector heartbeat、stale count。
- [ ] systemd/PM2 具 restart、退避、log rotation、build artifact version 與 healthcheck。
- [ ] 部署前後有 migration/rollback 步驟；回滾不遺失 command/audit/runtime state。
- [ ] 定期演練 API 重啟、引擎重啟、connector 斷線、交易所限流與網路中斷。

### G. 測試與驗收證據（P1）

- [ ] Unit：schema、formatter、risk validation、state transition、repository。
- [ ] Contract：OpenAPI/schema 與前端型別相容。
- [ ] Integration：API → engine adapter → runtime state → snapshot。
- [ ] PAPER E2E：create → start → heartbeat/orders → stop → audit；失敗與重試路徑亦覆蓋。
- [ ] Regression：並發 start、API restart、資料 stale、無權限、交易所錯誤、慢網路。
- [ ] CI 執行 Python tests、`npm run lint`、`npm run build`；結果連同本檢核表保存。

## 4. 量化服務目標（初版）

| 指標 | PAPER 目標 | LIVE 放行門檻 |
|---|---:|---:|
| Dashboard snapshot p95 | < 500 ms（不含交易所同步） | < 1 s |
| 前端初次總覽 LCP | < 2.5 s | < 2.5 s |
| 20 個網格 UI refresh API requests | 1 次 snapshot | 1 次 snapshot / event batch |
| 指令重複執行 | 0 | 0 |
| stale 資料誤標 LIVE | 0 | 0 |
| PNL/策略明細對帳差異 | 0（依來源精度） | 0（依來源精度） |
| PAPER E2E 成功率 | 100% CI | 100% CI + release smoke test |

## 5. 稽核輸出格式

每個迭代/PR 輸出以下內容：

1. **範圍與風險**：涉及的 mode、交易所、mutation、資料來源。
2. **檢核結果**：本文件每個適用項目的 pass/fail/N/A 與證據連結。
3. **測試證據**：指令、版本、結果、E2E 截圖或 log correlation id。
4. **例外接受**：P1/P2 未修項目、owner、到期日、暫時緩解措施。
5. **部署/回滾**：feature flag、migration、health checks、回滾步驟。

## 6. 本輪（Dashboard snapshot 垂直切片）適用門檻

本輪開始前，至少必須完成下列項目：

- A：snapshot 明確回報資料 freshness，不以 0 偽造未知值。
- D：Dashboard 從 N+1 輪詢移至一個 batch snapshot。
- E：移除 browser-facing localhost；含 loading/error/stale states。
- G：新增 snapshot route 測試；前端 lint/build 通過。

本輪**不啟用新的實盤下單能力**；任何 lifecycle 行為維持既有後端能力範圍，並在 UI 清楚表達資料來源和狀態限制。
