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


class ReadOnly(unittest.TestCase):
    def test_no_connector_can_send_an_order(self):
        with open(C.__file__, encoding="utf-8") as fh:
            src = fh.read().lower()
        for word in ("placeorder", "order/place", "/orders\"", "neworder", "cancelorder", "/order/cancel",
                     "order/create", "/v2/orders", "/v5/order/", "/fapi/v1/order", "order_send"):
            self.assertNotIn(word, src, word)


if __name__ == "__main__":
    unittest.main()
