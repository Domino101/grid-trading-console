import pytest
import asyncio
from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from core.services.grid.models import GridConfig, GridState, GridOrder, GridOrderSide, GridOrderStatus, GridType
from core.services.grid.implementations.grid_engine_impl import GridEngineImpl
from core.services.grid.implementations.position_tracker_impl import PositionTrackerImpl
from core.services.grid.coordinator.grid_coordinator import GridCoordinator
from core.services.grid.implementations.grid_strategy_impl import GridStrategyImpl
from web_ui.backend.runtime_repository import RuntimeSnapshotRepository
from core.adapters.exchanges.interface import ExchangeConfig, ExchangeType
from core.adapters.exchanges.adapters.binance_rest import BinanceRest


@pytest.fixture
def grid_config():
    return GridConfig(
        symbol="SOL/USDC:USDC",
        exchange="binance",
        grid_type=GridType.NEUTRAL,
        lower_price=Decimal("80"),
        upper_price=Decimal("120"),
        order_amount=Decimal("0.1"),
        grid_interval=Decimal("4.0"),
    )


@pytest.mark.asyncio
async def test_recover_old_orders_restores_state_and_profit(tmp_path, grid_config):
    """測試 recover_old_orders 正確匯入並從快照恢復歷史累計成交與盈虧"""
    db_path = str(tmp_path / "test_runtime.sqlite3")
    repo = RuntimeSnapshotRepository(db_path)
    RuntimeSnapshotRepository._instance = repo

    config_path = "config/grid/test-sol.yaml"
    mock_payload = {
        "symbol": "SOL/USDC:USDC",
        "filled_orders": {"buy": 8, "sell": 4, "cycles": 4},
        "profit": {"grid_profit": 16.0, "realized": 16.0, "fees": 0.0},
        "grid_position": 0.4,
        "average_cost": 95.0,
    }
    repo.save(config_path, mock_payload)

    state = GridState()
    tracker = PositionTrackerImpl(grid_config, state)
    mock_engine = AsyncMock()
    mock_engine.config = grid_config
    strategy = GridStrategyImpl()

    coordinator = GridCoordinator(
        config=grid_config,
        strategy=strategy,
        engine=mock_engine,
        tracker=tracker,
        grid_state=state,
        config_path=config_path,
    )

    await coordinator.recover_old_orders()

    assert state.completed_cycles == 4
    assert state.realized_profit == Decimal("16.0")
    assert tracker.completed_cycles == 4
    assert tracker.grid_profit == Decimal("16.0")
    assert state.filled_buy_count == 8
    assert state.filled_sell_count == 4
    assert state.current_position == Decimal("0.4")
    RuntimeSnapshotRepository.reset_instance()


@pytest.mark.asyncio
async def test_batch_orders_registered_in_pending_orders(grid_config):
    """測試批量下單成功後正確註冊至 _pending_orders 與 _pending_orders_by_client_id"""
    mock_adapter = MagicMock()
    mock_adapter.config.exchange_id = "binance"
    mock_adapter.config.exchange_type = MagicMock()
    mock_adapter.is_connected.return_value = True

    mock_rest = MagicMock(spec=BinanceRest)
    mock_adapter.rest = mock_rest

    class FakeBatchRes:
        def __init__(self, order_id, client_id):
            self.order_id = order_id
            self.client_id = client_id

    mock_rest.create_batch_orders.return_value = [
        FakeBatchRes("ex_1001", "cli_1001"),
        FakeBatchRes("ex_1002", "cli_1002"),
    ]

    engine = GridEngineImpl(mock_adapter)
    engine.config = grid_config

    orders = [
        GridOrder(
            order_id="",
            client_id="cli_1001",
            grid_id=1,
            side=GridOrderSide.BUY,
            price=Decimal("90"),
            amount=Decimal("0.1"),
            status=GridOrderStatus.PENDING,
            created_at=datetime.now(),
        ),
        GridOrder(
            order_id="",
            client_id="cli_1002",
            grid_id=2,
            side=GridOrderSide.SELL,
            price=Decimal("110"),
            amount=Decimal("0.1"),
            status=GridOrderStatus.PENDING,
            created_at=datetime.now(),
        ),
    ]

    placed = await engine._place_orders_via_batch_api(orders, current_price=Decimal("100"))
    assert len(placed) == 2

    pending = engine.get_pending_orders()
    assert len(pending) == 2
    assert "ex_1001" in engine._pending_orders
    assert "ex_1002" in engine._pending_orders
    assert "cli_1001" in engine._pending_orders_by_client_id
    assert "cli_1002" in engine._pending_orders_by_client_id
    assert engine._pending_orders["ex_1001"].order_id == "ex_1001"


