import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from copytrade import instruments, stats  # noqa: E402
from copytrade.connectors import simulator  # noqa: E402
from copytrade.engine import Engine, floor_step  # noqa: E402
from copytrade.models import Fill  # noqa: E402
from copytrade.risk import RiskTracker, rules_from  # noqa: E402
from copytrade.storage import Store  # noqa: E402


def build(edition, followers):
    """followers: list of settings dicts. Returns (engine, leader runtime, follower runtimes)."""
    simulator.MARKETS.clear()
    simulator.market(edition).frozen = True
    store = Store(":memory:")
    eng = Engine(edition, store)
    d = eng.defaults()
    lead = store.add_account("L", "leader", "sim", {"balance": 50000}, dict(d))
    fids = [store.add_account("F%d" % i, "follower", "sim", f.pop("_params", {"balance": 50000}), dict(d, **f), lead)
            for i, f in enumerate(followers)]
    eng.load()
    deadline = time.time() + 5
    while not all(r.connected for r in eng.rt.values()) and time.time() < deadline:
        time.sleep(0.01)
    eng.copying = True
    eng.tick()   # baseline
    return eng, eng.rt[lead], [eng.rt[i] for i in fids]


def net(r):
    return {p.symbol: p.qty for p in r.conn.positions()}


class Instruments(unittest.TestCase):
    def test_split_future(self):
        self.assertEqual(instruments.split_future("NQZ6"), ("NQ", "Z6"))
        self.assertEqual(instruments.split_future("MNQH27"), ("MNQ", "H27"))
        self.assertEqual(instruments.split_future("6EZ6"), ("6E", "Z6"))
        self.assertEqual(instruments.split_future("M2KZ6"), ("M2K", "Z6"))
        self.assertEqual(instruments.point_value("MESZ6"), 5.0)

    def test_floor_step(self):
        self.assertEqual(floor_step(0.237, 0.01), 0.23)
        self.assertEqual(floor_step(0.3, 0.1), 0.3)


class FuturesCopy(unittest.TestCase):
    def test_multiplier_mapping_and_flatten(self):
        eng, lead, (f1, f2, f3) = build("futures", [
            {"multiplier": 1},
            {"multiplier": 10, "symbol_map": "NQ=MNQ"},
            {"multiplier": 0.5, "invert": True},
        ])
        lead.conn.market_order("NQZ6", 2)
        eng.tick()
        self.assertEqual(net(f1), {"NQZ6": 2})
        self.assertEqual(net(f2), {"MNQZ6": 20})
        self.assertEqual(net(f3), {"NQZ6": -1})
        lead.conn.market_order("NQZ6", -1)   # partial exit
        eng.tick()
        self.assertEqual(net(f1), {"NQZ6": 1})
        self.assertEqual(net(f2), {"MNQZ6": 10})
        self.assertEqual(net(f3), {"NQZ6": -1})   # round(0.5) = 1, stays
        lead.conn.market_order("NQZ6", -3)   # reverse to short 2
        eng.tick()
        self.assertEqual(net(f1), {"NQZ6": -2})
        self.assertEqual(net(f2), {"MNQZ6": -20})
        lead.conn.market_order("NQZ6", 2)
        eng.tick()
        for f in (f1, f2, f3):
            self.assertEqual(net(f), {})

    def test_existing_positions_not_copied_and_manual_close_respected(self):
        simulator.MARKETS.clear()
        eng, lead, (f1,) = build("futures", [{}])
        lead.conn.market_order("ESZ6", 1)
        eng.tick()
        f1.conn.flatten()           # trader closes the follower by hand
        eng.tick()
        self.assertEqual(net(f1), {})  # no re-entry without a leader change

    def test_max_qty_and_disabled(self):
        eng, lead, (f1, f2) = build("futures", [{"max_qty": 3}, {}])
        f2.acct["enabled"] = False
        lead.conn.market_order("ESZ6", 5)
        eng.tick()
        self.assertEqual(net(f1), {"ESZ6": 3})
        self.assertEqual(net(f2), {})
        rows = eng.store.executions()
        self.assertTrue(any(r["status"] == "skipped" and r["follower_id"] == f2.id for r in rows))

    def test_daily_loss_breach_flattens_and_locks(self):
        eng, lead, (f1,) = build("futures", [{"risk": {"daily_loss_limit": 500}}])
        m = simulator.market("futures")
        m.set_price("NQZ6", 21000.0)
        lead.conn.market_order("NQZ6", 1)
        eng.tick()
        self.assertEqual(net(f1), {"NQZ6": 1})
        m.set_price("NQZ6", 20970.0)   # -30 pts * $20 = -600 open loss
        eng.tick()
        self.assertEqual(net(f1), {})
        self.assertEqual(f1.risk_state["lock"], "daily_loss")
        lead.conn.market_order("NQZ6", 1)      # leader adds: follower stays blocked
        eng.tick()
        self.assertEqual(net(f1), {})
        eng.unlock(f1.id)
        self.assertIsNone(f1.risk_state["lock"])


