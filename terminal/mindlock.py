"""MINDLOCK EMA CORE TREND V.1: the strategy engine of the terminal.

A Python port of the author's TradingView Pine Script (v6) strategy "MINDLOCK EMA
CORE TREND V.1", for any asset and any timeframe (M5, M15, M30, H1, H4).

One engine for the three uses: the live signal, the backtest and the paper
auto-trader. It walks closed bars [{t, o, h, l, c}] one by one, like Pine's
strategy with process_orders_on_close=true:

  signal     BUY  : EMA 20 crosses over EMA 50, close > EMA 100, RSI 14 > 50
             SELL : EMA 20 crosses under EMA 50, close < EMA 100, RSI 14 < 50
  entry      MARKET at the signal close, or a LIMIT order: BODY (candle body +
             tolerance, the default), EMA 20, EMA 50, EMA CORE middle, OTE Fibonacci;
             EMA and OTE limits follow the market bar by bar; never worse than the
             signal close; the pending order expires after N bars, is replaced by
             an opposite signal and cancelled outside the session
  stop       structural: lowest low (highest high) of the last 10 bars -/+ buffer,
             capped at the max SL; break-even once price moved the trigger
  target     fixed distance (TP units)
  reverse    an opposite signal closes the position, then enters the other way
  session    09:30-21:45 New York by default: no new order outside it
  day lock   once the day's REALIZED P&L reaches -2 % or +4 % of the day's
             opening equity: pending orders cancelled, no new entry until tomorrow
  size       the terminal's rule: RiskPercent of the equity at the stop
             (Pine's default is 1 contract: UseRiskPercent false + FixedLot 1),
             capped so the position is worth at most MaxLeverage x the equity

Unit (SL / TP / BE / tolerance), as in the script: Auto = futures tick, forex
pip, other assets 10 x the minimum tick.

Simulation limits (Pine's broker emulator is approximated): fills on bar prices,
a gap fills at the open, when a bar touches the stop and the target the path
open -> nearest extreme -> other extreme decides; exits are active from the bar
after the fill; no spread, commission or slippage.
Education and tooling, not financial advice.
"""

import datetime
import math

TIMEFRAMES = ("M5", "M15", "M30", "H1", "H4")
ENTRY_METHODS = ("MARKET", "BODY", "EMA20", "EMA50", "CORE", "OTE")
ENTRY_LABELS = {
    "MARKET": "MARKET - clôture du signal", "BODY": "BODY LIMIT - corps + tolérance",
    "EMA20": "EMA 20 - limit", "EMA50": "EMA 50 - limit", "CORE": "EMA CORE 20/50 - milieu",
    "OTE": "OTE Fibonacci - limit",
}
UNIT_MODES = ("Auto", "Pips", "Ticks", "Points")

PARAMS = {
    "EA_Name": "MINDLOCK EMA CORE TREND V.1",
    # 1. EMA engine
    "EMA20Length": 20, "EMA50Length": 50, "EMA100Length": 100,
    # 2. RSI
    "UseRSI": True, "RSILength": 14, "RSIMid": 50.0,
    "UseEMA100Filter": True,
    # 3. entry
    "EntryMethod": "BODY", "Tolerance": 5.0, "PendingExpiryBars": 10,
    "CancelOnOppositeSignal": True, "CloseOnOppositeSignal": True, "EnterAfterClose": True,
    "OTELevel": 0.705, "OTELookback": 20,
    # 4. risk
    "UnitMode": "Auto", "UnitOverride": 0.0,
    "StopLossMax": 100, "TakeProfit": 300, "StructLookback": 10, "StructBuffer": 2.0,
    "CapStructSL": True, "UseBreakEven": True, "BreakEvenTrigger": 200,
    # 5. daily lock (realized)
    "UseDailyLock": True, "DailyLossPercent": 2.0, "DailyProfitPercent": 4.0,
    # 5b. session (New York)
    "UseSession": True, "Session": "0930-2145", "CloseAtSessionEnd": False,
    # sizing (terminal)
    "UseRiskPercent": True, "RiskPercent": 0.5, "FixedLot": 1.0, "ContractSize": 0.0,
    "MaxLeverage": 30.0,
}
BOOL_KEYS = {k for k, v in PARAMS.items() if isinstance(v, bool)}
NUM_KEYS = {k for k, v in PARAMS.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}

