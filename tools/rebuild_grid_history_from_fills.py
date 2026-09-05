#!/usr/bin/env python3
"""
tools/rebuild_grid_history_from_fills.py

Rebuilds clean, authentic historical snapshots in `grid_runtime_history` and `bot_snapshot_history`
using the audited `trade_fills` ledger.

1. Clears pre-audit polluted snapshot history (before 1788593000) for active grid bots.
2. Generates hourly checkpoints from cumulative trade_fills realized_pnl and fees.
3. Inserts clean historical snapshots so the 24h and 7d trend charts display smooth, authentic curves.
"""

import os
import sys
import time
import json
import sqlite3
from typing import Dict, List, Tuple

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DB_PATH = os.path.join(PROJECT_ROOT, "data", "web_ui_runtime.sqlite3")

GRID_BOTS = [
    ("binance-neutral-perp-sui-usdc-usdc-0-0", "config/grid/binance-neutral-perp-sui-usdc-usdc-0-0.yaml", "SUI/USDC:USDC"),
    ("binance-neutral-perp-sol-usdc-usdc-84-250", "config/grid/binance-neutral-perp-sol-usdc-usdc-84-250.yaml", "SOL/USDC:USDC"),
    ("binance-neutral-perp-prom-usdt-usdt-2-8", "config/grid/binance-neutral-perp-prom-usdt-usdt-2-8.yaml", "PROM/USDT:USDT"),
    ("binance-neutral-perp-trump-usdc-usdc-1-3", "config/grid/binance-neutral-perp-trump-usdc-usdc-1-3.yaml", "TRUMP/USDC:USDC"),
    ("binance-neutral-perp-uni-usdc-usdc-4-12", "config/grid/binance-neutral-perp-uni-usdc-usdc-4-12.yaml", "UNI/USDC:USDC"),
]

# Audit cutoff timestamp: snapshots before this time contained the old fake profit calculation bug
AUDIT_CUTOFF = 1788593000.0


def rebuild_history():
    if not os.path.exists(DB_PATH):
        print(f"Database {DB_PATH} not found.")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    cursor = conn.cursor()

    # Ensure WAL mode and busy timeout
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA busy_timeout=30000;")

    now = time.time()
    seven_days_ago = now - 7 * 86400

    for bot_id, config_path, symbol in GRID_BOTS:
        print(f"\nProcessing {bot_id} ({symbol})...")

        # 1. Fetch current live snapshot to keep template structure
        cursor.execute(
            "SELECT payload FROM grid_runtime_snapshots WHERE config_path = ?",
            (config_path,),
        )
        row = cursor.fetchone()
        template = {}
        if row and row[0]:
            try:
                template = json.loads(row[0])
            except Exception:
                template = {}

        # 2. Fetch all trade_fills for this bot
        cursor.execute(
            """
            SELECT price, quantity, fee, realized_pnl, executed_at, side
            FROM trade_fills
            WHERE bot_id = ?
            ORDER BY executed_at ASC
            """,
            (bot_id,),
        )
        fills = cursor.fetchall()
        print(f"  Found {len(fills)} authentic trade fills.")

        if not fills:
            print("  No fills found, skipping.")
            continue

        # 3. Clean up pre-audit polluted history
        cursor.execute(
            "DELETE FROM grid_runtime_history WHERE config_path = ? AND recorded_at < ?",
            (config_path, AUDIT_CUTOFF),
        )
        del_legacy = cursor.rowcount
        cursor.execute(
            "DELETE FROM bot_snapshot_history WHERE (bot_id = ? OR config_path = ?) AND recorded_at < ?",
            (bot_id, config_path, AUDIT_CUTOFF),
        )
        del_p1 = cursor.rowcount
        print(f"  Cleaned {del_legacy} legacy snapshots and {del_p1} canonical snapshots prior to audit.")

        # 4. Generate hourly checkpoints from 7 days ago to AUDIT_CUTOFF
        first_fill_time = fills[0][4]
        start_time = max(seven_days_ago, first_fill_time)
        # align to the top of the hour
        start_hour = int(start_time // 3600) * 3600
        end_hour = int(AUDIT_CUTOFF // 3600) * 3600

        checkpoints = []
        fill_idx = 0
        n_fills = len(fills)
        cum_realized = 0.0
        cum_fee = 0.0
        last_price = fills[0][0]
        buy_count = 0
        sell_count = 0

        current_hour = start_hour
        while current_hour < end_hour:
            # advance fills up to current_hour
            while fill_idx < n_fills and fills[fill_idx][4] <= current_hour:
                p, q, f, r, t_exec, s = fills[fill_idx]
                cum_realized += float(r)
                cum_fee += float(f)
                last_price = float(p)
                if str(s).lower() == 'buy':
                    buy_count += 1
                else:
                    sell_count += 1
                fill_idx += 1

            # Only record if after first fill
            if current_hour >= first_fill_time:
                checkpoints.append({
                    "time": float(current_hour),
                    "price": last_price,
                    "grid_profit": round(cum_realized, 6),
                    "fees": round(cum_fee, 6),
                    "net_pnl": round(cum_realized - cum_fee, 6),
                    "buy_count": buy_count,
                    "sell_count": sell_count,
                })

            current_hour += 3600  # 1 hour step

        print(f"  Generated {len(checkpoints)} hourly clean checkpoints.")

        # 5. Insert clean checkpoints into history tables
        for cp in checkpoints:
            t_pt = cp["time"]
            gp = cp["grid_profit"]
            fees = cp["fees"]
            net = cp["net_pnl"]
            price = cp["price"]

            # clone template or create basic payload
            payload = dict(template)
            payload["current_price"] = price
            payload["symbol"] = symbol
            payload["bot_id"] = bot_id
            payload["profit"] = {
                "grid_profit": gp,
                "realized": gp,
                "unrealized": 0.0,
                "total": gp,
                "fees": fees,
                "net": net,
                "rate": round((net / 1000.0) * 100, 2) if net != 0 else 0.0,
            }
            payload_str = json.dumps(payload, ensure_ascii=False)

            cursor.execute(
                """
                INSERT INTO grid_runtime_history (config_path, payload, recorded_at)
                VALUES (?, ?, ?)
                """,
                (config_path, payload_str, t_pt),
            )
            cursor.execute(
                """
                INSERT INTO bot_snapshot_history (bot_id, config_path, payload, recorded_at)
                VALUES (?, ?, ?, ?)
                """,
                (bot_id, config_path, payload_str, t_pt),
            )

        conn.commit()
        print(f"  Successfully reseeded {len(checkpoints)} historical checkpoints for {bot_id}.")

    conn.close()
    print("\nAll grid histories rebuilt successfully!")


if __name__ == "__main__":
    rebuild_history()
