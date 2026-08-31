#!/usr/bin/env python3
"""P3-3: Replay one bot's canonical event stream and diff it against the
stored projection.

Usage:
    python scripts/tools/replay_bot.py --bot-id <bot_id> [--db data/web_ui_runtime.sqlite3] [--format text|json]

Uses DATABASE_URL (PostgreSQL) automatically when set; --db selects a SQLite
file otherwise.  Exit codes: 0 = replay consistent with the stored projection,
2 = divergence detected, 3 = bot not found.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from core.infrastructure.database.event_fold import (  # noqa: E402
    build_timeline,
    diff_projection,
    fold_timeline,
)
from core.infrastructure.database.ledger_repository import LedgerRepository  # noqa: E402


def replay(bot_id: str, db_path: str | None = None) -> dict:
    repo = LedgerRepository(db_path)
    store = repo.store
    if store.get_bot(bot_id) is None:
        return {"bot_id": bot_id, "status": "bot_not_found"}

    timeline = build_timeline(store, bot_id)
    rebuilt = fold_timeline(bot_id, timeline)
    stored = store.get_position(bot_id) or rebuilt.__class__(bot_id=bot_id)
    diffs = diff_projection(stored, rebuilt)

    events_summary = {}
    for e in timeline:
        events_summary[e.event_type] = events_summary.get(e.event_type, 0) + 1

    return {
        "bot_id": bot_id,
        "status": "consistent" if not diffs else "divergent",
        "event_count": len(timeline),
        "events_by_type": events_summary,
        "rebuilt": {
            "quantity": rebuilt.quantity,
            "completed_cycles": rebuilt.completed_cycles,
            "realized_profit": rebuilt.realized_profit,
            "total_fees": rebuilt.total_fees,
        },
        "stored": {
            "quantity": stored.quantity,
            "completed_cycles": stored.completed_cycles,
            "realized_profit": stored.realized_profit,
            "total_fees": stored.total_fees,
        },
        "diffs": {k: {"stored": v[0], "rebuilt": v[1]} for k, v in diffs.items()},
        "generated_at": time.time(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay bot events and diff vs stored projection (P3-3)")
    parser.add_argument("--bot-id", required=True)
    parser.add_argument("--db", default=None, help="SQLite path (ignored when DATABASE_URL is set)")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    args = parser.parse_args()

    report = replay(args.bot_id, args.db)
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"═══ 事件回放報告：{report['bot_id']} ═══")
        if report["status"] == "bot_not_found":
            print("❌ 找不到此 bot（bots 表無紀錄）")
            return 3
        print(f"狀態：{'✅ 一致' if report['status'] == 'consistent' else '❌ 存在差異'}")
        print(f"事件數：{report['event_count']}  分佈：{report['events_by_type']}")
        print(f"重建投影：{report['rebuilt']}")
        print(f"現存投影：{report['stored']}")
        if report["diffs"]:
            print("差異欄位（stored vs rebuilt）：")
            for k, d in report["diffs"].items():
                print(f"  - {k}: {d['stored']} vs {d['rebuilt']}")
    return 0 if report["status"] == "consistent" else (3 if report["status"] == "bot_not_found" else 2)


if __name__ == "__main__":
    raise SystemExit(main())