FUTURES_TICK = {"GC": 0.1, "MGC": 0.1, "SI": 0.005, "SIL": 0.005, "HG": 0.0005, "PL": 0.1, "PA": 0.5,
                "CL": 0.01, "MCL": 0.01, "BZ": 0.01, "NG": 0.001, "ES": 0.25, "MES": 0.25, "NQ": 0.25,
                "MNQ": 0.25, "YM": 1.0, "MYM": 1.0, "RTY": 0.1, "M2K": 0.1, "ZB": 0.03125, "ZN": 0.015625,
                "6E": 0.00005, "6B": 0.0001, "6J": 0.0000005, "BTC": 5.0, "MBT": 5.0, "ETH": 0.25}
FUTURES_MULT = {"GC": 100.0, "MGC": 10.0, "SI": 5000.0, "SIL": 1000.0, "HG": 25000.0, "CL": 1000.0,
                "MCL": 100.0, "NG": 10000.0, "ES": 50.0, "MES": 5.0, "NQ": 20.0, "MNQ": 2.0, "YM": 5.0,
                "MYM": 0.5, "RTY": 50.0, "M2K": 5.0}


def params(overrides=None):
    p = dict(PARAMS)
    for k, v in dict(overrides or {}).items():
        if k in BOOL_KEYS:
            p[k] = v in (True, "true", "True", 1, "1")
        elif k in NUM_KEYS:
            try:
                x = float(v)
            except (TypeError, ValueError):
                raise ValueError("%s doit être un nombre" % k)
            if math.isnan(x) or math.isinf(x) or x < 0:
                raise ValueError("%s doit être un nombre positif" % k)
            p[k] = int(x) if isinstance(PARAMS[k], int) else x
        elif k == "EntryMethod":
            if v not in ENTRY_METHODS:
                raise ValueError("mode d'entrée inconnu : %s" % v)
            p[k] = v
        elif k == "UnitMode":
            if v not in UNIT_MODES:
                raise ValueError("unité inconnue : %s" % v)
            p[k] = v
        elif k == "Session":
            parse_session(v)
            p[k] = str(v)
    for k in ("EMA20Length", "EMA50Length", "EMA100Length", "RSILength", "StructLookback", "OTELookback",
              "PendingExpiryBars", "StopLossMax", "TakeProfit", "BreakEvenTrigger"):
        if p[k] < 1:
            raise ValueError("%s doit être au moins 1" % k)
    p["OTELevel"] = min((0.618, 0.705, 0.789), key=lambda x: abs(x - p["OTELevel"]))
    return p


# ---------------------------------------------------------------- the asset

def kind_of(symbol):
    s = (symbol or "").upper()
    if s.endswith("=X"):
        return "forex"
    if s.endswith("=F"):
        return "futures"
    if s.endswith("-USD") or s.endswith("-EUR") or s.endswith("-USDT"):
        return "crypto"
    if s.startswith("^"):
        return "index"
    return "stock"


def root_of(symbol):
    return (symbol or "").upper().split("=")[0]


def mintick(symbol, price):
    k = kind_of(symbol)
    if k == "forex":
        return 0.001 if "JPY" in symbol.upper() else 0.00001
    if k == "futures":
        return FUTURES_TICK.get(root_of(symbol), 0.01)
    if k == "crypto":       # depends on the exchange; scaled on the price (BTC 1, ETH 0.1, SOL 0.01)
        return 10.0 ** (math.floor(math.log10(price)) - 4) if price and price >= 100 else 0.0001
    return 0.01 if (price or 0) >= 1 else 0.0001


