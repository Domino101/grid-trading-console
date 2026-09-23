import asyncio
import json
import sqlite3
import sys
import redis

sys.path.insert(0, "/root/grid-trading-console")

from run_multiple_grids import ExchangeAdapterPool

BOT_ID = "dca-binance-long-uni-usdc-usdc-20260919112626"
DB_PATH = "/root/grid-trading-console/data/web_ui_runtime.sqlite3"
CONFIG_PATH = "config/dca/dca-binance-long-uni-usdc-usdc-20260919112626.yaml"

async def check_and_cancel_erroneous_tp():
    pool = ExchangeAdapterPool()
    adapter = await pool.get(CONFIG_PATH)
    orders = await adapter.get_open_orders('UNI/USDC:USDC')
    print(f"Open orders on Binance: {len(orders)}")
    for o in orders:
        cid = getattr(o, "client_id", getattr(o, "clientOrderId", ""))
        side_val = str(getattr(o.side, "value", o.side)).lower()
        print(f"  Order: ID={o.id} side={side_val} price={o.price} amount={o.amount} cid={cid}")
        # If any erroneous SELL or TP order exists > 2.0 UNI, cancel it
        if (side_val == "sell" or "tp" in str(cid)) and float(o.amount) > 2.0:
            print(f"  Cancelling erroneous order {o.id} ({cid}) amount={o.amount}...")
            try:
                res = await adapter.cancel_order(o.id, "UNI/USDC:USDC")
                print(f"  Cancelled {o.id}: {res.status}")
            except Exception as e:
                print(f"  Failed to cancel {o.id}: {e}")
    await pool.disconnect_all()

def remediate_storage():
    print(f"1. Clearing Redis state for {BOT_ID}...")
    r = redis.Redis(host="localhost", port=6379, db=0)
    import base64
    b64_path = base64.urlsafe_b64encode(CONFIG_PATH.encode("utf-8")).decode("ascii").rstrip("=")
    del_keys = [
        f"grid:state:{BOT_ID}",
        f"grid:state:path:{b64_path}",
        "grid:leader:b322e202b5764d49b89626d607436a98"
    ]
    for k in del_keys:
        deleted = r.delete(k)
        print(f"  Deleted Redis key {k}: {deleted}")

    print("2. Calibrating SQLite database...")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Update bots status
    cur.execute("UPDATE bots SET status = 'running' WHERE bot_id = ?", (BOT_ID,))
    print(f"  Updated bots rows: {cur.rowcount}")

    # Update bot_positions: 2.0 UNI, avg_cost = (8.852 + 8.781)/2 = 8.8165
    cur.execute("""
        UPDATE bot_positions
        SET quantity = 2.0,
            average_cost = 8.8165,
            total_cost = 17.633,
            entry_price = 8.852,
            active_direction = 'long',
            completed_cycles = 1
        WHERE bot_id = ?
    """, (BOT_ID,))
    print(f"  Updated bot_positions rows: {cur.rowcount}")

    # Update bot_snapshots
    row = cur.execute("SELECT payload FROM bot_snapshots WHERE bot_id = ?", (BOT_ID,)).fetchone()
    if row:
        data = json.loads(row[0])
        data["status"] = "averaging"
        data["active_direction"] = "long"
        data["entry_price"] = 8.852
        data["position"] = {
            "quantity": 2.0,
            "average_cost": 8.8165,
            "total_cost": 17.633
        }
        # Keep the 4 open safety orders, strip any old tp orders
        data["open_orders"] = [
            o for o in data.get("open_orders", [])
            if "tp" not in str(o.get("client_order_id", "")).lower()
               and str(o.get("role", "")).lower() != "take_profit"
        ]
        # Keep round fills
        data["fills"] = [
            f for f in data.get("fills", [])
            if str(f.get("client_order_id", "")).startswith("dca_95ff0878_long_base_0090")
               or str(f.get("client_order_id", "")).startswith("dca_95ff0878_long_so01_0092")
        ]
        new_payload = json.dumps(data)
        cur.execute("UPDATE bot_snapshots SET payload = ? WHERE bot_id = ?", (new_payload, BOT_ID))
        print("  Updated bot_snapshots payload successfully.")

    conn.commit()
    conn.close()
    print("Remediation complete.")

if __name__ == "__main__":
    asyncio.run(check_and_cancel_erroneous_tp())
    remediate_storage()
