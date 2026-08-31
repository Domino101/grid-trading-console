"""P3-2: grid three-way reconciliation — self-heal scenarios with audit.

情境 A：本地持倉=0、交易所持倉>0 → 回補 + 審計。
情境 A'：本地持倉>0、交易所=0 → 按交易所真值歸零 + 審計（幽靈持倉）。
情境 B：本地掛單（含 TP 單語意）交易所已無 → 標記取消 + 審計。
"""

import os
import tempfile
import time
import unittest
from decimal import Decimal
from types import SimpleNamespace

from core.infrastructure.database.ledger_repository import LedgerRepository
from core.infrastructure.database.models import BotRecord
from core.services.reconciler.exchange_reconciler import ExchangeReconciler


class FakeExchange:
    def __init__(self, positions=None, open_orders=None):
        self._positions = positions or []
        self._open_orders = open_orders or []

    async def get_positions(self, symbols=None):
        return self._positions

    async def get_open_orders(self, symbol=None):
        return self._open_orders


class FakeEngine:
    def __init__(self, exchange, pending=None):
        self.exchange = exchange
        self._pending = pending or []

    def get_pending_orders(self):
        return self._pending

    async def get_current_price(self):
        return Decimal("123.45")


class FakeState:
    current_position = Decimal("0")


class FakeTracker:
    def __init__(self, virtual_position=Decimal("0")):
        self.virtual_position = Decimal(str(virtual_position))
        self.recovered_with = None

    def sync_recovered_position(self, virtual_position, virtual_cost):
        self.virtual_position = Decimal(str(virtual_position))
        self.recovered_with = (Decimal(str(virtual_position)), Decimal(str(virtual_cost)))


class FakeCoordinator:
    def __init__(self, bot_id, exchange, pending=None, virtual_position=Decimal("0")):
        self.bot_id = bot_id
        self.config = SimpleNamespace(symbol="ETH/USDC:USDC")
        self.engine = FakeEngine(exchange, pending)
        self.tracker = FakeTracker(virtual_position)
        self.state = FakeState()

    async def recover_old_orders(self):
        return None


def _position(qty, entry="100"):
    return SimpleNamespace(symbol="ETH/USDC:USDC", size=Decimal(str(qty)), entry_price=Decimal(entry))


class TestGridReconcile(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # 事件審計經由 LedgerRepository 單例 → 測試指向暫存 DB
        self.repo = LedgerRepository(os.path.join(self.tmp.name, "reconcile.sqlite3"))
        LedgerRepository._instance = self.repo
        self.addCleanup(self._reset_singleton)

    def _reset_singleton(self):
        LedgerRepository._instance = None

    def _register(self, bot_id):
        self.repo.register_or_update_bot(BotRecord(
            bot_id=bot_id, strategy_type="grid", symbol="ETH/USDC", exchange="binance",
            account_mode="paper", status="running", created_at=time.time(),
        ))

    async def test_scenario_a_exchange_position_rehydrates_local(self):
        """本地持倉=0、交易所>0 → 回補 + reconcile 審計事件。"""
        self._register("grid-a")
        coordinator = FakeCoordinator("grid-a", FakeExchange(positions=[_position("2.5", "120.5")]))
        result = await ExchangeReconciler.reconcile_grid(coordinator)
        self.assertTrue(result["rehydrated"])
        self.assertEqual(result["status"], "success")
        self.assertEqual(coordinator.tracker.virtual_position, Decimal("2.5"))
        self.assertEqual(coordinator.tracker.recovered_with[1], Decimal("120.5"))
        audits = [e for e in self.repo.get_events("grid-a") if e.event_type == "reconcile"]
        self.assertTrue(any(a.payload.get("action") == "position_rehydrated" for a in audits))

    async def test_scenario_a_prime_ghost_position_zeroed(self):
        """本地>0、交易所=0 → 按交易所真值歸零 + 審計。"""
        self._register("grid-a2")
        coordinator = FakeCoordinator(
            "grid-a2", FakeExchange(positions=[_position("0", "0")]), virtual_position=Decimal("3.0")
        )
        result = await ExchangeReconciler.reconcile_grid(coordinator)
        self.assertTrue(result["rehydrated"])
        self.assertEqual(coordinator.tracker.virtual_position, Decimal("0"))
        audits = [e for e in self.repo.get_events("grid-a2") if e.event_type == "reconcile"]
        self.assertTrue(any(a.payload.get("action") == "position_zeroed_to_exchange" for a in audits))

    async def test_scenario_b_stale_local_pending_cancelled_with_audit(self):
        """本地掛單交易所已無 → mark_cancelled + 審計（TP 單同語意）。"""
        self._register("grid-b")
        stale = SimpleNamespace(
            order_id="ex-1", client_id="cid-1", side=SimpleNamespace(value="buy"),
            price=Decimal("1200"), grid_id=3,
            status=SimpleNamespace(value="pending"),
            is_pending=lambda: True,
            mark_cancelled=lambda: None,
        )
        # 用真實 GridOrder 以驗證 mark_cancelled 生效
        from core.services.grid.models import GridOrder, GridOrderSide, GridOrderStatus

        from datetime import datetime as _dt

        stale_real = GridOrder(
            order_id="ex-1", grid_id=3, side=GridOrderSide.BUY, price=Decimal("1200"),
            amount=Decimal("0.1"), status=GridOrderStatus.PENDING, created_at=_dt.now(),
            client_id="cid-1",
        )
        alive = GridOrder(
            order_id="ex-2", grid_id=4, side=GridOrderSide.SELL, price=Decimal("1300"),
            amount=Decimal("0.1"), status=GridOrderStatus.PENDING, created_at=_dt.now(),
            client_id="cid-2",
        )
        exchange = FakeExchange(
            positions=[],
            open_orders=[SimpleNamespace(id="ex-2", client_id="cid-2")],
        )
        coordinator = FakeCoordinator("grid-b", exchange, pending=[stale_real, alive])
        # 為了讓 is_pending 反映真實狀態，覆寫為 GridOrder 的方法綁定
        result = await ExchangeReconciler.reconcile_grid(coordinator)
        self.assertGreaterEqual(result["stale_local_orders"], 1)
        self.assertEqual(stale_real.status, GridOrderStatus.CANCELLED)
        self.assertEqual(alive.status, GridOrderStatus.PENDING)  # 在世單不受影響
        audits = [e for e in self.repo.get_events("grid-b") if e.event_type == "reconcile"]
        self.assertTrue(any(a.payload.get("action") == "stale_local_order_cancelled" for a in audits))

    async def test_exchange_query_failure_degrades_without_fixes(self):
        """交易所查詢失敗 → 不做任何修復（fail-safe），回報 error/skipped 語意不誤傷。"""
        self._register("grid-c")

        class ExplodingExchange(FakeExchange):
            async def get_positions(self, symbols=None):
                raise RuntimeError("network down")

            async def get_open_orders(self, symbol=None):
                raise RuntimeError("network down")

        coordinator = FakeCoordinator("grid-c", ExplodingExchange(), virtual_position=Decimal("1.0"))
        result = await ExchangeReconciler.reconcile_grid(coordinator)
        self.assertEqual(result["status"], "success")  # 查詢失敗僅警告，不誤傷本地狀態
        self.assertFalse(result["rehydrated"])
        self.assertEqual(coordinator.tracker.virtual_position, Decimal("1.0"))


if __name__ == "__main__":
    unittest.main()
