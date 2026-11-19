"""MINDLOCK EMA CORE (V3 EMA TOUCH): the strategy engine of the terminal.

Universal: any asset, any of the swing timeframes M5, M15, M30, H1, H4. Stop and
target are in pips; PipSize 0 means automatic (about 1/10,000 of the price rounded
to a power of ten: 0.1 on gold, 0.0001 on EURUSD, 0.01 on USDJPY, 1 on bitcoin).

One engine for the three uses: the live signal, the backtest and the paper
auto-trader. Pure functions on bars [{t, o, h, l, c}], oldest first, closed bars only.

The inputs mirror the Expert Advisor's (MetaTrader) inputs. The rules below are
this module's reading of them; check them against the EA's source:

  trend      buy  : EMA fast > EMA slow          sell : EMA fast < EMA slow
  touch      the signal bar's range reaches the touch EMA (EMATouchReference 0 = fast,
             1 = slow) within EMATouchTolerancePips
  RSI        momentum: buy RSI > 50, sell RSI < 50 (RSIMomentum)
             extreme : no buy at or above RSIUpper, no sell at or below RSILower (RSIExtreme)
  candle     buy needs a bull candle (close > open), sell a bear candle (RequireBull/BearCandle)
  break      optional: buy close > previous high, sell close < previous low
  entry      at the close of the signal bar (= next bar's open), one signal per bar
  exits      stop StopLossPips, target TakeProfitPips (pip = PipSize, 0.10 $ on XAUUSD);
             if one bar touches both, the stop is counted first (conservative)
             optional break-even after BreakEvenPips
  reverse    CloseAndReverse: an opposite signal closes the trade and opens the other way
  size       UseRiskPercent: lots = balance x RiskPercent / (stop in $ per lot), else FixedLot
  day lock   UseDailyLock: no new entry once the day's P&L reaches -DailyLossPercent or
             +DailyProfitPercent of the day's opening balance (UTC day in the backtest)

Spread filter and slippage need tick data and are not simulated.
Education and tooling, not financial advice.
"""

import datetime
import math

TIMEFRAMES = ("M5", "M15", "M30", "H1", "H4")

PARAMS = {
    "EA_Name": "MINDLOCK EMA CORE XAUUSD V3 EMA TOUCH",

    "FastEMA": 20,
    "SlowEMA": 50,
    "EMATouchReference": 0,
    "EMATouchTolerancePips": 0.0,
    "RSIPeriod": 14,
    "UseRSI": True,
    "RSIMomentum": True,
    "RSIExtreme": True,
    "RSIUpper": 75.0,
    "RSILower": 25.0,
    "RequireBullCandle": True,
    "RequireBearCandle": True,
    "RequireBreakHigh": False,
    "RequireBreakLow": False,
    "UseRiskPercent": True,
    "RiskPercent": 0.5,
    "FixedLot": 1.0,
    "PipSize": 0.0,
    "ContractSize": 0.0,
    "StopLossPips": 150,
    "TakeProfitPips": 600,
    "UseBreakEven": False,
    "BreakEvenPips": 100.0,
    "UseDailyLock": True,
    "DailyLossPercent": 2.0,
    "DailyProfitPercent": 6.0,
    "CloseAndReverse": True,
    "MaxPositions": 1,
    "OneSignalPerBar": True,
}

BOOL_KEYS = {k for k, v in PARAMS.items() if isinstance(v, bool)}
NUM_KEYS = {k for k, v in PARAMS.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}


def params(overrides=None):
    p = dict(PARAMS)
    overrides = dict(overrides or {})
    if "XAUUSD_PipSize" in overrides and "PipSize" not in overrides:   # the EA's name for it
        overrides["PipSize"] = overrides["XAUUSD_PipSize"]
    for k, v in overrides.items():
        if k in BOOL_KEYS:
            p[k] = v in (True, "true", "True", 1, "1")
        elif k in NUM_KEYS:
            x = float(v)
            if math.isnan(x) or math.isinf(x) or x < 0:
                raise ValueError("%s doit être un nombre positif" % k)
            p[k] = int(x) if isinstance(PARAMS[k], int) else x
    if p["FastEMA"] < 1 or p["SlowEMA"] < 1 or p["RSIPeriod"] < 2:
        raise ValueError("périodes EMA / RSI invalides")
    if p["StopLossPips"] <= 0 or p["TakeProfitPips"] <= 0:
        raise ValueError("stop et cible doivent être > 0")
    return p


