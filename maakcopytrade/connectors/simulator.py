"""Paper-trading simulator: shared random-walk prices, futures (netting) and CFD (hedging).

With autotrade on, a small bot trades the account by itself, so a simulated
leader shows the whole copier working without any broker.
"""
import itertools
import math
import random
import threading
import time

from .. import instruments
from ..models import AccountInfo, ClosedTrade, Fill, Position
from ..stats import pair_fills_fifo
from .base import Connector, ConnectorError

_ids = itertools.count(1000)


class Market:
    def __init__(self, edition, seed=None):
        self.edition = edition
        self.prices = {}
        self.stamp = {}
        self.rng = random.Random(seed)
        self.lock = threading.Lock()
        self.frozen = False      # tests freeze prices

    def spec(self, symbol):
        """-> (price key, base price, tick, digits)."""
        if self.edition == "futures":
            pv, tick, base, key = instruments.future_spec(symbol)
            return key, base, tick, max(0, -int(math.floor(math.log10(tick)))) if tick < 1 else 0
        up = symbol.upper()
        for k in sorted(instruments.CFD, key=len, reverse=True):
            if up.startswith(k):
                size, base, digits = instruments.CFD[k]
                return k, base, 10 ** -digits, digits
        return up, 1.0, 0.00001, 5

    def price(self, symbol):
        key, base, tick, digits = self.spec(symbol)
        with self.lock:
            now = time.time()
            p = self.prices.get(key, base)
            if not self.frozen:
                dt_ = min(5.0, now - self.stamp.get(key, now))
                if dt_ > 0:
                    p *= math.exp(0.00025 * math.sqrt(dt_) * self.rng.gauss(0, 1))
            self.stamp[key] = now
            p = round(round(p / tick) * tick, digits)
            self.prices[key] = p
            return p

    def set_price(self, symbol, p):
        key = self.spec(symbol)[0]
        with self.lock:
            self.prices[key] = p
            self.stamp[key] = time.time()


MARKETS = {}


def market(edition):
    if edition not in MARKETS:
        MARKETS[edition] = Market(edition)
    return MARKETS[edition]


FUT_BOT = ["NQZ6", "ESZ6", "MNQZ6", "GCZ6", "CLZ6"]
CFD_BOT = ["EURUSD", "XAUUSD", "NAS100", "GBPUSD", "US30"]


