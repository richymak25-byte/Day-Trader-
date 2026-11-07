"""MINDLOCK Journal connectors, run against canned API answers (no network)."""
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone

from _load import load

C = load("mindlock-journal", "connectors")
SINCE = datetime(2026, 1, 1, tzinfo=timezone.utc)


class Fake(object):
    """Answers by URL fragment; records every call."""
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, method, url, headers=None, body=None, timeout=30):
        self.calls.append((method, url, headers or {}, body))
        for frag, answer in self.routes:
            if frag in url:
                a = answer(url, body) if callable(answer) else answer
                return a if isinstance(a, str) else json.dumps(a)
        raise C.ConnectorError("no route for " + url)


def use(routes):
    fake = Fake(routes)
    C.HTTP = fake
    C.SLEEP = lambda s: None
    return fake


class Time(unittest.TestCase):
    def test_parse_utc_and_zones(self):
        t = C.parse_utc("2026-11-04T14:35:10.123456789Z")
        self.assertEqual(C.to_local(t, "America/New_York"), "2026-11-04 09:35:10")  # EST
        self.assertEqual(C.to_local(C.parse_utc("2026-07-01T13:30:00Z"), "America/New_York"), "2026-07-01 09:30:00")
        self.assertEqual(C.to_local(C.parse_utc("2026-07-01T13:30:00+00:00"), "Europe/Paris"), "2026-07-01 15:30:00")
        self.assertEqual(C.to_local(C.parse_utc("2026-12-01T13:30:00Z"), "Europe/Paris"), "2026-12-01 14:30:00")
        self.assertEqual(C.to_local(C.parse_utc(1793799310000), "UTC"), "2026-11-04 13:35:10")
        self.assertEqual(C.to_local(C.parse_utc("2025-01-21T16:13:52.523293+00:00"), "UTC"), "2025-01-21 16:13:52")

    def test_env_secret(self):
        os.environ["MINDLOCK_TEST_KEY"] = "abc"
        self.assertEqual(C.secret({"k": "env:MINDLOCK_TEST_KEY"}, "k"), "abc")
        with self.assertRaises(C.ConnectorError):
            C.secret({"k": "env:MINDLOCK_NOT_SET"}, "k")