def unit_size(symbol, price, mode="Auto", override=0.0):
    """The script's unit: Auto = futures tick, forex pip, others 10 x min tick."""
    if override and override > 0:
        return override
    mt = mintick(symbol, price)
    k = kind_of(symbol)
    pip_fx = (mt * 10 if mt in (0.00001, 0.001) else mt) if k == "forex" else mt * 10
    if mode == "Pips":
        return pip_fx
    if mode == "Ticks":
        return mt
    if mode == "Points":
        return 1.0
    return mt if k == "futures" else pip_fx


def contract_for(symbol):
    s = (symbol or "").upper()
    if kind_of(s) == "forex":
        return 100000.0
    if s == "XAUUSD":
        return 100.0
    if kind_of(s) == "futures":
        return FUTURES_MULT.get(root_of(s), 1.0)
    return 1.0


def for_asset(p, symbol, price, usd_rate=1.0):
    q = dict(p)
    q["Unit"] = unit_size(symbol, price, p["UnitMode"], p["UnitOverride"])
    q["MinTick"] = mintick(symbol, price)
    if not q["ContractSize"]:
        q["ContractSize"] = contract_for(symbol)
    q["UsdRate"] = usd_rate or 1.0
    q["Symbol"] = symbol
    return q


def digits(p):
    t = p.get("MinTick") or 0.01
    return max(2, int(round(-math.log10(t)))) if t < 1 else 2


def rnd(x, p):
    return round(x, digits(p)) if x is not None else None


# ---------------------------------------------------------------- New York time and the session

def _nth_sunday(year, month, n):
    d = datetime.date(year, month, 1)
    d += datetime.timedelta(days=(6 - d.weekday()) % 7)
    return d + datetime.timedelta(weeks=n - 1)


def ny_time(ts):
    utc = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).replace(tzinfo=None)
    y = utc.year
    start = datetime.datetime.combine(_nth_sunday(y, 3, 2), datetime.time(7))   # 2:00 EST
    end = datetime.datetime.combine(_nth_sunday(y, 11, 1), datetime.time(6))    # 2:00 EDT
    return utc + datetime.timedelta(hours=-4 if start <= utc < end else -5)


def parse_session(s):
    try:
        a, b = str(s).split("-")
        if len(a) != 4 or len(b) != 4:
            raise ValueError
        sa, sb = int(a[:2]) * 60 + int(a[2:]), int(b[:2]) * 60 + int(b[2:])
        if not (0 <= sa < 1440 and 0 <= sb <= 1440):
            raise ValueError
        return sa, sb
    except (ValueError, IndexError):
        raise ValueError("session invalide %r (format 0930-2145)" % s)


def in_session(ts, p):
    if not p["UseSession"]:
        return True
    sa, sb = parse_session(p["Session"])
    t = ny_time(ts)
    m = t.hour * 60 + t.minute
    return sa <= m < sb if sa < sb else (m >= sa or m < sb)


# ---------------------------------------------------------------- indicators (Pine ta.*)

def ema(xs, n):
    """ta.ema: seeded with the SMA of the first n values, na before."""
    out = [None] * len(xs)
    if len(xs) < n:
        return out
    k = 2.0 / (n + 1)
    e = sum(xs[:n]) / n
    out[n - 1] = e
    for i in range(n, len(xs)):
        e = xs[i] * k + e * (1 - k)
        out[i] = e
    return out


def rsi(xs, n=14):
    """ta.rsi (Wilder's RMA)."""
    out = [None] * len(xs)
    if len(xs) <= n:
        return out
    g = l = 0.0
    for i in range(1, n + 1):
        d = xs[i] - xs[i - 1]
        g += max(d, 0)
        l += max(-d, 0)
    g, l = g / n, l / n
    out[n] = 100.0 if l == 0 else 100 - 100 / (1 + g / l)
    for i in range(n + 1, len(xs)):
        d = xs[i] - xs[i - 1]
        g = (g * (n - 1) + max(d, 0)) / n
        l = (l * (n - 1) + max(-d, 0)) / n
        out[i] = 100.0 if l == 0 else 100 - 100 / (1 + g / l)
    return out