@pytest.mark.asyncio
async def test_batch_result_mapping_survives_balance_precheck_skip(grid_config):
    """餘額預檢跳過第一張單時，回應不可錯位到錯的訂單（幽靈 order_id）。"""
    from unittest.mock import AsyncMock

    mock_adapter = MagicMock()
    mock_adapter.config.exchange_id = "binance"
    mock_adapter.is_connected.return_value = True
    mock_rest = MagicMock(spec=BinanceRest)
    mock_adapter.rest = mock_rest

    class FakeBatchRes:
        def __init__(self, order_id, client_id):
            self.order_id = order_id
            self.client_id = client_id

    # 只有第二張（SELL）真的送出，回應也只有一筆
    mock_rest.create_batch_orders.return_value = [FakeBatchRes("ex_sell_only", "cli_sell")]

    engine = GridEngineImpl(mock_adapter)
    engine.config = grid_config
    engine._get_orderbook_top = AsyncMock(return_value=None)
    engine._get_cached_balances = AsyncMock(return_value=None)
    engine._has_sufficient_balance = AsyncMock(side_effect=[False, True])

    buy = GridOrder(
        order_id="", client_id="cli_buy", grid_id=1, side=GridOrderSide.BUY,
        price=Decimal("90"), amount=Decimal("0.1"), status=GridOrderStatus.PENDING,
        created_at=datetime.now(),
    )
    sell = GridOrder(
        order_id="", client_id="cli_sell", grid_id=2, side=GridOrderSide.SELL,
        price=Decimal("110"), amount=Decimal("0.1"), status=GridOrderStatus.PENDING,
        created_at=datetime.now(),
    )

    await engine._place_orders_via_batch_api([buy, sell], current_price=Decimal("100"))

    assert sell.order_id == "ex_sell_only", "回應必須對位到真正送出的訂單"
    assert buy.order_id == ""
    assert buy.status == GridOrderStatus.FAILED


@pytest.mark.asyncio
async def test_on_order_update_handles_client_id_without_key_error(grid_config):
    """測試收到帶 client_id 的成交推播時不會因 _pending_orders KeyError 崩潰"""
    mock_adapter = MagicMock()
    mock_adapter.config.exchange_id = "binance"
    mock_adapter._map_symbol = lambda s: "SOLUSDC"
    engine = GridEngineImpl(mock_adapter)
    engine.config = grid_config

    order = GridOrder(
        order_id="13733689748",
        client_id="autobatch_grid_1",
        grid_id=1,
        side=GridOrderSide.BUY,
        price=Decimal("100"),
        amount=Decimal("0.1"),
        status=GridOrderStatus.PENDING,
        created_at=datetime.now(),
    )
    engine._pending_orders["13733689748"] = order
    engine._pending_orders_by_client_id["autobatch_grid_1"] = order

    callback_called = []
    def on_fill(o):
        callback_called.append(o)
    engine.subscribe_order_updates(on_fill)

    update_payload = {
        "s": "SOLUSDC",
        "i": "13733689748",
        "c": "autobatch_grid_1",
        "X": "FILLED",
        "p": "100.0",
        "z": "0.1",
        "e": "ORDER_TRADE_UPDATE",
    }

    await engine._on_order_update(update_payload)

    assert len(callback_called) == 1
    assert callback_called[0].status == GridOrderStatus.FILLED
    assert "13733689748" not in engine._pending_orders
    assert "autobatch_grid_1" not in engine._pending_orders_by_client_id


@pytest.mark.asyncio
async def test_binance_and_okx_subscribe_user_data_direct_call():
    """測試 Binance 與 OKX 適配器直接委託至 WebSocket subscribe_user_data"""
    from core.adapters.exchanges.adapters.binance import BinanceAdapter
    from core.adapters.exchanges.adapters.okx import OKXAdapter

    cfg = ExchangeConfig(
        name="binance",
        exchange_id="binance",
        exchange_type=ExchangeType.PERPETUAL,
        api_key="key",
        api_secret="secret",
    )
    binance_adapter = BinanceAdapter(cfg)
    binance_adapter._websocket = AsyncMock()
    binance_adapter._websocket.is_user_connected = False

    cb = MagicMock()
    await binance_adapter.subscribe_user_data(cb)
    binance_adapter._websocket.subscribe_user_data.assert_awaited_once_with(cb)

    okx_cfg = ExchangeConfig(
        name="okx",
        exchange_id="okx",
        exchange_type=ExchangeType.PERPETUAL,
        api_key="key",
        api_secret="secret",
    )
    okx_adapter = OKXAdapter(okx_cfg)
    okx_adapter._websocket = AsyncMock()
    okx_adapter._websocket.is_private_connected = False

    await okx_adapter.subscribe_user_data(cb)
    okx_adapter._websocket.subscribe_user_data.assert_awaited_once_with(cb)


@pytest.mark.asyncio
async def test_coordinator_start_initializes_engine_even_with_active_orders(grid_config):
    """測試 Coordinator.start() 在存在 active_orders 時仍會執行 engine.initialize()"""
    state = GridState()
    # 模擬已有 active_orders
    state.active_orders["dummy_1"] = GridOrder(
        order_id="dummy_1",
        grid_id=1,
        side=GridOrderSide.BUY,
        price=Decimal("90"),
        amount=Decimal("0.1"),
        status=GridOrderStatus.PENDING,
        created_at=datetime.now(),
    )
    tracker = PositionTrackerImpl(grid_config, state)
    strategy = GridStrategyImpl()

    mock_engine = MagicMock()
    mock_engine.initialize = AsyncMock()
    mock_engine.start = AsyncMock()
    mock_engine.subscribe_order_updates = MagicMock()

    coordinator = GridCoordinator(
        config=grid_config,
        strategy=strategy,
        engine=mock_engine,
        tracker=tracker,
        grid_state=state,
        config_path="config/grid/test-sol.yaml",
    )
    coordinator.balance_monitor = MagicMock()
    coordinator.balance_monitor.update_balance = AsyncMock()
    coordinator.recover_old_orders = AsyncMock()
    coordinator._run_monitoring_loop = AsyncMock()

    await coordinator.start()

    mock_engine.initialize.assert_awaited_once_with(grid_config)
    assert coordinator.state.status.value == "running"
