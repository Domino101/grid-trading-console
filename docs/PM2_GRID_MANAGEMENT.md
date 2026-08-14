# PM2 網格交易機器人管理手冊

> 建立於：2026-05-04 (於 2026-05-22 更新多網格管理)  
> 適用策略：
> * 單網格模式：`binance-neutral-perp-sol-usdc-100-160` (進程名稱: `grid-sol-usdc`)
> * 多網格模式：`run_multiple_grids.py` (進程名稱: `multi-grid-engine`)
>   * 支援同時運行：`SOL/USDC` 和 `ETH/USDC` 等多個網格，共享同一個 API 連接和 Websocket 數據流，避免 API 頻率限制

---

## 快速狀態確認

```bash
pm2 list                        # 所有進程狀態一覽（可同時看到單網格和多網格引擎）
pm2 show grid-sol-usdc          # 查看單網格進程詳細資訊
pm2 show multi-grid-engine      # 查看多網格引擎進程詳細資訊
pm2 monit                       # 即時 CPU/記憶體監控（互動式 Dashboard）
```

---

## 視覺化 Web UI（隨時開啟/關閉，不影響背景進程）

因為 PM2 是在背景 headless 運行，如果想要看到即時的網格狀態與圖表，可以使用專案內建的 Web UI：

```bash
# 啟動 Web UI（此指令會啟動前端與後端，並丟到背景執行）
cd /root/crypto-trading-open
./start_web_ui.sh
```

- **如何觀察**：在你的 Windows 瀏覽器輸入 `http://<你的Linux主機IP>:8001` (預設 port)
- **關閉影響**：你可以隨時關閉瀏覽器，**這完全不會影響網格進程**（網格依然由 PM2 處理）。
- **注意事項**：請確保 Web UI 中**不要**再點擊「啟動機器人」，否則可能會跟 PM2 啟動的進程衝突。目前 Web UI 僅作為讀取資料與圖表顯示的儀表板。

---

## Log 查看

### 1. 多網格引擎的 Log（推薦使用多網格時查看）
```bash
# 即時追蹤多網格 log (包含統計數據)
pm2 logs multi-grid-engine

# 查看最近 100 行
pm2 logs multi-grid-engine --lines 100

# 直接看 log 檔案
tail -f /root/crypto-trading-open/logs/pm2-multi-grid.log
tail -f /root/crypto-trading-open/logs/pm2-multi-grid-error.log
```

### 2. 單網格進程的 Log
```bash
# 即時追蹤單網格 log
pm2 logs grid-sol-usdc

# 查看最近 100 行
pm2 logs grid-sol-usdc --lines 100

# 直接看 log 檔案
tail -f /root/crypto-trading-open/logs/pm2-grid-sol-usdc.log
tail -f /root/crypto-trading-open/logs/pm2-grid-sol-usdc-error.log
```

### 3. 系統核心與引擎底層詳細 Log
```bash
# 查看最詳細的網格策略引擎日誌
tail -f /root/crypto-trading-open/logs/core.services.grid.implementations.grid_engine_impl.log
tail -f /root/crypto-trading-open/logs/system.log
```

---

## 啟動 / 停止 / 重啟

> ⚠️ **注意**：同一時間**請勿同時啟用** `grid-sol-usdc` 和 `multi-grid-engine` 來跑同一個交易對，否則會造成雙重下單！

### 1. 運行多個網格 (使用 `multi-grid-engine`)
如果你想同時運行 SOL/USDC、ETH/USDC 等多個網格：

#### 步驟 A：修改 PM2 設定檔
編輯專案根目錄的 `ecosystem.config.js`，找到 `multi-grid-engine` 的 `args` 欄位，填入你想運行的多個設定檔路徑（以空格分隔）：

```javascript
      // ecosystem.config.js 中的片段
      name: "multi-grid-engine",
      script: "run_multiple_grids.py",
      args: "config/grid/binance-neutral-perp-sol-usdc-100-160.yaml config/grid/binance-neutral-perp-eth-usdc-1800-3160.yaml",
```