def indicators(bars, p):
    c = [b["c"] for b in bars]
    return {"e20": ema(c, p["EMA20Length"]), "e50": ema(c, p["EMA50Length"]),
            "e100": ema(c, p["EMA100Length"]), "rsi": rsi(c, p["RSILength"])}


def lowest(bars, i, n):
    return min(b["l"] for b in bars[max(0, i - n + 1):i + 1])


def highest(bars, i, n):
    return max(b["h"] for b in bars[max(0, i - n + 1):i + 1])


# ---------------------------------------------------------------- the signal

def checks(bars, ind, i, p):
    """Every condition of the signal on closed bar i, for both sides."""
    e20, e50, e100, r = ind["e20"], ind["e50"], ind["e100"], ind["rsi"]
    if i < 1 or None in (e20[i], e50[i], e20[i - 1], e50[i - 1], e100[i]) or (p["UseRSI"] and r[i] is None):
        return None
    c = bars[i]["c"]
    up = e20[i] > e50[i] and e20[i - 1] <= e50[i - 1]
    down = e20[i] < e50[i] and e20[i - 1] >= e50[i - 1]
    fmt = "%." + str(digits(p)) + "f"
    f = lambda x: fmt % x
    out = {
        "buy": [("Croisement EMA 20 ↑ EMA 50", up, "EMA 20 %s / EMA 50 %s" % (f(e20[i]), f(e50[i])))],
        "sell": [("Croisement EMA 20 ↓ EMA 50", down, "EMA 20 %s / EMA 50 %s" % (f(e20[i]), f(e50[i])))],
    }
    if p.get("UseEMA100Filter", True):
        out["buy"].append(("Clôture > EMA 100", c > e100[i], "%s vs %s" % (f(c), f(e100[i]))))
        out["sell"].append(("Clôture < EMA 100", c < e100[i], "%s vs %s" % (f(c), f(e100[i]))))
    if p["UseRSI"]:
        out["buy"].append(("RSI > %g" % p["RSIMid"], r[i] > p["RSIMid"], "RSI %.1f" % r[i]))
        out["sell"].append(("RSI < %g" % p["RSIMid"], r[i] < p["RSIMid"], "RSI %.1f" % r[i]))
    return out


def signal_at(bars, ind, i, p):
    ch = checks(bars, ind, i, p)
    if not ch:
        return None
    for side in ("buy", "sell"):
        if all(ok for _, ok, _ in ch[side]):
            return side
    return None


# ---------------------------------------------------------------- prices

def entry_price(side, bars, ind, i, p, ote=None):
    """f_entryPrice / f_dynamicOtePrice: the limit price, never worse than the close."""
    b = bars[i]
    buy = side == "buy"
    tol = p["Tolerance"] * p["Unit"]
    m = p["EntryMethod"]
    if m == "BODY":
        x = min(b["o"], b["c"]) + tol if buy else max(b["o"], b["c"]) - tol
    elif m == "EMA20":
        x = ind["e20"][i] + tol if buy else ind["e20"][i] - tol
    elif m == "EMA50":
        x = ind["e50"][i] + tol if buy else ind["e50"][i] - tol
    elif m == "CORE":
        mid = (ind["e20"][i] + ind["e50"][i]) / 2.0
        x = mid + tol if buy else mid - tol
    elif m == "OTE":
        lo, hi = ote if ote else (lowest(bars, i, p["OTELookback"]), highest(bars, i, p["OTELookback"]))
        rng = hi - lo
        x = hi - rng * p["OTELevel"] + tol if buy else lo + rng * p["OTELevel"] - tol
    else:
        x = b["c"]
    return min(x, b["c"]) if buy else max(x, b["c"])


def struct_sl(side, entry, bars, i, p):
    """f_calcSL: structural stop, capped at the max SL, always on the right side."""
    buy = side == "buy"
    buf, cap = p["StructBuffer"] * p["Unit"], p["StopLossMax"] * p["Unit"]
    s = lowest(bars, i, p["StructLookback"]) - buf if buy else highest(bars, i, p["StructLookback"]) + buf
    dist = entry - s if buy else s - entry
    if p["CapStructSL"] and dist > cap:
        s = entry - cap if buy else entry + cap
    if buy and s >= entry:
        s = entry - cap
    if not buy and s <= entry:
        s = entry + cap
    return s


