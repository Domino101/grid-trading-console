"""
CoinFull Prime P0 資金安全回歸測試 (2026-10-21)

覆蓋五個阻斷發布項：
A-1 止損 dry-run/adapter 缺失不得改帳本；只結算實際 filled
A-2 名單同步不得關閉未成功平倉的成分
A-3 degraded 資料缺漏時 hold，不換倉
A-4 對沖標的被外部機器人保護時整輪 abort
A-5 停止排程不得同時停止止損守護
"""

import asyncio
import time
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from core.infrastructure.database.models import BasketEntry
from core.services.coinfull_prime.models import (
    RebalanceReport,
    RankedItem,
    SignalZScores,
    UniverseRankResult,
)
from core.services.coinfull_prime.rebalancer import PortfolioRebalancer
from core.services.coinfull_prime.regime_filter import RegimeFilter
from core.services.coinfull_prime.service import CoinFullPrimeService


class _FakeBasketLedger:
    """只實作 execute_stop_loss 會用到的介面，並記錄是否被呼叫。"""

    def __init__(self, entries):
        self.entries = entries
        self.apply_calls = []
        self.fills = []
        self.events = []

    def load_basket(self, bot_id, account_id=None, active_only=True):
        if active_only:
            return [e for e in self.entries if e.status == "active"]
        return list(self.entries)

    def apply_basket_fill(self, **kwargs):
        self.apply_calls.append(kwargs)
        for entry in self.entries:
            if entry.symbol == kwargs["symbol"] and entry.status == "active":
                entry.quantity = max(0.0, entry.quantity - kwargs["quantity"])
                if entry.quantity <= 1e-18:
                    entry.quantity = 0.0
                    entry.status = "closed"
        return -150.0

    def record_fill(self, fill):
        self.fills.append(fill)

    def append_event(self, *args, **kwargs):
        self.events.append((args, kwargs))


class _FakeAdapter:
    name = "binance_testnet"
    _market_info = {}

    def __init__(self, filled=Decimal("40"), average=8.5, order_id="EX123"):
        self.filled = filled
        self.average = average
        self.order_id = order_id
        self.create_order_calls = []

    async def create_order(self, **kwargs):
        self.create_order_calls.append(kwargs)
        return SimpleNamespace(
            filled=self.filled,
            average=self.average,
            price=self.average,
            id=self.order_id,
            fee={"cost": 0.05, "currency": "USDT"},
        )


class _BasketLedgerStub:
    def __init__(self, active=None, all_entries=None):
        self.active = active or []
        self.all_entries = all_entries or []

    def load_basket(self, bot_id, account_id=None, active_only=True):
        return self.active if active_only else self.all_entries


class TestBasketMirrorResurrection(unittest.TestCase):
    def test_closed_rows_prevent_mirror_backfill(self):
        service = CoinFullPrimeService()
        service._ledger = lambda: _BasketLedgerStub(
            active=[],
            all_entries=[SimpleNamespace(symbol="AAAUSDT", status="closed", quantity=0.0)],
        )
        service._backfill_side_files = MagicMock(return_value=True)
        self.assertEqual(service.load_active_basket("paper"), set())
        service._backfill_side_files.assert_not_called()

    def test_export_removes_account_with_no_active_entries(self):
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mirror.json"
            path.write_text(json.dumps({"paper": {"managed_symbols": ["AAAUSDT"]}}), encoding="utf-8")
            service = CoinFullPrimeService()
            service._ledger = lambda: _BasketLedgerStub(active=[])
            service._basket_file = lambda: path
            service._export_basket_mirror("paper")
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("paper", data)


