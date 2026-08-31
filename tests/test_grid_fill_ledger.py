"""P1-3: grid fills are recorded to the immutable trade_fills ledger."""

import unittest
import uuid
from datetime import datetime
from decimal import Decimal

from core.infrastructure.database.ledger_repository import LedgerRepository
from core.services.grid.implementations.position_tracker_impl import PositionTrackerImpl
from core.services.grid.models import (
    GridConfig,
    GridOrder,
    GridOrderSide,
    GridOrderStatus,
    GridState,
    GridType,
)


def _filled_order(order_id, side, price, amount, client_id):
    return GridOrder(
        order_id=order_id,
        grid_id=1,
        side=side,
        price=price,
        amount=amount,
        status=GridOrderStatus.FILLED,
        created_at=datetime.now(),
        client_id=client_id,
        filled_at=datetime.now(),
        filled_price=price,
        filled_amount=amount,
    )


class TestGridFillLedger(unittest.TestCase):
    def setUp(self):
        LedgerRepository.reset_instance()
        self.bot_id = "grid-fill-" + uuid.uuid4().hex[:12]
        config = GridConfig(
            exchange="binance",
            symbol="ETH/USDC:USDC",
            grid_type=GridType.NEUTRAL,
            order_amount=Decimal("0.01"),
            lower_price=Decimal("1800"),
            upper_price=Decimal("2000"),
            grid_interval=Decimal("20"),
            fee_rate=Decimal("0.0005"),
        )
        self.tracker = PositionTrackerImpl(config, GridState(), bot_id=self.bot_id)

    def test_fills_are_recorded_to_immutable_ledger(self):
        buy = _filled_order("ord_buy_1", GridOrderSide.BUY, Decimal("1840"), Decimal("0.01"), "cid_buy_1")
        sell = _filled_order("ord_sell_1", GridOrderSide.SELL, Decimal("1890"), Decimal("0.01"), "cid_sell_1")
        self.tracker.record_filled_order(buy)
        self.tracker.record_filled_order(sell)

        fills = LedgerRepository.instance().get_fills(self.bot_id)
        self.assertEqual(len(fills), 2)
        sides = {f.side for f in fills}
        self.assertEqual(sides, {"buy", "sell"})
        for f in fills:
            self.assertEqual(f.bot_id, self.bot_id)
            self.assertEqual(f.role, "grid")
            self.assertIsNotNone(f.exchange_order_id)
            self.assertIsNotNone(f.client_order_id)
            self.assertGreater(f.executed_at, 0)

    def test_fill_recording_is_idempotent(self):
        order = _filled_order("ord_buy_2", GridOrderSide.BUY, Decimal("1840"), Decimal("0.01"), "cid_buy_2")
        self.tracker.record_filled_order(order)
        self.tracker.record_filled_order(order)  # 重複記錄（例如重啟重放）

        fills = LedgerRepository.instance().get_fills(self.bot_id)
        self.assertEqual(len(fills), 1)

    def test_no_bot_id_skips_ledger_write(self):
        config = GridConfig(
            exchange="binance",
            symbol="ETH/USDC:USDC",
            grid_type=GridType.NEUTRAL,
            order_amount=Decimal("0.01"),
            lower_price=Decimal("1800"),
            upper_price=Decimal("2000"),
            grid_interval=Decimal("20"),
            fee_rate=Decimal("0.0005"),
        )
        tracker = PositionTrackerImpl(config, GridState())  # 無 bot_id
        order = _filled_order("ord_buy_3", GridOrderSide.BUY, Decimal("1840"), Decimal("0.01"), "cid_buy_3")
        tracker.record_filled_order(order)
        self.assertEqual(LedgerRepository.instance().get_fills(self.bot_id), [])


if __name__ == "__main__":
    unittest.main()
