"""
CoinFull Prime 盤中硬止損與預掛 STOP_MARKET 架構測試 (2026-10-29)

驗證項目：
1. 報價取值修復：TickerData.last_price 優先回傳 last/close，service 與 rebalancer 能正確自 TickerData 獲取市價。
2. 調倉管線預掛 STOP_MARKET：多頭買入成交後，向交易所發送帶有量化 stopPrice 的 STOP_MARKET (closePosition=True) 條件單。
3. 調倉前批量撤銷舊條件單：在發送新調倉單前，撤銷舊的 cfp_sl_ 條件單，且不誤撤外部機器人受保護之訂單。
4. Reconciler 補齊觸發成交後的記帳同步：當預掛條件單在交易所觸發成交導致持倉歸零時，對帳程序自動同步結算 Canonical Ledger。
5. 盤中止損 -2022 自愈：市價平倉若遇 -2022 拒單，自動反查預掛 STOP_MARKET 是否已成交並完成記帳。
"""

import asyncio
import time
import unittest
from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from core.adapters.exchanges.models import (
    OrderData,
    OrderSide,
    OrderStatus,
    OrderType,
    TickerData,
)
from core.infrastructure.database.models import BasketEntry
from core.services.coinfull_prime.models import (
    RankedItem,
    SignalZScores,
    UniverseRankResult,
)
from core.services.coinfull_prime.rebalancer import PortfolioRebalancer
from core.services.coinfull_prime.service import CoinFullPrimeService


def _make_order(
    id="ord-1",
    client_id="",
    symbol="BTCUSDT",
    side=OrderSide.BUY,
    type=OrderType.MARKET,
    amount=Decimal("1"),
    price=Decimal("100"),
    filled=Decimal("0"),
    status=OrderStatus.OPEN,
    average=None,
    params=None,
):
    now = datetime.now()
    return OrderData(
        id=id,
        client_id=client_id,
        symbol=symbol,
        side=side,
        type=type,
        amount=amount,
        price=price,
        filled=filled,
        remaining=max(Decimal("0"), amount - filled),
        cost=Decimal("0"),
        average=average or price,
        status=status,
        timestamp=now,
        updated=now,
        fee=None,
        trades=[],
        params=params or {},
        raw_data={},
    )


class _MockLedger:
    """Mock StrategyLedger for testing basket operations."""

    def __init__(self, entries=None):
        self.entries = list(entries or [])
        self.apply_calls = []
        self.recorded_fills = []
        self.events = []

    def load_basket(self, bot_id, account_id=None, active_only=True):
        if active_only:
            return [e for e in self.entries if e.status == "active"]
        return list(self.entries)

    def apply_basket_fill(self, **kwargs):
        self.apply_calls.append(kwargs)
        for e in self.entries:
            if e.symbol == kwargs.get("symbol") and e.status == "active":
                e.quantity = max(0.0, float(e.quantity) - float(kwargs.get("quantity", 0.0)))
                if e.quantity <= 1e-8:
                    e.status = "closed"
        return 50.0

    def record_fill(self, fill):
        self.recorded_fills.append(fill)

    def append_event(self, bot_id, event_type, payload=None, dedupe_key=None):
        self.events.append({
            "bot_id": bot_id,
            "event_type": event_type,
            "payload": payload,
            "dedupe_key": dedupe_key,
        })

    def ensure_bot(self, **kwargs):
        pass

    def save_basket(self, bot_id, account_id, entries, close_missing=False):
        pass


