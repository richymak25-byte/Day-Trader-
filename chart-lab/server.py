#!/usr/bin/env python3
"""Chart Lab: a free, local charting workspace for the Day Trader skills.

Unlimited indicators, drawings saved on your own disk, bar-by-bar replay and a
"Plan" button that runs trader-exit, trader-timemachine and trader-budget on a
trade you mark on the chart. Nothing here places an order.

    python3 chart-lab/server.py                 # http://127.0.0.1:8765
    python3 chart-lab/server.py --port 9000 --open
    python3 chart-lab/server.py --offline tests/fixtures/trader-timemachine   # no network

Run it from the folder that holds (or will hold) your trader/ files: drawings go
to trader/chartlab/, the plan to trader/plan.json, alerts to trader/alerts.json.

Data: Yahoo Finance's chart JSON (unofficial, free, delayed, can lag or answer
429). It listens on 127.0.0.1 only. Standard library only, Python 3.8+.
"""

import argparse
import calendar
import gzip
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
STATIC = os.path.join(HERE, "static")
UA = "Mozilla/5.0 (compatible; day-trader-skill/2.0; chart-lab)"
HOSTS = ("query2", "query1")
SYMBOL_RE = re.compile(r"^[A-Z0-9^][A-Z0-9.\-=^]{0,19}$")
# interval -> (Yahoo interval, range): the longest range Yahoo serves for it
INTERVALS = {
    "1m": ("1m", "5d"),
    "5m": ("5m", "60d"),
    "15m": ("15m", "60d"),
    "1h": ("60m", "1y"),
    "1d": ("1d", "5y"),
    "1wk": ("1wk", "max"),
}
CACHE_SECONDS = {"1m": 20, "5m": 45, "15m": 60, "1h": 120, "1d": 300, "1wk": 900}
MIME = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".json": "application/json",
        ".png": "image/png", ".ico": "image/x-icon"}
MAX_BODY = 2 * 1024 * 1024


class ApiError(Exception):
    def __init__(self, status, message):
        Exception.__init__(self, message)
        self.status = status


def norm_symbol(raw):
    sym = (raw or "").strip().upper()
    if not SYMBOL_RE.match(sym):
        raise ApiError(400, "symbole invalide: %r" % raw)
    return sym


# ---------------------------------------------------------------- data


def parse_chart(doc):
    """Yahoo chart JSON -> list of {timestamp(ms), open, high, low, close, volume}."""
    try:
        res = doc["chart"]["result"][0]
    except (KeyError, IndexError, TypeError):
        err = (doc.get("chart") or {}).get("error") if isinstance(doc, dict) else None
        raise ApiError(404, "pas de données: %s" % ((err or {}).get("description") or "réponse vide"))
    ts = res.get("timestamp") or []
    q = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    o, h, l, c, v = (q.get(k) or [] for k in ("open", "high", "low", "close", "volume"))
    bars = []
    for i, t in enumerate(ts):
        try:
            row = (o[i], h[i], l[i], c[i])
        except IndexError:
            break
        if any(x is None for x in row):
            continue
        bars.append({"timestamp": int(t) * 1000, "open": round(row[0], 4), "high": round(row[1], 4),
                     "low": round(row[2], 4), "close": round(row[3], 4),
                     "volume": int(v[i] or 0) if i < len(v) and v[i] is not None else 0})
    meta = res.get("meta") or {}
    return bars, {"symbol": meta.get("symbol"), "currency": meta.get("currency"),
                  "exchange": meta.get("exchangeName"), "timezone": meta.get("exchangeTimezoneName"),
                  "gmtoffset": meta.get("gmtoffset"), "name": meta.get("longName") or meta.get("shortName")}


def read_json_file(path):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