class TestStopLossSafety(unittest.TestCase):
    def setUp(self):
        self.rebalancer = PortfolioRebalancer({
            "portfolio": {"total_capital_usdt": 1000.0, "leverage": 1.0, "dry_run": True}
        })
        self.entry = BasketEntry(
            bot_id="coinfull_prime:p0_safety",
            account_id="p0_safety",
            symbol="P0SAFEUSDT",
            side="long",
            quantity=100.0,
            entry_price=10.0,
            notional_usdt=1000.0,
            status="active",
            updated_at=time.time(),
        )
        self.ledger = _FakeBasketLedger([self.entry])

    def _run(self, dry_run, adapter):
        import core.infrastructure.database.strategy_ledger as ledger_module

        with patch.object(ledger_module.StrategyLedger, "instance", return_value=self.ledger):
            return asyncio.run(self.rebalancer.execute_stop_loss(
                symbol="P0SAFEUSDT",
                account_id="p0_safety",
                current_price=8.5,
                exchange_adapter=adapter,
                dry_run=dry_run,
            ))

    def test_dry_run_does_not_mutate_basket_or_ledger(self):
        result = self._run(dry_run=True, adapter=_FakeAdapter())
        self.assertEqual(result["status"], "simulated")
        self.assertAlmostEqual(result["realized_pnl"], 0.0)
        self.assertEqual(self.entry.quantity, 100.0)
        self.assertEqual(self.entry.status, "active")
        self.assertEqual(self.ledger.apply_calls, [])
        self.assertEqual(self.ledger.fills, [])
        self.assertEqual(self.ledger.events, [])

    def test_missing_adapter_does_not_mutate_basket(self):
        result = self._run(dry_run=False, adapter=None)
        self.assertEqual(result["status"], "failed")
        self.assertIn("no exchange adapter", result["error"])
        self.assertEqual(self.entry.quantity, 100.0)
        self.assertEqual(self.entry.status, "active")
        self.assertEqual(self.ledger.apply_calls, [])

    def test_partial_fill_settles_only_filled_quantity(self):
        result = self._run(dry_run=False, adapter=_FakeAdapter(filled=Decimal("40")))
        self.assertEqual(result["status"], "filled")
        self.assertEqual(result["quantity"], 40.0)
        self.assertEqual(result["remaining_quantity"], 60.0)
        self.assertEqual(len(self.ledger.apply_calls), 1)
        self.assertEqual(self.ledger.apply_calls[0]["quantity"], 40.0)
        self.assertEqual(self.entry.quantity, 60.0)
        self.assertEqual(self.entry.status, "active")
        self.assertEqual(self.ledger.fills[0].exchange_order_id, "EX123")
        self.assertIn("EX123", self.ledger.fills[0].fill_id)


class TestBasketNameSyncPolicy(unittest.TestCase):
    def setUp(self):
        self.service = CoinFullPrimeService()
        self.rank_result = UniverseRankResult(
            long_list=[
                RankedItem(
                    symbol="AAAUSDT", score=1.0, rank=1, signals=SignalZScores(),
                    side="long", target_notional=100.0,
                )
            ],
            status="success",
        )

    def test_save_active_basket_never_closes_missing_components(self):
        calls = []

        class _Ledger:
            def save_basket(self, bot_id, account_id, entries, close_missing=True):
                calls.append({
                    "entries": list(entries),
                    "close_missing": close_missing,
                })
                return len(entries)

        self.service._ledger = lambda: _Ledger()
        self.service._export_basket_mirror = MagicMock()
        self.service.save_active_basket("p0_sync", ["AAAUSDT"], sides={"AAAUSDT": "long"})
        self.assertEqual(len(calls), 1)
        self.assertFalse(calls[0]["close_missing"])
        self.assertEqual([e.symbol for e in calls[0]["entries"]], ["AAAUSDT"])

    def test_sync_only_on_fully_completed_rebalance(self):
        report = RebalanceReport(status="completed")
        self.assertTrue(CoinFullPrimeService._should_sync_basket(self.rank_result, report, False))
        for status in ("partial", "failed", "skipped", "hedge_blocked", "degraded_hold"):
            report = RebalanceReport(status=status)
            self.assertFalse(
                CoinFullPrimeService._should_sync_basket(self.rank_result, report, False),
                f"status={status} 不應同步名單",
            )
        report = RebalanceReport(status="completed")
        self.assertFalse(CoinFullPrimeService._should_sync_basket(self.rank_result, report, True))


