"""
Unit tests for FIFO arbitrage trade matching and active window nearest order protection.
"""

import asyncio
from datetime import datetime
from decimal import Decimal
import pytest
from unittest.mock import AsyncMock, MagicMock

from core.services.grid.models import (
    GridConfig, GridType, GridState, GridOrder, GridOrderStatus, GridOrderSide
)
from core.services.grid.implementations import PositionTrackerImpl, GridEngineImpl
from core.services.grid.coordinator.grid_coordinator import GridCoordinator


def test_fifo_arbitrage_matching_multi_lots():
    """驗證多筆買單以 FIFO 順序撮合，計算真實價差毛利而非粗暴估算。"""
    config = GridConfig(
        exchange="binance",
        symbol="BTC/USDC:USDC",
        grid_type=GridType.NEUTRAL,
        lower_price=Decimal("90000"),
        upper_price=Decimal("100000"),
        grid_interval=Decimal("1000"),
        order_amount=Decimal("0.01"),
        fee_rate=Decimal("0.0002")
    )
    state = GridState()
    tracker = PositionTrackerImpl(config, state)

    now = datetime.now()

    # 1. 買入第 1 筆 @94000 數量 0.01
    buy1 = GridOrder(
        order_id="buy_1",
        grid_id=5,
        side=GridOrderSide.BUY,
        price=Decimal("94000"),
        amount=Decimal("0.01"),
        filled_price=Decimal("94000"),
        filled_amount=Decimal("0.01"),
        status=GridOrderStatus.FILLED,
        created_at=now,
        filled_at=now
    )
    tracker.record_filled_order(buy1)

    # 2. 買入第 2 筆 @93000 數量 0.01
    buy2 = GridOrder(
        order_id="buy_2",
        grid_id=4,
        side=GridOrderSide.BUY,
        price=Decimal("93000"),
        amount=Decimal("0.01"),
        filled_price=Decimal("93000"),
        filled_amount=Decimal("0.01"),
        status=GridOrderStatus.FILLED,
        created_at=now,
        filled_at=now
    )
    tracker.record_filled_order(buy2)

    # 尚未有賣單，完成套利輪迴應為 0，毛利為 0
    assert tracker.buy_count == 2
    assert tracker.sell_count == 0
    assert tracker.completed_cycles == 0
    assert tracker.grid_profit == Decimal("0")
    assert tracker.virtual_position == Decimal("0.02")

    # 3. 賣出 1 筆 @95000 數量 0.01
    # 按照 FIFO 原則，撮合最先買入的第 1 筆 (@94000)
    # 真實獲利 = (95000 - 94000) * 0.01 = 10.0 USDC
    sell1 = GridOrder(
        order_id="sell_1",
        grid_id=6,
        side=GridOrderSide.SELL,
        price=Decimal("95000"),
        amount=Decimal("0.01"),
        filled_price=Decimal("95000"),
        filled_amount=Decimal("0.01"),
        status=GridOrderStatus.FILLED,
        created_at=now,
        filled_at=now
    )
    tracker.record_filled_order(sell1)

    assert tracker.completed_cycles == 1
    assert tracker.grid_profit == Decimal("10.0")
    assert tracker.realized_pnl == Decimal("10.0")
    assert tracker.virtual_position == Decimal("0.01")  # 剩餘 buy2 (@93000)

    # 4. 賣出第 2 筆 @96000 數量 0.01
    # 撮合剩餘的第 2 筆 (@93000)
    # 真實獲利 = (96000 - 93000) * 0.01 = 30.0 USDC
    # 累計套利毛利 = 10.0 + 30.0 = 40.0 USDC
    sell2 = GridOrder(
        order_id="sell_2",
        grid_id=7,
        side=GridOrderSide.SELL,
        price=Decimal("96000"),
        amount=Decimal("0.01"),
        filled_price=Decimal("96000"),
        filled_amount=Decimal("0.01"),
        status=GridOrderStatus.FILLED,
        created_at=now,
        filled_at=now
    )
    tracker.record_filled_order(sell2)

    assert tracker.completed_cycles == 2
    assert tracker.grid_profit == Decimal("40.0")
    assert tracker.virtual_position == Decimal("0")

    # 檢查交易歷史與手續費紀錄
    history = tracker.get_trade_history(10)
    assert len(history) == 4
    # sell1 獲利 10.0
    assert history[2]["profit"] == 10.0
    # sell2 獲利 30.0
    assert history[3]["profit"] == 30.0


