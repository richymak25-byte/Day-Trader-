#!/usr/bin/env python3
"""Autopilot for paper day trading. Standard library only.

Runs the pack's own chain on a list of symbols, unattended, and places the
orders that pass on Alpaca's PAPER account. Nothing here can reach a live
account: the API host is fixed to paper-api.alpaca.markets and there is no
flag, config key or environment variable that changes it.

One tick (`run`) does, in order:

  1. safety   trader/config.json must say "paper": true and
              "autopilot": {"enabled": true}. Anything else: stop.
  2. sync     reads today's filled orders from Alpaca and records each fill
              once (ticket.py fill), so trader-risk sees real cash and losses.
  3. flatten  at or after flat_by (15:55 New York by default): cancels the
              bracket legs of every autopilot symbol still held and sells the
              shares at market, through trader-risk like any other order.
  4. hunt     before flat_by, for each symbol not held and not traded today:
                entry.py check     -> triggered, and fresh (max_trigger_age_min)
                exit.py plan       -> stop and target (trader/plan.json)
                timemachine.py run -> p_target - p_stop >= min_edge
                size.py            -> one bracket order (trader/orders.json)
                check.py           -> PASS, or the order is dropped
              then places it on the paper account and records it in
              trader/state/broker.json (status approved by "autopilot", then
              placed), so the daily loss limit and trades per day still hold.

    python3 autopilot.py status                 # config, clock, what it would do
    python3 autopilot.py run                    # one tick
    python3 autopilot.py run --dry-run          # the whole chain, nothing sent
    python3 autopilot.py loop --every 60        # a tick a minute until the close

Keys come from the environment only: ALPACA_API_KEY and ALPACA_SECRET_KEY
(or APCA_API_KEY_ID and APCA_API_SECRET_KEY). Use PAPER keys. Never put them
in a file in this repo or in the chat.

Journal: every decision is appended to trader/state/autopilot.jsonl.
Not financial advice. Paper money only.
"""

import argparse
import datetime
import importlib.util
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PAPER_HOST = "https://paper-api.alpaca.markets"   # fixed on purpose: no live trading from here

DEFAULTS = {
    "enabled": False,
    "symbols": ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMD", "META", "AMZN", "GOOGL", "TSLA"],
    "setup": "orb",
    "or_minutes": 15,
    "interval": "5m",
    "stop": "or-mid",
    "target": "2R",
    "min_edge": 0.0,            # p_target - p_stop from the time machine
    "min_hist_trades": 0,       # past sessions where the setup fired, at least
    "min_hist_avg_r": None,     # average R of those past trades, at least (None: no check)
    "replays": 2000,
    "max_trigger_age_min": 10,  # do not chase a breakout older than this
    "max_entries_per_tick": 1,
}


class AutopilotError(Exception):
    pass


class SafetyError(AutopilotError):
    """A rule that stops the autopilot for good (not paper, off, no keys...)."""


