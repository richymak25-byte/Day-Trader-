"""Contract specs, symbol helpers and prop firm presets.

Prop firm numbers change often: the presets are a starting point, always
check them against your own firm's current rules.
"""
import re

# root: (point value in USD, tick size, simulator base price, price key)
FUTURES = {
    "ES": (50.0, 0.25, 6000.0, "ES"), "MES": (5.0, 0.25, 6000.0, "ES"),
    "NQ": (20.0, 0.25, 21000.0, "NQ"), "MNQ": (2.0, 0.25, 21000.0, "NQ"),
    "YM": (5.0, 1.0, 44000.0, "YM"), "MYM": (0.5, 1.0, 44000.0, "YM"),
    "RTY": (50.0, 0.1, 2300.0, "RTY"), "M2K": (5.0, 0.1, 2300.0, "RTY"),
    "CL": (1000.0, 0.01, 70.0, "CL"), "MCL": (100.0, 0.01, 70.0, "CL"),
    "GC": (100.0, 0.1, 2700.0, "GC"), "MGC": (10.0, 0.1, 2700.0, "GC"),
    "SI": (5000.0, 0.005, 31.0, "SI"), "NG": (10000.0, 0.001, 3.2, "NG"),
    "6E": (125000.0, 0.00005, 1.08, "6E"), "M6E": (12500.0, 0.0001, 1.08, "6E"),
    "ZB": (1000.0, 0.03125, 118.0, "ZB"), "ZN": (1000.0, 0.015625, 110.0, "ZN"),
}

_MONTH = re.compile(r"^[FGHJKMNQUVXZ]\d{1,2}$")


def split_future(symbol):
    """'NQZ6' -> ('NQ', 'Z6'); 'MES' -> ('MES', ''). Unknown roots fall back to a regex."""
    s = symbol.upper().strip()
    for root in sorted(FUTURES, key=len, reverse=True):
        if s.startswith(root):
            rest = s[len(root):]
            if rest == "" or _MONTH.match(rest):
                return root, rest
    m = re.match(r"^([A-Z0-9]+?)([FGHJKMNQUVXZ]\d{1,2})$", s)
    if m:
        return m.group(1), m.group(2)
    return s, ""


def future_spec(symbol):
    root, _ = split_future(symbol)
    return FUTURES.get(root, (1.0, 0.01, 100.0, root))


def point_value(symbol):
    return future_spec(symbol)[0]


# CFD: (contract size per lot, simulator base price, digits)
CFD = {
    "EURUSD": (100000, 1.0800, 5), "GBPUSD": (100000, 1.2700, 5), "USDJPY": (100000, 150.00, 3),
    "AUDUSD": (100000, 0.6600, 5), "USDCAD": (100000, 1.3800, 5), "USDCHF": (100000, 0.8800, 5),
    "XAUUSD": (100, 2700.00, 2), "XAGUSD": (5000, 31.00, 3),
    "US30": (1, 44000.0, 1), "NAS100": (1, 21000.0, 1), "SPX500": (1, 6000.0, 1),
    "GER40": (1, 19500.0, 1), "USOIL": (100, 70.00, 2), "BTCUSD": (1, 90000.0, 2),
}


def cfd_base(symbol, suffix=""):
    s = symbol
    if suffix and s.endswith(suffix):
        s = s[: -len(suffix)]
    return s


def cfd_spec(symbol):
    base = symbol.upper()
    for k in sorted(CFD, key=len, reverse=True):
        if base.startswith(k):
            return CFD[k]
    return (100000, 1.0, 5)


def cfd_pnl(symbol, qty, entry, exit_):
    """USD profit for a signed lot quantity (simulator + estimates)."""
    size = cfd_spec(symbol)[0]
    raw = (exit_ - entry) * qty * size
    base = symbol.upper()[:6]
    if len(base) == 6 and base.startswith("USD") and base[3:] != "USD" and exit_:
        raw = raw / exit_          # quote currency is not USD, e.g. USDJPY
    return raw