class SimConnector(Connector):
    name = "sim"

    def __init__(self, params, edition):
        super().__init__(params, edition)
        self.market = market(edition)
        self.start_balance = float(self.params.get("balance") or (50000 if edition == "futures" else 100000))
        self.commission = float(self.params.get("commission") if self.params.get("commission") not in
                                (None, "") else (1.5 if edition == "futures" else 3.5))
        self.hedging = edition == "cfd" and str(self.params.get("hedging", "true")).lower() not in ("false", "0")
        self.suffix = self.params.get("symbol_suffix", "") or ""
        self.lock = threading.RLock()
        self.fills = []            # netting
        self.net = {}              # symbol -> [qty, avg]
        self.tickets = {}          # ticket -> Position (hedging)
        self.closed = []           # hedging closed trades
        self.realized = 0.0
        self.bot = None
        self.stop_evt = threading.Event()
        self.latency = float(self.params.get("latency_ms") or 0) / 1000.0

    # lifecycle ---------------------------------------------------------
    def connect(self):
        if str(self.params.get("autotrade", "")).lower() in ("true", "1", "on", "yes") and not self.bot:
            self.bot = threading.Thread(target=self._bot, daemon=True)
            self.bot.start()

    def close(self):
        self.stop_evt.set()

    # helpers -------------------------------------------------------------
    def _pv(self, symbol):
        return instruments.point_value(symbol)

    def _pnl(self, symbol, qty, entry, exit_):
        if self.edition == "futures":
            return (exit_ - entry) * qty * self._pv(symbol)
        return instruments.cfd_pnl(instruments.cfd_base(symbol, self.suffix), qty, entry, exit_)

    def _check_sltp(self):
        for t, p in list(self.tickets.items()):
            px = self.market.price(p.symbol)
            hit = None
            if p.qty > 0:
                if p.sl and px <= p.sl:
                    hit = p.sl
                elif p.tp and px >= p.tp:
                    hit = p.tp
            else:
                if p.sl and px >= p.sl:
                    hit = p.sl
                elif p.tp and px <= p.tp:
                    hit = p.tp
            if hit is not None:
                self._close_ticket(t, None, hit)

    # interface -------------------------------------------------------------
    def account(self):
        with self.lock:
            if self.hedging:
                self._check_sltp()
            unreal = sum(p.unrealized for p in self.positions())
            bal = self.start_balance + self.realized
            return AccountInfo(round(bal, 2), round(bal + unreal, 2), "USD")

    def positions(self):
        with self.lock:
            out = []
            if self.hedging:
                for p in self.tickets.values():
                    px = self.market.price(p.symbol)
                    p.unrealized = round(self._pnl(p.symbol, p.qty, p.avg_price, px), 2)
                    out.append(Position(**p.to_dict()))
            else:
                for sym, (q, avg) in self.net.items():
                    if q:
                        px = self.market.price(sym)
                        out.append(Position(sym, q, round(avg, 6), unrealized=round(self._pnl(sym, q, avg, px), 2)))
            return out

    def market_order(self, symbol, qty, sl=0.0, tp=0.0, comment=""):
        if not qty:
            raise ConnectorError("quantite nulle")
        if self.latency:
            time.sleep(self.latency)
        with self.lock:
            px = self.market.price(symbol)
            fee = self.commission * abs(qty)
            if self.hedging:
                t = str(next(_ids))
                self.tickets[t] = Position(symbol, qty, px, ticket=t, sl=float(sl or 0), tp=float(tp or 0))
                self.realized -= fee
                self.tickets[t].fee = fee        # opening commission, booked in the trade record at close
                self.tickets[t].opened = time.time()
                return t
            fid = str(next(_ids))
            self.fills.append(Fill(fid, symbol, qty, px, time.time(), fee))
            q, avg = self.net.get(symbol, [0.0, 0.0])
            newq = q + qty
            if q == 0 or (q > 0) == (qty > 0):
                avg = (avg * abs(q) + px * abs(qty)) / abs(newq)
            else:
                closed = min(abs(q), abs(qty))
                self.realized += self._pnl(symbol, closed * (1 if q > 0 else -1), avg, px)
                if (newq > 0) != (q > 0) and newq != 0:
                    avg = px
            self.realized -= fee
            self.net[symbol] = [round(newq, 8), avg if newq else 0.0]
            return fid

    def _close_ticket(self, ticket, volume, px=None):
        p = self.tickets.get(ticket)
        if not p:
            raise ConnectorError("ticket %s introuvable" % ticket)
        px = px if px is not None else self.market.price(p.symbol)
        vol = abs(p.qty) if volume is None else min(abs(volume), abs(p.qty))
        sign = 1 if p.qty > 0 else -1
        open_fee = getattr(p, "fee", 0.0) * vol / abs(p.qty)
        fee = self.commission * vol
        gross = self._pnl(p.symbol, vol * sign, p.avg_price, px)
        self.realized += gross - fee
        self.closed.append(ClosedTrade(
            id="%s-%d" % (ticket, len(self.closed)), symbol=p.symbol, side="long" if sign > 0 else "short",
            qty=round(vol, 2), entry_price=p.avg_price, exit_price=px, pnl=round(gross - fee - open_fee, 2),
            fees=round(fee + open_fee, 2), open_time=getattr(p, "opened", time.time()), close_time=time.time()))
        remaining = round(abs(p.qty) - vol, 8)
        if remaining <= 1e-9:
            del self.tickets[ticket]
        else:
            p.fee = getattr(p, "fee", 0.0) - open_fee
            p.qty = remaining * sign

    def close_ticket(self, ticket, volume=None):
        if self.latency:
            time.sleep(self.latency)
        with self.lock:
            self._close_ticket(str(ticket), volume)

    def modify_ticket(self, ticket, sl, tp):
        with self.lock:
            p = self.tickets.get(str(ticket))
            if not p:
                raise ConnectorError("ticket %s introuvable" % ticket)
            p.sl, p.tp = float(sl or 0), float(tp or 0)

    def closed_trades(self, since):
        with self.lock:
            if self.hedging:
                trades = list(self.closed)
            else:
                trades = pair_fills_fifo(self.fills, self._pv)
        return [t for t in trades if t.close_time > since]

    # demo bot ----------------------------------------------------------------
    def _bot(self):
        rng = random.Random()
        while not self.stop_evt.wait(rng.uniform(4, 11)):
            try:
                self._bot_step(rng)
            except Exception:
                pass

    def _bot_step(self, rng):
        pos = self.positions()
        if self.edition == "futures":
            if not pos or rng.random() < 0.25:
                if len(pos) < 2:
                    sym = rng.choice(FUT_BOT)
                    self.market_order(sym, rng.choice([1, 1, 2, 3]) * rng.choice([1, -1]), comment="bot")
                    return
            p = rng.choice(pos)
            r = rng.random()
            if r < 0.55:
                self.market_order(p.symbol, -p.qty, comment="bot")
            elif r < 0.8 and abs(p.qty) > 1:
                self.market_order(p.symbol, -1 if p.qty > 0 else 1, comment="bot")
            else:
                self.market_order(p.symbol, 1 if p.qty > 0 else -1, comment="bot")
            return
        if not pos or (len(pos) < 3 and rng.random() < 0.3):
            sym = rng.choice(CFD_BOT) + self.suffix
            side = rng.choice([1, -1])
            px = self.market.price(sym)
            dg = self.market.spec(sym)[3]
            dist = px * rng.uniform(0.0006, 0.0015)
            self.market_order(sym, round(rng.choice([0.1, 0.2, 0.5, 1.0]) * side, 2),
                              sl=round(px - side * dist, dg), tp=round(px + side * dist * 1.8, dg), comment="bot")
            return
        p = rng.choice(pos)
        r = rng.random()
        if r < 0.5:
            self.close_ticket(p.ticket)
        elif r < 0.75 and abs(p.qty) >= 0.2:
            self.close_ticket(p.ticket, round(abs(p.qty) / 2, 2))
        else:
            px = self.market.price(p.symbol)
            side = 1 if p.qty > 0 else -1
            self.modify_ticket(p.ticket, round(px - side * px * 0.0008, self.market.spec(p.symbol)[3]), p.tp)
