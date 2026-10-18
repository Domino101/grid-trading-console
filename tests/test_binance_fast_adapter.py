"""
Unit tests for BinanceFastAdapter (P2-3: Native WS/REST High-Performance Adapter)
"""

import asyncio
import hashlib
import hmac
import json
import time
import unittest
from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from core.adapters.exchanges.adapters.binance_fast import BinanceFastAdapter
from core.adapters.exchanges.factory import ExchangeFactory
from core.adapters.exchanges.interface import ExchangeConfig, ExchangeStatus
from core.adapters.exchanges.models import (
    ExchangeType,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
)


class TestBinanceFastAdapter(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = ExchangeConfig(
            exchange_id="binance_fast",
            name="Binance Futures Fast",
            exchange_type=ExchangeType.FUTURES,
            api_key="test_fast_api_key_12345",
            api_secret="test_fast_api_secret_67890",
            testnet=False,
            enable_websocket=True,
        )
        self.adapter = BinanceFastAdapter(self.config)

    async def asyncTearDown(self):
        await self.adapter.disconnect()

    def test_factory_registration(self):
        """測試 ExchangeFactory 正確註冊並能產出 BinanceFastAdapter"""
        factory = ExchangeFactory()
        adapter = factory.create_adapter(
            "binance_fast",
            api_key="test_key",
            api_secret="test_secret",
        )
        self.assertIsInstance(adapter, BinanceFastAdapter)
        self.assertEqual(adapter.config.exchange_id, "binance_fast")

    def test_hmac_signature_correctness(self):
        """測試原生 C-accelerated HMAC-SHA256 簽名正確性"""
        params = {"symbol": "BTCUSDT", "side": "BUY", "quantity": Decimal("1.5")}
        signed_query = self.adapter._sign_params(params)

        self.assertIn("timestamp=", signed_query)
        self.assertIn("signature=", signed_query)
        self.assertIn("symbol=BTCUSDT", signed_query)
        self.assertIn("quantity=1.5", signed_query)

        # 獨立驗證簽名
        parts = signed_query.split("&signature=")
        query_part = parts[0]
        sig_part = parts[1]
        expected_sig = hmac.new(
            b"test_fast_api_secret_67890",
            query_part.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        self.assertEqual(sig_part, expected_sig)

    def test_benchmark_order_prep_latency(self):
        """
        量化驗收：測量內部下單參數打包與簽名耗時
        DoD 要求：內部準備耗時由 15ms 降至 < 2ms (實測通常 < 0.05ms)
        """
        cost_ms = self.adapter.benchmark_order_prep(iterations=2000)
        print(f"\n⚡ [BinanceFastAdapter] 實測單次下單內部準備耗時: {cost_ms * 1000:.2f} μs ({cost_ms:.4f} ms)")
        self.assertLess(cost_ms, 2.0, f"下單內部準備耗時 {cost_ms}ms 超過 2ms 上限！")

    def test_parse_binance_order(self):
        """測試原生 Binance JSON 直轉 OrderData 模型"""
        raw_order = {
            "orderId": 123456789,
            "clientOrderId": "grid_fast_001",
            "symbol": "BTCUSDT",
            "status": "NEW",
            "side": "BUY",
            "type": "LIMIT",
            "price": "65000.50",
            "origQty": "0.100",
            "executedQty": "0.020",
            "cumQuote": "1300.01",
            "updateTime": 1788632396000,
        }

        order = self.adapter._parse_binance_order(raw_order, "BTC/USDT")
        self.assertEqual(order.id, "123456789")
        self.assertEqual(order.client_id, "grid_fast_001")
        self.assertEqual(order.symbol, "BTC/USDT")
        self.assertEqual(order.side, OrderSide.BUY)
        self.assertEqual(order.type, OrderType.LIMIT)
        self.assertEqual(order.status, OrderStatus.OPEN)
        self.assertEqual(order.price, Decimal("65000.50"))
        self.assertEqual(order.amount, Decimal("0.100"))
        self.assertEqual(order.filled, Decimal("0.020"))
        self.assertEqual(order.remaining, Decimal("0.080"))
        self.assertEqual(order.cost, Decimal("1300.01"))

    async def test_mocked_create_and_cancel_order(self):
        """測試下單與撤單的完整 REST 調用鏈路"""
        mock_resp_data = {
            "orderId": 987654321,
            "clientOrderId": "test_client_001",
            "symbol": "SOLUSDT",
            "status": "NEW",
            "side": "SELL",
            "type": "LIMIT",
            "price": "145.20",
            "origQty": "2.5",
            "executedQty": "0",
            "cumQuote": "0",
            "updateTime": 1788632396000,
        }

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=mock_resp_data)
        mock_post = MagicMock()
        mock_post.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_post.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.close = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_post)
        self.adapter._http_session = mock_session

        order = await self.adapter.create_order(
            symbol="SOL/USDT",
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            amount=Decimal("2.5"),
            price=Decimal("145.20"),
            params={"clientOrderId": "test_client_001"},
        )

        self.assertEqual(order.id, "987654321")
        self.assertEqual(order.side, OrderSide.SELL)
        self.assertEqual(order.price, Decimal("145.20"))
        self.assertEqual(order.amount, Decimal("2.5"))

        # 測試撤單
        mock_del_resp_data = dict(mock_resp_data)
        mock_del_resp_data["status"] = "CANCELED"
        mock_del_resp = AsyncMock()
        mock_del_resp.status = 200
        mock_del_resp.json = AsyncMock(return_value=mock_del_resp_data)
        mock_delete = MagicMock()
        mock_delete.__aenter__ = AsyncMock(return_value=mock_del_resp)
        mock_delete.__aexit__ = AsyncMock(return_value=None)
        mock_session.delete = MagicMock(return_value=mock_delete)

        cancelled = await self.adapter.cancel_order("987654321", "SOL/USDT")
        self.assertEqual(cancelled.id, "987654321")
        self.assertEqual(cancelled.status, OrderStatus.CANCELED)

    async def test_mocked_get_positions_and_balances(self):
        """測試持倉與餘額查詢"""
        mock_pos_data = [
            {
                "symbol": "BTCUSDT",
                "positionAmt": "0.500",
                "entryPrice": "62000.00",
                "markPrice": "63500.00",
                "unRealizedProfit": "750.00",
                "leverage": "10",
                "isolated": False,
                "isolatedMargin": "0",
                "liquidationPrice": "55000.00",
            }
        ]

        mock_bal_data = [
            {
                "asset": "USDT",
                "balance": "50000.00",
                "availableBalance": "35000.00",
            },
            {
                "asset": "USDC",
                "balance": "10000.00",
                "availableBalance": "10000.00",
            },
        ]

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.close = AsyncMock()

        # Mock positions
        pos_resp = AsyncMock()
        pos_resp.status = 200
        pos_resp.json = AsyncMock(return_value=mock_pos_data)
        pos_get = MagicMock()
        pos_get.__aenter__ = AsyncMock(return_value=pos_resp)
        pos_get.__aexit__ = AsyncMock(return_value=None)

        # Mock balances
        bal_resp = AsyncMock()
        bal_resp.status = 200
        bal_resp.json = AsyncMock(return_value=mock_bal_data)
        bal_get = MagicMock()
        bal_get.__aenter__ = AsyncMock(return_value=bal_resp)
        bal_get.__aexit__ = AsyncMock(return_value=None)

        mock_session.get = MagicMock(side_effect=[pos_get, bal_get])
        self.adapter._http_session = mock_session

        positions = await self.adapter.get_positions(["BTC/USDT"])
        self.assertEqual(len(positions), 1)
        self.assertIn(positions[0].symbol, ("BTC/USDT", "BTC/USDT:USDT"))
        self.assertEqual(positions[0].side, PositionSide.LONG)
        self.assertEqual(positions[0].size, Decimal("0.500"))
        self.assertEqual(positions[0].unrealized_pnl, Decimal("750.00"))

        balances = await self.adapter.get_balances()
        self.assertEqual(len(balances), 2)
        usdt_bal = next(b for b in balances if b.currency == "USDT")
        self.assertEqual(usdt_bal.free, Decimal("35000.00"))
        self.assertEqual(usdt_bal.total, Decimal("50000.00"))
        self.assertEqual(usdt_bal.used, Decimal("15000.00"))

    def test_ws_message_dispatching(self):
        """測試原生 WebSocket 行情分發與解析"""
        received_tickers = []
        received_obs = []

        self.adapter._ticker_callbacks["btcusdt@ticker"] = [lambda t: received_tickers.append(t)]
        self.adapter._orderbook_callbacks["btcusdt@depth5@100ms"] = [lambda ob: received_obs.append(ob)]

        # 模擬 24hrTicker 事件
        ticker_msg = json.dumps({
            "e": "24hrTicker",
            "E": 1788632396000,
            "s": "BTCUSDT",
            "b": "64999.00",
            "B": "1.25",
            "a": "65000.00",
            "A": "2.40",
            "c": "64999.50",
            "o": "64000.00",
            "h": "66000.00",
            "l": "63800.00",
            "v": "10500.2",
            "q": "682500000.0",
        })
        self.adapter._dispatch_ws_message(ticker_msg)

        self.assertEqual(len(received_tickers), 1)
        t = received_tickers[0]
        self.assertIn(t.symbol, ("BTC/USDT", "BTC/USDT:USDT"))
        self.assertEqual(t.bid, Decimal("64999.00"))
        self.assertEqual(t.ask, Decimal("65000.00"))
        self.assertEqual(t.last, Decimal("64999.50"))

        # 模擬 depth5 訂單簿事件
        depth_msg = json.dumps({
            "s": "BTCUSDT",
            "E": 1788632396000,
            "u": 998877,
            "bids": [["64999.00", "1.5"], ["64998.00", "3.0"]],
            "asks": [["65000.00", "2.1"], ["65001.00", "4.5"]],
        })
        self.adapter._dispatch_ws_message(depth_msg)

        self.assertEqual(len(received_obs), 1)
        ob = received_obs[0]
        self.assertIn(ob.symbol, ("BTC/USDT", "BTC/USDT:USDT"))
        self.assertEqual(len(ob.bids), 2)
        self.assertEqual(ob.bids[0].price, Decimal("64999.00"))
        self.assertEqual(ob.bids[0].size, Decimal("1.5"))
        self.assertEqual(ob.asks[0].price, Decimal("65000.00"))
        self.assertEqual(ob.nonce, 998877)


if __name__ == "__main__":
    unittest.main()
