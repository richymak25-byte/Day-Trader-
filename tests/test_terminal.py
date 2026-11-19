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
    """Like the Pine script: the lock follows the day's REALIZED P&L."""
    C = {"risk_pct_per_trade": 0.5, "daily_loss_lock_pct": 2.0, "daily_win_lock_pct": 4.0}

    @staticmethod
    def doc(*pnls):
        now = T.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        return {"fills": [{"at": now, "pnl": x} for x in pnls]}

    def test_risk_is_half_a_percent_of_the_day_open(self):
        d = T.day_state(self.doc(), 10000, self.C)
        self.assertEqual((d["risk_per_trade"], d["loss_limit"], d["win_limit"]), (50.0, 200.0, 400.0))
        self.assertIsNone(d["lock"])

    def test_open_losses_do_not_lock(self):
        doc = self.doc()
        T.day_state(doc, 10000, self.C)
        self.assertIsNone(T.day_state(doc, 9500, self.C)["lock"])      # equity down, nothing realized

    def test_loss_lock_trips_and_stays(self):
        doc = self.doc()
        T.day_state(doc, 10000, self.C)
        doc["fills"] += self.doc(-120, -85)["fills"]
        d = T.day_state(doc, 9795, self.C)
        self.assertEqual(d["lock"]["kind"], "loss")
        doc["fills"] += self.doc(500)["fills"]                          # a win afterwards does not unlock
        self.assertEqual(T.day_state(doc, 10300, self.C)["lock"]["kind"], "loss")
        self.assertIn("perte", T.lock_message(d))

    def test_win_lock_trips(self):
        doc = self.doc()
        T.day_state(doc, 10000, self.C)
        doc["fills"] += self.doc(399)["fills"]
        self.assertIsNone(T.day_state(doc, 10399, self.C)["lock"])
        doc["fills"] += self.doc(1)["fills"]
        self.assertEqual(T.day_state(doc, 10400, self.C)["lock"]["kind"], "win")

    def test_new_day_resets(self):
        doc = {"fills": [], "day": {"date": "2000-01-03", "start_equity": 10000, "lock": {"kind": "loss", "pnl": -200}}}
        d = T.day_state(doc, 9800, self.C)
        self.assertIsNone(d["lock"])
        self.assertEqual(d["start_equity"], 9800)


ML = load("terminal", "mindlock")


def bar(t, o, h, l, c):
    return {"t": t, "o": o, "h": h, "l": l, "c": c}


T0 = 1767628800        # 2026-01-05 16:00 UTC, 11:00 New York (in session)


def series(closes, start=T0, step=1800):
    out, prev = [], closes[0]
    for k, c in enumerate(closes):
        out.append(bar(start + k * step, prev, max(prev, c) + 0.2, min(prev, c) - 0.2, c))
        prev = c
    return out