def target_of(side, entry, p):
    d = p["TakeProfit"] * p["Unit"]
    return entry + d if side == "buy" else entry - d


def risk_per_lot(sl_distance, p):
    return sl_distance * p.get("ContractSize", 1) * p.get("UsdRate", 1)


def lots_for(balance, sl_distance, p, price=None):
    if not p["UseRiskPercent"]:
        lots = round(p["FixedLot"], 2)
    else:
        per = risk_per_lot(sl_distance, p)
        if per <= 0:
            return 0.0
        lots = balance * p["RiskPercent"] / 100.0 / per
    if price and p.get("MaxLeverage"):
        notional = price * p.get("ContractSize", 1) * p.get("UsdRate", 1)
        if notional > 0:
            lots = min(lots, balance * p["MaxLeverage"] / notional)
    return max(0.0, math.floor(lots * 100 + 1e-9) / 100.0)


def pnl_usd(side, entry, exit_, lots, p):
    return (exit_ - entry) * (1 if side == "buy" else -1) * lots * p.get("ContractSize", 1) * p.get("UsdRate", 1)


def units(side, entry, exit_, p):
    return (exit_ - entry) * (1 if side == "buy" else -1) / p["Unit"]


# ---------------------------------------------------------------- the engine

def new_state():
    return {"pos": None, "pending": None, "last_signal_t": None, "trades": [], "events": [],
            "day": None, "day_start_equity": None, "day_start_net": 0.0, "net": 0.0, "locked": False,
            "last_t": None}


