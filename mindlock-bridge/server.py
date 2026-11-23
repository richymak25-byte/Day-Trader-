#!/usr/bin/env python3
"""MINDLOCK webhook bridge: TradingView -> this server (Render) -> MT5 EA.

Standard library only, so Render needs nothing installed.

  POST /webhook/<TOKEN>        TradingView posts the JSON from MINDLOCK EMA CORE TREND
                               (webhook mode): {"action":"buy","side":"buy","ticker":"XAUUSD",
                               "qty":1,"price":3655.12,"sl":3645.12,"tp":3685.12}
  GET  /signals/<TOKEN>?after=N   the EA polls here. Plain text, one line per signal,
                               easy to parse in MQL5:
                                 OK|<boot>|<server_now>|<last_id>
                                 <id>|<t>|<action>|<side>|<ticker>|<qty>|<price>|<sl>|<tp>|<reason>|<tf>
                               (empty fields stay empty)
  GET  /health                 "ok", for Render's health check. No token, no data.

Environment:
  BRIDGE_TOKEN   required. A long random secret, in the webhook URL and in the EA.
  PORT           set by Render.
  MAX_SIGNALS    queue size kept in memory (default 500).

Signals live in memory. A restart (deploy, Render maintenance) empties the
queue and changes <boot>; the EA notices and starts again from the new queue.
The EA also ignores any signal older than its MaxSignalAgeSec, so a signal
that arrives late is never executed late.
"""

import collections
import hmac
import json
import os
import random
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ACTIONS = {"buy", "sell", "close", "exit", "modify_sl"}
SIDES = {"buy", "sell", "all"}
FIELDS = ("action", "side", "ticker", "qty", "price", "sl", "tp", "reason", "tf")
DEDUP_SECONDS = 5.0
MAX_BODY = 4096


class BadSignal(ValueError):
    pass


def _num(v, name):
    if v is None or v == "":
        return ""
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise BadSignal("%s is not a number" % name)
    if f != f or f in (float("inf"), float("-inf")):
        raise BadSignal("%s is not a number" % name)
    if f <= 0:
        raise BadSignal("%s must be above 0" % name)
    return str(int(f)) if f == int(f) and abs(f) < 1e15 else repr(f)


def _word(v, name, maxlen=24):
    s = str(v or "").strip()
    if len(s) > maxlen or any(c in s for c in "|\r\n"):
        raise BadSignal("%s is malformed" % name)
    return s


def parse_signal(body):
    """TradingView's body (text) -> a clean dict of strings. Raises BadSignal."""
    try:
        d = json.loads(body)
    except ValueError:
        raise BadSignal("body is not JSON (alert message must be {{strategy.order.alert_message}})")
    if not isinstance(d, dict):
        raise BadSignal("body is not a JSON object")
    action = _word(d.get("action"), "action").lower()
    if action not in ACTIONS:
        raise BadSignal("unknown action %r" % action)
    side = _word(d.get("side"), "side").lower()
    if side not in SIDES:
        raise BadSignal("unknown side %r" % side)
    ticker = _word(d.get("ticker"), "ticker", 32).upper()
    if not ticker:
        raise BadSignal("no ticker")
    sig = {"action": action, "side": side, "ticker": ticker,
           "qty": _num(d.get("qty"), "qty"), "price": _num(d.get("price"), "price"),
           "sl": _num(d.get("sl"), "sl"), "tp": _num(d.get("tp"), "tp"),
           "reason": _word(d.get("reason"), "reason", 32), "tf": _word(d.get("tf"), "tf", 8)}
    if action in ("buy", "sell"):
        if side != action:
            raise BadSignal("entry side must match action")
        if not sig["sl"]:
            raise BadSignal("an entry needs an sl")
        p, sl = float(sig["price"] or 0), float(sig["sl"])
        if p and ((action == "buy" and sl >= p) or (action == "sell" and sl <= p)):
            raise BadSignal("sl is on the wrong side of the entry")
    if action == "modify_sl" and not sig["sl"]:
        raise BadSignal("modify_sl needs an sl")
    return sig


