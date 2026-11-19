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


ML = load("terminal", "mindlock")


def bar(t, o, h, l, c):
    return {"t": t, "o": o, "h": h, "l": l, "c": c}


class Mindlock(unittest.TestCase):
    def test_default_params_match_the_ea(self):
        p = ML.for_asset(ML.params(), "GC=F", 3600.0)
        self.assertEqual((p["FastEMA"], p["SlowEMA"], p["RSIPeriod"], p["StopLossPips"], p["TakeProfitPips"]),
                         (20, 50, 14, 150, 600))
        self.assertEqual((p["RSIUpper"], p["RSILower"], p["PipSize"], p["ContractSize"], p["RiskPercent"]),
                         (75.0, 25.0, 0.1, 100.0, 0.5))
        self.assertEqual(ML.levels("buy", 2000.0, p), (1985.0, 2060.0))
        self.assertEqual(ML.levels("sell", 2000.0, p), (2015.0, 1940.0))

    def test_lot_size_risks_half_a_percent(self):
        p = ML.for_asset(ML.params(), "GC=F", 3600.0)
        self.assertEqual(ML.lots_for(100000, p), 0.33)        # 500 $ / (15 $ x 100 oz)
        self.assertEqual(ML.lots_for(10000, p), 0.03)
        self.assertEqual(ML.lots_for(10000, ML.params({"UseRiskPercent": False})), 1.0)

    def test_universal_pips_and_contracts(self):
        eur = ML.for_asset(ML.params(), "EURUSD=X", 1.0972)
        self.assertEqual((eur["PipSize"], eur["ContractSize"]), (0.0001, 100000.0))
        self.assertEqual(ML.levels("buy", 1.1, eur), (1.085, 1.16))
        jpy = ML.for_asset(ML.params(), "USDJPY=X", 150.2)
        self.assertEqual(jpy["PipSize"], 0.01)
        btc = ML.for_asset(ML.params(), "BTC-USD", 62000.0)
        self.assertEqual((btc["PipSize"], btc["ContractSize"]), (1.0, 1.0))
        eu_stock = ML.for_asset(ML.params(), "MC.PA", 390.0, usd_rate=1.1)
        self.assertAlmostEqual(ML.risk_per_lot(eu_stock), 150 * 0.01 * 1 * 1.1)
        self.assertEqual(ML.params({"XAUUSD_PipSize": 0.1})["PipSize"], 0.1)    # the EA's input name

    def test_h4_from_h1(self):
        h1 = [{"t": 14400 + 3600 * k, "o": k, "h": k + 1, "l": k - 1, "c": k + 0.5} for k in range(8)]
        h4 = ML.aggregate(h1, 14400)
        self.assertEqual(len(h4), 2)
        self.assertEqual((h4[0]["o"], h4[0]["h"], h4[0]["l"], h4[0]["c"]), (0, 4, -1, 3.5))

    def test_buy_signal_needs_every_filter(self):
        p = ML.for_asset(ML.params({"FastEMA": 3, "SlowEMA": 5, "RSIPeriod": 3}), "TEST", 100.0)
        closes = [100 + i * 0.5 + (0.8 if i % 2 else 0) for i in range(30)]
        bars = [bar(i * 1800, c - 0.2, c + 0.3, c - 0.6, c) for i, c in enumerate(closes)]
        ind = ML.indicators(bars, p)
        i = len(bars) - 1
        f = ind["fast"][i]
        bars[i] = bar(i * 1800, f - 0.1, f + 0.4, f - 0.3, f + 0.3)       # bull candle touching EMA fast
        ind = ML.indicators(bars, p)
        ch = ML.checks(bars, ind, i, p)
        names = {n: ok for n, ok, _ in ch["buy"]}
        self.assertTrue(names["Bougie haussière"])
        self.assertEqual(ML.signal_at(bars, ind, i, p) == "buy", all(names.values()))

    def test_stop_counts_first_when_both_are_hit(self):
        p = ML.for_asset(ML.params(), "GC=F", 2000.0)
        pos = {"side": "buy", "entry": 2000.0, "sl": 1985.0, "tp": 2060.0, "lots": 1.0, "t": 0}
        self.assertEqual(ML.manage(pos, bar(1, 2000, 2070, 1980, 2000), p), (1985.0, "stop"))
        tr = ML.close_trade(pos, 2060.0, 2, "cible", p)
        self.assertEqual((tr["pips"], tr["pnl"]), (600.0, 6000.0))

    def test_backtest_and_csv(self):
        csv = "\n".join("2026.01.%02d\t%02d:%02d\t%.2f\t%.2f\t%.2f\t%.2f\t100"
                        % (1 + k // 48, (k % 48) // 2, 30 * (k % 2), 2000 + k, 2001 + k, 1999 + k, 2000.5 + k)
                        for k in range(200))
        bars = ML.parse_csv("<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\n" + csv)
        self.assertEqual(len(bars), 200)
        r = ML.backtest(bars, ML.for_asset(ML.params(), "GC=F", 2000.0), 100000)
        self.assertEqual(r["bars"], 200)
        self.assertGreaterEqual(r["trades"], 0)