@pytest.mark.asyncio
async def test_active_window_s1_nearest_order_protection():
    """驗證價格接近 S1 時，S1 賣單在目標窗口內，且不會被撤單。"""
    GridEngineImpl._shared_api_circuit_until = 0.0
    config = GridConfig(
        exchange="binance",
        symbol="BTC/USDC:USDC",
        grid_type=GridType.NEUTRAL,
        lower_price=Decimal("90000"),
        upper_price=Decimal("100000"),
        grid_interval=Decimal("1000"),
        order_amount=Decimal("0.01"),
        active_window_each_side=2,
        active_window_refresh_interval=90,
    )

    mock_adapter = MagicMock()
    mock_adapter.config.exchange_type.name = "FUTURE"
    mock_adapter.config.exchange_id = "binance"
    mock_adapter.cancel_order = AsyncMock()

    # 模擬當前交易所掛單：
    # S1 @96000 (Grid 7), S2 @97000 (Grid 8)
    # B1 @94000 (Grid 5), B2 @93000 (Grid 4)
    s1_order = MagicMock()
    s1_order.id = "s1_96k"
    s1_order.grid_id = 7
    s1_order.price = Decimal("96000")
    s1_order.side = GridOrderSide.SELL

    b1_order = MagicMock()
    b1_order.id = "b1_94k"
    b1_order.grid_id = 5
    b1_order.price = Decimal("94000")
    b1_order.side = GridOrderSide.BUY

    mock_adapter.get_open_orders = AsyncMock(return_value=[s1_order, b1_order])

    engine = GridEngineImpl(mock_adapter)
    engine.config = config

    # 1. 價格為 94500 (介於 94000 與 95000 之間)
    engine.get_current_price = AsyncMock(return_value=Decimal("94500"))
    engine.place_batch_orders = AsyncMock(return_value=[])

    await engine._refresh_active_window_once(window_each_side=2)

    # S1 (@96000) 與 B1 (@94000) 絕對不能被取消
    mock_adapter.cancel_order.assert_not_called()

    # 2. 價格劇烈上漲至 95900 (極度接近 S1 96000，過半格)
    engine.get_current_price = AsyncMock(return_value=Decimal("95900"))
    # 此時現價 95900 離 S1 (96000) 僅差 100 (< 1000 格距)
    await engine._refresh_active_window_once(window_each_side=2)

    # 再次確認 S1 (@96000) 絕對沒有被撤單
    mock_adapter.cancel_order.assert_not_called()


def test_fifo_arbitrage_restart_recovery():
    """驗證重啟並從持久化快照恢復後，第一筆反向成交單能無縫進行 FIFO 撮合並計算真實毛利。"""
    config = GridConfig(
        exchange="binance",
        symbol="BTC/USDC:USDC",
        grid_type=GridType.NEUTRAL,
        lower_price=Decimal("90000"),
        upper_price=Decimal("100000"),
        grid_interval=Decimal("1000"),
        order_amount=Decimal("0.01"),
    )
    state = GridState()
    tracker = PositionTrackerImpl(config, state)

    # 模擬從持久化快照恢復：已成交 5 輪、毛利 50、持倉 +0.02 BTC (成本 94500)
    tracker.completed_cycles = 5
    tracker.grid_profit = Decimal("50.0")
    tracker.realized_pnl = Decimal("50.0")
    tracker.sync_recovered_position(Decimal("0.02"), Decimal("94500"))

    now = datetime.now()
    # 重啟後第一筆賣單成交 @96000 數量 0.01
    sell_order = GridOrder(
        order_id="sell_post_restart",
        grid_id=7,
        side=GridOrderSide.SELL,
        price=Decimal("96000"),
        amount=Decimal("0.01"),
        filled_price=Decimal("96000"),
        filled_amount=Decimal("0.01"),
        status=GridOrderStatus.FILLED,
        created_at=now,
        filled_at=now
    )
    tracker.record_filled_order(sell_order)

    # 撮合成本 94500 的底倉，獲利 = (96000 - 94500) * 0.01 = 15.0
    # 累計毛利 = 50.0 + 15.0 = 65.0
    # 完成輪數 = 5 + 1 = 6
    # 剩餘持倉 = 0.01
    assert tracker.completed_cycles == 6
    assert tracker.grid_profit == Decimal("65.0")
    assert tracker.virtual_position == Decimal("0.01")


