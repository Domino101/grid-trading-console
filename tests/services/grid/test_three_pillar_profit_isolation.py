"""
Unit tests for 3-pillar grid profit metrics and manual intervention isolation.
"""

from decimal import Decimal
from datetime import datetime
import pytest

from core.services.grid.models import GridConfig, GridType, GridState, GridOrder, GridOrderStatus, GridOrderSide
from core.services.grid.implementations import PositionTrackerImpl


def test_three_pillar_profit_and_virtual_position_isolation():
    config = GridConfig(
        exchange="binance",
        symbol="ETH/USDC:USDC",
        grid_type=GridType.NEUTRAL,
        lower_price=Decimal("1800"),
        upper_price=Decimal("2000"),
        grid_interval=Decimal("10"),
        order_amount=Decimal("0.01"),
        fee_rate=Decimal("0.0005")
    )
    state = GridState()
    state.update_current_price(Decimal("1900"), 10)

    tracker = PositionTrackerImpl(config, state)

    # 1. 模擬交易所全帳戶有手動加倉 (+0.800 ETH)
    tracker.sync_initial_position(Decimal("0.800"), Decimal("1850"))
    assert tracker.account_position == Decimal("0.800")
    # 網格自身的虛擬持倉應仍然為 0
    assert tracker.virtual_position == Decimal("0")
    assert tracker.calculate_unrealized_pnl(Decimal("1900")) == Decimal("0")

    # 2. 網格產生 2 筆買單成交 (@1890, @1880)
    now = datetime.now()
    buy1 = GridOrder(
        order_id="grid_buy_1",
        grid_id=9,
        side=GridOrderSide.BUY,
        price=Decimal("1890"),
        amount=Decimal("0.01"),
        filled_price=Decimal("1890"),
        filled_amount=Decimal("0.01"),
        status=GridOrderStatus.FILLED,
        created_at=now,
        filled_at=now
    )
    tracker.record_filled_order(buy1)

    buy2 = GridOrder(
        order_id="grid_buy_2",
        grid_id=8,
        side=GridOrderSide.BUY,
        price=Decimal("1880"),
        amount=Decimal("0.01"),
        filled_price=Decimal("1880"),
        filled_amount=Decimal("0.01"),
        status=GridOrderStatus.FILLED,
        created_at=now,
        filled_at=now
    )
    tracker.record_filled_order(buy2)

    # 虛擬持倉應為 +0.02 ETH，均價為 1885
    assert tracker.virtual_position == Decimal("0.02")
    assert tracker.virtual_average_cost == Decimal("1885")
    # 未實現浮動盈虧 (現價 1900): 0.02 * (1900 - 1885) = +0.30 USDC
    assert tracker.calculate_unrealized_pnl(Decimal("1900")) == Decimal("0.30")

    # 3. 網格產生 1 筆賣單成交 (@1900)
    sell1 = GridOrder(
        order_id="grid_sell_1",
        grid_id=10,
        side=GridOrderSide.SELL,
        price=Decimal("1900"),
        amount=Decimal("0.01"),
        filled_price=Decimal("1900"),
        filled_amount=Decimal("0.01"),
        status=GridOrderStatus.FILLED,
        created_at=now,
        filled_at=now
    )
    tracker.record_filled_order(sell1)

    # 完成循環 1 次
    assert tracker.completed_cycles == 1
    # 網格套利利潤 (毛利) = 1 * 10 * 0.01 = 0.10 USDC
    assert tracker.grid_profit == Decimal("0.10")
    # 剩餘虛擬持倉 = +0.01 ETH，均價 1885
    assert tracker.virtual_position == Decimal("0.01")
    assert tracker.virtual_average_cost == Decimal("1885")
    # 未實現浮動盈虧 (現價 1900): 0.01 * (1900 - 1885) = +0.15 USDC
    assert tracker.calculate_unrealized_pnl(Decimal("1900")) == Decimal("0.15")

    # 統計資料檢驗
    stats = tracker.get_statistics()
    assert stats.grid_profit == Decimal("0.10")
    assert stats.grid_position == Decimal("0.01")
    assert stats.unrealized_profit == Decimal("0.15")
    assert stats.account_position == Decimal("0.800")
    # 手續費: (1890*0.01 + 1880*0.01 + 1900*0.01) * 0.0005 = 56.70 * 0.0005 = 0.02835
    expected_fees = Decimal("56.70") * Decimal("0.0005")
    assert stats.total_fees == expected_fees
    # 淨收益 = 0.10 + 0.15 - 0.02835 = 0.22165
    assert stats.net_profit == Decimal("0.10") + Decimal("0.15") - expected_fees

    display_dict = stats.to_display_dict()
    assert display_dict["grid_position"] == 0.01
    assert display_dict["account_position"] == 0.8
    assert display_dict["profit"]["grid_profit"] == 0.1
    assert display_dict["profit"]["unrealized"] == 0.15


def test_zero_maker_fee_resolution_and_calculation():
    """驗證零手續費交易對（如幣安 USDC 合約 0 掛單費）費率解析與 0 手續費計算"""
    config = GridConfig(
        exchange="binance",
        symbol="ETH/USDC:USDC",
        grid_type=GridType.NEUTRAL,
        lower_price=Decimal("1800"),
        upper_price=Decimal("2000"),
        grid_interval=Decimal("10"),
        order_amount=Decimal("0.01"),
        fee_rate=Decimal("0.0")  # 明確指定 0 手續費
    )
    state = GridState()
    state.update_current_price(Decimal("1900"), 10)

    tracker = PositionTrackerImpl(config, state)
    assert tracker.resolve_fee_rate() == Decimal("0.0")

    now = datetime.now()
    buy = GridOrder(
        order_id="grid_buy_zero_fee",
        grid_id=9,
        side=GridOrderSide.BUY,
        price=Decimal("1890"),
        amount=Decimal("0.01"),
        filled_price=Decimal("1890"),
        filled_amount=Decimal("0.01"),
        status=GridOrderStatus.FILLED,
        created_at=now,
        filled_at=now
    )
    tracker.record_filled_order(buy)

    sell = GridOrder(
        order_id="grid_sell_zero_fee",
        grid_id=10,
        side=GridOrderSide.SELL,
        price=Decimal("1900"),
        amount=Decimal("0.01"),
        filled_price=Decimal("1900"),
        filled_amount=Decimal("0.01"),
        status=GridOrderStatus.FILLED,
        created_at=now,
        filled_at=now
    )
    tracker.record_filled_order(sell)

    assert tracker.completed_cycles == 1
    assert tracker.grid_profit == Decimal("0.10")
    assert tracker.total_fees == Decimal("0.0")
    stats = tracker.get_statistics()
    assert stats.total_fees == Decimal("0.0")
    assert stats.net_profit == Decimal("0.10")

