"""P4-1 統一記帳一致性稽核器測試（core/infrastructure/database/ledger_audit.py）。

以「注入缺陷 → 稽核器必須抓出來」的方式驗證 L1–L15 檢查矩陣，
而不是只驗證乾淨帳本回傳 healthy（避免稽核器淪為永遠通過的橡皮圖章）。
"""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from core.infrastructure.database.ledger_audit import (
    CANONICAL_TABLES,
    LedgerConsistencyAuditor,
    run_audit,
)
from core.infrastructure.database.ledger_repository import LedgerRepository
from core.infrastructure.database.models import TradeFill
from core.infrastructure.database.strategy_ledger import StrategyLedger


class LedgerAuditTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data_dir = self.root / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.repo = LedgerRepository(str(self.root / "ledger.sqlite3"))
        self.ledger = StrategyLedger(repository=self.repo)
        StrategyLedger.reset_instance()
        LedgerRepository._instance = self.repo
        self.addCleanup(LedgerRepository.reset_instance)
        self.addCleanup(StrategyLedger.reset_instance)
        self.addCleanup(self.tmp.cleanup)

    def audit(self, **kwargs):
        kwargs.setdefault("project_root", self.root)
        kwargs.setdefault("data_dir", self.data_dir)
        return run_audit(ledger=self.ledger, **kwargs)

    def check_ids(self, report):
        return {f.check_id for f in report.findings}

    def _insert_raw_fill(self, fill_id: str, bot_id: str, client_order_id: str = "c1", price: float = 100.0):
        """繞過門面直接寫入（模擬歷史髒資料 / 舊版程式碼殘留）。"""
        from core.infrastructure.database.ledger_store import trade_fills

        with self.repo.store._engine.begin() as conn:  # noqa: SLF001 - 測試專用注入
            conn.execute(
                trade_fills.insert().values(
                    fill_id=fill_id,
                    bot_id=bot_id,
                    exchange_order_id=None,
                    client_order_id=client_order_id,
                    side="buy",
                    role="grid",
                    price=price,
                    quantity=1.0,
                    fee=0.0,
                    fee_currency="USDT",
                    realized_pnl=0.0,
                    executed_at=time.time(),
                )
            )

    def test_clean_ledger_is_healthy(self):
        self.ledger.ensure_bot("grid-1", "grid", "BTC/USDT", "binance", account_mode="paper")
        self.ledger.record_fill(TradeFill("f1", "grid-1", "c1", "buy", "grid", 100.0, 1.0, time.time()))
        self.ledger.save_snapshot("grid-1", "config/grid/a.yaml", {"cycles": 1}, force_history=True)
        report = self.audit()
        self.assertTrue(report.healthy, msg=json.dumps(report.to_dict()["findings"], ensure_ascii=False))
        self.assertNotIn("L1", self.check_ids(report))
        self.assertNotIn("L3", self.check_ids(report))
        self.assertNotIn("L15", self.check_ids(report))
        self.assertGreaterEqual(report.counts["trade_fills"], 1)

    def test_detects_orphan_fills_and_events(self):
        self._insert_raw_fill("orphan-1", "ghost-bot")
        self.repo.append_event("ghost-bot", "state_transition", {"x": 1})
        report = self.audit()
        self.assertIn("L1", self.check_ids(report))
        self.assertIn("L2", self.check_ids(report))
        self.assertFalse(report.healthy)
        finding = next(f for f in report.findings if f.check_id == "L1")
        self.assertEqual(finding.severity, "high")
        self.assertIn("ghost-bot", finding.evidence["bot_ids"])

    def test_detects_duplicate_client_order_ids(self):
        self.ledger.ensure_bot("grid-1", "grid", "BTC/USDT", "binance", account_mode="paper")
        self._insert_raw_fill("a1", "grid-1", client_order_id="same-cid", price=100.0)
        self._insert_raw_fill("a2", "grid-1", client_order_id="same-cid", price=100.0)
        report = self.audit()
        self.assertIn("L13", self.check_ids(report))

    def test_detects_non_idempotent_rebalance_events(self):
        self.ledger.ensure_bot("coinfull_prime:paper", "coinfull_prime", "BASKET_16", "paper", account_mode="paper")
        self.ledger.append_event("coinfull_prime:paper", "daily_rebalance", {"orders": []})  # 無 dedupe_key
        report = self.audit()
        self.assertIn("L6", self.check_ids(report))
        finding = next(f for f in report.findings if f.check_id == "L6")
        self.assertEqual(finding.evidence.get("daily_rebalance"), 1)

    def test_detects_fill_event_gap(self):
        self.ledger.ensure_bot("dca-1", "dca", "ETH/USDT", "binance", account_mode="paper")
        self._insert_raw_fill("raw-1", "dca-1")  # 有流水、無 canonical fill 事件
        report = self.audit()
        self.assertIn("L5", self.check_ids(report))

    def test_detects_contract_violation_for_basket_strategy(self):
        self.ledger.ensure_bot("coinfull_prime:paper", "coinfull_prime", "BASKET_16", "paper", account_mode="paper")
        self.ledger.record_fill(
            TradeFill("cf1", "coinfull_prime:paper", "c1", "buy", "rebalance_entry", 100.0, 1.0, time.time())
        )
        report = self.audit()  # 無 bot_baskets 成分 → 違反籃子策略記帳契約
        self.assertIn("L8", self.check_ids(report))

    def test_detects_phantom_bots(self):
        self.ledger.ensure_bot("coinfull_prime:binance_live", "coinfull_prime", "BASKET_16", "binance_live",
                               account_mode="live", status="running")
        report = self.audit()
        self.assertIn("L9", self.check_ids(report))

    def test_detects_pnl_identity_violation(self):
        self.ledger.ensure_bot("grid-1", "grid", "BTC/USDT", "binance", account_mode="paper")
        self.ledger.save_snapshot(
            "grid-1",
            "config/grid/a.yaml",
            {"net_pnl": 999.0, "grid_profit": 10.0, "unrealized_pnl": 5.0, "profit": {"fees": 1.0}},
            force_history=True,
        )
        report = self.audit()
        self.assertIn("L11", self.check_ids(report))
        finding = next(f for f in report.findings if f.check_id == "L11")
        self.assertAlmostEqual(finding.evidence["sample"][0]["expected"], 14.0)

    def test_detects_ghost_backend_when_postgres_configured(self):
        previous = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = "postgresql://grid:grid@localhost:5432/grid"
        try:
            report = self.audit()
            self.assertIn("L3", self.check_ids(report))
            finding = next(f for f in report.findings if f.check_id == "L3")
            self.assertEqual(finding.severity, "critical")
            self.assertFalse(report.healthy)
        finally:
            if previous is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = previous

    def test_detects_accounting_health_degradation(self):
        self.ledger._health.record_failure("record_fill", RuntimeError("disk full"))
        report = self.audit()
        self.assertIn("L12", self.check_ids(report))

    def test_detects_side_file_divergence(self):
        self.ledger.ensure_bot("coinfull_prime:paper", "coinfull_prime", "BASKET_16", "paper", account_mode="paper")
        from core.infrastructure.database.models import BasketEntry

        self.ledger.save_basket(
            "coinfull_prime:paper", "paper", [BasketEntry("coinfull_prime:paper", "paper", "BTCUSDT", "long", 1.0, 100.0)]
        )
        (self.data_dir / "coinfull_prime_active_basket.json").write_text(
            json.dumps({"paper": {"managed_symbols": ["BTCUSDT", "STALEUSDT"], "count": 2}}), encoding="utf-8"
        )
        report = self.audit()
        self.assertIn("L7", self.check_ids(report))
        finding = next(f for f in report.findings if f.check_id == "L7")
        self.assertIn("STALEUSDT", finding.evidence["basket_divergence"][0]["only_in_file"])

    def test_detects_policy_bypass_in_scanned_tree(self):
        fake_root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(fake_root, ignore_errors=True))
        (fake_root / "core" / "services" / "strategy_x").mkdir(parents=True)
        (fake_root / "core" / "services" / "strategy_x" / "bad.py").write_text(
            "from core.infrastructure.database.ledger_store import SqliteLedgerStore\n"
            "store = SqliteLedgerStore()\n",
            encoding="utf-8",
        )
        auditor = LedgerConsistencyAuditor(ledger=self.ledger, project_root=fake_root)
        report = auditor.run(include_policy_scan=True, data_dir=self.data_dir)
        self.assertIn("L4", {f.check_id for f in report.findings})
        finding = next(f for f in report.findings if f.check_id == "L4")
        self.assertEqual(finding.evidence["offenders"][0]["pattern"], "SqliteLedgerStore()")

    def test_repository_has_no_policy_bypass(self):
        """真實 repo：策略層/API 層已無繞過統一門面的記帳路徑。"""
        project_root = Path(__file__).resolve().parents[1]
        auditor = LedgerConsistencyAuditor(ledger=self.ledger, project_root=project_root)
        report = auditor.run(include_policy_scan=True, data_dir=self.data_dir)
        offenders = [f for f in report.findings if f.check_id == "L4"]
        self.assertEqual(offenders, [], msg=json.dumps([f.to_dict() for f in offenders], ensure_ascii=False))

    def test_audit_log_missing_db_self_heals_without_false_positive(self):
        """全新安裝本來就沒有 grid_audit.db：稽核應觸發自我修復，而不是報 L10 誤判。"""
        audit_db = Path(self.root) / "data" / "grid_audit.db"
        if audit_db.exists():
            audit_db.unlink()
        report = self.audit()
        self.assertNotIn("L10", self.check_ids(report), msg=json.dumps(
            [f.to_dict() for f in report.findings if f.check_id == "L10"], ensure_ascii=False))
        self.assertTrue(report.counts.get("audit_log_self_healed"), "應記錄自我修復已發生")
        self.assertTrue(audit_db.exists(), "自我修復後 grid_audit.db 應已建立")

    def test_audit_log_health_reports_uncreatable_db(self):
        """自我修復也建不起來時，L10 必須報出（含探測證據）。"""
        from unittest import mock

        audit_db = Path(self.root) / "data" / "grid_audit.db"
        if audit_db.exists():
            audit_db.unlink()
        with mock.patch(
            "web_ui.backend.audit.AuditLogger.get_instance",
            side_effect=RuntimeError("read-only filesystem"),
        ):
            report = self.audit()
        findings = [f for f in report.findings if f.check_id == "L10"]
        self.assertEqual(len(findings), 1, msg=str(self.check_ids(report)))
        self.assertEqual(findings[0].severity, "medium")
        probe = findings[0].evidence.get("probe") or {}
        self.assertIn("probe_error", probe)
        self.assertIn("read-only filesystem", str(probe["probe_error"]))

    def test_report_renderers(self):
        self.ledger.ensure_bot("grid-1", "grid", "BTC/USDT", "binance", account_mode="paper")
        report = self.audit()
        data = report.to_dict()
        self.assertEqual(data["backend"], "sqlite")
        self.assertIn("summary", data)
        self.assertIn("strategies", data)
        markdown = report.to_markdown()
        self.assertIn("統一記帳一致性稽核報告", markdown)
        self.assertIn("| 策略 |", markdown)
        json.dumps(data, ensure_ascii=False, default=str)  # 必須可序列化（API 契約）

    def test_schema_declares_all_canonical_tables(self):
        from sqlalchemy import inspect

        existing = set(inspect(self.repo.store._engine).get_table_names())  # noqa: SLF001
        self.assertTrue(set(CANONICAL_TABLES).issubset(existing), msg=str(sorted(set(CANONICAL_TABLES) - existing)))


if __name__ == "__main__":
    unittest.main()
