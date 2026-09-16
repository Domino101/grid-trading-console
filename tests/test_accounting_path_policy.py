"""P4-1 記帳路徑政策護欄 (Harness Guardrails)。

這些測試不是驗證行為，而是讓「記帳孤島」在物理上無法再次出現：

1. 策略層 / API 層 / 引擎入口不得直接開啟帳本後端（SqliteLedgerStore / sqlite3.connect）。
2. 三個策略的寫入路徑都必須經過 StrategyLedger（統一記帳門面）。
3. schema 三處定義（SQLAlchemy meta / schema.sql / Alembic migration）必須同步，
   避免只有某一種部署方式拿得到新表。
4. CoinFull Prime 的檔案落地只能是「鏡像」——同一函式內必須先寫 canonical ledger。
"""

import re
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

POLICY_SCAN_ROOTS = ("core/services", "web_ui/backend")
POLICY_SCAN_ENTRYPOINTS = ("run_multiple_grids.py", "run_grid_trading.py", "run_coinfull_prime.py")
POLICY_ALLOWLIST = {"web_ui/backend/lifecycle.py"}  # connect_sqlite()：專案唯一低階連線器

RAW_STORE_PATTERN = re.compile(r"\bSqliteLedgerStore\s*\(")
RAW_SQLITE_PATTERN = re.compile(r"\bsqlite3\.connect\s*\(")

NEW_TABLES = ("bot_baskets", "bot_regime_decisions", "bot_runtime_state")


def _iter_policy_files():
    for root in POLICY_SCAN_ROOTS:
        base = PROJECT_ROOT / root
        if base.exists():
            for path in sorted(base.rglob("*.py")):
                rel = str(path.relative_to(PROJECT_ROOT))
                if rel in POLICY_ALLOWLIST:
                    continue
                yield rel, path
    for entry in POLICY_SCAN_ENTRYPOINTS:
        path = PROJECT_ROOT / entry
        if path.exists():
            yield entry, path


class TestAccountingPathPolicy(unittest.TestCase):
    def test_no_direct_ledger_backend_in_strategy_or_api_layer(self):
        offenders = []
        for rel, path in _iter_policy_files():
            text = path.read_text(encoding="utf-8", errors="ignore")
            for lineno, line in enumerate(text.splitlines(), start=1):
                if line.strip().startswith("#"):
                    continue
                if RAW_STORE_PATTERN.search(line) or RAW_SQLITE_PATTERN.search(line):
                    offenders.append(f"{rel}:{lineno}: {line.strip()[:120]}")
        self.assertEqual(
            offenders,
            [],
            msg="策略層/API 層繞過了統一記帳門面（PostgreSQL 模式下會形成幽靈資料源）：\n"
            + "\n".join(offenders),
        )

    def test_all_three_strategies_write_through_strategy_ledger(self):
        expectations = {
            "core/services/coinfull_prime/rebalancer.py": "StrategyLedger",
            "core/services/coinfull_prime/service.py": "StrategyLedger",
            "core/services/grid/implementations/position_tracker_impl.py": "StrategyLedger",
            "web_ui/backend/runtime_repository.py": "StrategyLedger",
        }
        for rel, symbol in expectations.items():
            text = (PROJECT_ROOT / rel).read_text(encoding="utf-8")
            self.assertIn(symbol, text, msg=f"{rel} 未使用統一記帳門面 {symbol}")

    def test_coinfull_prime_side_files_are_mirrors_only(self):
        """檔案落地必須伴隨 canonical ledger 寫入（DB 為真相源）。"""
        service = (PROJECT_ROOT / "core/services/coinfull_prime/service.py").read_text(encoding="utf-8")
        for method, ledger_call in (
            ("def save_active_basket", "ledger.save_basket"),
            ("def append_regime_history", "ledger.record_regime_decision"),
            ("def save_scheduler_state", "set_state"),
            ("def _persist_run_snapshot", "ledger.save_snapshot"),
        ):
            start = service.index(method)
            body = service[start : start + 4000]
            self.assertIn(ledger_call, body, msg=f"{method} 未先寫 canonical ledger（{ledger_call}）")

    def test_no_raw_relative_sqlite_path_in_coinfull_prime(self):
        """程式碼（非註解）中不得殘留硬編碼的相對 SQLite 路徑。"""
        service = (PROJECT_ROOT / "core/services/coinfull_prime/service.py").read_text(encoding="utf-8")
        offenders = [
            f"{lineno}: {line.strip()[:120]}"
            for lineno, line in enumerate(service.splitlines(), start=1)
            if "web_ui_runtime.sqlite3" in line and not line.strip().startswith("#")
        ]
        self.assertEqual(offenders, [], msg="\n".join(offenders))


