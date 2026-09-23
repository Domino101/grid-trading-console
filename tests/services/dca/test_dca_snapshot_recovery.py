"""
Unit tests for DCA Coordinator snapshot recovery and open order takeover.
"""

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from types import SimpleNamespace

from core.services.dca.coordinator import DcaCoordinator
from core.services.dca.models import (
    DcaConfig,
    DcaDirection,
    DcaOrder,
    DcaOrderRole,
    DcaOrderStatus,
    DcaStatus,
    bot_id_hash8,
)
from web_ui.backend.runtime_repository import RuntimeSnapshotRepository


class DummyExecutor:
    def __init__(self, bot_id="test-dca-bot", symbol="ETH/USDC:USDC", position_qty=Decimal("0")):
        self._bot_id = bot_id
        self._symbol = symbol
        self.fetch_open_orders = AsyncMock(return_value=[])
        self.fetch_order = AsyncMock(return_value=None)
        self.last_price = AsyncMock(return_value=Decimal("2500"))
        pos_list = []
        if position_qty > 0:
            pos_list.append(SimpleNamespace(
                symbol=symbol,
                amount=position_qty,
                size=position_qty,
                side=SimpleNamespace(value="long"),
                entry_price=Decimal("2450"),
            ))
        self._exchange = SimpleNamespace(get_positions=AsyncMock(return_value=pos_list))


@pytest.fixture
def dca_config():
    return DcaConfig(
        exchange="binance",
        symbol="ETH/USDC:USDC",
        base_order_amount=Decimal("10"),
        direction=DcaDirection.BOTH,
        max_safety_orders=5,
    )


@pytest.mark.asyncio
async def test_dca_recover_state_from_snapshot_restores_cycles_and_profit(dca_config, monkeypatch):
    bot_id = "test-dca-recovery-bot"
    executor = DummyExecutor(bot_id=bot_id, position_qty=Decimal("0.5"))
    coordinator = DcaCoordinator(dca_config, bot_id, executor)

    snapshot_data = {
        "bot_id": bot_id,
        "symbol": "ETH/USDC:USDC",
        "status": "averaging",
        "active_direction": "long",
        "cycles": 5,
        "profit": {
            "realized": 123.45,
            "fees": 1.5,
        },
        "position": {
            "quantity": 0.5,
            "average_cost": 2400.0,
            "total_cost": 1200.0,
        },
        "entry_price": 2450.0,
        "fills": [
            {"client_order_id": "dca_12345678_long_base", "role": "base", "price": 2450.0, "quantity": 0.2},
            {"client_order_id": "dca_12345678_long_so01", "role": "safety", "price": 2350.0, "quantity": 0.3},
        ],
    }

    mock_repo = MagicMock()
    mock_repo.load_all.return_value = {
        f"config/dca/{bot_id}.yaml": (snapshot_data, 1788111000.0)
    }
    monkeypatch.setattr(RuntimeSnapshotRepository, "instance", classmethod(lambda cls: mock_repo))

    recovered = await coordinator.recover_state_from_snapshot(f"config/dca/{bot_id}.yaml")

    assert recovered is True
    assert coordinator.state.cycle_count == 5
    assert coordinator._realized_profit == Decimal("123.45")
    assert coordinator._total_fees == Decimal("1.5")
    assert len(coordinator._fills) == 2
    assert coordinator.state.position_quantity == Decimal("0.5")
    assert coordinator.state.total_cost == Decimal("1200.0")
    assert coordinator.state.entry_price == Decimal("2450.0")
    assert coordinator.state.active_direction == "long"
    assert coordinator.state.status == DcaStatus.AVERAGING


@pytest.mark.asyncio
async def test_dca_reconcile_startup_orders_takes_over_open_orders(dca_config):
    bot_id = "test-dca-orders-bot"
    executor = DummyExecutor(bot_id=bot_id)
    my_hash = bot_id_hash8(bot_id)

    base_order = DcaOrder(
        client_order_id=f"dca_{my_hash}_long_base_0001",
        symbol="ETH/USDC:USDC",
        side="buy",
        role=DcaOrderRole.BASE,
        price=Decimal("2450"),
        quantity=Decimal("0.1"),
        status=DcaOrderStatus.PLACED,
    )
    so_order = DcaOrder(
        client_order_id=f"dca_{my_hash}_long_so01_0002",
        symbol="ETH/USDC:USDC",
        side="buy",
        role=DcaOrderRole.SAFETY,
        price=Decimal("2400"),
        quantity=Decimal("0.1"),
        status=DcaOrderStatus.PLACED,
    )
    tp_order = DcaOrder(
        client_order_id=f"dca_{my_hash}_long_tp_0003",
        symbol="ETH/USDC:USDC",
        side="sell",
        role=DcaOrderRole.TAKE_PROFIT,
        price=Decimal("2500"),
        quantity=Decimal("0.1"),
        status=DcaOrderStatus.PLACED,
    )

    executor.fetch_open_orders.return_value = [base_order, so_order, tp_order]
    coordinator = DcaCoordinator(dca_config, bot_id, executor)

    taken = await coordinator.reconcile_startup_orders()

    assert taken == 3
    assert f"dca_{my_hash}_long_base_0001" in coordinator._active
    assert coordinator._base_order == base_order
    assert coordinator._tp_client_id == f"dca_{my_hash}_long_tp_0003"
    assert coordinator._active[f"dca_{my_hash}_long_so01_0002"].slot_index == 1
