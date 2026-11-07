"""Trading-day arithmetic in the exchange time zone (stdlib only)."""
import datetime as dt
import time

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None


def get_tz(name):
    if not name or ZoneInfo is None:
        return None
    try:
        return ZoneInfo(name)
    except Exception:  # Windows without the tzdata package: fall back to local time
        return None


def local(ts, tz):
    return dt.datetime.fromtimestamp(ts, tz) if tz else dt.datetime.fromtimestamp(ts)


def hhmm(text):
    h, m = (text or "00:00").split(":")
    return int(h), int(m)


def trading_day(ts, tz, roll="00:00"):
    """Date of the trading session that `ts` belongs to.

    With roll "18:00" (CME futures), Sunday 18:00 onward already counts as Monday.
    """
    d = local(ts, tz)
    h, m = hhmm(roll)
    if (h, m) != (0, 0) and (d.hour, d.minute) >= (h, m):
        d = d + dt.timedelta(days=1)
    return d.date().isoformat()


def past_time(ts, tz, at, roll="00:00"):
    """True when the local clock is at/after `at` and before the next session roll."""
    if not at:
        return False
    d = local(ts, tz)
    now = (d.hour, d.minute)
    a, r = hhmm(at), hhmm(roll)
    if r == (0, 0):
        return now >= a
    if a < r:
        return a <= now < r
    return now >= a or now < r


def now():
    return time.time()
