"""Python contract for the frontend formatter / create-wizard rules."""

import unittest


def money(value):
    return "—" if value is None else f"${value:,.2f}"


def freshness_text(data_status, last_updated_at):
    if data_status == "unavailable":
        return "尚未同步"
    if data_status == "stale":
        return "資料過期"
    if not last_updated_at:
        return "已同步"
    return "synced"


def can_advance_create_step(step, form, mode, live_confirmation, min_notional_error):
    if step == 1:
        return bool(form.get("symbol") and form.get("exchange"))
    if step == 2:
        return bool(
            form.get("lower_price")
            and form.get("upper_price")
            and form.get("grid_interval")
            and form.get("order_amount")
            and form["lower_price"] < form["upper_price"]
            and not min_notional_error
        )
    return mode != "live" or live_confirmation == "CONFIRM LIVE"


def next_poll_delay(failures, base=15_000, cap=60_000):
    if failures <= 0:
        return base
    return min(cap, base * (2 ** min(failures, 5)))


class FrontendFormatContractTests(unittest.TestCase):
    def test_unknown_money_is_em_dash(self):
        self.assertEqual(money(None), "—")
        self.assertNotEqual(money(0), "—")

    def test_freshness_separates_unavailable_from_stale(self):
        self.assertEqual(freshness_text("unavailable", None), "尚未同步")
        self.assertEqual(freshness_text("stale", 1), "資料過期")

    def test_create_step_validation(self):
        form = {
            "symbol": "BTC/USDT",
            "exchange": "binance",
            "lower_price": 60000,
            "upper_price": 70000,
            "grid_interval": 100,
            "order_amount": 0.001,
        }
        self.assertTrue(can_advance_create_step(1, form, "paper", "", False))
        self.assertFalse(can_advance_create_step(1, {**form, "symbol": ""}, "paper", "", False))
        self.assertTrue(can_advance_create_step(2, form, "paper", "", False))
        self.assertFalse(can_advance_create_step(2, {**form, "lower_price": 80000}, "paper", "", False))
        self.assertFalse(can_advance_create_step(3, form, "live", "yes", False))
        self.assertTrue(can_advance_create_step(3, form, "live", "CONFIRM LIVE", False))

    def test_backoff_caps(self):
        self.assertEqual(next_poll_delay(0), 15_000)
        self.assertEqual(next_poll_delay(2), 60_000)


if __name__ == "__main__":
    unittest.main()