class TestCoinFullPrimeStopMarket(unittest.TestCase):

    def test_ticker_data_last_price_property(self):
        """驗證 TickerData.last_price 屬性能相容提取 last 或 close。"""
        t1 = TickerData(symbol="BTC/USDT", timestamp=datetime.now(), last=Decimal("65000.5"))
        self.assertEqual(t1.last_price, Decimal("65000.5"))

        t2 = TickerData(symbol="ETH/USDT", timestamp=datetime.now(), close=Decimal("2500.25"))
        self.assertEqual(t2.last_price, Decimal("2500.25"))

        t3 = TickerData(symbol="SOL/USDT", timestamp=datetime.now(), last=Decimal("150"), close=Decimal("149"))
        self.assertEqual(t3.last_price, Decimal("150"))

    def test_rebalancer_quote_retrieval_with_ticker_data(self):
        """驗證 PortfolioRebalancer.execute_rebalance 能正確從 TickerData 讀取 last/close 價格。"""
        rebalancer = PortfolioRebalancer(config={
            "portfolio": {"total_capital_usdt": 1000, "dry_run": True},
            "risk_control": {"intra_week_stop_loss_pct": -0.12}
        })

        mock_adapter = MagicMock()
        mock_adapter.get_ticker = AsyncMock(return_value=TickerData(
            symbol="AVAXUSDT",
            timestamp=datetime.now(),
            last=Decimal("25.5")
        ))
        mock_adapter.get_positions = AsyncMock(return_value=[])

        rank_res = UniverseRankResult(
            as_of_date="2026-10-29",
            long_list=[RankedItem(symbol="AVAXUSDT", rank=1, score=1.0, signals=SignalZScores(), target_notional=100.0, side="long")],
            short_list=[]
        )

        with patch("core.infrastructure.database.strategy_ledger.StrategyLedger.instance") as mock_inst:
            mock_inst.return_value = _MockLedger()
            report = asyncio.run(rebalancer.execute_rebalance(
                rank_result=rank_res,
                exchange_adapter=mock_adapter,
                dry_run=True,
                account_id="paper"
            ))

        self.assertGreater(len(report.orders), 0)
        self.assertEqual(report.orders[0].symbol, "AVAXUSDT")
        mock_adapter.get_ticker.assert_called_with("AVAXUSDT")

    def test_rebalancer_pre_cancel_old_stop_orders(self):
        """驗證在調倉前會撤銷舊的 cfp_sl_ 條件單，且不撤銷受保護外部機器人訂單。"""
        rebalancer = PortfolioRebalancer(config={
            "portfolio": {"total_capital_usdt": 1000, "dry_run": False},
        })

        old_order_1 = _make_order(
            id="order-111",
            client_id="cfp_sl_AVAXUSDT",
            symbol="AVAXUSDT",
            side=OrderSide.SELL,
            type=OrderType.STOP_MARKET,
            amount=Decimal("10"),
            price=Decimal("20"),
            status=OrderStatus.OPEN,
        )
        old_order_2 = _make_order(
            id="order-222",
            client_id="grid_order_999",
            symbol="AVAXUSDT",
            side=OrderSide.SELL,
            type=OrderType.LIMIT,
            amount=Decimal("5"),
            price=Decimal("30"),
            status=OrderStatus.OPEN,
        )

        mock_adapter = MagicMock()
        mock_adapter.get_open_orders = AsyncMock(return_value=[old_order_1, old_order_2])
        mock_adapter.cancel_order = AsyncMock(return_value=old_order_1)
        mock_adapter.name = "binance_live"

        count = asyncio.run(rebalancer._cancel_existing_stop_orders(
            exchange_adapter=mock_adapter,
            symbols={"AVAXUSDT", "BTCUSDT"},
            protected_symbols={"BTCUSDT"}  # BTC is protected
        ))

        self.assertEqual(count, 1)
        mock_adapter.cancel_order.assert_called_once_with("order-111", "AVAXUSDT")

    def test_rebalance_places_stop_market_order_on_long_entry(self):
        """驗證多頭買入成交後，會自動送出帶有量化 stopPrice 的 STOP_MARKET 條件單。"""
        rebalancer = PortfolioRebalancer(config={
            "portfolio": {"total_capital_usdt": 1000, "dry_run": False},
            "risk_control": {"intra_week_stop_loss_pct": -0.12}
        })

        created_orders = []

        async def fake_create_order(symbol, side, order_type, amount, price=None, params=None):
            od = _make_order(
                id=f"order-{len(created_orders)+1}",
                client_id=params.get("clientOrderId") if params else "",
                symbol=symbol,
                side=side,
                type=order_type,
                amount=amount,
                price=price or Decimal("100.0"),
                average=Decimal("100.0"),
                filled=amount,
                status=OrderStatus.FILLED if order_type == OrderType.MARKET else OrderStatus.OPEN,
            )
            created_orders.append((symbol, side, order_type, amount, params))
            return od

        mock_adapter = MagicMock()
        mock_adapter.create_order = AsyncMock(side_effect=fake_create_order)
        mock_adapter.get_positions = AsyncMock(return_value=[])
        mock_adapter.get_open_orders = AsyncMock(return_value=[])
        mock_adapter.name = "binance_live"
        mock_adapter._market_info = {
            "SOLUSDT": {
                "id": "SOLUSDT",
                "symbol": "SOL/USDT:USDT",
                "linear": True,
                "precision": {"amount": "0.1", "price": "0.01"},
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                    {"filterType": "LOT_SIZE", "stepSize": "0.1"}
                ]
            }
        }

        rank_res = UniverseRankResult(
            as_of_date="2026-10-29",
            long_list=[RankedItem(symbol="SOLUSDT", rank=1, score=1.0, signals=SignalZScores(), target_notional=100.0, side="long")],
            short_list=[]
        )

        with patch("core.infrastructure.database.strategy_ledger.StrategyLedger.instance") as mock_inst:
            fake_ledger = _MockLedger()
            mock_inst.return_value = fake_ledger
            report = asyncio.run(rebalancer.execute_rebalance(
                rank_result=rank_res,
                exchange_adapter=mock_adapter,
                dry_run=False,
                prices_override={"SOLUSDT": 100.0},
                account_id="binance_live"
            ))

        self.assertEqual(report.status, "completed")
        self.assertEqual(len(created_orders), 2)

        buy_order = created_orders[0]
        self.assertEqual(buy_order[0], "SOLUSDT")
        self.assertEqual(buy_order[1], OrderSide.BUY)
        self.assertEqual(buy_order[2], OrderType.MARKET)

        stop_order = created_orders[1]
        self.assertEqual(stop_order[0], "SOLUSDT")
        self.assertEqual(stop_order[1], OrderSide.SELL)
        self.assertEqual(stop_order[2], OrderType.STOP_MARKET)
        self.assertEqual(stop_order[4]["stopPrice"], 88.0)
        self.assertTrue(stop_order[4]["closePosition"])
        self.assertEqual(stop_order[4]["clientOrderId"], "cfp_sl_SOLUSDT")

    def test_reconciler_syncs_filled_stop_market_order(self):
        """驗證 Reconciler 偵測到交易所部位歸零且預掛 STOP_MARKET 已 FILLED 時，自動結算記帳。"""
        service = CoinFullPrimeService(config_path="config/coinfull_prime/universe.yaml")

        entry = BasketEntry(
            bot_id="coinfull_prime:binance_live",
            account_id="binance_live",
            symbol="LINKUSDT",
            side="long",
            quantity=10.0,
            entry_price=15.0,
            notional_usdt=150.0,
            status="active"
        )
        fake_ledger = _MockLedger([entry])

        mock_adapter = MagicMock()
        mock_adapter.get_positions = AsyncMock(return_value=[])

        sl_order = _make_order(
            id="sl-exchange-999",
            client_id="cfp_sl_LINKUSDT",
            symbol="LINKUSDT",
            side=OrderSide.SELL,
            type=OrderType.STOP_MARKET,
            amount=Decimal("10.0"),
            price=Decimal("13.2"),
            average=Decimal("13.15"),
            filled=Decimal("10.0"),
            status=OrderStatus.FILLED,
        )
        mock_adapter.get_order = AsyncMock(return_value=sl_order)

        with patch.object(service, "get_positions", AsyncMock(return_value=[])):
            with patch.object(service, "load_basket_entries", return_value=[entry]):
                with patch.object(service, "_ledger", return_value=fake_ledger):
                    with patch.object(service, "_export_basket_mirror"):
                        result = asyncio.run(service.reconcile_positions(
                            account_id="binance_live",
                            adapter=mock_adapter
                        ))

        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["missing_on_exchange"]), 0)
        self.assertEqual(len(result["reconciled_stop_losses"]), 1)
        self.assertEqual(result["reconciled_stop_losses"][0]["symbol"], "LINKUSDT")
        self.assertEqual(result["reconciled_stop_losses"][0]["order_id"], "sl-exchange-999")
        self.assertEqual(result["reconciled_stop_losses"][0]["price"], 13.15)

        self.assertEqual(len(fake_ledger.apply_calls), 1)
        self.assertEqual(fake_ledger.apply_calls[0]["symbol"], "LINKUSDT")
        self.assertTrue(fake_ledger.apply_calls[0]["reduce_only"])
        self.assertEqual(len(fake_ledger.recorded_fills), 1)
        self.assertEqual(fake_ledger.recorded_fills[0].role, "stop_loss")
        self.assertEqual(fake_ledger.recorded_fills[0].exchange_order_id, "sl-exchange-999")

    def test_execute_stop_loss_2022_recovery(self):
        """驗證盤中動態止損遇 -2022 拒單時，自動反查預掛訂單並完成記帳結算。"""
        rebalancer = PortfolioRebalancer(config={
            "portfolio": {"total_capital_usdt": 1000, "dry_run": False},
            "risk_control": {"intra_week_stop_loss_pct": -0.12}
        })

        entry = BasketEntry(
            bot_id="coinfull_prime:binance_live",
            account_id="binance_live",
            symbol="NEARUSDT",
            side="long",
            quantity=50.0,
            entry_price=5.0,
            notional_usdt=250.0,
            status="active"
        )
        fake_ledger = _MockLedger([entry])

        mock_adapter = MagicMock()
        mock_adapter.create_order = AsyncMock(
            side_effect=Exception("binance -2022: ReduceOnly Order is rejected.")
        )
        mock_adapter._market_info = {
            "NEARUSDT": {
                "id": "NEARUSDT",
                "symbol": "NEAR/USDT:USDT",
                "linear": True,
                "precision": {"amount": "1", "price": "0.001"}
            }
        }

        sl_order = _make_order(
            id="sl-near-777",
            client_id="cfp_sl_NEARUSDT",
            symbol="NEARUSDT",
            side=OrderSide.SELL,
            type=OrderType.STOP_MARKET,
            amount=Decimal("50.0"),
            price=Decimal("4.4"),
            average=Decimal("4.395"),
            filled=Decimal("50.0"),
            status=OrderStatus.FILLED,
        )
        mock_adapter.get_order = AsyncMock(return_value=sl_order)

        with patch("core.infrastructure.database.strategy_ledger.StrategyLedger.instance", return_value=fake_ledger):
            res = asyncio.run(rebalancer.execute_stop_loss(
                symbol="NEARUSDT",
                account_id="binance_live",
                current_price=4.38,
                exchange_adapter=mock_adapter,
                dry_run=False
            ))

        self.assertEqual(res["status"], "filled")
        self.assertEqual(res["symbol"], "NEARUSDT")
        self.assertEqual(res["exit_price"], 4.395)
        self.assertEqual(len(fake_ledger.apply_calls), 1)
        self.assertEqual(len(fake_ledger.recorded_fills), 1)
        self.assertEqual(fake_ledger.recorded_fills[0].exchange_order_id, "sl-near-777")


if __name__ == "__main__":
    unittest.main()
