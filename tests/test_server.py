"""MINDLOCK Journal online server: password, shared journal between devices, MetaApi."""
import json
import os
import shutil
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone

from _load import load

server = load("mindlock-journal", "server")
C = load("mindlock-journal", "connectors")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None


class Server(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        with open(os.path.join(cls.tmp, "MINDLOCK-Journal.html"), "w") as fh:
            fh.write("<title>MINDLOCK Journal</title>")
        with open(os.path.join(cls.tmp, "trades.json"), "w") as fh:
            json.dump({"trades": []}, fh)
        cls.synced = []
        cls.httpd, cls.app = server.start(cls.tmp, "127.0.0.1", 0, password="correct horse",
                                          sync_now=lambda: cls.synced.append(1))
        cls.base = "http://127.0.0.1:%d" % cls.httpd.server_address[1]
        cls.opener = urllib.request.build_opener(NoRedirect)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        shutil.rmtree(cls.tmp)

    def call(self, path, method="GET", body=None, cookie=None, ctype=None):
        data = body.encode() if isinstance(body, str) else body
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if cookie:
            req.add_header("Cookie", cookie)
        if ctype:
            req.add_header("Content-Type", ctype)
        try:
            r = self.opener.open(req)
            return r.status, r.read().decode(), r.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode(), e.headers

    def login(self, pw="correct horse"):
        code, _, h = self.call("/login", "POST", "password=" + pw.replace(" ", "+"))
        return code, (h.get("Set-Cookie") or "").split(";")[0]

    def test_everything_needs_the_password(self):
        self.assertIn("Mot de passe", self.call("/")[1])
        self.assertEqual(self.call("/data.json")[0], 401)
        self.assertEqual(self.call("/api/state")[0], 401)
        self.assertEqual(self.call("/api/state", "PUT", "{}", ctype="application/json")[0], 401)
        self.assertEqual(self.call("/healthz")[0], 200)
        self.assertEqual(self.call("/manifest.webmanifest")[0], 200)

    def test_wrong_password_then_lockout(self):
        self.app.failures.clear()
        for _ in range(5):
            self.assertEqual(self.login("nope")[0], 401)
        code, body, _ = self.call("/login", "POST", "password=correct+horse")
        self.assertIn("Trop d'essais", body)
        self.app.failures.clear()

    def test_two_devices_share_one_journal(self):
        self.app.failures.clear()
        code, phone = self.login()
        self.assertEqual(code, 303)
        self.assertTrue(phone.startswith("mindlock_session="))
        _, pc = self.login()
        self.assertEqual(self.call("/", cookie=pc)[1], "<title>MINDLOCK Journal</title>")
        st = {"trades": [{"id": "t1", "notes": "from the phone"}], "days": {}, "settings": {}}
        code, body, _ = self.call("/api/state", "PUT", json.dumps(st), phone, "application/json")
        self.assertEqual(code, 200)
        v = json.loads(body)["version"]
        got = json.loads(self.call("/api/state", cookie=pc)[1])
        self.assertEqual((got["version"], got["state"]["trades"][0]["notes"]), (v, "from the phone"))
        self.assertEqual(self.call("/api/state?since=%d" % v, cookie=pc)[0], 204)
        # sync.py reads the same file back, so notes survive in trades.json
        with open(os.path.join(self.tmp, "mindlock-backup.json")) as fh:
            self.assertEqual(json.load(fh)["trades"][0]["notes"], "from the phone")
        self.assertEqual(self.call("/api/sync", "POST", "", pc)[0], 202)
        self.assertEqual(self.synced, [1])

    def test_forged_or_old_cookie_is_refused(self):
        self.assertEqual(self.call("/data.json", cookie="mindlock_session=9999999999.abc")[0], 401)
        old = "%d.%s" % (time.time() - 10, "0" * 64)
        self.assertEqual(self.call("/data.json", cookie="mindlock_session=" + old)[0], 401)

    def test_bad_writes_are_refused(self):
        _, ck = self.login()
        self.assertEqual(self.call("/api/state", "PUT", "not json", ck, "application/json")[0], 400)
        self.assertEqual(self.call("/api/state", "PUT", "[]", ck, "application/json")[0], 400)
        self.assertEqual(self.call("/api/state", "PUT", "{}", ck, "text/plain")[0], 415)

    def test_online_without_password_refuses_to_start(self):
        with self.assertRaises(SystemExit):
            server.start(self.tmp, "0.0.0.0", 0, password="")


class MetaApi(unittest.TestCase):
    def test_reads_every_deployed_account(self):
        deals = [
            {"id": "1", "type": "DEAL_TYPE_BALANCE", "time": "2026-11-01T08:00:00.000Z", "profit": 100000},
            {"id": "2", "type": "DEAL_TYPE_SELL", "entryType": "DEAL_ENTRY_IN", "positionId": "77", "symbol": "XAUUSD",
             "volume": 0.5, "price": 2410.5, "time": "2026-11-05T13:00:00.000Z", "commission": -3.5, "swap": 0, "profit": 0},
            {"id": "3", "type": "DEAL_TYPE_BUY", "entryType": "DEAL_ENTRY_OUT", "positionId": "77", "symbol": "XAUUSD",
             "volume": 0.5, "price": 2400.5, "time": "2026-11-05T14:10:00.000Z", "commission": -3.5, "swap": 0,
             "profit": 500}]
        calls = []

        def fake(method, url, headers=None, body=None, timeout=30):
            calls.append((url, headers))
            if url.endswith("/users/current/accounts"):
                return json.dumps([{"_id": "a1", "name": "FTMO 100K", "login": "123", "state": "DEPLOYED",
                                    "region": "london"},
                                   {"_id": "a2", "name": "Foxx 50K", "login": "456", "state": "UNDEPLOYED"}])
            return json.dumps(deals)
        C.HTTP = fake
        _, t, _ = C.metaapi({"token": "tok"}, datetime(2026, 1, 1, tzinfo=timezone.utc), "Europe/Paris")
        self.assertEqual(len(t), 1)
        t = t[0]
        self.assertEqual((t["account"], t["side"], t["qty"], t["pnl"], t["fees"]), ("FTMO 100K", "short", 0.5, 493.0, 7.0))
        self.assertEqual((t["openTime"], t["closeTime"]), ("2026-11-05 14:00:00", "2026-11-05 15:10:00"))
        self.assertIn("mt-client-api-v1.london.agiliumtrade.ai/users/current/accounts/a1/history-deals/time/", calls[1][0])
        self.assertEqual(calls[0][1]["auth-token"], "tok")
        self.assertEqual(len(calls), 2)  # the undeployed account is not read


if __name__ == "__main__":
    unittest.main()