class TestDegradedHold(unittest.TestCase):
    def test_degraded_data_holds_instead_of_rebalancing(self):
        service = CoinFullPrimeService()
        service.quant_engine = MagicMock()
        service.quant_engine.fetch_universe = AsyncMock(return_value=["BTCUSDT", "AAAUSDT"])
        service.quant_engine.fetch_features = AsyncMock()
        service.rebalancer = MagicMock()
        service.rebalancer.total_capital = 1000.0
        service.rebalancer.leverage = 1.0
        service.rebalancer.dry_run = True
        service.rebalancer.execute_rebalance = AsyncMock()
        service.regime_filter = RegimeFilter({"regime_gate": {"mode": "flatten"}})
        service.regime_filter.collect = AsyncMock(return_value={
            "btc_above_ma50": None,
            "breadth_pct": None,
            "funding_pctile": None,
            "btc_ret_1d_pct": None,
            "btc_close": None,
            "btc_ma50": None,
        })
        service.get_adapter = AsyncMock(return_value=None)
        service.load_active_basket = MagicMock(return_value=set())
        service.get_external_bot_symbols = MagicMock(return_value=set())
        service.append_regime_history = MagicMock()
        service._persist_run_snapshot = MagicMock()
        service.exclude_tradfi = True
        service.selected_account = "paper"

        rank_result, report = asyncio.run(service.trigger_once(dry_run=True, account_id="paper"))

        self.assertEqual(report.status, "degraded_hold")
        self.assertEqual(report.orders, [])
        self.assertEqual(rank_result.long_list, [])
        service.rebalancer.execute_rebalance.assert_not_called()
        service.quant_engine.fetch_features.assert_not_called()


class TestHedgeProtection(unittest.TestCase):
    def test_protected_hedge_aborts_before_opening_longs(self):
        sig = SignalZScores()
        rank_result = UniverseRankResult(
            long_list=[
                RankedItem(
                    symbol="AAAUSDT", score=1.0, rank=1, signals=sig,
                    side="long", target_notional=100.0,
                )
            ],
            short_list=[
                RankedItem(
                    symbol="BTCUSDT", score=0.0, rank=0, signals=sig,
                    side="short", target_notional=500.0,
                )
            ],
            status="success",
        )
        rebalancer = PortfolioRebalancer({
            "portfolio": {"total_capital_usdt": 1000.0, "leverage": 1.0, "dry_run": True}
        })
        report = asyncio.run(rebalancer.execute_rebalance(
            rank_result=rank_result,
            exchange_adapter=None,
            dry_run=True,
            protected_symbols={"BTCUSDT"},
            account_id="paper",
        ))
        self.assertEqual(report.status, "hedge_blocked")
        self.assertEqual(report.orders, [])
        self.assertIn("BTCUSDT", report.error)


class TestStopLossLifecycle(unittest.TestCase):
    def _service_with_task(self):
        service = CoinFullPrimeService()
        service._scheduler_task = None
        service._stop_loss_task = MagicMock()
        service._is_running = True
        service.scheduler_enabled = True
        service.save_scheduler_state = MagicMock()
        return service

    def test_stop_scheduler_keeps_guard_when_active_longs(self):
        service = self._service_with_task()
        service._has_active_long_positions = MagicMock(return_value=True)
        service.stop_scheduler(disable=True)
        self.assertTrue(service._stop_loss_running)
        service._stop_loss_task.cancel.assert_not_called()

    def test_stop_scheduler_cancels_guard_when_no_longs(self):
        service = self._service_with_task()
        task = service._stop_loss_task
        service._has_active_long_positions = MagicMock(return_value=False)
        service.stop_scheduler(disable=True)
        self.assertFalse(service._stop_loss_running)
        task.cancel.assert_called_once()
        self.assertIsNone(service._stop_loss_task)

    def test_shutdown_always_cancels_guard(self):
        service = self._service_with_task()
        task = service._stop_loss_task
        service._has_active_long_positions = MagicMock(return_value=True)
        service.stop_scheduler(disable=False)
        self.assertFalse(service._stop_loss_running)
        task.cancel.assert_called_once()
        service._has_active_long_positions.assert_not_called()


if __name__ == "__main__":
    unittest.main()
