"""The copy engine.

Futures (net mode): when the leader's net position on a contract changes, each
follower is brought to round(leader_qty * multiplier) on its mapped contract.
Only leader CHANGES trigger orders, so a follower you close by hand stays flat
until the leader moves again.

CFD (ticket mode): every leader ticket is mirrored. New ticket -> open,
volume down -> proportional partial close, ticket gone -> close, SL/TP moved ->
SL/TP moved on the follower. The leader->follower ticket map is stored in
SQLite, so a restart does not duplicate anything.
"""
import math
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

from . import connectors, instruments
from .risk import RiskTracker, rules_from

FUTURES_DEFAULTS = {"multiplier": 1.0, "invert": False, "symbol_map": {}, "max_qty": 0}
CFD_DEFAULTS = {"sizing": "multiplier", "multiplier": 1.0, "fixed_lot": 0.01, "min_lot": 0.01, "lot_step": 0.01,
                "max_lot": 0, "symbol_suffix": "", "symbol_map": {}, "copy_sltp": True, "invert": False}


def parse_map(value):
    """'NQ=MNQ, ES=MES' or a dict -> dict."""
    if isinstance(value, dict):
        return {str(k).strip().upper(): str(v).strip().upper() for k, v in value.items() if k}
    out = {}
    for part in (value or "").replace(";", ",").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip():
                out[k.strip().upper()] = v.strip().upper()
    return out


def _round_half_away(x):
    return int(math.floor(abs(x) + 0.5)) * (1 if x >= 0 else -1)


def floor_step(x, step):
    if step <= 0:
        return x
    n = math.floor(x / step + 1e-9)
    digits = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0
    return round(n * step, digits)


class Runtime:
    def __init__(self, acct):
        self.acct = acct
        self.conn = None
        self.connected = False
        self.error = ""
        self.info = None
        self.positions = []
        self.metrics = {}
        self.risk_state = None
        self.last_retry = 0.0
        self.last_trades_sync = 0.0
        self.last_equity = 0.0

    @property
    def id(self):
        return self.acct["id"]

    @property
    def settings(self):
        return self.acct.get("settings") or {}


