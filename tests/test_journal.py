"""MINDLOCK Journal: CSV detection, buy/sell pairing, P&L and the built dashboard."""
import json
import os
import shutil
import tempfile
import unittest

from _load import ROOT, load

sync = load("mindlock-journal", "sync")


class Parsing(unittest.TestCase):
    def test_numbers_in_every_broker_style(self):
        self.assertEqual(sync.parse_num("1,234.50"), 1234.5)
        self.assertEqual(sync.parse_num("1.234,50"), 1234.5)
        self.assertEqual(sync.parse_num("-12,5"), -12.5)
        self.assertEqual(sync.parse_num("$(200.00)"), -200.0)
        self.assertEqual(sync.parse_num("1,000"), 1000.0)
        self.assertIsNone(sync.parse_num(""))

    def test_times(self):
        self.assertEqual(sync.parse_time("2026-11-02T09:46:00-05:00"), "2026-11-02 09:46:00")
        self.assertEqual(sync.parse_time("2026.11.05 14:00:00"), "2026-11-05 14:00:00")
        self.assertEqual(sync.parse_time("11/06/2026 01:50:00 PM"), "2026-11-06 13:50:00")
        self.assertEqual(sync.parse_time("20261104;093510"), "2026-11-04 09:35:10")

    def test_day_or_month_first_is_decided_per_file(self):
        rows = lambda *d: "time,symbol,side,qty,price\n" + "".join("%s 10:00,AAPL,buy,1,1\n" % x for x in d)
        self.assertEqual(sync.parse_csv(rows("05/11/2026"))[0][0]["time"], "2026-11-05 10:00:00")  # French default
        self.assertEqual(sync.parse_csv(rows("05/11/2026", "05/13/2026"))[0][0]["time"], "2026-05-11 10:00:00")
        self.assertEqual(sync.parse_csv(rows("05/11/2026", "25/11/2026"))[0][0]["time"], "2026-11-05 10:00:00")
        self.assertEqual(sync.parse_csv(rows("05.11.2026"))[0][0]["time"], "2026-11-05 10:00:00")

    def test_sides(self):
        self.assertEqual(sync.parse_side("BOT"), "buy")
        self.assertEqual(sync.parse_side("Achat"), "buy")
        self.assertEqual(sync.parse_side("SLD"), "sell")
        self.assertEqual(sync.parse_side("Sell Short"), "sell")
        self.assertIsNone(sync.parse_side("balance"))

    def test_futures_multipliers(self):
        self.assertEqual(sync.multiplier("NQZ5"), 20.0)
        self.assertEqual(sync.multiplier("MNQZ5"), 2.0)
        self.assertEqual(sync.multiplier("ES"), 50.0)
        self.assertEqual(sync.multiplier("ESPR"), 1.0)  # a stock ticker, not the future
        self.assertEqual(sync.multiplier("TSLA"), 1.0)
        self.assertEqual(sync.multiplier("NQZ5", {"NQ": 10}), 10.0)