class TestSchemaParity(unittest.TestCase):
    """新表必須同時存在於三處 schema 定義，否則某條部署路徑會缺表。"""

    def test_sqlalchemy_meta_declares_new_tables(self):
        from core.infrastructure.database import ledger_store

        for table in NEW_TABLES:
            self.assertIn(table, ledger_store.meta.tables, msg=f"SQLAlchemy meta 缺少 {table}")

    def test_schema_sql_declares_new_tables(self):
        text = (PROJECT_ROOT / "core/infrastructure/database/schema.sql").read_text(encoding="utf-8")
        for table in NEW_TABLES:
            self.assertRegex(text, rf"CREATE TABLE IF NOT EXISTS {table}\b", msg=f"schema.sql 缺少 {table}")

    def test_alembic_migration_declares_new_tables(self):
        migration = PROJECT_ROOT / "migrations/versions/0003_unified_strategy_accounting.py"
        self.assertTrue(migration.exists(), "缺少 0003_unified_strategy_accounting migration")
        text = migration.read_text(encoding="utf-8")
        for table in NEW_TABLES:
            self.assertIn(f'"{table}"', text, msg=f"migration 0003 缺少 {table}")
        self.assertIn('down_revision = "0002_bot_events"', text)

    def test_models_expose_new_dataclasses(self):
        from core.infrastructure.database import models

        for name in ("BasketEntry", "RegimeDecisionRecord", "RuntimeStateEntry"):
            self.assertTrue(hasattr(models, name), msg=f"models 缺少 {name}")

    def test_ledger_store_protocol_covers_new_operations(self):
        from core.infrastructure.database.ledger_store import LedgerStore

        annotations = getattr(LedgerStore, "__annotations__", {})  # Protocol 成員以方法存在
        for op in (
            "save_basket_entries",
            "get_basket",
            "apply_basket_fill",
            "record_regime_decision",
            "get_regime_decisions",
            "set_runtime_state",
            "get_runtime_state",
        ):
            self.assertTrue(hasattr(LedgerStore, op), msg=f"LedgerStore Protocol 缺少 {op}")
            self.assertIsNotNone(annotations, msg="Protocol 應保留型別註解")


class TestBackfillAndAuditTooling(unittest.TestCase):
    def test_backfill_module_exports_contract(self):
        from core.infrastructure.database import ledger_backfill

        self.assertTrue(callable(ledger_backfill.backfill_coinfull_prime_side_files))
        self.assertTrue(callable(ledger_backfill.export_ledger_to_side_files))
        self.assertEqual(ledger_backfill.SCHEDULER_BOT_ID, "coinfull_prime:scheduler")

    def test_cli_tool_exists_and_is_executable_entry(self):
        tool = PROJECT_ROOT / "tools/ledger_consistency_audit.py"
        self.assertTrue(tool.exists())
        text = tool.read_text(encoding="utf-8")
        self.assertIn("def main(", text)
        self.assertIn("--backfill", text)
        self.assertIn("--fail-on", text)

    def test_api_endpoint_registered(self):
        text = (PROJECT_ROOT / "web_ui/backend/routes/system.py").read_text(encoding="utf-8")
        self.assertIn('@router.get("/ledger-audit")', text)
        self.assertIn("run_audit", text)


if __name__ == "__main__":
    unittest.main()
