"""訂單健康告警：掛單為 0 且連續被拒單時必須發出訊號。

事故背景：BRUSDT 全數掛單被 -4014 拒絕 1.5 天，狀態仍是 running、無告警。
"""

from __future__ import annotations

import unittest
from unittest import mock

import run_multiple_grids as rmg
from core.adapters.exchanges.adapters.binance_rest import BinanceRest


class FakeLogger:
    def __init__(self):
        self.criticals = []

    def critical(self, message, *args, **kwargs):
        self.criticals.append(message)

    def debug(self, *args, **kwargs):
        pass


class OrderHealthAlertTests(unittest.TestCase):
    def setUp(self):
        rmg._ORDER_ALERT_AT.clear()

    def test_no_alert_while_orders_are_alive(self):
        logger = FakeLogger()
        with mock.patch.object(
            BinanceRest, "get_rejection_stats",
            return_value={"consecutive": 99, "total": 99, "last_code": "-4014", "last_message": "x"},
        ):
            rmg._emit_order_health_alert("cfg", "bot-1", "BR/USDT:USDT", 2, logger)
        self.assertEqual(logger.criticals, [])

    def test_alert_when_zero_orders_and_consecutive_rejections(self):
        logger = FakeLogger()
        with mock.patch.object(
            BinanceRest, "get_rejection_stats",
            return_value={"consecutive": 12, "total": 12, "last_code": "-4014", "last_message": "tick"},
        ), mock.patch("core.infrastructure.database.bot_events.emit_bot_event") as emit:
            rmg._emit_order_health_alert("cfg", "bot-1", "BR/USDT:USDT", 0, logger)

        self.assertEqual(len(logger.criticals), 1)
        self.assertIn("-4014", logger.criticals[0])
        emit.assert_called_once()

    def test_alert_is_throttled(self):
        logger = FakeLogger()
        with mock.patch.object(
            BinanceRest, "get_rejection_stats",
            return_value={"consecutive": 12, "total": 12, "last_code": "-4014", "last_message": "tick"},
        ), mock.patch("core.infrastructure.database.bot_events.emit_bot_event"):
            rmg._emit_order_health_alert("cfg", "bot-1", "BR/USDT:USDT", 0, logger)
            rmg._emit_order_health_alert("cfg", "bot-1", "BR/USDT:USDT", 0, logger)

        self.assertEqual(len(logger.criticals), 1, "5 分鐘內不得重複告警")


class PositionDriftAlertTests(unittest.TestCase):
    def setUp(self):
        rmg._POSITION_ALERT_AT.clear()

    def test_no_alert_within_tolerance(self):
        logger = FakeLogger()
        with mock.patch("core.infrastructure.database.bot_events.emit_bot_event") as emit:
            rmg._emit_position_drift_alert(
                "cfg", "bot-1", "BR/USDT:USDT",
                {"grid_position": 100, "account_position": 110},
                order_amount=11, logger=logger,
            )
        self.assertEqual(logger.criticals, [])
        emit.assert_not_called()

    def test_alert_when_drift_exceeds_two_orders(self):
        logger = FakeLogger()
        with mock.patch("core.infrastructure.database.bot_events.emit_bot_event") as emit:
            rmg._emit_position_drift_alert(
                "cfg", "bot-1", "BR/USDT:USDT",
                {"grid_position": 4635, "account_position": 5139},
                order_amount=11, logger=logger,
            )
        self.assertEqual(len(logger.criticals), 1)
        self.assertIn("持倉漂移", logger.criticals[0])
        emit.assert_called_once()

    def test_alert_is_throttled(self):
        logger = FakeLogger()
        with mock.patch("core.infrastructure.database.bot_events.emit_bot_event"):
            for _ in range(2):
                rmg._emit_position_drift_alert(
                    "cfg", "bot-1", "BR/USDT:USDT",
                    {"grid_position": 4635, "account_position": 5139},
                    order_amount=11, logger=logger,
                )
        self.assertEqual(len(logger.criticals), 1)


if __name__ == "__main__":
    unittest.main()
