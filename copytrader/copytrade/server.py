"""Local HTTP server: JSON API + the dashboard (127.0.0.1 only by default)."""
import csv
import io
import json
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import __version__, instruments, stats
from .connectors import EDITION_CONNECTORS
from .storage import mask_params

STATIC = os.path.join(os.path.dirname(__file__), "static")
TYPES = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}

TITLES = {"futures": "CopyTrade Futures", "cfd": "CopyTrade CFD"}


class App:
    def __init__(self, engine, store):
        self.engine = engine
        self.store = store
        self.edition = engine.edition

    # GET ---------------------------------------------------------------------------
    def meta(self, q):
        return {"edition": self.edition, "title": TITLES[self.edition], "version": __version__,
                "unit": "contrats" if self.edition == "futures" else "lots",
                "connectors": EDITION_CONNECTORS[self.edition],
                "presets": instruments.FUTURES_PRESETS if self.edition == "futures" else instruments.CFD_PRESETS,
                "defaults": self.engine.defaults()}

    def state(self, q):
        snap = self.engine.snapshot()
        accts = {a["id"]: a for a in self.store.accounts()}
        today_start = time.time() - 86400
        for a in snap["accounts"]:
            a["params"] = mask_params(accts.get(a["id"], {}).get("params", {}))
            a["trades_24h"] = len(self.store.trades(a["id"], since=today_start))
        snap["executions"] = self.store.executions(25)
        return snap

    def _acct_filter(self, q):
        v = (q.get("account") or ["all"])[0]
        return None if v in ("all", "") else int(v)

    def _since(self, q):
        days = float((q.get("days") or ["0"])[0] or 0)
        return time.time() - days * 86400 if days > 0 else 0.0

    def stats(self, q):
        acct = self._acct_filter(q)
        trades = self.store.trades(acct, since=self._since(q))
        start = 0.0
        role = (q.get("role") or ["followers"])[0]
        accts = self.store.accounts()
        if acct is None and role != "all":
            ids = {a["id"] for a in accts if a["role"] == ("leader" if role == "leaders" else "follower")}
            trades = [t for t in trades if t["account_id"] in ids]
        chosen = [a for a in accts if acct is None or a["id"] == acct]
        for a in chosen:
            if acct is None and role != "all" and a["role"] != ("leader" if role == "leaders" else "follower"):
                continue
            r = self.engine.rt.get(a["id"])
            rs = (r.risk_state if r else None) or {}
            start += float((a["settings"].get("risk") or {}).get("start_balance") or rs.get("start") or 0)
        out = stats.compute(trades, start, self.engine.tz)
        out["start_balance"] = start
        if acct is not None:
            out["equity_snapshots"] = self.store.equity(acct, self._since(q))
        return out

    def trades(self, q):
        acct = self._acct_filter(q)
        limit = int((q.get("limit") or ["500"])[0])
        return self.store.trades(acct, since=self._since(q), limit=limit)

    def events(self, q):
        return self.store.events(int((q.get("limit") or ["300"])[0]))

    def executions(self, q):
        return self.store.executions(int((q.get("limit") or ["300"])[0]))

    def trades_csv(self, q):
        rows = self.trades(dict(q, limit=["100000"]))
        names = {a["id"]: a["name"] for a in self.store.accounts()}
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["compte", "symbole", "sens", "qte", "entree", "sortie", "pnl", "frais", "ouverture", "cloture"])
        for t in rows:
            w.writerow([names.get(t["account_id"], t["account_id"]), t["symbol"], t["side"], t["qty"],
                        t["entry_price"], t["exit_price"], t["pnl"], t["fees"],
                        time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t["open_time"])),
                        time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t["close_time"]))])
        return buf.getvalue()

    # POST -----------------------------------------------------------------------------
    def _clean(self, body, partial=False):
        out = {}
        if "name" in body or not partial:
            name = str(body.get("name") or "").strip()
            if not name:
                raise ValueError("Nom requis")
            out["name"] = name[:80]
        if "role" in body or not partial:
            if body.get("role") not in ("leader", "follower"):
                raise ValueError("Role invalide")
            out["role"] = body["role"]
        if "connector" in body or not partial:
            ids = [c["id"] for c in EDITION_CONNECTORS[self.edition]]
            if body.get("connector") not in ids:
                raise ValueError("Connecteur invalide")
            out["connector"] = body["connector"]
        if "leader_id" in body:
            out["leader_id"] = int(body["leader_id"]) if body.get("leader_id") not in (None, "") else None
        if "params" in body:
            out["params"] = dict(body["params"] or {})
        if "settings" in body:
            st = self.engine.defaults()
            st.update(body["settings"] or {})
            out["settings"] = st
        if "enabled" in body:
            out["enabled"] = bool(body["enabled"])
        if out.get("role") == "follower" and not partial and not out.get("leader_id"):
            raise ValueError("Un compte copieur doit avoir un compte maitre")
        return out

    def add_account(self, body):
        c = self._clean(body)
        acct_id = self.store.add_account(c["name"], c["role"], c["connector"], c.get("params", {}),
                                         c.get("settings", self.engine.defaults()), c.get("leader_id"),
                                         c.get("enabled", True))
        self.store.event("info", "Compte ajoute: %s (%s)" % (c["name"], c["role"]), acct_id)
        self.engine.reload_account(acct_id)
        return {"id": acct_id}

    def update_account(self, acct_id, body):
        c = self._clean(body, partial=True)
        reconnect = "params" in c or "connector" in c
        acct = self.store.update_account(acct_id, **c)
        if not acct:
            raise KeyError("compte introuvable")
        r = self.engine.rt.get(acct_id)
        if reconnect or not r:
            self.engine.reload_account(acct_id)
        else:
            r.acct = acct
        return {"ok": True}

    def action(self, path, body):
        m = re.match(r"^/api/accounts/(\d+)(?:/(\w+))?$", path)
        if path == "/api/accounts":
            return self.add_account(body)
        if m:
            acct_id, verb = int(m.group(1)), m.group(2)
            if verb is None:
                return self.update_account(acct_id, body)
            if verb == "delete":
                self.engine.remove_account(acct_id)
                self.store.delete_account(acct_id)
                return {"ok": True}
            if verb == "enable":
                return self.update_account(acct_id, {"enabled": bool(body.get("enabled"))})
            if verb == "flatten":
                self.engine.flatten(acct_id)
                return {"ok": True}
            if verb == "unlock":
                self.engine.unlock(acct_id)
                return {"ok": True}
            if verb == "reset_risk":
                self.engine.reset_risk(acct_id)
                return {"ok": True}
            if verb == "reconnect":
                self.engine.reload_account(acct_id)
                return {"ok": True}
        if path == "/api/engine":
            if "copying" in body:
                self.engine.set_copying(bool(body["copying"]))
            if "sync_on_start" in body:
                self.store.put("sync_on_start", bool(body["sync_on_start"]))
            return {"ok": True}
        if path == "/api/flatten_all":
            self.engine.flatten_all(bool(body.get("include_leaders")))
            return {"ok": True}
        raise KeyError("route inconnue")


