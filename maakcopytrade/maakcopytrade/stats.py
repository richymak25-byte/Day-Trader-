"""Trading statistics: everything the dashboard shows is computed here."""
import datetime as dt
import math
from collections import OrderedDict, defaultdict, deque

from .models import ClosedTrade

WEEKDAYS = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]


def pair_fills_fifo(fills, point_value_fn):
    """Rebuild round-trip trades from executions, first in first out.

    `fills` are models.Fill (qty signed). Returns a list of ClosedTrade, one per
    matched chunk, so a 3-lot entry closed in two exits gives two trades.
    """
    open_lots = defaultdict(deque)   # symbol -> deque of [qty_signed, price, ts, fee_per_unit, fill_id]
    trades = []
    for f in sorted(fills, key=lambda x: (x.ts, x.id)):
        qty = f.qty
        fee_unit = (f.fee / abs(f.qty)) if f.qty else 0.0
        lots = open_lots[f.symbol]
        while qty and lots and (lots[0][0] > 0) != (qty > 0):
            lot = lots[0]
            matched = min(abs(qty), abs(lot[0]))
            side = 1 if lot[0] > 0 else -1
            pv = point_value_fn(f.symbol)
            gross = (f.price - lot[1]) * matched * side * pv
            fees = (lot[3] + fee_unit) * matched
            trades.append(ClosedTrade(
                id="%s-%s-%g" % (lot[4], f.id, matched), symbol=f.symbol,
                side="long" if side > 0 else "short", qty=matched, entry_price=lot[1],
                exit_price=f.price, pnl=round(gross - fees, 2), fees=round(fees, 2),
                open_time=lot[2], close_time=f.ts))
            lot[0] -= matched * side
            qty += matched * side
            if abs(lot[0]) < 1e-9:
                lots.popleft()
        if abs(qty) > 1e-9:
            lots.append([qty, f.price, f.ts, fee_unit, f.id])
    return trades


def _safe_div(a, b):
    return a / b if b else 0.0


def _streaks(pnls):
    best_w = best_l = cur_w = cur_l = 0
    for p in pnls:
        if p > 0:
            cur_w += 1
            cur_l = 0
        elif p < 0:
            cur_l += 1
            cur_w = 0
        best_w, best_l = max(best_w, cur_w), max(best_l, cur_l)
    return best_w, best_l


def _group(trades, keyfn, order=None):
    g = OrderedDict()
    if order:
        for k in order:
            g[k] = []
    for t in trades:
        g.setdefault(keyfn(t), []).append(t)
    out = []
    for k, ts in g.items():
        pnl = sum(t["pnl"] for t in ts)
        wins = sum(1 for t in ts if t["pnl"] > 0)
        out.append({"key": k, "trades": len(ts), "pnl": round(pnl, 2),
                    "win_rate": round(100.0 * _safe_div(wins, len(ts)), 1)})
    return out