def auto_pip(price):
    """About 1/10,000 of the price, as a power of ten."""
    if not price or price <= 0:
        return 0.01
    return 10.0 ** (math.floor(math.log10(price)) - 4)


def contract_for(symbol):
    s = (symbol or "").upper()
    if s.endswith("=X"):
        return 100000.0          # forex lot
    if s in ("GC=F", "XAUUSD", "MGC=F"):
        return 100.0             # gold lot, 100 oz
    if s in ("SI=F", "XAGUSD"):
        return 5000.0
    return 1.0                   # stocks, indices, crypto: 1 unit per lot


def for_asset(p, symbol, price, usd_rate=1.0):
    """The params resolved for one asset: pip size, contract size and the quote
    currency's value in USD (P&L and sizing are in USD)."""
    q = dict(p)
    if not q["PipSize"]:
        q["PipSize"] = auto_pip(price)
    if not q["ContractSize"]:
        q["ContractSize"] = contract_for(symbol)
    q["UsdRate"] = usd_rate or 1.0
    return q


# ---------------------------------------------------------------- indicators (MetaTrader style)

def ema(xs, n):
    """EMA seeded with the SMA of the first n values (as iMA does); None before that."""
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
    """Wilder's RSI; None until n + 1 closes."""
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
    closes = [b["c"] for b in bars]
    return {"fast": ema(closes, p["FastEMA"]), "slow": ema(closes, p["SlowEMA"]),
            "rsi": rsi(closes, p["RSIPeriod"])}


# ---------------------------------------------------------------- the signal

def _f(x):
    return ("%.5f" % x).rstrip("0").rstrip(".") if abs(x) < 10 else "%.2f" % x


def checks(bars, ind, i, p):
    """Every filter for both sides on closed bar i: {"buy": [(name, ok, detail)], "sell": [...]}."""
    b = bars[i]
    f, s, r = ind["fast"][i], ind["slow"][i], ind["rsi"][i]
    if f is None or s is None or (p["UseRSI"] and r is None) or i < 1:
        return None
    ref = f if int(p["EMATouchReference"]) == 0 else s
    tol = p["EMATouchTolerancePips"] * p["PipSize"]
    touch = b["l"] - tol <= ref <= b["h"] + tol
    ref_name = "EMA %d" % (p["FastEMA"] if int(p["EMATouchReference"]) == 0 else p["SlowEMA"])
    prev = bars[i - 1]
    out = {}
    for side in ("buy", "sell"):
        up = side == "buy"
        c = [("Tendance EMA %d %s EMA %d" % (p["FastEMA"], ">" if up else "<", p["SlowEMA"]),
              f > s if up else f < s, "%s / %s" % (_f(f), _f(s))),
             ("Touche de la %s" % ref_name, touch, "bougie %s – %s, EMA %s" % (_f(b["l"]), _f(b["h"]), _f(ref)))]
        if p["UseRSI"]:
            if p["RSIMomentum"]:
                c.append(("RSI %s 50" % (">" if up else "<"), r > 50 if up else r < 50, "RSI %.1f" % r))
            if p["RSIExtreme"]:
                c.append(("RSI pas en zone extrême (%s %.0f)" % ("<" if up else ">", p["RSIUpper"] if up else p["RSILower"]),
                          r < p["RSIUpper"] if up else r > p["RSILower"], "RSI %.1f" % r))
        if up and p["RequireBullCandle"]:
            c.append(("Bougie haussière", b["c"] > b["o"], "O %s → C %s" % (_f(b["o"]), _f(b["c"]))))
        if not up and p["RequireBearCandle"]:
            c.append(("Bougie baissière", b["c"] < b["o"], "O %s → C %s" % (_f(b["o"]), _f(b["c"]))))
        if up and p["RequireBreakHigh"]:
            c.append(("Clôture > plus haut précédent", b["c"] > prev["h"], "%s vs %s" % (_f(b["c"]), _f(prev["h"]))))
        if not up and p["RequireBreakLow"]:
            c.append(("Clôture < plus bas précédent", b["c"] < prev["l"], "%s vs %s" % (_f(b["c"]), _f(prev["l"]))))
        out[side] = c
    return out