def _load_mod(folder, name):
    path = os.path.join(ROOT, folder, name + ".py")
    spec = importlib.util.spec_from_file_location("autopilot_" + name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ticket = _load_mod("trader-broker", "ticket")


# ---------------------------------------------------------------- files and config

def read_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_config(path):
    cfg = read_json(path)
    if cfg is None:
        raise AutopilotError("no %s: copy trader-budget/config.example.json there and add an "
                             "\"autopilot\" block (see trader-autopilot/SKILL.md)" % path)
    ap = dict(DEFAULTS)
    ap.update(cfg.get("autopilot") or {})
    return cfg, ap


def check_safety(cfg, ap):
    if cfg.get("paper") is not True:
        raise SafetyError("\"paper\" is not true in trader/config.json. The autopilot trades paper only.")
    if str(cfg.get("broker", "alpaca")).lower() != "alpaca":
        raise SafetyError("the autopilot only drives Alpaca's paper account (\"broker\": \"alpaca\")")
    if ap.get("enabled") is not True:
        raise SafetyError("the autopilot is off: set \"autopilot\": {\"enabled\": true} in trader/config.json")
    if cfg.get("allow_short") or cfg.get("allow_margin") or cfg.get("allow_options"):
        raise SafetyError("the autopilot runs long-only, cash-only, stocks-only: turn off "
                             "allow_short, allow_margin and allow_options")
    if not cfg.get("max_loss_per_trade_usd") or not cfg.get("max_daily_loss_usd"):
        raise SafetyError("set max_loss_per_trade_usd and max_daily_loss_usd before running unattended")


class Journal:
    def __init__(self, path, now_fn):
        self.path = path
        self.now_fn = now_fn

    def __call__(self, event, **kw):
        rec = {"at": ticket.ny_iso(self.now_fn()), "event": event}
        rec.update(kw)
        d = os.path.dirname(self.path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
        detail = " ".join("%s=%s" % (k, v) for k, v in kw.items() if not isinstance(v, (dict, list)))
        print("[%s] %-9s %s" % (rec["at"][11:16], event, detail))


# ---------------------------------------------------------------- the broker (paper only)

def rest_order(order):
    """ticket.tool_input (the MCP shape) -> Alpaca REST /v2/orders body."""
    ti = ticket.tool_input(order)
    body = {k: ti[k] for k in ("symbol", "side", "qty", "notional", "type", "time_in_force",
                               "limit_price", "stop_price", "client_order_id", "extended_hours") if k in ti}
    if ticket.is_bracket(order):
        body["order_class"] = "bracket"
        if "take_profit_limit_price" in ti:
            body["take_profit"] = {"limit_price": ti["take_profit_limit_price"]}
        if "stop_loss_stop_price" in ti:
            sl = {"stop_price": ti["stop_loss_stop_price"]}
            if "stop_loss_limit_price" in ti:
                sl["limit_price"] = ti["stop_loss_limit_price"]
            body["stop_loss"] = sl
    return body


class AlpacaPaper:
    def __init__(self):
        self.key = os.environ.get("ALPACA_API_KEY") or os.environ.get("APCA_API_KEY_ID")
        self.secret = os.environ.get("ALPACA_SECRET_KEY") or os.environ.get("APCA_API_SECRET_KEY")
        if not self.key or not self.secret:
            raise SafetyError("no Alpaca paper keys: export ALPACA_API_KEY and ALPACA_SECRET_KEY "
                                 "(paper keys, from the Alpaca dashboard) in your shell")

    def _req(self, method, path, body=None, query=None):
        url = PAPER_HOST + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "APCA-API-KEY-ID": self.key, "APCA-API-SECRET-KEY": self.secret,
            "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                raw = r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            raise AutopilotError("Alpaca %s %s: HTTP %s %s" % (method, path, e.code,
                                                            e.read().decode("utf-8", "replace")[:300]))
        except urllib.error.URLError as e:
            raise AutopilotError("Alpaca %s %s: %s" % (method, path, e.reason))
        return json.loads(raw) if raw.strip() else None

    def account(self):
        return self._req("GET", "/v2/account")

    def clock(self):
        return self._req("GET", "/v2/clock")

    def positions(self):
        return {p["symbol"]: p for p in self._req("GET", "/v2/positions") or []}

    def open_orders(self, symbol):
        return self._req("GET", "/v2/orders", query={"status": "open", "symbols": symbol, "nested": "true"}) or []

    def closed_orders(self, after_iso):
        return self._req("GET", "/v2/orders", query={"status": "closed", "after": after_iso,
                                                     "nested": "true", "limit": 500}) or []

    def cancel(self, order_id):
        return self._req("DELETE", "/v2/orders/%s" % order_id)

    def submit(self, body):
        return self._req("POST", "/v2/orders", body=body)


