# 網格交易持久化方案分析

## 問題背景

- **現況**：Windows IDE → SSH → Linux，手動執行 `python3 run_grid_trading.py config/grid/...`
- **痛點**：Windows 當機 / IDE 掛掉 → SSH 連線斷 → 進程被 SIGHUP 殺死 → 網格停止
- **環境**：Linux (Ubuntu 24.04), systemd 255, pm2 6.0.5 已安裝，無 tmux/screen

---

## 方案比較總表

| 方案 | 可靠性 | 自動重啟 | 管理難度 | 開機自動啟動 | 適合此專案 |
|------|--------|---------|---------|------------|-----------|
| **A. systemd Service** | ⭐⭐⭐⭐⭐ | ✅ 原生 | 中 | ✅ 最佳 | ✅ **強烈推薦** |
| **B. PM2** | ⭐⭐⭐⭐ | ✅ 原生 | 低（已安裝）| ✅ 可設定 | ✅ **推薦（最快上手）** |
| **C. tmux / screen** | ⭐⭐⭐ | ❌ 需手動 | 低 | ❌ 需額外配置 | ⚠️ 過渡方案 |
| **D. Docker 容器** | ⭐⭐⭐⭐⭐ | ✅ restart policy | 高 | ✅ | ✅ 進階方案 |
| **E. nohup / disown** | ⭐⭐ | ❌ 無 | 最低 | ❌ | ❌ 不推薦 |

---

## 方案 A：systemd Service（最穩定，生產環境首選）

### 原理
將交易程式註冊為 Linux 系統服務，由 systemd 管理生命週期。即使 SSH 斷線、機器重啟，服務都會自動恢復。

### 優點
- ✅ 開機自動啟動（`enable` 後永久有效）
- ✅ 崩潰自動重啟（`Restart=always`，可設重啟間隔）
- ✅ 系統級日誌（`journalctl -u grid-sol-usdc`）
- ✅ 完全與 SSH 會話無關
- ✅ 符合生產環境標準，最可靠

### 缺點
- ⚠️ 需要 root 或 sudo 權限（你已是 root，無問題）
- ⚠️ 修改 config 後需 `systemctl restart` 重載

### 實作步驟

**第一步：建立 service 檔案**

```bash
cat > /etc/systemd/system/grid-sol-usdc.service << 'EOF'
[Unit]
Description=Binance Grid Trading - SOL/USDC Neutral Perp 100-160
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/nofx_recovered/crypto-trading-open
Environment=PYTHONUNBUFFERED=1
ExecStart=/root/nofx_recovered/crypto-trading-open/venv/bin/python3 run_grid_trading.py config/grid/binance-neutral-perp-sol-usdc-100-160.yaml
Restart=always
RestartSec=30
StartLimitInterval=300
StartLimitBurst=5
StandardOutput=append:/root/nofx_recovered/crypto-trading-open/logs/grid-sol-usdc.log
StandardError=append:/root/nofx_recovered/crypto-trading-open/logs/grid-sol-usdc.err

[Install]
WantedBy=multi-user.target
EOF
```

**第二步：啟用並啟動**

```bash
systemctl daemon-reload
systemctl enable grid-sol-usdc.service   # 開機自動啟動
systemctl start grid-sol-usdc.service    # 立即啟動
systemctl status grid-sol-usdc.service   # 查看狀態
```

**日常管理指令**

```bash
systemctl stop grid-sol-usdc       # 停止
systemctl restart grid-sol-usdc    # 重啟（改 config 後用）
journalctl -u grid-sol-usdc -f     # 即時看 log
journalctl -u grid-sol-usdc --since "1 hour ago"  # 看最近 1 小時
```

---

## 方案 B：PM2（最快上手，已安裝）

### 原理
PM2 是 Node.js 的進程管理器，但完全支援 Python 程序。你的環境已安裝 `pm2 6.0.5`，可以**立即使用**。

### 優點
- ✅ **已安裝，5分鐘內可部署**
- ✅ 崩潰自動重啟
- ✅ 開機自動啟動（`pm2 startup` + `pm2 save`）
- ✅ 內建日誌管理 + `pm2 logs` 即時查看
- ✅ `pm2 monit` 可看 CPU/記憶體即時監控
- ✅ 與 SSH 會話完全無關

