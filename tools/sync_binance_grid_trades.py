#!/usr/bin/env python3
"""
從幣安交易所 (Binance API) 同步回填實盤網格機器人的真實成交流水與統計數據。

用法:
  python tools/sync_binance_grid_trades.py --dry-run
  python tools/sync_binance_grid_trades.py --execute
"""

import argparse
import collections
from datetime import datetime
from decimal import Decimal
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

from dotenv import load_dotenv

DB_PATH = Path("data/web_ui_runtime.sqlite3")

LIVE_BOTS = [
    {
        "bot_id": "binance-neutral-perp-uni-usdc-usdc-4-12",
        "symbol": "UNI/USDC:USDC",
        "config_path": "config/grid/binance-neutral-perp-uni-usdc-usdc-4-12.yaml",
        "since_ms": int((time.time() - 10 * 86400) * 1000),  # 過去 10 天
    },
    {
        "bot_id": "binance-neutral-perp-trump-usdc-usdc-1-3",
        "symbol": "TRUMP/USDC:USDC",
        "config_path": "config/grid/binance-neutral-perp-trump-usdc-usdc-1-3.yaml",
        "since_ms": int((time.time() - 10 * 86400) * 1000),
    },
    {
        "bot_id": "binance-neutral-perp-prom-usdt-usdt-2-8",
        "symbol": "PROM/USDT:USDT",
        "config_path": "config/grid/binance-neutral-perp-prom-usdt-usdt-2-8.yaml",
        "since_ms": int((time.time() - 10 * 86400) * 1000),
    },
    {
        "bot_id": "binance-neutral-perp-sol-usdc-usdc-84-250",
        "symbol": "SOL/USDC:USDC",
        "config_path": "config/grid/binance-neutral-perp-sol-usdc-usdc-84-250.yaml",
        "since_ms": int((time.time() - 10 * 86400) * 1000),
    },
    {
        "bot_id": "binance-neutral-perp-sui-usdc-usdc-0-0",
        "symbol": "SUI/USDC:USDC",
        "config_path": "config/grid/binance-neutral-perp-sui-usdc-usdc-0-0.yaml",
        "since_ms": int((time.time() - 10 * 86400) * 1000),
    },
]


def init_binance_client():
    load_dotenv(Path(".env"))
    import ccxt

    api_key = os.getenv("BINANCE_API_KEY")
    api_secret = os.getenv("BINANCE_API_SECRET")
    if not api_key or not api_secret:
        raise RuntimeError("未在 .env 中找到 BINANCE_API_KEY 或 BINANCE_API_SECRET！")

    client = ccxt.binance({
        "apiKey": api_key,
        "secret": api_secret,
        "timeout": 15000,
        "enableRateLimit": True,
        "options": {"defaultType": "swap"},
    })
    return client


def fetch_all_trades_for_symbol(client, symbol: str, since_ms: int):
    """分頁拉取幣安指定交易對從 since_ms 以來的所有真實成交"""
    all_trades = []
    current_since = since_ms
    limit = 1000

    while True:
        try:
            trades = client.fetch_my_trades(symbol, since=current_since, limit=limit)
            if not trades:
                break
            all_trades.extend(trades)
            if len(trades) < limit:
                break
            last_ts = trades[-1]["timestamp"]
            if last_ts == current_since:
                break
            current_since = last_ts + 1
            time.sleep(0.2)
        except Exception as exc:
            print(f"  ⚠️ 拉取 {symbol} 成交異常: {exc}")
            break

    unique_trades = {}
    for t in all_trades:
        unique_trades[str(t["id"])] = t

    sorted_trades = sorted(unique_trades.values(), key=lambda x: x["timestamp"])
    return sorted_trades


def calculate_trade_metrics(trades):
    """根據幣安真實成交流水計算成交筆數、手續費、成交量與已實現盈虧"""
    total_trades = len(trades)
    buy_count = 0
    sell_count = 0
    total_volume = Decimal("0")
    total_fees = Decimal("0")
    total_realized_pnl = Decimal("0")

    buy_queue = collections.deque()
    sell_queue = collections.deque()
    cycles = 0

    for t in trades:
        side = t["side"].lower()
        price = Decimal(str(t["price"]))
        amount = Decimal(str(t["amount"]))
        vol = price * amount
        total_volume += vol

        fee_cost = Decimal(str((t.get("fee") or {}).get("cost") or 0.0))
        total_fees += fee_cost

        info_pnl = (t.get("info") or {}).get("realizedPnl")
        if info_pnl is not None:
            total_realized_pnl += Decimal(str(info_pnl))

        if side == "buy":
            buy_count += 1
            if sell_queue:
                sell_queue.popleft()
                cycles += 1
            else:
                buy_queue.append((price, amount))
        else:
            sell_count += 1
            if buy_queue:
                buy_queue.popleft()
                cycles += 1
            else:
                sell_queue.append((price, amount))

    return {
        "total_trades": total_trades,
        "buy_count": buy_count,
        "sell_count": sell_count,
        "total_volume": float(total_volume),
        "total_fees": float(total_fees),
        "total_realized_pnl": float(total_realized_pnl),
        "cycles": cycles,
    }


