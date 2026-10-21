"""
CoinFull Prime P1-B 回歸測試 (2026-10-21)

C-4 資金費率 Carry 入帳與 equity snapshot helper
C-5 交易所持倉 vs bot_baskets reconciliation
"""

import asyncio
import time
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from core.infrastructure.database.models import BasketEntry
from core.services.coinfull_prime.service import CoinFullPrimeService


class _FakeLedger:
    def __init__(self):
        self.fills = []
        self.states = {}
        self.bots = []
        self.events = []

    def ensure_bot(self, **kwargs):
        self.bots.append(kwargs)

    def get_state(self, bot_id, key, default=None):
        return self.states.get((bot_id, key), default)

    def set_state(self, bot_id, key, value):
        self.states[(bot_id, key)] = value
        return True

    def record_fill(self, fill):
        if any(f.fill_id == fill.fill_id for f in self.fills):
            return False
        self.fills.append(fill)
        return True

    def get_fills(self, bot_id, limit=100000):
        return [f for f in self.fills if f.bot_id == bot_id]

    def append_event(self, *args, **kwargs):
        self.events.append((args, kwargs))


class _IncomeAdapter:
    def __init__(self, records):
        self._rest = SimpleNamespace(
            exchange=SimpleNamespace(fetch_income=lambda *args, **kwargs: records)
        )


class _LedgerAdapter:
    def __init__(self, records):
        self._rest = SimpleNamespace(
            exchange=SimpleNamespace(fetch_ledger=lambda *args, **kwargs: records)
        )


class _BalanceAdapter:
    def __init__(self, free, total):
        self.free = free
        self.total = total

    async def get_balances(self):
        return [SimpleNamespace(
            currency="USDT",
            free=Decimal(str(self.free)),
            total=Decimal(str(self.total)),
        )]


class TestFundingIncome(unittest.TestCase):
    def setUp(self):
        self.service = CoinFullPrimeService()
        self.ledger = _FakeLedger()
        self.service._ledger = lambda: self.ledger
        self.service.selected_account = "binance_testnet"
        self.service._is_running = False

    def _records(self):
        return [
            {
                "symbol": "BTC/USDT:USDT",
                "type": "funding",
                "income": "0.85",
                "currency": "USDT",
                "timestamp": 1700000000000,
                "id": "tran-1",
                "info": {"incomeType": "FUNDING_FEE", "asset": "USDT", "time": "1700000000000"},
            },
            {
                "symbol": "BTC/USDT:USDT",
                "type": "realized_pnl",
                "income": "5.0",
                "currency": "USDT",
                "timestamp": 1700000001000,
                "id": "tran-2",
                "info": {"incomeType": "REALIZED_PNL"},
            },
            {
                "symbol": "ETH/USDT:USDT",
                "type": "funding",
                "income": "-0.20",
                "currency": "USDT",
                "timestamp": 1700000002000,
                "id": "tran-3",
                "info": {"incomeType": "FUNDING_FEE", "asset": "USDT", "time": "1700000002000"},
            },
        ]

    def test_funding_income_is_recorded_idempotently(self):
        adapter = _IncomeAdapter(self._records())
        first = asyncio.run(self.service.sync_funding_income("binance_testnet", adapter=adapter, since_ms=0))
        self.assertEqual(first, 2)
        self.assertEqual(len(self.ledger.fills), 2)
        self.assertEqual({f.role for f in self.ledger.fills}, {"funding"})
        self.assertEqual({f.side for f in self.ledger.fills}, {"funding"})
        self.assertAlmostEqual(sum(f.realized_pnl for f in self.ledger.fills), 0.65)
        self.assertIn("funding:binance_testnet:tran-1", {f.fill_id for f in self.ledger.fills})
        cursor = self.ledger.states[("coinfull_prime:binance_testnet", "funding_sync_cursor")]
        self.assertEqual(cursor["since_ms"], 1700000002000)

        second = asyncio.run(self.service.sync_funding_income("binance_testnet", adapter=adapter, since_ms=0))
        self.assertEqual(second, 0)
        self.assertEqual(len(self.ledger.fills), 2)

    def test_funding_total_uses_canonical_fills(self):
        adapter = _IncomeAdapter(self._records())
        asyncio.run(self.service.sync_funding_income("binance_testnet", adapter=adapter))
        self.assertAlmostEqual(
            self.service._funding_income_total("coinfull_prime:binance_testnet"), 0.65
        )

    def test_fetch_ledger_fallback(self):
        records = [{
            "id": "ledger-1",
            "timestamp": 1700000000000,
            "type": "funding",
            "amount": 0.85,
            "direction": "in",
            "currency": "USDT",
            "info": {
                "symbol": "BTCUSDT",
                "incomeType": "FUNDING_FEE",
                "income": "0.85",
                "asset": "USDT",
                "time": "1700000000000",
                "tranId": "ledger-1",
            },
        }]
        recorded = asyncio.run(self.service.sync_funding_income(
            "binance_testnet", adapter=_LedgerAdapter(records), since_ms=0
        ))
        self.assertEqual(recorded, 1)
        self.assertEqual(self.ledger.fills[0].fill_id, "funding:binance_testnet:ledger-1")
        self.assertAlmostEqual(self.ledger.fills[0].realized_pnl, 0.85)

    def test_account_equity_from_adapter(self):
        equity = asyncio.run(self.service._account_equity_from_adapter(_BalanceAdapter(900, 1000)))
        self.assertEqual(equity, {"equity_usdt": 1000.0, "free_margin_usdt": 900.0})
        self.assertIsNone(asyncio.run(self.service._account_equity_from_adapter(None)))