class DryBroker:
    """Says what it would send. Reads nothing; holds nothing."""

    def account(self):
        return {"account_number": "PA-DRY-RUN", "status": "ACTIVE", "cash": None}

    def clock(self):
        return None

    def positions(self):
        return {}

    def open_orders(self, symbol):
        return []

    def closed_orders(self, after_iso):
        return []

    def cancel(self, order_id):
        return None

    def submit(self, body):
        return {"id": "dry-run", "status": "dry-run", "body": body}


# ---------------------------------------------------------------- running the pack's tools

class Tools:
    def __init__(self, workdir, now=None, offline=None):
        self.workdir = workdir
        self.now = now          # "2026-10-19 10:20" (New York) or None
        self.offline = offline  # a folder with <SYM>.json (5m today) and <SYM>-60d5m.json(.gz)

    def run(self, folder, script, args, want_json=True):
        cmd = [sys.executable, os.path.join(ROOT, folder, script)] + list(args)
        if self.now:
            cmd += ["--now", self.now]
        p = subprocess.run(cmd, cwd=self.workdir, capture_output=True, text=True)
        out = p.stdout.strip()
        data = None
        if want_json and out:
            try:
                data = json.loads(out)
            except ValueError:
                data = None
        return p.returncode, data, (p.stderr.strip() or out)[-600:]

    def _off(self, sym, kind):
        if not self.offline:
            return []
        names = {"today": [sym + ".json", sym + ".json.gz"],
                 "60d": [sym + "-60d5m.json.gz", sym + "-60d5m.json"]}[kind]
        for n in names:
            p = os.path.join(self.offline, n)
            if os.path.exists(p):
                return ["--offline", p if kind == "60d" else self.offline]
        return []

    def entry(self, sym, ap):
        return self.run("trader-entry", "entry.py",
                        ["check", sym, "--setup", ap["setup"], "--or-minutes", str(ap["or_minutes"]),
                         "--interval", ap["interval"], "--write", "--json"] + self._off(sym, "today"))

    def plan(self, ap):
        return self.run("trader-exit", "exit.py", ["plan", "--stop", ap["stop"], "--target", ap["target"],
                                                   "--write", "--json"])

    def timemachine(self, sym, ap):
        return self.run("trader-timemachine", "timemachine.py",
                        ["run", "--replays", str(ap["replays"]), "--write", "--json"] + self._off(sym, "60d"))

    def size(self):
        return self.run("trader-budget", "size.py", ["--write", "--json"])

    def risk(self):
        return self.run("trader-risk", "check.py", ["--json"])

    def flatten(self, sym, qty):
        return self.run("trader-broker", "ticket.py", ["flatten", "--symbol", sym, "--qty", str(qty)],
                        want_json=False)

    def fill(self, sym, side, qty, price, date):
        return self.run("trader-broker", "ticket.py", ["fill", "--symbol", sym, "--side", side, "--qty", str(qty),
                                                       "--price", str(price), "--date", date], want_json=False)


# ---------------------------------------------------------------- the autopilot

