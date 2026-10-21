#!/usr/bin/env python3
"""CoinFull Prime legacy ledger repair (L2/L5/L6 scope).

只修 CoinFull Prime 的歷史資料，不動 grid/DCA：
* L6：為既有 daily_rebalance/regime_gate 事件補 dedupe_key（只處理 NULL）。
* L5：由 trade_fills 補齊缺少的 canonical fill 事件（只補缺，不重複）。
* L2：註冊 legacy bot_id=coinfull_prime（archived），讓 12 筆舊事件有身分。

預設 dry-run；加 --apply 才寫入。SQLite 模式會先備份 DB。
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.infrastructure.database.strategy_ledger import StrategyLedger  # noqa: E402

COINFULL_ACCOUNTS = ("paper", "binance_testnet", "binance_live")


def _fill_gaps(ledger: StrategyLedger, bot_ids: List[str]) -> Dict[str, int]:
    gaps: Dict[str, int] = {}
    for bot_id in bot_ids:
        fills = ledger.get_fills(bot_id, limit=100000)
        if not fills:
            continue
        events = ledger.get_events(bot_id, limit=1000000)
        existing = {
            str((e.payload or {}).get("fill_id"))
            for e in events
            if e.event_type == "fill" and isinstance(e.payload, dict)
        }
        missing = sum(1 for f in fills if f.fill_id not in existing)
        if missing:
            gaps[bot_id] = missing
    return gaps


def _backup_sqlite(ledger: StrategyLedger) -> Path | None:
    health = ledger.health() or {}
    if str(health.get("backend", "")).lower() != "sqlite":
        print("PostgreSQL backend: 不自動備份，請自行確認 snapshot/backup。")
        return None
    db_path = health.get("database_path") or str(PROJECT_ROOT / "data" / "web_ui_runtime.sqlite3")
    source = Path(str(db_path))
    if not source.exists():
        print(f"備份跳過：DB 不存在 {source}")
        return None
    backup_dir = PROJECT_ROOT / "data" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = backup_dir / f"{source.stem}_pre_coinfull_legacy_repair_{stamp}{source.suffix}"
    shutil.copy2(source, target)
    print(f"已備份 DB: {target}")
    return target


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CoinFull Prime legacy ledger repair (dry-run by default)")
    parser.add_argument("--apply", action="store_true", help="實際寫入；預設只顯示 will-fix 數量")
    parser.add_argument("--bot-prefix", default="coinfull_prime", help="事件 bot_id 前綴")
    parser.add_argument("--no-backup", action="store_true", help="SQLite 模式不備份（不建議）")
    args = parser.parse_args(argv)

    ledger = StrategyLedger.instance()
    health = ledger.health() or {}
    print(f"backend={health.get('backend')} database={health.get('database_path')}")

    bot_ids = [f"{args.bot_prefix}:{acc}" for acc in COINFULL_ACCOUNTS]
    dedupe_gaps = ledger.count_legacy_event_dedupe_gaps(args.bot_prefix)
    fill_gaps = _fill_gaps(ledger, bot_ids)
    legacy_bot = ledger.get_bot(args.bot_prefix)

    print(f"L6 dedupe gaps ({args.bot_prefix}*): {dedupe_gaps}")
    print(f"L5 fill event gaps: {fill_gaps or '{}'}")
    print(f"L2 legacy bot registered: {legacy_bot is not None}")

    if not args.apply:
        print("dry-run: 未寫入。確認後加 --apply。")
        return 0

    if not args.no_backup:
        _backup_sqlite(ledger)

    if legacy_bot is None:
        ledger.ensure_bot(
            bot_id=args.bot_prefix,
            strategy_type="coinfull_prime",
            symbol="BASKET_16",
            exchange="legacy",
            account_mode="paper",
            status="archived",
        )
        print(f"已註冊 legacy bot: {args.bot_prefix} (archived)")

    if dedupe_gaps:
        fixed = ledger.backfill_legacy_event_dedupe_keys(args.bot_prefix)
        print(f"已補 dedupe_key: {fixed}")

    for bot_id, _ in fill_gaps.items():
        fixed = ledger.backfill_missing_fill_events(bot_id)
        print(f"已補 fill 事件 ({bot_id}): {fixed}")

    print("完成。請重跑 tools/ledger_consistency_audit.py --markdown --fail-on high 驗證。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