def test_fifo_arbitrage_short_grid():
    """驗證做空網格先賣後買以 FIFO 正確撮合獲利。"""
    config = GridConfig(
        exchange="binance",
        symbol="BTC/USDC:USDC",
        grid_type=GridType.SHORT,
        lower_price=Decimal("90000"),
        upper_price=Decimal("100000"),
        grid_interval=Decimal("1000"),
        order_amount=Decimal("0.01"),
    )
    state = GridState()
    tracker = PositionTrackerImpl(config, state)

    now = datetime.now()
    # 1. 先賣出 2 筆: @98000 (0.01), @97000 (0.01)
    sell1 = GridOrder(
        order_id="short_1", grid_id=8, side=GridOrderSide.SELL,
        price=Decimal("98000"), amount=Decimal("0.01"),
        filled_price=Decimal("98000"), filled_amount=Decimal("0.01"),
        status=GridOrderStatus.FILLED, created_at=now, filled_at=now
    )
    sell2 = GridOrder(
        order_id="short_2", grid_id=7, side=GridOrderSide.SELL,
        price=Decimal("97000"), amount=Decimal("0.01"),
        filled_price=Decimal("97000"), filled_amount=Decimal("0.01"),
        status=GridOrderStatus.FILLED, created_at=now, filled_at=now
    )
    tracker.record_filled_order(sell1)
    tracker.record_filled_order(sell2)

    assert tracker.virtual_position == Decimal("-0.02")
    assert tracker.completed_cycles == 0

    # 2. 買入平倉 1 筆 @95000 數量 0.01
    # 撮合最先賣出的 short_1 (@98000)
    # 做空獲利 = (98000 - 95000) * 0.01 = 30.0
    buy1 = GridOrder(
        order_id="cover_1", grid_id=5, side=GridOrderSide.BUY,
        price=Decimal("95000"), amount=Decimal("0.01"),
        filled_price=Decimal("95000"), filled_amount=Decimal("0.01"),
        status=GridOrderStatus.FILLED, created_at=now, filled_at=now
    )
    tracker.record_filled_order(buy1)

    assert tracker.completed_cycles == 1
    assert tracker.grid_profit == Decimal("30.0")
    assert tracker.virtual_position == Decimal("-0.01")


@pytest.mark.asyncio
async def test_active_window_pullback_and_inversion_cleanup():
    """驗證價格回落時，現價上方的殘留買單會被正確取消並替換為賣單，絕不在現價上方殘留倒掛買單或重複掛單。"""
    GridEngineImpl._shared_api_circuit_until = 0.0
    config = GridConfig(
        exchange="binance",
        symbol="BTC/USDC:USDC",
        grid_type=GridType.NEUTRAL,
        lower_price=Decimal("90000"),
        upper_price=Decimal("100000"),
        grid_interval=Decimal("1000"),
        order_amount=Decimal("0.01"),
        active_window_each_side=2,
    )
    mock_adapter = MagicMock()
    mock_adapter.config.exchange_type.name = "FUTURE"
    mock_adapter.config.exchange_id = "binance"
    mock_adapter.cancel_order = AsyncMock()

    # 模擬場景：先前價格在 95200 時在 Grid 6 (@95000) 掛了買單
    # 現在價格回落至 94800 (< 95000)
    inverted_buy = MagicMock()
    inverted_buy.id = "buy_95k"
    inverted_buy.grid_id = 6
    inverted_buy.price = Decimal("95000")
    inverted_buy.side = GridOrderSide.BUY

    valid_buy = MagicMock()
    valid_buy.id = "buy_94k"
    valid_buy.grid_id = 5
    valid_buy.price = Decimal("94000")
    valid_buy.side = GridOrderSide.BUY

    mock_adapter.get_open_orders = AsyncMock(return_value=[inverted_buy, valid_buy])

    engine = GridEngineImpl(mock_adapter)
    engine.config = config
    engine.get_current_price = AsyncMock(return_value=Decimal("94800"))
    engine.place_batch_orders = AsyncMock(return_value=[])

    await engine._refresh_active_window_once(window_each_side=2)

    # 1. 驗證倒掛買單 buy_95k 被取消 (因為 95000 > 94800，現價上方應為賣單，不可留買單)
    mock_adapter.cancel_order.assert_any_call("buy_95k", "BTC/USDC:USDC")

    # 2. 驗證新補的訂單包含 Grid 6 (@95000) 的 SELL 單
    placed_orders = engine.place_batch_orders.call_args[0][0]
    grid_6_placed = [o for o in placed_orders if o.grid_id == 6]
    assert len(grid_6_placed) == 1
    assert grid_6_placed[0].side == GridOrderSide.SELL
    assert grid_6_placed[0].price == Decimal("95000")


