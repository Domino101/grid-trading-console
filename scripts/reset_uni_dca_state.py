import json
import sqlite3
import sys
import redis
import base64

BOT_ID = "dca-binance-long-uni-usdc-usdc-20260919112626"
DB_PATH = "/root/grid-trading-console/data/web_ui_runtime.sqlite3"
CONFIG_PATH = "config/dca/dca-binance-long-uni-usdc-usdc-20260919112626.yaml"

def reset_to_clean_armed_state():
    print(f"Resetting state for {BOT_ID}...")
    
    # 1. Clear Redis
    r = redis.Redis(host="localhost", port=6379, db=0)
    b64_path = base64.urlsafe_b64encode(CONFIG_PATH.encode("utf-8")).decode("ascii").rstrip("=")
    del_keys = [
        f"grid:state:{BOT_ID}",
        f"grid:state:path:{b64_path}",
        "grid:leader:b322e202b5764d49b89626d607436a98"
    ]
    for k in del_keys:
        d = r.delete(k)
        print(f"  Deleted Redis key {k}: {d}")

    # 2. SQLite
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("UPDATE bots SET status = 'running' WHERE bot_id = ?", (BOT_ID,))
    print(f"  Updated bots rows: {cur.rowcount}")

    cur.execute("""
        UPDATE bot_positions
        SET quantity = 0.0,
            average_cost = 0.0,
            total_cost = 0.0,
            entry_price = NULL,
            active_direction = NULL,
            completed_cycles = 1
        WHERE bot_id = ?
    """, (BOT_ID,))
    print(f"  Updated bot_positions rows: {cur.rowcount}")

    row = cur.execute("SELECT payload FROM bot_snapshots WHERE bot_id = ?", (BOT_ID,)).fetchone()
    if row:
        data = json.loads(row[0])
        data["status"] = "armed"
        data["active_direction"] = None
        data["entry_price"] = None
        data["position"] = {
            "quantity": 0.0,
            "average_cost": 0.0,
            "total_cost": 0.0
        }
        data["open_orders"] = []
        data["fills"] = []
        data["ladder_slots"] = []
        new_payload = json.dumps(data)
        cur.execute("UPDATE bot_snapshots SET payload = ? WHERE bot_id = ?", (new_payload, BOT_ID))
        print("  Updated bot_snapshots to clean armed state.")

    conn.commit()
    conn.close()
    print("Reset finished.")

if __name__ == "__main__":
    reset_to_clean_armed_state()
