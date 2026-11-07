"""Plain data objects shared by the engine, the connectors and the stats."""
from dataclasses import dataclass, asdict


@dataclass
class Position:
    symbol: str
    qty: float              # signed: > 0 long, < 0 short (contracts or lots)
    avg_price: float = 0.0
    ticket: str = ""        # CFD position ticket, "" for a net (futures) position
    sl: float = 0.0
    tp: float = 0.0
    unrealized: float = 0.0

    def to_dict(self):
        return asdict(self)


@dataclass
class AccountInfo:
    balance: float
    equity: float
    currency: str = "USD"

    def to_dict(self):
        return asdict(self)


@dataclass
class ClosedTrade:
    id: str
    symbol: str
    side: str               # "long" or "short"
    qty: float
    entry_price: float
    exit_price: float
    pnl: float              # net of fees
    fees: float
    open_time: float        # epoch seconds
    close_time: float

    def to_dict(self):
        return asdict(self)


@dataclass
class Fill:
    """One execution, used to rebuild round-trip trades (FIFO)."""
    id: str
    symbol: str
    qty: float              # signed: buy > 0, sell < 0
    price: float
    ts: float
    fee: float = 0.0
