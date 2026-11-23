import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from _load import load

bridge = load("mindlock-bridge", "server")

TOKEN = "t0ken-tres-long-123456"
# exactly what MINDLOCK EMA CORE TREND V.4 sends in webhook mode
ENTRY = '{"action":"buy","side":"buy","ticker":"XAUUSD","qty":1,"price":3655.12,"sl":3645.12,"tp":3685.12}'
CLOSE = '{"action":"close","side":"sell","ticker":"XAUUSD","qty":1,"reason":"signal_oppose"}'
EXIT_TP = '{"action":"exit","side":"buy","ticker":"XAUUSD","qty":1,"price":3685.12,"reason":"tp"}'
BE = '{"action":"modify_sl","side":"buy","ticker":"XAUUSD","qty":1,"sl":3655.12,"reason":"break_even"}'
ADJ = ('{"action":"modify_sl","side":"sell","ticker":"NAS100","qty":1,"price":25010.5,"sl":25060.5,'
       '"tp":24910.5,"reason":"sl_ajuste"}')
ALL = '{"action":"close","side":"all","ticker":"US30","qty":1,"reason":"fin_de_session"}'


class Parse(unittest.TestCase):
    def test_every_message_the_script_sends(self):
        for body in (ENTRY, CLOSE, EXIT_TP, BE, ADJ, ALL):
            bridge.parse_signal(body)

    def test_entry_fields(self):
        s = bridge.parse_signal(ENTRY)
        self.assertEqual((s["action"], s["side"], s["ticker"], s["qty"]), ("buy", "buy", "XAUUSD", "1"))
        self.assertEqual((s["price"], s["sl"], s["tp"]), ("3655.12", "3645.12", "3685.12"))

    def test_rejects(self):
        bad = ['not json', '[]', '{"action":"hack","side":"buy","ticker":"X"}',
               '{"action":"buy","side":"sell","ticker":"X","sl":1}',
               '{"action":"buy","side":"buy","ticker":"X"}',                          # no sl
               '{"action":"buy","side":"buy","ticker":"X","price":10,"sl":11}',       # sl above a buy
               '{"action":"sell","side":"sell","ticker":"X","price":10,"sl":9}',      # sl below a sell
               '{"action":"buy","side":"buy","ticker":"X|Y","sl":1}',                 # breaks the line format
               '{"action":"modify_sl","side":"buy","ticker":"X"}',
               '{"action":"buy","side":"buy","ticker":"X","sl":"NaN"}']
        for body in bad:
            with self.assertRaises(bridge.BadSignal, msg=body):
                bridge.parse_signal(body)


class QueueTests(unittest.TestCase):
    def test_ids_dedup_and_render(self):
        t = [1000.0]
        q = bridge.Queue(clock=lambda: t[0])
        a = q.add(bridge.parse_signal(ENTRY))
        self.assertIsNone(q.add(bridge.parse_signal(ENTRY)))      # TradingView double-send
        t[0] += 6
        b = q.add(bridge.parse_signal(ENTRY))                     # same signal later is a new one
        self.assertEqual((a["id"], b["id"]), (1, 2))
        lines = q.render(1).strip().split("\n")
        head = lines[0].split("|")
        self.assertEqual(head[0], "OK")
        self.assertEqual(head[3], "2")
        self.assertEqual(len(lines), 2)
        row = lines[1].split("|")
        self.assertEqual(len(row), 11)
        self.assertEqual(row[2:9], ["buy", "buy", "XAUUSD", "1", "3655.12", "3645.12", "3685.12"])


class Http(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.q = bridge.Queue()
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), bridge.make_handler(cls.q, TOKEN))
        cls.base = "http://127.0.0.1:%d" % cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def call(self, method, path, body=None):
        data = body.encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "text/plain"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8")

    def test_flow(self):
        self.assertEqual(self.call("GET", "/health"), (200, "ok\n"))
        self.assertEqual(self.call("POST", "/webhook/wrong-token", ENTRY)[0], 404)
        self.assertEqual(self.call("GET", "/signals/wrong-token")[0], 404)
        code, text = self.call("POST", "/webhook/" + TOKEN, ADJ)
        self.assertEqual(code, 200)
        self.assertTrue(text.startswith("queued"))
        self.assertEqual(self.call("POST", "/webhook/" + TOKEN, "hello")[0], 400)
        code, text = self.call("GET", "/signals/%s?after=0" % TOKEN)
        self.assertEqual(code, 200)
        self.assertIn("|modify_sl|sell|NAS100|", text)


if __name__ == "__main__":
    unittest.main()
