import asyncio
import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timedelta

from core.services.grid.models.grid_config import GridConfig, GridType
from core.services.grid.models.grid_order import GridOrder, GridOrderSide, GridOrderStatus
from core.services.grid.implementations.grid_engine_impl import GridEngineImpl
from core.services.grid.implementations.order_health_checker import OrderHealthChecker
from core.adapters.exchanges.adapters.paper import create_paper_adapter
from core.adapters.exchanges.models import OrderData, OrderSide, OrderStatus


class TestGridGhostOrdersRecovery(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        config_data = {
            "grid_system": {
                "exchange": "binance",
                "symbol": "BR/USDT:USDT",
                "grid_type": "neutral",
                "price_range": {
                    "lower_price": 0.28,
                    "upper_price": 0.84,
                },
                "grid_interval": 0.002,
                "order_amount": 14.0,
                "quantity_precision": 1,
                "price_decimals": 4,
                "account_mode": "paper",
                "active_window_each_side": 2,
                "active_window_refresh_interval": 60,
            }
        }
        self.adapter = create_paper_adapter(config_data)
        self.grid_config = GridConfig(
            exchange="binance",
            symbol="BR/USDT:USDT",
            grid_type=GridType.NEUTRAL,
            lower_price=Decimal("0.28"),
            upper_price=Decimal("0.84"),
            grid_interval=Decimal("0.002"),
            order_amount=Decimal("14.0"),
            active_window_each_side=2,
            active_window_refresh_interval=60,
        )
        self.engine = GridEngineImpl(self.adapter)
        await self.engine.initialize(self.grid_config)
        self.engine.get_current_price = AsyncMock(return_value=Decimal("0.4500"))

    async def asyncTearDown(self):
        await self.engine.stop()
        await self.adapter.disconnect()

    async def test_remove_order_from_pending_by_client_id(self):
        """測試以 client_id 或 order_id 皆能精確刪除 pending orders 與快取"""
        order = GridOrder(
            order_id="grid_75_1000",
            client_id="grid_75_1000",
            grid_id=75,
            side=GridOrderSide.BUY,
            price=Decimal("0.4480"),
            amount=Decimal("14.0"),
            status=GridOrderStatus.PENDING,
            created_at=datetime.now() - timedelta(seconds=60),
        )
        self.engine._pending_orders["grid_75_1000"] = order
        self.engine._pending_orders_by_client_id["grid_75_1000"] = order

        removed = self.engine._remove_order_from_pending("grid_75_1000")
        self.assertGreaterEqual(removed, 1)
        self.assertNotIn("grid_75_1000", self.engine._pending_orders)
        self.assertNotIn("grid_75_1000", self.engine._pending_orders_by_client_id)

    async def test_sync_orders_from_exchange_purges_ghost_orders(self):
        """測試當交易所掛單為空時，_sync_orders_from_exchange 能清除過期的幽靈掛單"""
        # 模擬 5 筆過期的幽靈掛單 (下單超過 15 秒)
        old_time = datetime.now() - timedelta(seconds=30)
        for gid in [70, 71, 72, 73, 74]:
            oid = f"26000000{gid}"
            cid = f"grid_{gid}_123456"
            order = GridOrder(
                order_id=oid,
                client_id=cid,
                grid_id=gid,
                side=GridOrderSide.BUY,
                price=Decimal("0.4400"),
                amount=Decimal("14.0"),
                status=GridOrderStatus.PENDING,
                created_at=old_time,
            )
            order._placed_time = old_time.timestamp()
            self.engine._pending_orders[oid] = order
            self.engine._pending_orders_by_client_id[cid] = order

        self.assertEqual(len(self.engine._pending_orders), 5)
        self.assertEqual(len(self.engine._pending_orders_by_client_id), 5)

        # 交易所回傳空列表（掛單數為 0）
        await self.engine._sync_orders_from_exchange([])

        # 幽靈掛單應被全部清理
        self.assertEqual(len(self.engine._pending_orders), 0)
        self.assertEqual(len(self.engine._pending_orders_by_client_id), 0)

    async def test_order_health_checker_resurrects_when_zero_orders(self):
        """測試 OrderHealthChecker 遇到交易所 0 掛單時，不再直接跳過，而是強制自愈重建活躍窗口"""
        checker = OrderHealthChecker(self.grid_config, self.engine)

        # 模擬本機有幽靈單
        old_time = datetime.now() - timedelta(seconds=60)
        ghost_order = GridOrder(
            order_id="ghost_999",
            client_id="grid_999",
            grid_id=75,
            side=GridOrderSide.BUY,
            price=Decimal("0.4480"),
            amount=Decimal("14.0"),
            status=GridOrderStatus.PENDING,
            created_at=old_time,
        )
        ghost_order._placed_time = old_time.timestamp()
        self.engine._pending_orders["ghost_999"] = ghost_order

        # 模擬 _fetch_orders_and_positions 回傳 ([], [])
        checker._fetch_orders_and_positions = AsyncMock(return_value=([], []))

        # 執行健康檢查
        await checker._perform_health_check_internal()

        # 幽靈單應被清除
        self.assertNotIn("ghost_999", self.engine._pending_orders)
        # 且紙盤中應已重建活躍窗口掛單（至少 3 筆）
        open_orders = await self.adapter.get_open_orders("BR/USDT:USDT")
        self.assertGreaterEqual(len(open_orders), 3)


if __name__ == "__main__":
    unittest.main()
