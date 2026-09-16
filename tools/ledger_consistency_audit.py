#!/usr/bin/env python3
"""tools/ledger_consistency_audit.py

統一記帳一致性稽核 CLI（P4-1）。

驗證 grid / DCA / CoinFull Prime 三個策略是否真的共用同一套 **資料庫記帳**：
不是檢查程式碼有沒有呼叫 API，而是檢查帳本本身的不變式（L1–L15，
詳見 core/infrastructure/database/ledger_audit.py）。

用法::

    python3 tools/ledger_consistency_audit.py                  # 文字報告
    python3 tools/ledger_consistency_audit.py --json           # 機器可讀 JSON
    python3 tools/ledger_consistency_audit.py --markdown       # 可直接貼進稽核報告
    python3 tools/ledger_consistency_audit.py --backfill       # 先冪等回填檔案型帳本再稽核
    python3 tools/ledger_consistency_audit.py --export-mirror  # 由 DB 反向產生檔案鏡像
    python3 tools/ledger_consistency_audit.py --fail-on high   # CI 閘門（發現 high 以上即 exit 1）

退出碼：0 = 通過；1 = 發現達 ``--fail-on`` 等級（預設 critical）；2 = 執行錯誤。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def _print_text(report_dict: Dict[str, Any]) -> None:
    summary = report_dict["summary"]
    print("=" * 78)
    print("統一記帳一致性稽核 (Unified Ledger Consistency Audit) — P4-1")
    print("=" * 78)
    print(f"產生時間     : {report_dict['generated_at_iso']}")
    print(f"帳本後端     : {report_dict['backend']}  (DATABASE_URL 已設定: {report_dict['database_url_configured']})")
    print(f"資料庫路徑   : {report_dict['database_path']}")
    verdict = "✅ HEALTHY" if summary["healthy"] else "❌ UNHEALTHY"
    print(f"整體判定     : {verdict}  (最嚴重: {summary['worst_severity'] or 'none'}, 發現 {summary['total_findings']} 項)")
    print("-" * 78)
    print("記帳計數:")
    for key, value in sorted(report_dict["counts"].items()):
        print(f"  {key:<28} {value}")
    print("-" * 78)
    print("各策略記帳覆蓋:")
    header = f"  {'strategy':<18}{'bots':>6}{'fills':>8}{'fill_evt':>10}{'snapshots':>11}{'basket':>8}  contract"
    print(header)
    for name, info in sorted(report_dict["strategies"].items()):
        print(
            f"  {name:<18}{info.get('bots', 0):>6}{info.get('fills', 0):>8}"
            f"{info.get('fill_events', 0):>10}{info.get('snapshots', 0):>11}"
            f"{info.get('basket_rows', 0):>8}  {'✅' if info.get('contract_ok') else '❌'}"
        )
    print("-" * 78)
    health = report_dict.get("accounting_health") or {}
    print(
        "記帳層健康度 : "
        f"ok={health.get('writes_ok', 0)} failed={health.get('writes_failed', 0)} "
        f"degraded={health.get('degraded', False)} last_error={health.get('last_error')}"
    )
    findings: List[Dict[str, Any]] = report_dict["findings"]
    if not findings:
        print("發現清單     : （無）")
    else:
        print(f"發現清單 ({len(findings)}):")
        for f in findings:
            print(f"  [{f['severity'].upper():<8}] {f['check_id']} {f['title']}")
            print(f"             {f['detail']}")
            if f.get("remediation"):
                print(f"             → 修復: {f['remediation']}")
            evidence = f.get("evidence") or {}
            if evidence:
                snippet = json.dumps(evidence, ensure_ascii=False, default=str)
                print(f"             證據: {snippet[:600]}{'…' if len(snippet) > 600 else ''}")
    print("=" * 78)


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="統一記帳一致性稽核 (grid / dca / coinfull_prime)")
    parser.add_argument("--json", action="store_true", help="輸出 JSON")
    parser.add_argument("--markdown", action="store_true", help="輸出 Markdown 報告")
    parser.add_argument("--backfill", action="store_true", help="先冪等回填 CoinFull Prime 檔案型帳本")
    parser.add_argument("--export-mirror", action="store_true", help="由 DB 真相源反向產生檔案鏡像")
    parser.add_argument("--no-policy-scan", action="store_true", help="跳過程式碼路徑政策掃描 (L4)")
    parser.add_argument("--data-dir", default=os.path.join(PROJECT_ROOT, "data"), help="data/ 目錄")
    parser.add_argument(
        "--db",
        default="",
        help="直接稽核指定的 SQLite 帳本檔（預設沿用環境/工廠解析出的真相源）",
    )
    parser.add_argument(
        "--fail-on",
        default="critical",
        choices=["critical", "high", "medium", "low", "none"],
        help="達此嚴重度即以 exit 1 結束（CI 閘門）",
    )
    args = parser.parse_args(argv)

    try:
        from core.infrastructure.database.ledger_audit import run_audit
        from core.infrastructure.database.ledger_backfill import (
            backfill_coinfull_prime_side_files,
            export_ledger_to_side_files,
        )
        from core.infrastructure.database.strategy_ledger import StrategyLedger

        if args.db:
            # 稽核指定檔案（例如生產庫的唯讀副本），不影響環境解析出的預設真相源
            from core.infrastructure.database.ledger_store import SqliteLedgerStore

            ledger = StrategyLedger(repository=SqliteLedgerStore(database_path=args.db))
        else:
            ledger = StrategyLedger.instance()

        if args.backfill:
            stats = backfill_coinfull_prime_side_files(data_dir=args.data_dir, ledger=ledger)
            print("📥 檔案型帳本回填結果:")
            print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
            print("-" * 78)

        report = run_audit(
            ledger=ledger,
            project_root=PROJECT_ROOT,
            include_policy_scan=not args.no_policy_scan,
            data_dir=args.data_dir,
        )

        if args.export_mirror:
            exported = export_ledger_to_side_files(data_dir=args.data_dir, ledger=ledger)
            print("📤 檔案鏡像匯出結果:")
            print(json.dumps(exported, ensure_ascii=False, indent=2, default=str))
            print("-" * 78)

        if args.json:
            print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, default=str))
        elif args.markdown:
            print(report.to_markdown())
        else:
            _print_text(report.to_dict())

        if args.fail_on == "none":
            return 0
        threshold = SEVERITY_RANK[args.fail_on]
        worst = report.worst_severity
        if worst is not None and SEVERITY_RANK.get(worst, 9) <= threshold:
            return 1
        return 0
    except Exception as exc:  # CLI 必須給出明確失敗原因
        print(f"❌ 稽核執行失敗: {type(exc).__name__}: {exc}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