def signal_at(bars, ind, i, p):
    ch = checks(bars, ind, i, p)
    if not ch:
        return None
    for side in ("buy", "sell"):
        if all(ok for _, ok, _ in ch[side]):
            return side
    return None


def digits(p):
    """Price decimals: one more than the pip's (0.1 -> 2, 0.0001 -> 5), at least 2."""
    return max(2, int(round(-math.log10(p["PipSize"]))) + 1) if p["PipSize"] < 1 else 2


def levels(side, entry, p):
    pip, nd = p["PipSize"], digits(p)
    sl_d, tp_d = p["StopLossPips"] * pip, p["TakeProfitPips"] * pip
    if side == "buy":
        return round(entry - sl_d, nd), round(entry + tp_d, nd)
    return round(entry + sl_d, nd), round(entry - tp_d, nd)


def lots_for(balance, p):
    if not p["UseRiskPercent"]:
        return round(p["FixedLot"], 2)
    risk_usd = balance * p["RiskPercent"] / 100.0
    per_lot = risk_per_lot(p)
    return max(0.0, math.floor(risk_usd / per_lot * 100) / 100.0) if per_lot > 0 else 0.0


def risk_per_lot(p):
    return p["StopLossPips"] * p["PipSize"] * p.get("ContractSize", 1) * p.get("UsdRate", 1)


def pnl_usd(side, entry, exit_, lots, p):
    return (exit_ - entry) * (1 if side == "buy" else -1) * lots * p.get("ContractSize", 1) * p.get("UsdRate", 1)


def pips(side, entry, exit_, p):
    return (exit_ - entry) * (1 if side == "buy" else -1) / p["PipSize"]


# ---------------------------------------------------------------- one step (shared by backtest and live)

def manage(pos, bar, p):
    """Bar-by-bar exit check for an open position. Returns (exit_price, reason) or None.
    Moves the stop to break-even when allowed (after the exit check, so not on the same bar)."""
    if pos["side"] == "buy":
        if bar["l"] <= pos["sl"]:
            return pos["sl"], "stop" if pos["sl"] < pos["entry"] else "break-even"
        if bar["h"] >= pos["tp"]:
            return pos["tp"], "cible"
        if p["UseBreakEven"] and bar["h"] - pos["entry"] >= p["BreakEvenPips"] * p["PipSize"]:
            pos["sl"] = max(pos["sl"], pos["entry"])
    else:
        if bar["h"] >= pos["sl"]:
            return pos["sl"], "stop" if pos["sl"] > pos["entry"] else "break-even"
        if bar["l"] <= pos["tp"]:
            return pos["tp"], "cible"
        if p["UseBreakEven"] and pos["entry"] - bar["l"] >= p["BreakEvenPips"] * p["PipSize"]:
            pos["sl"] = min(pos["sl"], pos["entry"])
    return None


def close_trade(pos, price, t, reason, p):
    return dict(pos, exit=round(price, digits(p)), exit_t=t, reason=reason,
                pips=round(pips(pos["side"], pos["entry"], price, p), 1),
                pnl=round(pnl_usd(pos["side"], pos["entry"], price, pos["lots"], p), 2))


def open_trade(side, bar, balance, p):
    entry = bar["c"]
    sl, tp = levels(side, entry, p)
    nd = digits(p)
    lots = lots_for(balance, p)
    if lots <= 0:
        return None
    return {"side": side, "entry": round(entry, nd), "t": bar["t"], "sl": sl, "tp": tp, "lots": lots}


def lock_state(day_pnl, day_start, p):
    if not p["UseDailyLock"] or day_start <= 0:
        return None
    if day_pnl <= -day_start * p["DailyLossPercent"] / 100:
        return "loss"
    if day_pnl >= day_start * p["DailyProfitPercent"] / 100:
        return "win"
    return None


# ---------------------------------------------------------------- backtest