### 缺點
- ⚠️ 依賴 Node.js/PM2 進程本身（但 PM2 本身很穩定）
- ⚠️ log 預設存在 `~/.pm2/logs/`（可自訂）

### 實作步驟

```bash
# 切換到專案目錄
cd /root/nofx_recovered/crypto-trading-open

# 先停止目前手動跑的進程（Ctrl+C 或 kill）
# 然後用 PM2 啟動
pm2 start venv/bin/python3 \
  --name "grid-sol-usdc" \
  --interpreter none \
  -- run_grid_trading.py config/grid/binance-neutral-perp-sol-usdc-100-160.yaml

# 設定開機自動啟動
pm2 startup   # 會給你一條指令，複製執行
pm2 save      # 儲存當前進程清單

# 查看狀態
pm2 status
pm2 logs grid-sol-usdc --lines 50
pm2 monit
```

**PM2 管理指令**

```bash
pm2 stop grid-sol-usdc      # 停止
pm2 restart grid-sol-usdc   # 重啟
pm2 delete grid-sol-usdc    # 移除
pm2 logs grid-sol-usdc -f   # 即時 log
```

---

## 方案 C：tmux / screen（過渡方案，目前未安裝）

### 原理
建立持久化的終端 session，SSH 斷線後 session 仍存在，重連後可接回。

### 優點
- 可以在 SSH 斷線後重新接回互動終端（看到完整 stdout）

### 缺點
- ❌ **系統未安裝 tmux 或 screen**（需先安裝）
- ❌ 機器重啟後 session 消失，需手動重啟
- ❌ 不是真正意義的「持久化服務」
- 建議只作為**臨時過渡**，不適合生產

### 若要安裝 tmux

```bash
apt-get install -y tmux
tmux new-session -d -s grid 'cd /root/nofx_recovered/crypto-trading-open && venv/bin/python3 run_grid_trading.py config/grid/binance-neutral-perp-sol-usdc-100-160.yaml'
tmux attach -t grid  # SSH 重連後接回
```

---

## 方案 D：Docker 容器（進階方案，最佳隔離性）

### 原理
將整個交易環境封裝進 Docker 容器，使用 `--restart=unless-stopped` 策略。

### 優點
- ✅ 環境完全隔離，不受主機環境影響
- ✅ 方便遷移到其他主機
- ✅ 可用 Docker Compose 管理多個 bot

### 缺點
- ⚠️ 配置複雜度最高
- ⚠️ 需要建立 Dockerfile、處理 venv 路徑
- ⚠️ 適合有 Docker 基礎的用戶

---

## 方案 E：nohup / disown（不推薦）

```bash
nohup venv/bin/python3 run_grid_trading.py config/grid/... &> logs/nohup.log &
disown
```

### 缺點
- ❌ 無自動重啟能力
- ❌ 機器重啟後消失
- ❌ 只能應對 SSH 斷線，功能最弱
- 僅作為應急臨時手段

---

## 推薦方案（依優先順序）

### 🥇 立即可用：PM2（方案 B）
> **理由**：已安裝 pm2 6.0.5，5 分鐘內完成部署，具備自動重啟 + 開機啟動 + 日誌管理，適合快速解決問題。

### 🥈 長期穩定：systemd（方案 A）
> **理由**：systemd 是 Linux 最原生的服務管理機制，最穩定可靠，適合長期生產環境。建議在 PM2 穩定運行後，再遷移到 systemd 以提升可靠性。

### 💡 最終建議組合
1. **立即**：用 PM2 接手目前的手動進程
2. **一週內**：建立 systemd service 替換 PM2（或兩者並行測試）
3. **進階**：考慮搭配監控告警（如 Telegram bot 通知崩潰/重啟事件）

---

## 注意事項

> [!IMPORTANT]
> 切換服務管理器前，先優雅停止目前正在運行的手動進程，避免雙重交易。
> 目前偵測到有一個進程已運行 2 小時以上。

> [!WARNING]
> 使用 `Restart=always` 或 PM2 的自動重啟時，若程序因 API 錯誤不斷崩潰，重啟間隔 `RestartSec=30` 可防止短時間內過多重啟觸發 Binance 封 IP。

> [!TIP]
> 建議同時設定 Telegram/Line 告警（你的專案有 `run_price_alert.py`），在 bot 重啟或崩潰時發送通知，形成完整的監控閉環。