class Engine:
    """Bar-by-bar port of the Pine strategy. `st` is a plain dict (JSON-safe) so the live
    paper trader can store it between runs.

    hooks (all optional):
      balance()          equity for the size (default: start balance + realized)
      locked(st)         external day lock (the terminal's); default: the script's realized lock
      on_open(pos)       a position was opened
      on_close(trade)    a position was closed"""

    def __init__(self, bars, p, st=None, balance=100000.0, hooks=None):
        self.bars, self.p = bars, p
        self.ind = indicators(bars, p)
        self.st = st if st is not None else new_state()
        self.start_balance = balance
        self.h = hooks or {}

    def equity_now(self, i):
        eq = self.start_balance + self.st["net"]
        pos = self.st["pos"]
        if pos:
            eq += pnl_usd(pos["side"], pos["entry"], self.bars[i]["c"], pos["lots"], self.p)
        return eq

    def _balance(self):
        return self.h["balance"]() if "balance" in self.h else self.start_balance + self.st["net"]

    def _event(self, i, kind, text):
        ev = self.st["events"]
        ev.insert(0, {"t": self.bars[i]["t"], "kind": kind, "text": text})
        del ev[100:]

    def _open(self, side, price, i, why):
        p = self.p
        sl = struct_sl(side, price, self.bars, i, p)
        dist = abs(price - sl)
        lots = lots_for(self._balance(), dist, p, price)
        if lots <= 0:
            self._event(i, "skip", "signal %s ignoré : taille nulle pour ce stop" % side.upper())
            return None
        pos = {"side": side, "entry": rnd(price, p), "t": self.bars[i]["t"], "sl0": rnd(sl, p),
               "sl": rnd(sl, p), "tp": rnd(target_of(side, price, p), p), "lots": lots, "be": False,
               "risk_usd": round(risk_per_lot(dist, p) * lots, 2), "why": why}
        self.st["pos"] = pos
        self._event(i, "open", "%s @ %s | SL %s | TP %s | %s lot" % ("BUY" if side == "buy" else "SELL",
                                                                 pos["entry"], pos["sl"], pos["tp"], lots))
        if "on_open" in self.h:
            self.h["on_open"](pos)
        return pos

    def _close(self, price, i, reason):
        p, pos = self.p, self.st["pos"]
        pnl = round(pnl_usd(pos["side"], pos["entry"], price, pos["lots"], p), 2)
        tr = dict(pos, exit=rnd(price, p), exit_t=self.bars[i]["t"], reason=reason, pnl=pnl,
                  units=round(units(pos["side"], pos["entry"], price, p), 1))
        self.st["trades"].insert(0, tr)
        del self.st["trades"][2000:]
        self.st["net"] = round(self.st["net"] + pnl, 2)
        self.st["pos"] = None
        self._event(i, "close", "%s fermé (%s) %+.2f $" % ("BUY" if tr["side"] == "buy" else "SELL", reason, pnl))
        if "on_close" in self.h:
            self.h["on_close"](tr)
        return tr

    # -- the broker emulator: what happens inside bar i
    def _emulate(self, i):
        b, st, p = self.bars[i], self.st, self.p
        pos = st["pos"]
        if pos and pos["t"] < b["t"]:
            buy = pos["side"] == "buy"
            stop, tp = pos["sl"], pos["tp"]
            if buy:
                stop_open, tp_open, stop_hit, tp_hit = b["o"] <= stop, b["o"] >= tp, b["l"] <= stop, b["h"] >= tp
            else:
                stop_open, tp_open, stop_hit, tp_hit = b["o"] >= stop, b["o"] <= tp, b["h"] >= stop, b["l"] <= tp
            label = "BE" if pos["be"] else "SL"
            if stop_open:
                self._close(b["o"], i, label)
            elif tp_open:
                self._close(b["o"], i, "TP")
            elif stop_hit and tp_hit:
                high_first = (b["h"] - b["o"]) <= (b["o"] - b["l"])     # Pine: nearest extreme first
                tp_first = high_first if buy else not high_first
                self._close(tp if tp_first else stop, i, "TP" if tp_first else label)
            elif stop_hit:
                self._close(stop, i, label)
            elif tp_hit:
                self._close(tp, i, "TP")
        pend = st["pending"]
        if pend and not st["pos"] and pend["t"] < b["t"]:
            lim, buy = pend["price"], pend["side"] == "buy"
            fill = None
            if buy and b["o"] <= lim:
                fill = b["o"]
            elif buy and b["l"] <= lim:
                fill = lim
            elif not buy and b["o"] >= lim:
                fill = b["o"]
            elif not buy and b["h"] >= lim:
                fill = lim
            if fill is not None:
                st["pending"] = None
                self._open(pend["side"], fill, i, "limit " + p["EntryMethod"])

    # -- the script at the close of bar i
    def _script(self, i):
        b, st, p, ind = self.bars[i], self.st, self.p, self.ind
        day = ny_time(b["t"]).strftime("%Y-%m-%d")
        if st["day"] != day:
            st["day"], st["day_start_equity"], st["day_start_net"], st["locked"] = day, self.equity_now(i), st["net"], False
        if "locked" in self.h:
            locked = bool(self.h["locked"](st))
        else:
            realized = st["net"] - st["day_start_net"]
            pct = realized / st["day_start_equity"] * 100 if st["day_start_equity"] else 0.0
            if p["UseDailyLock"] and not st["locked"] and (pct <= -p["DailyLossPercent"] or pct >= p["DailyProfitPercent"]):
                st["locked"] = True
                self._event(i, "lock", "DAILY LOCK : %+.2f %% réalisé aujourd'hui, plus d'entrée" % pct)
            locked = p["UseDailyLock"] and st["locked"]
        if locked and st["pending"]:
            st["pending"] = None
            self._event(i, "cancel", "ordre annulé (daily lock)")
        sig = signal_at(self.bars, ind, i, p)
        pos = st["pos"]
        flat = pos is None
        opp = p["CloseOnOppositeSignal"] and pos is not None and sig is not None and sig != pos["side"]
        reverse = opp and p["EnterAfterClose"]
        insess = in_session(b["t"], p)
        pend = st["pending"]
        if pend and pend["t"] < b["t"]:
            pend["age"] = pend.get("age", 0) + 1
        fresh = st["last_signal_t"] != b["t"]
        if flat and pend:
            expired = pend["age"] >= p["PendingExpiryBars"]
            opposite = p["CancelOnOppositeSignal"] and fresh and sig is not None and sig != pend["side"]
            outside = p["UseSession"] and not insess
            if expired or opposite or outside:
                self._event(i, "cancel", "ordre %s annulé (%s)" % ("BUY" if pend["side"] == "buy" else "SELL",
                            "hors session" if outside else "remplacé, signal opposé" if opposite else "expiré"))
                st["pending"] = None
        if pos is not None:
            st["pending"] = None
            if sig and fresh and not reverse:
                st["last_signal_t"] = b["t"]
        if opp:
            self._close(b["c"], i, "signal opposé")
        if p["CloseAtSessionEnd"] and st["pos"] and i > 0 and in_session(self.bars[i - 1]["t"], p) and not insess:
            self._close(b["c"], i, "fin de session")
        can = (not locked and insess and (flat or reverse) and st["pending"] is None
               and st["last_signal_t"] != b["t"])
        if can and sig and self.h.get("allow_entry", lambda: True)():
            self.enter(i, sig)
        elif sig and not st["pos"] and st["last_signal_t"] != b["t"]:
            self._event(i, "signal", "signal %s %s" % ("BUY" if sig == "buy" else "SELL",
                        "ignoré (daily lock)" if locked else "ignoré (hors session)" if not insess
                        else "en attente de ta décision" if not self.h.get("allow_entry", lambda: True)()
                        else "ignoré"))
        self._track_pending(i, locked)
        self._break_even(i)
        return sig

    def enter(self, i, sig):
        """Creates the entry of a signal on bar i: market at the close or a limit order."""
        b, st, p, ind = self.bars[i], self.st, self.p, self.ind
        st["last_signal_t"] = b["t"]
        if p["EntryMethod"] == "MARKET":
            return self._open(sig, b["c"], i, "market")
        ote = None
        if p["EntryMethod"] == "OTE":
            ote = [lowest(self.bars, i, p["OTELookback"]), highest(self.bars, i, p["OTELookback"])]
        st["pending"] = {"side": sig, "t": b["t"], "age": 0, "ote": ote,
                         "price": rnd(entry_price(sig, self.bars, ind, i, p, ote), p)}
        st["pending"]["sl"] = rnd(struct_sl(sig, st["pending"]["price"], self.bars, i, p), p)
        st["pending"]["tp"] = rnd(target_of(sig, st["pending"]["price"], p), p)
        self._event(i, "pending", "ordre limit %s @ %s" % ("BUY" if sig == "buy" else "SELL", st["pending"]["price"]))
        return st["pending"]

    def _track_pending(self, i, locked):
        b, st, p, ind = self.bars[i], self.st, self.p, self.ind
        pend = st["pending"]
        if pend and not st["pos"] and not locked:
            if p["EntryMethod"] == "OTE":
                pend["ote"] = [min(pend["ote"][0], b["l"]), max(pend["ote"][1], b["h"])]
                pend["price"] = rnd(entry_price(pend["side"], self.bars, ind, i, p, pend["ote"]), p)
            elif p["EntryMethod"] in ("EMA20", "EMA50", "CORE"):
                pend["price"] = rnd(entry_price(pend["side"], self.bars, ind, i, p), p)
            pend["sl"] = rnd(struct_sl(pend["side"], pend["price"], self.bars, i, p), p)
            pend["tp"] = rnd(target_of(pend["side"], pend["price"], p), p)

    def _break_even(self, i):
        b, st, p = self.bars[i], self.st, self.p
        pos = st["pos"]
        if pos:
            buy = pos["side"] == "buy"
            trig = p["BreakEvenTrigger"] * p["Unit"]
            if p["UseBreakEven"] and not pos["be"] and (b["h"] >= pos["entry"] + trig if buy else b["l"] <= pos["entry"] - trig):
                pos["be"] = True
                pos["sl"] = pos["entry"]
                self._event(i, "be", "break-even activé (SL à l'entrée)")

    def step(self, i):
        self._emulate(i)
        sig = self._script(i)
        self.st["last_t"] = self.bars[i]["t"]
        return sig


