import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, FIX, ROOT

ap = load("trader-autopilot", "autopilot")

NOW = "2026-10-19 10:20"


class FakeBroker:
    """A paper account in memory: records what would be sent."""

    def __init__(self, positions=None, closed=None, account="PA1234567", fail=False):
        self.sent, self.canceled = [], []
        self._positions = positions or {}
        self._closed = closed or []
        self._account = account
        self.fail = fail

    def account(self):
        return {"account_number": self._account}

    def clock(self):
        return {"is_open": True}

    def positions(self):
        return dict(self._positions)

    def open_orders(self, symbol):
        return [{"id": "leg-%s" % symbol}]

    def closed_orders(self, after_iso):
        return self._closed

    def cancel(self, order_id):
        self.canceled.append(order_id)

    def submit(self, body):
        if self.fail:
            raise ap.AutopilotError("HTTP 403 insufficient buying power")
        self.sent.append(body)
        return {"id": "ord-%d" % len(self.sent), "status": "accepted"}


def config(**autopilot):
    with open(os.path.join(ROOT, "trader-budget", "config.example.json")) as fh:
        cfg = json.load(fh)
    block = {"enabled": True, "symbols": ["TSLA"], "target": "1R", "replays": 500}
    block.update(autopilot)
    cfg["autopilot"] = block
    return cfg


