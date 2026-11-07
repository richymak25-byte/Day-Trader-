"""ProjectX Gateway API connector (TopstepX and the other ProjectX prop firms).

base_url is the firm's gateway, e.g. https://api.topstepx.com. The API key is
created in the firm's platform settings (API access may be a paid add-on).
"""
import datetime as dt
import time

from .. import instruments
from ..models import AccountInfo, Fill, Position
from ..stats import pair_fills_fifo
from .base import Connector, ConnectorError, http_json


class ProjectXConnector(Connector):
    name = "projectx"

    def __init__(self, params, edition):
        super().__init__(params, edition)
        self.base = (self.params.get("base_url") or "https://api.topstepx.com").rstrip("/")
        self.token = None
        self.expires = 0.0
        self.account_id = None
        self.names = {}     # contractId -> name
        self.ids = {}       # name -> contractId
        self.ticks = {}     # contractId -> (tick size, tick value)
        self._last_bal = 0.0

    def _auth(self):
        r = http_json("POST", self.base + "/api/Auth/loginKey",
                      {"userName": self.params.get("username"), "apiKey": self.params.get("api_key")})
        if not r or not r.get("success") or not r.get("token"):
            raise ConnectorError("Authentification ProjectX refusee: %s" % (r or {}).get("errorMessage"))
        self.token = r["token"]
        self.expires = time.time() + 20 * 3600

    def _call(self, path, body):
        if not self.token or time.time() > self.expires:
            self._auth()
        r = http_json("POST", self.base + path, body, {"Authorization": "Bearer " + self.token}) or {}
        if r.get("success") is False:
            raise ConnectorError("%s: %s" % (path, r.get("errorMessage") or r.get("errorCode")))
        return r

    def _accounts(self):
        return self._call("/api/Account/search", {"onlyActiveAccounts": True}).get("accounts", [])

    def connect(self):
        self._auth()
        want = (self.params.get("account") or "").strip()
        accts = self._accounts()
        for a in accts:
            if not want or a.get("name") == want or str(a.get("id")) == want:
                self.account_id = a["id"]
                return
        raise ConnectorError("Compte ProjectX '%s' introuvable (disponibles: %s)"
                             % (want, ", ".join(a.get("name", "?") for a in accts)))

    def _name(self, cid):
        if cid not in self.names:
            c = self._call("/api/Contract/searchById", {"contractId": cid}).get("contract") or {}
            self.names[cid] = c.get("name") or cid
            self.ids[self.names[cid]] = cid
            if c.get("tickSize"):
                self.ticks[cid] = (c["tickSize"], c.get("tickValue"))
        return self.names[cid]

    def _id(self, name):
        if name not in self.ids:
            r = self._call("/api/Contract/search", {"searchText": name, "live": False})
            for c in r.get("contracts", []):
                self.ids[c["name"]] = c["id"]
                self.names[c["id"]] = c["name"]
            if name not in self.ids:
                raise ConnectorError("Contrat ProjectX inconnu: %s" % name)
        return self.ids[name]

    def account(self):
        bal = self._last_bal
        for a in self._accounts():
            if a["id"] == self.account_id:
                bal = float(a.get("balance") or 0)
                self._last_bal = bal
        unreal = 0.0   # the REST API has no open P&L; estimated from the last trade price is not available
        return AccountInfo(bal, bal + unreal, "USD")

    def positions(self):
        out = []
        for p in self._call("/api/Position/searchOpen", {"accountId": self.account_id}).get("positions", []):
            size = float(p.get("size") or 0)
            if not size:
                continue
            sign = 1 if p.get("type") == 1 else -1
            out.append(Position(self._name(p["contractId"]), sign * size, float(p.get("averagePrice") or 0)))
        return out

    def market_order(self, symbol, qty, sl=0.0, tp=0.0, comment=""):
        r = self._call("/api/Order/place", {"accountId": self.account_id, "contractId": self._id(symbol),
                                            "type": 2, "side": 0 if qty > 0 else 1, "size": int(abs(qty))})
        return str(r.get("orderId", ""))

    def flatten(self, symbol=None):
        for p in self.positions():
            if symbol and p.symbol != symbol:
                continue
            self._call("/api/Position/closeContract", {"accountId": self.account_id, "contractId": self._id(p.symbol)})

    def closed_trades(self, since):
        start = dt.datetime.fromtimestamp(max(since - 86400 * 2, time.time() - 86400 * 30), dt.timezone.utc)
        r = self._call("/api/Trade/search", {"accountId": self.account_id, "startTimestamp": start.isoformat()})
        fills = []
        for t in r.get("trades", []):
            if t.get("voided"):
                continue
            ts = dt.datetime.fromisoformat(t["creationTimestamp"].replace("Z", "+00:00")).timestamp()
            q = float(t.get("size") or 0) * (1 if t.get("side") == 0 else -1)
            fills.append(Fill(str(t["id"]), self._name(t["contractId"]), q, float(t["price"]), ts,
                              float(t.get("fees") or 0)))
        return [x for x in pair_fills_fifo(fills, instruments.point_value) if x.close_time > since]
