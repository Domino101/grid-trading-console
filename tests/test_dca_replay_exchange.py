"""Unit tests for Phase C2: ReplayExchange and matching rules R-1 through R-13.

Tests all C2 audit items (2.1 through 2.13):
- 2.1 R-1: No lookahead bias (placed at bar i, matched at bar j > i)
- 2.2 R-2: Buy limit matching vector (low <= price)
- 2.3 R-3: Sell limit matching vector (high >= price)
- 2.4 Gap opening fill prices (min(open, price) for buy, max(open, price) for sell)
- 2.5 R-4: Conservative multi-order sequence in same bar (unfavorable safety buy before TP sell)
- 2.6 R-4: Same-type sorting (safety buys sorted high-to-low)
- 2.7 R-5: Market orders executed at bar open (slippage model)
- 2.8 Duck-typing contract compatibility with DcaOrderExecutor
- 2.9 Cancel order semantics
- 2.10 Zero I/O compliance
- 2.11 Decimal purity compliance
- 2.12 R-6 Whole amount filled (no partial fills) & docstring notice
- 2.13 Grid regression prevention
"""

import asyncio
import os
import sys
import unittest
from decimal import Decimal

_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from core.services.dca.backtest.candles import Candle, make_candle, to_candles
from core.services.dca.backtest.replay_exchange import ReplayExchange, ReplayOrderRecord
from core.services.dca.execution.dca_order_executor import DcaOrderExecutor
from core.services.dca.models.dca_config import DcaConfig
from core.services.dca.models.dca_order import DcaOrderRole