class Connectors(unittest.TestCase):
    def test_projectx_topstepx(self):
        fake = use([
            ("/api/Auth/loginKey", {"token": "TKN", "success": True, "errorCode": 0}),
            ("/api/Account/search", {"accounts": [{"id": 7, "name": "50KTC-1", "canTrade": True}], "success": True}),
            ("/api/Trade/search", {"trades": [
                {"id": 1, "accountId": 7, "contractId": "CON.F.US.EP.H25", "creationTimestamp": "2026-03-02T14:40:00+00:00",
                 "price": 6000.0, "profitAndLoss": None, "fees": 1.4, "commissions": 0.0, "side": 0, "size": 1,
                 "voided": False, "orderId": 10},
                {"id": 2, "accountId": 7, "contractId": "CON.F.US.EP.H25", "creationTimestamp": "2026-03-02T14:50:00+00:00",
                 "price": 6004.0, "profitAndLoss": 200.0, "fees": 1.4, "commissions": 0.0, "side": 1, "size": 1,
                 "voided": False, "orderId": 11},
                {"id": 3, "accountId": 7, "contractId": "CON.F.US.EP.H25", "creationTimestamp": "2026-03-02T15:00:00+00:00",
                 "price": 1.0, "side": 0, "size": 1, "voided": True}], "success": True}),
            ("/api/Contract/searchById", {"contract": {"id": "CON.F.US.EP.H25", "name": "ESH6", "tickSize": 0.25,
                                                       "tickValue": 12.5}}),
        ])
        e, t, m = C.projectx({"name": "Topstep", "username": "u", "api_key": "k"}, SINCE, "UTC")
        self.assertEqual(len(e), 2)  # the voided one is dropped
        self.assertEqual((e[0]["symbol"], e[0]["side"], e[1]["side"], e[0]["account"]), ("ESH6", "buy", "sell", "50KTC-1"))
        self.assertEqual(m, {"ESH6": 50.0})
        self.assertEqual(fake.calls[1][2]["Authorization"], "Bearer TKN")
        trades = C._sync().pair(e, m)
        self.assertEqual((trades[0]["pnl"], trades[0]["account"]), (197.2, "50KTC-1"))  # 4 pts x $50 - 2.80

    def test_projectx_refused_login(self):
        use([("/api/Auth/loginKey", {"success": False, "errorCode": 3, "errorMessage": "InvalidCredentials"})])
        with self.assertRaises(C.ConnectorError):
            C.projectx({"username": "u", "api_key": "bad"}, SINCE, "UTC")

    def test_alpaca_pages(self):
        page1 = [{"id": "a%d" % i, "transaction_time": "2026-11-04T14:35:10Z", "symbol": "TSLA", "side": "buy",
                  "qty": "1", "price": "360"} for i in range(100)]
        page2 = [{"id": "z", "transaction_time": "2026-11-04T15:00:00Z", "symbol": "TSLA", "side": "sell_short",
                  "qty": "100", "price": "361"}]
        fake = use([("page_token=a99", page2), ("/v2/account/activities/FILL", page1)])
        e, _, _ = C.alpaca({"key_id": "k", "secret_key": "s"}, SINCE, "America/New_York")
        self.assertEqual(len(e), 101)
        self.assertEqual((e[-1]["side"], e[0]["time"]), ("sell", "2026-11-04 09:35:10"))
        self.assertIn("paper-api.alpaca.markets", fake.calls[0][1])

    def test_ibkr_flex(self):
        send = ("<FlexStatementResponse><Status>Success</Status><ReferenceCode>999</ReferenceCode>"
                "<Url>https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/GetStatement</Url>"
                "</FlexStatementResponse>")
        wait = "<FlexStatementResponse><Status>Warn</Status><ErrorCode>1019</ErrorCode></FlexStatementResponse>"
        stmt = ('<FlexQueryResponse><FlexStatements><FlexStatement><Trades>'
                '<Trade accountId="U1" symbol="MES" buySell="BUY" quantity="2" tradePrice="6000" '
                'dateTime="20261104;093510" ibCommission="-1.24" multiplier="5" ibExecID="x1" levelOfDetail="EXECUTION"/>'
                '<Trade accountId="U1" symbol="MES" buySell="SELL" quantity="-2" tradePrice="6010" '
                'dateTime="2026-11-04;10:05:00" ibCommission="-1.24" multiplier="5" ibExecID="x2" levelOfDetail="EXECUTION"/>'
                '<Trade accountId="U1" symbol="MES" buySell="SELL" quantity="-2" tradePrice="6010" levelOfDetail="ORDER"/>'
                '</Trades></FlexStatement></FlexStatements></FlexQueryResponse>')
        answers = [wait, stmt]
        use([("SendRequest", send), ("GetStatement", lambda u, b: answers.pop(0))])
        e, _, m = C.ibkr({"token": "t", "query_id": "q"}, SINCE, "UTC")
        self.assertEqual(len(e), 2)
        self.assertEqual(m, {"MES": 5.0})
        t = C._sync().pair(e, m)[0]
        self.assertEqual((t["pnl"], t["closeTime"], t["account"]), (97.52, "2026-11-04 10:05:00", "U1"))

    def test_tradovate(self):
        use([("/auth/accesstokenrequest", {"accessToken": "T"}),
             ("/account/list", [{"id": 5, "name": "APEX-123-01"}]),
             ("/order/list", [{"id": 50, "accountId": 5}, {"id": 51, "accountId": 5}]),
             ("/fillFee/list", [{"id": 500, "commission": 0.5, "exchangeFee": 0.37}]),
             ("/fill/list", [{"id": 500, "orderId": 50, "contractId": 9, "timestamp": "2026-11-05T14:45:00.000Z",
                              "action": "Sell", "qty": 2, "price": 21000.25, "active": True},
                             {"id": 501, "orderId": 51, "contractId": 9, "timestamp": "2026-11-05T14:59:00.000Z",
                              "action": "Buy", "qty": 2, "price": 20990, "active": True}]),
             ("/contract/item", {"id": 9, "name": "MNQZ6"})])
        e, _, _ = C.tradovate({"username": "u", "password": "p"}, SINCE, "UTC")
        self.assertEqual((e[0]["account"], e[0]["symbol"], e[0]["fee"]), ("APEX-123-01", "MNQZ6", 0.87))
        t = C._sync().pair(e)[0]
        self.assertEqual((t["side"], t["pnl"]), ("short", 40.13))  # 10.25 x 2 x $2 - 0.87

    def test_mt5_positions(self):
        deals = [
            {"type": 2, "entry": 0, "position_id": 0, "profit": 100000, "time": 0, "volume": 0, "price": 0},  # deposit
            {"type": 0, "entry": 0, "position_id": 11, "symbol": "XAUUSD", "volume": 0.5, "price": 2400.0,
             "time": 1793800000, "time_msc": 1793800000000, "profit": 0, "commission": -2.5, "swap": 0, "fee": 0},
            {"type": 1, "entry": 1, "position_id": 11, "symbol": "XAUUSD", "volume": 0.2, "price": 2410.0,
             "time": 1793800600, "time_msc": 1793800600000, "profit": 200, "commission": -1, "swap": 0, "fee": 0},
            {"type": 1, "entry": 1, "position_id": 11, "symbol": "XAUUSD", "volume": 0.3, "price": 2405.0,
             "time": 1793801200, "time_msc": 1793801200000, "profit": 150, "commission": -1.5, "swap": -0.4, "fee": 0},
            {"type": 1, "entry": 0, "position_id": 12, "symbol": "EURUSD", "volume": 1, "price": 1.08,
             "time": 1793802000, "profit": 0, "commission": -3, "swap": 0, "fee": 0}]  # still open
        t = C.mt5_trades(deals, "FTMO 100K")
        self.assertEqual(len(t), 1)
        t = t[0]
        self.assertEqual((t["side"], t["qty"], t["entry"], t["exit"]), ("long", 0.5, 2400.0, 2407.0))
        self.assertEqual((t["pnl"], t["fees"], t["account"]), (344.6, 5.0, "FTMO 100K"))

    def test_oanda(self):
        use([("/v3/accounts/A1/trades", {"trades": [
            {"id": "77", "instrument": "EUR_USD", "price": "1.0850", "openTime": "2026-11-05T13:00:00.000000000Z",
             "initialUnits": "-10000", "averageClosePrice": "1.0830", "closeTime": "2026-11-05T14:30:00.000000000Z",
             "realizedPL": "20.0000", "financing": "-0.5000"}]})])
        _, t, _ = C.oanda({"token": "t", "account_id": "A1"}, SINCE, "UTC")
        self.assertEqual((t[0]["symbol"], t[0]["side"], t[0]["qty"], t[0]["pnl"]), ("EURUSD", "short", 10000.0, 19.5))

    def test_bybit_signs_and_pages(self):
        fake = use([("/v5/execution/list", lambda u, b: {"retCode": 0, "result": {
            "list": [{"execId": "e1" + u[-5:], "symbol": "BTCUSDT", "side": "Buy", "execQty": "0.01",
                      "execPrice": "70000", "execFee": "0.4", "execTime": "1793800000000", "execType": "Trade"},
                     {"execId": "f", "symbol": "BTCUSDT", "side": "Sell", "execQty": "0.01", "execPrice": "0",
                      "execTime": "1793800000000", "execType": "Funding"}], "nextPageCursor": ""}})])
        since = datetime.now(timezone.utc).replace(microsecond=0) - __import__("datetime").timedelta(days=10)
        e, _, _ = C.bybit({"api_key": "k", "api_secret": "s"}, since, "UTC")
        self.assertEqual(len(fake.calls), 2)  # two 7-day windows
        self.assertEqual(len(e), 2)  # funding rows skipped
        self.assertEqual(len(fake.calls[0][2]["X-BAPI-SIGN"]), 64)

    def test_folder_of_exports(self):
        tmp = tempfile.mkdtemp()
        try:
            with open(os.path.join(tmp, "ninja.csv"), "w") as fh:
                fh.write("Time,Instrument,Action,Quantity,Price,Account\n"
                         "2026-11-05 09:45:00,NQ 12-26,Buy,1,21000,Sim101\n"
                         "2026-11-05 09:50:00,NQ 12-26,Sell,1,21005,Sim101\n")
            e, t, _ = C.folder({"path": tmp}, SINCE, "UTC")
            trades = C._sync().pair(e)
            self.assertEqual((trades[0]["account"], trades[0]["pnl"]), ("Sim101", 100.0))  # NQ: $20 a point
        finally:
            shutil.rmtree(tmp)

    def test_one_broken_connector_does_not_stop_the_others(self):
        use([("/api/Auth/loginKey", {"success": False, "errorMessage": "nope"}),
             ("/v3/accounts/A1/trades", {"trades": []})])
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "connectors.json")
            with open(path, "w") as fh:
                json.dump({"timezone": "UTC", "connectors": [
                    {"name": "Topstep", "type": "projectx", "username": "u", "api_key": "k"},
                    {"name": "Off", "type": "alpaca", "enabled": False},
                    {"name": "Odd", "type": "nope"},
                    {"name": "OANDA", "type": "oanda", "token": "t", "account_id": "A1"}]}, fh)
            logs = []
            e, t, m = C.run_all(path, logs.append)
            self.assertEqual((e, t), ([], []))
            self.assertTrue(any("Topstep" in l and "refused" in l for l in logs), logs)
            self.assertTrue(any("unknown type" in l for l in logs), logs)
            self.assertTrue(any(l.startswith("  OANDA: 0 executions") for l in logs), logs)
            self.assertFalse(any("Off" in l for l in logs))
        finally:
            shutil.rmtree(tmp)


