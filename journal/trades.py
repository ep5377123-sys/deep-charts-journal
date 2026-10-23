"""Group fills into trades.

A trade runs flat -> flat per (firm, account, symbol), in time order. Deep Charts' position id
is not used for grouping: each scale-in order gets its own id, and a platform restart can give
the exit a new id.

Replay (backtest) fills land in the file for the day the replay ran but carry the replayed
market time, so their date is earlier than the file's. They become "Replay" trades, grouped per
replay day so the same bars replayed twice never merge.
"""

from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime


@dataclass
class Trade:
    trade_id: str
    firm: str
    account: str
    account_type: str
    symbol: str
    session_date: str
    side: str
    max_qty: int
    entry_ts: int
    entry_price: float
    exit_ts: int | None
    exit_price: float | None
    duration_s: float | None
    points: float | None
    gross: float | None
    fees: float
    net: float | None
    win: int | None
    n_fills: int
    status: str
    replayed_on: str = ""  # file date of a replay trade; "" for live trades
    fill_keys: list = field(default_factory=list)
    position_ids: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def to_payload(self):
        d = asdict(self)
        d.pop("fill_keys")
        if not self.replayed_on:  # keeps live payloads, and so their fingerprints, unchanged
            d.pop("replayed_on")
        d["position_ids"] = ",".join(self.position_ids)
        return d


def account_type(firm):
    return "SIM" if firm.strip().upper() == "SIM" else "Prop"


def local_date(ts_ms):
    return datetime.fromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d")


def is_replay(f):
    return local_date(f.ts_ms) < f.session_date


def fee_per_side(cfg, firm, root):
    fees = cfg.get("fees_per_side", {})
    for table in (fees.get(firm, {}), fees.get("default", {})):
        if root in table:
            return table[root]
    return None


def _vwap(legs):
    qty = sum(abs(q) for _, q in legs)
    if not qty:
        return None, 0
    return round(sum(abs(q) * f.price for f, q in legs) / qty, 6), qty


def _make_trade(legs, cfg, closed):
    first = legs[0][0]
    root = first.root
    direction = 1 if legs[0][1] > 0 else -1
    entries = [(f, q) for f, q in legs if (q > 0) == (direction > 0)]
    exits = [(f, q) for f, q in legs if (q > 0) != (direction > 0)]
    entry_price, _ = _vwap(entries)
    exit_price, _ = _vwap(exits)

    running = max_qty = 0
    for _, q in legs:
        running += q
        max_qty = max(max_qty, abs(running))

    warnings = []
    mult = cfg["multipliers"].get(root)
    if mult is None:
        warnings.append(f"multiplier missing: {root}")
    fee = fee_per_side(cfg, first.firm, root)
    if fee is None:
        warnings.append(f"fees not configured: {first.firm} {root}")
        fee = 0
    fees = round(sum(abs(q) for _, q in legs) * fee, 2)

    gross = points = net = win = None
    exit_ts = exits[-1][0].ts_ms if exits else None
    if closed:
        points = round((exit_price - entry_price) * direction, 6)
        if mult is not None:
            gross = round(sum(-q * f.price for f, q in legs) * mult, 2)
            net = round(gross - fees, 2)
            win = 1 if net > 0 else 0

    keys, pids = [], []
    for f, _ in legs:
        if f.key not in keys:
            keys.append(f.key)
        if f.position_id not in pids:
            pids.append(f.position_id)

    trade_id = f"{first.firm}|{first.account}|{first.symbol}|{first.ts_ms}"
    replayed_on = ""
    if is_replay(first):
        replayed_on = first.session_date
        trade_id += f"|replay {replayed_on}"

    return Trade(
        trade_id=trade_id,
        firm=first.firm, account=first.account,
        account_type="Replay" if replayed_on else account_type(first.firm),
        symbol=first.symbol, session_date=local_date(first.ts_ms) if replayed_on else first.session_date,
        side="Long" if direction > 0 else "Short", max_qty=max_qty,
        entry_ts=first.ts_ms, entry_price=entry_price,
        exit_ts=exit_ts if closed else None, exit_price=exit_price if closed else None,
        duration_s=(exit_ts - first.ts_ms) / 1000 if closed else None,
        points=points, gross=gross, fees=fees, net=net, win=win,
        n_fills=len(keys), status="Closed" if closed else "Open", replayed_on=replayed_on,
        fill_keys=keys, position_ids=pids, warnings=warnings,
    )


def build_trades(fills, cfg):
    """Return (trades, {fill_key: trade_id}). Fills must already be unique by key."""
    groups = defaultdict(list)
    for f in fills:
        groups[(f.firm, f.account, f.symbol, f.session_date if is_replay(f) else "")].append(f)

    trades = []
    for group in groups.values():
        group.sort(key=lambda f: (f.ts_ms, f.session_date, f.source_file, f.line_no))
        net, legs = 0, []
        for f in group:
            q = f.qty
            if net and net + q and (net > 0) != (net + q > 0):
                # One fill reverses the position: it closes this trade and opens the next.
                legs.append((f, -net))
                trades.append(_make_trade(legs, cfg, closed=True))
                q += net
                net, legs = 0, []
            legs.append((f, q))
            net += q
            if net == 0:
                trades.append(_make_trade(legs, cfg, closed=True))
                legs = []
        if legs:
            trades.append(_make_trade(legs, cfg, closed=False))

    trades.sort(key=lambda t: (t.entry_ts, t.trade_id))
    owner = {}
    for t in trades:
        for k in t.fill_keys:
            owner.setdefault(k, t.trade_id)
    return trades, owner
