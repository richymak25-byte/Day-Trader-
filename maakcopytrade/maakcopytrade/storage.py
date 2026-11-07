"""SQLite persistence (one database file per edition)."""
import json
import os
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    role TEXT NOT NULL,              -- leader | follower
    leader_id INTEGER,
    connector TEXT NOT NULL,
    params TEXT NOT NULL DEFAULT '{}',
    settings TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1,
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS trades (
    account_id INTEGER NOT NULL, trade_id TEXT NOT NULL, symbol TEXT, side TEXT, qty REAL,
    entry_price REAL, exit_price REAL, pnl REAL, fees REAL, open_time REAL, close_time REAL,
    PRIMARY KEY (account_id, trade_id)
);
CREATE INDEX IF NOT EXISTS trades_time ON trades (close_time);
CREATE TABLE IF NOT EXISTS equity (account_id INTEGER, ts REAL, balance REAL, equity REAL);
CREATE INDEX IF NOT EXISTS equity_acct ON equity (account_id, ts);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, level TEXT, account_id INTEGER, message TEXT
);
CREATE TABLE IF NOT EXISTS executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, leader_id INTEGER, follower_id INTEGER,
    symbol TEXT, action TEXT, qty REAL, status TEXT, latency_ms REAL, message TEXT
);
CREATE TABLE IF NOT EXISTS risk_state (account_id INTEGER PRIMARY KEY, data TEXT);
CREATE TABLE IF NOT EXISTS ticket_map (
    follower_id INTEGER, leader_ticket TEXT, follower_tickets TEXT,
    PRIMARY KEY (follower_id, leader_ticket)
);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
"""

SECRET_KEYS = ("password", "sec", "api_key", "secret")
MASK = "********"


def mask_params(params):
    return {k: (MASK if any(s in k for s in SECRET_KEYS) and v else v) for k, v in params.items()}


class Store:
    def __init__(self, path):
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.db.executescript(SCHEMA)
            self.db.commit()

    def _q(self, sql, args=(), commit=False):
        with self.lock:
            cur = self.db.execute(sql, args)
            if commit:
                self.db.commit()
            return cur

    # accounts -----------------------------------------------------------
    def _acct(self, row):
        d = dict(row)
        d["params"] = json.loads(d["params"] or "{}")
        d["settings"] = json.loads(d["settings"] or "{}")
        d["enabled"] = bool(d["enabled"])
        return d

    def accounts(self):
        return [self._acct(r) for r in self._q("SELECT * FROM accounts ORDER BY role, id")]

    def account(self, acct_id):
        r = self._q("SELECT * FROM accounts WHERE id=?", (acct_id,)).fetchone()
        return self._acct(r) if r else None

    def add_account(self, name, role, connector, params, settings, leader_id=None, enabled=True):
        cur = self._q("INSERT INTO accounts (name, role, leader_id, connector, params, settings, enabled, created)"
                      " VALUES (?,?,?,?,?,?,?,?)",
                      (name, role, leader_id, connector, json.dumps(params), json.dumps(settings),
                       int(enabled), time.time()), commit=True)
        return cur.lastrowid

    def update_account(self, acct_id, **fields):
        old = self.account(acct_id)
        if not old:
            return None
        if "params" in fields:   # keep stored secrets when the UI sends the mask back
            merged = dict(fields["params"])
            for k, v in merged.items():
                if v == MASK:
                    merged[k] = old["params"].get(k, "")
            fields["params"] = merged
        sets, args = [], []
        for k in ("name", "role", "leader_id", "connector", "params", "settings", "enabled"):
            if k in fields:
                v = fields[k]
                if k in ("params", "settings"):
                    v = json.dumps(v)
                if k == "enabled":
                    v = int(bool(v))
                sets.append("%s=?" % k)
                args.append(v)
        if sets:
            self._q("UPDATE accounts SET %s WHERE id=?" % ", ".join(sets), args + [acct_id], commit=True)
        return self.account(acct_id)

    def delete_account(self, acct_id):
        for sql in ("DELETE FROM accounts WHERE id=?", "DELETE FROM risk_state WHERE account_id=?",
                    "DELETE FROM ticket_map WHERE follower_id=?"):
            self._q(sql, (acct_id,))
        self._q("UPDATE accounts SET leader_id=NULL WHERE leader_id=?", (acct_id,), commit=True)

    # trades ---------------------------------------------------------------
    def upsert_trades(self, acct_id, trades):
        with self.lock:
            for t in trades:
                self.db.execute(
                    "INSERT OR REPLACE INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (acct_id, t.id, t.symbol, t.side, t.qty, t.entry_price, t.exit_price, t.pnl,
                     t.fees, t.open_time, t.close_time))
            self.db.commit()

    def trades(self, acct_id=None, since=0.0, limit=None):
        sql = "SELECT * FROM trades WHERE close_time >= ?"
        args = [since]
        if acct_id is not None:
            sql += " AND account_id = ?"
            args.append(acct_id)
        sql += " ORDER BY close_time DESC"
        if limit:
            sql += " LIMIT %d" % int(limit)
        return [dict(r) for r in self._q(sql, args)]

    def last_trade_time(self, acct_id):
        r = self._q("SELECT MAX(close_time) FROM trades WHERE account_id=?", (acct_id,)).fetchone()
        return r[0] or 0.0

    # equity / events / executions -----------------------------------------
    def add_equity(self, acct_id, ts, balance, equity):
        self._q("INSERT INTO equity VALUES (?,?,?,?)", (acct_id, ts, balance, equity), commit=True)

    def equity(self, acct_id, since=0.0):
        return [dict(r) for r in self._q(
            "SELECT ts, balance, equity FROM equity WHERE account_id=? AND ts>=? ORDER BY ts",
            (acct_id, since))]

    def event(self, level, message, acct_id=None):
        self._q("INSERT INTO events (ts, level, account_id, message) VALUES (?,?,?,?)",
                (time.time(), level, acct_id, message), commit=True)

    def events(self, limit=200):
        return [dict(r) for r in self._q("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))]

    def execution(self, leader_id, follower_id, symbol, action, qty, status, latency_ms, message=""):
        self._q("INSERT INTO executions (ts, leader_id, follower_id, symbol, action, qty, status, latency_ms,"
                " message) VALUES (?,?,?,?,?,?,?,?,?)",
                (time.time(), leader_id, follower_id, symbol, action, qty, status, latency_ms, message),
                commit=True)

    def executions(self, limit=200):
        return [dict(r) for r in self._q("SELECT * FROM executions ORDER BY id DESC LIMIT ?", (limit,))]

    # risk state / ticket map / kv -------------------------------------------
    def risk_state(self, acct_id):
        r = self._q("SELECT data FROM risk_state WHERE account_id=?", (acct_id,)).fetchone()
        return json.loads(r[0]) if r else None

    def save_risk_state(self, acct_id, data):
        self._q("INSERT OR REPLACE INTO risk_state VALUES (?,?)", (acct_id, json.dumps(data)), commit=True)

    def ticket_map(self, follower_id):
        return {r[0]: json.loads(r[1]) for r in self._q(
            "SELECT leader_ticket, follower_tickets FROM ticket_map WHERE follower_id=?", (follower_id,))}

    def set_ticket(self, follower_id, leader_ticket, follower_tickets):
        if follower_tickets:
            self._q("INSERT OR REPLACE INTO ticket_map VALUES (?,?,?)",
                    (follower_id, leader_ticket, json.dumps(follower_tickets)), commit=True)
        else:
            self._q("DELETE FROM ticket_map WHERE follower_id=? AND leader_ticket=?",
                    (follower_id, leader_ticket), commit=True)

    def get(self, key, default=None):
        r = self._q("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(r[0]) if r else default

    def put(self, key, value):
        self._q("INSERT OR REPLACE INTO kv VALUES (?,?)", (key, json.dumps(value)), commit=True)