class TestDcaReplayExchange(unittest.TestCase):
    def setUp(self):
        # Base set of 5 15-minute candles
        self.candles = [
            make_candle(1700000000, 100, 105, 95, 102, 1000),    # Bar 0
            make_candle(1700000900, 102, 108, 98, 106, 1200),    # Bar 1
            make_candle(1700001800, 106, 107, 90, 92, 1500),     # Bar 2
            make_candle(1700002700, 92, 115, 88, 112, 2000),     # Bar 3
            make_candle(1700003600, 112, 114, 108, 110, 800),    # Bar 4
        ]
        self.exchange = ReplayExchange(self.candles)

    def test_audit_2_1_r1_no_lookahead_bias(self):
        """2.1 P0: Order placed at bar i must NEVER match at bar i, only at bar j > i."""
        # Set exchange to bar 0
        self.exchange.set_bar(0)
        # Place a buy limit order at 104 (bar 0 has low=95 <= 104)
        order = asyncio.run(self.exchange.create_order(
            symbol="ETH/USDC:USDC",
            side="buy",
            order_type="limit",
            amount=Decimal("1.0"),
            price=Decimal("104"),
            params={"client_order_id": "dca_test_long_base"},
        ))
        self.assertEqual(order.status, "open")
        self.assertEqual(order.filled, Decimal("0"))

        # Advance bar 0 -> Since placed_bar is 0, it must NOT match in bar 0!
        filled_bar0 = self.exchange.advance(0)
        self.assertEqual(len(filled_bar0), 0)
        rec = asyncio.run(self.exchange.get_order(order.id, "ETH/USDC:USDC"))
        self.assertEqual(rec.status, "open")

        # Advance to bar 1 (bar 1 has low=98 <= 104) -> Matches now in bar 1!
        filled_bar1 = self.exchange.advance(1)
        self.assertEqual(len(filled_bar1), 1)
        self.assertEqual(filled_bar1[0].id, order.id)
        self.assertEqual(filled_bar1[0].status, "filled")
        self.assertEqual(filled_bar1[0].filled_bar, 1)

    def test_audit_2_2_r2_buy_limit_matching_vector(self):
        """2.2 P0: Buy limit low <= price -> fills; low > price -> does not fill."""
        candles = [
            make_candle(1700000000, 100, 105, 95, 100),
            make_candle(1700000900, 100, 105, 99, 100),  # Bar 1: low = 99
            make_candle(1700001800, 102, 105, 101, 104), # Bar 2: low = 101
        ]
        ex = ReplayExchange(candles)
        ex.set_bar(0)

        o1 = asyncio.run(ex.create_order(
            "SOL/USDC", "buy", "limit", Decimal("2.0"), Decimal("100"),
            params={"client_order_id": "so1"}
        ))
        o2 = asyncio.run(ex.create_order(
            "SOL/USDC", "buy", "limit", Decimal("2.0"), Decimal("98"),
            params={"client_order_id": "so2"}
        ))

        # Advance to bar 1 (low = 99):
        # o1 (price=100) satisfies low(99) <= 100 -> FILLED at 100
        # o2 (price=98) has low(99) > 98 -> NOT filled
        filled = ex.advance(1)
        self.assertEqual(len(filled), 1)
        self.assertEqual(filled[0].client_id, "so1")
        self.assertEqual(filled[0].average, Decimal("100"))

        rec2 = asyncio.run(ex.get_order(o2.id, "SOL/USDC"))
        self.assertEqual(rec2.status, "open")

        # Advance to bar 2 (low = 101):
        # o2 (price=98) has low(101) > 98 -> still NOT filled
        filled2 = ex.advance(2)
        self.assertEqual(len(filled2), 0)

    def test_audit_2_3_r3_sell_limit_matching_vector(self):
        """2.3 P0: Sell limit high >= price -> fills; high < price -> does not fill."""
        candles = [
            make_candle(1700000000, 100, 105, 95, 100),
            make_candle(1700000900, 100, 111, 99, 105),  # Bar 1: high = 111
            make_candle(1700001800, 100, 109, 99, 102),  # Bar 2: high = 109
        ]
        ex = ReplayExchange(candles)
        ex.set_bar(0)

        o1 = asyncio.run(ex.create_order(
            "SOL/USDC", "sell", "limit", Decimal("1.0"), Decimal("110"),
            params={"client_order_id": "tp1"}
        ))
        o2 = asyncio.run(ex.create_order(
            "SOL/USDC", "sell", "limit", Decimal("1.0"), Decimal("112"),
            params={"client_order_id": "tp2"}
        ))

        # Advance to bar 1 (high = 111):
        # o1 (price=110) satisfies high(111) >= 110 -> FILLED
        # o2 (price=112) has high(111) < 112 -> NOT filled
        filled = ex.advance(1)
        self.assertEqual(len(filled), 1)
        self.assertEqual(filled[0].client_id, "tp1")
        self.assertEqual(filled[0].average, Decimal("110"))

        # Advance to bar 2 (high = 109):
        # o2 (price=112) has high(109) < 112 -> NOT filled
        filled2 = ex.advance(2)
        self.assertEqual(len(filled2), 0)

    def test_audit_2_4_gap_opening_prices(self):
        """2.4 P0: Gaps execute at open price (min(open, limit) for buy, max(open, limit) for sell)."""
        candles = [
            make_candle(1700000000, 100, 105, 95, 100),
            # Bar 1: Gapped down! open = 95, high = 96, low = 90, close = 92
            make_candle(1700000900, 95, 96, 90, 92),
            # Bar 2: Gapped up! open = 115, high = 120, low = 114, close = 118
            make_candle(1700001800, 115, 120, 114, 118),
        ]
        ex = ReplayExchange(candles)
        ex.set_bar(0)

        # Buy limit at 100 (market opened at 95) -> Fills at 95 (better price!)
        buy_order = asyncio.run(ex.create_order(
            "BTC/USDC", "buy", "limit", Decimal("0.5"), Decimal("100"),
            params={"client_order_id": "gap_buy"}
        ))
        filled1 = ex.advance(1)
        self.assertEqual(len(filled1), 1)
        self.assertEqual(filled1[0].average, Decimal("95"))

        # Sell limit at 110 (market opened at 115) -> Fills at 115 (better price!)
        sell_order = asyncio.run(ex.create_order(
            "BTC/USDC", "sell", "limit", Decimal("0.5"), Decimal("110"),
            params={"client_order_id": "gap_sell"}
        ))
        filled2 = ex.advance(2)
        self.assertEqual(len(filled2), 1)
        self.assertEqual(filled2[0].average, Decimal("115"))

    def test_audit_2_5_r4_conservative_same_bar_sequence(self):
        """2.5 P0: In the same bar, unfavorable safety orders execute BEFORE take profit orders."""
        candles = [
            make_candle(1700000000, 100, 102, 98, 100),
            # Bar 1: Huge bar from 90 to 110 (open=100)
            make_candle(1700000900, 100, 110, 90, 104),
        ]
        ex = ReplayExchange(candles)
        ex.set_bar(0)

        # Long position: Safety Buy at 95 and TP Sell at 105
        # Even if TP was placed first, conservative rule requires Safety Buy to be settled first!
        tp = asyncio.run(ex.create_order(
            "ETH/USDC", "sell", "limit", Decimal("1.0"), Decimal("105"),
            params={"client_order_id": "dca_test_long_tp"}
        ))
        so = asyncio.run(ex.create_order(
            "ETH/USDC", "buy", "limit", Decimal("1.0"), Decimal("95"),
            params={"client_order_id": "dca_test_long_so01"}
        ))

        filled = ex.advance(1)
        self.assertEqual(len(filled), 2)
        # Assert Safety Buy is filled BEFORE Take Profit Sell
        self.assertEqual(filled[0].client_id, "dca_test_long_so01")
        self.assertEqual(filled[1].client_id, "dca_test_long_tp")

    def test_audit_2_6_r4_same_type_safety_order_sorting(self):
        """2.6 P1: Multiple safety buy orders in the same bar fill in high-to-low order."""
        candles = [
            make_candle(1700000000, 100, 102, 98, 100),
            # Bar 1 drops to 90
            make_candle(1700000900, 100, 101, 90, 91),
        ]
        ex = ReplayExchange(candles)
        ex.set_bar(0)

        # Place safety orders in reverse order
        so3 = asyncio.run(ex.create_order("ETH/USDC", "buy", "limit", Decimal("1"), Decimal("92"), params={"client_order_id": "so03"}))
        so1 = asyncio.run(ex.create_order("ETH/USDC", "buy", "limit", Decimal("1"), Decimal("98"), params={"client_order_id": "so01"}))
        so2 = asyncio.run(ex.create_order("ETH/USDC", "buy", "limit", Decimal("1"), Decimal("95"), params={"client_order_id": "so02"}))

        filled = ex.advance(1)
        self.assertEqual(len(filled), 3)
        self.assertEqual([f.client_id for f in filled], ["so01", "so02", "so03"])
        self.assertEqual([f.average for f in filled], [Decimal("98"), Decimal("95"), Decimal("92")])

    def test_audit_2_7_r5_market_orders_fill_at_open(self):
        """2.7 P0: Market stop loss orders fill at bar open price."""
        candles = [
            make_candle(1700000000, 100, 102, 98, 100),
            make_candle(1700000900, 85, 88, 80, 82),  # Gapped down to open 85
        ]
        ex = ReplayExchange(candles)
        ex.set_bar(0)

        sl = asyncio.run(ex.create_order(
            "ETH/USDC", "sell", "market", Decimal("1.5"),
            params={"client_order_id": "dca_test_long_sl"}
        ))
        filled = ex.advance(1)
        self.assertEqual(len(filled), 1)
        self.assertEqual(filled[0].average, Decimal("85"))

    def test_audit_2_8_duck_typing_compatibility_with_executor(self):
        """2.8 P0: ReplayExchange fulfills the full 5-method duck-typing contract of DcaOrderExecutor."""
        candles = [
            make_candle(1700000000, 2500, 2510, 2490, 2500),
            make_candle(1700000900, 2500, 2520, 2480, 2505),
        ]
        ex = ReplayExchange(candles)
        config = DcaConfig(
            symbol="ETH/USDC:USDC",
            exchange="binance",
            base_order_amount=Decimal("20"),
        )
        executor = DcaOrderExecutor(config, "test_bot", ex, min_notional=Decimal("0"), circuit_probe=lambda: False)

        # 1. last_price() -> calls get_ticker()
        price = asyncio.run(executor.last_price())
        self.assertEqual(price, Decimal("2500"))

        # 2. place_limit() -> calls create_order()
        outcome = asyncio.run(executor.place_limit(
            role=DcaOrderRole.BASE,
            side="buy",
            price=Decimal("2495"),
            quantity=Decimal("0.008"),
            leg="long",
        ))
        self.assertEqual(outcome.kind.value, "placed")
        self.assertIsNotNone(outcome.order)

        # 3. fetch_open_orders() -> calls get_open_orders()
        open_orders = asyncio.run(executor.fetch_open_orders())
        self.assertEqual(len(open_orders), 1)

        # 4. cancel() -> calls cancel_order()
        cancel_ok = asyncio.run(executor.cancel(outcome.order))
        self.assertTrue(cancel_ok)

        # 5. fetch_order() -> calls get_order()
        fetched = asyncio.run(executor.fetch_order(outcome.order.client_order_id))
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.status.value, "cancelled")

    def test_audit_2_9_cancel_order_semantics(self):
        """2.9 P0: Cancelled order no longer matches, cancelling non-open order returns error."""
        ex = ReplayExchange(self.candles)
        ex.set_bar(0)

        o1 = asyncio.run(ex.create_order("ETH/USDC", "buy", "limit", Decimal("1"), Decimal("95"), params={"client_order_id": "o1"}))
        res = asyncio.run(ex.cancel_order(o1.id, "ETH/USDC"))
        self.assertTrue(res["success"])

        # Try to cancel again -> False
        res2 = asyncio.run(ex.cancel_order(o1.id, "ETH/USDC"))
        self.assertFalse(res2["success"])

        # Advance to bar 2 (low=90 <= 95) -> o1 must NOT match because it was cancelled!
        filled = ex.advance(2)
        self.assertEqual(len(filled), 0)

    def test_audit_2_10_zero_io_static_check(self):
        """2.10 P0: backtest package must contain zero I/O, sleep, or wall-clock calls."""
        backtest_dir = os.path.join(_project_root, "core", "services", "dca", "backtest")
        forbidden = ["open(", "sqlite3", "requests", "aiohttp", "asyncio.sleep", "datetime.now"]

        for root, _, files in os.walk(backtest_dir):
            for file in files:
                if not file.endswith(".py"):
                    continue
                path = os.path.join(root, file)
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                for token in forbidden:
                    self.assertNotIn(
                        token, content,
                        f"Forbidden token '{token}' found in {path}"
                    )

    def test_audit_2_11_decimal_purity_static_check(self):
        """2.11 P1: replay_exchange.py calculation paths must not use float()."""
        path = os.path.join(_project_root, "core", "services", "dca", "backtest", "replay_exchange.py")
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        for idx, line in enumerate(lines, 1):
            if "float(" in line and not line.strip().startswith("#"):
                self.fail(f"float() found at {path}:{idx}: {line}")

    def test_audit_2_12_no_partial_fills_and_docstring(self):
        """2.12 P1: Whole order fills (filled == amount) and docstring contains explicit notice."""
        path = os.path.join(_project_root, "core", "services", "dca", "backtest", "replay_exchange.py")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("不模擬部分成交", content)

        ex = ReplayExchange(self.candles)
        ex.set_bar(0)
        o1 = asyncio.run(ex.create_order("ETH/USDC", "buy", "limit", Decimal("5.1234"), Decimal("95")))
        filled = ex.advance(2)
        self.assertEqual(len(filled), 1)
        self.assertEqual(filled[0].filled, Decimal("5.1234"))
        self.assertEqual(filled[0].amount, Decimal("5.1234"))

    def test_audit_2_13_grid_zero_regression(self):
        """2.13 P0: Grid trading core and runner files must have zero modifications."""
        import subprocess
        res = subprocess.run(
            ["git", "diff", "--stat", "core/services/grid/", "run_grid_trading.py"],
            capture_output=True, text=True, cwd=_project_root
        )
        self.assertEqual(res.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