class Engine:
    def __init__(self, edition, store, tz=None, roll="00:00", poll=0.25):
        self.edition = edition
        self.store = store
        self.tz = tz
        self.roll = roll
        self.poll = poll
        self.risk = RiskTracker(tz, roll)
        self.rt = {}                    # account id -> Runtime
        self.lock = threading.RLock()
        self.copying = bool(store.get("copying", False))
        self.leader_state = {}          # leader id -> {symbol: qty} or {ticket: Position}
        self.tmaps = {}                 # follower id -> {leader_ticket: [[ticket, symbol, qty], ...]}
        self.pool = ThreadPoolExecutor(max_workers=16)
        self.stop_evt = threading.Event()
        self.thread = None
        self.latencies = []

    # ------------------------------------------------------------------ setup
    def defaults(self):
        return dict(FUTURES_DEFAULTS if self.edition == "futures" else CFD_DEFAULTS)

    def load(self):
        for acct in self.store.accounts():
            self._add_runtime(acct)

    def _add_runtime(self, acct):
        r = Runtime(acct)
        r.risk_state = self.store.risk_state(acct["id"])
        with self.lock:
            self.rt[acct["id"]] = r
        self.tmaps[acct["id"]] = self.store.ticket_map(acct["id"])
        self.pool.submit(self._connect, r)
        return r

    def reload_account(self, acct_id):
        acct = self.store.account(acct_id)
        old = self.rt.pop(acct_id, None)
        if old and old.conn:
            try:
                old.conn.close()
            except Exception:
                pass
        self.leader_state.pop(acct_id, None)
        if acct:
            r = self._add_runtime(acct)
            if old:
                r.risk_state = old.risk_state

    def remove_account(self, acct_id):
        old = self.rt.pop(acct_id, None)
        if old and old.conn:
            old.conn.close()
        self.leader_state.pop(acct_id, None)
        self.tmaps.pop(acct_id, None)

    def _connect(self, r):
        r.last_retry = time.time()
        try:
            conn = connectors.make(r.acct["connector"], r.acct["params"], self.edition)
            conn.connect()
            r.conn = conn
            r.connected = True
            r.error = ""
            self.store.event("info", "Connecte (%s)" % r.acct["connector"], r.id)
        except Exception as e:
            r.connected = False
            r.error = str(e)
            self.store.event("error", "Connexion impossible: %s" % e, r.id)

    def start(self):
        if not self.thread:
            self.thread = threading.Thread(target=self._loop, daemon=True)
            self.thread.start()

    def shutdown(self):
        self.stop_evt.set()
        for r in list(self.rt.values()):
            if r.conn:
                try:
                    r.conn.close()
                except Exception:
                    pass

    def set_copying(self, on):
        self.copying = bool(on)
        self.store.put("copying", self.copying)
        if on:
            self.leader_state.clear()   # re-baseline: never copy positions opened while stopped
        self.store.event("info", "Copie %s" % ("demarree" if on else "arretee"))

    # ------------------------------------------------------------------- loop
    def _loop(self):
        while not self.stop_evt.is_set():
            t0 = time.time()
            try:
                self.tick()
            except Exception:
                self.store.event("error", "Erreur moteur: %s" % traceback.format_exc(limit=3))
            self.stop_evt.wait(max(0.02, self.poll - (time.time() - t0)))

    def tick(self, now=None):
        now = now or time.time()
        runtimes = list(self.rt.values())
        for r in runtimes:
            if not r.connected and now - r.last_retry > 15 and r.conn is None:
                self.pool.submit(self._connect, r)
                r.last_retry = now
        list(self.pool.map(lambda r: self._refresh(r, now), [r for r in runtimes if r.connected]))
        if self.copying:
            for r in runtimes:
                if r.acct["role"] == "leader" and r.connected and r.acct["enabled"]:
                    self._copy_from(r, now)
        for r in runtimes:
            if r.connected and now - r.last_trades_sync > 20:
                r.last_trades_sync = now
                self.pool.submit(self._sync_trades, r)
            if r.connected and r.info and now - r.last_equity > 60:
                r.last_equity = now
                self.store.add_equity(r.id, now, r.info.balance, r.info.equity)

    def _refresh(self, r, now):
        try:
            r.info = r.conn.account()
            r.positions = r.conn.positions()
        except Exception as e:
            r.error = str(e)
            self.store.event("error", "Lecture du compte: %s" % e, r.id)
            return
        r.error = ""
        rules = rules_from(r.settings)
        if r.risk_state is None:
            r.risk_state = self.risk.fresh_state(r.info.balance, rules)
        before = dict(r.risk_state)
        r.metrics, breach = self.risk.evaluate(r.risk_state, rules, r.info.balance, r.info.equity, now)
        if r.risk_state != before:
            self.store.save_risk_state(r.id, r.risk_state)
        if breach:
            code, msg = breach
            if r.acct["role"] == "follower":
                self.store.event("risk", "%s. Compte aplati et copie bloquee." % msg, r.id)
                self._flatten(r)
            else:
                self.store.event("risk", "Compte maitre: %s (aucune action sur le maitre)" % msg, r.id)

    def _sync_trades(self, r):
        try:
            since = max(0.0, self.store.last_trade_time(r.id) - 3600)
            trades = r.conn.closed_trades(since)
            if trades:
                self.store.upsert_trades(r.id, trades)
        except Exception as e:
            self.store.event("warn", "Historique des trades: %s" % e, r.id)

    # ------------------------------------------------------------- following
    def followers(self, leader_id):
        return [r for r in self.rt.values()
                if r.acct["role"] == "follower" and r.acct.get("leader_id") == leader_id]

    def _blocked(self, f):
        if not f.acct["enabled"]:
            return "copie desactivee sur ce compte"
        if not f.connected:
            return "compte deconnecte"
        if f.risk_state and f.risk_state.get("lock"):
            return "bloque par le risque (%s)" % f.risk_state["lock"]
        return ""

    def _copy_from(self, leader, now):
        if self.edition == "futures":
            self._copy_net(leader, now)
        else:
            self._copy_tickets(leader, now)

    # futures -------------------------------------------------------------------
    def map_future(self, symbol, settings):
        root, month = instruments.split_future(symbol)
        mapped = parse_map(settings.get("symbol_map")).get(root, root)
        return mapped + month

    def _copy_net(self, leader, now):
        cur = {}
        for p in leader.positions:
            cur[p.symbol] = cur.get(p.symbol, 0) + p.qty
        prev = self.leader_state.get(leader.id)
        self.leader_state[leader.id] = cur
        if prev is None:
            if not self.store.get("sync_on_start", False):
                return
            prev = {}
        changes = [(s, cur.get(s, 0)) for s in set(prev) | set(cur) if prev.get(s, 0) != cur.get(s, 0)]
        if not changes:
            return
        for sym, q in changes:
            self.store.event("info", "%s: %s -> %g" % (leader.acct["name"], sym, q), leader.id)
        jobs = [self.pool.submit(self._follow_net, leader, f, changes, now) for f in self.followers(leader.id)]
        for j in jobs:
            j.result()

    def target_qty(self, leader_qty, settings):
        mult = float(settings.get("multiplier") or 1)
        target = _round_half_away(leader_qty * mult)
        if settings.get("invert"):
            target = -target
        caps = [float(settings.get("max_qty") or 0), float((settings.get("risk") or {}).get("max_qty") or 0)]
        caps = [c for c in caps if c > 0]
        if caps:
            cap = int(min(caps))
            target = max(-cap, min(cap, target))
        return target

    def _follow_net(self, leader, f, changes, t0):
        blocked = self._blocked(f)
        try:
            fpos = {p.symbol: p.qty for p in f.conn.positions()} if f.connected else {}
        except Exception as e:
            fpos = {}
            blocked = blocked or "lecture positions: %s" % e
        for sym, lq in changes:
            fsym = self.map_future(sym, f.settings)
            target = self.target_qty(lq, f.settings)
            current = fpos.get(fsym, 0)
            delta = target - current
            if not delta:
                continue
            reducing = abs(target) < abs(current) and (target == 0 or (target > 0) == (current > 0))
            if blocked and not reducing:
                self.store.execution(leader.id, f.id, fsym, "BUY" if delta > 0 else "SELL", abs(delta), "skipped",
                                     0, blocked)
                continue
            try:
                f.conn.market_order(fsym, delta, comment="copy %s" % leader.acct["name"])
                lat = (time.time() - t0) * 1000
                self._lat(lat)
                fpos[fsym] = target
                self.store.execution(leader.id, f.id, fsym, "BUY" if delta > 0 else "SELL", abs(delta), "filled",
                                     round(lat, 1), "%g -> %g" % (current, target))
            except Exception as e:
                self.store.execution(leader.id, f.id, fsym, "BUY" if delta > 0 else "SELL", abs(delta), "error",
                                     0, str(e))
                self.store.event("error", "Copie %s: %s" % (fsym, e), f.id)

    # cfd -------------------------------------------------------------------------
    def map_cfd(self, symbol, leader_settings, settings):
        base = instruments.cfd_base(symbol, leader_settings.get("symbol_suffix", "") or "")
        mapped = parse_map(settings.get("symbol_map")).get(base.upper(), base)
        return mapped + (settings.get("symbol_suffix") or "")

    def lot_size(self, leader_lots, settings, leader_eq, follower_eq):
        mode = settings.get("sizing", "multiplier")
        mult = float(settings.get("multiplier") or 1)
        if mode == "fixed":
            lots = float(settings.get("fixed_lot") or 0.01)
        elif mode == "equity":
            lots = leader_lots * mult * ((follower_eq / leader_eq) if leader_eq else 1.0)
        else:
            lots = leader_lots * mult
        lots = floor_step(lots, float(settings.get("lot_step") or 0.01))
        caps = [float(settings.get("max_lot") or 0), float((settings.get("risk") or {}).get("max_qty") or 0)]
        caps = [c for c in caps if c > 0]
        if caps:
            lots = min(lots, min(caps))
        if lots < float(settings.get("min_lot") or 0.01) - 1e-9:
            return 0.0
        return lots

    def _copy_tickets(self, leader, now):
        cur = {p.ticket: p for p in leader.positions}
        prev = self.leader_state.get(leader.id)
        self.leader_state[leader.id] = cur
        if prev is None:
            if not self.store.get("sync_on_start", False):
                return
            prev = {}
        ops = []
        for t, p in cur.items():
            if t not in prev:
                ops.append(("open", t, p, None))
            else:
                o = prev[t]
                if abs(p.qty) < abs(o.qty) - 1e-9:
                    ops.append(("reduce", t, p, o))
                elif abs(p.qty) > abs(o.qty) + 1e-9:
                    ops.append(("add", t, p, o))
                if (p.sl, p.tp) != (o.sl, o.tp):
                    ops.append(("sltp", t, p, o))
        for t, o in prev.items():
            if t not in cur:
                ops.append(("close", t, None, o))
        if not ops:
            return
        for op, t, p, o in ops:
            sym = (p or o).symbol
            self.store.event("info", "%s: %s ticket %s (%s)" % (leader.acct["name"], op, t, sym), leader.id)
        jobs = [self.pool.submit(self._follow_tickets, leader, f, ops, now) for f in self.followers(leader.id)]
        for j in jobs:
            j.result()

    def _follow_tickets(self, leader, f, ops, t0):
        blocked = self._blocked(f)
        tmap = self.tmaps.setdefault(f.id, {})
        s = f.settings
        invert = bool(s.get("invert"))
        for op, lt, p, o in ops:
            sym = self.map_cfd((p or o).symbol, leader.settings, s)
            entries = tmap.get(lt, [])
            try:
                if op in ("open", "add"):
                    vol = abs(p.qty) - (abs(o.qty) if o else 0)
                    lots = self.lot_size(vol, s, leader.info.equity if leader.info else 0,
                                         f.info.equity if f.info else 0)
                    side = (1 if p.qty > 0 else -1) * (-1 if invert else 1)
                    if blocked:
                        self.store.execution(leader.id, f.id, sym, "BUY" if side > 0 else "SELL", lots, "skipped",
                                             0, blocked)
                        continue
                    if lots <= 0:
                        self.store.execution(leader.id, f.id, sym, "BUY" if side > 0 else "SELL", 0, "skipped", 0,
                                             "lot calcule sous le minimum")
                        continue
                    sl, tp = (p.sl, p.tp) if s.get("copy_sltp", True) else (0.0, 0.0)
                    if invert:
                        sl, tp = tp, sl
                    ticket = f.conn.market_order(sym, side * lots, sl, tp, comment="copy %s" % lt)
                    entries = entries + [[str(ticket), sym, side * lots]]
                    self._done(leader, f, sym, side, lots, t0, "%s ticket %s" % (op, lt))
                elif op == "close":
                    for tk, esym, q in entries:
                        if self._close_entry(f, tk, esym, q, None):
                            self._done(leader, f, esym, -1 if q > 0 else 1, abs(q), t0, "close ticket %s" % lt)
                        else:
                            self.store.execution(leader.id, f.id, esym, "CLOSE", abs(q), "skipped", 0,
                                                 "deja fermee sur le copieur (SL/TP ou manuel)")
                    entries = []
                elif op == "reduce":
                    ratio = (abs(o.qty) - abs(p.qty)) / abs(o.qty)
                    total = sum(abs(e[2]) for e in entries)
                    to_close = floor_step(total * ratio, float(s.get("lot_step") or 0.01))
                    if total - to_close < float(s.get("min_lot") or 0.01) - 1e-9:
                        to_close = total
                    left = to_close
                    for e in reversed(list(entries)):
                        if left <= 1e-9:
                            break
                        part = min(abs(e[2]), left)
                        if not self._close_entry(f, e[0], e[1], e[2], part if part < abs(e[2]) - 1e-9 else None):
                            entries.remove(e)     # already gone on the follower
                            continue
                        self._done(leader, f, e[1], -1 if e[2] > 0 else 1, part, t0, "partial ticket %s" % lt)
                        left = round(left - part, 8)
                        rem = round(abs(e[2]) - part, 8)
                        if rem <= 1e-9:
                            entries.remove(e)
                        else:
                            e[2] = rem * (1 if e[2] > 0 else -1)
                elif op == "sltp" and s.get("copy_sltp", True) and f.conn.hedging:
                    sl, tp = (p.tp, p.sl) if invert else (p.sl, p.tp)
                    for e in entries:
                        f.conn.modify_ticket(e[0], sl, tp)
                    if entries:
                        self.store.execution(leader.id, f.id, sym, "MODIFY", 0, "filled",
                                             round((time.time() - t0) * 1000, 1), "SL %g / TP %g" % (sl, tp))
            except Exception as e:
                self.store.execution(leader.id, f.id, sym, op.upper(), 0, "error", 0, str(e))
                self.store.event("error", "Copie %s %s: %s" % (op, sym, e), f.id)
            tmap[lt] = entries
            self.store.set_ticket(f.id, lt, entries)
            if not entries:
                tmap.pop(lt, None)

    def _close_entry(self, f, ticket, sym, qty, volume):
        """Returns False when the follower ticket was already gone (its own SL/TP, or closed by hand)."""
        if f.conn.hedging:
            if ticket not in {p.ticket for p in f.conn.positions()}:
                return False
            f.conn.close_ticket(ticket, volume)
        else:
            vol = abs(qty) if volume is None else volume
            f.conn.market_order(sym, -vol if qty > 0 else vol, comment="copy close")
        return True

    def _done(self, leader, f, sym, side, qty, t0, msg):
        lat = (time.time() - t0) * 1000
        self._lat(lat)
        self.store.execution(leader.id, f.id, sym, "BUY" if side > 0 else "SELL", qty, "filled", round(lat, 1), msg)

    def _lat(self, ms):
        self.latencies = (self.latencies + [ms])[-200:]

    # --------------------------------------------------------------- actions
    def _flatten(self, r):
        try:
            r.conn.flatten()
            self.tmaps[r.id] = {}
            for lt in list(self.store.ticket_map(r.id)):
                self.store.set_ticket(r.id, lt, [])
            self.store.event("warn", "Compte aplati", r.id)
        except Exception as e:
            self.store.event("error", "Aplatissement impossible: %s" % e, r.id)

    def flatten(self, acct_id):
        r = self.rt.get(acct_id)
        if r and r.connected:
            self._flatten(r)

    def flatten_all(self, include_leaders=False):
        for r in list(self.rt.values()):
            if r.connected and (include_leaders or r.acct["role"] == "follower"):
                self._flatten(r)

    def unlock(self, acct_id):
        r = self.rt.get(acct_id)
        if r and r.risk_state:
            r.risk_state["lock"], r.risk_state["lock_reason"] = None, ""
            self.store.save_risk_state(acct_id, r.risk_state)
            self.store.event("info", "Verrou de risque leve manuellement", acct_id)

    def reset_risk(self, acct_id):
        r = self.rt.get(acct_id)
        if r:
            r.risk_state = None
            self.store.save_risk_state(acct_id, {})
            self.store.event("info", "Etat de risque reinitialise", acct_id)

    # ------------------------------------------------------------- snapshot
    def snapshot(self):
        accts = []
        for r in sorted(self.rt.values(), key=lambda x: (x.acct["role"] != "leader", x.id)):
            a = r.acct
            accts.append({
                "id": r.id, "name": a["name"], "role": a["role"], "leader_id": a.get("leader_id"),
                "connector": a["connector"], "enabled": a["enabled"], "settings": a["settings"],
                "connected": r.connected, "error": r.error,
                "hedging": bool(r.conn and r.conn.hedging),
                "info": r.info.to_dict() if r.info else None,
                "positions": [p.to_dict() for p in r.positions],
                "risk": r.metrics,
            })
        lat = sorted(self.latencies)
        return {
            "edition": self.edition, "copying": self.copying, "accounts": accts,
            "latency": {"avg": round(sum(lat) / len(lat), 1) if lat else None,
                        "p95": round(lat[int(len(lat) * 0.95) - 1], 1) if len(lat) >= 20 else None,
                        "count": len(lat)},
            "sync_on_start": bool(self.store.get("sync_on_start", False)),
            "ts": time.time(),
        }
