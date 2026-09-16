#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

echo "=== STEP 1: Fetching Universe-79 Data (2024-01 to 2026-07) ==="
python3 fetch_universe79.py --start 2024-01 --end 2026-07 --workers 16 --symbols-file symbols_clean150.txt

echo "=== STEP 2: Building Universe Panel ==="
python3 build_panel79.py --top-n 79

echo "=== STEP 3: Running Exp15 Backtest ==="
python3 exp15_universe79.py | tee exp15_oos_out.txt

echo "=== PIPELINE COMPLETE ==="