#### 步驟 B：使用 PM2 啟動 / 重啟 / 停止
```bash
# 啟動多網格引擎
pm2 start ecosystem.config.js --only multi-grid-engine

# 重啟多網格引擎（改完網格配置或新增網格後使用）
pm2 restart multi-grid-engine

# 停止多網格引擎
pm2 stop multi-grid-engine

# 從 PM2 清單中移除
pm2 delete multi-grid-engine
```

### 2. 運行單個網格 (使用 `grid-sol-usdc`)
```bash
# 啟動單網格
pm2 start ecosystem.config.js --only grid-sol-usdc

# 重啟單網格
pm2 restart grid-sol-usdc

# 停止單網格
pm2 stop grid-sol-usdc
```

---

## 開機自動啟動管理

```bash
# 確認 pm2 開機服務狀態
systemctl is-enabled pm2-root
systemctl status pm2-root

# 儲存當前進程清單（非常重要！新增/刪除或修改進程後必須執行，否則開機不會套用新設定）
pm2 save

# 重新產生開機啟動腳本（系統遷移時使用）
pm2 startup systemd -u root --hp /root
```

---

## 修改策略參數後的標準流程

### 1. 多網格模式修改流程：
```bash
# A. 編輯目標設定檔（例如修改 ETH 區間或下單量）
nano config/grid/binance-neutral-perp-eth-usdc-1800-3160.yaml

# B. 重啟多網格引擎
pm2 restart multi-grid-engine

# C. 確認重啟與日誌正常
pm2 list
pm2 logs multi-grid-engine --lines 40
```

### 2. 單網格模式修改流程：
```bash
# A. 編輯設定檔
nano config/grid/binance-neutral-perp-sol-usdc-100-160.yaml

# B. 重啟單網格進程
pm2 restart grid-sol-usdc

# C. 確認重啟正常
pm2 logs grid-sol-usdc --lines 30
```

---

## 手動啟動（緊急 / 除錯 / 前台運行用）

> ⚠️ 若 PM2 中對應的進程正在運行，**請務必先停止 PM2 進程**，再進行手動啟動，避免重複下單！

```bash
cd /root/crypto-trading-open
source venv/bin/activate
```

### 1. 手動啟動多個網格 (並發執行)
直接在指令後方列出所有需要啟動的設定檔路徑：
```bash
python3 run_multiple_grids.py config/grid/binance-neutral-perp-sol-usdc-100-160.yaml config/grid/binance-neutral-perp-eth-usdc-1800-3160.yaml
```

### 2. 手動啟動單個網格
* **帶有互動式 TUI 介面** (適合本機/tmux，會佔用整個終端畫面)：
  ```bash
  python3 run_grid_trading.py config/grid/binance-neutral-perp-sol-usdc-100-160.yaml
  ```
* **無 UI 的 Headless 模式** (適合純後台或 SSH 指令)：
  ```bash
  python3 run_grid_trading.py config/grid/binance-neutral-perp-sol-usdc-100-160.yaml --no-ui
  ```

---

## 常見問題排查

### 1. 啟動後顯示「跳過批量下單：該交易對已達200掛單上限」
* **原因**：Binance 交易所限制單個交易對最多只能有 200 個掛單。當你之前運行的網格沒有清理乾淨，或者有其他策略在掛單時，會觸發此保護機制。
* **解決辦法**：
  在啟動前，手動清理該交易對的現有掛單。可以使用以下 Python 一鍵清除命令：
  ```bash
  # 清理 SOL/USDC 掛單
  python3 -c "
  import asyncio, yaml
  from run_grid_trading import create_exchange_adapter
  async def main():
      with open('config/grid/binance-neutral-perp-sol-usdc-100-160.yaml') as f: cfg = yaml.safe_load(f)
      adapter = await create_exchange_adapter(cfg)
      orders = await adapter.get_open_orders('SOL/USDC:USDC')
      print(f'找到 {len(orders)} 個掛單，正在清除...')
      for o in orders: await adapter.cancel_order(str(o.id), 'SOL/USDC:USDC')
      print('清除完畢')
      await adapter.disconnect()
  asyncio.run(main())
  "
  ```
  *(同理，若要清除 ETH/USDC，將上述的 yaml 路徑和交易對名稱更換成 `ETH/USDC:USDC` 即可)*

