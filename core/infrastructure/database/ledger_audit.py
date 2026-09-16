"""P4-1: 統一記帳一致性稽核 (Unified Ledger Consistency Auditor).

科學化驗證「grid / dca / coinfull_prime 三者是否真的共用同一套資料庫記帳」：
不是檢查程式碼有沒有呼叫 API，而是檢查 **帳本本身的不變式 (invariants)**。

檢查矩陣（check_id / 預設嚴重度）：

=====  ==============================  ========  ==============================
編號   名稱                            嚴重度     意涵
=====  ==============================  ========  ==============================
L1     ORPHAN_FILLS                    High      成交流水指向未註冊的 bot（P1-6 外鍵破壞）
L2     ORPHAN_EVENTS                   High      事件流指向未註冊的 bot
L3     GHOST_BACKEND                   Critical  DATABASE_URL 為 PostgreSQL 但實際寫 SQLite（幽靈資料源）
L4     RAW_STORE_BYPASS                High      策略層繞過統一門面直接開 store / raw sqlite3
L5     FILL_EVENT_GAP                  Medium    有 fills 卻無 canonical fill 事件（退回 legacy 回放）
L6     NON_IDEMPOTENT_EVENTS           Medium    可重跑事件未帶 dedupe_key（重啟/補跑會重複記帳）
L7     SIDE_FILE_DIVERGENCE            High      檔案型帳本與 DB 分歧（記帳孤島殘留）
L8     CONTRACT_VIOLATION              Medium    未滿足該策略的記帳契約（部位/籃子/快照缺位）
L9     PHANTOM_BOTS                    Low       登記了從無記帳活動的機器人身分（污染儀表板）
L10    AUDIT_LOG_HEALTH                High      操作稽核日誌 (grid_audit.db) 缺表或寫入失敗
L11    PNL_IDENTITY                    High      net_pnl ≠ grid_profit + unrealized - fees（會計恆等式）
L12    ACCOUNTING_HEALTH               High      統一記帳層出現寫入失敗（可觀測降級）
L13    DUPLICATE_FILLS                 Medium    同一 client_order_id 被記為多筆不同 fill_id（重複入帳）
L14    MODE_MISMATCH                   Medium    testnet 環境被標記為 live（實盤/測試資金語意錯置）
L15    SCHEMA_DRIFT                    Critical  canonical 表缺失（schema 未遷移）
=====  ==============================  ========  ==============================
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from core.infrastructure.database.strategy_ledger import STRATEGY_ACCOUNTING_CONTRACT, contract_for
from core.logging import get_logger

logger = get_logger(__name__)

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

CANONICAL_TABLES: Tuple[str, ...] = (
    "bots",
    "trade_fills",
    "bot_positions",
    "bot_snapshots",
    "bot_snapshot_history",
    "bot_identity_events",
    "bot_events",
    "bot_baskets",
    "bot_regime_decisions",
    "bot_runtime_state",
)

#: 只代表身分生命週期、不代表記帳活動的事件型別（幽靈機器人判定需排除）
NON_ACCOUNTING_EVENT_TYPES = {"bot_created", "bot_renamed"}

#: 可重跑（必須冪等）的事件型別 → 缺 dedupe_key 即為缺陷
IDEMPOTENT_EVENT_TYPES = {
    "daily_rebalance",
    "manual_rebalance",
    "regime_gate",
    "weekly_rebalance",
}

#: 記帳唯一路徑政策掃描範圍（策略層 / API 層 / 引擎入口）
POLICY_SCAN_ROOTS = ("core/services", "web_ui/backend")
#: 白名單：專案內唯一允許的低階連線點（已套 EnvironmentGuard），不視為繞過
POLICY_ALLOWLIST = {
    "web_ui/backend/lifecycle.py": "connect_sqlite() 是專案唯一的低階 SQLite 連線器（已套 EnvironmentGuard 路徑隔離）",
}
POLICY_SCAN_ENTRYPOINTS = ("run_multiple_grids.py", "run_grid_trading.py", "run_coinfull_prime.py")

#: 維運/遷移工具中「刻意」直接使用低階連線的檔案（非策略記帳路徑，故不在掃描範圍）。
#: 列舉於此以供未來把 ``tools/`` 納入掃描時沿用同一套白名單語意。
TOOLING_RAW_STORE_ALLOWLIST = {
    "scripts/migration/sqlite_to_postgres.py": "SQLite → PostgreSQL 遷移工具，來源必須是實體 SQLite 檔",
    "tools/ledger_consistency_audit.py": "稽核 CLI 的 --db 需直接開啟指定帳本檔（唯讀稽核）",
    "tools/cleanup_paper_phantom_fills.py": "一次性清理工具，直接操作實體 SQLite 檔",
    "tools/rebuild_grid_history_from_fills.py": "歷史重建工具，直接操作實體 SQLite 檔",
    "tools/sync_binance_grid_trades.py": "交易所對帳工具，直接操作實體 SQLite 檔",
}
_RAW_STORE_PATTERN = re.compile(r"\bSqliteLedgerStore\s*\(")
_RAW_SQLITE_PATTERN = re.compile(r"\bsqlite3\.connect\s*\(")


@dataclass
class AuditFinding:
    """單一稽核發現（可直接序列化為 JSON / 渲染為 Markdown 表格）。"""

    check_id: str
    severity: str
    title: str
    detail: str
    evidence: Dict[str, Any] = field(default_factory=dict)
    remediation: str = ""
    scope: str = "ledger"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "check_id": self.check_id,
            "severity": self.severity,
            "title": self.title,
            "detail": self.detail,
            "evidence": self.evidence,
            "remediation": self.remediation,
            "scope": self.scope,
        }


@dataclass
class LedgerAuditReport:
    """稽核報告（含計數、後端資訊、發現清單與整體判定）。"""

    generated_at: float = field(default_factory=time.time)
    backend: str = "unknown"
    database_path: Optional[str] = None
    database_url_configured: bool = False
    counts: Dict[str, Any] = field(default_factory=dict)
    findings: List[AuditFinding] = field(default_factory=list)
    strategies: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    accounting_health: Dict[str, Any] = field(default_factory=dict)

    @property
    def worst_severity(self) -> Optional[str]:
        if not self.findings:
            return None
        return sorted(self.findings, key=lambda f: SEVERITY_ORDER.get(f.severity, 9))[0].severity

    @property
    def healthy(self) -> bool:
        return not any(f.severity in ("critical", "high") for f in self.findings)

    def summary(self) -> Dict[str, Any]:
        by_severity: Dict[str, int] = {}
        by_check: Dict[str, int] = {}
        for f in self.findings:
            by_severity[f.severity] = by_severity.get(f.severity, 0) + 1
            by_check[f.check_id] = by_check.get(f.check_id, 0) + 1
        return {
            "healthy": self.healthy,
            "worst_severity": self.worst_severity,
            "total_findings": len(self.findings),
            "by_severity": by_severity,
            "by_check": by_check,
            "backend": self.backend,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "generated_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.generated_at)),
            "backend": self.backend,
            "database_path": self.database_path,
            "database_url_configured": self.database_url_configured,
            "healthy": self.healthy,
            "worst_severity": self.worst_severity,
            "counts": self.counts,
            "strategies": self.strategies,
            "accounting_health": self.accounting_health,
            "summary": self.summary(),
            "findings": [f.to_dict() for f in sorted(self.findings, key=lambda x: SEVERITY_ORDER.get(x.severity, 9))],
        }

    def to_markdown(self) -> str:
        data = self.to_dict()
        lines: List[str] = []
        lines.append("# 統一記帳一致性稽核報告 (Ledger Consistency Audit)")
        lines.append("")
        lines.append(f"- 產生時間：{data['generated_at_iso']}")
        lines.append(f"- 帳本後端：`{data['backend']}`（DATABASE_URL 已設定：{data['database_url_configured']}）")
        lines.append(f"- 資料庫路徑：`{data['database_path']}`")
        summary = data["summary"]
        lines.append(
            f"- 整體判定：**{'✅ HEALTHY' if summary['healthy'] else '❌ UNHEALTHY'}**"
            f"（最嚴重等級 {summary['worst_severity'] or 'none'}，共 {summary['total_findings']} 項發現）"
        )
        lines.append("")
        lines.append("## 記帳計數")
        lines.append("")
        lines.append("| 指標 | 數量 |")
        lines.append("| :-- | --: |")
        for key, value in sorted(data["counts"].items()):
            lines.append(f"| `{key}` | {value} |")
        lines.append("")
        lines.append("## 各策略記帳覆蓋")
        lines.append("")
        lines.append("| 策略 | bots | fills | fill 事件 | 部位承載 | 快照 | 判定 |")
        lines.append("| :-- | --: | --: | --: | :-- | --: | :-- |")
        for name, info in sorted(data["strategies"].items()):
            lines.append(
                f"| `{name}` | {info.get('bots', 0)} | {info.get('fills', 0)} | {info.get('fill_events', 0)} "
                f"| {info.get('position_store') or '—'} | {info.get('snapshots', 0)} "
                f"| {'✅' if info.get('contract_ok') else '❌'} |"
            )
        lines.append("")
        lines.append("## 發現清單")
        lines.append("")
        if not data["findings"]:
            lines.append("（無發現）")
        else:
            lines.append("| 編號 | 嚴重度 | 標題 | 細節 | 修復建議 |")
            lines.append("| :-- | :-- | :-- | :-- | :-- |")
            for f in data["findings"]:
                detail = str(f["detail"]).replace("|", "\\|").replace("\n", " ")
                remediation = str(f["remediation"]).replace("|", "\\|").replace("\n", " ")
                title = str(f["title"]).replace("|", "\\|")
                lines.append(f"| {f['check_id']} | {f['severity'].upper()} | {title} | {detail} | {remediation} |")
        lines.append("")
        return "\n".join(lines)


class LedgerConsistencyAuditor:
    """以帳本不變式驗證三策略記帳統一度的稽核器（唯讀，可安全在生產執行）。"""

    def __init__(self, ledger: Optional[Any] = None, project_root: Optional[Any] = None):
        if ledger is None:
            from core.infrastructure.database.strategy_ledger import StrategyLedger

            ledger = StrategyLedger.instance()
        self._ledger = ledger
        self._project_root = Path(project_root) if project_root else Path(__file__).resolve().parents[3]

    # ── 主流程 ────────────────────────────────────────────────────────────
    def run(self, include_policy_scan: bool = True, data_dir: Optional[Any] = None) -> LedgerAuditReport:
        report = LedgerAuditReport()
        report.backend = getattr(self._ledger, "backend", "unknown")
        report.database_path = getattr(self._ledger, "database_path", None)
        report.database_url_configured = bool(os.getenv("DATABASE_URL"))
        report.accounting_health = self._ledger.health() if hasattr(self._ledger, "health") else {}

        store = getattr(self._ledger, "store", None)
        bots = self._ledger.list_bots()
        bot_ids = {b.bot_id for b in bots}

        fills = self._collect_fills(store, bot_ids)
        events = self._collect_events(store, bot_ids)
        baskets = self._collect_rows(store, "bot_baskets")
        regime = self._collect_rows(store, "bot_regime_decisions")
        snapshots = self._collect_rows(store, "bot_snapshots")

        report.counts = {
            "bots": len(bots),
            "trade_fills": len(fills),
            "bot_events": len(events),
            "bot_baskets": len(baskets),
            "bot_regime_decisions": len(regime),
            "bot_snapshots": len(snapshots),
            "bot_positions": len(self._collect_rows(store, "bot_positions")),
        }
        report.strategies = self._strategy_coverage(bots, fills, events, baskets, snapshots)

        self._check_schema(store, report)
        self._check_backend(report)
        self._check_orphans(fills, events, bot_ids, report)
        self._check_fill_event_gap(bots, fills, events, report)
        self._check_event_idempotency(events, report)
        self._check_duplicate_fills(fills, report)
        self._check_mode_mismatch(bots, report)
        self._check_contracts(bots, fills, baskets, snapshots, report)
        self._check_phantom_bots(bots, fills, events, snapshots, report)
        self._check_pnl_identity(snapshots, report)
        self._check_accounting_health(report)
        self._check_side_files(report, data_dir=data_dir, baskets=baskets, regime=regime, bots=bots)
        self._check_audit_log(report)
        if include_policy_scan:
            self._check_policy_bypass(report)
        return report

    # ── 資料收集（唯讀，容錯） ────────────────────────────────────────────
    def _collect_fills(self, store: Any, bot_ids: Iterable[str]) -> List[Any]:
        out: List[Any] = []
        try:
            from sqlalchemy import select

            from core.infrastructure.database.ledger_store import trade_fills

            with store._ro() as conn:  # noqa: SLF001 - 稽核器為唯讀內部工具
                rows = conn.execute(select(trade_fills)).all()
            out.extend(rows)
        except Exception as exc:
            logger.warning(f"[LedgerAudit] 讀取 trade_fills 失敗: {exc}")
        return out

    def _collect_events(self, store: Any, bot_ids: Iterable[str]) -> List[Any]:
        try:
            from sqlalchemy import select

            from core.infrastructure.database.ledger_store import bot_events

            with store._ro() as conn:  # noqa: SLF001
                return list(conn.execute(select(bot_events)).all())
        except Exception as exc:
            logger.warning(f"[LedgerAudit] 讀取 bot_events 失敗: {exc}")
            return []

    def _collect_rows(self, store: Any, table_name: str) -> List[Any]:
        try:
            from sqlalchemy import select, text

            from core.infrastructure.database import ledger_store as ls

            table = getattr(ls, table_name, None)
            if table is None:
                return []
            with store._ro() as conn:  # noqa: SLF001
                return list(conn.execute(select(table)).all())
        except Exception as exc:
            logger.debug(f"[LedgerAudit] 讀取 {table_name} 失敗: {exc}")
            return []

    def _strategy_coverage(
        self,
        bots: Sequence[Any],
        fills: Sequence[Any],
        events: Sequence[Any],
        baskets: Sequence[Any],
        snapshots: Sequence[Any],
    ) -> Dict[str, Dict[str, Any]]:
        coverage: Dict[str, Dict[str, Any]] = {}
        for bot in bots:
            strategy = (getattr(bot, "strategy_type", "") or "unknown").lower()
            info = coverage.setdefault(
                strategy,
                {
                    "bots": 0,
                    "fills": 0,
                    "fill_events": 0,
                    "snapshots": 0,
                    "basket_rows": 0,
                    "position_store": contract_for(strategy).get("position_store")
                    or contract_for(strategy).get("basket_store"),
                }
            )
            info["bots"] += 1
        bot_strategy = {getattr(b, "bot_id", ""): (getattr(b, "strategy_type", "") or "").lower() for b in bots}
        for f in fills:
            strategy = bot_strategy.get(getattr(f, "bot_id", ""), "unknown")
            coverage.setdefault(strategy, {"bots": 0, "fills": 0, "fill_events": 0, "snapshots": 0, "basket_rows": 0})
            coverage[strategy]["fills"] += 1
        for e in events:
            if getattr(e, "event_type", "") != "fill":
                continue
            strategy = bot_strategy.get(getattr(e, "bot_id", ""), "unknown")
            coverage.setdefault(strategy, {"bots": 0, "fills": 0, "fill_events": 0, "snapshots": 0, "basket_rows": 0})
            coverage[strategy]["fill_events"] += 1
        for s in snapshots:
            payload = self._payload_dict(getattr(s, "payload", None))
            strategy = (payload.get("strategy_type") or bot_strategy.get(getattr(s, "bot_id", ""), "") or "unknown").lower()
            coverage.setdefault(strategy, {"bots": 0, "fills": 0, "fill_events": 0, "snapshots": 0, "basket_rows": 0})
            coverage[strategy]["snapshots"] += 1
        for b in baskets:
            strategy = bot_strategy.get(getattr(b, "bot_id", ""), "coinfull_prime")
            coverage.setdefault(strategy, {"bots": 0, "fills": 0, "fill_events": 0, "snapshots": 0, "basket_rows": 0})
            coverage[strategy]["basket_rows"] += 1
        for strategy, info in coverage.items():
            contract = contract_for(strategy)
            position_ok = True
            if contract.get("basket_store"):
                position_ok = info["basket_rows"] > 0 or info["fills"] == 0
            elif contract.get("position_store"):
                position_ok = info["fills"] == 0 or info["snapshots"] > 0
            info["contract_ok"] = bool(position_ok)
            if contract.get("requires_fill_events") and info["fills"] > 0:
                info["contract_ok"] = info["contract_ok"] and info["fill_events"] > 0
        return coverage

    @staticmethod
    def _payload_dict(raw: Any) -> Dict[str, Any]:
        if isinstance(raw, dict):
            return raw
        try:
            data = json.loads(raw) if raw else {}
            return data if isinstance(data, dict) else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}

    # ── 各項檢查 ──────────────────────────────────────────────────────────
    def _add(self, report: LedgerAuditReport, finding: AuditFinding) -> None:
        report.findings.append(finding)

    def _check_schema(self, store: Any, report: LedgerAuditReport) -> None:
        missing: List[str] = []
        try:
            from sqlalchemy import inspect

            existing = set(inspect(store._engine).get_table_names())  # noqa: SLF001
            missing = [t for t in CANONICAL_TABLES if t not in existing]
        except Exception as exc:
            self._add(
                report,
                AuditFinding(
                    "L15", "high", "SCHEMA_DRIFT", f"無法檢視 schema：{exc}",
                    remediation="確認 SQLAlchemy 已安裝且 ledger store 可連線。",
                ),
            )
            return
        if missing:
            self._add(
                report,
                AuditFinding(
                    "L15",
                    "critical",
                    "SCHEMA_DRIFT",
                    f"canonical ledger 缺少 {len(missing)} 張表：{', '.join(missing)}",
                    evidence={"missing_tables": missing},
                    remediation="執行 LedgerStore.initialize()（冪等 create_all）或全新 DB 跑 alembic upgrade head（0003_unified_accounting）。",
                ),
            )

    def _check_backend(self, report: LedgerAuditReport) -> None:
        url = os.getenv("DATABASE_URL") or ""
        if url.strip().lower().startswith(("postgres://", "postgresql://", "postgresql+")) and report.backend != "postgresql":
            self._add(
                report,
                AuditFinding(
                    "L3",
                    "critical",
                    "GHOST_BACKEND",
                    "DATABASE_URL 指向 PostgreSQL，但實際記帳後端為 "
                    f"{report.backend}：寫入會落到本機 SQLite（幽靈資料源，稽核 F3 類回歸）。",
                    evidence={"database_url_prefix": url.split("://")[0], "backend": report.backend},
                    remediation="所有記帳一律經 StrategyLedger → LedgerRepository → create_ledger_store()；"
                                "並檢查 PostgreSQL 連線失敗降級日誌。",
                ),
            )

    def _check_orphans(
        self,
        fills: Sequence[Any],
        events: Sequence[Any],
        bot_ids: Iterable[str],
        report: LedgerAuditReport,
    ) -> None:
        known = set(bot_ids)
        orphan_fills = sorted({getattr(f, "bot_id", "") for f in fills if getattr(f, "bot_id", "") not in known})
        if orphan_fills:
            self._add(
                report,
                AuditFinding(
                    "L1", "high", "ORPHAN_FILLS",
                    f"{len(orphan_fills)} 個 bot_id 有成交但未註冊於 bots 表（破壞 P1-6 帳本完整性）",
                    evidence={"bot_ids": orphan_fills[:20]},
                    remediation="寫入 fills 前先經 StrategyLedger.ensure_bot() 登記身分。",
                ),
            )
        orphan_events = sorted({getattr(e, "bot_id", "") for e in events if getattr(e, "bot_id", "") not in known})
        if orphan_events:
            self._add(
                report,
                AuditFinding(
                    "L2", "high", "ORPHAN_EVENTS",
                    f"{len(orphan_events)} 個 bot_id 有事件但未註冊於 bots 表",
                    evidence={"bot_ids": orphan_events[:20]},
                    remediation="事件寫入前先經 StrategyLedger.ensure_bot() 登記身分。",
                ),
            )

    def _check_fill_event_gap(
        self,
        bots: Sequence[Any],
        fills: Sequence[Any],
        events: Sequence[Any],
        report: LedgerAuditReport,
    ) -> None:
        fill_counts: Dict[str, int] = {}
        for f in fills:
            fill_counts[getattr(f, "bot_id", "")] = fill_counts.get(getattr(f, "bot_id", ""), 0) + 1
        event_bots = {getattr(e, "bot_id", "") for e in events if getattr(e, "event_type", "") == "fill"}
        gap = sorted(b for b, n in fill_counts.items() if n > 0 and b not in event_bots)
        if gap:
            self._add(
                report,
                AuditFinding(
                    "L5", "medium", "FILL_EVENT_GAP",
                    f"{len(gap)} 個 bot 有 trade_fills 但無 canonical fill 事件，事件溯源回放退回 legacy 路徑",
                    evidence={"bot_ids": gap[:20], "fills_without_events": sum(fill_counts[b] for b in gap)},
                    remediation="改用 StrategyLedger.record_fill()（會同步 emit_fill_event）。",
                ),
            )

    def _check_event_idempotency(self, events: Sequence[Any], report: LedgerAuditReport) -> None:
        missing: Dict[str, int] = {}
        for e in events:
            etype = getattr(e, "event_type", "")
            if etype in IDEMPOTENT_EVENT_TYPES and not getattr(e, "dedupe_key", None):
                missing[etype] = missing.get(etype, 0) + 1
        if missing:
            self._add(
                report,
                AuditFinding(
                    "L6", "medium", "NON_IDEMPOTENT_EVENTS",
                    f"可重跑事件未帶 dedupe_key：{missing}（重啟/補跑會產生重複記帳）",
                    evidence=missing,
                    remediation="換倉/閘門事件一律帶 dedupe_key（如 rebalance:{account}:{batch_ts}）。",
                ),
            )

    def _check_duplicate_fills(self, fills: Sequence[Any], report: LedgerAuditReport) -> None:
        seen: Dict[Tuple[str, str], List[str]] = {}
        for f in fills:
            key = (getattr(f, "bot_id", ""), getattr(f, "client_order_id", "") or "")
            if not key[1]:
                continue
            seen.setdefault(key, []).append(getattr(f, "fill_id", ""))
        dups = {k: v for k, v in seen.items() if len(set(v)) > 1}
        if dups:
            sample = [{"bot_id": k[0], "client_order_id": k[1], "fill_ids": v[:5]} for k, v in list(dups.items())[:10]]
            self._add(
                report,
                AuditFinding(
                    "L13", "medium", "DUPLICATE_FILLS",
                    f"{len(dups)} 組 client_order_id 對應多筆 fill_id（疑似重複入帳）",
                    evidence={"sample": sample},
                    remediation="fill_id 需由交易所 order id / 穩定業務鍵推導，不可使用毫秒時間戳。",
                ),
            )

    def _check_mode_mismatch(self, bots: Sequence[Any], report: LedgerAuditReport) -> None:
        bad = [
            getattr(b, "bot_id", "")
            for b in bots
            if "testnet" in (getattr(b, "exchange", "") or "").lower()
            and (getattr(b, "account_mode", "") or "") == "live"
        ]
        if bad:
            self._add(
                report,
                AuditFinding(
                    "L14", "medium", "MODE_MISMATCH",
                    f"{len(bad)} 個測試網機器人被標記為 live（實盤資金語意錯置）",
                    evidence={"bot_ids": bad[:20]},
                    remediation="account_mode 僅 live/paper；測試網歸 paper，環境細節放 exchange 欄位。",
                ),
            )

    def _check_contracts(
        self,
        bots: Sequence[Any],
        fills: Sequence[Any],
        baskets: Sequence[Any],
        snapshots: Sequence[Any],
        report: LedgerAuditReport,
    ) -> None:
        fill_bots = {getattr(f, "bot_id", "") for f in fills}
        basket_bots = {getattr(b, "bot_id", "") for b in baskets}
        snapshot_bots = {getattr(s, "bot_id", "") for s in snapshots}
        violations: List[Dict[str, Any]] = []
        for bot in bots:
            bot_id = getattr(bot, "bot_id", "")
            strategy = (getattr(bot, "strategy_type", "") or "").lower()
            contract = contract_for(strategy)
            if bot_id not in fill_bots:
                continue
            if contract.get("basket_store") and bot_id not in basket_bots:
                violations.append({"bot_id": bot_id, "strategy": strategy, "missing": contract["basket_store"]})
            elif contract.get("requires_snapshot") and bot_id not in snapshot_bots:
                violations.append({"bot_id": bot_id, "strategy": strategy, "missing": "bot_snapshots"})
        if violations:
            self._add(
                report,
                AuditFinding(
                    "L8", "medium", "CONTRACT_VIOLATION",
                    f"{len(violations)} 個有成交的機器人未滿足記帳契約（缺部位/籃子/快照承載）",
                    evidence={"violations": violations[:20], "contract": STRATEGY_ACCOUNTING_CONTRACT},
                    remediation="依 STRATEGY_ACCOUNTING_CONTRACT 補齊承載表寫入（籃子型策略必寫 bot_baskets）。",
                ),
            )

    def _check_phantom_bots(
        self,
        bots: Sequence[Any],
        fills: Sequence[Any],
        events: Sequence[Any],
        snapshots: Sequence[Any],
        report: LedgerAuditReport,
    ) -> None:
        active_bots = (
            {getattr(f, "bot_id", "") for f in fills}
            | {getattr(s, "bot_id", "") for s in snapshots}
            | {
                getattr(e, "bot_id", "")
                for e in events
                if getattr(e, "event_type", "") not in NON_ACCOUNTING_EVENT_TYPES
            }
        )
        phantoms = [
            {
                "bot_id": getattr(b, "bot_id", ""),
                "strategy_type": getattr(b, "strategy_type", ""),
                "status": getattr(b, "status", ""),
            }
            for b in bots
            if getattr(b, "bot_id", "") not in active_bots
            and (getattr(b, "status", "") or "").lower() in ("running", "idle")
        ]
        if phantoms:
            self._add(
                report,
                AuditFinding(
                    "L9", "low", "PHANTOM_BOTS",
                    f"{len(phantoms)} 個機器人身分從無記帳活動卻標示 running/idle（污染儀表板與避碰名單）",
                    evidence={"bots": phantoms[:20]},
                    remediation="只在實際使用該帳號環境時登記身分；未使用者標記 registered/archived。",
                ),
            )

    def _check_pnl_identity(self, snapshots: Sequence[Any], report: LedgerAuditReport) -> None:
        bad: List[Dict[str, Any]] = []
        for row in snapshots:
            payload = self._payload_dict(getattr(row, "payload", None))
            profit = payload.get("profit") if isinstance(payload.get("profit"), dict) else {}
            net = payload.get("net_pnl")
            if net is None:
                continue
            grid_profit = profit.get("grid_profit", payload.get("grid_profit", 0.0)) or 0.0
            unrealized = payload.get("unrealized_pnl", profit.get("unrealized", 0.0)) or 0.0
            fees = profit.get("fees", payload.get("total_fees", 0.0)) or 0.0
            try:
                expected = float(grid_profit) + float(unrealized) - float(fees)
                if abs(float(net) - expected) > max(1e-6, abs(expected) * 1e-6):
                    bad.append(
                        {
                            "bot_id": getattr(row, "bot_id", ""),
                            "net_pnl": float(net),
                            "expected": round(expected, 8),
                        }
                    )
            except (TypeError, ValueError):
                continue
        if bad:
            self._add(
                report,
                AuditFinding(
                    "L11", "high", "PNL_IDENTITY",
                    f"{len(bad)} 份快照違反會計恆等式 net_pnl = grid_profit + unrealized_pnl - total_fees",
                    evidence={"sample": bad[:10]},
                    remediation="快照產生端需以恆等式計算 net_pnl（見 0.3.1 修正），不可直接覆蓋為 0。",
                ),
            )

    def _check_accounting_health(self, report: LedgerAuditReport) -> None:
        health = report.accounting_health or {}
        if health.get("writes_failed"):
            self._add(
                report,
                AuditFinding(
                    "L12", "high", "ACCOUNTING_HEALTH",
                    f"統一記帳層累計 {health['writes_failed']} 次寫入失敗（最後錯誤 op={health.get('last_error_op')}）",
                    evidence={k: health.get(k) for k in ("writes_ok", "writes_failed", "last_error", "last_error_at", "errors_by_op")},
                    remediation="檢查 DB 鎖/磁碟/連線；記帳失敗不得靜默，需上告警。",
                ),
            )

    def _check_side_files(
        self,
        report: LedgerAuditReport,
        data_dir: Optional[Any] = None,
        baskets: Sequence[Any] = (),
        regime: Sequence[Any] = (),
        bots: Sequence[Any] = (),
    ) -> None:
        directory = Path(data_dir) if data_dir else (self._project_root / "data")
        basket_file = directory / "coinfull_prime_active_basket.json"
        if not basket_file.exists():
            return
        try:
            with open(basket_file, "r", encoding="utf-8") as handle:
                data = json.load(handle) or {}
        except Exception as exc:
            self._add(
                report,
                AuditFinding(
                    "L7", "high", "SIDE_FILE_DIVERGENCE",
                    f"無法讀取檔案型籃子帳本：{exc}",
                    evidence={"file": str(basket_file)},
                    remediation="修復檔案或執行 tools/ledger_consistency_audit.py --backfill 後改由 DB 匯出鏡像。",
                ),
            )
            return
        db_symbols: Dict[str, set] = {}
        for row in baskets:
            if getattr(row, "status", "active") != "active":
                continue
            db_symbols.setdefault(getattr(row, "account_id", ""), set()).add(getattr(row, "symbol", ""))
        diverged: List[Dict[str, Any]] = []
        for account_id, payload in (data or {}).items():
            file_symbols = set((payload or {}).get("managed_symbols") or [])
            db_set = db_symbols.get(str(account_id), set())
            if file_symbols and file_symbols != db_set:
                diverged.append(
                    {
                        "account_id": account_id,
                        "only_in_file": sorted(file_symbols - db_set)[:20],
                        "only_in_db": sorted(db_set - file_symbols)[:20],
                    }
                )
        regime_file = directory / "coinfull_prime_regime_history.jsonl"
        regime_lines = 0
        if regime_file.exists():
            try:
                with open(regime_file, "r", encoding="utf-8") as handle:
                    regime_lines = sum(1 for line in handle if line.strip())
            except Exception:
                regime_lines = -1
        if diverged or (regime_lines > len(regime) > 0) or (regime_lines > 0 and len(regime) == 0):
            self._add(
                report,
                AuditFinding(
                    "L7", "high", "SIDE_FILE_DIVERGENCE",
                    "檔案型帳本與 canonical ledger 分歧（記帳孤島殘留，重啟後可能以舊檔覆寫新帳）",
                    evidence={
                        "basket_divergence": diverged[:10],
                        "regime_jsonl_lines": regime_lines,
                        "regime_db_rows": len(regime),
                        "file": str(basket_file),
                    },
                    remediation="執行 tools/ledger_consistency_audit.py --backfill 回填，之後檔案僅由 export_ledger_to_side_files() 產生。",
                ),
            )

    def _check_audit_log(self, report: LedgerAuditReport) -> None:
        """操作稽核日誌（data/grid_audit.db）自身健康度 —— 「稽核功能」是否可用。"""
        db_path = self._project_root / "data" / "grid_audit.db"
        try:
            from core.infrastructure.database.guard import EnvironmentGuard

            safe_path = EnvironmentGuard.get_safe_database_path(str(db_path))
        except Exception:
            safe_path = str(db_path)
        if not os.path.exists(safe_path):
            # 全新安裝本來就沒有 grid_audit.db（尚無任何操作）。先觸發 AuditLogger
            # 自我修復探測：能建起來就不是缺陷，建不起來才是真的 L10。
            healed: Dict[str, Any] = {}
            try:
                from web_ui.backend.audit import AuditLogger

                healed = AuditLogger.get_instance(str(self._project_root)).health() or {}
            except Exception as exc:  # 探測失敗 → 保留原始 medium 判定
                healed = {"probe_error": f"{type(exc).__name__}: {exc}"}
            if os.path.exists(safe_path) and healed.get("schema_ok"):
                report.counts["audit_log_self_healed"] = True
                return
            self._add(
                report,
                AuditFinding(
                    "L10", "medium", "AUDIT_LOG_HEALTH",
                    "操作稽核資料庫不存在，且自我修復探測未能建立（所有操作稽核都會靜默失敗）",
                    evidence={"path": safe_path, "probe": healed},
                    remediation="檢查 data/ 目錄是否存在與可寫入（權限/磁碟）；必要時手動建立後重啟服務。",
                ),
            )
            return
        try:
            import sqlite3

            conn = sqlite3.connect(f"file:{os.path.abspath(safe_path)}?mode=ro", uri=True, timeout=5.0)
            try:
                tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
                if "audit_log" not in tables:
                    self._add(
                        report,
                        AuditFinding(
                            "L10", "high", "AUDIT_LOG_HEALTH",
                            "grid_audit.db 存在但缺 audit_log 表 → 所有操作稽核寫入都會靜默失敗",
                            evidence={"path": safe_path, "tables": sorted(tables)},
                            remediation="AuditLogger 需自我修復 schema（0.3.6 已加入 _ensure_schema 重試）。",
                        ),
                    )
                    return
                rows = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()
                report.counts["audit_log_rows"] = int(rows[0]) if rows else 0
            finally:
                conn.close()
        except Exception as exc:
            self._add(
                report,
                AuditFinding(
                    "L10", "high", "AUDIT_LOG_HEALTH",
                    f"無法驗證操作稽核日誌：{exc}",
                    evidence={"path": safe_path},
                    remediation="檢查檔案權限/損毀；必要時由備份還原 grid_audit.db。",
                ),
            )
            return
        try:
            from web_ui.backend.audit import AuditLogger

            failures = AuditLogger.total_write_failures()
            if failures:
                self._add(
                    report,
                    AuditFinding(
                        "L10", "high", "AUDIT_LOG_HEALTH",
                        f"操作稽核日誌本進程累計 {failures} 次寫入失敗",
                        evidence={"write_failures": failures, "path": safe_path},
                        remediation="檢查 AuditLogger 自我修復日誌與 data/ 權限。",
                    ),
                )
        except Exception:
            pass

    def _check_policy_bypass(self, report: LedgerAuditReport) -> None:
        """L4：策略層/API 層不得繞過統一記帳門面（靜態掃描，可被單元測試固化）。"""
        offenders: List[Dict[str, Any]] = []
        targets: List[Path] = []
        for root in POLICY_SCAN_ROOTS:
            base = self._project_root / root
            if base.exists():
                targets.extend(sorted(base.rglob("*.py")))
        for entry in POLICY_SCAN_ENTRYPOINTS:
            path = self._project_root / entry
            if path.exists():
                targets.append(path)
        for path in targets:
            rel = str(path.relative_to(self._project_root))
            if rel in POLICY_ALLOWLIST:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                hits = []
                if _RAW_STORE_PATTERN.search(line):
                    hits.append("SqliteLedgerStore()")
                if _RAW_SQLITE_PATTERN.search(line):
                    hits.append("sqlite3.connect()")
                if hits:
                    offenders.append(
                        {
                            "file": rel,
                            "line": lineno,
                            "pattern": ", ".join(hits),
                            "snippet": stripped[:160],
                        }
                    )
        if offenders:
            self._add(
                report,
                AuditFinding(
                    "L4", "high", "RAW_STORE_BYPASS",
                    f"{len(offenders)} 處策略/API 程式碼繞過 StrategyLedger 直接開啟帳本後端"
                    "（PostgreSQL 模式下會形成幽靈資料源，且繞過 EnvironmentGuard）",
                    evidence={"offenders": offenders[:20]},
                    remediation="改用 StrategyLedger.instance()；tests/test_accounting_path_policy.py 會擋下回歸。",
                ),
            )


def run_audit(
    ledger: Optional[Any] = None,
    project_root: Optional[Any] = None,
    include_policy_scan: bool = True,
    data_dir: Optional[Any] = None,
) -> LedgerAuditReport:
    """便捷入口：執行一次完整稽核並回傳報告物件。"""
    return LedgerConsistencyAuditor(ledger=ledger, project_root=project_root).run(
        include_policy_scan=include_policy_scan, data_dir=data_dir
    )


__all__ = [
    "CANONICAL_TABLES",
    "AuditFinding",
    "LedgerAuditReport",
    "LedgerConsistencyAuditor",
    "run_audit",
]