# ---------------------------------------------------------------- backtest

def backtest(bars, p, balance=100000.0):
    eng = Engine(bars, p, balance=balance)
    curve, markers = [], []
    peak, max_dd = balance, 0.0
    locked_days = set()
    every = max(1, len(bars) // 600)
    for i in range(len(bars)):
        sig = eng.step(i)
        if sig:
            markers.append({"t": bars[i]["t"], "side": sig})
        if eng.st["locked"]:
            locked_days.add(eng.st["day"])
        eq = eng.equity_now(i)
        peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)
        if i % every == 0 or i == len(bars) - 1:
            curve.append({"t": bars[i]["t"], "equity": round(eq, 2)})
    trades = eng.st["trades"]
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gw, gl = sum(t["pnl"] for t in wins), -sum(t["pnl"] for t in losses)
    end = balance + eng.st["net"]
    exits = {}
    for t in trades:
        exits[t["reason"]] = exits.get(t["reason"], 0) + 1
    return {
        "params": p, "bars": len(bars), "from": bars[0]["t"] if bars else None, "to": bars[-1]["t"] if bars else None,
        "start_balance": balance, "end_balance": round(end, 2), "net": round(end - balance, 2),
        "net_pct": round((end / balance - 1) * 100, 2) if balance else 0,
        "net_units": round(sum(t["units"] for t in trades), 1),
        "trades": len(trades), "wins": len(wins), "losses": len(losses),
        "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else 0,
        "profit_factor": round(gw / gl, 2) if gl else None,
        "avg_win": round(gw / len(wins), 2) if wins else 0, "avg_loss": round(-gl / len(losses), 2) if losses else 0,
        "max_drawdown": round(max_dd, 2), "max_drawdown_pct": round(max_dd / peak * 100, 2) if peak else 0,
        "locked_days": len(locked_days), "exits": exits, "signals": len(markers),
        "open_position": eng.st["pos"], "pending": eng.st["pending"],
        "trade_list": trades[:200], "curve": curve, "markers": markers[-400:],
    }