class Queue:
    def __init__(self, maxlen=500, clock=time.time):
        self.lock = threading.Lock()
        self.items = collections.deque(maxlen=maxlen)
        self.last_id = 0
        self.boot = random.randint(1, 2 ** 31 - 1)
        self.clock = clock
        self._recent = {}

    def add(self, sig):
        now = self.clock()
        key = json.dumps(sig, sort_keys=True)
        with self.lock:
            seen = self._recent.get(key)
            if seen is not None and now - seen < DEDUP_SECONDS:
                return None                     # TradingView sent the same alert twice
            self._recent = {k: t for k, t in self._recent.items() if now - t < DEDUP_SECONDS}
            self._recent[key] = now
            self.last_id += 1
            rec = dict(sig, id=self.last_id, t=round(now, 3))
            self.items.append(rec)
            return rec

    def after(self, n):
        with self.lock:
            return [r for r in self.items if r["id"] > n], self.last_id

    def render(self, after_id):
        rows, last = self.after(after_id)
        lines = ["OK|%d|%.3f|%d" % (self.boot, self.clock(), last)]
        for r in rows:
            lines.append("|".join([str(r["id"]), "%.3f" % r["t"]] + [str(r[k]) for k in FIELDS]))
        return "\n".join(lines) + "\n"


def make_handler(queue, token):
    tok = token.encode("utf-8")

    def authorized(given):
        return bool(given) and hmac.compare_digest(given.encode("utf-8"), tok)

    class Handler(BaseHTTPRequestHandler):
        server_version = "mindlock-bridge"

        def log_message(self, fmt, *args):     # Render logs: no tokens, no bodies
            pass

        def _send(self, code, text, ctype="text/plain; charset=utf-8"):
            data = text.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _parts(self):
            u = urlparse(self.path)
            return [p for p in u.path.split("/") if p], parse_qs(u.query)

        def do_GET(self):
            parts, q = self._parts()
            if parts == ["health"]:
                return self._send(200, "ok\n")
            if len(parts) == 2 and parts[0] == "signals" and authorized(parts[1]):
                try:
                    after_id = int((q.get("after") or ["0"])[0])
                except ValueError:
                    after_id = 0
                return self._send(200, queue.render(after_id))
            return self._send(404, "not found\n")

        def do_POST(self):
            parts, _ = self._parts()
            if not (len(parts) == 2 and parts[0] == "webhook" and authorized(parts[1])):
                return self._send(404, "not found\n")
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > MAX_BODY:
                return self._send(400, "bad length\n")
            body = self.rfile.read(n).decode("utf-8", "replace")
            try:
                sig = parse_signal(body)
            except BadSignal as e:
                print("rejected: %s" % e, flush=True)
                return self._send(400, "rejected: %s\n" % e)
            rec = queue.add(sig)
            if rec is None:
                print("duplicate ignored: %s %s %s" % (sig["action"], sig["side"], sig["ticker"]), flush=True)
                return self._send(200, "duplicate\n")
            print("signal %d: %s %s %s price=%s sl=%s tp=%s %s" % (
                rec["id"], rec["action"], rec["side"], rec["ticker"], rec["price"], rec["sl"], rec["tp"],
                rec["reason"]), flush=True)
            return self._send(200, "queued %d\n" % rec["id"])

    return Handler


def main():
    token = os.environ.get("BRIDGE_TOKEN", "")
    if len(token) < 16:
        print("BRIDGE_TOKEN is missing or shorter than 16 characters", file=sys.stderr)
        return 2
    queue = Queue(maxlen=int(os.environ.get("MAX_SIGNALS", "500")))
    port = int(os.environ.get("PORT", "10000"))
    srv = ThreadingHTTPServer(("0.0.0.0", port), make_handler(queue, token))
    print("mindlock-bridge listening on %d (boot %d)" % (port, queue.boot), flush=True)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