class Autopilot:
    def __init__(self, workdir, broker, tools, now_utc=None, dry_run=False):
        self.workdir = workdir
        self.trader = os.path.join(workdir, "trader")
        self.broker = broker
        self.tools = tools
        self.fixed_now = now_utc
        self.dry_run = dry_run
        self.cfg, self.ap = load_config(os.path.join(self.trader, "config.json"))
        self.state_path = os.path.join(self.trader, "state", "autopilot.json")
        self.broker_path = os.path.join(self.trader, "state", "broker.json")
        self.log = Journal(os.path.join(self.trader, "state", "autopilot.jsonl"), self.now)

    # time
    def now(self):
        return self.fixed_now or ticket.parse_now(None)

    def ny(self):
        return ticket.to_eastern(self.now())

    def today(self):
        return self.ny().date().isoformat()

    def flat_by(self):
        hh, mm = str(self.cfg.get("flat_by", "15:55")).split(":")
        return datetime.time(int(hh), int(mm))

    # state
    def state(self):
        st = read_json(self.state_path, {}) or {}
        if st.get("date") != self.today():
            st = {"date": self.today(), "traded": [], "orders": {}, "fills": [], "flattened": []}
        return st

    def save(self, st):
        ticket.write_json(self.state_path, st)

    # 2. sync fills
    def sync(self, st):
        ny_midnight = datetime.datetime.combine(self.ny().date(), datetime.time(0, 0))
        after = ticket.eastern_to_utc(ny_midnight).strftime("%Y-%m-%dT%H:%M:%SZ")
        ours = set(st["orders"])
        for o in self.broker.closed_orders(after):
            for leg in [o] + list(o.get("legs") or []):
                parent = leg.get("id") if leg is o else o.get("id")
                if parent not in ours and leg.get("id") not in ours:
                    continue
                if leg.get("id") in st["fills"] or not leg.get("filled_qty") or float(leg["filled_qty"]) <= 0:
                    continue
                code, _, msg = self.tools.fill(leg["symbol"], leg["side"], leg["filled_qty"],
                                               leg["filled_avg_price"], self.today())
                st["fills"].append(leg["id"])
                self.log("fill", symbol=leg["symbol"], side=leg["side"], qty=leg["filled_qty"],
                         price=leg["filled_avg_price"], recorded=code == 0)

    # 3. flatten
    def flatten(self, st):
        held = self.broker.positions()
        for sym in sorted(set(st["traded"]) & set(held)):
            qty = abs(int(float(held[sym]["qty"])))
            if qty <= 0 or str(held[sym].get("side", "long")) != "long":
                continue
            self.catch_up(st, sym, qty, held[sym].get("avg_entry_price"))
            for o in self.broker.open_orders(sym):
                self.broker.cancel(o["id"])
            code, _, msg = self.tools.flatten(sym, qty)
            if code != 0:
                self.log("error", step="flatten", symbol=sym, msg=msg)
                continue
            self.place_from_orders(st, sym, kind="exit")
            st["flattened"].append(sym)

    def catch_up(self, st, sym, qty, price):
        """The broker holds qty shares but trader/portfolio.json has fewer (a fill
        not synced yet): record the buy from the broker's position, so the exit
        is a plain sale of what is held and trader-risk never mistakes it for a short."""
        pf = read_json(os.path.join(self.trader, "portfolio.json"), {}) or {}
        have = sum(float(p.get("shares", 0)) for p in pf.get("positions", [])
                   if str(p.get("ticker", "")).upper() == sym)
        missing = qty - have
        if missing <= 1e-9 or not price:
            return
        self.tools.fill(sym, "buy", int(round(missing)), price, self.today())
        for oid, o in st["orders"].items():
            if o["symbol"] == sym and o["kind"] == "entry" and oid not in st["fills"]:
                st["fills"].append(oid)   # the entry fill is now recorded; sync must not add it again
        self.log("fill", symbol=sym, side="buy", qty=int(round(missing)), price=price, source="position")

    # 4. hunt
    def hunt(self, st):
        held = self.broker.positions()
        placed = 0
        for sym in [s.upper() for s in self.ap["symbols"]]:
            if placed >= int(self.ap["max_entries_per_tick"]):
                break
            if sym in st["traded"] or sym in held:
                continue
            if len(st["traded"]) >= int(self.cfg.get("max_trades_per_day", 3)):
                self.log("done", why="max_trades_per_day reached")
                return
            if self.consider(st, sym):
                placed += 1

    def consider(self, st, sym):
        ap = self.ap
        code, e, msg = self.tools.entry(sym, ap)
        if not e:
            self.log("skip", symbol=sym, step="entry", why=msg.splitlines()[-1] if msg else "no data")
            return False
        if e.get("status") != "triggered":
            return False
        age = e.get("minutes_since")
        if age is not None and age > float(ap["max_trigger_age_min"]):
            self.log("skip", symbol=sym, step="entry", why="breakout is %s min old" % age)
            return False
        code, plan, msg = self.tools.plan(ap)
        if code != 0:
            self.log("skip", symbol=sym, step="exit", why=msg.splitlines()[-1] if msg else "no plan")
            return False
        code, tm, msg = self.tools.timemachine(sym, ap)
        if code != 0 or not tm:
            self.log("skip", symbol=sym, step="timemachine", why=msg.splitlines()[-1] if msg else "failed")
            return False
        mc, hist = tm.get("mc") or {}, tm.get("history") or {}
        edge = (mc.get("p_target") or 0) - (mc.get("p_stop") or 0)
        why = None
        if edge < float(ap["min_edge"]):
            why = "edge %.3f < min_edge %s" % (edge, ap["min_edge"])
        elif (hist.get("triggered") or 0) < int(ap["min_hist_trades"]):
            why = "only %s past trades" % hist.get("triggered")
        elif ap["min_hist_avg_r"] is not None and (hist.get("avg_R") or 0) < float(ap["min_hist_avg_r"]):
            why = "history avg R %s < %s" % (hist.get("avg_R"), ap["min_hist_avg_r"])
        if why:
            self.log("skip", symbol=sym, step="timemachine", why=why, verdict=tm.get("verdict"))
            return False
        code, sz, msg = self.tools.size()
        if code != 0:
            self.log("skip", symbol=sym, step="budget", why=msg.splitlines()[-1] if msg else "no order")
            return False
        return self.place_from_orders(st, sym, kind="entry", context={"edge": round(edge, 3),
                                                                      "verdict": tm.get("verdict")})

    def place_from_orders(self, st, sym, kind, context=None):
        code, risk, msg = self.tools.risk()
        if not risk or not risk.get("results"):
            self.log("skip", symbol=sym, step="risk", why=msg.splitlines()[-1] if msg else "no verdict")
            return False
        orders = read_json(os.path.join(self.trader, "orders.json"), {}).get("orders", [])
        res = risk["results"][0]
        if res.get("verdict") != "PASS" or not orders:
            self.log("blocked", symbol=sym, kind=kind, reasons=res.get("reasons"))
            return False
        order = dict(orders[0])
        fp = ticket.fingerprint(order)
        body = rest_order(order)
        body["client_order_id"] = "autopilot-%s-%s-%s" % (self.today(), fp, kind)
        loss, stop = ticket.max_loss(order)
        if self.dry_run:
            self.log("dry-run", symbol=sym, kind=kind, plain=ticket.plain(order), body=body, **(context or {}))
            if kind == "entry":
                st["traded"].append(sym)
            return True
        bstate = ticket.load_state(self.broker_path)
        base = {"date": self.today(), "index": 1, "fingerprint": fp, "symbol": sym, "side": order.get("side"),
                "qty": order.get("qty"), "kind": kind, "max_loss": loss, "stop": stop,
                "plain": ticket.plain(order), "paper": True, "broker": "alpaca"}
        bstate.setdefault("orders", []).append(dict(base, at=ticket.ny_iso(self.now()), status="approved",
                                                    reply="autopilot (paper account, unattended)"))
        try:
            resp = self.broker.submit(body)
        except AutopilotError as e:
            bstate["orders"].append(dict(base, at=ticket.ny_iso(self.now()), status="failed", note=str(e)[:300]))
            ticket.write_json(self.broker_path, bstate)
            self.log("error", symbol=sym, step="submit", msg=str(e))
            if kind == "entry":
                st["traded"].append(sym)   # do not retry the same symbol today
            return False
        bstate["orders"].append(dict(base, at=ticket.ny_iso(self.now()), status="placed",
                                     broker_order_id=resp.get("id")))
        ticket.write_json(self.broker_path, bstate)
        st["orders"][resp.get("id")] = {"symbol": sym, "kind": kind}
        if kind == "entry":
            st["traded"].append(sym)
        self.log("placed", symbol=sym, kind=kind, plain=ticket.plain(order), id=resp.get("id"),
                 **(context or {}))
        return True

    # one tick
    def tick(self):
        check_safety(self.cfg, self.ap)
        acct = self.broker.account() or {}
        if not str(acct.get("account_number", "")).upper().startswith("PA"):
            raise SafetyError("the Alpaca account %r does not look like a paper account (PA...); stopping"
                                 % acct.get("account_number"))
        st = self.state()
        clock = self.broker.clock()
        if clock is not None and not clock.get("is_open"):
            self.log("closed", next_open=clock.get("next_open"))
            self.save(st)
            return "closed"
        self.sync(st)
        if self.ny().time() >= self.flat_by():
            self.flatten(st)
            self.save(st)
            return "flat"
        self.hunt(st)
        self.save(st)
        return "open"

    def status(self):
        st = self.state()
        print("autopilot %s, paper %s, symbols %s" % ("ON" if self.ap.get("enabled") else "OFF",
                                                      self.cfg.get("paper"), ", ".join(self.ap["symbols"])))
        print("rules: stop %s, target %s, min_edge %s, max %s trades/day, $%s max loss/trade, $%s/day, "
              "flat by %s" % (self.ap["stop"], self.ap["target"], self.ap["min_edge"],
                              self.cfg.get("max_trades_per_day"), self.cfg.get("max_loss_per_trade_usd"),
                              self.cfg.get("max_daily_loss_usd"), self.cfg.get("flat_by", "15:55")))
        print("today %s: traded %s, fills %d" % (st["date"], ", ".join(st["traded"]) or "nothing",
                                                 len(st["fills"])))
        try:
            check_safety(self.cfg, self.ap)
            print("safety: OK")
        except AutopilotError as e:
            print("safety: STOP, %s" % e)


