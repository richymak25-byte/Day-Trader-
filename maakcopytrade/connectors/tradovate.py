"""Tradovate REST connector (prop firm futures accounts hosted on Tradovate).

Needs Tradovate API access (API key: cid + sec) on the user that owns the
accounts. Polling based: positions are read on every engine tick.
"""
import time

from .. import instruments
from ..models import AccountInfo, Fill, Position
from ..stats import pair_fills_fifo
from .base import Connector, ConnectorError, http_json

HOSTS = {"demo": "https://demo.tradovateapi.com/v1", "live": "https://live.tradovateapi.com/v1"}


class TradovateConnector(Connector):
    name = "tradovate"

    def __init__(self, params, edition):
        super().__init__(params, edition)
        self.base = HOSTS.get(self.params.get("env", "demo"), HOSTS["demo"])
        self.token = None
        self.expires = 0.0
        self.account_id = None
        self.account_name = None
        self.contracts = {}       # id -> name
        self.contract_ids = {}    # name -> id

    def _auth(self):
        p = self.params
        body = {"name": p.get("username"), "password": p.get("password"), "appId": p.get("app_id") or "MaakCopyTrade",
                "appVersion": p.get("app_version") or "1.0", "cid": p.get("cid"), "sec": p.get("sec"),
                "deviceId": p.get("device_id") or "maakcopytrade-local"}
        r = http_json("POST", self.base + "/auth/accesstokenrequest", body)
        if not r or "accessToken" not in r:
            raise ConnectorError("Authentification Tradovate refusee: %s" % (r or {}).get("errorText", r))
        self.token = r["accessToken"]
        self.expires = time.time() + 75 * 60

    def _call(self, method, path, body=None):
        if not self.token or time.time() > self.expires:
            self._auth()
        return http_json(method, self.base + path, body, {"Authorization": "Bearer " + self.token})

    def connect(self):
        self._auth()
        want = (self.params.get("account") or "").strip()
        accts = self._call("GET", "/account/list") or []
        for a in accts:
            if not want or a.get("name") == want or str(a.get("id")) == want:
                self.account_id, self.account_name = a["id"], a["name"]
                return
        raise ConnectorError("Compte Tradovate '%s' introuvable (disponibles: %s)"
                             % (want, ", ".join(a.get("name", "?") for a in accts)))

    def _contract_name(self, cid):
        if cid not in self.contracts:
            c = self._call("GET", "/contract/item?id=%s" % cid) or {}
            self.contracts[cid] = c.get("name", str(cid))
            self.contract_ids[self.contracts[cid]] = cid
        return self.contracts[cid]

    def _contract_id(self, name):
        if name not in self.contract_ids:
            c = self._call("GET", "/contract/find?name=%s" % name) or {}
            if "id" not in c:
                raise ConnectorError("Contrat Tradovate inconnu: %s" % name)
            self.contract_ids[name] = c["id"]
            self.contracts[c["id"]] = name
        return self.contract_ids[name]

    def account(self):
        r = self._call("POST", "/cashBalance/getcashbalancesnapshot", {"accountId": self.account_id}) or {}
        bal = float(r.get("totalCashValue") or r.get("netLiq") or 0)
        open_pnl = float(r.get("openPnL") or 0)
        return AccountInfo(bal, bal + open_pnl, "USD")

    def positions(self):
        out = []
        for p in self._call("GET", "/position/list") or []:
            if p.get("accountId") != self.account_id or not p.get("netPos"):
                continue
            out.append(Position(self._contract_name(p["contractId"]), float(p["netPos"]),
                                float(p.get("netPrice") or 0)))
        return out

    def market_order(self, symbol, qty, sl=0.0, tp=0.0, comment=""):
        body = {"accountSpec": self.account_name, "accountId": self.account_id,
                "action": "Buy" if qty > 0 else "Sell", "symbol": symbol, "orderQty": int(abs(qty)),
                "orderType": "Market", "isAutomated": True}
        r = self._call("POST", "/order/placeorder", body) or {}
        if r.get("failureReason") or "orderId" not in r:
            raise ConnectorError("Ordre refuse: %s" % (r.get("failureText") or r.get("failureReason") or r))
        return str(r["orderId"])

    def flatten(self, symbol=None):
        for p in self.positions():
            if symbol and p.symbol != symbol:
                continue
            self._call("POST", "/order/liquidateposition",
                       {"accountId": self.account_id, "contractId": self._contract_id(p.symbol), "admin": False})

    def closed_trades(self, since):
        orders = {o["id"]: o.get("accountId") for o in (self._call("GET", "/order/list") or [])}
        fills = []
        for f in self._call("GET", "/fill/list") or []:
            if orders.get(f.get("orderId")) != self.account_id or not f.get("active", True):
                continue
            ts = _parse_ts(f.get("timestamp"))
            q = float(f.get("qty") or 0) * (1 if f.get("action") == "Buy" else -1)
            fills.append(Fill(str(f["id"]), self._contract_name(f["contractId"]), q, float(f["price"]), ts))
        return [t for t in pair_fills_fifo(fills, instruments.point_value) if t.close_time > since]


def _parse_ts(text):
    import datetime as dt
    if not text:
        return time.time()
    try:
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return time.time()