# ---------------------------------------------------------------- timeframes and CSV

def aggregate(bars, seconds):
    """Group bars into buckets of `seconds` aligned on UTC (H4 from H1 bars)."""
    out = []
    for b in bars:
        k = b["t"] - b["t"] % seconds
        if out and out[-1]["t"] == k:
            o = out[-1]
            o["h"], o["l"], o["c"] = max(o["h"], b["h"]), min(o["l"], b["l"]), b["c"]
            o["v"] = o.get("v", 0) + b.get("v", 0)
        else:
            out.append({"t": k, "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b.get("v", 0)})
    return out


def parse_csv(text):
    """MetaTrader or TradingView export: DATE TIME OPEN HIGH LOW CLOSE ... ('YYYY.MM.DD HH:MM'),
    or time,open,high,low,close with a UNIX time or an ISO date. Returns bars, oldest first."""
    bars = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line[0] in "<#" or line[:4].lower() in ("date", "time"):
            continue
        parts = [x for x in line.replace(";", ",").replace("\t", ",").split(",") if x != ""]
        if len(parts) < 5:
            parts = line.split()
        try:
            if parts[0].isdigit() and len(parts[0]) >= 9:
                t = int(parts[0])
                o, h, l, c = (float(x) for x in parts[1:5])
            else:
                if len(parts) > 5 and ":" in parts[1]:
                    stamp, nums = parts[0] + " " + parts[1], parts[2:6]
                else:
                    stamp, nums = parts[0], parts[1:5]
                stamp = stamp.replace(".", "-").replace("/", "-").replace("T", " ").strip()
                dt = datetime.datetime.strptime(stamp[:16], "%Y-%m-%d %H:%M")
                t = int(dt.replace(tzinfo=datetime.timezone.utc).timestamp())
                o, h, l, c = (float(x) for x in nums)
        except (ValueError, IndexError):
            continue
        bars.append({"t": t, "o": o, "h": h, "l": l, "c": c})
    bars.sort(key=lambda b: b["t"])
    return bars
