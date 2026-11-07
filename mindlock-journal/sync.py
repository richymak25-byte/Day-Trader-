#!/usr/bin/env python3
"""MINDLOCK Journal: automatic sync.

Collects every execution it can find, pairs buys and sells into round-trip
trades, and builds the dashboard with the data inside it:

    python3 mindlock-journal/sync.py                 # trader/state/broker.json + trader/journal/import/*.csv
    python3 mindlock-journal/sync.py --csv export.csv
    python3 mindlock-journal/sync.py --watch 60      # re-sync every 60 seconds

Writes trader/journal/trades.json and trader/journal/MINDLOCK-Journal.html.
Notes, tags, setups and ratings typed in trades.json are kept on every sync.
Python 3.8+, standard library only.
"""
import argparse
import csv
import glob
import io
import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "index.html")
PLACEHOLDER = "/*__MINDLOCK_DATA__*/null"
EPS = 1e-9

# point value per contract for common futures roots (longest root first)
MULTIPLIERS = [("MNQ", 2.0), ("MES", 5.0), ("MYM", 0.5), ("M2K", 5.0), ("MCL", 100.0),
               ("MGC", 10.0), ("RTY", 50.0), ("NQ", 20.0), ("ES", 50.0), ("YM", 5.0),
               ("CL", 1000.0), ("GC", 100.0)]

USER_FIELDS = ("setup", "tags", "mistakes", "emotion", "rating", "notes", "followedRules",
               "screenshot", "stop", "target")

COLS = {
    "symbol": ["symbol", "ticker", "instrument", "contract", "item", "symbole", "underlying",
               "market", "asset", "actif", "pair", "paire", "contractname"],
    "side": ["side", "action", "buysell", "bs", "direction", "sens", "type", "transactiontype"],
    "qty": ["qty", "quantity", "shares", "size", "volume", "lots", "lot", "filledqty", "quantite",
            "qte", "contracts", "positionsize", "filled", "amount"],
    "price": ["price", "fillprice", "avgprice", "averageprice", "executionprice", "execprice", "prix",
              "tradeprice", "avgfillprice", "filledprice"],
    "time": ["datetime", "time", "timestamp", "executedat", "filledat", "executiontime", "filltime",
             "transacttime", "filledtime", "dateheure", "date", "tradedate"],
    "clock": ["time", "heure", "executiontime"],
    "fees": ["commission", "commissions", "fees", "fee", "comm", "frais", "commfee", "totalfees"],
    "swap": ["swap", "swaps"],
    "openTime": ["opentime", "entrytime", "entrydate", "opendate", "dateouverture", "heureouverture",
                 "boughttimestamp", "openedat"],
    "closeTime": ["closetime", "exittime", "exitdate", "closedate", "datefermeture", "heurefermeture",
                  "soldtimestamp", "closedat"],
    "entry": ["openprice", "entryprice", "prixouverture", "prixentree", "buyprice"],
    "exit": ["closeprice", "exitprice", "prixfermeture", "prixsortie", "sellprice"],
    "pnl": ["profit", "pnl", "pl", "netpnl", "realizedpnl", "profitloss", "gain", "resultat",
            "netprofit", "realizedpl"],
}


def norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]", "", s.lower())


def multiplier(symbol, overrides=None):
    sym = symbol.upper().strip()
    if overrides and sym in overrides:
        return float(overrides[sym])
    for root, mult in MULTIPLIERS:
        if overrides and root in overrides:
            mult = float(overrides[root])
        if re.match(r"^%s(?:[FGHJKMNQUVXZ]\d{1,4}|\s|$|!|1!)" % root, sym):
            return mult
    return 1.0


def parse_num(v):
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    neg = ("(" in s and ")" in s) or "-" in s
    s = re.sub(r"[^0-9,.]", "", s)
    if not s:
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".") if s.count(",") == 1 and not re.search(r",\d{3}$", s) else s.replace(",", "")
    try:
        n = float(s)
    except ValueError:
        return None
    return -n if neg else n


TIME_FORMATS = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M",
                "%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M",
                "%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %I:%M %p", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M",
                "%Y%m%d %H:%M:%S", "%Y%m%d;%H%M%S", "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y.%m.%d"]