def sync_bot_trades(conn, client, bot_info, execute: bool = False):
    bot_id = bot_info["bot_id"]
    symbol = bot_info["symbol"]
    config_path = bot_info["config_path"]
    since_ms = bot_info["since_ms"]

    print(f"\n📡 正在向幣安拉取 {symbol} ({bot_id}) 真實成交流水...")
    trades = fetch_all_trades_for_symbol(client, symbol, since_ms)
    metrics = calculate_trade_metrics(trades)

    print(f"  ✓ 取得 {len(trades)} 筆幣安真實驗證成交！")
    print(f"    - 買單: {metrics['buy_count']} 筆, 賣單: {metrics['sell_count']} 筆, 完成套利循環: {metrics['cycles']} 次")
    print(f"    - 累計成交額: ${metrics['total_volume']:,.2f}")
    print(f"    - 累計手續費: ${metrics['total_fees']:.4f}")
    print(f"    - 幣安結算盈虧: ${metrics['total_realized_pnl']:+.4f}")

    if not execute:
        return metrics

    cursor = conn.cursor()

    # 1. 確保 bots 表中 account_mode 為 live
    cursor.execute(
        "UPDATE bots SET account_mode = 'live', exchange = 'binance', "
        "config_path = ?, status = 'running' WHERE bot_id = ?",
        (config_path, bot_id)
    )

    # 2. 清理該 bot 舊有的記錄，寫入幣安真值
    cursor.execute("DELETE FROM trade_fills WHERE bot_id = ?", (bot_id,))

    # 3. 批量插入真實成交流水
    insert_rows = []
    for t in trades:
        fill_id = f"binance_{t['id']}"
        order_id = str(t["order"])
        client_order_id = (t.get("info") or {}).get("clientOrderId") or str(t["order"])
        side = t["side"].lower()
        role = "grid"
        price = float(t["price"])
        quantity = float(t["amount"])
        fee = float((t.get("fee") or {}).get("cost") or 0.0)
        fee_currency = (t.get("fee") or {}).get("currency") or "USDT"
        realized_pnl = float((t.get("info") or {}).get("realizedPnl") or 0.0)
        executed_at = float(t["timestamp"] / 1000.0)

        insert_rows.append((
            fill_id, bot_id, order_id, client_order_id, side, role,
            price, quantity, fee, fee_currency, realized_pnl, executed_at
        ))

    cursor.executemany(
        """
        INSERT OR REPLACE INTO trade_fills (
            fill_id, bot_id, exchange_order_id, client_order_id, side, role,
            price, quantity, fee, fee_currency, realized_pnl, executed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        insert_rows
    )

    # 4. 更新 bot_positions 表
    cursor.execute(
        """
        UPDATE bot_positions SET
            completed_cycles = ?,
            realized_profit = ?,
            total_fees = ?,
            updated_at = strftime('%s', 'now')
        WHERE bot_id = ?
        """,
        (metrics["cycles"], metrics["total_realized_pnl"], metrics["total_fees"], bot_id)
    )

    # 5. 更新 bot_snapshots payload
    cursor.execute("SELECT payload FROM bot_snapshots WHERE bot_id = ?", (bot_id,))
    row = cursor.fetchone()
    if row:
        try:
            payload = json.loads(row[0])
            if "filled_orders" not in payload or not isinstance(payload["filled_orders"], dict):
                payload["filled_orders"] = {}
            payload["filled_orders"]["buy"] = metrics["buy_count"]
            payload["filled_orders"]["sell"] = metrics["sell_count"]
            payload["filled_orders"]["cycles"] = metrics["cycles"]

            if "profit" not in payload or not isinstance(payload["profit"], dict):
                payload["profit"] = {}
            payload["profit"]["grid_profit"] = metrics["total_realized_pnl"]
            payload["profit"]["realized"] = metrics["total_realized_pnl"]
            payload["profit"]["fees"] = metrics["total_fees"]
            unrealized = float(payload.get("profit", {}).get("unrealized") or 0.0)
            total_profit = round(metrics["total_realized_pnl"] + unrealized, 4)
            net_profit = round(total_profit - metrics["total_fees"], 4)
            payload["profit"]["total"] = total_profit
            payload["profit"]["net"] = net_profit
            initial_cap = float(payload.get("balance", {}).get("total") or 0.0)
            if initial_cap > 0:
                payload["profit"]["rate"] = round((net_profit / initial_cap) * 100, 4)

            cursor.execute(
                "UPDATE bot_snapshots SET payload = ?, updated_at = strftime('%s', 'now') WHERE bot_id = ?",
                (json.dumps(payload, ensure_ascii=False), bot_id)
            )
        except Exception as e:
            print(f"  ⚠️ 更新 bot_snapshots 失敗: {e}")

    # 6. 更新 grid_runtime_snapshots
    cursor.execute("SELECT payload FROM grid_runtime_snapshots WHERE config_path = ?", (config_path,))
    row = cursor.fetchone()
    if row:
        try:
            payload = json.loads(row[0])
            if "filled_orders" not in payload or not isinstance(payload["filled_orders"], dict):
                payload["filled_orders"] = {}
            payload["filled_orders"]["buy"] = metrics["buy_count"]
            payload["filled_orders"]["sell"] = metrics["sell_count"]
            payload["filled_orders"]["cycles"] = metrics["cycles"]

            if "profit" not in payload or not isinstance(payload["profit"], dict):
                payload["profit"] = {}
            payload["profit"]["grid_profit"] = metrics["total_realized_pnl"]
            payload["profit"]["realized"] = metrics["total_realized_pnl"]
            payload["profit"]["fees"] = metrics["total_fees"]
            payload["volume"] = metrics["total_volume"]

            unrealized = float(payload.get("profit", {}).get("unrealized") or 0.0)
            total_profit = round(metrics["total_realized_pnl"] + unrealized, 4)
            net_profit = round(total_profit - metrics["total_fees"], 4)
            payload["profit"]["total"] = total_profit
            payload["profit"]["net"] = net_profit
            initial_cap = float(payload.get("balance", {}).get("total") or 0.0)
            if initial_cap > 0:
                payload["profit"]["rate"] = round((net_profit / initial_cap) * 100, 4)

            cursor.execute(
                "UPDATE grid_runtime_snapshots SET payload = ?, updated_at = strftime('%s', 'now') WHERE config_path = ?",
                (json.dumps(payload, ensure_ascii=False), config_path)
            )
        except Exception as e:
            print(f"  ⚠️ 更新 grid_runtime_snapshots 失敗: {e}")

    conn.commit()
    print(f"  ✅ 成功回填 {len(insert_rows)} 筆真實成交流水至 SQLite！")
    return metrics


def main():
    parser = argparse.ArgumentParser(description="幣安實盤成交流水同步回填工具")
    parser.add_argument("--execute", action="store_true", help="確認執行回填寫入資料庫")
    parser.add_argument("--bot-id", type=str, help="指定單一 bot_id 進行同步")
    args = parser.parse_args()

    client = init_binance_client()
    conn = sqlite3.connect(DB_PATH)

    bots_to_sync = LIVE_BOTS
    if args.bot_id:
        bots_to_sync = [b for b in LIVE_BOTS if b["bot_id"] == args.bot_id]

    print("=" * 80)
    print("🚀 開始從幣安交易所同步真實網格交易流水")
    print(f"模式: {'⚡ EXECUTE (正式寫入資料庫)' if args.execute else '🔍 DRY-RUN (檢視預覽，不更動資料庫)'}")
    print("=" * 80)

    total_synced_fills = 0
    for bot_info in bots_to_sync:
        m = sync_bot_trades(conn, client, bot_info, execute=args.execute)
        total_synced_fills += m["total_trades"]

    conn.close()
    print("\n" + "=" * 80)
    if args.execute:
        print(f"🎉 全部 5 個實盤機器人真實交易已成功從幣安回填完成！共同步 {total_synced_fills} 筆真實驗證成交。")
        print("請重啟 Web UI 或等待前端輪詢更新以載入最新數據。")
    else:
        print(f"預覽完成！共找到 {total_synced_fills} 筆真實成交。")
        print("👉 若要確認寫入資料庫，請帶參數：python tools/sync_binance_grid_trades.py --execute")
    print("=" * 80)


if __name__ == "__main__":
    main()
