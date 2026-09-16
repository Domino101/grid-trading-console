"""P4-1: 檔案型帳本 → Canonical Ledger 回填遷移 (side-file backfill).

CoinFull Prime 在 0.3.2～0.3.5 之間把「專屬持倉籃子 / 風險閘門歷史 / 換倉權益
快照 / 排程器狀態」記在 ``data/`` 下的 JSON 與 JSONL 檔案（記帳孤島）。
0.3.6 起這四類資料的 **唯一真相源是 canonical ledger**，檔案僅降為匯出鏡像。

本模組負責一次性（且冪等）把既有檔案內容搬進資料庫：

===============================  ============================================
檔案                             目標表
===============================  ============================================
coinfull_prime_active_basket.json  ``bot_baskets``
coinfull_prime_regime_history.jsonl ``bot_regime_decisions`` (+ ``bot_events``)
coinfull_prime_run_snapshots.jsonl  ``bot_snapshots`` / ``bot_snapshot_history``
coinfull_prime_scheduler.json       ``bot_runtime_state``
===============================  ============================================

冪等性：籃子為 upsert；閘門決策以 ``dedupe_key=regime:{account}:{timestamp}``
去重；快照回填以 ``bot_runtime_state`` 的 ``backfill:*`` 標記記錄已匯入行數；
排程狀態為 KV upsert。因此可安全重複執行（CLI / API / 服務啟動自我修復）。
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.infrastructure.database.models import BasketEntry, RegimeDecisionRecord
from core.logging import get_logger

logger = get_logger(__name__)

BASKET_FILE = "coinfull_prime_active_basket.json"
REGIME_FILE = "coinfull_prime_regime_history.jsonl"
SNAPSHOT_FILE = "coinfull_prime_run_snapshots.jsonl"
SCHEDULER_FILE = "coinfull_prime_scheduler.json"

CFP_BOT_PREFIX = "coinfull_prime"
SCHEDULER_BOT_ID = "coinfull_prime:scheduler"


def default_data_dir() -> Path:
    """專案 ``data/`` 目錄（與各策略既有落地路徑一致）。"""
    return Path(__file__).resolve().parent.parent.parent.parent / "data"


def _parse_ts(value: Any) -> Optional[float]:
    """容忍 ISO-8601 字串 / epoch 秒 / None。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (TypeError, ValueError):
        return None


def _read_jsonl(path: Path) -> List[dict]:
    rows: List[dict] = []
    if not path.exists():
        return rows
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    return rows