class Pairing(unittest.TestCase):
    def test_scale_in_then_flip_to_short(self):
        text = ("Date,Time,Symbol,Side,Qty,Price,Commission\n"
                "2026-11-04,09:35:10,AAPL,BUY,10,230.00,-1.00\n"
                "2026-11-04,09:40:00,AAPL,BUY,10,231.00,-1.00\n"
                "2026-11-04,10:00:00,AAPL,SELL,25,233.00,-1.25\n"
                "2026-11-04,10:20:00,AAPL,BUY,5,232.00,-0.50\n")
        execs, closed = sync.parse_csv(text)
        self.assertEqual((len(execs), len(closed)), (4, 0))
        t = sync.pair(execs)
        self.assertEqual(len(t), 2)
        self.assertEqual((t[0]["side"], t[0]["qty"], t[0]["entry"], t[0]["exit"]), ("long", 20, 230.5, 233.0))
        self.assertEqual(t[0]["pnl"], 46.75)  # 20 x 2.50 - 3.25 fees
        self.assertEqual((t[1]["side"], t[1]["qty"], t[1]["pnl"]), ("short", 5, 4.5))
        self.assertEqual(t[1]["closeTime"], "2026-11-04 10:20:00")

    def test_open_position_stays_open(self):
        execs, _ = sync.parse_csv("time,symbol,side,qty,price\n2026-11-04 10:00,TSLA,buy,3,360\n")
        t = sync.pair(execs)
        self.assertEqual((t[0]["status"], t[0]["pnl"]), ("open", None))

    def test_futures_point_value(self):
        execs, _ = sync.parse_csv("time;symbol;action;qty;price\n"
                                  "2026-11-05 09:45:00;MNQZ5;Sell;2;21000,25\n"
                                  "2026-11-05 09:59:00;MNQZ5;Buy;2;20990\n")
        t = sync.pair(execs)
        self.assertEqual((t[0]["side"], t[0]["pnl"]), ("short", 41.0))  # 10.25 pts x 2 x $2

    def test_mt5_closed_trade_history(self):
        text = ("Open Time;Symbol;Type;Volume;Open Price;Close Time;Close Price;Commission;Swap;Profit\n"
                "2026.11.05 14:00:00;EURUSD;sell;1.00;1,0850;2026.11.05 15:30:00;1,0830;-7,00;0;200,00\n")
        execs, closed = sync.parse_csv(text)
        self.assertEqual(len(execs), 0)
        self.assertEqual((closed[0]["side"], closed[0]["pnl"], closed[0]["fees"]), ("short", 193.0, 7.0))

    def test_ctrader_history_export(self):
        text = ("ID,Symbol,Opening Direction,Opening Time (UTC+1),Closing Time (UTC+1),Entry Price,Closing Price,"
                "Closing Quantity,Commission,Gross USD,Net USD\n"
                "123,XAUUSD,Sell,05/11/2026 14:00:00,05/11/2026 15:10:00,2410.50,2400.50,0.50 Lots,-3.50,500.00,496.50\n")
        _, closed = sync.parse_csv(text)
        t = closed[0]
        self.assertEqual((t["symbol"], t["side"], t["qty"], t["entry"], t["exit"]), ("XAUUSD", "short", 0.5, 2410.5, 2400.5))
        self.assertEqual((t["pnl"], t["openTime"], t["closeTime"]), (496.5, "2026-11-05 14:00:00", "2026-11-05 15:10:00"))

    def test_match_trader_history_export(self):
        text = ("ID,Open time,Symbol,Side,Volume,Open price,Close time,Close price,Swap,Commission,Profit\n"
                "9,2026-11-05 09:00:00,US100,BUY,1.00,21000.0,2026-11-05 09:30:00,21050.0,0,-5.00,50.00\n")
        _, closed = sync.parse_csv(text)
        self.assertEqual((closed[0]["side"], closed[0]["pnl"], closed[0]["fees"]), ("long", 45.0, 5.0))

    def test_tradovate_sold_first_is_a_short(self):
        text = ("symbol,qty,buyPrice,sellPrice,pnl,boughtTimestamp,soldTimestamp\n"
                "NQZ5,1,21010.00,21000.00,$(200.00),11/06/2026 09:50:00,11/06/2026 09:45:00\n")
        _, closed = sync.parse_csv(text)
        t = closed[0]
        self.assertEqual((t["side"], t["entry"], t["exit"], t["pnl"]), ("short", 21000.0, 21010.0, -200.0))
        self.assertEqual(t["openTime"], "2026-11-06 09:45:00")

    def test_trade_id_matches_the_dashboard(self):
        # tradeId() in index.html gives the same ids (checked with node: tc40bf6cc082f5302 for "A")
        self.assertEqual(sync.trade_id("TSLA", "2026-11-02 09:46:00", "long", 364.2),
                         sync.trade_id("TSLA", "2026-11-02 09:46:00", "long", 364.20))
        self.assertEqual(sync.trade_id("A"), "tc40bf6cc082f5302")


