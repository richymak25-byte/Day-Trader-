"""MINDLOCK Journal: the online server.

Serves the dashboard with a password, keeps one shared copy of your journal
(notes, tags, daily journal, playbook, settings) so the phone and the computer
show the same thing, and runs the broker sync in the background.

    python3 mindlock-journal/sync.py --serve                       # this computer only
    MINDLOCK_PASSWORD=... python3 mindlock-journal/sync.py --serve --host 0.0.0.0   # online

A public address (any host other than 127.0.0.1) refuses to start without
MINDLOCK_PASSWORD. Python 3.8+, standard library only.
"""
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler

try:
    from http.server import ThreadingHTTPServer as Server
except ImportError:  # pragma: no cover
    from http.server import HTTPServer as Server

HERE = os.path.dirname(os.path.abspath(__file__))
COOKIE = "mindlock_session"
SESSION_DAYS = 30
MAX_BODY = 25 * 1024 * 1024
LOCK = threading.Lock()


def write_atomic(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


class App(object):
    def __init__(self, jdir, password=None, secret=None, sync_now=None):
        self.jdir = jdir
        self.password = password or ""
        self.sync_now = sync_now or (lambda: None)
        self.state_path = os.path.join(jdir, "mindlock-backup.json")
        self.failures = {}
        if not secret:
            spath = os.path.join(jdir, ".session-secret")
            if os.path.isfile(spath):
                with open(spath) as fh:
                    secret = fh.read().strip()
            else:
                secret = secrets.token_hex(32)
                write_atomic(spath, secret)
        self.secret = secret.encode("utf-8")

    # ------------------------------------------------------------ sessions
    def make_session(self):
        exp = str(int(time.time()) + SESSION_DAYS * 86400)
        sig = hmac.new(self.secret, (exp + self._pw_tag()).encode(), hashlib.sha256).hexdigest()
        return "%s.%s" % (exp, sig)

    def _pw_tag(self):
        # changing the password logs every device out
        return hashlib.sha256(self.password.encode("utf-8")).hexdigest()[:16]

    def valid_session(self, value):
        if not self.password:
            return True
        try:
            exp, sig = (value or "").split(".", 1)
            if int(exp) < time.time():
                return False
        except ValueError:
            return False
        good = hmac.new(self.secret, (exp + self._pw_tag()).encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, good)

    def check_password(self, ip, given):
        n, until = self.failures.get(ip, (0, 0))
        if until > time.time():
            return "wait"
        if self.password and hmac.compare_digest(given.encode("utf-8"), self.password.encode("utf-8")):
            self.failures.pop(ip, None)
            return "ok"
        n += 1
        self.failures[ip] = (n, time.time() + (min(300, 2 ** (n - 4) * 15) if n >= 5 else 0))
        return "bad"

    # ------------------------------------------------------------ shared state
    def read_state(self):
        if not os.path.isfile(self.state_path):
            return {"version": 0, "state": None}
        with open(self.state_path, encoding="utf-8") as fh:
            data = json.load(fh)
        return {"version": int(data.get("_version", 0)), "state": data}

    def write_state(self, state):
        with LOCK:
            cur = self.read_state()["version"]
            state = dict(state)
            state["_version"] = cur + 1
            write_atomic(self.state_path, json.dumps(state, ensure_ascii=False))
            return cur + 1


LOGIN = """<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>MINDLOCK Journal</title>
<meta name="theme-color" content="#0b0d14"><link rel="manifest" href="manifest.webmanifest">
<link rel="apple-touch-icon" href="icon-192.png"><link rel="icon" href="icon.svg">
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0b0d14;color:#e8eaf3;
font:15px/1.5 Inter,"Segoe UI",system-ui,-apple-system,Roboto,sans-serif}
form{width:min(360px,90vw);background:#121522;border:1px solid #252a3d;border-radius:18px;padding:30px 26px;
box-shadow:0 10px 40px rgba(0,0,0,.4)}h1{margin:14px 0 4px;font-size:20px;letter-spacing:.14em}
p{margin:0 0 20px;color:#8a90a8;font-size:13px}input{width:100%;box-sizing:border-box;padding:12px 14px;
border-radius:11px;border:1px solid #252a3d;background:#0b0d14;color:inherit;font:inherit;outline:none}
input:focus{border-color:#7c5cff}button{width:100%;margin-top:14px;padding:12px;border:0;border-radius:11px;
background:#7c5cff;color:#fff;font:inherit;font-weight:700;cursor:pointer}.err{color:#f2545b;margin-top:12px;font-size:13px}
img{width:52px;height:52px}</style></head><body><form method="post" action="login">
<img src="icon.svg" alt=""><h1>MINDLOCK</h1><p>Ton journal de trading</p>
<input type="password" name="password" placeholder="Mot de passe" autocomplete="current-password" autofocus required>
<button type="submit">Entrer</button>{ERR}</form></body></html>"""

MANIFEST = {"name": "MINDLOCK Journal", "short_name": "MINDLOCK", "start_url": "./", "scope": "./",
            "display": "standalone", "background_color": "#0b0d14", "theme_color": "#0b0d14",
            "icons": [{"src": "icon-192.png", "sizes": "192x192", "type": "image/png"},
                      {"src": "icon-512.png", "sizes": "512x512", "type": "image/png"},
                      {"src": "icon.svg", "sizes": "any", "type": "image/svg+xml"}]}

STATIC = {"/icon.svg": ("icon.svg", "image/svg+xml"), "/icon-192.png": ("icon-192.png", "image/png"),
          "/icon-512.png": ("icon-512.png", "image/png")}


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = "MINDLOCK"
        sys_version = ""

        def log_message(self, *a):
            pass

        # ---------------------------------------------------- helpers
        def _ip(self):
            fwd = self.headers.get("X-Forwarded-For", "")
            return fwd.split(",")[0].strip() or self.client_address[0]

        def _cookie(self):
            for part in (self.headers.get("Cookie") or "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == COOKIE:
                    return v
            return ""

        def _authed(self):
            return app.valid_session(self._cookie())

        def _send(self, code, body=b"", ctype="text/plain; charset=utf-8", headers=None):
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "same-origin")
            self.send_header("X-Frame-Options", "DENY")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, code, data):
            self._send(code, json.dumps(data, ensure_ascii=False), "application/json; charset=utf-8")

        def _file(self, name, ctype):
            path = os.path.join(app.jdir if name.endswith((".html", ".json")) else HERE, name)
            if not os.path.isfile(path):
                return self._send(404, "not found")
            with open(path, "rb") as fh:
                self._send(200, fh.read(), ctype)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                raise ValueError("too large")
            return self.rfile.read(n) if n else b""

        def _set_cookie(self, value, max_age):
            secure = "; Secure" if self.headers.get("X-Forwarded-Proto", "") == "https" else ""
            return {"Set-Cookie": "%s=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=%d%s"
                                  % (COOKIE, value, max_age, secure)}

        # ---------------------------------------------------- routes
        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/healthz":
                return self._send(200, "ok")
            if path == "/manifest.webmanifest":
                return self._send(200, json.dumps(MANIFEST), "application/manifest+json")
            if path in STATIC:
                return self._file(*STATIC[path])
            if path == "/logout":
                return self._send(303, "", headers=dict(self._set_cookie("", 0), Location="./"))
            if not self._authed():
                if path.startswith("/api/") or path == "/data.json":
                    return self._json(401, {"error": "login"})
                return self._send(200, LOGIN.replace("{ERR}", ""), "text/html; charset=utf-8")
            if path == "/":
                return self._file("MINDLOCK-Journal.html", "text/html; charset=utf-8")
            if path == "/data.json":
                return self._file("trades.json", "application/json; charset=utf-8")
            if path == "/api/state":
                st = app.read_state()
                since = self.path.partition("since=")[2].split("&")[0]
                if since.isdigit() and int(since) >= st["version"]:
                    return self._send(204)
                return self._json(200, st)
            return self._send(404, "not found")

        def do_POST(self):
            path = self.path.split("?")[0]
            if path == "/login":
                try:
                    from urllib.parse import parse_qs
                    form = parse_qs(self._body().decode("utf-8", "replace"))
                except ValueError:
                    return self._send(413, "too large")
                res = app.check_password(self._ip(), (form.get("password") or [""])[0])
                if res == "ok":
                    return self._send(303, "", headers=dict(self._set_cookie(app.make_session(), SESSION_DAYS * 86400),
                                                            Location="./"))
                msg = ("Trop d'essais, patiente un moment." if res == "wait" else "Mot de passe incorrect.")
                return self._send(401, LOGIN.replace("{ERR}", '<div class="err">%s</div>' % msg), "text/html; charset=utf-8")
            if not self._authed():
                return self._json(401, {"error": "login"})
            if path == "/api/sync":
                app.sync_now()
                return self._json(202, {"ok": True})
            return self._send(404, "not found")

        def do_PUT(self):
            if self.path.split("?")[0] != "/api/state":
                return self._send(404, "not found")
            if not self._authed():
                return self._json(401, {"error": "login"})
            if "json" not in (self.headers.get("Content-Type") or ""):
                return self._send(415, "json only")
            try:
                state = json.loads(self._body().decode("utf-8"))
            except ValueError:
                return self._send(400, "bad json")
            if not isinstance(state, dict) or not isinstance(state.get("trades", []), list):
                return self._send(400, "bad state")
            return self._json(200, {"version": app.write_state(state)})

    return Handler


def start(jdir, host="127.0.0.1", port=8765, password=None, sync_now=None):
    password = password if password is not None else os.environ.get("MINDLOCK_PASSWORD", "")
    if host not in ("127.0.0.1", "localhost", "::1") and len(password) < 8:
        raise SystemExit("An online journal needs MINDLOCK_PASSWORD (8 characters or more).")
    app = App(jdir, password, os.environ.get("MINDLOCK_SECRET"), sync_now)
    httpd = Server((host, port), make_handler(app))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    shown = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    print("MINDLOCK Journal live: http://%s:%d%s  (Ctrl+C to stop)"
          % (shown, port, "  [password on]" if password else ""))
    return httpd, app