def backfill_coinfull_prime_side_files(
    data_dir: Optional[Any] = None,
    ledger: Optional[Any] = None,
    dry_run: bool = False,
    force: bool = False,
) -> Dict[str, Any]:
    """把 CoinFull Prime 檔案型帳本回填進 canonical ledger（冪等）。

    Args:
        data_dir: ``data/`` 目錄（預設為專案 data 目錄）。
        ledger:   ``StrategyLedger`` 實例（預設取單例）。
        dry_run:  只統計、不寫入。
        force:    忽略 ``backfill:*`` 標記，強制重跑快照回填。

    Returns:
        統計 dict（可直接序列化為 API 回應 / CLI 輸出）。
    """
    directory = Path(data_dir) if data_dir else default_data_dir()
    if ledger is None:
        from core.infrastructure.database.strategy_ledger import StrategyLedger

        ledger = StrategyLedger.instance()

    stats: Dict[str, Any] = {
        "data_dir": str(directory),
        "dry_run": bool(dry_run),
        "files_found": {},
        "baskets": {"accounts": 0, "symbols": 0},
        "regime_decisions": {"imported": 0, "skipped_duplicate": 0},
        "run_snapshots": {"imported": 0, "skipped_marker": 0},
        "scheduler_states": {"imported": 0},
        "errors": [],
        "completed_at": time.time(),
    }

    for name in (BASKET_FILE, REGIME_FILE, SNAPSHOT_FILE, SCHEDULER_FILE):
        stats["files_found"][name] = (directory / name).exists()

    # ── 1. 專屬持倉籃子 ────────────────────────────────────────────────────
    basket_path = directory / BASKET_FILE
    if basket_path.exists():
        try:
            with open(basket_path, "r", encoding="utf-8") as handle:
                data = json.load(handle) or {}
            for account_id, payload in (data or {}).items():
                if not isinstance(payload, dict):
                    continue
                updated_at = _parse_ts(payload.get("updated_at")) or time.time()
                bot_id = f"{CFP_BOT_PREFIX}:{account_id}"
                rich = payload.get("entries")
                if isinstance(rich, list) and rich:
                    # 0.3.6 鏡像格式：含 side / quantity / entry_price / realized_pnl，
                    # 可無損還原（DB 遺失、僅剩鏡像時的災復路徑）
                    entries = []
                    for row in rich:
                        if not isinstance(row, dict):
                            continue
                        sym = str(row.get("symbol") or "").strip()
                        if not sym:
                            continue
                        entry_price = row.get("entry_price", row.get("avg_price"))
                        entries.append(
                            BasketEntry(
                                bot_id=bot_id,
                                account_id=str(account_id),
                                symbol=sym,
                                side=str(row.get("side") or "long"),
                                quantity=float(row.get("quantity", row.get("qty", 0.0)) or 0.0),
                                entry_price=float(entry_price) if entry_price is not None else None,
                                notional_usdt=float(row.get("notional_usdt", 0.0) or 0.0),
                                realized_pnl=float(row.get("realized_pnl", 0.0) or 0.0),
                                status=str(row.get("status") or "active"),
                                opened_at=_parse_ts(row.get("opened_at")) or updated_at,
                                updated_at=updated_at,
                            )
                        )
                else:
                    # 0.3.5 以前的檔案型帳本：只有受管標的名單（無部位細節）
                    entries = [
                        BasketEntry(
                            bot_id=bot_id,
                            account_id=str(account_id),
                            symbol=str(sym),
                            side="long",
                            quantity=0.0,
                            entry_price=None,
                            notional_usdt=0.0,
                            status="active",
                            opened_at=updated_at,
                            updated_at=updated_at,
                        )
                        for sym in (payload.get("managed_symbols") or [])
                        if sym
                    ]
                if not entries:
                    continue
                stats["baskets"]["accounts"] += 1
                stats["baskets"]["symbols"] += len(entries)
                if not dry_run:
                    ledger.ensure_bot(
                        bot_id=bot_id,
                        strategy_type="coinfull_prime",
                        symbol="BASKET_16",
                        exchange=str(account_id),
                        account_mode="live" if "live" in str(account_id) else "paper",
                        status="migrated",
                    )
                    # close_missing=False：回填為非破壞性，不覆寫 DB 既有的活躍成分
                    ledger.save_basket(bot_id, str(account_id), entries, close_missing=False)
        except Exception as exc:
            stats["errors"].append(f"{BASKET_FILE}: {type(exc).__name__}: {exc}")

    # ── 2. 風險閘門決策歷史 ────────────────────────────────────────────────
    regime_rows = _read_jsonl(directory / REGIME_FILE)
    for row in regime_rows:
        account_id = str(row.get("account_id") or "paper")
        bot_id = f"{CFP_BOT_PREFIX}:{account_id}"
        decided_at = _parse_ts(row.get("timestamp")) or time.time()
        dedupe_key = f"regime:{account_id}:{row.get('timestamp') or int(decided_at)}"
        existing = [] if dry_run else ledger.load_regime_decisions(bot_id=bot_id, limit=100000)
        if any(getattr(r, "dedupe_key", None) == dedupe_key for r in existing):
            stats["regime_decisions"]["skipped_duplicate"] += 1
            continue
        stats["regime_decisions"]["imported"] += 1
        if dry_run:
            continue
        record = RegimeDecisionRecord(
            bot_id=bot_id,
            account_id=account_id,
            decided_at=decided_at,
            pts=int(row.get("pts", 0) or 0),
            scale=float(row.get("scale", 1.0) or 1.0),
            allow_new_entries=bool(row.get("allow_new_entries", True)),
            force_flatten=bool(row.get("force_flatten", False)),
            crash_defer=bool(row.get("crash_defer", False)),
            degraded=bool(row.get("degraded", False)),
            mode=row.get("mode"),
            reason="; ".join(row.get("reasons") or [])[:2000] or None,
            payload=row,
            dedupe_key=dedupe_key,
        )
        ledger.record_regime_decision(record)

    # ── 3. 換倉權益/持倉快照 ───────────────────────────────────────────────
    snapshot_rows = _read_jsonl(directory / SNAPSHOT_FILE)
    marker_key = f"backfill:{SNAPSHOT_FILE}"
    marker = {} if dry_run else (ledger.get_state(SCHEDULER_BOT_ID, marker_key, {}) or {})
    if snapshot_rows and not force and int(marker.get("lines", -1)) == len(snapshot_rows):
        stats["run_snapshots"]["skipped_marker"] = len(snapshot_rows)
    else:
        # 依時間升序回放，使 bot_snapshots 最終停在最新一筆、歷史表保留完整時序
        ordered = sorted(snapshot_rows, key=lambda r: _parse_ts(r.get("timestamp")) or 0.0)
        for row in ordered:
            account_id = str(row.get("account_id") or "paper")
            bot_id = f"{CFP_BOT_PREFIX}:{account_id}"
            ts = _parse_ts(row.get("timestamp")) or time.time()
            payload: Dict[str, Any] = {
                "bot_id": bot_id,
                "strategy_type": "coinfull_prime",
                "account_id": account_id,
                "snapshot_kind": "run",
                "timestamp": row.get("timestamp"),
                "regime": row.get("regime"),
                "rebalance": row.get("rebalance"),
                "portfolio": row.get("portfolio"),
                # 籃子策略的淨曝險/UPnL 可正可負，不適用累計計數單調護欄（P1-4）
                "_monotonic_bypass": True,
            }
            stats["run_snapshots"]["imported"] += 1
            if dry_run:
                continue
            ledger.ensure_bot(
                bot_id=bot_id,
                strategy_type="coinfull_prime",
                symbol="BASKET_16",
                exchange=account_id,
                account_mode="live" if "live" in account_id else "paper",
                status="migrated",
            )
            ledger.save_snapshot(bot_id, f"config/coinfull_prime/{account_id}", payload, timestamp=ts, force_history=True)
        if not dry_run and snapshot_rows:
            ledger.set_state(
                SCHEDULER_BOT_ID,
                marker_key,
                {"lines": len(snapshot_rows), "imported_at": time.time(), "source": str(directory / SNAPSHOT_FILE)},
            )

    # ── 4. 排程器狀態 ─────────────────────────────────────────────────────
    scheduler_path = directory / SCHEDULER_FILE
    if scheduler_path.exists():
        try:
            with open(scheduler_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle) or {}
            if isinstance(payload, dict) and payload:
                stats["scheduler_states"]["imported"] = 1
                if not dry_run:
                    ledger.set_state(SCHEDULER_BOT_ID, "scheduler", payload)
        except Exception as exc:
            stats["errors"].append(f"{SCHEDULER_FILE}: {type(exc).__name__}: {exc}")

    if stats["errors"]:
        logger.warning(f"⚠️ [Backfill] CoinFull Prime 檔案型帳本回填完成但有錯誤: {stats['errors']}")
    else:
        logger.info(
            "✅ [Backfill] CoinFull Prime 檔案型帳本已收斂至 canonical ledger: "
            f"籃子 {stats['baskets']['accounts']} 個帳號/{stats['baskets']['symbols']} 成分, "
            f"閘門決策 {stats['regime_decisions']['imported']} 筆, "
            f"換倉快照 {stats['run_snapshots']['imported']} 筆, "
            f"排程狀態 {stats['scheduler_states']['imported']} 筆"
        )
    return stats


