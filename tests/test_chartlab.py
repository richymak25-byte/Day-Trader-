"""Chart Lab tests: the local server, offline, on TSLA's real 5-minute bars
(tests/fixtures/trader-timemachine, the 60 days to October 19 2026)."""

import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from _load import load, FIX, ROOT

lab = load("chart-lab", "server")

OFFLINE = os.path.join(FIX, "trader-timemachine")
NOW = "2026-10-19 10:15"


class ChartLab(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.mkdtemp()
        os.makedirs(os.path.join(cls.work, "trader"))
        shutil.copy(os.path.join(ROOT, "trader-budget", "config.example.json"),
                    os.path.join(cls.work, "trader", "config.json"))
        cls.srv = lab.make_server(0, cls.work, OFFLINE, NOW)
        cls.base = "http://127.0.0.1:%d" % cls.srv.server_address[1]
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        shutil.rmtree(cls.work, ignore_errors=True)

    def call(self, method, path, body=None, headers=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers or {})
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                raw, status = r.read(), r.status
        except urllib.error.HTTPError as e:
            raw, status = e.read(), e.code
        try:
            return status, json.loads(raw.decode("utf-8"))
        except ValueError:
            return status, raw

    def test_page_and_vendored_chart_library(self):
        status, html = self.call("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"<title>Chart Lab</title>", html)
        self.assertIn(b"/static/vendor/klinecharts.min.js", html)
        status, js = self.call("GET", "/static/vendor/klinecharts.min.js")
        self.assertEqual(status, 200)
        self.assertIn(b"KLineChart v9.8.12", js[:300])
        self.assertTrue(os.path.exists(os.path.join(ROOT, "chart-lab", "static", "vendor", "KLINECHARTS-LICENSE")))

    def test_static_files_stay_inside_the_folder(self):
        for path in ("/static/../server.py", "/static/%2e%2e/server.py", "/static/vendor/../../server.py"):
            status, _ = self.call("GET", path)
            self.assertEqual(status, 404, path)

    def test_bars_from_the_offline_file(self):
        status, d = self.call("GET", "/api/bars?symbol=tsla&interval=5m")
        self.assertEqual(status, 200)
        self.assertEqual(d["meta"]["source"], "offline")
        self.assertEqual(d["meta"]["timezone"], "America/New_York")
        bars = d["bars"]
        self.assertGreater(len(bars), 4000)
        b = bars[0]
        self.assertEqual(sorted(b), ["close", "high", "low", "open", "timestamp", "volume"])
        self.assertGreater(b["timestamp"], 10 ** 12)  # milliseconds for the chart
        self.assertTrue(all(x["low"] <= x["close"] <= x["high"] for x in bars))
        self.assertEqual([x["timestamp"] for x in bars], sorted(x["timestamp"] for x in bars))

    def test_bad_symbol_and_interval(self):
        self.assertEqual(self.call("GET", "/api/bars?symbol=../etc&interval=5m")[0], 400)
        self.assertEqual(self.call("GET", "/api/bars?symbol=TSLA&interval=7m")[0], 400)
        self.assertEqual(self.call("GET", "/api/bars?symbol=ZZZZ&interval=5m")[0], 404)

    def test_drawings_round_trip_to_disk(self):
        doc = {"drawings": [{"name": "segment", "points": [{"timestamp": 1792163400000, "value": 361.13},
                                                            {"timestamp": 1792178400000, "value": 367.99}],
                             "extendData": None, "lock": False, "visible": True}]}
        status, r = self.call("PUT", "/api/drawings/TSLA", doc)
        self.assertEqual((status, r["count"]), (200, 1))
        with open(os.path.join(self.work, "trader", "chartlab", "drawings", "TSLA.json")) as fh:
            self.assertEqual(json.load(fh)["drawings"], doc["drawings"])
        status, back = self.call("GET", "/api/drawings/tsla")
        self.assertEqual(back["drawings"], doc["drawings"])
        self.assertEqual(self.call("GET", "/api/drawings/NVDA")[1]["drawings"], [])
        self.assertEqual(self.call("PUT", "/api/drawings/..%2Fx", doc)[0], 400)
        self.assertEqual(self.call("PUT", "/api/drawings/TSLA", {"drawings": "no"})[0], 400)

    def test_other_sites_cannot_drive_the_server(self):
        status, r = self.call("PUT", "/api/layout", {"grid": 4}, {"Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        status, _ = self.call("GET", "/api/watchlist", headers={"Host": "evil.example"})
        self.assertEqual(status, 403)
        status, _ = self.call("PUT", "/api/layout", {"grid": 2}, {"Origin": self.base})
        self.assertEqual(status, 200)

    def test_watchlist_reads_the_scanner_hits(self):
        self.assertEqual(self.call("GET", "/api/watchlist")[1]["scanner"], [])
        lab.write_json(os.path.join(self.work, "trader", "watchlist.json"),
                       {"mode": "daily", "as_of": "2026-10-19", "hits": [{"symbol": "TSLA", "setups": ["bull_flag"], "price": 360.1}]})
        try:
            d = self.call("GET", "/api/watchlist")[1]
            self.assertEqual(d["scanner"], [{"symbol": "TSLA", "setups": ["bull_flag"], "price": 360.1}])
            self.assertEqual(d["as_of"], "2026-10-19")
        finally:
            os.remove(os.path.join(self.work, "trader", "watchlist.json"))

    def test_plan_runs_exit_timemachine_and_budget(self):
        status, r = self.call("POST", "/api/plan", {"symbol": "TSLA", "entry": 364.18, "stop": 358.32, "replays": 500})
        self.assertEqual(status, 200, r)
        self.assertNotIn("error", r)
        p = r["plan"]
        self.assertEqual((p["symbol"], p["entry"], p["stop"], p["target"]), ("TSLA", 364.18, 358.32, 375.9))
        self.assertEqual((p["date"], p["entry_time"], p["setup"]), ("2026-10-19", "10:15 AM", "level"))
        mc = r["timemachine"]["mc"]
        self.assertEqual(mc["replays"], 500)
        self.assertAlmostEqual(mc["p_target"] + mc["p_stop"] + mc["p_neither"], 1.0, places=2)
        self.assertNotIn("bands", mc)
        s = r["budget"]["sizing"]
        self.assertEqual((s["qty"], s["bound_by"]), (8, "max loss"))
        self.assertLessEqual(s["max_loss"], 50)
        self.assertTrue(os.path.exists(os.path.join(self.work, "trader", "plan.json")))
        # sizing only: no order file is written from the page
        self.assertFalse(os.path.exists(os.path.join(self.work, "trader", "orders.json")))

        status, a = self.call("POST", "/api/alerts/from-plan")
        self.assertTrue(a["ok"], a)
        rules = self.call("GET", "/api/alerts")[1]["rules"]
        self.assertEqual(sorted(x["type"] for x in rules), ["stop", "target", "time"])

    def test_plan_refuses_a_stop_above_the_entry(self):
        status, r = self.call("POST", "/api/plan", {"symbol": "TSLA", "entry": 364.18, "stop": 370})
        self.assertEqual(status, 400)
        self.assertIn("stop", r["error"])
        self.assertEqual(self.call("POST", "/api/plan", {"symbol": "TSLA", "entry": "x", "stop": 1})[0], 400)
        self.assertEqual(self.call("POST", "/api/plan", {"symbol": "TSLA", "entry": 364.18, "stop": 360, "target": 300})[0], 400)

    def test_price_alert(self):
        status, r = self.call("POST", "/api/alerts", {"symbol": "TSLA", "kind": "above", "price": 380, "note": "Chart Lab"})
        self.assertTrue(r["ok"], r)
        rules = self.call("GET", "/api/alerts")[1]["rules"]
        self.assertTrue(any(x.get("type") == "above" and x.get("price") == 380 for x in rules), rules)
        self.assertEqual(self.call("POST", "/api/alerts", {"symbol": "TSLA", "kind": "sideways", "price": 1})[0], 400)

    def test_runs_on_python_3_8(self):
        with open(os.path.join(ROOT, "chart-lab", "server.py"), encoding="utf-8") as fh:
            src = fh.read()
        for bad in ("import zoneinfo", "from zoneinfo", ".removeprefix(", ".removesuffix("):
            self.assertNotIn(bad, src)
        self.assertNotRegex(src, r"(?<![\w.])(list|dict|tuple)\[")


if __name__ == "__main__":
    unittest.main()