def make_handler(app):
    gets = {"/api/meta": app.meta, "/api/state": app.state, "/api/stats": app.stats, "/api/trades": app.trades,
            "/api/events": app.events, "/api/executions": app.executions}

    class Handler(BaseHTTPRequestHandler):
        server_version = "CopyTrade/" + __version__

        def log_message(self, fmt, *args):
            pass

        def _send(self, code, payload, ctype="application/json; charset=utf-8", extra=None):
            data = payload if isinstance(payload, bytes) else (
                payload.encode() if isinstance(payload, str) else json.dumps(payload).encode())
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            try:
                if u.path in gets:
                    return self._send(200, gets[u.path](q))
                if u.path == "/api/trades.csv":
                    return self._send(200, app.trades_csv(q), "text/csv; charset=utf-8",
                                      {"Content-Disposition": "attachment; filename=trades-%s.csv" % app.edition})
                name = "index.html" if u.path in ("/", "") else u.path.lstrip("/")
                full = os.path.realpath(os.path.join(STATIC, name))
                if not full.startswith(os.path.realpath(STATIC)) or not os.path.isfile(full):
                    return self._send(404, {"error": "introuvable"})
                with open(full, "rb") as fh:
                    return self._send(200, fh.read(), TYPES.get(os.path.splitext(full)[1], "application/octet-stream"))
            except Exception as e:
                return self._send(500, {"error": str(e)})

        def do_POST(self):
            # same-origin guard: refuse cross-site form posts against the local API
            origin = self.headers.get("Origin")
            if origin and urlparse(origin).netloc != self.headers.get("Host"):
                return self._send(403, {"error": "origine refusee"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}") if n else {}
                return self._send(200, app.action(urlparse(self.path).path, body))
            except (ValueError, KeyError) as e:
                return self._send(400, {"error": str(e).strip("'")})
            except Exception as e:
                return self._send(500, {"error": str(e)})

    return Handler


def serve(app, host, port):
    httpd = ThreadingHTTPServer((host, port), make_handler(app))
    httpd.daemon_threads = True
    return httpd