class CfdCopy(unittest.TestCase):
    def test_ticket_lifecycle(self):
        eng, lead, (f1, f2) = build("cfd", [
            {"sizing": "multiplier", "multiplier": 2},
            {"sizing": "fixed", "fixed_lot": 0.05, "symbol_suffix": ".r", "copy_sltp": False},
        ])
        t = lead.conn.market_order("XAUUSD", 0.5, sl=2690, tp=2720)
        eng.tick()
        p1, = f1.conn.positions()
        self.assertEqual((p1.symbol, p1.qty, p1.sl, p1.tp), ("XAUUSD", 1.0, 2690, 2720))
        p2, = f2.conn.positions()
        self.assertEqual((p2.symbol, p2.qty, p2.sl), ("XAUUSD.r", 0.05, 0))
        lead.conn.modify_ticket(t, 2695, 2730)
        eng.tick()
        self.assertEqual((f1.conn.positions()[0].sl, f1.conn.positions()[0].tp), (2695, 2730))
        lead.conn.close_ticket(t, 0.25)   # half
        eng.tick()
        self.assertAlmostEqual(f1.conn.positions()[0].qty, 0.5)
        self.assertEqual(len(f2.conn.positions()), 1)       # 0.05 - 0.02 still above min
        lead.conn.close_ticket(t)
        eng.tick()
        self.assertEqual(f1.conn.positions(), [])
        self.assertEqual(f2.conn.positions(), [])
        self.assertEqual(eng.store.ticket_map(f1.id), {})
        self.assertTrue(f1.conn.closed_trades(0))

    def test_equity_sizing_and_min_lot(self):
        eng, lead, (f1,) = build("cfd", [{"sizing": "equity", "_params": {"balance": 10000}}])
        lead.conn.market_order("EURUSD", -1.0)   # leader 50k, follower 10k -> 0.2
        eng.tick()
        self.assertAlmostEqual(f1.conn.positions()[0].qty, -0.2)
        lead.conn.market_order("EURUSD", 0.01)   # 0.002 lot -> under minimum, skipped
        eng.tick()
        self.assertEqual(len(f1.conn.positions()), 1)


class Risk(unittest.TestCase):
    def test_trailing_intraday_locks_at_start(self):
        tr = RiskTracker()
        rules = rules_from({"risk": {"start_balance": 50000, "max_drawdown": 2500,
                                     "drawdown_type": "trailing_intraday", "lock_offset": 100}})
        st = tr.fresh_state(50000, rules)
        now = time.time()
        m, b = tr.evaluate(st, rules, 50000, 51000, now)
        self.assertEqual(m["floor"], 48500)
        m, b = tr.evaluate(st, rules, 53000, 53000, now)
        self.assertEqual(m["floor"], 50100)          # locked at start + 100
        m, b = tr.evaluate(st, rules, 53000, 50050, now)
        self.assertEqual(b[0], "max_drawdown")

    def test_static_and_target(self):
        tr = RiskTracker()
        rules = rules_from({"risk": {"start_balance": 10000, "max_drawdown": 1000, "profit_target": 1000,
                                     "stop_on_target": True}})
        st = tr.fresh_state(10000, rules)
        m, b = tr.evaluate(st, rules, 11000, 11000, time.time())
        self.assertEqual(b[0], "profit_target")


class Stats(unittest.TestCase):
    def test_fifo_pairing(self):
        fills = [Fill("1", "ESZ6", 3, 6000, 1), Fill("2", "ESZ6", -1, 6002, 2),
                 Fill("3", "ESZ6", -4, 6001, 3), Fill("4", "ESZ6", 2, 5999, 4)]
        trades = stats.pair_fills_fifo(fills, instruments.point_value)
        self.assertEqual([(t.side, t.qty, t.pnl) for t in trades],
                         [("long", 1, 100.0), ("long", 2, 100.0), ("short", 2, 200.0)])

    def test_compute(self):
        day = 86400
        base = 1767225600 + 15 * 3600
        tr = [{"symbol": "ES", "side": "long", "qty": 1, "pnl": p, "fees": 1, "open_time": base + i * day,
               "close_time": base + i * day + 60} for i, p in enumerate([100, -50, 200, -25])]
        s = stats.compute(tr, 50000)["summary"]
        self.assertEqual(s["net_pnl"], 225)
        self.assertEqual(s["win_rate"], 50.0)
        self.assertEqual(s["profit_factor"], 4.0)
        self.assertEqual(s["max_drawdown"], 50)
        self.assertEqual(s["consistency_pct"], 66.7)
        self.assertEqual(s["max_consec_losses"], 1)


if __name__ == "__main__":
    unittest.main()
