"""Paper adapter: public ticker in, simulated fills, no private exchange calls."""

import asyncio
import os
import sys
import types
import unittest
from decimal import Decimal

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

def _load_paper_module():
    """Load paper adapter without executing core/__init__.py (needs injector)."""
    import importlib.util

    def ensure_pkg(name, rel):
        if name in sys.modules:
            return sys.modules[name]
        module = types.ModuleType(name)
        module.__path__ = [os.path.join(ROOT, rel)]
        module.__package__ = name
        sys.modules[name] = module
        return module

    ensure_pkg("core", "core")
    ensure_pkg("core.adapters", "core/adapters")
    ensure_pkg("core.adapters.exchanges", "core/adapters/exchanges")
    ensure_pkg("core.adapters.exchanges.adapters", "core/adapters/exchanges/adapters")
    ensure_pkg("core.services", "core/services")
    ensure_pkg("core.services.events", "core/services/events")

    def load(name, rel):
        if name in sys.modules and getattr(sys.modules[name], "__file__", None):
            return sys.modules[name]
        spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    # interface imports Event from core.services.events
    if "core.services.events" not in sys.modules or not getattr(sys.modules["core.services.events"], "Event", None):
        events = types.ModuleType("core.services.events")
        events.Event = object
        sys.modules["core.services.events"] = events
        sys.modules["core.services.events.event"] = events

    load("core.adapters.exchanges.models", "core/adapters/exchanges/models.py")
    load("core.adapters.exchanges.interface", "core/adapters/exchanges/interface.py")
    return load("core.adapters.exchanges.adapters.paper", "core/adapters/exchanges/adapters/paper.py")


class PaperAdapterTests(unittest.TestCase):
    def test_limit_buy_fills_when_price_crosses_and_never_needs_keys(self):
        paper = _load_paper_module()
        create_paper_adapter = paper.create_paper_adapter
        from core.adapters.exchanges.models import OrderSide, OrderStatus, OrderType

        prices = {"BTC/USDT": Decimal("100")}
        adapter = create_paper_adapter(
            {"grid_system": {"exchange": "binance", "symbol": "BTC/USDT", "account_mode": "paper"}},
            ticker_provider=lambda symbol: prices[symbol],
        )
        asyncio.run(adapter.connect())
        order = asyncio.run(adapter.create_order(
            "BTC/USDT", OrderSide.BUY, OrderType.LIMIT, Decimal("2"), Decimal("90"),
        ))
        self.assertEqual(order.status, OrderStatus.OPEN)
        self.assertEqual(len(asyncio.run(adapter.get_open_orders("BTC/USDT"))), 1)

        prices["BTC/USDT"] = Decimal("89")
        filled = adapter.set_last_price("BTC/USDT", Decimal("89"))
        self.assertEqual(len(filled), 1)
        self.assertEqual(filled[0].status, OrderStatus.FILLED)
        self.assertEqual(asyncio.run(adapter.get_open_orders("BTC/USDT")), [])
        positions = asyncio.run(adapter.get_positions(["BTC/USDT"]))
        self.assertEqual(positions[0].size, Decimal("2"))
        asyncio.run(adapter.disconnect())

    def test_market_sell_reduces_position_with_decimal_pnl(self):
        paper = _load_paper_module()
        create_paper_adapter = paper.create_paper_adapter
        from core.adapters.exchanges.models import OrderSide, OrderStatus, OrderType

        adapter = create_paper_adapter(
            {"grid_system": {"exchange": "binance", "symbol": "ETH/USDT", "account_mode": "paper"}},
            ticker_provider=lambda _symbol: Decimal("2000"),
        )
        asyncio.run(adapter.connect())
        buy = asyncio.run(adapter.create_order(
            "ETH/USDT", OrderSide.BUY, OrderType.MARKET, Decimal("1"), None,
        ))
        self.assertEqual(buy.status, OrderStatus.FILLED)
        adapter.set_last_price("ETH/USDT", Decimal("2100"))
        sell = asyncio.run(adapter.create_order(
            "ETH/USDT", OrderSide.SELL, OrderType.MARKET, Decimal("1"), None,
        ))
        self.assertEqual(sell.status, OrderStatus.FILLED)
        self.assertGreater(adapter._realized, Decimal("0"))
        self.assertEqual(adapter._position_size, Decimal("0"))
        asyncio.run(adapter.disconnect())

    def test_is_paper_config(self):
        paper = _load_paper_module()
        is_paper_config = paper.is_paper_config

        self.assertTrue(is_paper_config({"grid_system": {"account_mode": "paper"}}))
        self.assertFalse(is_paper_config({"grid_system": {"account_mode": "live"}}))
        self.assertFalse(is_paper_config({"grid_system": {}}))


class AccountModeHelperTests(unittest.TestCase):
    def test_reads_account_mode_from_yaml_helper(self):
        backend = os.path.join(ROOT, "web_ui", "backend")
        if backend not in sys.path:
            sys.path.insert(0, backend)
        from web_ui.backend.lifecycle import account_mode_from_yaml

        self.assertEqual(account_mode_from_yaml({"grid_system": {"account_mode": "paper"}}), "paper")
        self.assertEqual(account_mode_from_yaml({"grid_system": {"account_mode": "live"}}), "live")
        self.assertEqual(account_mode_from_yaml({"grid_system": {}}), "unknown")


if __name__ == "__main__":
    unittest.main()