class FakeMT5(object):
    """Stands in for the MetaTrader5 package (Windows only)."""
    def __init__(self, login=123456, deals=()):
        self.info = type("Info", (), {"login": login, "server": "FTMO-Server2", "company": "FTMO"})()
        self.deals, self.paths, self.logins, self.closed = list(deals), [], [], 0

    def initialize(self, path=None, **kw):
        self.paths.append(path)
        return True

    def account_info(self):
        return self.info

    def login(self, *a, **kw):
        self.logins.append(a)
        return True

    def history_deals_get(self, a, b):
        return self.deals

    def last_error(self):
        return (1, "x")

    def shutdown(self):
        self.closed += 1


class MT5Attach(unittest.TestCase):
    DEALS = [{"type": 0, "entry": 0, "position_id": 1, "symbol": "US30", "volume": 1, "price": 44000, "time": 1793800000,
              "profit": 0, "commission": -3, "swap": 0, "fee": 0},
             {"type": 1, "entry": 1, "position_id": 1, "symbol": "US30", "volume": 1, "price": 44050, "time": 1793800900,
              "profit": 50, "commission": -3, "swap": 0, "fee": 0}]

    def setUp(self):
        import sys
        self.tmp = tempfile.mkdtemp()
        for d in ("FTMO Global Markets MT5 Terminal", "FundedNext MT5 Terminal"):
            os.makedirs(os.path.join(self.tmp, d))
            open(os.path.join(self.tmp, d, "terminal64.exe"), "w").close()
        self.env = dict(os.environ)
        for k in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "LOCALAPPDATA"):
            os.environ.pop(k, None)
        os.environ["ProgramFiles"] = self.tmp
        self.fake = FakeMT5(deals=self.DEALS)
        sys.modules["MetaTrader5"] = self.fake

    def tearDown(self):
        import sys
        sys.modules.pop("MetaTrader5", None)
        os.environ.clear()
        os.environ.update(self.env)
        shutil.rmtree(self.tmp)

    def test_attaches_to_the_firm_terminal_without_a_password(self):
        _, t, _ = C.mt5({"name": "FTMO", "terminal": "ftmo"}, SINCE, "UTC")
        self.assertIn("FTMO Global Markets", self.fake.paths[0])
        self.assertEqual((t[0]["account"], t[0]["pnl"], t[0]["side"]), ("FTMO #123456", 44.0, "long"))
        self.assertEqual((self.fake.logins, self.fake.closed), ([], 1))

    def test_never_borrows_another_firm_terminal(self):
        with self.assertRaises(C.ConnectorError) as cm:
            C.mt5({"name": "FundingPips", "terminal": "fundingpips"}, SINCE, "UTC")
        self.assertIn("--list-mt5", str(cm.exception))
        self.assertEqual(self.fake.paths, [])

    def test_never_switches_the_account_you_trade_on(self):
        with self.assertRaises(C.ConnectorError) as cm:
            C.mt5({"name": "FTMO", "login": 999}, SINCE, "UTC")
        self.assertIn("123456", str(cm.exception))
        self.assertEqual(self.fake.logins, [])
        self.assertEqual(self.fake.closed, 1)

    def test_switches_only_when_allowed(self):
        C.mt5({"name": "FTMO", "login": 999, "allow_login": True, "password": "inv", "server": "S"}, SINCE, "UTC")
        self.assertEqual(self.fake.logins, [(999,)])


