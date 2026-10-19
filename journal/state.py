"""Local SQLite state: every fill seen, what has been queued for the Sheet, and an outbox
that survives restarts and network outages. Kept in %LOCALAPPDATA% (not OneDrive) so two
PCs never sync each other's state file."""

import json
import sqlite3
import time
from pathlib import Path

from .parser import Fill

SCHEMA = """
CREATE TABLE IF NOT EXISTS fills (
    key TEXT PRIMARY KEY, firm TEXT, account TEXT, session_date TEXT, symbol TEXT,
    ts_ms INTEGER, price REAL, qty INTEGER, flag TEXT, position_id TEXT, close_id TEXT,
    first_flag TEXT, source_file TEXT, line_no INTEGER, sent INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS trades_sent (trade_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT, payload TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0, next_try REAL NOT NULL DEFAULT 0, last_error TEXT
);
"""

FILL_COLS = [
    "key", "firm", "account", "session_date", "symbol", "ts_ms", "price", "qty", "flag",
    "position_id", "close_id", "first_flag", "source_file", "line_no",
]


def fingerprint(trade):
    return json.dumps(trade.to_payload(), sort_keys=True)


class State:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.executescript(SCHEMA)

    def close(self):
        self.db.close()

    def add_fills(self, fills):
        """Insert fills not seen before; return the new ones."""
        new = []
        with self.db:
            for f in fills:
                cur = self.db.execute(
                    f"INSERT OR IGNORE INTO fills ({','.join(FILL_COLS)}) VALUES ({','.join('?' * len(FILL_COLS))})",
                    [getattr(f, c) for c in FILL_COLS],
                )
                if cur.rowcount:
                    new.append(f)
        return new

    def all_fills(self):
        rows = self.db.execute(f"SELECT {','.join(FILL_COLS)} FROM fills").fetchall()
        return [Fill(*r) for r in rows]

    def unsent_fills(self):
        rows = self.db.execute(f"SELECT {','.join(FILL_COLS)} FROM fills WHERE sent = 0 ORDER BY ts_ms").fetchall()
        return [Fill(*r) for r in rows]

    def changed_trades(self, trades):
        sent = dict(self.db.execute("SELECT trade_id, fingerprint FROM trades_sent").fetchall())
        return [t for t in trades if sent.get(t.trade_id) != fingerprint(t)]

    def queue(self, payloads, fills, trades):
        """Atomically put payloads in the outbox and mark their contents as queued."""
        with self.db:
            for p in payloads:
                self.db.execute("INSERT INTO outbox (payload) VALUES (?)", (json.dumps(p),))
            self.db.executemany("UPDATE fills SET sent = 1 WHERE key = ?", [(f.key,) for f in fills])
            self.db.executemany(
                "INSERT INTO trades_sent (trade_id, fingerprint) VALUES (?, ?) "
                "ON CONFLICT(trade_id) DO UPDATE SET fingerprint = excluded.fingerprint",
                [(t.trade_id, fingerprint(t)) for t in trades],
            )

    def next_outbox(self):
        """Oldest queued payload, or None. Strict FIFO so later trade updates win."""
        row = self.db.execute(
            "SELECT id, payload, attempts, next_try FROM outbox ORDER BY id LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return {"id": row[0], "payload": json.loads(row[1]), "attempts": row[2], "next_try": row[3]}

    def outbox_done(self, item_id):
        with self.db:
            self.db.execute("DELETE FROM outbox WHERE id = ?", (item_id,))

    def outbox_failed(self, item_id, attempts, error):
        delay = min(300, 5 * 2 ** min(attempts, 6))
        with self.db:
            self.db.execute(
                "UPDATE outbox SET attempts = ?, next_try = ?, last_error = ? WHERE id = ?",
                (attempts + 1, time.time() + delay, str(error)[:500], item_id),
            )

    def counts(self):
        q = lambda sql: self.db.execute(sql).fetchone()[0]
        return {
            "fills": q("SELECT COUNT(*) FROM fills"),
            "fills_unsent": q("SELECT COUNT(*) FROM fills WHERE sent = 0"),
            "trades_sent": q("SELECT COUNT(*) FROM trades_sent"),
            "outbox": q("SELECT COUNT(*) FROM outbox"),
            "outbox_error": (self.db.execute("SELECT last_error FROM outbox WHERE last_error IS NOT NULL ORDER BY id LIMIT 1").fetchone() or [None])[0],
            "last_fill_ts": q("SELECT MAX(ts_ms) FROM fills"),
        }