def compute(trades, start_balance=0.0, tz=None):
    """`trades` are dicts with the ClosedTrade fields. Returns one big dict."""
    trades = sorted(trades, key=lambda t: t["close_time"])
    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    n = len(pnls)
    gross_profit, gross_loss = sum(wins), -sum(losses)
    net = sum(pnls)
    win_rate = _safe_div(len(wins), n)
    avg_win = _safe_div(gross_profit, len(wins))
    avg_loss = _safe_div(gross_loss, len(losses))

    def day_of(t):
        return dt.datetime.fromtimestamp(t["close_time"], tz).date().isoformat()

    # daily
    days = OrderedDict()
    for t in trades:
        d = days.setdefault(day_of(t), {"date": day_of(t), "pnl": 0.0, "trades": 0, "wins": 0})
        d["pnl"] += t["pnl"]
        d["trades"] += 1
        d["wins"] += 1 if t["pnl"] > 0 else 0
    daily = []
    for d in days.values():
        d["pnl"] = round(d["pnl"], 2)
        d["win_rate"] = round(100.0 * _safe_div(d.pop("wins"), d["trades"]), 1)
        daily.append(d)
    day_pnls = [d["pnl"] for d in daily]
    green = sum(1 for p in day_pnls if p > 0)
    best_day = max(day_pnls) if day_pnls else 0.0
    worst_day = min(day_pnls) if day_pnls else 0.0
    positive_total = sum(p for p in day_pnls if p > 0)
    consistency = round(100.0 * _safe_div(best_day, positive_total), 1) if best_day > 0 else None

    # equity curve and drawdown (trade by trade)
    eq = start_balance
    peak = start_balance
    max_dd = max_dd_pct = 0.0
    curve = [{"t": trades[0]["open_time"], "equity": round(eq, 2)}] if trades else []
    for t in trades:
        eq += t["pnl"]
        peak = max(peak, eq)
        dd = peak - eq
        if dd > max_dd:
            max_dd = dd
            max_dd_pct = 100.0 * _safe_div(dd, peak)
        curve.append({"t": t["close_time"], "equity": round(eq, 2), "pnl": t["pnl"], "symbol": t["symbol"]})

    # risk-adjusted (daily)
    sharpe = sortino = 0.0
    if len(day_pnls) > 1:
        mean = sum(day_pnls) / len(day_pnls)
        sd = math.sqrt(sum((p - mean) ** 2 for p in day_pnls) / (len(day_pnls) - 1))
        downside = [min(p, 0) for p in day_pnls]
        dsd = math.sqrt(sum(p * p for p in downside) / (len(day_pnls) - 1))
        sharpe = _safe_div(mean, sd) * math.sqrt(252)
        sortino = _safe_div(mean, dsd) * math.sqrt(252)

    best_w, best_l = _streaks(pnls)
    durations = [max(0.0, t["close_time"] - t["open_time"]) for t in trades]
    longs = [t for t in trades if t["side"] == "long"]
    shorts = [t for t in trades if t["side"] == "short"]

    def side_stats(ts):
        w = sum(1 for t in ts if t["pnl"] > 0)
        return {"trades": len(ts), "pnl": round(sum(t["pnl"] for t in ts), 2),
                "win_rate": round(100.0 * _safe_div(w, len(ts)), 1)}

    def hour_of(t):
        return "%02dh" % dt.datetime.fromtimestamp(t["open_time"], tz).hour

    def wd_of(t):
        return WEEKDAYS[dt.datetime.fromtimestamp(t["close_time"], tz).weekday()]

    by_symbol = sorted(_group(trades, lambda t: t["symbol"]), key=lambda r: -r["pnl"])
    by_hour = sorted(_group(trades, hour_of), key=lambda r: r["key"])
    by_weekday = [r for r in _group(trades, wd_of, WEEKDAYS) if r["trades"] or r["key"] in WEEKDAYS[:5]]

    return {
        "summary": {
            "net_pnl": round(net, 2), "trades": n, "wins": len(wins), "losses": len(losses),
            "breakeven": n - len(wins) - len(losses),
            "win_rate": round(100.0 * win_rate, 1),
            "profit_factor": round(_safe_div(gross_profit, gross_loss), 2) if gross_loss else None,
            "gross_profit": round(gross_profit, 2), "gross_loss": round(gross_loss, 2),
            "avg_win": round(avg_win, 2), "avg_loss": round(avg_loss, 2),
            "payoff": round(_safe_div(avg_win, avg_loss), 2) if avg_loss else None,
            "expectancy": round(_safe_div(net, n), 2),
            "largest_win": round(max(wins), 2) if wins else 0.0,
            "largest_loss": round(min(losses), 2) if losses else 0.0,
            "max_consec_wins": best_w, "max_consec_losses": best_l,
            "avg_duration_s": round(_safe_div(sum(durations), n), 1),
            "fees": round(sum(t.get("fees", 0.0) for t in trades), 2),
            "max_drawdown": round(max_dd, 2), "max_drawdown_pct": round(max_dd_pct, 2),
            "recovery_factor": round(_safe_div(net, max_dd), 2) if max_dd else None,
            "sharpe": round(sharpe, 2), "sortino": round(sortino, 2),
            "trading_days": len(daily), "green_days": green,
            "green_days_pct": round(100.0 * _safe_div(green, len(daily)), 1),
            "best_day": round(best_day, 2), "worst_day": round(worst_day, 2),
            "avg_day": round(_safe_div(sum(day_pnls), len(day_pnls)), 2),
            "consistency_pct": consistency,
            "volume": round(sum(t["qty"] for t in trades), 2),
        },
        "long": side_stats(longs), "short": side_stats(shorts),
        "daily": daily, "equity": curve,
        "by_symbol": by_symbol, "by_hour": by_hour, "by_weekday": by_weekday,
    }