def parse_time(v):
    """Any common timestamp to 'YYYY-MM-DD HH:MM:SS' (local exchange time, offset dropped)."""
    if v is None:
        return None
    s = str(v).strip().replace(",", " ")
    if not s:
        return None
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$", "", s) if re.match(r"^\d{4}-\d{2}-\d{2}T", s) else s
    s = re.sub(r"\s+(EST|EDT|ET|UTC|GMT|CET|CEST)$", "", s, flags=re.I)
    s = re.sub(r"\.\d+$", "", s)
    for fmt in TIME_FORMATS:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    if re.match(r"^\d{10}(\.\d+)?$", s):
        return datetime.fromtimestamp(float(s), timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return None


def parse_side(v):
    s = norm(v)
    if re.match(r"^(b$|buy|bot|bought|long|achat|acheter|buytoopen|buytocover|btc|bto)", s):
        return "buy"
    if re.match(r"^(s$|sell|sld|sold|short|vente|vendre|selltoopen|selltoclose|sts|stc|sellshort)", s):
        return "sell"
    return None


def _part(p):
    if p is None:
        return ""
    if isinstance(p, float):
        return "%.10g" % p
    return str(p)


def trade_id(*parts):
    """Same id as the dashboard's tradeId(): two FNV-1a 32-bit hashes over the UTF-8 key,
    so a file synced here and imported again in the browser is not counted twice."""
    data = "|".join(_part(p) for p in parts).encode("utf-8")
    out = ""
    for basis in (2166136261, 2246822519):
        h = basis
        for byte in bytearray(data):
            h = ((h ^ byte) * 16777619) & 0xFFFFFFFF
        out += "%08x" % h
    return "t" + out


# ---------------------------------------------------------------- csv

def read_csv_text(text):
    text = text.lstrip("﻿")
    first = text.splitlines()[0] if text.strip() else ""
    delim = max([",", ";", "\t", "|"], key=first.count)
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    # some brokers put a title above the header: take the first row with 3+ known columns
    known = set(n for names in COLS.values() for n in names)
    for i, row in enumerate(rows[:15]):
        if sum(1 for c in row if norm(c) in known) >= 3:
            return rows[i], [r for r in rows[i + 1:] if any(c.strip() for c in r)]
    return (rows[0], rows[1:]) if rows else ([], [])


def find_cols(header):
    keys = [norm(h) for h in header]
    found = {}
    for field, names in COLS.items():
        for name in names:
            if name in keys:
                idx = keys.index(name)
                if field == "clock" and found.get("time") == idx:
                    continue
                found[field] = idx
                break
    # separate Date and Time columns: the date is the day, the time is the clock
    for d in ("date", "tradedate"):
        if d in keys and "time" in keys and "datetime" not in keys:
            found["time"], found["clock"] = keys.index(d), keys.index("time")
            break
    return found


def parse_csv(text, source="csv", overrides=None):
    """Return (executions, closed_trades) from any broker CSV."""
    header, rows = read_csv_text(text)
    c = find_cols(header)

    def g(row, k):
        i = c.get(k)
        return row[i] if i is not None and i < len(row) else None

    execs, trades = [], []
    closed_format = "symbol" in c and (("entry" in c and "exit" in c) or ("pnl" in c and "openTime" in c))
    if closed_format:
        for row in rows:
            sym = (g(row, "symbol") or "").strip().upper()
            ot, ct = parse_time(g(row, "openTime") or g(row, "time")), parse_time(g(row, "closeTime"))
            qty = abs(parse_num(g(row, "qty")) or 0)
            side = parse_side(g(row, "side") or "")
            bought_first = norm(header[c["openTime"]]) == "boughttimestamp" if "openTime" in c else False
            if side is None and bought_first and ot and ct:
                # Tradovate style: bought before sold means long
                side = "buy" if ot <= ct else "sell"
                if side == "sell":
                    ot, ct = ct, ot
            if not sym or not side or not ot:
                continue
            entry, exit_ = parse_num(g(row, "entry")), parse_num(g(row, "exit"))
            if "entry" in c and norm(header[c["entry"]]) == "buyprice" and side == "sell":
                entry, exit_ = exit_, entry
            fee = 0.0
            for k in ("fees", "swap"):
                n = parse_num(g(row, k))
                if n:
                    # a cost is shown negative by most brokers, positive by some: fees always cost
                    fee += abs(n) if k == "fees" else -n
            pnl = parse_num(g(row, "pnl"))
            mult = multiplier(sym, overrides)
            d = 1 if side == "buy" else -1
            if pnl is None and entry is not None and exit_ is not None:
                pnl = d * (exit_ - entry) * qty * mult
            if pnl is None:
                continue
            net = pnl - fee
            trades.append({
                "id": trade_id(sym, ot, ct, side, qty, entry), "symbol": sym,
                "side": "long" if side == "buy" else "short", "qty": qty, "entry": entry, "exit": exit_,
                "openTime": ot, "closeTime": ct or ot, "fees": round(fee, 2), "pnl": round(net, 2),
                "status": "closed", "source": source, "multiplier": mult, "executions": []})
        return execs, trades

    for row in rows:
        sym = (g(row, "symbol") or "").strip().upper()
        q = parse_num(g(row, "qty"))
        price = parse_num(g(row, "price"))
        side = parse_side(g(row, "side") or "") if "side" in c else None
        if side is None and q is not None and "side" not in c:
            side = "sell" if q < 0 else "buy"
        t = g(row, "time")
        if "clock" in c and c.get("clock") != c.get("time") and g(row, "clock"):
            t = "%s %s" % (t, g(row, "clock"))
        when = parse_time(t)
        if not sym or not side or not q or not price or not when:
            continue
        execs.append({"time": when, "symbol": sym, "side": side, "qty": abs(q), "price": price,
                      "fee": abs(parse_num(g(row, "fees")) or 0.0), "source": source})
    return execs, trades


# ---------------------------------------------------------------- pairing

def pair(executions, overrides=None):
    """FIFO position walk per symbol: a trade opens when the position leaves 0 and
    closes when it comes back to 0. A fill that flips the position is split."""
    by_sym = {}
    for i, e in enumerate(executions):
        by_sym.setdefault(e["symbol"], []).append((e["time"], i, e))
    out = []
    for sym, fills in by_sym.items():
        fills.sort(key=lambda x: (x[0], x[1]))
        mult = multiplier(sym, overrides)
        pos, cur = 0.0, None
        for _, _, f in fills:
            signed = f["qty"] if f["side"] == "buy" else -f["qty"]
            fee_left = f.get("fee", 0.0)
            while abs(signed) > EPS:
                if abs(pos) < EPS:
                    cur = {"symbol": sym, "dir": 1 if signed > 0 else -1, "openQty": 0.0, "openVal": 0.0,
                           "closeQty": 0.0, "closeVal": 0.0, "fees": 0.0, "openTime": f["time"],
                           "closeTime": None, "executions": [], "source": f.get("source", "sync")}
                if pos == 0 or (pos > 0) == (signed > 0):
                    part = abs(signed)
                    cur["openQty"] += part
                    cur["openVal"] += part * f["price"]
                    pos += signed
                    signed = 0.0
                else:
                    part = min(abs(signed), abs(pos))
                    cur["closeQty"] += part
                    cur["closeVal"] += part * f["price"]
                    step = part if signed > 0 else -part
                    pos += step
                    signed -= step
                cur["fees"] += fee_left
                fee_left = 0.0
                cur["executions"].append({"time": f["time"], "side": f["side"], "qty": round(part, 6),
                                          "price": f["price"]})
                if abs(pos) < EPS and cur["closeQty"] > EPS:
                    pos = 0.0
                    cur["closeTime"] = f["time"]
                    out.append(finish(cur, mult))
                    cur = None
        if cur is not None:
            out.append(finish(cur, mult))
    out.sort(key=lambda t: t["openTime"])
    return out


def finish(cur, mult):
    closed = cur["closeTime"] is not None
    entry = cur["openVal"] / cur["openQty"] if cur["openQty"] else None
    exit_ = cur["closeVal"] / cur["closeQty"] if cur["closeQty"] else None
    pnl = None
    if closed:
        pnl = round(cur["dir"] * (cur["closeVal"] - cur["openVal"]) * mult - cur["fees"], 2)
    side = "long" if cur["dir"] > 0 else "short"
    return {"id": trade_id(cur["symbol"], cur["openTime"], side, cur["executions"][0]["price"]),
            "symbol": cur["symbol"], "side": side, "qty": round(cur["openQty"], 6),
            "entry": round(entry, 6) if entry is not None else None,
            "exit": round(exit_, 6) if exit_ is not None else None,
            "openTime": cur["openTime"], "closeTime": cur["closeTime"], "fees": round(cur["fees"], 2),
            "pnl": pnl, "status": "closed" if closed else "open", "source": cur["source"],
            "multiplier": mult, "executions": cur["executions"]}


# ---------------------------------------------------------------- sources

def broker_fills(path):
    """Fills that /trader-broker logged (status 'filled') in trader/state/broker.json."""
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as fh:
        state = json.load(fh)
    out = []
    for rec in state.get("orders", []):
        if rec.get("status") != "filled":
            continue
        when = parse_time(rec.get("at")) or parse_time(rec.get("date"))
        qty, price = parse_num(rec.get("qty")), parse_num(rec.get("price"))
        side = parse_side(rec.get("side", ""))
        if when and qty and price and side and rec.get("symbol"):
            out.append({"time": when, "symbol": rec["symbol"].upper(), "side": side, "qty": abs(qty),
                        "price": price, "fee": 0.0, "source": "trader-broker"})
    return out


def merge(old, new):
    """New trades win on numbers; the user's own fields from the old file are kept."""
    keep = dict((t["id"], t) for t in old)
    seen = set()
    out = []
    for t in new:
        prev = keep.get(t["id"])
        if prev:
            for k in USER_FIELDS:
                if k in prev:
                    t[k] = prev[k]
        seen.add(t["id"])
        out.append(t)
    for t in old:  # trades typed by hand or imported earlier stay
        if t["id"] not in seen:
            out.append(t)
    out.sort(key=lambda t: t.get("openTime") or "")
    return out


def stats(trades):
    closed = [t for t in trades if t.get("status") == "closed" and t.get("pnl") is not None]
    wins = [t["pnl"] for t in closed if t["pnl"] > 0]
    losses = [t["pnl"] for t in closed if t["pnl"] < 0]
    net = sum(t["pnl"] for t in closed)
    pf = (sum(wins) / -sum(losses)) if losses else (float("inf") if wins else 0.0)
    return {"trades": len(closed), "open": len(trades) - len(closed), "net": round(net, 2),
            "win_rate": round(100.0 * len(wins) / len(closed), 1) if closed else 0.0,
            "profit_factor": round(pf, 2) if pf != float("inf") else None}


def build(args):
    overrides = {}
    jdir = os.path.join(args.dir, "journal")
    cfg_path = os.path.join(jdir, "config.json")
    if os.path.isfile(cfg_path):
        with open(cfg_path, encoding="utf-8") as fh:
            overrides = json.load(fh).get("multipliers", {})
    execs = broker_fills(os.path.join(args.dir, "state", "broker.json"))
    closed = []
    files = sorted(glob.glob(os.path.join(jdir, "import", "*.csv"))) + list(args.csv or [])
    for path in files:
        with open(path, encoding="utf-8-sig", errors="replace") as fh:
            e, t = parse_csv(fh.read(), source=os.path.basename(path), overrides=overrides)
        execs += e
        closed += t
        print("  %s: %d executions, %d closed trades" % (path, len(e), len(t)))
    new = pair(execs, overrides) + closed
    trades_path = os.path.join(jdir, "trades.json")
    old = []
    if os.path.isfile(trades_path):
        with open(trades_path, encoding="utf-8") as fh:
            old = json.load(fh).get("trades", [])
    trades = merge(old, new)
    os.makedirs(os.path.join(jdir, "import"), exist_ok=True)
    data = {"app": "MINDLOCK Journal", "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "trades": trades}
    with open(trades_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, ensure_ascii=False)
    with open(TEMPLATE, encoding="utf-8") as fh:
        html = fh.read()
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    out_html = os.path.join(jdir, "MINDLOCK-Journal.html")
    with open(out_html, "w", encoding="utf-8") as fh:
        fh.write(html.replace(PLACEHOLDER, payload, 1))
    s = stats(trades)
    print("MINDLOCK Journal: %d closed trades, %d open, net %.2f, win rate %.1f%%, profit factor %s"
          % (s["trades"], s["open"], s["net"], s["win_rate"],
             "n/a" if s["profit_factor"] is None else s["profit_factor"]))
    print("Dashboard: %s" % out_html)
    return out_html


def main(argv=None):
    p = argparse.ArgumentParser(description="MINDLOCK Journal: sync trades and build the dashboard")
    p.add_argument("--dir", default="trader", help="the trader/ folder (default: trader)")
    p.add_argument("--csv", nargs="*", help="extra broker CSV exports to import")
    p.add_argument("--watch", type=int, default=0, help="re-sync every N seconds")
    a = p.parse_args(argv)
    if not os.path.isfile(TEMPLATE):
        sys.exit("missing template: %s" % TEMPLATE)
    build(a)
    while a.watch > 0:
        time.sleep(a.watch)
        build(a)


if __name__ == "__main__":
    main()
