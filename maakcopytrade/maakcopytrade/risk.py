"""Prop firm style risk rules, evaluated on every account.

A breach flattens the follower and locks copying:
  * daily_loss:   unlocks by itself at the next session roll
  * max_drawdown: stays locked until you unlock it by hand
  * profit_target (when stop_on_target): stays locked until unlocked
  * flatten_time: flattens and blocks new entries until the roll
"""
from . import clock

DEFAULT_RULES = {
    "start_balance": 0.0,
    "daily_loss_limit": 0.0,      # 0 = off
    "max_drawdown": 0.0,          # 0 = off
    "drawdown_type": "static",    # static | trailing_intraday | trailing_eod
    "lock_offset": 0.0,           # trailing floor stops rising at start + offset
    "profit_target": 0.0,
    "stop_on_target": False,
    "safety_buffer": 0.0,         # act this many dollars before the real limit
    "max_qty": 0.0,               # contracts or lots per symbol, 0 = off
    "flatten_time": "",           # "15:55" in the session time zone, "" = off
}


def rules_from(settings):
    r = dict(DEFAULT_RULES)
    r.update({k: v for k, v in (settings or {}).get("risk", {}).items() if k in DEFAULT_RULES})
    for k in ("start_balance", "daily_loss_limit", "max_drawdown", "lock_offset", "profit_target",
              "safety_buffer", "max_qty"):
        try:
            r[k] = float(r[k] or 0)
        except (TypeError, ValueError):
            r[k] = 0.0
    r["stop_on_target"] = bool(r["stop_on_target"])
    return r


class RiskTracker:
    """Keeps per-account state (day start balance, peaks, locks) and evaluates the rules."""

    def __init__(self, tz=None, roll="00:00"):
        self.tz = tz
        self.roll = roll

    def fresh_state(self, balance, rules):
        start = rules["start_balance"] or balance
        return {"start": start, "day": None, "day_start": balance, "peak_equity": max(balance, start),
                "peak_eod": max(balance, start), "lock": None, "lock_reason": ""}

    def evaluate(self, state, rules, balance, equity, ts):
        """Updates `state` in place. Returns (metrics, new_breach or None)."""
        start = rules["start_balance"] or state.setdefault("start", balance)
        day = clock.trading_day(ts, self.tz, self.roll)
        if state.get("day") != day:
            if state.get("day") is not None:
                state["peak_eod"] = max(state.get("peak_eod", start), balance)
            state["day"] = day
            state["day_start"] = balance
            if state.get("lock") in ("daily_loss", "flatten_time"):
                state["lock"], state["lock_reason"] = None, ""
        state["peak_equity"] = max(state.get("peak_equity", start), equity)

        buf = rules["safety_buffer"]
        daily_pnl = equity - state["day_start"]
        floor = None
        if rules["max_drawdown"] > 0:
            dd = rules["max_drawdown"]
            kind = rules["drawdown_type"]
            if kind == "trailing_intraday":
                peak = state["peak_equity"]
            elif kind == "trailing_eod":
                peak = state["peak_eod"]
            else:
                peak = start
            floor = peak - dd
            if kind != "static":
                floor = min(floor, start + rules["lock_offset"])
                floor = max(floor, start - dd)
        metrics = {
            "day": day,
            "day_start": round(state["day_start"], 2),
            "daily_pnl": round(daily_pnl, 2),
            "dll_left": round(rules["daily_loss_limit"] + daily_pnl, 2) if rules["daily_loss_limit"] > 0 else None,
            "floor": round(floor, 2) if floor is not None else None,
            "dd_left": round(equity - floor, 2) if floor is not None else None,
            "profit": round(balance - start, 2),
            "target_pct": round(100.0 * (balance - start) / rules["profit_target"], 1)
            if rules["profit_target"] > 0 else None,
            "lock": state.get("lock"),
            "lock_reason": state.get("lock_reason", ""),
        }
        if state.get("lock"):
            return metrics, None

        breach = None
        if floor is not None and equity <= floor + buf:
            breach = ("max_drawdown", "Drawdown max atteint: equity %.2f <= plancher %.2f" % (equity, floor))
        elif rules["daily_loss_limit"] > 0 and daily_pnl <= -(rules["daily_loss_limit"] - buf):
            breach = ("daily_loss", "Perte journaliere %.2f >= limite %.2f" % (-daily_pnl, rules["daily_loss_limit"]))
        elif rules["profit_target"] > 0 and rules["stop_on_target"] and balance - start >= rules["profit_target"]:
            breach = ("profit_target", "Objectif de profit atteint (%.2f)" % (balance - start))
        elif rules["flatten_time"] and clock.past_time(ts, self.tz, rules["flatten_time"], self.roll):
            breach = ("flatten_time", "Heure de cloture %s atteinte" % rules["flatten_time"])
        if breach:
            state["lock"], state["lock_reason"] = breach
            metrics["lock"], metrics["lock_reason"] = breach
        return metrics, breach