class Data(object):
    def __init__(self, offline=None):
        self.offline = offline
        self.cache = {}
        self.lock = threading.Lock()

    def offline_file(self, sym, interval=None):
        if not self.offline:
            return None
        if os.path.isfile(self.offline):
            return self.offline
        names = sorted(f for f in os.listdir(self.offline)
                       if re.match(r"(?i)(yahoo-|chart-)?%s[-.]" % re.escape(sym), f)
                       and f.endswith((".json", ".json.gz")))
        if interval:
            pref = [f for f in names if interval in f]
            names = pref or names
        return os.path.join(self.offline, names[0]) if names else None

    def fetch(self, url):
        last = None
        for attempt, host in enumerate(HOSTS * 2):
            req = urllib.request.Request(url.replace("{host}", host),
                                         headers={"User-Agent": UA, "Accept": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                last = "HTTP %s" % e.code
                if e.code == 404:
                    try:
                        return json.loads(e.read().decode("utf-8"))
                    except ValueError:
                        raise ApiError(404, "symbole inconnu")
                if e.code == 429 and attempt == 1:
                    time.sleep(2)
            except (urllib.error.URLError, OSError, ValueError) as e:
                last = str(getattr(e, "reason", e))
        raise ApiError(502, "Yahoo ne répond pas (%s). Réessaie dans une minute." % last)

    def bars(self, sym, interval):
        if interval not in INTERVALS:
            raise ApiError(400, "intervalle inconnu: %s" % interval)
        if self.offline:
            path = self.offline_file(sym, INTERVALS[interval][0])
            if not path:
                raise ApiError(404, "pas de fichier hors ligne pour %s" % sym)
            bars, meta = parse_chart(read_json_file(path))
            meta["source"] = "offline"
            return {"bars": bars, "meta": meta}
        key = (sym, interval)
        with self.lock:
            hit = self.cache.get(key)
            if hit and time.time() - hit[0] < CACHE_SECONDS[interval]:
                return hit[1]
        yi, yr = INTERVALS[interval]
        url = ("https://{host}.finance.yahoo.com/v8/finance/chart/%s?interval=%s&range=%s&includePrePost=false"
               % (urllib.parse.quote(sym), yi, yr))
        bars, meta = parse_chart(self.fetch(url))
        meta["source"] = "yahoo"
        out = {"bars": bars, "meta": meta}
        with self.lock:
            self.cache[key] = (time.time(), out)
        return out

    def search(self, q):
        if self.offline or not q:
            return []
        url = ("https://{host}.finance.yahoo.com/v1/finance/search?q=%s&quotesCount=8&newsCount=0"
               % urllib.parse.quote(q))
        try:
            doc = self.fetch(url)
        except ApiError:
            return []
        return [{"symbol": x.get("symbol"), "name": x.get("shortname") or x.get("longname") or "",
                 "exchange": x.get("exchDisp") or x.get("exchange") or "", "type": x.get("quoteType") or ""}
                for x in doc.get("quotes", []) if x.get("symbol")]


# ---------------------------------------------------------------- skills


def run_tool(args, cwd):
    p = subprocess.run([sys.executable] + args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       universal_newlines=True, timeout=300)
    return p.returncode, p.stdout, p.stderr


def tool(name, script):
    return os.path.join(REPO, name, script)


def as_json(text):
    try:
        return json.loads(text)
    except ValueError:
        return None


def num(body, key, required=True):
    v = body.get(key)
    if v in (None, ""):
        if required:
            raise ApiError(400, "%s manquant" % key)
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ApiError(400, "%s n'est pas un nombre" % key)
    if not f > 0:
        raise ApiError(400, "%s doit être positif" % key)
    return f


class App(object):
    def __init__(self, workdir, data, now=None):
        self.workdir = workdir
        self.data = data
        self.now = now

    def path(self, *parts):
        return os.path.join(self.workdir, "trader", *parts)

    def now_args(self):
        return ["--now", self.now] if self.now else []

    # drawings: one file per symbol, shared by every interval (points are timestamps)
    def drawings_path(self, sym):
        return self.path("chartlab", "drawings", "%s.json" % sym)

    def get_drawings(self, sym):
        p = self.drawings_path(sym)
        if not os.path.exists(p):
            return {"symbol": sym, "drawings": []}
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)

    def put_drawings(self, sym, body):
        items = body.get("drawings")
        if not isinstance(items, list):
            raise ApiError(400, "drawings doit être une liste")
        doc = {"symbol": sym, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "drawings": items}
        write_json(self.drawings_path(sym), doc)
        return {"ok": True, "count": len(items)}

    def get_layout(self):
        p = self.path("chartlab", "layout.json")
        if not os.path.exists(p):
            return {}
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)

    def put_layout(self, body):
        if not isinstance(body, dict):
            raise ApiError(400, "layout invalide")
        write_json(self.path("chartlab", "layout.json"), body)
        return {"ok": True}

    def watchlist(self):
        out = {"scanner": [], "as_of": None, "mode": None}
        p = self.path("watchlist.json")
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as fh:
                    doc = json.load(fh)
                out["as_of"], out["mode"] = doc.get("as_of"), doc.get("mode")
                for h in doc.get("hits", [])[:200]:
                    if isinstance(h, dict) and h.get("symbol"):
                        out["scanner"].append({"symbol": h["symbol"], "setups": h.get("setups") or [],
                                               "price": h.get("price")})
            except (OSError, ValueError):
                pass
        return out

    def plan(self, body):
        sym = norm_symbol(body.get("symbol"))
        entry, stop = num(body, "entry"), num(body, "stop")
        target = num(body, "target", required=False)
        if stop >= entry:
            raise ApiError(400, "le stop doit être sous l'entrée (achat seulement)")
        if target is not None and target <= entry:
            raise ApiError(400, "l'objectif doit être au-dessus de l'entrée")
        out = {"steps": []}
        args = [tool("trader-exit", "exit.py"), "plan", "--symbol", sym, "--entry", "%.4f" % entry,
                "--stop", "price:%.4f" % stop, "--target", ("price:%.4f" % target) if target else "2R",
                "--setup", "level", "--write", "--json"] + self.now_args()
        when = self.entry_clock(sym)
        if when:  # the Time Machine replays from the latest 5-minute bar's close
            args += ["--date", when[0], "--time", when[1]]
        rc, so, se = run_tool(args, self.workdir)
        out["steps"].append({"tool": "trader-exit", "rc": rc, "stderr": se.strip()[-2000:]})
        out["plan"] = as_json(so)
        if rc != 0 or not out["plan"]:
            out["error"] = (se.strip() or so.strip() or "trader-exit a refusé le plan")[-2000:]
            return out
        replays = int(body.get("replays") or 10000)
        replays = max(500, min(replays, 20000))
        args = [tool("trader-timemachine", "timemachine.py"), "run", "--json", "--write",
                "--replays", str(replays)] + self.now_args()
        off = self.data.offline_file(sym, "5m")
        if off:
            args += ["--offline", off]
        rc, so, se = run_tool(args, self.workdir)
        out["steps"].append({"tool": "trader-timemachine", "rc": rc, "stderr": se.strip()[-2000:]})
        tm = as_json(so)
        if tm and isinstance(tm.get("mc"), dict):
            tm["mc"].pop("bands", None)  # the fan is big; the page only needs the odds
        out["timemachine"] = tm
        if not tm:
            out["timemachine_error"] = (se.strip() or so.strip())[-2000:]
        args = [tool("trader-budget", "size.py"), "--json"] + self.now_args()
        rc, so, se = run_tool(args, self.workdir)
        out["steps"].append({"tool": "trader-budget", "rc": rc, "stderr": se.strip()[-2000:]})
        out["budget"] = as_json(so)
        if not out["budget"]:
            out["budget_error"] = (se.strip() or so.strip())[-2000:]
        return out

    def entry_clock(self, sym):
        """(date, "10:15 AM") of the latest complete 5-minute bar's close, exchange time."""
        try:
            d = self.data.bars(sym, "5m")
        except ApiError:
            return None
        bars, off = d.get("bars") or [], d["meta"].get("gmtoffset")
        if not bars or off is None:
            return None
        off = int(off)
        now = time.time()
        if self.now:  # pinned clock, exchange local time
            try:
                now = calendar.timegm(time.strptime(self.now[:16], "%Y-%m-%d %H:%M")) - off
            except ValueError:
                pass
        closes = [b["timestamp"] // 1000 // 300 * 300 + 300 for b in bars]
        done = [c for c in closes if c <= now]
        if not done:
            return None
        t = time.gmtime(done[-1] + off)
        return time.strftime("%Y-%m-%d", t), time.strftime("%I:%M %p", t).lstrip("0")

    def alerts_from_plan(self):
        rc, so, se = run_tool([tool("trader-alerts", "alerts.py"), "add", "--from-plan"], self.workdir)
        return {"ok": rc == 0, "output": (so + se).strip()[-3000:]}

    def alert_price(self, body):
        sym = norm_symbol(body.get("symbol"))
        price = num(body, "price")
        kind = body.get("kind")
        if kind not in ("above", "below"):
            raise ApiError(400, "kind doit être above ou below")
        args = [tool("trader-alerts", "alerts.py"), "add", "--symbol", sym, "--%s" % kind, "%.4f" % price]
        note = (body.get("note") or "").strip()[:120]
        if note:
            args += ["--note", note]
        rc, so, se = run_tool(args, self.workdir)
        return {"ok": rc == 0, "output": (so + se).strip()[-3000:]}

    def alerts(self):
        p = self.path("alerts.json")
        if not os.path.exists(p):
            return {"rules": []}
        with open(p, encoding="utf-8") as fh:
            doc = json.load(fh)
        rules = doc.get("rules", doc) if isinstance(doc, dict) else doc
        return {"rules": rules if isinstance(rules, list) else []}


def write_json(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


# ---------------------------------------------------------------- http


class Handler(BaseHTTPRequestHandler):
    server_version = "ChartLab/1.0"
    app = None  # set by make_server

    def log_message(self, fmt, *args):
        if os.environ.get("CHARTLAB_LOG"):
            BaseHTTPRequestHandler.log_message(self, fmt, *args)

    def send(self, status, body, ctype="application/json; charset=utf-8", cache=False):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=3600" if cache else "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def local_request(self):
        # a page on another site must not drive this server (DNS rebinding, CSRF)
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost", "[::1]"):
            return False
        origin = self.headers.get("Origin")
        if origin:
            o = urllib.parse.urlparse(origin)
            if o.hostname not in ("127.0.0.1", "localhost", "::1"):
                return False
        return True

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise ApiError(413, "requête trop grosse")
        raw = self.rfile.read(n) if n else b"{}"
        try:
            doc = json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            raise ApiError(400, "JSON invalide")
        if not isinstance(doc, dict):
            raise ApiError(400, "un objet JSON est attendu")
        return doc

    def route(self, method):
        if not self.local_request():
            raise ApiError(403, "Chart Lab n'accepte que les requêtes locales")
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        p = u.path
        app = self.app
        if method == "GET":
            if p in ("/", "/index.html"):
                return self.static("index.html")
            if p.startswith("/static/"):
                return self.static(p[len("/static/"):])
            if p == "/api/bars":
                return self.send(200, app.data.bars(norm_symbol(q.get("symbol")), q.get("interval", "5m")))
            if p == "/api/search":
                return self.send(200, {"results": app.data.search(q.get("q", "")[:40])})
            if p == "/api/watchlist":
                return self.send(200, app.watchlist())
            if p == "/api/layout":
                return self.send(200, app.get_layout())
            if p == "/api/alerts":
                return self.send(200, app.alerts())
            if p == "/api/status":
                return self.send(200, {"offline": bool(app.data.offline), "workdir": app.workdir})
            m = re.match(r"^/api/drawings/([^/]+)$", p)
            if m:
                return self.send(200, app.get_drawings(norm_symbol(urllib.parse.unquote(m.group(1)))))
        if method == "PUT":
            m = re.match(r"^/api/drawings/([^/]+)$", p)
            if m:
                return self.send(200, app.put_drawings(norm_symbol(urllib.parse.unquote(m.group(1))), self.body()))
            if p == "/api/layout":
                return self.send(200, app.put_layout(self.body()))
        if method == "POST":
            if p == "/api/plan":
                return self.send(200, app.plan(self.body()))
            if p == "/api/alerts/from-plan":
                return self.send(200, app.alerts_from_plan())
            if p == "/api/alerts":
                return self.send(200, app.alert_price(self.body()))
        raise ApiError(404, "introuvable")

    def static(self, rel):
        full = os.path.realpath(os.path.join(STATIC, rel))
        if not full.startswith(os.path.realpath(STATIC) + os.sep) or not os.path.isfile(full):
            raise ApiError(404, "introuvable")
        with open(full, "rb") as fh:
            data = fh.read()
        self.send(200, data, MIME.get(os.path.splitext(full)[1], "application/octet-stream"),
                  cache="/vendor/" in full.replace(os.sep, "/"))

    def handle_method(self, method):
        try:
            self.route(method)
        except ApiError as e:
            self.send(e.status, {"error": str(e)})
        except subprocess.TimeoutExpired:
            self.send(504, {"error": "un outil a mis trop de temps à répondre"})
        except Exception as e:  # keep the server up whatever one request does
            self.send(500, {"error": "erreur interne: %s" % e})

    def do_GET(self):
        self.handle_method("GET")

    def do_PUT(self):
        self.handle_method("PUT")

    def do_POST(self):
        self.handle_method("POST")


def make_server(port=8765, workdir=".", offline=None, now=None, host="127.0.0.1"):
    app = App(os.path.abspath(workdir), Data(os.path.abspath(offline) if offline else None), now)
    handler = type("BoundHandler", (Handler,), {"app": app})
    return ThreadingHTTPServer((host, port), handler)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Chart Lab: free local charts for the Day Trader skills.")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--workdir", default=".", help="the folder that holds trader/ (default: here)")
    ap.add_argument("--offline", metavar="PATH", help="saved Yahoo chart JSON files (a folder or one file)")
    ap.add_argument("--now", help=argparse.SUPPRESS)  # pins the skills' clock, for tests and demos
    ap.add_argument("--open", action="store_true", help="open the browser")
    a = ap.parse_args(argv)
    srv = make_server(a.port, a.workdir, a.offline, a.now)
    url = "http://127.0.0.1:%d/" % srv.server_address[1]
    print("Chart Lab: %s  (Ctrl+C pour arrêter)" % url)
    print("Fichiers: %s" % os.path.join(os.path.abspath(a.workdir), "trader"))
    if a.offline:
        print("Mode hors ligne: %s" % a.offline)
    if a.open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