# ---------------------------------------------------------------- CLI

def build(a):
    workdir = os.path.abspath(a.workdir)
    now_utc = ticket.parse_now(a.now) if a.now else None
    broker = DryBroker() if (a.dry_run or a.offline) else AlpacaPaper()
    tools = Tools(workdir, now=a.now, offline=a.offline)
    return Autopilot(workdir, broker, tools, now_utc=now_utc, dry_run=a.dry_run or bool(a.offline))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Paper-only autopilot for the Day Trader pack.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("status", "run", "loop"):
        p = sub.add_parser(name)
        p.add_argument("--workdir", default=".", help="the project folder that holds trader/ (default .)")
        p.add_argument("--now", help='pretend it is this New York time, e.g. "2026-10-19 10:20"')
        p.add_argument("--dry-run", action="store_true", help="run the whole chain, send nothing")
        p.add_argument("--offline", metavar="DIR", help="saved charts (<SYM>.json, <SYM>-60d5m.json.gz); implies --dry-run")
        if name == "loop":
            p.add_argument("--every", type=float, default=60, help="seconds between ticks (default 60)")
    a = ap.parse_args(argv)
    try:
        pilot = build(a) if a.cmd != "status" else Autopilot(os.path.abspath(a.workdir), DryBroker(),
                                                              Tools(os.path.abspath(a.workdir)),
                                                              ticket.parse_now(a.now) if a.now else None)
        if a.cmd == "status":
            pilot.status()
            return 0
        if a.cmd == "run":
            print("tick: %s" % pilot.tick())
            return 0
        if a.every < 15:
            raise AutopilotError("--every must be at least 15 seconds (be polite to the free data)")
        while True:
            try:
                r = pilot.tick()
            except SafetyError:
                raise
            except AutopilotError as e:
                pilot.log("error", msg=str(e))
                r = "error"
            if r == "flat" and not pilot.broker.positions():
                pilot.log("stop", why="flat for the day")
                return 0
            if r == "closed" or a.now:
                return 0
            time.sleep(a.every)
    except AutopilotError as e:
        print("autopilot: %s" % e, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nautopilot stopped by you. Open paper positions keep their bracket stop and target.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