def backtest(bars, p, balance=100000.0):
    """Runs the strategy over closed bars. Returns stats, trades, equity curve and signal markers."""
    ind = indicators(bars, p)
    trades, curve, markers = [], [], []
    pos = None
    bal = balance
    peak, max_dd = balance, 0.0
    day, day_start, day_pnl, locked_days = None, bal, 0.0, set()
    for i, bar in enumerate(bars):
        d = datetime.datetime.fromtimestamp(bar["t"], datetime.timezone.utc).strftime("%Y-%m-%d")
        if d != day:
            day, day_start, day_pnl = d, bal, 0.0
        if pos:
            hit = manage(pos, bar, p)
            if hit:
                tr = close_trade(pos, hit[0], bar["t"], hit[1], p)
                trades.append(tr)
                bal += tr["pnl"]
                day_pnl += tr["pnl"]
                pos = None
        sig = signal_at(bars, ind, i, p)
        if sig:
            markers.append({"t": bar["t"], "side": sig})
            if pos and pos["side"] != sig and p["CloseAndReverse"]:
                tr = close_trade(pos, bar["c"], bar["t"], "inversion", p)
                trades.append(tr)
                bal += tr["pnl"]
                day_pnl += tr["pnl"]
                pos = None
            lk = lock_state(day_pnl, day_start, p)
            if lk:
                locked_days.add((d, lk))
            if pos is None and not lk:
                pos = open_trade(sig, bar, bal, p)
        open_pnl = pnl_usd(pos["side"], pos["entry"], bar["c"], pos["lots"], p) if pos else 0.0
        eq = bal + open_pnl
        peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)
        if i % 4 == 0 or i == len(bars) - 1:
            curve.append({"t": bar["t"], "equity": round(eq, 2)})
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gross_w, gross_l = sum(t["pnl"] for t in wins), -sum(t["pnl"] for t in losses)
    return {
        "params": p, "bars": len(bars),
        "from": bars[0]["t"] if bars else None, "to": bars[-1]["t"] if bars else None,
        "start_balance": balance, "end_balance": round(bal, 2),
        "net": round(bal - balance, 2), "net_pct": round((bal / balance - 1) * 100, 2) if balance else 0,
        "net_pips": round(sum(t["pips"] for t in trades), 1),
        "trades": len(trades), "wins": len(wins), "losses": len(losses),
        "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else 0,
        "profit_factor": round(gross_w / gross_l, 2) if gross_l else None,
        "avg_win": round(gross_w / len(wins), 2) if wins else 0,
        "avg_loss": round(-gross_l / len(losses), 2) if losses else 0,
        "max_drawdown": round(max_dd, 2), "max_drawdown_pct": round(max_dd / peak * 100, 2) if peak else 0,
        "locked_days": len(locked_days),
        "open_position": pos, "trade_list": trades[-200:][::-1], "curve": curve, "markers": markers[-400:],
    }


# ---------------------------------------------------------------- timeframes

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


# ---------------------------------------------------------------- MT5 CSV import

def parse_csv(text):
    """MetaTrader history export (tab or comma): <DATE> <TIME> <OPEN> <HIGH> <LOW> <CLOSE> ...
    or DATE,OPEN,HIGH,LOW,CLOSE with 'YYYY.MM.DD HH:MM'. Returns bars, oldest first."""
    bars = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line[0] in "<#" or line[:4].lower() in ("date", "time"):
            continue
        parts = [x for x in line.replace(";", ",").replace("\t", ",").split(",") if x != ""]
        if len(parts) < 5:
            parts = line.split()
        try:
            if ":" in parts[1]:
                stamp, nums = parts[0] + " " + parts[1], parts[2:6]
            else:
                stamp, nums = parts[0], parts[1:5]
            stamp = stamp.replace(".", "-").replace("/", "-").strip()
            dt = datetime.datetime.strptime(stamp[:16], "%Y-%m-%d %H:%M")
            o, h, l, c = (float(x) for x in nums)
        except (ValueError, IndexError):
            continue
        bars.append({"t": int(dt.replace(tzinfo=datetime.timezone.utc).timestamp()), "o": o, "h": h, "l": l, "c": c})
    bars.sort(key=lambda b: b["t"])
    return bars
