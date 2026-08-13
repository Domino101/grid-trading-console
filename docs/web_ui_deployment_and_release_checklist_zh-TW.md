# Web UI 部署與 Release 稽核清單

本文件適用於 `crypto-webui` 的 PAPER 與 LIVE 部署。不可用舊的 `nohup` 腳本作為正式環境持久化方案；正式環境使用 systemd unit `scripts/deployment/crypto-webui.service`。

## 1. 部署前

- [ ] 在受控 host 以非 root service user 部署專案，例如 `/opt/crypto-trading-open`。
- [ ] 建立 Python venv，安裝 `requirements.txt`；在 `web_ui/frontend` 執行 `npm ci && npm run build`。
- [ ] `web_ui/frontend/dist` 已產出，FastAPI 能掛載靜態前端。
- [ ] `.env` 權限為 `0600`，設定 `GRID_API_TOKEN`，且 token 不出現在 shell history、unit 或 log。
- [ ] `GRID_LIVE_TRADING_ENABLED` 預設為未設定或 `false`。
- [ ] LIVE 發布需要明確風險核准後才設定 `GRID_LIVE_TRADING_ENABLED=true`。
- [ ] 審閱 `config/grid/*.yaml`：確認每筆策略 `account_mode` 與目標交易所一致。
- [ ] 執行 `npm run lint`、`npm run build`、pytest/PAPER E2E，保存結果。
- [ ] 最低本地持久化流程測試：`python -m unittest tests.test_paper_grid_lifecycle_e2e`。
- [ ] CI/release venv HTTP contract：`python -m unittest tests.test_web_ui_http_contract`（需先安裝 `requirements.txt`）。

## 2. 安裝與啟動 systemd

1. 複製 `scripts/deployment/crypto-webui.service` 到 `/etc/systemd/system/crypto-webui.service`。
2. 將 unit 內 `/opt/crypto-trading-open`、`crypto`、venv path 替換為目標環境真實值。
3. 執行：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now crypto-webui
sudo systemctl status crypto-webui
```

4. 僅檢查程序/SQLite 可用性：

```bash
curl --fail http://127.0.0.1:8001/healthz
```

5. 使用 Bearer token 驗證完整、受保護的系統狀態：

```bash
curl --fail -H "Authorization: Bearer $GRID_API_TOKEN" \
  http://127.0.0.1:8001/api/v1/system/health
```

`/healthz` 不回傳交易所、帳戶、策略或 connector 詳情；完整健康資訊必須使用已認證 endpoint。

## 3. PAPER 發布驗收

- [ ] 建立 PAPER 策略；確認 YAML 中 `account_mode: paper`。
- [ ] 送出 Start，看到 `pending_engine`。
- [ ] 等待 `GRID_LIFECYCLE running` / stats heartbeat，確認 command 轉為 `running`。
- [ ] 總覽與詳情顯示正確 `live/stale/unavailable` freshness，沒有將未知數據顯示為零。
- [ ] Stop 後看到 engine `stopped` confirmation，command 轉為 `stopped`。
- [ ] 在詳情檢查 command timeline、engine events、PNL history。
- [ ] 重啟 `crypto-webui`，確認歷史 snapshot、命令與事件仍存在，且舊資料標記 stale。

## 4. LIVE 額外放行

- [ ] 無未解 P0/P1 稽核項目，參照 `grid_trading_console_audit_standard_zh-TW.md`。
- [ ] 已完成相同交易所/交易對的 PAPER E2E。
- [ ] `GRID_LIVE_TRADING_ENABLED=true` 有變更單與到期日；未核准時維持 false。
- [ ] Live create 通過後端 market rule：symbol、min notional、price precision、quantity precision。
- [ ] 值班人員已確認 start/stop/delete 的資金與撤單影響。
- [ ] 已驗證 API、engine、connector 斷線與 rollback 流程。

## 5. 回滾與事故處理

- UI/API 問題：`sudo systemctl restart crypto-webui`，並用 `/healthz` 驗證。
- 交易異常：依策略操作手冊停止策略；不要先刪除 YAML 或 SQLite，以免破壞稽核證據。
- 版本回滾：部署上一個已知版本、重建前端、restart service。保留 `data/web_ui_runtime.sqlite3` 與 audit SQLite。
- 每次 incident 記錄 command id、grid id、時間、engine event、connector/交易所錯誤與採取動作。