class Mindlock(unittest.TestCase):
    def test_defaults_match_the_pine_script(self):
        p = ML.params()
        self.assertEqual((p["EMA20Length"], p["EMA50Length"], p["EMA100Length"], p["RSILength"], p["RSIMid"]),
                         (20, 50, 100, 14, 50.0))
        self.assertEqual((p["EntryMethod"], p["Tolerance"], p["PendingExpiryBars"], p["StopLossMax"], p["TakeProfit"]),
                         ("BODY", 5.0, 10, 100, 300))
        self.assertEqual((p["StructLookback"], p["StructBuffer"], p["BreakEvenTrigger"]), (10, 2.0, 200))
        self.assertEqual((p["DailyLossPercent"], p["DailyProfitPercent"], p["Session"]), (2.0, 4.0, "0930-2145"))

    def test_universal_unit_like_the_script(self):
        self.assertEqual(ML.unit_size("EURUSD=X", 1.09), 0.0001)          # forex pip
        self.assertEqual(ML.unit_size("USDJPY=X", 150.0), 0.01)
        self.assertEqual(ML.unit_size("GC=F", 3600.0), 0.1)               # futures tick
        self.assertEqual(ML.unit_size("ES=F", 6000.0), 0.25)
        self.assertAlmostEqual(ML.unit_size("AAPL", 349.0), 0.1)          # 10 x min tick
        self.assertAlmostEqual(ML.unit_size("AAPL", 349.0, "Points"), 1.0)
        self.assertEqual(ML.unit_size("BTC-USD", 62000.0, override=5), 5)

    def test_crossover_signal_needs_ema100_and_rsi(self):
        p = ML.for_asset(ML.params({"EMA20Length": 3, "EMA50Length": 6, "EMA100Length": 10, "RSILength": 5}), "GC=F", 100)
        closes = [100 + k for k in range(20)] + [119 - 2 * k for k in range(8)] + [104 + 3 * k for k in range(8)]
        bars = series(closes)
        ind = ML.indicators(bars, p)
        sigs = [(i, ML.signal_at(bars, ind, i, p)) for i in range(len(bars))]
        buys = [i for i, s in sigs if s == "buy"]
        self.assertTrue(buys)
        i = buys[0]
        self.assertTrue(ind["e20"][i] > ind["e50"][i] and ind["e20"][i - 1] <= ind["e50"][i - 1])
        self.assertGreater(bars[i]["c"], ind["e100"][i])

    def test_structural_stop_is_capped(self):
        p = ML.for_asset(ML.params(), "GC=F", 2000)            # unit 0.1: max SL 10 $, buffer 0.2 $
        bars = [bar(T0 + k * 1800, 2000, 2001, 1990 if k == 0 else 1999, 2000) for k in range(10)]
        self.assertAlmostEqual(ML.struct_sl("buy", 2000.0, bars, 9, p), 1990.0)       # 1990 - 0.2 -> capped at 10 $
        bars[0]["l"] = 1999
        self.assertAlmostEqual(ML.struct_sl("buy", 2000.0, bars, 9, p), 1998.8)       # 1999 - 0.2
        self.assertAlmostEqual(ML.target_of("buy", 2000.0, p), 2030.0)

    def test_body_limit_never_worse_than_close(self):
        p = ML.for_asset(ML.params(), "GC=F", 2000)
        bars = [bar(T0, 1990, 2005, 1989, 2000)]
        ind = {"e20": [1995], "e50": [1990]}
        self.assertAlmostEqual(ML.entry_price("buy", bars, ind, 0, p), 1990.5)        # body low + 5 x 0.1
        self.assertAlmostEqual(ML.entry_price("sell", bars, ind, 0, p), 2000.0)       # body high - 0.5, max with close

    def test_engine_limit_fill_stop_and_break_even(self):
        p = ML.for_asset(ML.params({"UseSession": False}), "GC=F", 2000)
        bars = [bar(T0 + k * 1800, 2000, 2001, 1999, 2000) for k in range(5)]
        eng = ML.Engine(bars, p, balance=100000)
        eng.st["pending"] = {"side": "buy", "t": bars[0]["t"], "age": 0, "ote": None, "price": 1999.5}
        eng.step(1)                                            # low 1999 <= 1999.5: filled at the limit
        pos = eng.st["pos"]
        self.assertEqual(pos["entry"], 1999.5)
        self.assertAlmostEqual(pos["sl"], 1998.8)              # lowest low 1999 - 0.2
        self.assertAlmostEqual(pos["tp"], 2029.5)
        bars[2] = bar(bars[2]["t"], 2000, 2020, 1999.5, 2015)  # +20.5 >= 200 units x 0.1 -> break-even
        eng.step(2)
        self.assertTrue(eng.st["pos"]["be"])
        bars[3] = bar(bars[3]["t"], 2010, 2011, 1990, 1995)
        eng.step(3)
        tr = eng.st["trades"][0]
        self.assertEqual((tr["reason"], tr["exit"]), ("BE", 1999.5))

    def test_daily_lock_on_realized_loss(self):
        p = ML.for_asset(ML.params({"UseSession": False, "RiskPercent": 5, "MaxLeverage": 1000}), "GC=F", 2000)
        bars = [bar(T0 + k * 1800, 2000, 2001, 1999, 2000) for k in range(4)]
        eng = ML.Engine(bars, p, balance=10000)
        eng.step(0)
        eng._open("buy", 2000.0, 0, "test")
        bars[1] = bar(bars[1]["t"], 1980, 1981, 1970, 1975)  # gaps under the stop: filled at the open
        eng.step(1)
        self.assertEqual(eng.st["trades"][0]["exit"], 1980)
        self.assertTrue(eng.st["locked"])                      # realized loss > 2 % of the day's equity

    def test_lot_size_and_leverage_cap(self):
        p = ML.for_asset(ML.params(), "GC=F", 3600)
        self.assertEqual(ML.lots_for(100000, 10.0, p, 3600), 0.5)          # 500 $ / (10 $ x 100 oz)
        self.assertEqual(ML.lots_for(100000, 0.5, p, 3600), 8.33)          # capped: 30 x 100k / 360k
        self.assertEqual(ML.lots_for(10000, 10.0, ML.params({"UseRiskPercent": False}), None), 1.0)

    def test_session_new_york(self):
        p = ML.params()
        self.assertTrue(ML.in_session(T0, p))                              # 11:00 NY
        self.assertFalse(ML.in_session(T0 - 6 * 3600, p))                  # 05:00 NY

    def test_h4_from_h1(self):
        h1 = [{"t": 14400 + 3600 * k, "o": k, "h": k + 1, "l": k - 1, "c": k + 0.5} for k in range(8)]
        h4 = ML.aggregate(h1, 14400)
        self.assertEqual(len(h4), 2)
        self.assertEqual((h4[0]["o"], h4[0]["h"], h4[0]["l"], h4[0]["c"]), (0, 4, -1, 3.5))

    def test_backtest_and_csv(self):
        rows = []
        for k in range(400):
            c = 2000 + 30 * __import__("math").sin(k / 15.0)
            rows.append("2026.01.%02d\t%02d:%02d\t%.2f\t%.2f\t%.2f\t%.2f\t100"
                        % (5 + k // 48, (k % 48) // 2, 30 * (k % 2), c, c + 1, c - 1, c + 0.3))
        bars = ML.parse_csv("<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\n" + "\n".join(rows))
        self.assertEqual(len(bars), 400)
        r = ML.backtest(bars, ML.for_asset(ML.params({"UseSession": False}), "GC=F", 2000.0), 100000)
        self.assertGreater(r["signals"], 0)
        self.assertEqual(r["trades"], r["wins"] + r["losses"])


if __name__ == "__main__":
    unittest.main()