@pytest.mark.asyncio
async def test_short_grid_active_window_symmetry():
    """驗證做空網格活躍窗口完全對稱：現價上方掛賣單、現價下方掛買單。"""
    config = GridConfig(
        exchange="binance",
        symbol="BTC/USDC:USDC",
        grid_type=GridType.SHORT,
        lower_price=Decimal("90000"),
        upper_price=Decimal("100000"),
        grid_interval=Decimal("1000"),
        order_amount=Decimal("0.01"),
        active_window_each_side=2,
    )
    mock_adapter = MagicMock()
    mock_adapter.config.exchange_type.name = "FUTURE"
    mock_adapter.config.exchange_id = "binance"
    mock_adapter.get_open_orders = AsyncMock(return_value=[])
    mock_adapter.cancel_order = AsyncMock()

    engine = GridEngineImpl(mock_adapter)
    engine.config = config
    # 現價 94800 (介於 94000 與 95000 之間)
    engine.get_current_price = AsyncMock(return_value=Decimal("94800"))
    engine.place_batch_orders = AsyncMock(return_value=[])

    await engine._refresh_active_window_once(window_each_side=2)

    placed_orders = engine.place_batch_orders.call_args[0][0]
    # 現價 94800 上方 (95000, 96000) 必須為 SELL
    sells = [o for o in placed_orders if o.side == GridOrderSide.SELL]
    # 現價 94800 下方 (94000, 93000) 必須為 BUY
    buys = [o for o in placed_orders if o.side == GridOrderSide.BUY]

    assert len(sells) == 2
    assert all(o.price > Decimal("94800") for o in sells)
    assert len(buys) == 2
    assert all(o.price < Decimal("94800") for o in buys)


def test_negative_profit_sell_below_cost_does_not_increment_completed_cycles():
    """驗證持倉下方賣單（虧損賣出）時，completed_cycles 不會被錯誤遞增，且真實記錄虧損與 loss_cycles。"""
    config = GridConfig(
        exchange="binance",
        symbol="BTC/USDC:USDC",
        grid_type=GridType.NEUTRAL,
        lower_price=Decimal("90000"),
        upper_price=Decimal("100000"),
        grid_interval=Decimal("1000"),
        order_amount=Decimal("0.01"),
        fee_rate=Decimal("0.0")
    )
    state = GridState()
    tracker = PositionTrackerImpl(config, state)
    now = datetime.now()

    # 1. 買入 @100000 數量 0.01
    buy = GridOrder(
        order_id="buy_high",
        grid_id=10,
        side=GridOrderSide.BUY,
        price=Decimal("100000"),
        amount=Decimal("0.01"),
        filled_price=Decimal("100000"),
        filled_amount=Decimal("0.01"),
        status=GridOrderStatus.FILLED,
        created_at=now,
        filled_at=now
    )
    tracker.record_filled_order(buy)

    # 2. 在持倉下方賣出 @95000 數量 0.01 (虧損 50 USDC)
    sell_low = GridOrder(
        order_id="sell_low",
        grid_id=5,
        side=GridOrderSide.SELL,
        price=Decimal("95000"),
        amount=Decimal("0.01"),
        filled_price=Decimal("95000"),
        filled_amount=Decimal("0.01"),
        status=GridOrderStatus.FILLED,
        created_at=now,
        filled_at=now
    )
    tracker.record_filled_order(sell_low)

    # 驗證：虧損賣出不增加 completed_cycles，但增加 loss_cycles
    assert tracker.completed_cycles == 0
    assert tracker.loss_cycles == 1
    assert tracker.grid_profit == Decimal("-50.0")
    assert tracker.realized_pnl == Decimal("-50.0")

    metrics = tracker.get_metrics()
    assert metrics.win_trades == 0
    assert metrics.loss_trades == 1
    assert metrics.win_rate == Decimal("0")


def test_fallback_profit_inflation_is_eliminated():
    """驗證移除粗暴理論估算公式後，get_statistics 不會因 cycles 虛構正利潤。"""
    config = GridConfig(
        exchange="binance",
        symbol="BTC/USDC:USDC",
        grid_type=GridType.NEUTRAL,
        lower_price=Decimal("90000"),
        upper_price=Decimal("100000"),
        grid_interval=Decimal("1000"),
        order_amount=Decimal("0.01"),
        fee_rate=Decimal("0.0")
    )
    state = GridState()
    tracker = PositionTrackerImpl(config, state)

    # 人為設定 completed_cycles = 10，但真實 grid_profit = 0
    tracker.completed_cycles = 10
    tracker.grid_profit = Decimal("0")

    stats = tracker.get_statistics()
    # 絕不自動捏造 10 * 1000 * 0.01 = 100.0 的虛擬正利潤
    assert stats.realized_profit == Decimal("0")
    assert stats.grid_profit == Decimal("0")


