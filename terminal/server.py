#!/usr/bin/env python3
"""MINDLOCK Terminal: a local web trading terminal built on the eleven skills.

    python3 terminal/server.py            # then open http://127.0.0.1:8765
    python3 terminal/server.py --port 9000 --budget 10000

Standard library only, like the rest of the pack. Free, delayed Yahoo data.
The terminal trades a PAPER account (trader/paper.json) and never sends an
order to a real broker: going live stays with /trader-broker and your yes.

Education and tooling, not financial advice.
"""

import argparse
import concurrent.futures
import datetime
import importlib.util
import json
import math
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STATIC = os.path.join(HERE, "static")
UA = "Mozilla/5.0 (compatible; mindlock-terminal/1.0)"
TICKER = re.compile(r"^[\^A-Z0-9][A-Z0-9.\-=^]{0,14}$")


def _skill(folder, name):
    spec = importlib.util.spec_from_file_location("trader_" + name, os.path.join(ROOT, folder, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


EXIT = _skill("trader-exit", "exit")
BUDGET = _skill("trader-budget", "size")
RISK = _skill("trader-risk", "check")
TM = _skill("trader-timemachine", "timemachine")
NEWS = _skill("trader-news", "news")
ALERTS = _skill("trader-alerts", "alerts")
SCAN = _skill("trader-scanner", "scan")
sys.path.insert(0, HERE)
import mindlock as ML  # noqa: E402




def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


class ApiError(Exception):
    def __init__(self, msg, code=400):
        Exception.__init__(self, msg)
        self.code = code


# ---------------------------------------------------------------- data (cached)

_cache, _cache_lock = {}, threading.Lock()


def http_get(url, ttl=15, timeout=15):
    now = time.time()
    with _cache_lock:
        hit = _cache.get(url)
        if hit and now - hit[0] < ttl:
            return hit[1]
    last = None
    for attempt in range(2):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                text = r.read().decode("utf-8", "replace")
            with _cache_lock:
                _cache[url] = (now, text)
                if len(_cache) > 500:
                    for k in sorted(_cache, key=lambda k: _cache[k][0])[:250]:
                        _cache.pop(k, None)
            return text
        except urllib.error.HTTPError as e:
            last = "HTTP %d" % e.code
            if e.code == 404:
                body = e.read().decode("utf-8", "replace")
                return body
            if e.code not in (429, 500, 502, 503, 504):
                break
            time.sleep(1.5)
            url = url.replace("query2.", "query1.")
        except (urllib.error.URLError, OSError) as e:
            last = str(getattr(e, "reason", e))
            break
    raise ApiError("source de données indisponible (%s)" % last, 502)


RANGES = {  # UI period -> (yahoo range, interval)
    "1D": ("1d", "1m"), "5D": ("5d", "5m"), "M30": ("60d", "30m"), "1M": ("1mo", "30m"), "3M": ("3mo", "1d"),
    "6M": ("6mo", "1d"), "1Y": ("1y", "1d"), "5Y": ("5y", "1wk"),
}


def chart(sym, period="1D", ttl=15, include_pre=False):
    if period in ("M5", "M15", "H1", "H4"):
        return tf_chart(sym, period, ttl)
    rng, interval = RANGES.get(period, RANGES["1D"])
    url = ("https://query2.finance.yahoo.com/v8/finance/chart/%s?range=%s&interval=%s&includePrePost=%s"
           % (urllib.parse.quote(sym), rng, interval, "true" if include_pre else "false"))
    data = json.loads(http_get(url, ttl=ttl))
    res = (data.get("chart") or {}).get("result")
    if not res:
        err = ((data.get("chart") or {}).get("error") or {}).get("description") or "symbole inconnu"
        raise ApiError("%s : %s" % (sym, err), 404)
    r = res[0]
    meta = r.get("meta") or {}
    q = ((r.get("indicators") or {}).get("quote") or [{}])[0]
    bars = []
    for i, t in enumerate(r.get("timestamp") or []):
        c = (q.get("close") or [None])[i] if i < len(q.get("close") or []) else None
        if t is None or c is None:
            continue
        g = lambda k: (q.get(k) or [None] * (i + 1))[i]
        bars.append({"t": int(t), "o": g("open") or c, "h": g("high") or c, "l": g("low") or c, "c": c,
                     "v": int(g("volume") or 0)})
    price = meta.get("regularMarketPrice") or (bars[-1]["c"] if bars else None)
    prev = meta.get("previousClose") if period == "1D" else None
    if period == "M30":
        prev = chart(sym, "1D", ttl=ttl)["prev_close"]
    prev = prev or meta.get("chartPreviousClose")
    return {"symbol": meta.get("symbol") or sym, "name": meta.get("longName") or meta.get("shortName") or sym,
            "currency": meta.get("currency"), "exchange": meta.get("fullExchangeName") or meta.get("exchangeName"),
            "price": price, "prev_close": prev, "interval": interval, "period": period,
            "gmtoffset": meta.get("gmtoffset", 0), "day_high": meta.get("regularMarketDayHigh"),
            "day_low": meta.get("regularMarketDayLow"), "volume": meta.get("regularMarketVolume"),
            "high52": meta.get("fiftyTwoWeekHigh"), "low52": meta.get("fiftyTwoWeekLow"),
            "time": meta.get("regularMarketTime"), "bars": bars}


def tf_chart(sym, tf, ttl=15):
    """Chart payload for a strategy timeframe (H4 built from H1), forming bar included."""
    bars, meta = tf_bars(sym, tf, closed=False, ttl=ttl)
    day = chart(sym, "1D", ttl=ttl)
    return dict(day, interval=TF_SPEC[tf][1] if tf != "H4" else "4h", period=tf, bars=bars[-1500:])


def quote(sym):
    c = chart(sym, "1D", ttl=10)
    p, pc = c["price"], c["prev_close"]
    chg = (p - pc) if (p is not None and pc) else None
    spark = [b["c"] for b in c["bars"]][-80:]
    return {"symbol": c["symbol"], "name": c["name"], "price": p, "prev_close": pc, "change": chg,
            "change_pct": (chg / pc * 100) if chg is not None else None, "spark": spark,
            "day_high": c["day_high"], "day_low": c["day_low"], "volume": c["volume"], "time": c["time"]}


_pool = concurrent.futures.ThreadPoolExecutor(max_workers=8)


def quotes(symbols):
    out = {}
    futs = {_pool.submit(quote, s): s for s in symbols}
    for f in concurrent.futures.as_completed(futs):
        s = futs[f]
        try:
            out[s] = f.result()
        except Exception as e:
            out[s] = {"symbol": s, "error": str(e)}
    return [out[s] for s in symbols]


# ---------------------------------------------------------------- indicators

def ema(xs, n):
    if not xs:
        return []
    k, out, e = 2.0 / (n + 1), [], xs[0]
    for x in xs:
        e = x * k + e * (1 - k)
        out.append(e)
    return out


def sma(xs, n):
    return sum(xs[-n:]) / n if len(xs) >= n else None


def macd_hist(closes):
    if len(closes) < 35:
        return None
    m = [a - b for a, b in zip(ema(closes, 12), ema(closes, 26))]
    sig = ema(m, 9)
    return m[-1] - sig[-1], m[-2] - sig[-2]


def vwap(bars):
    pv = vol = 0.0
    for b in bars:
        tp = (b["h"] + b["l"] + b["c"]) / 3
        pv += tp * b["v"]
        vol += b["v"]
    return pv / vol if vol else None


def atr(bars, n=14):
    if len(bars) < n + 1:
        return None
    trs = [max(b["h"] - b["l"], abs(b["h"] - a["c"]), abs(b["l"] - a["c"])) for a, b in zip(bars, bars[1:])]
    return sum(trs[-n:]) / n


# ---------------------------------------------------------------- news + sentiment

BULL = ["beat", "beats", "surge", "soar", "jump", "rall", "record", "upgrade", "raise", "raises", "buyback",
        "outperform", "strong", "growth", "approval", "approved", "partnership", "wins", "bullish", "higher",
        "gain", "tops", "exceeds", "rate cut", "cuts rates", "dovish", "cooling inflation", "boost"]
BEAR = ["miss", "misses", "plunge", "sink", "tumble", "fall", "drop", "slump", "downgrade", "cut ", "cuts ",
        "lawsuit", "probe", "investigation", "recall", "warning", "warns", "weak", "bearish", "lower", "loss",
        "layoff", "bankrupt", "fraud", "delay", "hawkish", "rate hike", "hot inflation", "tariff", "sell-off",
        "selloff", "crash", "fears"]
MACRO_WORDS = {"fed": "Fed", "fomc": "FOMC", "powell": "Fed", "cpi": "CPI", "inflation": "Inflation",
               "payroll": "NFP", "jobs report": "NFP", "opec": "OPEP", "oil": "Pétrole", "treasury": "Taux",
               "yield": "Taux", "tariff": "Tarifs", "gdp": "PIB", "ecb": "BCE", "recession": "Récession"}


def sentiment(title):
    t = " " + (title or "").lower() + " "
    s = sum(1 for w in BULL if w in t) - sum(1 for w in BEAR if w in t)
    return max(-2, min(2, s))


def news(sym, limit=20):
    url = NEWS.YAHOO % urllib.parse.quote(sym)
    try:
        items = NEWS.parse_rss(http_get(url, ttl=120).encode("utf-8"), sym)
    except Exception as e:
        raise ApiError("flux de news indisponible pour %s (%s)" % (sym, e), 502)
    out = []
    for it in items[:limit]:
        title = it.get("title") or ""
        low = title.lower()
        tags = sorted({v for k, v in MACRO_WORDS.items() if re.search(r"\b%s\b" % re.escape(k), low)})
        out.append({"title": title, "link": it.get("link"), "time": it.get("time"), "summary": it.get("summary"),
                    "sentiment": sentiment(title), "tags": tags, "ticker": sym})
    out.sort(key=lambda x: x.get("time") or "", reverse=True)
    return out


# ---------------------------------------------------------------- the bias engine

def bias(sym):
    """Rule-based, transparent: each signal adds or removes points, every reason is shown."""
    intraday = chart(sym, "1D", ttl=15)
    daily = chart(sym, "6M", ttl=300)
    try:
        heads = news(sym, 15)
    except ApiError:
        heads = []
    reasons, score = [], 0
    ib = intraday["bars"]
    dc = [b["c"] for b in daily["bars"]]
    price = intraday["price"]

    def add(pts, text):
        nonlocal score
        score += pts
        reasons.append({"points": pts, "text": text})

    if price and intraday["prev_close"]:
        chg = (price / intraday["prev_close"] - 1) * 100
        if abs(chg) >= 0.3:
            add(10 if chg > 0 else -10, "%+.2f %% vs la clôture d'hier" % chg)
    if len(ib) >= 10:
        closes = [b["c"] for b in ib]
        e9, e21 = ema(closes, 9)[-1], ema(closes, 21)[-1]
        add(15 if e9 > e21 else -15, "EMA 9 %s EMA 21 en intraday" % (">" if e9 > e21 else "<"))
        vw = vwap(ib)
        if vw:
            add(15 if price > vw else -15, "prix %s VWAP (%.2f)" % ("au-dessus du" if price > vw else "sous le", vw))
        mh = macd_hist(closes)
        if mh:
            if mh[0] > 0:
                add(10, "MACD positif%s" % (" et en hausse" if mh[0] > mh[1] else ""))
            else:
                add(-10, "MACD négatif%s" % (" et en baisse" if mh[0] < mh[1] else ""))
    if len(dc) >= 50:
        s20, s50 = sma(dc, 20), sma(dc, 50)
        add(15 if price > s50 else -15, "prix %s la moyenne 50 jours (%.2f)" % (">" if price > s50 else "<", s50))
        add(10 if s20 > s50 else -10, "tendance de fond : MM20 %s MM50" % (">" if s20 > s50 else "<"))
    r = SCAN.rsi(dc + ([price] if price else []))
    if r is not None:
        if r >= 70:
            add(-10, "RSI 14 j à %.0f : suracheté" % r)
        elif r <= 30:
            add(10, "RSI 14 j à %.0f : survendu, rebond possible" % r)
        else:
            add(5 if r > 50 else -5, "RSI 14 j à %.0f" % r)
    news_pts = sum(h["sentiment"] for h in heads[:10])
    if news_pts:
        pts = max(-15, min(15, news_pts * 3))
        add(pts, "ton des %d derniers titres : %s" % (min(10, len(heads)), "positif" if pts > 0 else "négatif"))
    score = max(-100, min(100, score))
    label = "HAUSSIER" if score >= 20 else "BAISSIER" if score <= -20 else "NEUTRE"
    a = atr(daily["bars"]) if daily["bars"] else None
    return {"symbol": sym, "price": price, "score": score, "label": label,
            "confidence": min(95, 40 + abs(score) // 2), "reasons": reasons,
            "atr_daily": a, "macro_tags": sorted({t for h in heads for t in h["tags"]}),
            "note": "Score à règles transparentes (tendance, VWAP, MACD, RSI, ton des news). "
                    "Ce n'est pas une prédiction ni un conseil financier."}


MACRO = [("SPY", "S&P 500"), ("QQQ", "Nasdaq 100"), ("IWM", "Russell 2000"), ("^VIX", "VIX"),
         ("^TNX", "US 10 ans"), ("DX-Y.NYB", "Dollar (DXY)"), ("CL=F", "Pétrole WTI"), ("GC=F", "Or"),
         ("BTC-USD", "Bitcoin"), ("EURUSD=X", "EUR/USD")]


def macro():
    qs = quotes([s for s, _ in MACRO])
    names = dict(MACRO)
    score, notes = 0, []
    by = {q["symbol"]: q for q in qs if q.get("price") is not None}
    for q in qs:
        q["label"] = names.get(q["symbol"], q["symbol"])

    def pct(s):
        q = by.get(s) or {}
        return q.get("change_pct")

    for s, w, txt in (("SPY", 1, "S&P 500"), ("QQQ", 1, "Nasdaq")):
        p = pct(s)
        if p is not None:
            score += w if p > 0 else -w
            notes.append("%s %+.2f %%" % (txt, p))
    p = pct("^VIX")
    if p is not None:
        score += -1 if p > 0 else 1
        notes.append("VIX %s (%+.1f %%)" % ("monte" if p > 0 else "baisse", p))
    p = pct("^TNX")
    if p is not None and abs(p) > 0.5:
        score += -1 if p > 0 else 1
        notes.append("taux 10 ans %s" % ("en hausse" if p > 0 else "en baisse"))
    p = pct("DX-Y.NYB")
    if p is not None and abs(p) > 0.2:
        score += -1 if p > 0 else 1
        notes.append("dollar %s" % ("fort" if p > 0 else "faible"))
    regime = "RISK-ON" if score >= 2 else "RISK-OFF" if score <= -2 else "MIXTE"
    return {"quotes": qs, "regime": regime, "score": score, "notes": notes}


# ---------------------------------------------------------------- paper account

def paper_path():
    return os.path.join(os.getcwd(), "trader", "paper.json")


_paper_lock = threading.Lock()


def paper_load(budget):
    p = paper_path()
    if os.path.exists(p):
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    return {"start_cash": budget, "cash": budget, "positions": {}, "orders": [], "fills": [], "log": []}


def paper_save(doc):
    p = paper_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
    os.replace(tmp, p)


def _fill(doc, sym, side, qty, price, why):
    now = utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    pos = doc["positions"].get(sym, {"qty": 0, "avg": 0.0})
    if side == "buy":
        cost = qty * price
        if cost > doc["cash"] + 1e-6:
            raise ApiError("cash insuffisant : %.2f $ requis, %.2f $ disponibles" % (cost, doc["cash"]))
        pos["avg"] = (pos["avg"] * pos["qty"] + cost) / (pos["qty"] + qty)
        pos["qty"] += qty
        doc["cash"] -= cost
        pnl = 0.0
    else:
        if qty > pos["qty"]:
            raise ApiError("vente de %d %s refusée : seulement %d en position (pas de vente à découvert)"
                           % (qty, sym, pos["qty"]))
        pnl = (price - pos["avg"]) * qty
        pos["qty"] -= qty
        doc["cash"] += qty * price
    if pos["qty"]:
        doc["positions"][sym] = pos
    else:
        doc["positions"].pop(sym, None)
    doc["cash"] = round(doc["cash"], 2)
    doc["fills"].insert(0, {"at": now, "symbol": sym, "side": side, "qty": qty, "price": round(price, 4),
                            "pnl": round(pnl, 2), "why": why})
    del doc["fills"][300:]


def paper_sweep(doc):
    """Brackets: if the last price crossed the stop or target, close at that level."""
    live = [o for o in doc["orders"] if o["status"] == "open"]
    if not live:
        return
    qs = {q["symbol"]: q for q in quotes(sorted({o["symbol"] for o in live})) if q.get("price")}
    for o in live:
        q = qs.get(o["symbol"])
        if not q:
            continue
        p = q["price"]
        have = doc["positions"].get(o["symbol"], {}).get("qty", 0)
        if have <= 0:
            o["status"] = "cancelled"
            continue
        hit = None
        if o.get("stop") and p <= o["stop"]:
            hit = ("stop", o["stop"])
        elif o.get("target") and p >= o["target"]:
            hit = ("target", o["target"])
        if hit:
            _fill(doc, o["symbol"], "sell", min(o["qty"], have), hit[1], "bracket %s" % hit[0])
            o["status"] = "filled_" + hit[0]


RULE_DEFAULTS = {"risk_pct_per_trade": 0.5, "daily_loss_lock_pct": 1.0, "daily_win_lock_pct": 1.0}


def day_state(doc, equity, c):
    """The day's lock. Equity is measured against the New York day's opening equity;
    once the loss or the win limit is reached, new entries stay locked until tomorrow."""
    today = ny_today()
    day = doc.get("day") or {}
    if day.get("date") != today:
        day = {"date": today, "start_equity": round(equity, 2), "lock": None}
    start = day["start_equity"]
    loss_lim = round(start * float(c["daily_loss_lock_pct"]) / 100, 2)
    win_lim = round(start * float(c["daily_win_lock_pct"]) / 100, 2)
    pnl = round(equity - start, 2)
    if not day.get("lock"):
        if loss_lim > 0 and pnl <= -loss_lim:
            day["lock"] = {"kind": "loss", "at": utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"), "pnl": pnl}
        elif win_lim > 0 and pnl >= win_lim:
            day["lock"] = {"kind": "win", "at": utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"), "pnl": pnl}
    doc["day"] = day
    return dict(day, pnl=pnl, loss_limit=loss_lim, win_limit=win_lim,
                risk_per_trade=round(start * float(c["risk_pct_per_trade"]) / 100, 2),
                risk_pct=c["risk_pct_per_trade"], loss_pct=c["daily_loss_lock_pct"], win_pct=c["daily_win_lock_pct"])


def lock_message(day):
    lk = day.get("lock")
    if not lk:
        return None
    if lk["kind"] == "loss":
        return ("VERROU JOURNALIER (perte) : %.2f $ aujourd'hui, limite -%.2f $ (%s %% du compte). "
                "Plus aucune entrée avant demain. Les clôtures restent possibles." % (lk["pnl"], day["loss_limit"], day["loss_pct"]))
    return ("VERROU JOURNALIER (gain) : +%.2f $ aujourd'hui, objectif +%.2f $ (%s %% du compte) atteint. "
            "Journée terminée, gains protégés. Les clôtures restent possibles." % (lk["pnl"], day["win_limit"], day["win_pct"]))


def paper_view(doc, c=None):
    c = c or cfg_rules()
    syms = sorted(doc["positions"])
    qs = {q["symbol"]: q for q in quotes(syms)} if syms else {}
    rows, mv = [], 0.0
    for s in syms:
        pos = doc["positions"][s]
        p = (qs.get(s) or {}).get("price") or pos["avg"]
        val = p * pos["qty"]
        mv += val
        rows.append({"symbol": s, "qty": pos["qty"], "avg": pos["avg"], "price": p, "value": val,
                     "pnl": (p - pos["avg"]) * pos["qty"], "pnl_pct": (p / pos["avg"] - 1) * 100 if pos["avg"] else 0})
    equity = doc["cash"] + mv + ml_open_pnl(doc)
    day = day_state(doc, equity, c)
    day["message"] = lock_message(day)
    realized = sum(f["pnl"] for f in doc["fills"])
    today = utcnow().strftime("%Y-%m-%d")
    return {"cash": doc["cash"], "equity": equity, "start_cash": doc["start_cash"],
            "total_pnl": equity - doc["start_cash"], "realized": realized,
            "realized_today": sum(f["pnl"] for f in doc["fills"] if f["at"].startswith(today)),
            "positions": rows, "orders": [o for o in doc["orders"] if o["status"] == "open"],
            "fills": doc["fills"][:50], "day": day}


# ---------------------------------------------------------------- handlers

CONFIG = {}


def cfg_rules():
    c = dict(BUDGET.DEFAULTS)
    c.update(RULE_DEFAULTS)
    p = os.path.join(os.getcwd(), "trader", "config.json")
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as fh:
                c.update(json.load(fh))
        except ValueError:
            pass
    c.update({k: v for k, v in CONFIG.items() if v is not None and k != "anytime"})
    sp = ml_params()
    c["risk_pct_per_trade"] = sp["RiskPercent"]
    if sp["UseDailyLock"]:
        c["daily_loss_lock_pct"], c["daily_win_lock_pct"] = sp["DailyLossPercent"], sp["DailyProfitPercent"]
    else:
        c["daily_loss_lock_pct"] = c["daily_win_lock_pct"] = 0
    return c


# ---------------------------------------------------------------- MINDLOCK EMA CORE (universal)

TF_SPEC = {  # timeframe -> (yahoo range, yahoo interval, seconds per bar, bars to aggregate)
    "M5": ("60d", "5m", 300, 1), "M15": ("60d", "15m", 900, 1), "M30": ("60d", "30m", 1800, 1),
    "H1": ("730d", "60m", 3600, 1), "H4": ("730d", "60m", 14400, 4),
}
TF_DEPTH = {"M5": "60 jours", "M15": "60 jours", "M30": "60 jours", "H1": "2 ans", "H4": "2 ans"}


def ml_path():
    return os.path.join(os.getcwd(), "trader", "mindlock.json")


def ml_params():
    try:
        with open(ml_path(), encoding="utf-8") as fh:
            return ML.params(json.load(fh))
    except (OSError, ValueError):
        return ML.params()


def _tf(tf):
    tf = str(tf or "M30").upper()
    if tf not in TF_SPEC:
        raise ApiError("timeframe inconnu : %s (M5, M15, M30, H1, H4)" % tf)
    return tf


def tf_bars(sym, tf, closed=True, ttl=30):
    """Bars of one symbol on one timeframe, oldest first; closed bars only by default."""
    rng, interval, secs, _ = TF_SPEC[tf]
    url = ("https://query2.finance.yahoo.com/v8/finance/chart/%s?range=%s&interval=%s"
           % (urllib.parse.quote(sym), rng, interval))
    data = json.loads(http_get(url, ttl=ttl, timeout=25))
    res = (data.get("chart") or {}).get("result")
    if not res:
        err = ((data.get("chart") or {}).get("error") or {}).get("description") or "symbole inconnu"
        raise ApiError("%s : %s" % (sym, err), 404)
    r = res[0]
    q = ((r.get("indicators") or {}).get("quote") or [{}])[0]
    bars = []
    for i, t in enumerate(r.get("timestamp") or []):
        vals = [(q.get(k) or [None] * (i + 1))[i] for k in ("open", "high", "low", "close")]
        if t is None or None in vals:
            continue
        bars.append({"t": int(t), "o": vals[0], "h": vals[1], "l": vals[2], "c": vals[3],
                     "v": int((q.get("volume") or [0] * (i + 1))[i] or 0)})
    if TF_SPEC[tf][3] > 1:
        bars = ML.aggregate(bars, secs)
    if closed:
        now = time.time()
        bars = [b for b in bars if b["t"] + secs <= now]
    meta = r.get("meta") or {}
    return bars, meta


_fx = {}


def usd_rate(ccy):
    """Value of one unit of `ccy` in USD (P&L of non-USD quotes)."""
    if ccy in ("GBp", "GBX"):          # London quotes in pence
        return usd_rate("GBP") / 100
    ccy = (ccy or "USD").upper()
    if ccy == "USD":
        return 1.0
    hit = _fx.get(ccy)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    rate = quote("%sUSD=X" % ccy)["price"]
    if not rate:
        raise ApiError("pas de taux de change %s/USD" % ccy)
    _fx[ccy] = (time.time(), rate)
    return rate


def ml_asset(sym, tf, closed=True, ttl=30):
    """(bars, resolved params, meta) for one symbol and timeframe."""
    bars, meta = tf_bars(sym, tf, closed, ttl)
    p0 = ml_params()
    if len(bars) < p0["SlowEMA"] + 10:
        raise ApiError("trop peu de bougies %s pour %s (%d)" % (tf, sym, len(bars)), 502)
    ccy = meta.get("currency") or "USD"
    try:
        rate = usd_rate(ccy)
    except ApiError:
        rate = 1.0
    p = ML.for_asset(p0, sym, bars[-1]["c"], rate)
    p["Currency"] = ccy
    return bars, p, meta


def ml_key(sym, tf):
    return "%s|%s" % (sym, tf)


def ml_state(doc):
    st = doc.setdefault("mindlock", {})
    if "position" in st or isinstance(st.get("auto"), bool):        # first version: gold M30 only
        old_pos, old_auto = st.pop("position", None), st.pop("auto", False)
        st["positions"] = {ml_key("GC=F", "M30"): old_pos} if old_pos else {}
        st["auto"] = [ml_key("GC=F", "M30")] if old_auto is True else []
        st["last_t"] = {}
    st.setdefault("auto", [])
    st.setdefault("positions", {})
    st.setdefault("trades", [])
    if not isinstance(st.get("last_t"), dict):
        st["last_t"] = {}
    return st


def ml_close(doc, key, price, t, reason, p):
    st = ml_state(doc)
    pos = st["positions"].pop(key)
    tr = ML.close_trade(pos, price, t, reason, p)
    tr["key"] = key
    st["trades"].insert(0, tr)
    del st["trades"][300:]
    doc["cash"] = round(doc["cash"] + tr["pnl"], 2)
    doc["fills"].insert(0, {"at": utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"), "symbol": key.replace("|", " "),
                            "side": "sell" if tr["side"] == "buy" else "buy", "qty": tr["lots"],
                            "price": tr["exit"], "pnl": tr["pnl"], "why": "MINDLOCK %s" % reason})
    return tr


def ml_open(doc, key, side, bar, p, why):
    st = ml_state(doc)
    pos = ML.open_trade(side, bar, paper_view(doc)["day"]["start_equity"], p)
    if not pos:
        raise ApiError("taille nulle : compte trop petit pour %s %% de risque avec un stop de %d pips"
                       % (p["RiskPercent"], p["StopLossPips"]))
    pos.update({"key": key, "pip": p["PipSize"], "contract": p["ContractSize"], "usd_rate": p["UsdRate"],
                "risk_usd": round(ML.risk_per_lot(p) * pos["lots"], 2)})
    st["positions"][key] = pos
    doc["fills"].insert(0, {"at": utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"), "symbol": key.replace("|", " "),
                            "side": side, "qty": pos["lots"], "price": pos["entry"], "pnl": 0.0,
                            "why": "MINDLOCK %s" % why, "max_loss": pos["risk_usd"]})
    return pos


def ml_step(doc, key, data=None):
    """Walk every closed bar of one symbol/timeframe since the last one seen: stop /
    target first, then the signal (auto mode only), close-and-reverse, the day lock."""
    st = ml_state(doc)
    sym, tf = key.split("|")
    bars, p, _ = data or ml_asset(sym, tf)
    ind = ML.indicators(bars, p)
    last = st["last_t"].get(key)
    if last is None:
        st["last_t"][key] = bars[-1]["t"]
        return
    for i, bar in enumerate(bars):
        if bar["t"] <= last:
            continue
        st["last_t"][key] = bar["t"]
        if key in st["positions"]:
            pos = st["positions"][key]
            pp = dict(p, PipSize=pos.get("pip", p["PipSize"]), UsdRate=pos.get("usd_rate", p["UsdRate"]))
            hit = ML.manage(pos, bar, pp)
            if hit:
                ml_close(doc, key, hit[0], bar["t"], hit[1], pp)
        if key not in st["auto"]:
            continue
        sig = ML.signal_at(bars, ind, i, p)
        if not sig:
            continue
        pos = st["positions"].get(key)
        if pos and pos["side"] != sig and p["CloseAndReverse"]:
            ml_close(doc, key, bar["c"], bar["t"], "inversion", p)
        if key not in st["positions"] and not paper_view(doc)["day"].get("lock"):
            ml_open(doc, key, sig, bar, p, "auto")


def ml_open_pnl(doc):
    """Mark every open MINDLOCK position to the last price; returns the total in USD."""
    total = 0.0
    for key, pos in ml_state(doc)["positions"].items():
        try:
            px = quote(key.split("|")[0])["price"]
        except Exception:
            continue
        pnl = (px - pos["entry"]) * (1 if pos["side"] == "buy" else -1) * pos["lots"] \
            * pos.get("contract", 1) * pos.get("usd_rate", 1)
        pos["price"], pos["pnl"] = px, round(pnl, 2)
        total += pnl
    return total


def ml_loop():
    while True:
        time.sleep(60)
        try:
            c = cfg_rules()
            with _paper_lock:
                doc = paper_load(c.get("budget") or 0)
                st = ml_state(doc)
                for key in sorted(set(st["auto"]) | set(st["positions"])):
                    try:
                        ml_step(doc, key)
                    except Exception as e:
                        if os.environ.get("TERMINAL_LOG"):
                            print("mindlock", key, e)
                paper_save(doc)
        except Exception as e:  # keep the loop alive
            if os.environ.get("TERMINAL_LOG"):
                print("mindlock loop:", e)


def _sym_tf(q):
    return _sym(q), _tf(q.get("tf"))


def api_ml_signal(q):
    sym, tf = _sym_tf(q)
    key = ml_key(sym, tf)
    data = ml_asset(sym, tf)
    bars, p, meta = data
    ind = ML.indicators(bars, p)
    i = len(bars) - 1
    ch = ML.checks(bars, ind, i, p) or {"buy": [], "sell": []}
    sig = ML.signal_at(bars, ind, i, p)
    c = cfg_rules()
    with _paper_lock:
        doc = paper_load(c.get("budget") or 0)
        st = ml_state(doc)
        if key in st["auto"] or key in st["positions"]:
            ml_step(doc, key, data)
        view = paper_view(doc, c)
        paper_save(doc)
    last = bars[-1]
    sides = {}
    for side in ("buy", "sell"):
        sl, tp = ML.levels(side, last["c"], p)
        sides[side] = {"checks": [{"name": n, "ok": ok, "detail": d} for n, ok, d in ch[side]],
                       "ok": bool(ch[side]) and all(x[1] for x in ch[side]), "sl": sl, "tp": tp}
    recent = [{"t": bars[j]["t"], "side": s} for j in range(max(0, i - 400), i + 1)
              for s in [ML.signal_at(bars, ind, j, p)] if s]
    lots = ML.lots_for(view["day"]["start_equity"], p)
    return {"params": p, "base_params": ml_params(), "symbol": sym, "tf": tf, "key": key, "name": meta.get("shortName") or sym,
            "bar": last, "bar_seconds": TF_SPEC[tf][2], "signal": sig, "sides": sides,
            "ema_fast": ind["fast"][i], "ema_slow": ind["slow"][i], "rsi": ind["rsi"][i],
            "lots": lots, "risk_usd": round(lots * ML.risk_per_lot(p), 2),
            "position": st["positions"].get(key), "positions": list(st["positions"].values()),
            "auto": key in st["auto"], "autos": st["auto"], "trades": st["trades"][:30],
            "day": view["day"], "markers": recent}


def api_ml_scan(q):
    syms = [s.strip().upper() for s in (q.get("symbols") or "").split(",") if s.strip()][:25]
    tfs = [_tf(t) for t in (q.get("tfs") or "M5,M15,M30,H1,H4").split(",") if t.strip()]
    if not syms:
        raise ApiError("aucun symbole à scanner")

    def one(sym, tf):
        bars, p, _ = ml_asset(sym, tf, ttl=60)
        ind = ML.indicators(bars, p)
        i = len(bars) - 1
        f, s = ind["fast"][i], ind["slow"][i]
        ch = ML.checks(bars, ind, i, p) or {"buy": [], "sell": []}
        score = {k: sum(1 for x in ch[k] if x[1]) for k in ("buy", "sell")}
        return {"symbol": sym, "tf": tf, "signal": ML.signal_at(bars, ind, i, p),
                "trend": "up" if f and s and f > s else "down" if f and s else None,
                "rsi": ind["rsi"][i], "score": score, "n": len(ch["buy"]), "t": bars[i]["t"]}

    futs = {_pool.submit(one, s, t): (s, t) for s in syms for t in tfs}
    cells = []
    for f in concurrent.futures.as_completed(futs):
        s, t = futs[f]
        try:
            cells.append(f.result())
        except Exception as e:
            cells.append({"symbol": s, "tf": t, "error": str(e)})
    return {"symbols": syms, "tfs": tfs, "cells": cells}


def api_ml_backtest(body):
    try:
        p0 = ML.params(dict(ml_params(), **(body.get("params") or {})))
    except ValueError as e:
        raise ApiError(str(e))
    sym = _sym(body) if body.get("symbol") else "GC=F"
    tf = _tf(body.get("tf"))
    rate = 1.0
    if body.get("csv"):
        bars = ML.parse_csv(body["csv"])
        source = "fichier MT5 %s (%d bougies)" % (body.get("csv_name") or "", len(bars))
    else:
        bars, meta = tf_bars(sym, tf, True, ttl=300)
        try:
            rate = usd_rate(meta.get("currency"))
        except ApiError:
            pass
        source = "Yahoo %s %s, %s" % (sym, tf, TF_DEPTH[tf])
    if len(bars) < p0["SlowEMA"] + 10:
        raise ApiError("pas assez de bougies (%d) pour un backtest" % len(bars))
    p = ML.for_asset(p0, sym, bars[-1]["c"], rate)
    bal = _num(body, "balance", False) or 100000.0
    res = ML.backtest(bars, p, bal)
    res["source"] = source
    return res


def api_ml_params(body):
    try:
        p = ML.params(dict(ml_params(), **(body.get("params") or {})))
    except ValueError as e:
        raise ApiError(str(e))
    os.makedirs(os.path.dirname(ml_path()), exist_ok=True)
    with open(ml_path(), "w", encoding="utf-8") as fh:
        json.dump(p, fh, indent=2)
    return {"params": p}


def api_ml_auto(body):
    sym, tf = _sym_tf(body)
    key = ml_key(sym, tf)
    bars, _, _ = ml_asset(sym, tf)
    c = cfg_rules()
    with _paper_lock:
        doc = paper_load(c.get("budget") or 0)
        st = ml_state(doc)
        if body.get("on") is True:
            if key not in st["auto"]:
                st["auto"].append(key)
            st["last_t"][key] = bars[-1]["t"]        # act on bars that close from now on
        else:
            st["auto"] = [k for k in st["auto"] if k != key]
        paper_save(doc)
    return {"auto": key in st["auto"], "autos": st["auto"]}


def api_ml_execute(body):
    if body.get("confirm") is not True:
        raise ApiError("ordre non confirmé")
    sym, tf = _sym_tf(body)
    key = ml_key(sym, tf)
    data = ml_asset(sym, tf)
    bars, p, _ = data
    sig = ML.signal_at(bars, ML.indicators(bars, p), len(bars) - 1, p)
    side = body.get("side")
    if side not in ("buy", "sell") or side != sig:
        raise ApiError("pas de signal %s valide sur la dernière bougie %s clôturée de %s"
                       % ({"buy": "ACHAT", "sell": "VENTE"}.get(side, ""), tf, sym))
    c = cfg_rules()
    with _paper_lock:
        doc = paper_load(c.get("budget") or 0)
        st = ml_state(doc)
        if key in st["positions"] or key in st["auto"]:
            ml_step(doc, key, data)
        day = paper_view(doc, c)["day"]
        if day.get("lock"):
            raise ApiError(lock_message(day))
        pos = st["positions"].get(key)
        if pos:
            if pos["side"] == side:
                raise ApiError("une position %s est déjà ouverte sur %s %s (MaxPositions = 1)" % (side, sym, tf))
            if not p["CloseAndReverse"]:
                raise ApiError("position opposée ouverte et CloseAndReverse = false")
            ml_close(doc, key, bars[-1]["c"], bars[-1]["t"], "inversion", p)
        ml_open(doc, key, side, bars[-1], p, "manuel")
        st["last_t"][key] = bars[-1]["t"]
        paper_save(doc)
        return {"position": st["positions"][key]}


def api_ml_close(body):
    key = str(body.get("key") or "")
    c = cfg_rules()
    with _paper_lock:
        doc = paper_load(c.get("budget") or 0)
        st = ml_state(doc)
        pos = st["positions"].get(key)
        if not pos:
            raise ApiError("aucune position MINDLOCK ouverte sur %s" % key.replace("|", " "))
        price = quote(key.split("|")[0])["price"]
        p = dict(ml_params(), PipSize=pos.get("pip") or 0.01, ContractSize=pos.get("contract", 1),
                 UsdRate=pos.get("usd_rate", 1))
        tr = ml_close(doc, key, price, int(time.time()), "clôture manuelle", p)
        paper_save(doc)
    return {"trade": tr}


def live_rules():
    """The rules with today's dollar limits: 0.5 % of the day's opening equity per trade,
    the loss lock as the daily loss limit. Returns (rules, day)."""
    c = cfg_rules()
    with _paper_lock:
        doc = paper_load(c.get("budget") or 0)
        day = paper_view(doc, c)["day"]
        paper_save(doc)
    c["max_loss_per_trade_usd"] = day["risk_per_trade"]
    c["max_daily_loss_usd"] = day["loss_limit"]
    return c, day


def _sym(q, key="symbol"):
    s = str(q.get(key) or "").strip().upper()
    if not TICKER.match(s):
        raise ApiError("symbole invalide : %r" % s)
    return s


def _num(body, key, required=True):
    v = body.get(key)
    if v in (None, ""):
        if required:
            raise ApiError("champ manquant : %s" % key)
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ApiError("%s doit être un nombre" % key)
    if math.isnan(x) or math.isinf(x):
        raise ApiError("%s doit être un nombre" % key)
    return x


def ny_today():
    return ALERTS.to_eastern(utcnow()).date().isoformat()


def api_plan(body):
    d = lambda x: EXIT.D(repr(x))
    entry = _num(body, "entry")
    rule = body.get("stop_rule") or "pct"
    if rule not in ("pct", "price", "atr", "or_mid", "or_low"):
        raise ApiError("règle de stop inconnue")
    param = _num(body, "stop_param", rule in ("pct", "price", "atr"))
    target_r = _num(body, "target_r", False) or 2.0
    kw = {}
    if rule in ("or_mid", "or_low"):
        kw = {"or_high": d(_num(body, "or_high")), "or_low": d(_num(body, "or_low"))}
    elif rule == "atr":
        kw = {"atr": _num(body, "atr"), "atr_bars": 14}
    try:
        res = EXIT.compute(d(entry), rule, d(param if param is not None else 1.0), "r", d(target_r), **kw)
    except EXIT.PlanError as e:
        raise ApiError(str(e))
    return {k: (float(v) if hasattr(v, "quantize") else v) for k, v in res.items()}


def api_size(body):
    sym = _sym(body)
    plan = {"symbol": sym, "side": "buy", "entry": _num(body, "entry"), "stop": _num(body, "stop"),
            "target": _num(body, "target"), "setup": body.get("setup") or "manual", "date": ny_today()}
    c, day = live_rules()
    if day.get("lock"):
        raise ApiError(lock_message(day))
    with _paper_lock:
        cash = paper_load(c.get("budget") or 0)["cash"]
    try:
        s = BUDGET.size_trade(plan, c, budget=day["start_equity"], cash=cash)
    except BUDGET.SizeError as e:
        raise ApiError(str(e))
    return {"sizing": s, "order": BUDGET.bracket_order(s), "day": day}


def api_risk(body):
    order = body.get("order")
    if not isinstance(order, dict):
        raise ApiError("aucun ordre à vérifier")
    c, day = live_rules()
    with _paper_lock:
        doc = paper_load(c.get("budget") or 0)
    port = {"cash": doc["cash"], "positions": [{"ticker": s, "shares": p["qty"],
                                                "lots": [{"shares": p["qty"], "cost": p["qty"] * p["avg"]}]}
                                               for s, p in doc["positions"].items()]}
    today = ny_today()
    state = {"orders": [{"date": f["at"][:10], "status": "placed", "symbol": f["symbol"], "side": f["side"],
                         "qty": f["qty"], "kind": "entry" if f["side"] == "buy" else "exit",
                         "max_loss": f.get("max_loss", 0)} for f in doc["fills"] if f["at"][:10] == today]}
    prices = {}
    try:
        prices[order["symbol"]] = quote(order["symbol"])["price"]
    except Exception:
        pass
    try:
        res = RISK.check([order], c, portfolio=port, prices=prices, state=state)
    except RISK.RiskError as e:
        raise ApiError(str(e))
    return {"result": res, "text": RISK.report(res)}


def api_paper_order(body):
    c = cfg_rules()
    sym = _sym(body)
    side = body.get("side") or "buy"
    if side not in ("buy", "sell"):
        raise ApiError("sens invalide")
    try:
        qty = int(body.get("qty") or 0)
    except (TypeError, ValueError):
        raise ApiError("quantité invalide")
    if qty <= 0:
        raise ApiError("la quantité doit être un entier positif")
    if body.get("confirm") is not True:
        raise ApiError("ordre non confirmé")
    price = quote(sym)["price"]
    if not price:
        raise ApiError("pas de prix pour %s" % sym)
    if side == "buy":
        _, day = live_rules()
        if day.get("lock"):
            raise ApiError(lock_message(day))
        order = {"symbol": sym, "side": "buy", "qty": str(qty), "type": "market", "time_in_force": "day"}
        if body.get("stop") not in (None, "") and body.get("target") not in (None, ""):
            order.update({"order_class": "bracket", "stop_loss_stop_price": "%.2f" % _num(body, "stop"),
                          "take_profit_limit_price": "%.2f" % _num(body, "target")})
        r = api_risk({"order": order})["result"]["results"][0]
        reasons = r["reasons"]
        if CONFIG.get("anytime"):
            reasons = [x for x in reasons if not x.startswith("window:")]
        if reasons:
            raise ApiError("BLOQUÉ par le Risk Manager : " + " ; ".join(reasons))
    with _paper_lock:
        doc = paper_load(c.get("budget") or 0)
        stop, target = _num(body, "stop", False), _num(body, "target", False)
        if side == "buy" and stop is not None and stop >= price:
            raise ApiError("le stop %.2f est au-dessus du prix %.2f" % (stop, price))
        if side == "buy" and target is not None and target <= price:
            raise ApiError("la cible %.2f est sous le prix %.2f" % (target, price))
        _fill(doc, sym, side, qty, price, body.get("why") or "manuel")
        if side == "buy" and stop is not None:
            doc["fills"][0]["max_loss"] = round((price - stop) * qty, 2)
        if side == "buy" and (stop or target):
            doc["orders"].append({"id": "%d" % (time.time() * 1000), "symbol": sym, "qty": qty, "stop": stop,
                                  "target": target, "status": "open",
                                  "at": doc["fills"][0]["at"]})
        if side == "sell" and sym not in doc["positions"]:
            for o in doc["orders"]:
                if o["symbol"] == sym and o["status"] == "open":
                    o["status"] = "cancelled"
        view = paper_view(doc, c)
        paper_save(doc)
        return view


def api_paper(reset=False):
    c = cfg_rules()
    with _paper_lock:
        if reset:
            doc = {"start_cash": c.get("budget") or 0, "cash": c.get("budget") or 0, "positions": {},
                   "orders": [], "fills": [], "log": []}
        else:
            doc = paper_load(c.get("budget") or 0)
            paper_sweep(doc)
            for key in list(ml_state(doc)["positions"]):
                try:
                    ml_step(doc, key)
                except ApiError:
                    pass
        view = paper_view(doc, c)
        paper_save(doc)
        return view


def api_timemachine(body):
    sym = _sym(body)
    text = http_get(TM.chart_url(sym), ttl=300, timeout=30)
    now = utcnow()
    try:
        meta, bars, _ = TM.parse_bars(TM.chart_result(text, sym), now)
    except TM.TMError as e:
        raise ApiError(str(e))
    days = TM.split_sessions(bars)
    if not days:
        raise ApiError("aucune séance complète pour %s" % sym)
    date = body.get("date") or max(days)
    raw = {"symbol": sym, "side": "buy", "date": date, "entry": _num(body, "entry"), "stop": _num(body, "stop"),
           "target": _num(body, "target"), "entry_time": body.get("entry_time") or "10:15 AM",
           "setup": body.get("setup") or "orb", "interval": "5m"}
    try:
        plan = TM.normalize_plan(raw)
        doc = TM.time_machine(text, plan, now, replays=int(body.get("replays") or 10000))
    except TM.TMError as e:
        raise ApiError(str(e))
    doc["mc"].pop("paths", None)
    return doc


def api_scan(q):
    syms = [s.strip().upper() for s in (q.get("symbols") or "").split(",") if s.strip()][:40]
    if not syms:
        raise ApiError("aucun symbole à scanner")
    out = []

    def one(s):
        d = chart(s, "6M", ttl=600)
        closes = [b["c"] for b in d["bars"]]
        setups = SCAN.find_setups(closes)[0] if len(closes) >= 30 else []
        r = SCAN.rsi(closes)
        chg = (closes[-1] / closes[-2] - 1) * 100 if len(closes) > 1 else None
        return {"symbol": s, "price": d["price"], "rsi": r, "setups": setups,
                "change_6m": (closes[-1] / closes[0] - 1) * 100 if closes else None,
                "change_pct": chg}

    futs = {_pool.submit(one, s): s for s in syms}
    for f in concurrent.futures.as_completed(futs):
        try:
            out.append(f.result())
        except Exception as e:
            out.append({"symbol": futs[f], "error": str(e)})
    out.sort(key=lambda r: (-len(r.get("setups") or []), r["symbol"]))
    return {"rows": out}


def api_search(q):
    text = (q.get("q") or "").strip()
    if not text:
        return {"results": []}
    url = ("https://query2.finance.yahoo.com/v1/finance/search?q=%s&quotesCount=8&newsCount=0"
           % urllib.parse.quote(text))
    data = json.loads(http_get(url, ttl=600))
    return {"results": [{"symbol": r.get("symbol"), "name": r.get("shortname") or r.get("longname"),
                         "exchange": r.get("exchDisp"), "type": r.get("typeDisp")}
                        for r in data.get("quotes", []) if r.get("symbol")]}


GET_ROUTES = {
    "/api/mindlock/signal": api_ml_signal, "/api/mindlock/scan": api_ml_scan,
    "/api/quotes": lambda q: {"quotes": quotes([s.strip().upper() for s in (q.get("symbols") or "").split(",")
                                                if s.strip()][:40])},
    "/api/chart": lambda q: chart(_sym(q), q.get("period") or "1D"),
    "/api/news": lambda q: {"items": news(_sym(q))},
    "/api/bias": lambda q: bias(_sym(q)),
    "/api/macro": lambda q: macro(),
    "/api/market": lambda q: ALERTS.market_status(utcnow()),
    "/api/paper": lambda q: api_paper(),
    "/api/config": lambda q: {"rules": cfg_rules(), "anytime": bool(CONFIG.get("anytime"))},
    "/api/scan": api_scan,
    "/api/search": api_search,
}
POST_ROUTES = {
    "/api/plan": api_plan, "/api/size": api_size, "/api/risk": api_risk,
    "/api/paper/order": api_paper_order, "/api/paper/reset": lambda b: api_paper(reset=True),
    "/api/timemachine": api_timemachine,
    "/api/mindlock/backtest": api_ml_backtest, "/api/mindlock/params": api_ml_params,
    "/api/mindlock/auto": api_ml_auto, "/api/mindlock/execute": api_ml_execute,
    "/api/mindlock/close": api_ml_close,
}
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}


class Handler(BaseHTTPRequestHandler):
    server_version = "MINDLOCKTerminal/1.0"

    def log_message(self, fmt, *args):
        if os.environ.get("TERMINAL_LOG"):
            BaseHTTPRequestHandler.log_message(self, fmt, *args)

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _api(self, fn, arg):
        try:
            self._send(200, fn(arg))
        except ApiError as e:
            self._send(e.code, {"error": str(e)})
        except Exception as e:  # keep the terminal alive, say what broke
            self._send(500, {"error": "%s: %s" % (type(e).__name__, e)})

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        if u.path in GET_ROUTES:
            return self._api(GET_ROUTES[u.path], q)
        path = "index.html" if u.path in ("/", "") else u.path.lstrip("/")
        full = os.path.normpath(os.path.join(STATIC, path))
        if not full.startswith(STATIC) or not os.path.isfile(full):
            return self._send(404, {"error": "introuvable"})
        with open(full, "rb") as fh:
            self._send(200, fh.read(), TYPES.get(os.path.splitext(full)[1], "application/octet-stream"))

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if u.path not in POST_ROUTES:
            return self._send(404, {"error": "introuvable"})
        n = int(self.headers.get("Content-Length") or 0)
        if n > 40_000_000:
            return self._send(413, {"error": "requête trop grande"})
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._send(400, {"error": "JSON invalide"})
        if not isinstance(body, dict):
            return self._send(400, {"error": "JSON invalide"})
        self._api(POST_ROUTES[u.path], body)


def main(argv=None):
    ap = argparse.ArgumentParser(description="MINDLOCK Terminal (paper trading, local)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--budget", type=float, help="paper account size (default: trader/config.json or 10000)")
    ap.add_argument("--risk-pct", type=float, help="risk per trade, %% of the account (default 0.5)")
    ap.add_argument("--loss-lock", type=float, help="daily loss lock, %% of the account (default 1.0)")
    ap.add_argument("--win-lock", type=float, help="daily win lock, %% of the account (default 1.0)")
    ap.add_argument("--anytime", action="store_true",
                    help="paper practice outside market hours (skips only the entry-window rule)")
    a = ap.parse_args(argv)
    CONFIG["anytime"] = a.anytime
    for key, val in (("risk_pct_per_trade", a.risk_pct), ("daily_loss_lock_pct", a.loss_lock),
                     ("daily_win_lock_pct", a.win_lock)):
        if val is not None and not 0 <= val <= 100:
            ap.error("les pourcentages vont de 0 à 100")
        CONFIG[key] = val
    CONFIG["budget"] = a.budget
    if not cfg_rules().get("budget"):
        CONFIG["budget"] = 10000.0
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    threading.Thread(target=ml_loop, daemon=True).start()
    print("MINDLOCK Terminal sur http://%s:%d  (compte PAPER, Ctrl+C pour quitter)" % (a.host, a.port))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    sys.exit(main())