# Risk presets. Amounts in account currency. drawdown_type:
#   static | trailing_intraday | trailing_eod
FUTURES_PRESETS = {
    "custom": {"label": "Personnalise", "start_balance": 50000, "daily_loss_limit": 0, "max_drawdown": 0,
               "drawdown_type": "static", "lock_offset": 0, "profit_target": 0, "max_qty": 0},
    "topstep_50k": {"label": "Topstep 50K", "start_balance": 50000, "daily_loss_limit": 1000,
                    "max_drawdown": 2000, "drawdown_type": "trailing_eod", "lock_offset": 0,
                    "profit_target": 3000, "max_qty": 5},
    "topstep_100k": {"label": "Topstep 100K", "start_balance": 100000, "daily_loss_limit": 2000,
                     "max_drawdown": 3000, "drawdown_type": "trailing_eod", "lock_offset": 0,
                     "profit_target": 6000, "max_qty": 10},
    "topstep_150k": {"label": "Topstep 150K", "start_balance": 150000, "daily_loss_limit": 3000,
                     "max_drawdown": 4500, "drawdown_type": "trailing_eod", "lock_offset": 0,
                     "profit_target": 9000, "max_qty": 15},
    "apex_50k": {"label": "Apex 50K", "start_balance": 50000, "daily_loss_limit": 0,
                 "max_drawdown": 2500, "drawdown_type": "trailing_intraday", "lock_offset": 100,
                 "profit_target": 3000, "max_qty": 10},
    "apex_100k": {"label": "Apex 100K", "start_balance": 100000, "daily_loss_limit": 0,
                  "max_drawdown": 3000, "drawdown_type": "trailing_intraday", "lock_offset": 100,
                  "profit_target": 6000, "max_qty": 14},
    "mffu_50k": {"label": "MyFundedFutures 50K", "start_balance": 50000, "daily_loss_limit": 0,
                 "max_drawdown": 2000, "drawdown_type": "trailing_eod", "lock_offset": 0,
                 "profit_target": 3000, "max_qty": 5},
    "tpt_50k": {"label": "Take Profit Trader 50K", "start_balance": 50000, "daily_loss_limit": 0,
                "max_drawdown": 2000, "drawdown_type": "trailing_eod", "lock_offset": 0,
                "profit_target": 3000, "max_qty": 6},
}

CFD_PRESETS = {
    "custom": {"label": "Personnalise", "start_balance": 100000, "daily_loss_limit": 0, "max_drawdown": 0,
               "drawdown_type": "static", "lock_offset": 0, "profit_target": 0, "max_qty": 0},
    "ftmo_10k": {"label": "FTMO 10K (5% / 10%)", "start_balance": 10000, "daily_loss_limit": 500,
                 "max_drawdown": 1000, "drawdown_type": "static", "lock_offset": 0,
                 "profit_target": 1000, "max_qty": 0},
    "ftmo_100k": {"label": "FTMO 100K (5% / 10%)", "start_balance": 100000, "daily_loss_limit": 5000,
                  "max_drawdown": 10000, "drawdown_type": "static", "lock_offset": 0,
                  "profit_target": 10000, "max_qty": 0},
    "fundednext_100k": {"label": "FundedNext 100K (5% / 10%)", "start_balance": 100000,
                        "daily_loss_limit": 5000, "max_drawdown": 10000, "drawdown_type": "static",
                        "lock_offset": 0, "profit_target": 8000, "max_qty": 0},
    "the5ers_100k": {"label": "The5ers 100K (5% / 6% trailing)", "start_balance": 100000,
                     "daily_loss_limit": 5000, "max_drawdown": 6000, "drawdown_type": "trailing_eod",
                     "lock_offset": 0, "profit_target": 8000, "max_qty": 0},
}