### 2. PM2 進程一直重啟（↺ 次數不斷增加）
* **解決辦法**：
  ```bash
  # 查看 error log 尋找崩潰原因
  pm2 logs multi-grid-engine --err --lines 50
  ```
  常見原因：
  - API Key 失效或 IP 被封 → 檢查 `.env` 設定。
  - 網路超時 → 引擎設置有 30 秒延遲重啟以防止頻繁請求。
  - 設定檔 YAML 語法格式錯誤 → 可以先用 `python3 run_multiple_grids.py ...` 手動執行，測試語法是否正確。

---

## 設定檔與重要路徑對照表

| 項目 | 路徑 |
|------|------|
| PM2 進程定義設定 | `ecosystem.config.js` |
| SOL 網格策略設定 | `config/grid/binance-neutral-perp-sol-usdc-100-160.yaml` |
| ETH 網格策略設定 | `config/grid/binance-neutral-perp-eth-usdc-1800-3160.yaml` |
| API 密鑰等環境變數 | `.env` |
| 多網格常駐日誌 | `logs/pm2-multi-grid.log` / `logs/pm2-multi-grid-error.log` |
| 網格引擎詳細日誌 | `logs/core.services.grid.implementations.grid_engine_impl.log` |rading.py config/grid/binance-neutral-perp-sol-usdc-100-160.yaml --no-ui
```

---

## 常見問題排查

### PM2 進程一直重啟（↺ 次數不斷增加）

```bash
# 查看 error log 找崩潰原因
pm2 logs grid-sol-usdc --err --lines 50

# 或直接看 error 檔案
cat logs/pm2-grid-sol-usdc-error.log | tail -50
```

常見原因：
- API Key 失效或 IP 被封 → 檢查 `.env` 設定
- Binance 連線超時 → 等待後自動重啟（設定 30 秒間隔）
- 程式碼語法錯誤 → 手動執行確認錯誤訊息

### 開機後沒有自動啟動

```bash
systemctl status pm2-root       # 確認服務狀態
pm2 resurrect                   # 手動恢復已儲存的進程清單
pm2 save                        # 再次儲存（確保清單是最新的）
```

### 想查看系統目前掛了幾筆單

```bash
# 看 grid engine log
grep "活跃窗口\|active_window\|挂单\|orders" logs/core.services.grid.implementations.grid_engine_impl.log | tail -20
```

---

## 設定檔位置

| 項目 | 路徑 |
|------|------|
| PM2 進程定義 | `ecosystem.config.js` |
| 策略參數 | `config/grid/binance-neutral-perp-sol-usdc-100-160.yaml` |
| API Key 等環境變數 | `.env` |
| PM2 進程清單備份 | `/root/.pm2/dump.pm2` |
| PM2 log 目錄 | `/root/.pm2/logs/` |
| 應用程式 log 目錄 | `logs/` |

---

## 部署架構說明

```
Linux 主機 (常駐)
  └── systemd
        └── pm2-root.service  ← 開機自動啟動
              └── PM2 daemon
                    └── grid-sol-usdc  ← 網格交易進程（headless --no-ui）
                          ├── 崩潰自動重啟 (RestartDelay: 30s, MaxRestarts: 10)
                          └── Log → logs/pm2-grid-sol-usdc.log
```

> Windows IDE / SSH 斷線不影響上述進程，網格持續運行。
