#!/usr/bin/env python3
"""
清理 Paper 模擬網格機器人中因撤單誤判累積之幽靈成交與虛假盈利工具。

用法:
  python tools/cleanup_paper_phantom_fills.py --dry-run
  python tools/cleanup_paper_phantom_fills.py --execute --all-paper
  python tools/cleanup_paper_phantom_fills.py --execute --bot-id a039a2ee2f76449abe0bf04b6b19c7a4
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path("data/web_ui_runtime.sqlite3")


def get_affected_paper_bots(conn, target_bot_id=None):
    cursor = conn.cursor()
    if target_bot_id:
        cursor.execute(
            "SELECT bot_id, strategy_type, symbol, exchange, config_path, status "
            "FROM bots WHERE bot_id = ? AND account_mode = 'paper' AND strategy_type = 'grid'",
            (target_bot_id,)
        )
    else:
        cursor.execute(
            "SELECT bot_id, strategy_type, symbol, exchange, config_path, status "
            "FROM bots WHERE account_mode = 'paper' AND strategy_type = 'grid'"
        )
    return cursor.fetchall()


def analyze_bot(conn, bot_id):
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*), COALESCE(SUM(realized_pnl), 0), COALESCE(SUM(fee), 0) "
        "FROM trade_fills WHERE bot_id = ?",
        (bot_id,)
    )
    fill_count, total_pnl, total_fee = cursor.fetchone()
    return fill_count, total_pnl, total_fee


def clean_bot_data(conn, bot_id, config_path):
    cursor = conn.cursor()
    # 1. 刪除 trade_fills
    cursor.execute("DELETE FROM trade_fills WHERE bot_id = ?", (bot_id,))
    deleted_fills = cursor.rowcount

    # 2. 重置 bot_positions
    cursor.execute(
        "UPDATE bot_positions SET completed_cycles = 0, realized_profit = 0, "
        "quantity = 0, total_cost = 0, total_fees = 0, average_cost = 0, entry_price = 0, "
        "updated_at = strftime('%s', 'now') WHERE bot_id = ?",
        (bot_id,)
    )

    # 3. 更新 bot_snapshots payload
    cursor.execute("SELECT payload FROM bot_snapshots WHERE bot_id = ?", (bot_id,))
    row = cursor.fetchone()
    if row:
        try:
            payload = json.loads(row[0])
            if "filled_orders" in payload and isinstance(payload["filled_orders"], dict):
                payload["filled_orders"]["buy"] = 0
                payload["filled_orders"]["sell"] = 0
                payload["filled_orders"]["cycles"] = 0
            if "profit" in payload and isinstance(payload["profit"], dict):
                payload["profit"]["grid_profit"] = 0.0
                payload["profit"]["realized"] = 0.0
                payload["profit"]["fees"] = 0.0
            payload["grid_position"] = 0.0
            payload["average_cost"] = 0.0
            cursor.execute(
                "UPDATE bot_snapshots SET payload = ?, updated_at = strftime('%s', 'now') WHERE bot_id = ?",
                (json.dumps(payload, ensure_ascii=False), bot_id)
            )
        except Exception:
            pass

    # 4. 更新 grid_runtime_snapshots
    if config_path:
        cursor.execute("SELECT payload FROM grid_runtime_snapshots WHERE config_path = ?", (config_path,))
        row = cursor.fetchone()
        if row:
            try:
                payload = json.loads(row[0])
                if "filled_orders" in payload and isinstance(payload["filled_orders"], dict):
                    payload["filled_orders"]["buy"] = 0
                    payload["filled_orders"]["sell"] = 0
                    payload["filled_orders"]["cycles"] = 0
                if "profit" in payload and isinstance(payload["profit"], dict):
                    payload["profit"]["grid_profit"] = 0.0
                    payload["profit"]["realized"] = 0.0
                    payload["profit"]["fees"] = 0.0
                payload["grid_position"] = 0.0
                payload["average_cost"] = 0.0
                cursor.execute(
                    "UPDATE grid_runtime_snapshots SET payload = ?, updated_at = strftime('%s', 'now') WHERE config_path = ?",
                    (json.dumps(payload, ensure_ascii=False), config_path)
                )
            except Exception:
                pass

    return deleted_fills


def main():
    parser = argparse.ArgumentParser(description="清理 Paper 模擬網格幽靈成交紀錄")
    parser.add_argument("--execute", action="store_true", help="確認執行清理（未帶則僅分析 dry-run）")
    parser.add_argument("--bot-id", type=str, default=None, help="指定清理的單一 Bot ID")
    parser.add_argument("--all-paper", action="store_true", help="清理所有 Paper 網格機器人")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"❌ 資料庫不存在: {DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    try:
        bots = get_affected_paper_bots(conn, args.bot_id)
        if not bots:
            print("ℹ️ 未找到符合條件的 Paper 網格機器人。")
            return

        print(f"🔍 找到 {len(bots)} 個 Paper 網格機器人：\n")
        print(f"{'Bot ID':<34} | {'Symbol':<16} | {'成交筆數':<8} | {'虛假盈虧 (USD)':<14} | {'狀態'}")
        print("-" * 88)

        total_phantom_fills = 0
        total_phantom_pnl = 0.0

        for bot_id, stype, symbol, ex, cfg_path, status in bots:
            fills, pnl, fee = analyze_bot(conn, bot_id)
            total_phantom_fills += fills
            total_phantom_pnl += pnl
            print(f"{bot_id:<34} | {symbol:<16} | {fills:<8} | ${pnl:<13.4f} | {status}")

        print("-" * 88)
        print(f"統計總計: {total_phantom_fills} 筆假成交, 累計虛假盈利: ${total_phantom_pnl:.4f}\n")

        if not args.execute:
            print("⚠️  目前處於 DRY-RUN 檢視模式，未做任何資料庫變更。")
            print("👉 若要確認執行清洗歸零，請帶參數：--execute --all-paper")
            return

        print("🚀 開始執行清洗歸零作業...")
        total_cleaned = 0
        for bot_id, stype, symbol, ex, cfg_path, status in bots:
            cleaned = clean_bot_data(conn, bot_id, cfg_path)
            total_cleaned += cleaned
            print(f"  ✓ 已清理 {symbol} ({bot_id}): 刪除 {cleaned} 筆幽靈成交，重置持倉與統計為 0")

        conn.commit()
        print(f"\n🎉 清洗完成！共清理 {total_cleaned} 筆假成交紀錄。")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
