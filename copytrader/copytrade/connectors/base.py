"""Connector interface: one instance per trading account."""
import json
import ssl
import urllib.error
import urllib.request


class ConnectorError(Exception):
    pass


class Connector:
    """Every broker adapter implements these methods.

    qty is always signed (> 0 buy / long, < 0 sell / short), in contracts for
    futures and lots for CFD.
    """
    name = "base"
    hedging = False          # True: positions are tickets (MT5 hedging), False: one net position per symbol

    def __init__(self, params, edition):
        self.params = params or {}
        self.edition = edition

    def connect(self):
        pass

    def close(self):
        pass

    def account(self):
        raise NotImplementedError

    def positions(self):
        raise NotImplementedError

    def market_order(self, symbol, qty, sl=0.0, tp=0.0, comment=""):
        """Returns the new position ticket for hedging accounts, an order id otherwise."""
        raise NotImplementedError

    def close_ticket(self, ticket, volume=None):
        raise ConnectorError("close_ticket non supporte par %s" % self.name)

    def modify_ticket(self, ticket, sl, tp):
        raise ConnectorError("modify_ticket non supporte par %s" % self.name)

    def flatten(self, symbol=None):
        for p in self.positions():
            if symbol and p.symbol != symbol:
                continue
            if self.hedging and p.ticket:
                self.close_ticket(p.ticket)
            elif p.qty:
                self.market_order(p.symbol, -p.qty, comment="flatten")

    def closed_trades(self, since):
        return []


def http_json(method, url, body=None, headers=None, timeout=10):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        raise ConnectorError("HTTP %s sur %s: %s" % (e.code, url, e.read()[:300].decode("utf-8", "replace")))
    except urllib.error.URLError as e:
        raise ConnectorError("Connexion impossible a %s: %s" % (url, e.reason))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        raise ConnectorError("Reponse non JSON de %s" % url)