class Autopilot(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.off = os.path.join(self.dir, "off")
        os.makedirs(os.path.join(self.dir, "trader"))
        os.makedirs(self.off)
        shutil.copy(os.path.join(FIX, "trader-entry", "TSLA-2026-10-19-5m.json"), os.path.join(self.off, "TSLA.json"))
        shutil.copy(os.path.join(FIX, "trader-timemachine", "TSLA-60d5m.json.gz"), self.off)

    def tearDown(self):
        shutil.rmtree(self.dir)

    def write_cfg(self, cfg):
        with open(os.path.join(self.dir, "trader", "config.json"), "w") as fh:
            json.dump(cfg, fh)

    def pilot(self, broker, now=NOW, cfg=None):
        self.write_cfg(cfg or config())
        tools = ap.Tools(self.dir, now=now, offline=self.off)
        return ap.Autopilot(self.dir, broker, tools, now_utc=ap.ticket.parse_now(now))

    def quiet(self, fn):
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            r = fn()
        return r, out.getvalue()

    def broker_log(self):
        with open(os.path.join(self.dir, "trader", "state", "broker.json")) as fh:
            return json.load(fh)["orders"]

    # ------------------------------------------------------------ the reel's trade, unattended

    def test_places_the_reel_bracket_on_paper(self):
        b = FakeBroker()
        r, _ = self.quiet(self.pilot(b).tick)
        self.assertEqual(r, "open")
        self.assertEqual(len(b.sent), 1)
        body = b.sent[0]
        self.assertEqual(body["symbol"], "TSLA")
        self.assertEqual(body["qty"], "8")
        self.assertEqual(body["order_class"], "bracket")
        self.assertEqual(body["take_profit"], {"limit_price": "370.04"})
        self.assertEqual(body["stop_loss"], {"stop_price": "358.32"})
        self.assertTrue(body["client_order_id"].startswith("autopilot-2026-10-19-"))
        log = self.broker_log()
        self.assertEqual([r["status"] for r in log], ["approved", "placed"])
        self.assertEqual(log[-1]["max_loss"], 46.88)
        self.assertEqual(log[-1]["broker_order_id"], "ord-1")

    def test_does_not_trade_the_same_symbol_twice_a_day(self):
        b = FakeBroker()
        p = self.pilot(b)
        self.quiet(p.tick)
        self.quiet(p.tick)
        self.assertEqual(len(b.sent), 1)

    def test_time_machine_gate_drops_a_weak_edge(self):
        b = FakeBroker()
        _, out = self.quiet(self.pilot(b, cfg=config(min_edge=0.5)).tick)
        self.assertEqual(b.sent, [])
        self.assertIn("min_edge", out)

    def test_stale_breakout_is_not_chased(self):
        b = FakeBroker()
        _, out = self.quiet(self.pilot(b, now="2026-10-19 10:45").tick)
        self.assertEqual(b.sent, [])
        self.assertIn("min old", out)

    def test_risk_block_stops_the_order(self):
        cfg = config()
        cfg["max_loss_per_trade_usd"] = 5   # 1 share risks 5.86: size.py refuses, nothing is sent
        b = FakeBroker()
        self.quiet(self.pilot(b, cfg=cfg).tick)
        self.assertEqual(b.sent, [])

    def test_failed_submit_is_logged_and_not_retried(self):
        b = FakeBroker(fail=True)
        p = self.pilot(b)
        self.quiet(p.tick)
        self.assertEqual(self.broker_log()[-1]["status"], "failed")
        b.fail = False
        self.quiet(p.tick)
        self.assertEqual(b.sent, [])

    # ------------------------------------------------------------ out by the close

    def test_flattens_at_flat_by(self):
        b = FakeBroker()
        p = self.pilot(b)
        self.quiet(p.tick)
        b._positions = {"TSLA": {"qty": "8", "side": "long", "avg_entry_price": "364.20"}}
        later = self.pilot(b, now="2026-10-19 15:56")
        r, _ = self.quiet(later.tick)
        self.assertEqual(r, "flat")
        self.assertEqual(b.canceled, ["leg-TSLA"])
        sell = b.sent[-1]
        self.assertEqual((sell["symbol"], sell["side"], sell["qty"], sell["type"]), ("TSLA", "sell", "8", "market"))

    def test_records_fills_once(self):
        closed = [{"id": "ord-1", "symbol": "TSLA", "side": "buy", "filled_qty": "8",
                   "filled_avg_price": "364.20", "legs": [
                       {"id": "tp-1", "symbol": "TSLA", "side": "sell", "filled_qty": "8",
                        "filled_avg_price": "370.04"},
                       {"id": "sl-1", "symbol": "TSLA", "side": "sell", "filled_qty": "0"}]},
                  {"id": "not-ours", "symbol": "AAPL", "side": "buy", "filled_qty": "1",
                   "filled_avg_price": "200"}]
        b = FakeBroker(closed=closed)
        p = self.pilot(b)
        self.quiet(p.tick)                       # places ord-1
        self.quiet(p.tick)                       # sync
        self.quiet(p.tick)                       # sync again: nothing new
        st = p.state()
        self.assertEqual(st["fills"], ["ord-1", "tp-1"])
        with open(os.path.join(self.dir, "trader", "portfolio.json")) as fh:
            pf = json.load(fh)
        self.assertNotIn("AAPL", json.dumps(pf))

    # ------------------------------------------------------------ safety

    def test_refuses_without_paper(self):
        cfg = config()
        cfg["paper"] = False
        with self.assertRaises(ap.AutopilotError):
            self.pilot(FakeBroker(), cfg=cfg).tick()

    def test_refuses_when_disabled(self):
        with self.assertRaises(ap.AutopilotError):
            self.pilot(FakeBroker(), cfg=config(enabled=False)).tick()

    def test_refuses_short_or_margin(self):
        cfg = config()
        cfg["allow_short"] = True
        with self.assertRaises(ap.AutopilotError):
            self.pilot(FakeBroker(), cfg=cfg).tick()

    def test_refuses_a_non_paper_account(self):
        with self.assertRaises(ap.AutopilotError):
            self.quiet(self.pilot(FakeBroker(account="123456789")).tick)

    def test_host_is_the_paper_host(self):
        self.assertEqual(ap.PAPER_HOST, "https://paper-api.alpaca.markets")
        with open(os.path.join(ROOT, "trader-autopilot", "autopilot.py")) as fh:
            src = fh.read()
        self.assertNotIn("://api.alpaca.markets", src)

    def test_no_keys_no_broker(self):
        saved = {k: os.environ.pop(k, None) for k in
                 ("ALPACA_API_KEY", "ALPACA_SECRET_KEY", "APCA_API_KEY_ID", "APCA_API_SECRET_KEY")}
        try:
            with self.assertRaises(ap.AutopilotError):
                ap.AlpacaPaper()
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
