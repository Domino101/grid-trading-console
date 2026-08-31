import asyncio
import pytest
from decimal import Decimal
from unittest.mock import MagicMock, AsyncMock

from core.services.grid.models.grid_config import GridConfig, GridType
from core.services.grid.implementations.grid_engine_impl import GridEngineImpl
from core.adapters.exchanges.adapters.paper import create_paper_adapter
from core.adapters.exchanges.interface import ExchangeConfig
from core.adapters.exchanges.models import ExchangeType, TickerData


@pytest.mark.asyncio
async def test_grid_engine_paper_exchange_id_and_active_window():
    config_data = {
        "grid_system": {
            "exchange": "binance",
            "symbol": "PROM/USDT",
            "grid_type": "neutral",
            "price_range": {
                "lower_price": 2.0,
                "upper_price": 8.0,
            },
            "grid_interval": 0.1,
            "order_amount": 1.0,
            "quantity_precision": 2,
            "price_decimals": 2,
            "account_mode": "paper",
            "active_window_each_side": 2,
            "active_window_refresh_interval": 60,
        }
    }
    adapter = create_paper_adapter(config_data)
    grid_config = GridConfig(
        exchange="binance",
        symbol="PROM/USDT",
        grid_type=GridType.NEUTRAL,
        lower_price=Decimal("2.0"),
        upper_price=Decimal("8.0"),
        grid_interval=Decimal("0.1"),
        order_amount=Decimal("1.0"),
        active_window_each_side=2,
        active_window_refresh_interval=60,
    )
    engine = GridEngineImpl(adapter)
    await engine.initialize(grid_config)

    # Test _get_exchange_id
    assert engine._get_exchange_id() == "binance"

    # Set mock current price between grids (e.g. 5.05)
    engine.get_current_price = AsyncMock(return_value=Decimal("5.05"))

    # Refresh active window once manually
    await engine._refresh_active_window_once(window_each_side=2)

    # Verify orders were placed in paper adapter
    open_orders = await adapter.get_open_orders("PROM/USDT")
    assert len(open_orders) == 4
    buy_orders = [o for o in open_orders if str(getattr(o.side, 'value', o.side)) == "buy"]
    sell_orders = [o for o in open_orders if str(getattr(o.side, 'value', o.side)) == "sell"]
    assert len(buy_orders) == 2
    assert len(sell_orders) == 2

    # Clean up
    await engine.stop()
    await adapter.disconnect()
