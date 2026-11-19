import os
import tempfile
import unittest

from _load import load

T = load("terminal", "server")


class Indicators(unittest.TestCase):
    def test_ema_starts_at_first_value_and_follows(self):
        e = T.ema([10, 10, 10, 20], 3)
        self.assertEqual(e[:3], [10, 10, 10])
        self.assertAlmostEqual(e[3], 15.0)

    def test_vwap_weights_by_volume(self):
        bars = [{"h": 10, "l": 10, "c": 10, "v": 1}, {"h": 20, "l": 20, "c": 20, "v": 3}]
        self.assertAlmostEqual(T.vwap(bars), 17.5)

    def test_sentiment_is_clamped(self):
        self.assertGreater(T.sentiment("Apple beats estimates, shares surge to record"), 0)
        self.assertLess(T.sentiment("Tesla shares plunge after downgrade and recall"), 0)
        self.assertEqual(T.sentiment("Company holds annual meeting"), 0)


class Paper(unittest.TestCase):
    def test_buy_then_sell_books_the_pnl(self):
        doc = {"start_cash": 1000, "cash": 1000, "positions": {}, "orders": [], "fills": []}
        T._fill(doc, "ABC", "buy", 10, 50.0, "test")
        self.assertEqual(doc["cash"], 500)
        T._fill(doc, "ABC", "sell", 10, 55.0, "test")
        self.assertEqual(doc["cash"], 1050)
        self.assertEqual(doc["fills"][0]["pnl"], 50.0)
        self.assertNotIn("ABC", doc["positions"])

    def test_no_short_and_no_margin(self):
        doc = {"start_cash": 100, "cash": 100, "positions": {}, "orders": [], "fills": []}
        with self.assertRaises(T.ApiError):
            T._fill(doc, "ABC", "buy", 10, 50.0, "test")
        with self.assertRaises(T.ApiError):
            T._fill(doc, "ABC", "sell", 1, 50.0, "test")

    def test_plan_uses_trader_exit(self):
        p = T.api_plan({"entry": 100, "stop_rule": "pct", "stop_param": 1, "target_r": 2})
        self.assertEqual((p["stop"], p["target"], p["risk"]), (99.0, 102.0, 1.0))
        with self.assertRaises(T.ApiError):
            T.api_plan({"entry": 100, "stop_rule": "price", "stop_param": 101})


if __name__ == "__main__":
    unittest.main()


class DailyLock(unittest.TestCase):
    C = {"risk_pct_per_trade": 0.5, "daily_loss_lock_pct": 1.0, "daily_win_lock_pct": 1.0}

    def test_risk_is_half_a_percent_of_the_day_open(self):
        doc = {}
        d = T.day_state(doc, 10000, self.C)
        self.assertEqual((d["risk_per_trade"], d["loss_limit"], d["win_limit"]), (50.0, 100.0, 100.0))
        self.assertIsNone(d["lock"])

    def test_loss_lock_trips_and_stays(self):
        doc = {}
        T.day_state(doc, 10000, self.C)
        d = T.day_state(doc, 9899, self.C)
        self.assertEqual(d["lock"]["kind"], "loss")
        d = T.day_state(doc, 10500, self.C)          # a bounce does not unlock the day
        self.assertEqual(d["lock"]["kind"], "loss")
        self.assertIn("perte", T.lock_message(d))

    def test_win_lock_trips(self):
        doc = {}
        T.day_state(doc, 10000, self.C)
        self.assertIsNone(T.day_state(doc, 10099, self.C)["lock"])
        self.assertEqual(T.day_state(doc, 10100, self.C)["lock"]["kind"], "win")

    def test_new_day_resets(self):
        doc = {"day": {"date": "2000-01-03", "start_equity": 10000, "lock": {"kind": "loss", "pnl": -200}}}
        d = T.day_state(doc, 9800, self.C)
        self.assertIsNone(d["lock"])
        self.assertEqual(d["start_equity"], 9800)
