#!/bin/bash
set -e

# 顏色定義
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}═══════════════════════════════════════${NC}"
echo -e "${GREEN}  網格交易 Web UI 啟動腳本${NC}"
echo -e "${GREEN}═══════════════════════════════════════${NC}"

# 獲取腳本所在目錄的絕對路徑
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# 檢查 Python 依賴
echo -e "${YELLOW}[1/4] 檢查後端依賴...${NC}"
pip install -q -r web_ui/backend/requirements.txt

# 構建前端
echo -e "${YELLOW}[2/4] 構建前端...${NC}"
cd web_ui/frontend
npm install
npm run build
cd ../..

# 啟動後端 (nohup 持久化)
echo -e "${YELLOW}[3/4] 啟動後端服務...${NC}"
cd web_ui/backend

# 停止舊的實例
PID_FILE="../../logs/web_ui.pid"
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if ps -p $OLD_PID > /dev/null; then
        echo -e "${YELLOW}停止舊實例 (PID: $OLD_PID)...${NC}"
        kill $OLD_PID
        sleep 2
    fi
    rm "$PID_FILE"
fi

nohup python3 main.py > ../../logs/web_ui.log 2>&1 &
WEB_UI_PID=$!
echo $WEB_UI_PID > ../../logs/web_ui.pid
cd ../..

echo -e "${GREEN}═══════════════════════════════════════${NC}"
echo -e "${GREEN}✅ Web UI 已啟動${NC}"
echo -e "${GREEN}   PID: $WEB_UI_PID${NC}"
echo -e "${GREEN}   訪問: http://localhost:8001${NC}"
echo -e "${GREEN}   日誌: logs/web_ui.log${NC}"
echo -e "${GREEN}═══════════════════════════════════════${NC}"
