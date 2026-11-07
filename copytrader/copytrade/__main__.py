"""Command line: python -m copytrade futures|cfd [--port N] [--demo]"""
import argparse
import os
import sys
import threading
import webbrowser

from . import clock
from .engine import Engine
from .server import App, serve
from .storage import Store

EDITIONS = {
    # port, session time zone, session roll (CME futures roll at 18:00 New York)
    "futures": (8710, "America/New_York", "18:00"),
    "cfd": (8720, "Europe/Athens", "00:00"),   # most MT5 prop servers run on GMT+2/+3
}


def seed_demo(store, engine, edition):
    """One simulated leader with the demo bot and two followers, so the dashboard lives at once."""
    d = engine.defaults()
    if edition == "futures":
        lead = store.add_account("Maitre SIM", "leader", "sim", {"balance": 50000, "autotrade": True}, dict(d))
        f1 = dict(d, multiplier=1, risk={"start_balance": 50000, "daily_loss_limit": 1000, "max_drawdown": 2000,
                                         "drawdown_type": "trailing_eod", "profit_target": 3000, "max_qty": 5})
        f2 = dict(d, multiplier=10, symbol_map={"NQ": "MNQ", "ES": "MES", "GC": "MGC", "CL": "MCL"},
                  risk={"start_balance": 50000, "max_drawdown": 2500, "drawdown_type": "trailing_intraday",
                        "lock_offset": 100, "profit_target": 3000, "max_qty": 30})
        store.add_account("Topstep 50K SIM", "follower", "sim", {"balance": 50000, "latency_ms": 40}, f1, lead)
        store.add_account("Apex 50K SIM (micros x10)", "follower", "sim", {"balance": 50000, "latency_ms": 60}, f2,
                          lead)
    else:
        lead = store.add_account("Maitre SIM", "leader", "sim", {"balance": 100000, "autotrade": True}, dict(d))
        f1 = dict(d, sizing="equity", risk={"start_balance": 10000, "daily_loss_limit": 500, "max_drawdown": 1000,
                                            "profit_target": 1000})
        f2 = dict(d, sizing="multiplier", multiplier=0.5, symbol_suffix=".r",
                  risk={"start_balance": 100000, "daily_loss_limit": 5000, "max_drawdown": 10000,
                        "profit_target": 10000})
        store.add_account("FTMO 10K SIM (equity)", "follower", "sim", {"balance": 10000, "latency_ms": 40}, f1, lead)
        store.add_account("FundedNext 100K SIM (x0.5, .r)", "follower", "sim",
                          {"balance": 100000, "latency_ms": 60, "symbol_suffix": ".r"}, f2, lead)
    store.put("copying", True)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="copytrade", description="Copieur de trades local avec dashboard")
    ap.add_argument("edition", choices=sorted(EDITIONS))
    ap.add_argument("--port", type=int)
    ap.add_argument("--host", default="127.0.0.1", help="laisser 127.0.0.1 sauf raison precise")
    ap.add_argument("--data", default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                                   "data"))
    ap.add_argument("--tz", help="fuseau de la session (defaut selon l'edition)")
    ap.add_argument("--roll", help="heure de changement de journee de trading, ex 18:00")
    ap.add_argument("--poll", type=float, default=0.25, help="intervalle de lecture du maitre (s)")
    ap.add_argument("--demo", action="store_true", help="cree des comptes simules si la base est vide")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args(argv)

    port, tzname, roll = EDITIONS[a.edition]
    store = Store(os.path.join(a.data, "%s.db" % a.edition))
    engine = Engine(a.edition, store, clock.get_tz(a.tz or tzname), a.roll or roll, a.poll)
    if a.demo and not store.accounts():
        seed_demo(store, engine, a.edition)
    engine.copying = bool(store.get("copying", False))
    engine.load()
    engine.start()
    port = a.port or port
    httpd = serve(App(engine, store), a.host, port)
    url = "http://%s:%d/" % ("127.0.0.1" if a.host in ("0.0.0.0", "") else a.host, port)
    print("%s pret sur %s  (Ctrl+C pour arreter)" % ("CopyTrade " + a.edition.upper(), url))
    if not a.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        engine.shutdown()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