def export_ledger_to_side_files(
    data_dir: Optional[Any] = None,
    ledger: Optional[Any] = None,
    accounts: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """反向匯出：把 DB 真相源寫回檔案鏡像（供人工查看 / 離線分析）。

    這是「DB 為唯一真相源、檔案僅為鏡像」的落地工具：鏡像永遠由 DB 產生，
    不再由策略直接寫入，因此不可能出現檔案與 DB 分歧的雙來源。
    """
    directory = Path(data_dir) if data_dir else default_data_dir()
    if ledger is None:
        from core.infrastructure.database.strategy_ledger import StrategyLedger

        ledger = StrategyLedger.instance()
    directory.mkdir(parents=True, exist_ok=True)
    account_list = accounts or ["paper", "binance_testnet", "binance_live"]
    exported: Dict[str, Any] = {"basket_symbols": 0, "regime_decisions": 0, "run_snapshots": 0}

    basket_mirror: Dict[str, Any] = {}
    for account_id in account_list:
        entries = ledger.load_basket(f"{CFP_BOT_PREFIX}:{account_id}", account_id=account_id, active_only=True)
        if not entries:
            continue
        basket_mirror[account_id] = {
            "managed_symbols": sorted({e.symbol for e in entries}),
            "count": len({e.symbol for e in entries}),
            "source": "canonical_ledger:bot_baskets",
            "entries": [
                {
                    "symbol": e.symbol,
                    "side": e.side,
                    "quantity": e.quantity,
                    "entry_price": e.entry_price,
                    "notional_usdt": e.notional_usdt,
                    "realized_pnl": e.realized_pnl,
                    "status": e.status,
                }
                for e in entries
            ],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        exported["basket_symbols"] += len(entries)
    if basket_mirror:
        _write_json(directory / BASKET_FILE, basket_mirror)

    decisions = ledger.load_regime_decisions(limit=5000)
    if decisions:
        lines = []
        for record in sorted(decisions, key=lambda r: r.decided_at):
            payload = dict(record.payload or {})
            payload.update(
                {
                    "account_id": record.account_id,
                    "timestamp": datetime.fromtimestamp(record.decided_at, tz=timezone.utc).isoformat(),
                    "source": "canonical_ledger:bot_regime_decisions",
                }
            )
            lines.append(json.dumps(payload, ensure_ascii=False, default=str))
        _write_text(directory / REGIME_FILE, "\n".join(lines) + "\n")
        exported["regime_decisions"] = len(decisions)

    snapshots: List[str] = []
    for account_id in account_list:
        for payload, ts in ledger.load_history(f"{CFP_BOT_PREFIX}:{account_id}", limit=5000):
            if not isinstance(payload, dict) or payload.get("snapshot_kind") != "run":
                continue
            line = {
                "timestamp": payload.get("timestamp")
                or datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
                "account_id": payload.get("account_id") or account_id,
                "regime": payload.get("regime"),
                "rebalance": payload.get("rebalance"),
                "portfolio": payload.get("portfolio"),
                "source": "canonical_ledger:bot_snapshot_history",
            }
            snapshots.append(json.dumps(line, ensure_ascii=False, default=str))
    if snapshots:
        _write_text(directory / SNAPSHOT_FILE, "\n".join(snapshots) + "\n")
        exported["run_snapshots"] = len(snapshots)

    scheduler_state = ledger.get_state(SCHEDULER_BOT_ID, "scheduler")
    if isinstance(scheduler_state, dict) and scheduler_state:
        mirror = dict(scheduler_state)
        mirror["source"] = "canonical_ledger:bot_runtime_state"
        _write_json(directory / SCHEDULER_FILE, mirror)

    exported["data_dir"] = str(directory)
    return exported


def _write_json(path: Path, payload: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False, default=str)
    os.replace(tmp, path)


def _write_text(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(tmp, path)


__all__ = [
    "BASKET_FILE",
    "REGIME_FILE",
    "SCHEDULER_FILE",
    "SNAPSHOT_FILE",
    "backfill_coinfull_prime_side_files",
    "default_data_dir",
    "export_ledger_to_side_files",
]