class Build(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dir = os.path.join(self.tmp, "trader")
        os.makedirs(os.path.join(self.dir, "state"))
        os.makedirs(os.path.join(self.dir, "journal", "import"))
        with open(os.path.join(self.dir, "state", "broker.json"), "w") as fh:
            json.dump({"orders": [
                {"at": "2026-11-02T09:46:00-05:00", "status": "filled", "symbol": "TSLA", "side": "buy",
                 "qty": "8", "price": 364.20},
                {"at": "2026-11-02T10:00:00-05:00", "status": "approved", "symbol": "TSLA", "side": "sell"},
                {"at": "2026-11-02T10:31:00-05:00", "status": "filled", "symbol": "TSLA", "side": "sell",
                 "qty": "8", "price": 370.04}]}, fh)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_broker_fills_become_a_trade_and_notes_survive_a_resync(self):
        out = sync.build(type("A", (), {"dir": self.dir, "csv": None, "backup": None})())
        path = os.path.join(self.dir, "journal", "trades.json")
        with open(path) as fh:
            data = json.load(fh)
        self.assertEqual(len(data["trades"]), 1)
        t = data["trades"][0]
        self.assertEqual((t["symbol"], t["side"], t["pnl"], t["source"]), ("TSLA", "long", 46.72, "trader-broker"))
        t["notes"], t["setup"] = "clean ORB", "Opening Range Breakout"
        with open(path, "w") as fh:
            json.dump(data, fh)
        sync.build(type("A", (), {"dir": self.dir, "csv": None, "backup": None})())
        with open(path) as fh:
            again = json.load(fh)["trades"][0]
        self.assertEqual((again["notes"], again["setup"]), ("clean ORB", "Opening Range Breakout"))
        with open(out, encoding="utf-8") as fh:
            html = fh.read()
        self.assertNotIn(sync.PLACEHOLDER, html)
        self.assertIn('"clean ORB"', html)
        self.assertIn("<title>MINDLOCK Journal</title>", html)

    def test_backup_file_restores_notes_journal_and_manual_trades(self):
        args = type("A", (), {"dir": self.dir, "csv": None, "backup": None})()
        sync.build(args)
        jdir = os.path.join(self.dir, "journal")
        with open(os.path.join(jdir, "trades.json")) as fh:
            tid = json.load(fh)["trades"][0]["id"]
        backup = {"trades": [{"id": tid, "notes": "from the browser", "rating": 5, "tags": []},
                             {"id": "mmanual1", "symbol": "ES", "side": "long", "qty": 1, "pnl": 125.0,
                              "status": "closed", "openTime": "2026-11-03 10:00:00",
                              "closeTime": "2026-11-03 10:20:00", "source": "manuel"}],
                  "days": {"2026-11-02": {"plan": "ORB only", "mood": 4}},
                  "playbook": [{"id": "pb9", "name": "Gap fill", "rules": ["Gap > 2%"]}],
                  "settings": {"currency": "EUR", "startBalance": 5000, "theme": "light"}}
        with open(os.path.join(jdir, "mindlock-backup.json"), "w") as fh:
            json.dump(backup, fh)
        out = sync.build(args)
        with open(os.path.join(jdir, "trades.json")) as fh:
            data = json.load(fh)
        t = [x for x in data["trades"] if x["id"] == tid][0]
        self.assertEqual((t["notes"], t["rating"], t["pnl"]), ("from the browser", 5, 46.72))
        self.assertEqual(len(data["trades"]), 2)
        self.assertEqual(data["days"]["2026-11-02"]["plan"], "ORB only")
        self.assertEqual(data["settings"], {"currency": "EUR", "startBalance": 5000})
        with open(out, encoding="utf-8") as fh:
            self.assertIn('"Gap fill"', fh.read())

    def test_template_carries_the_placeholder_once(self):
        with open(os.path.join(ROOT, "mindlock-journal", "index.html"), encoding="utf-8") as fh:
            self.assertEqual(fh.read().count(sync.PLACEHOLDER), 1)


if __name__ == "__main__":
    unittest.main()
