"""MetaTrader 5 connector (CFD / forex), through the official MetaTrader5 package.

The package talks to ONE terminal per Python process, so every MT5 account
runs in its own worker process, bound to its own terminal installation
(terminal_path). Windows only, like the MetaTrader5 package itself.
"""
import multiprocessing as mp
import threading

from ..models import AccountInfo, ClosedTrade, Position
from .base import Connector, ConnectorError

MAGIC = 26110701


def _worker(conn, cfg):  # runs in the child process
    try:
        import MetaTrader5 as mt5
    except ImportError:
        conn.send(("err", "Package MetaTrader5 absent: pip install MetaTrader5 (Windows)"))
        return
    import datetime as dt
    import time as _t

    kw = {}
    if cfg.get("terminal_path"):
        kw["path"] = cfg["terminal_path"]
    if cfg.get("login"):
        kw.update(login=int(cfg["login"]), password=cfg.get("password", ""), server=cfg.get("server", ""))
    if not mt5.initialize(**kw):
        conn.send(("err", "initialize MT5 a echoue: %s" % (mt5.last_error(),)))
        return
    conn.send(("ok", None))

    def filling(sym):
        info = mt5.symbol_info(sym)
        fm = info.filling_mode if info else 0
        if fm & 2:
            return mt5.ORDER_FILLING_IOC
        if fm & 1:
            return mt5.ORDER_FILLING_FOK
        return mt5.ORDER_FILLING_RETURN

    def deal(sym, qty, position=None, sl=0.0, tp=0.0, comment=""):
        mt5.symbol_select(sym, True)
        tick = mt5.symbol_info_tick(sym)
        if tick is None:
            raise RuntimeError("symbole %s indisponible" % sym)
        req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": sym, "volume": float(abs(qty)),
               "type": mt5.ORDER_TYPE_BUY if qty > 0 else mt5.ORDER_TYPE_SELL,
               "price": tick.ask if qty > 0 else tick.bid, "deviation": int(cfg.get("deviation") or 20),
               "magic": MAGIC, "comment": (comment or "copytrade")[:31],
               "type_time": mt5.ORDER_TIME_GTC, "type_filling": filling(sym)}
        if position:
            req["position"] = int(position)
        if sl:
            req["sl"] = float(sl)
        if tp:
            req["tp"] = float(tp)
        r = mt5.order_send(req)
        if r is None or r.retcode not in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED):
            raise RuntimeError("ordre refuse: %s" % ((r.retcode, r.comment) if r else mt5.last_error(),))
        return str(r.order)

    while True:
        try:
            cmd, args = conn.recv()
        except EOFError:
            break
        try:
            if cmd == "quit":
                break
            if cmd == "account":
                a = mt5.account_info()
                res = (a.balance, a.equity, a.currency, a.margin_mode)
            elif cmd == "positions":
                res = [(p.symbol, p.volume * (1 if p.type == 0 else -1), p.price_open, str(p.ticket), p.sl, p.tp,
                        p.profit + p.swap) for p in (mt5.positions_get() or [])]
            elif cmd == "order":
                res = deal(*args)
            elif cmd == "close":
                ticket, volume = args
                ps = mt5.positions_get(ticket=int(ticket))
                if not ps:
                    raise RuntimeError("position %s introuvable" % ticket)
                p = ps[0]
                vol = p.volume if volume is None else min(volume, p.volume)
                res = deal(p.symbol, -vol if p.type == 0 else vol, position=p.ticket, comment="copytrade close")
            elif cmd == "modify":
                ticket, sl, tp = args
                ps = mt5.positions_get(ticket=int(ticket))
                if not ps:
                    raise RuntimeError("position %s introuvable" % ticket)
                r = mt5.order_send({"action": mt5.TRADE_ACTION_SLTP, "position": int(ticket),
                                    "symbol": ps[0].symbol, "sl": float(sl or 0), "tp": float(tp or 0)})
                if r is None or r.retcode != mt5.TRADE_RETCODE_DONE:
                    raise RuntimeError("modification refusee: %s" % ((r.retcode, r.comment) if r else "?",))
                res = True
            elif cmd == "history":
                since = args
                frm = dt.datetime.fromtimestamp(max(since - 86400 * 3, _t.time() - 86400 * 90))
                to = dt.datetime.fromtimestamp(_t.time() + 86400 * 2)
                deals = mt5.history_deals_get(frm, to) or []
                opens = {d.position_id: d for d in deals if d.entry == 0}
                res = []
                for d in deals:
                    if d.entry not in (1, 3):
                        continue
                    o = opens.get(d.position_id)
                    res.append((str(d.ticket), d.symbol, "long" if d.type == 1 else "short", d.volume,
                                o.price if o else 0.0, d.price, d.profit + d.commission + d.swap + d.fee
                                + (o.commission if o else 0.0), -(d.commission + d.fee + (o.commission if o else 0)),
                                float(o.time) if o else float(d.time), float(d.time)))
            else:
                raise RuntimeError("commande inconnue %s" % cmd)
            conn.send(("ok", res))
        except Exception as e:  # report and keep serving
            conn.send(("err", str(e)))
    mt5.shutdown()


class MT5Connector(Connector):
    name = "mt5"

    def __init__(self, params, edition):
        super().__init__(params, edition)
        self.proc = None
        self.conn = None
        self.lock = threading.Lock()
        self.hedging = True

    def connect(self):
        ctx = mp.get_context("spawn")
        parent, child = ctx.Pipe()
        self.proc = ctx.Process(target=_worker, args=(child, dict(self.params)), daemon=True)
        self.proc.start()
        self.conn = parent
        if not parent.poll(60):
            raise ConnectorError("Le terminal MT5 ne repond pas")
        status, msg = parent.recv()
        if status != "ok":
            raise ConnectorError(msg)
        acct = self._rpc("account")
        self.hedging = acct[3] == 2  # ACCOUNT_MARGIN_MODE_RETAIL_HEDGING

    def _rpc(self, cmd, args=None, timeout=30):
        with self.lock:
            if not self.conn:
                raise ConnectorError("MT5 non connecte")
            self.conn.send((cmd, args))
            if not self.conn.poll(timeout):
                raise ConnectorError("MT5: delai depasse pour %s" % cmd)
            status, res = self.conn.recv()
        if status != "ok":
            raise ConnectorError(res)
        return res

    def close(self):
        try:
            if self.conn:
                self.conn.send(("quit", None))
        except Exception:
            pass
        if self.proc:
            self.proc.join(2)

    def account(self):
        b, e, cur, _ = self._rpc("account")
        return AccountInfo(b, e, cur)

    def positions(self):
        return [Position(s, q, px, ticket=t, sl=sl, tp=tp, unrealized=pl)
                for s, q, px, t, sl, tp, pl in self._rpc("positions")]

    def market_order(self, symbol, qty, sl=0.0, tp=0.0, comment=""):
        return self._rpc("order", (symbol, qty, None, sl, tp, comment))

    def close_ticket(self, ticket, volume=None):
        return self._rpc("close", (ticket, volume))

    def modify_ticket(self, ticket, sl, tp):
        return self._rpc("modify", (ticket, sl, tp))

    def closed_trades(self, since):
        return [ClosedTrade(*row) for row in self._rpc("history", since) if row[9] > since]
