"""MINDLOCK Journal: broker and prop firm connectors.

Each connector reads your own account history through the broker's official
API and returns executions (paired into trades by sync.py) or closed trades.
Read only: no connector can place, change or cancel an order.

Settings live in trader/journal/connectors.json (see connectors.example.json).
A secret can be written as "env:NAME" to read it from an environment variable.

    type        covers
    alpaca      Alpaca (paper and live)
    ibkr        Interactive Brokers, through a Flex Query (token + query id)
    projectx    TopstepX and the prop firms on ProjectX (API key)
    tradovate   Tradovate and the prop firms on Tradovate (API access required)
    mt5         MetaTrader 5: FTMO, FundedNext, The5ers, E8 and every MT5 broker or prop firm (Windows)
    oanda       OANDA v20 (practice and live)
    bybit       Bybit (USDT perpetuals, spot...): crypto prop firms on Bybit
    binance     Binance USD-M futures
    folder      any folder (Downloads, a prop firm export folder...): every CSV dropped there

Python 3.8+, standard library only (mt5 needs `pip install MetaTrader5`).
"""
import glob
import hashlib
import hmac
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone


class ConnectorError(Exception):
    pass


# ---------------------------------------------------------------- http

def _request(method, url, headers=None, body=None, timeout=30):
    headers = dict(headers or {})
    headers.setdefault("User-Agent", "MINDLOCK-Journal/1.0")
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    host = urllib.parse.urlsplit(url).netloc
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = e.read()[:300].decode("utf-8", "replace")
        raise ConnectorError("HTTP %s from %s: %s" % (e.code, host, detail))
    except urllib.error.URLError as e:
        raise ConnectorError("cannot reach %s: %s" % (host, e.reason))


HTTP = _request  # the tests swap this for canned responses
SLEEP = time.sleep


def get_json(url, headers=None):
    return json.loads(HTTP("GET", url, headers))


def post_json(url, body, headers=None):
    return json.loads(HTTP("POST", url, headers, body))


def qs(params):
    return urllib.parse.urlencode([(k, v) for k, v in params.items() if v is not None])


# ---------------------------------------------------------------- settings and time

def secret(cfg, key, required=True):
    v = cfg.get(key)
    if isinstance(v, str) and v.startswith("env:"):
        v = os.environ.get(v[4:])
    if required and not v:
        raise ConnectorError("missing '%s' (put it in connectors.json or in an env: variable)" % key)
    return v


def _nth_sunday(year, month, n):
    d = datetime(year, month, 1)
    d += timedelta(days=(6 - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last_sunday(year, month):
    d = datetime(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() + 1) % 7)


def _us_dst(utc):
    start = _nth_sunday(utc.year, 3, 2) + timedelta(hours=7)   # 2:00 local, as UTC for -5
    end = _nth_sunday(utc.year, 11, 1) + timedelta(hours=6)
    return start <= utc.replace(tzinfo=None) < end


def _eu_dst(utc):
    start = _last_sunday(utc.year, 3) + timedelta(hours=1)     # 1:00 UTC everywhere in the EU
    end = _last_sunday(utc.year, 10) + timedelta(hours=1)
    return start <= utc.replace(tzinfo=None) < end


ZONES = {"America/New_York": (-5, _us_dst), "America/Toronto": (-5, _us_dst),
         "America/Chicago": (-6, _us_dst), "America/Montreal": (-5, _us_dst),
         "Europe/Paris": (1, _eu_dst), "Europe/Brussels": (1, _eu_dst), "Europe/Zurich": (1, _eu_dst),
         "Europe/Berlin": (1, _eu_dst), "Europe/Madrid": (1, _eu_dst), "Europe/Rome": (1, _eu_dst),
         "Europe/London": (0, _eu_dst), "Europe/Lisbon": (0, _eu_dst), "Africa/Abidjan": (0, None),
         "Africa/Dakar": (0, None), "Africa/Casablanca": (1, None), "Africa/Lagos": (1, None),
         "Africa/Douala": (1, None), "Africa/Kinshasa": (1, None), "UTC": (0, None)}


def to_local(utc, tz="local"):
    """An aware UTC datetime to 'YYYY-MM-DD HH:MM:SS' in the journal's time zone."""
    if utc.tzinfo is None:
        utc = utc.replace(tzinfo=timezone.utc)
    if tz in ZONES:
        std, dst = ZONES[tz]
        off = std + (1 if dst and dst(utc.astimezone(timezone.utc)) else 0)
        local = utc.astimezone(timezone.utc) + timedelta(hours=off)
    else:
        local = utc.astimezone()  # the computer's own time zone
    return local.strftime("%Y-%m-%d %H:%M:%S")


def parse_utc(s):
    """ISO 8601 with Z, offsets and up to 9 fraction digits, or epoch seconds / milliseconds."""
    if s is None or s == "":
        return None
    if isinstance(s, (int, float)) or re.match(r"^\d{10,13}(\.\d+)?$", str(s)):
        v = float(s)
        return datetime.fromtimestamp(v / 1000.0 if v > 1e11 else v, timezone.utc)
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?(?:\.(\d+))?\s*(Z|[+-]\d{2}:?\d{2})?$",
                 str(s).strip())
    if not m:
        raise ConnectorError("unreadable time: %s" % s)
    y, mo, d, h, mi, sec, frac, off = m.groups()
    dt = datetime(int(y), int(mo), int(d), int(h), int(mi), int(sec or 0),
                  int((frac or "0")[:6].ljust(6, "0")), timezone.utc)
    if off and off != "Z":
        sign = 1 if off[0] == "+" else -1
        hh, mm = int(off[1:3]), int(off[-2:])
        dt -= sign * timedelta(hours=hh, minutes=mm)
    return dt


