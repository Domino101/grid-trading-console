# grid-trading-console

多交易所網格交易引擎與 Web 總控台。顯示名稱已從舊庫 `crypto-trading-open` 獨立出來；這個倉庫是原始碼專案，**不是**可下載的桌面安裝包。舊的假下載說明已移到 [`docs/archive/README_fake_download_page.md`](docs/archive/README_fake_download_page.md)。

若要開一個**乾淨歷史**的新 private 庫，在你自己的 GitHub 帳號執行：

```bash
gh auth login   # 必須是 Domino101，不是 Arena bot
./scripts/publish_private_repo.sh
```

預設會建立 `Domino101/grid-trading-console`，不含 `.git` 舊歷史、不含 `config/exchanges` 金鑰、不含 `data/` 與 token 檔。

## 帳戶模式

| 模式 | 行為 |
|---|---|
| `PAPER` | 走 `PaperExchangeAdapter`：只讀公開行情，在記憶體訂單簿撮合。每個 PAPER 策略有獨立模擬帳戶。 |
| `LIVE` | 真實交易所下單。建立與啟動都需要 `GRID_LIVE_TRADING_ENABLED=true`。 |
| 未標 `account_mode` 的既有 YAML | 視為 **legacy 實盤**，不是 PAPER。 |

總覽 KPI 在有 LIVE 策略時只加總 LIVE；PAPER 盈虧另外顯示，不會混進實盤收益。

預設模擬盤起始權益 `PAPER_STARTING_EQUITY=10000`，手續費 `PAPER_FEE_RATE=0.0002`。

## 必要環境變數

| 變數 | 說明 |
|---|---|
| `GRID_API_TOKEN` | Web UI / API Bearer token。未設定時後端寫入 `.grid_api_token`（權限 0600），不會印在 log。 |
| `GRID_LIVE_TRADING_ENABLED` | 預設關閉。只有 `1` / `true` / `yes` 才允許 LIVE 建立與啟動。 |
| `GRID_CORS_ORIGINS` | CORS 白名單，逗號分隔。`*` 時關閉 credentials。 |
| `PAPER_STARTING_EQUITY` | PAPER 模擬帳戶起始權益。 |
| `PAPER_FEE_RATE` | PAPER 模擬手續費率。 |
| `PROJECT_ROOT` / `PYTHON_BIN` | PM2 路徑參數；見 `ecosystem.config.js`。 |

## 本機啟動

需要 Python 3.11+、Node.js 22+，以及虛擬環境：

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 引擎：未指定 YAML 時進入熱插拔待命，不會偷偷啟動五個內建實盤
python run_multiple_grids.py

# Web UI（開發）
cd web_ui/frontend && npm ci && npm run dev
# 另開終端
cd web_ui/backend && python main.py
```

瀏覽器只打同源 `/api`。Vite 會把 `/api` 與 `/ws` proxy 到後端，遠端 preview 不要改成 `localhost`。

登入頁輸入 `GRID_API_TOKEN` 或 `.grid_api_token` 的內容。不要把交易所 API 金鑰貼進登入框。

## 正式部署

`start_web_ui.sh` 仍用 `nohup`，**只適合本機實驗**。正式環境使用 systemd：

- 單元：[`scripts/deployment/crypto-webui.service`](scripts/deployment/crypto-webui.service)
- 清單：[`docs/web_ui_deployment_and_release_checklist_zh-TW.md`](docs/web_ui_deployment_and_release_checklist_zh-TW.md)

PM2 只應跑 `multi-grid-engine`（可另跑波動率掃描器）。不要同時再跑一份單幣 `run_grid_trading.py`，否則同一 YAML 會雙開。

## 操作語意

- 總覽只顯示 `config/active_grids.json` 已註冊策略；`config/grid/archived/` 不會進清單。
- 建立策略預設不啟動（`auto_start=false`）。啟動成功只代表命令 `pending_engine`，要等引擎 heartbeat 才算 `running`。
- 停止會取消掛單，**預設不平倉**。
- 刪除只允許已停止且沒有未完成命令的策略；否則 HTTP 409。
- 未知盈虧 / 餘額顯示 `—`，不會用 `0` 偽裝成真實帳戶資料。

## 測試與 CI

```bash
./scripts/ci.sh
```

或分開跑：

```bash
python3 -m unittest \
  tests.test_lifecycle_and_safety \
  tests.test_paper_adapter \
  tests.test_command_repository \
  tests.test_runtime_repository \
  tests.test_paper_grid_lifecycle_e2e \
  tests.test_frontend_format_contract \
  tests.test_dashboard_snapshot \
  tests.test_web_ui_http_contract

cd web_ui/frontend && npm test && npm run lint && npm run build
```

沒有安裝 FastAPI 時，HTTP / snapshot 測試會 skip。GitHub Actions 工作流範本在 `docs/github-actions-ci.yml`（需 `workflows` 權限才能放到 `.github/workflows/ci.yml`）。

## 文件

- 稽核與修改計畫：`docs/grid_trading_console_audit_20260813_zh-TW.md`
- 升級計畫：`docs/grid_trading_console_upgrade_plan_zh-TW.md`
- 變更紀錄：`CHANGELOG.md`
