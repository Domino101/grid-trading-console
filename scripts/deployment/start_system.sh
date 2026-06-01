#!/usr/bin/env bash
set -euo pipefail

# 加載私密環境變數（若存在）
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "$SCRIPT_DIR/../.." && pwd )"
if [ -f "$PROJECT_ROOT/.secrets/binance.env" ]; then
  set -a
  source "$PROJECT_ROOT/.secrets/binance.env"
  set +a
elif [ -f "$PROJECT_ROOT/.env" ]; then
  set -a
  source "$PROJECT_ROOT/.env"
  set +a
fi

# 啟動網格交易（可傳參覆蓋）
VENV_PATH="${VENV_PATH:-.venv}"
CONFIG_PATH="${CONFIG_PATH:-config/grid/binance-long-perp-usdc-btc.yaml}"
DEBUG_FLAG="${DEBUG_FLAG:-}" # 設為 --debug 可開啟詳細日誌

if [ -f "$VENV_PATH/bin/activate" ]; then
  source "$VENV_PATH/bin/activate"
fi

BINANCE_API_KEY=${BINANCE_API_KEY:-}
BINANCE_API_SECRET=${BINANCE_API_SECRET:-}

exec env BINANCE_API_KEY="$BINANCE_API_KEY" BINANCE_API_SECRET="$BINANCE_API_SECRET" \
  python3 run_grid_trading.py "$CONFIG_PATH" ${DEBUG_FLAG}