def iso_z(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def ex(cid, when, symbol, side, qty, price, fee=0.0, account="", source=""):
    return {"id": cid, "time": when, "symbol": str(symbol).upper(), "side": side, "qty": abs(float(qty)),
            "price": float(price), "fee": abs(float(fee or 0)), "account": account or "", "source": source}


# ---------------------------------------------------------------- Alpaca

def alpaca(cfg, since, tz):
    base = cfg.get("base_url") or ("https://paper-api.alpaca.markets" if cfg.get("paper", True)
                                   else "https://api.alpaca.markets")
    h = {"APCA-API-KEY-ID": secret(cfg, "key_id"), "APCA-API-SECRET-KEY": secret(cfg, "secret_key")}
    out, token = [], None
    for _ in range(200):
        page = get_json("%s/v2/account/activities/FILL?%s" % (base, qs(
            {"after": iso_z(since), "direction": "asc", "page_size": 100, "page_token": token})), h)
        for a in page:
            out.append(ex("alpaca:" + a["id"], to_local(parse_utc(a["transaction_time"]), tz), a["symbol"],
                          "buy" if a.get("side") == "buy" else "sell", a["qty"], a["price"],
                          account=cfg.get("name", "Alpaca"), source="alpaca"))
        if len(page) < 100:
            break
        token = page[-1]["id"]
    return out, [], {}


# ---------------------------------------------------------------- Interactive Brokers (Flex Web Service)

FLEX = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService"


def ibkr(cfg, since, tz):
    token, query = secret(cfg, "token"), secret(cfg, "query_id")
    root = ET.fromstring(HTTP("GET", "%s/SendRequest?%s" % (FLEX, qs({"t": token, "q": query, "v": 3}))))
    if (root.findtext("Status") or "") != "Success":
        raise ConnectorError("Flex: %s" % (root.findtext("ErrorMessage") or "request refused"))
    ref, url = root.findtext("ReferenceCode"), root.findtext("Url") or FLEX + "/GetStatement"
    for attempt in range(12):
        root = ET.fromstring(HTTP("GET", "%s?%s" % (url, qs({"t": token, "q": ref, "v": 3}))))
        if root.tag != "FlexStatementResponse":
            break
        code = root.findtext("ErrorCode") or ""
        if code not in ("1018", "1019", "1021"):  # still generating / try again shortly
            raise ConnectorError("Flex: %s" % (root.findtext("ErrorMessage") or code))
        SLEEP(5)
    else:
        raise ConnectorError("Flex statement not ready after a minute, try again")
    return ibkr_parse(root, cfg.get("name"))


def ibkr_parse(root, name=None):
    from_sync = _sync()
    out, mult = [], {}
    for t in root.iter("Trade"):
        a = t.attrib
        if a.get("levelOfDetail", "EXECUTION") != "EXECUTION":
            continue
        side = from_sync.parse_side(a.get("buySell", ""))
        when = from_sync.parse_time(a.get("dateTime") or ("%s %s" % (a.get("tradeDate", ""), a.get("tradeTime", ""))))
        qty, price = from_sync.parse_num(a.get("quantity")), from_sync.parse_num(a.get("tradePrice"))
        if not side or not when or not qty or not price:
            continue
        sym = a.get("symbol", "").upper()
        m = from_sync.parse_num(a.get("multiplier")) or 1.0
        if m != 1.0:
            mult[sym] = m
        out.append(ex("ibkr:" + (a.get("ibExecID") or a.get("tradeID") or "%s%s%s" % (sym, when, qty)), when, sym,
                      side, qty, price, from_sync.parse_num(a.get("ibCommission")) or 0.0,
                      account=name or a.get("accountId", ""), source="ibkr"))
    return out, [], mult


# ---------------------------------------------------------------- ProjectX (TopstepX and other prop firms)

def projectx(cfg, since, tz):
    base = (cfg.get("base_url") or "https://api.topstepx.com").rstrip("/")
    auth = post_json(base + "/api/Auth/loginKey", {"userName": secret(cfg, "username"),
                                                   "apiKey": secret(cfg, "api_key")})
    if not auth.get("success") or not auth.get("token"):
        raise ConnectorError("ProjectX login refused (%s)" % (auth.get("errorMessage") or auth.get("errorCode")))
    h = {"Authorization": "Bearer " + auth["token"], "accept": "text/plain"}
    accts = post_json(base + "/api/Account/search", {"onlyActiveAccounts": bool(cfg.get("only_active", False))}, h)
    wanted = set(cfg.get("accounts") or [])
    contracts, out, mult = {}, [], {}
    for acc in accts.get("accounts", []):
        if wanted and acc.get("name") not in wanted and str(acc.get("id")) not in wanted:
            continue
        res = post_json(base + "/api/Trade/search", {"accountId": acc["id"], "startTimestamp": iso_z(since),
                                                     "endTimestamp": iso_z(datetime.now(timezone.utc))}, h)
        for t in res.get("trades", []):
            if t.get("voided"):
                continue
            cid = t["contractId"]
            if cid not in contracts:
                c = post_json(base + "/api/Contract/searchById", {"contractId": cid}, h).get("contract") or {}
                contracts[cid] = c
            c = contracts[cid]
            sym = c.get("name") or cid.split(".")[-2] + cid.split(".")[-1]
            if c.get("tickSize") and c.get("tickValue"):
                mult[sym.upper()] = float(c["tickValue"]) / float(c["tickSize"])
            out.append(ex("projectx:%s" % t["id"], to_local(parse_utc(t["creationTimestamp"]), tz), sym,
                          "buy" if int(t.get("side", 0)) == 0 else "sell", t["size"], t["price"],
                          (t.get("fees") or 0) + (t.get("commissions") or 0),
                          account=acc.get("name") or str(acc["id"]), source=cfg.get("name", "projectx")))
    return out, [], mult


# ---------------------------------------------------------------- Tradovate (beta)

def tradovate(cfg, since, tz):
    base = cfg.get("base_url") or ("https://demo.tradovateapi.com/v1" if cfg.get("demo")
                                   else "https://live.tradovateapi.com/v1")
    body = {"name": secret(cfg, "username"), "password": secret(cfg, "password"),
            "appId": cfg.get("app_id", "MINDLOCK Journal"), "appVersion": "1.0",
            "cid": secret(cfg, "cid", False), "sec": secret(cfg, "sec", False)}
    if cfg.get("device_id"):
        body["deviceId"] = cfg["device_id"]
    auth = post_json(base + "/auth/accesstokenrequest", dict((k, v) for k, v in body.items() if v))
    if not auth.get("accessToken"):
        raise ConnectorError("Tradovate login refused (%s)" % (auth.get("errorText") or "no token, is API access on?"))
    h = {"Authorization": "Bearer " + auth["accessToken"]}
    accounts = dict((a["id"], a.get("name", str(a["id"]))) for a in get_json(base + "/account/list", h))
    order_acct = dict((o["id"], o.get("accountId")) for o in get_json(base + "/order/list", h))
    fees = {}
    try:
        for f in get_json(base + "/fillFee/list", h):
            fees[f["id"]] = sum(float(f.get(k) or 0) for k in ("commission", "clearingFee", "exchangeFee",
                                                              "nfaFee", "brokerageFee", "ipFee", "orderRoutingFee"))
    except (ConnectorError, ValueError, KeyError):
        pass
    names, out = {}, []
    for f in get_json(base + "/fill/list", h):
        if f.get("active") is False:
            continue
        when = parse_utc(f["timestamp"])
        if when < since:
            continue
        cid = f["contractId"]
        if cid not in names:
            names[cid] = get_json(base + "/contract/item?id=%s" % cid, h).get("name", str(cid))
        out.append(ex("tradovate:%s" % f["id"], to_local(when, tz), names[cid],
                      "buy" if str(f.get("action", "")).lower() == "buy" else "sell", f["qty"], f["price"],
                      fees.get(f["id"], 0.0), account=accounts.get(order_acct.get(f.get("orderId")), ""),
                      source=cfg.get("name", "tradovate")))
    return out, [], {}


# ---------------------------------------------------------------- MetaTrader 5 (Windows)

def mt5(cfg, since, tz):
    try:
        import MetaTrader5 as mt  # noqa: N813 (official package name)
    except ImportError:
        raise ConnectorError("MetaTrader 5 needs Windows, the MT5 terminal and: pip install MetaTrader5")
    kw = {}
    for k in ("path", "server"):
        if cfg.get(k):
            kw[k] = cfg[k]
    if cfg.get("login"):
        kw["login"] = int(cfg["login"])
        kw["password"] = secret(cfg, "password")
    if not mt.initialize(**kw):
        raise ConnectorError("MT5 initialize failed: %s" % (mt.last_error(),))
    try:
        deals = mt.history_deals_get(since.replace(tzinfo=None) - timedelta(days=1), datetime.now() + timedelta(days=1))
        info = mt.account_info()
    finally:
        mt.shutdown()
    name = cfg.get("name") or (str(info.login) if info else "MT5")
    return [], mt5_trades(deals or [], name, cfg.get("time_shift_hours", 0)), {}


def mt5_trades(deals, account, shift_hours=0):
    """Deals grouped by position: entries (entry 0) open it, exits (1, 2, 3) close it.
    Net P&L = profit + commission + swap + fee of every deal of the position."""
    get = (lambda d, k: d.get(k)) if deals and isinstance(deals[0], dict) else (lambda d, k: getattr(d, k, None))
    pos = {}
    for d in deals:
        if get(d, "type") not in (0, 1):  # 0 buy, 1 sell; balance, credit... are skipped
            continue
        pos.setdefault(get(d, "position_id"), []).append(d)
    out = []
    for pid, ds in pos.items():
        ds.sort(key=lambda d: get(d, "time_msc") or get(d, "time") * 1000)
        ins = [d for d in ds if get(d, "entry") == 0]
        outs = [d for d in ds if get(d, "entry") in (1, 2, 3)]
        if not ins or not outs:
            continue
        vin, vout = sum(get(d, "volume") for d in ins), sum(get(d, "volume") for d in outs)
        if vout + 1e-9 < vin:
            continue  # still partly open
        fees = -sum((get(d, "commission") or 0) + (get(d, "fee") or 0) for d in ds)
        net = sum((get(d, "profit") or 0) + (get(d, "commission") or 0) + (get(d, "swap") or 0) + (get(d, "fee") or 0)
                  for d in ds)

        def t(d):
            return (datetime(1970, 1, 1) + timedelta(seconds=get(d, "time"), hours=shift_hours)).strftime(
                "%Y-%m-%d %H:%M:%S")

        sym = get(ins[0], "symbol")
        out.append({"id": _sync().trade_id("mt5", account, pid), "symbol": str(sym).upper(),
                    "side": "long" if get(ins[0], "type") == 0 else "short", "qty": round(vin, 6),
                    "entry": round(sum(get(d, "price") * get(d, "volume") for d in ins) / vin, 6),
                    "exit": round(sum(get(d, "price") * get(d, "volume") for d in outs) / vout, 6),
                    "openTime": t(ins[0]), "closeTime": t(outs[-1]), "fees": round(fees, 2), "pnl": round(net, 2),
                    "status": "closed", "source": "mt5", "account": account, "multiplier": 1.0, "executions": []})
    return out


# ---------------------------------------------------------------- OANDA

def oanda(cfg, since, tz):
    base = cfg.get("base_url") or ("https://api-fxpractice.oanda.com" if cfg.get("practice", True)
                                   else "https://api-fxtrade.oanda.com")
    h = {"Authorization": "Bearer " + secret(cfg, "token")}
    ids = [cfg["account_id"]] if cfg.get("account_id") else [a["id"] for a in get_json(base + "/v3/accounts", h)["accounts"]]
    out = []
    for aid in ids:
        before = None
        for _ in range(50):
            page = get_json("%s/v3/accounts/%s/trades?%s" % (base, aid, qs(
                {"state": "CLOSED", "count": 500, "beforeID": before})), h).get("trades", [])
            for t in page:
                opened = parse_utc(t["openTime"])
                if opened < since:
                    continue
                units = float(t.get("initialUnits", 0))
                pnl = float(t.get("realizedPL", 0)) + float(t.get("financing", 0))
                out.append({"id": _sync().trade_id("oanda", aid, t["id"]), "symbol": t["instrument"].replace("_", ""),
                            "side": "long" if units > 0 else "short", "qty": abs(units), "entry": float(t["price"]),
                            "exit": float(t["averageClosePrice"]) if t.get("averageClosePrice") else None,
                            "openTime": to_local(opened, tz), "closeTime": to_local(parse_utc(t["closeTime"]), tz),
                            "fees": 0.0, "pnl": round(pnl, 2), "status": "closed", "source": "oanda",
                            "account": cfg.get("name") or aid, "multiplier": 1.0, "executions": []})
            if len(page) < 500 or (page and parse_utc(page[-1]["openTime"]) < since):
                break
            before = page[-1]["id"]
    return [], out, {}


# ---------------------------------------------------------------- Bybit and Binance

def _windows(since, days=7):
    now = datetime.now(timezone.utc)
    a = since
    while a < now:
        b = min(a + timedelta(days=days) - timedelta(milliseconds=1), now)
        yield int(a.timestamp() * 1000), int(b.timestamp() * 1000)
        a = b + timedelta(milliseconds=1)


def bybit(cfg, since, tz):
    base = cfg.get("base_url") or ("https://api-testnet.bybit.com" if cfg.get("testnet") else "https://api.bybit.com")
    key, sec = secret(cfg, "api_key"), secret(cfg, "api_secret")
    out = []
    for cat in cfg.get("categories") or ["linear"]:
        for a, b in _windows(since):
            cursor = None
            for _ in range(100):
                q = qs({"category": cat, "limit": 100, "startTime": a, "endTime": b, "cursor": cursor})
                stamp, window = str(int(time.time() * 1000)), "10000"
                sign = hmac.new(sec.encode(), (stamp + key + window + q).encode(), hashlib.sha256).hexdigest()
                res = get_json(base + "/v5/execution/list?" + q, {"X-BAPI-API-KEY": key, "X-BAPI-SIGN": sign,
                                                                  "X-BAPI-TIMESTAMP": stamp,
                                                                  "X-BAPI-RECV-WINDOW": window})
                if res.get("retCode") not in (0, None):
                    raise ConnectorError("Bybit: %s" % res.get("retMsg"))
                r = res.get("result") or {}
                for e in r.get("list", []):
                    if e.get("execType", "Trade") != "Trade":
                        continue
                    out.append(ex("bybit:" + e["execId"], to_local(parse_utc(e["execTime"]), tz), e["symbol"],
                                  "buy" if e["side"] == "Buy" else "sell", e["execQty"], e["execPrice"],
                                  e.get("execFee") or 0, account=cfg.get("name", "Bybit"), source="bybit"))
                cursor = r.get("nextPageCursor")
                if not cursor:
                    break
    return out, [], {}


def binance(cfg, since, tz):
    base = cfg.get("base_url") or "https://fapi.binance.com"
    key, sec = secret(cfg, "api_key"), secret(cfg, "api_secret")
    symbols = cfg.get("symbols") or []
    if not symbols:
        raise ConnectorError("Binance needs the list of symbols you trade, e.g. \"symbols\": [\"BTCUSDT\"]")
    out = []
    for sym in symbols:
        for a, b in _windows(since):
            q = qs({"symbol": sym.upper(), "startTime": a, "endTime": b, "limit": 1000,
                    "timestamp": int(time.time() * 1000), "recvWindow": 10000})
            sig = hmac.new(sec.encode(), q.encode(), hashlib.sha256).hexdigest()
            for t in get_json("%s/fapi/v1/userTrades?%s&signature=%s" % (base, q, sig), {"X-MBX-APIKEY": key}):
                out.append(ex("binance:%s:%s" % (t["symbol"], t["id"]), to_local(parse_utc(t["time"]), tz),
                              t["symbol"], "buy" if t["side"] == "BUY" else "sell", t["qty"], t["price"],
                              t.get("commission") or 0, account=cfg.get("name", "Binance"), source="binance"))
    return out, [], {}


# ---------------------------------------------------------------- any folder of CSV exports

def folder(cfg, since, tz):
    path = os.path.expanduser(secret(cfg, "path"))
    if not os.path.isdir(path):
        raise ConnectorError("folder not found: %s" % path)
    s = _sync()
    execs, trades = [], []
    for f in sorted(glob.glob(os.path.join(path, cfg.get("pattern") or "*.csv"))):
        if os.path.getmtime(f) < since.timestamp():
            continue
        with open(f, encoding="utf-8-sig", errors="replace") as fh:
            e, t = s.parse_csv(fh.read(), source=os.path.basename(f))
        for x in e + t:
            x.setdefault("account", cfg.get("account", ""))
        execs += e
        trades += t
    return execs, trades, {}


TYPES = {"alpaca": alpaca, "ibkr": ibkr, "projectx": projectx, "tradovate": tradovate, "mt5": mt5,
         "oanda": oanda, "bybit": bybit, "binance": binance, "folder": folder}


def _sync():
    import sys
    mod = sys.modules.get("mindlock_sync") or sys.modules.get("__main__")
    if mod is None or not hasattr(mod, "parse_csv"):
        here = os.path.dirname(os.path.abspath(__file__))
        import importlib.util
        spec = importlib.util.spec_from_file_location("mindlock_sync", os.path.join(here, "sync.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules["mindlock_sync"] = mod
        spec.loader.exec_module(mod)
    return mod


def run_all(path, log=print):
    """Run every enabled connector in connectors.json. One failing connector never stops the others."""
    if not os.path.isfile(path):
        return [], [], {}
    with open(path, encoding="utf-8") as fh:
        conf = json.load(fh)
    tz = conf.get("timezone", "local")
    since = datetime.now(timezone.utc) - timedelta(days=int(conf.get("history_days", 90)))
    execs, trades, mult = [], [], {}
    for cfg in conf.get("connectors", []):
        if cfg.get("enabled", True) is False:
            continue
        kind, label = cfg.get("type"), cfg.get("name") or cfg.get("type")
        fn = TYPES.get(kind)
        if not fn:
            log("  %s: unknown type '%s' (known: %s)" % (label, kind, ", ".join(sorted(TYPES))))
            continue
        try:
            e, t, m = fn(cfg, since, tz)
        except ConnectorError as err:
            log("  %s: %s" % (label, err))
            continue
        except Exception as err:  # a broker changing its answer must not stop the journal
            log("  %s: unexpected answer (%s: %s)" % (label, type(err).__name__, err))
            continue
        for x in e + t:
            if not x.get("account") and kind != "folder":  # a folder CSV keeps the same id as a browser import
                x["account"] = label
        execs += e
        trades += t
        mult.update(m)
        log("  %s: %d executions, %d closed trades" % (label, len(e), len(t)))
    return execs, trades, mult
