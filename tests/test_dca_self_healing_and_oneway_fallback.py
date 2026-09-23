"""
單元測試：DCA 持倉歸零自癒與單向持倉反向 TP 降級限價單
"""

import asyncio
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.services.dca.coordinator.dca_coordinator import DcaCoordinator
from core.services.dca.coordinator.take_profit_manager import (
    TakeProfitManager,
    TpPositionClosedExternallyError,
)
from core.services.dca.execution.dca_order_executor import DcaOrderExecutor
from core.services.dca.interfaces.dca_executor import (
    IDcaOrderExecutor,
    OrderOutcome,
    OutcomeKind,
)
from core.services.dca.models.dca_config import DcaConfig, DcaDirection
from core.services.dca.models.dca_order import (
    DcaOrder,
    DcaOrderRole,
    DcaOrderStatus,
    bot_id_hash8,
)
from core.services.dca.models.dca_state import DcaState, DcaStatus


class TestDcaSelfHealingAndFallback(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.config = DcaConfig(
            exchange="binance",
            symbol="ETH/USDC:USDC",
            direction=DcaDirection.LONG,
            base_order_amount=Decimal("40.0"),
            max_safety_orders=5,
            price_deviation_percent=Decimal("1.0"),
            take_profit_percent=Decimal("1.0"),
            active_safety_orders_window=2,
            cooldown_seconds=300,
        )

    async def test_zero_position_self_healing_in_degraded(self):
        """測試在 degraded 狀態下，若交易所持倉為 0，能自主收輪轉入 COMPLETED"""
        mock_executor = AsyncMock(spec=IDcaOrderExecutor)
        mock_executor.fetch_open_orders.return_value = []
        mock_executor.fetch_position.return_value = {
            "size": Decimal("0"),
            "side": "none",
            "raw_amount": Decimal("0"),
            "entry_price": Decimal("0"),
        }

        coord = DcaCoordinator(self.config, "test-bot-01", mock_executor)
        coord.state.status = DcaStatus.DEGRADED
        coord.state.position_quantity = Decimal("1000.0")
        coord.state.total_cost = Decimal("100.0")

        # 模擬殘留掛單
        order = DcaOrder(
            client_order_id="test_so01", symbol="ETH/USDC:USDC", side="buy",
            role=DcaOrderRole.SAFETY, price=Decimal("2400"), quantity=Decimal("1"),
        )
        coord._active["test_so01"] = order

        await coord._step_degraded()

        # 驗證狀態已切至 COMPLETED
        self.assertEqual(coord.state.status, DcaStatus.COMPLETED)
        # 驗證撤單已被調用
        mock_executor.cancel.assert_awaited_once_with(order)

    async def test_zero_position_self_healing_in_reconcile(self):
        """測試在 _reconcile 對帳中，若交易所持倉為 0，能自動標記 _flag_round_finished"""
        mock_executor = AsyncMock(spec=IDcaOrderExecutor)
        mock_executor.fetch_open_orders.return_value = []
        mock_executor.fetch_position.return_value = {
            "size": Decimal("0"),
            "side": "none",
            "raw_amount": Decimal("0"),
            "entry_price": Decimal("0"),
        }

        coord = DcaCoordinator(self.config, "test-bot-02", mock_executor)
        coord.state.status = DcaStatus.AVERAGING
        coord.state.position_quantity = Decimal("500.0")

        await coord._reconcile()

        self.assertTrue(coord._flag_round_finished)

    async def test_oneway_opposing_tp_fallback_to_normal_limit(self):
        """測試多單在單向空頭持倉衝突下，ReduceOnly 被拒能自動降級為普通限價單成功送出"""
        mock_exchange = AsyncMock()

        # 第一次 create_order (帶 reduceOnly) 拋出 -2022
        # 第二次 create_order (不帶 reduceOnly) 成功
        def side_effect_create(*args, **kwargs):
            params = kwargs.get("params", {})
            if params.get("reduceOnly") or params.get("reduce_only"):
                raise RuntimeError('binance {"code":-2022,"msg":"ReduceOnly Order is rejected."}')
            return SimpleNamespace(id="order-12345", status="open")

        mock_exchange.create_order.side_effect = side_effect_create
        # 模擬交易所實盤為反向持倉（空頭 -0.180）
        pos_data = SimpleNamespace(
            symbol="ETH/USDC:USDC",
            amount=Decimal("-0.180"),
            size=Decimal("0.180"),
            side=SimpleNamespace(value="short"),
            entry_price=Decimal("2500"),
        )
        mock_exchange.get_positions.return_value = [pos_data]

        executor = DcaOrderExecutor(self.config, "test-bot-03", mock_exchange)

        outcome = await executor.place_limit(
            role=DcaOrderRole.TAKE_PROFIT,
            side="sell",
            price=Decimal("2520"),
            quantity=Decimal("0.016"),
        )

        self.assertEqual(outcome.kind, OutcomeKind.PLACED)
        self.assertEqual(outcome.order.order_id, "order-12345")
        # 驗證 create_order 被呼叫了兩次（第一次被拒，第二次降級成功）
        self.assertEqual(mock_exchange.create_order.await_count, 2)
        # 驗證第二次呼叫的 params 中沒有 reduceOnly
        second_call_params = mock_exchange.create_order.await_args_list[1].kwargs["params"]
        self.assertNotIn("reduce_only", second_call_params)
        self.assertNotIn("reduceOnly", second_call_params)

    async def test_tp_position_closed_externally_when_exchange_pos_zero(self):
        """測試在持倉為 0 時，ReduceOnly 被拒會回傳 POSITION_CLOSED_EXTERNALLY 而不重試"""
        mock_exchange = AsyncMock()
        mock_exchange.create_order.side_effect = RuntimeError('binance {"code":-2022,"msg":"ReduceOnly Order is rejected."}')
        # 模擬交易所實盤持倉已為 0
        mock_exchange.get_positions.return_value = []

        executor = DcaOrderExecutor(self.config, "test-bot-04", mock_exchange)

        outcome = await executor.place_limit(
            role=DcaOrderRole.TAKE_PROFIT,
            side="buy",
            price=Decimal("0.092"),
            quantity=Decimal("1253.0"),
        )

        self.assertEqual(outcome.kind, OutcomeKind.POSITION_CLOSED_EXTERNALLY)
        # 驗證沒有進行第二次降級呼叫（因為持倉為 0 不能亂開新倉）
        self.assertEqual(mock_exchange.create_order.await_count, 1)

    async def test_reconcile_ignores_stale_order_id(self):
        """測試 _reconcile 對帳時若交易所返回同 client_id 但不同 order_id 的舊單，會被防禦性忽略"""
        mock_executor = AsyncMock(spec=IDcaOrderExecutor)
        # 交易所 open_orders 不包含此訂單（觸發 fetch_order）
        mock_executor.fetch_open_orders.return_value = []

        # 交易所返回同 client_id 但 order_id 不同的舊訂單
        stale_order = DcaOrder(
            client_order_id="dca_testbot_short_base_0001",
            order_id="exchange-old-999",
            symbol="ETH/USDC:USDC",
            side="sell",
            role=DcaOrderRole.BASE,
            price=Decimal("2500"),
            quantity=Decimal("1"),
            status=DcaOrderStatus.FILLED,
            average_fill_price=Decimal("2500"),
            filled_quantity=Decimal("1"),
        )
        mock_executor.fetch_order.return_value = stale_order
        mock_executor.fetch_position.return_value = {
            "size": Decimal("1.0"),
            "side": "short",
            "raw_amount": Decimal("-1.0"),
            "entry_price": Decimal("2500"),
        }

        coord = DcaCoordinator(self.config, "test-bot-05", mock_executor)
        coord.state.status = DcaStatus.AVERAGING
        coord.state.position_quantity = Decimal("1.0")

        # 本機追蹤的 order 其 order_id 為 current-111
        tracked_order = DcaOrder(
            client_order_id="dca_testbot_short_base_0001",
            order_id="current-111",
            symbol="ETH/USDC:USDC",
            side="sell",
            role=DcaOrderRole.BASE,
            price=Decimal("2500"),
            quantity=Decimal("1"),
            status=DcaOrderStatus.PLACED,
        )
        coord._active["dca_testbot_short_base_0001"] = tracked_order

        # 執行對帳
        await coord._reconcile()

        # 驗證此舊單被忽略，沒有進入 _processed_fill_ids，持倉數量未被重複累加
        self.assertNotIn("dca_testbot_short_base_0001", coord._processed_fill_ids)
        self.assertEqual(coord.state.position_quantity, Decimal("1.0"))

    async def test_tp_replace_heals_position_from_exchange(self):
        """測試當 TP 替換失敗時，會從交易所持倉自癒本機 position_quantity 與 total_cost"""
        mock_executor = AsyncMock(spec=IDcaOrderExecutor)
        # 交易所實際持倉為 5.0，均價 1500，方向與做多機器人一致（long）
        mock_executor.fetch_position.return_value = {
            "size": Decimal("5.0"),
            "side": "long",
            "raw_amount": Decimal("5.0"),
            "entry_price": Decimal("1500"),
        }

        coord = DcaCoordinator(self.config, "test-bot-06", mock_executor)
        coord.state.status = DcaStatus.AVERAGING
        # 本機因重複累加等原因虛高為 12.0
        coord.state.position_quantity = Decimal("12.0")
        coord.state.total_cost = Decimal("18000.0")
        coord._flag_safety_filled = True

        from core.services.dca.coordinator.take_profit_manager import TpReplaceError
        mock_tp_mgr = AsyncMock()
        mock_tp_mgr.replace.side_effect = TpReplaceError("ReduceOnly rejected", tp_missing=True)
        coord._tp_manager = mock_tp_mgr

        await coord._attempt_tp_replace()

        # 驗證持倉已自癒為交易所實盤的 5.0，total_cost 自癒為 7500.0
        self.assertEqual(coord.state.position_quantity, Decimal("5.0"))
        self.assertEqual(coord.state.total_cost, Decimal("7500.0"))

    async def test_reconcile_startup_orders_syncs_order_seq(self):
        """測試啟動時接管訂單能將 _order_seq 同步至最大序號"""
        mock_exchange = AsyncMock()
        executor = DcaOrderExecutor(self.config, "test-bot-07", mock_exchange)
        executor._order_seq = 0

        my_hash = bot_id_hash8("test-bot-07")
        # 模擬啟動時交易所已存在 seq=0042 的訂單
        startup_order = DcaOrder(
            client_order_id=f"dca_{my_hash}_long_tp_0042",
            order_id="ex-42",
            symbol="ETH/USDC:USDC",
            side="sell",
            role=DcaOrderRole.TAKE_PROFIT,
            price=Decimal("2600"),
            quantity=Decimal("0.1"),
        )
        executor.fetch_open_orders = AsyncMock(return_value=[startup_order])

        coord = DcaCoordinator(self.config, "test-bot-07", executor)
        taken = await coord.reconcile_startup_orders()

        self.assertEqual(taken, 1)
        self.assertEqual(executor._order_seq, 42)

    async def test_safety_order_insufficient_margin_does_not_fail_coordinator(self):
        """測試安全單遭遇保證金不足 (-2019) 時，coordinator 僅暫緩補單，維持 AVERAGING 狀態且不撤銷已有 TP 單"""
        mock_executor = AsyncMock(spec=IDcaOrderExecutor)
        mock_executor.last_price.return_value = Decimal("2480")
        # 模擬安全單下單回傳保證金不足
        mock_executor.place_limit.return_value = OrderOutcome(
            OutcomeKind.REJECTED_INSUFFICIENT_MARGIN,
            reason='binance {"code":-2019,"msg":"Margin is insufficient."}',
        )

        coord = DcaCoordinator(self.config, "test-bot-08", mock_executor)
        coord.state.active_direction = "long"
        coord.state.status = DcaStatus.AVERAGING
        coord.state.position_quantity = Decimal("1.0")
        coord.state.total_cost = Decimal("2500.0")

        # 模擬已有 TP 單掛在交易所
        tp_order = DcaOrder(
            client_order_id="dca_testbot_long_tp_0001",
            order_id="tp-999",
            symbol="ETH/USDC:USDC",
            side="sell",
            role=DcaOrderRole.TAKE_PROFIT,
            price=Decimal("2550"),
            quantity=Decimal("1.0"),
            status=DcaOrderStatus.PLACED,
        )
        coord._active[tp_order.client_order_id] = tp_order
        coord._tp_client_id = tp_order.client_order_id

        # 初始化安全單槽位
        from core.services.dca.ladder import build_ladder
        plan = build_ladder(self.config, Decimal("2500"), "long")
        coord.state.init_slots(plan.levels)

        # 執行補單
        await coord._replenish_window()

        # 驗證狀態未變成 failed，依然維持 AVERAGING
        self.assertEqual(coord.state.status, DcaStatus.AVERAGING)
        # 驗證 TP 單依然在 _active 中，沒有被撤單
        self.assertIn(tp_order.client_order_id, coord._active)

    async def test_reconcile_runtime_syncs_discrepant_position(self):
        """測試在線對帳巡檢時，若交易所實盤持倉 (60.2) 與本機持倉 (30.2) 不一致，自癒同步並觸發 TP 修正"""
        mock_executor = AsyncMock(spec=IDcaOrderExecutor)
        mock_executor.fetch_open_orders.return_value = []
        mock_executor.fetch_position.return_value = {
            "size": Decimal("60.2"),
            "side": "short",
            "raw_amount": Decimal("-60.2"),
            "entry_price": Decimal("6.62"),
        }

        coord = DcaCoordinator(self.config, "test-bot-09", mock_executor)
        coord.state.active_direction = "short"
        coord.state.status = DcaStatus.AVERAGING
        coord.state.position_quantity = Decimal("30.2")
        coord.state.total_cost = Decimal("199.5")

        await coord._reconcile()

        # 驗證持倉已自癒為 60.2
        self.assertEqual(coord.state.position_quantity, Decimal("60.2"))
        self.assertEqual(coord.state.entry_price, Decimal("6.62"))
        self.assertEqual(coord.state.total_cost, Decimal("60.2") * Decimal("6.62"))
        # 驗證觸發了 TP 重掛標記
        self.assertTrue(coord._pending_tp_replace)

    async def test_reconcile_ignores_opposing_direction_position(self):
        """測試在線對帳巡檢時，若交易所存在反向持倉（例如做多機器人遇到實盤空頭），禁止覆蓋本機持倉"""
        mock_executor = AsyncMock(spec=IDcaOrderExecutor)
        mock_executor.fetch_open_orders.return_value = []
        # 交易所實盤為反向空頭持倉 22.0
        mock_executor.fetch_position.return_value = {
            "size": Decimal("22.0"),
            "side": "short",
            "raw_amount": Decimal("-22.0"),
            "entry_price": Decimal("9.113"),
        }

        coord = DcaCoordinator(self.config, "test-bot-10", mock_executor)
        coord.state.active_direction = "long"
        coord.state.status = DcaStatus.AVERAGING
        # 本機真實做多持倉為 3.0
        coord.state.position_quantity = Decimal("3.0")
        coord.state.total_cost = Decimal("26.78")
        coord.state.entry_price = Decimal("8.927")

        await coord._reconcile()

        # 驗證本機持倉絕對沒有被反向持倉污染覆蓋！
        self.assertEqual(coord.state.position_quantity, Decimal("3.0"))
        self.assertEqual(coord.state.entry_price, Decimal("8.927"))
        self.assertEqual(coord.state.total_cost, Decimal("26.78"))
        # 驗證不會觸發以 22.0 空單持倉覆蓋後的 TP 重掛
        self.assertFalse(coord._pending_tp_replace)

    async def test_tp_replace_ignores_opposing_direction_position(self):
        """測試 TP 替換失敗時，若交易所存在反向持倉，自癒機制嚴格略過反向持倉，維持本機持倉"""
        mock_executor = AsyncMock(spec=IDcaOrderExecutor)
        # 交易所實盤為反向空頭持倉 22.0
        mock_executor.fetch_position.return_value = {
            "size": Decimal("22.0"),
            "side": "short",
            "raw_amount": Decimal("-22.0"),
            "entry_price": Decimal("9.113"),
        }

        coord = DcaCoordinator(self.config, "test-bot-11", mock_executor)
        coord.state.active_direction = "long"
        coord.state.status = DcaStatus.AVERAGING
        coord.state.position_quantity = Decimal("3.0")
        coord.state.total_cost = Decimal("26.78")
        coord.state.entry_price = Decimal("8.927")

        from core.services.dca.coordinator.take_profit_manager import TpReplaceError
        mock_tp_mgr = AsyncMock()
        mock_tp_mgr.replace.side_effect = TpReplaceError("ReduceOnly rejected", tp_missing=True)
        coord._tp_manager = mock_tp_mgr

        await coord._attempt_tp_replace()

        # 驗證本機持倉依然為 3.0，未被反向的 22.0 覆蓋
        self.assertEqual(coord.state.position_quantity, Decimal("3.0"))
        self.assertEqual(coord.state.total_cost, Decimal("26.78"))


if __name__ == "__main__":
    unittest.main()