class WindowsFiles(unittest.TestCase):
    def test_launchers_and_template(self):
        root = os.path.dirname(C.__file__)
        with open(os.path.join(root, "connectors.prop-firms.example.json"), encoding="utf-8") as fh:
            conf = json.load(fh)
        on = [c for c in conf["connectors"] if c.get("enabled", True)]
        self.assertEqual([c["name"] for c in on], ["FTMO", "FundedNext", "FundingPips", "Foxx Funded", "Topstep", "Lucid"])
        for c in conf["connectors"]:
            self.assertIn(c["type"], C.TYPES)
        for name in ("Installer MINDLOCK.bat", "Lancer MINDLOCK.bat"):
            with open(os.path.join(root, "windows", name), "rb") as fh:
                raw = fh.read()
            self.assertIn(b"\r\n", raw)
            self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))
            self.assertIn(b"mindlock-journal\\sync.py", raw)
            raw.decode("ascii")  # plain ASCII: no codepage surprises in cmd.exe


class ReadOnly(unittest.TestCase):
    def test_no_connector_can_send_an_order(self):
        with open(C.__file__, encoding="utf-8") as fh:
            src = fh.read().lower()
        for word in ("placeorder", "order/place", "/orders\"", "neworder", "cancelorder", "/order/cancel",
                     "order/create", "/v2/orders", "/v5/order/", "/fapi/v1/order", "order_send"):
            self.assertNotIn(word, src, word)


if __name__ == "__main__":
    unittest.main()
