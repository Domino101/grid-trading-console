#!/usr/bin/env python3
"""CoinFull Prime live basket reconciliation repair.

用法：
    python3 tools/coinfull_reconcile_repair.py --account-id binance_live
    python3 tools/coinfull_reconcile_repair.py --account-id binance_live --apply

只處理兩類帳本殘留：
* 帳本 quantity>0，但交易所在該 symbol 已無部位（reason=missing_on_exchange）。
* 帳本 quantity<=0 的 active 幽靈成分（reason=zero_quantity）。

不處理方向/數量不一致（quantity_mismatch）；那需要人工判斷。
預設 dry-run；--apply 才寫入，SQLite 會先備份。
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:  # 與 web_ui/backend/main.py 相同：載入 .env 才會啟用 GRID_LIVE_TRADING_ENABLED
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env", override=False)
except Exception:
    pass

from core.infrastructure.database.strategy_ledger import StrategyLedger  # noqa: E402
from core.services.coinfull_prime.rebalancer import PortfolioRebalancer  # noqa: E402
from core.services.coinfull_prime.service import CoinFullPrimeService  # noqa: E402


def _backup_sqlite(ledger: StrategyLedger) -> Path | None:
    health = ledger.health() or {}
    if str(health.get("backend", "")).lower() != "sqlite":
        print("PostgreSQL backend: 不自動備份，請自行確認 snapshot/backup。")
        return None
    source = Path(str(health.get("database_path") or PROJECT_ROOT / "data" / "web_ui_runtime.sqlite3"))
    if not source.exists():
        print(f"備份跳過：DB 不存在 {source}")
        return None
    backup_dir = PROJECT_ROOT / "data" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = backup_dir / f"{source.stem}_pre_coinfull_reconcile_repair_{stamp}{source.suffix}"
    shutil.copy2(source, target)
    print(f"已備份 DB: {target}")
    return target


async def _collect(account_id: str):
    service = CoinFullPrimeService()
    await service.initialize()
    positions = await service.get_positions(account_id)
    if not getattr(service, "_last_positions_ok", False):
        raise RuntimeError(
            "取得交易所持倉失敗或無 live adapter；拒絕用空持倉推論 missing，"
            "請先確認 GRID_LIVE_TRADING_ENABLED 與 API 金鑰。"
        )
    ledger = StrategyLedger.instance()
    bot_id = f"coinfull_prime:{account_id}"
    entries = ledger.load_basket(bot_id, account_id=account_id, active_only=True)
    return service, ledger, bot_id, positions, entries


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CoinFull Prime live basket reconciliation repair")
    parser.add_argument("--account-id", default="binance_live")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args(argv)

    service, ledger, bot_id, positions, entries = asyncio.run(_collect(args.account_id))
    exchange_map: Dict[str, dict] = {
        PortfolioRebalancer._normalize_symbol(p.get("symbol", "")): p
        for p in positions if p.get("symbol")
    }

    missing: List[Tuple[str, float, str]] = []
    zero: List[str] = []
    mismatch: List[dict] = []
    for entry in entries:
        sym = PortfolioRebalancer._normalize_symbol(entry.symbol)
        qty = float(entry.quantity or 0.0)
        if qty <= 0:
            zero.append(sym)
            continue
        pos = exchange_map.get(sym)
        exchange_signed = float(pos.get("signed_size", 0.0)) if pos else 0.0
        if not pos or abs(exchange_signed) < 1e-8:
            missing.append((sym, qty, str(entry.side)))
            continue
        ledger_signed = qty if str(entry.side).lower() == "long" else -qty
        if abs(ledger_signed - exchange_signed) > max(1e-8, abs(ledger_signed) * 0.01):
            mismatch.append({
                "symbol": sym,
                "ledger_qty": ledger_signed,
                "exchange_qty": exchange_signed,
            })

    print(f"account={args.account_id} bot_id={bot_id}")
    print(f"missing_on_exchange={[(s, q, side) for s, q, side in missing]}")
    print(f"zero_quantity_active={zero}")
    print(f"quantity_mismatch={mismatch}（不自動處理）")

    if not args.apply:
        print("dry-run: 未寫入。確認後加 --apply。")
        return 0

    if not args.no_backup:
        _backup_sqlite(ledger)

    now = time.time()
    closed = 0
    for sym, qty, side in missing:
        info = ledger.close_basket_entry(bot_id, args.account_id, sym, reason="missing_on_exchange")
        if not info:
            print(f"跳過（非 active）: {sym}")
            continue
        ledger.append_event(
            bot_id,
            "reconcile_close",
            payload={
                "symbol": sym,
                "side": side,
                "previous_quantity": qty,
                "reason": "missing_on_exchange",
                "realized_pnl": "unknown_not_fabricated",
                "note": "exchange position absent; ledger closed by reconciliation repair",
            },
            dedupe_key=f"reconcile_close:missing:{args.account_id}:{sym}:{int(now)}",
        )
        print(f"已關閉 missing: {sym} qty={qty}")
        closed += 1

    for sym in zero:
        info = ledger.close_basket_entry(bot_id, args.account_id, sym, reason="zero_quantity")
        if not info:
            print(f"跳過（非 active）: {sym}")
            continue
        ledger.append_event(
            bot_id,
            "reconcile_close",
            payload={
                "symbol": sym,
                "previous_quantity": 0.0,
                "reason": "zero_quantity",
                "note": "active basket entry had no position; closed as ghost",
            },
            dedupe_key=f"reconcile_close:zero:{args.account_id}:{sym}:{int(now)}",
        )
        print(f"已關閉 zero-qty ghost: {sym}")
        closed += 1

    try:
        service._export_basket_mirror(args.account_id)
        print(f"已更新檔案鏡像: {args.account_id}")
    except Exception as ex:
        print(f"更新鏡像失敗（DB 已修復）: {ex}")

    print(f"完成，關閉 {closed} 筆。建議呼叫 POST /api/v1/coinfull-prime/reconcile 複查。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