class TestReconciliation(unittest.TestCase):
    def setUp(self):
        self.service = CoinFullPrimeService()
        self.ledger = _FakeLedger()
        self.service._ledger = lambda: self.ledger
        self.service.selected_account = "binance_testnet"
        self.service._is_running = False
        self.service.load_basket_entries = MagicMock(return_value=[
            BasketEntry(
                bot_id="coinfull_prime:binance_testnet",
                account_id="binance_testnet",
                symbol="AAAUSDT", side="long", quantity=10.0, status="active",
            ),
            BasketEntry(
                bot_id="coinfull_prime:binance_testnet",
                account_id="binance_testnet",
                symbol="BBBUSDT", side="long", quantity=10.0, status="active",
            ),
            BasketEntry(
                bot_id="coinfull_prime:binance_testnet",
                account_id="binance_testnet",
                symbol="CCCUSDT", side="long", quantity=2.0, status="active",
            ),
        ])
        self.service.get_positions = AsyncMock(return_value=[
            {"symbol": "AAAUSDT", "signed_size": 10.0, "role": "coinfull_prime"},
            {"symbol": "BBBUSDT", "signed_size": 5.0, "role": "coinfull_prime"},
            {"symbol": "DDDUSDT", "signed_size": 3.0, "role": "unmanaged"},
            {"symbol": "EEEUSDT", "signed_size": -1.0, "role": "external_bot"},
        ])
        self.service.get_adapter = AsyncMock(return_value=object())

    def test_reconcile_reports_missing_and_mismatch(self):
        result = asyncio.run(self.service.reconcile_positions("binance_testnet"))
        self.assertEqual(result["status"], "mismatch")
        self.assertEqual(result["missing_on_exchange"], ["CCCUSDT"])
        self.assertEqual(result["quantity_mismatch"][0]["symbol"], "BBBUSDT")
        self.assertEqual(result["unmanaged_on_exchange"], ["DDDUSDT"])
        self.assertEqual(result["protected_on_exchange"], ["EEEUSDT"])
        self.assertEqual(len(self.ledger.events), 1)
        args, kwargs = self.ledger.events[0]
        self.assertEqual(kwargs["event_type"], "reconcile")
        self.assertEqual(kwargs["payload"]["status"], "mismatch")

    def test_paper_and_missing_adapter_are_skipped(self):
        self.assertEqual(
            asyncio.run(self.service.reconcile_positions("paper"))["status"],
            "skipped_paper",
        )
        self.service.get_adapter = AsyncMock(return_value=None)
        self.assertEqual(
            asyncio.run(self.service.reconcile_positions("binance_testnet"))["status"],
            "skipped_no_adapter",
        )

    def test_positions_api_failure_is_not_reported_as_missing(self):
        self.service.get_positions = AsyncMock(return_value=[])
        self.service._last_positions_ok = False
        result = asyncio.run(self.service.reconcile_positions("binance_testnet"))
        self.assertEqual(result["status"], "skipped_api_error")
        self.assertEqual(self.ledger.events, [])


if __name__ == "__main__":
    unittest.main()
