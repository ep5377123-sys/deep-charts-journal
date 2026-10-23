"""The watch loop: read changed .fills files, rebuild trades, queue changes, deliver them."""

import logging
import time

from . import __version__
from .config import existing_watch_dirs
from .parser import parse_file, scan_dirs
from .sheets import SheetsError
from .trades import build_trades, local_date

log = logging.getLogger("journal")

OUTDATED_RECEIVER = "Sheet receiver is outdated (no Replay tab): redeploy apps_script/Code.gs, see README"


def fill_payload(f, trade_id):
    return {
        "key": f.key, "firm": f.firm, "account": f.account, "symbol": f.symbol,
        "session_date": f.session_date, "ts_ms": f.ts_ms, "price": f.price, "qty": f.qty,
        "flag": f.flag, "position_id": f.position_id, "close_id": f.close_id,
        "first_flag": f.first_flag, "source_file": f.source_file, "trade_id": trade_id,
    }


def journaled(trades, cfg):
    """Trades opened at or after journal_since_ms. A trade opened earlier stays out even if it
    closes later, so a cutoff never produces a half trade. Replay trades carry historical
    times, so they are cut by the day the replay ran instead."""
    since = cfg.get("journal_since_ms") or 0
    since_day = local_date(since) if since else ""
    return [t for t in trades if (t.replayed_on >= since_day if t.replayed_on else t.entry_ts >= since)]


def chunks(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


class Syncer:
    def __init__(self, cfg, state, client, pc):
        self.cfg, self.state, self.client, self.pc = cfg, state, client, pc
        self.file_sigs = {}
        self.bad_lines = {}
        self.warnings = set()
        self.open_trades = 0
        self.trade_count = 0

    def scan(self):
        """Read new/changed files; return the number of fills not seen before."""
        dirs = existing_watch_dirs(self.cfg)
        new = 0
        for path in scan_dirs(dirs):
            try:
                st = path.stat()
            except OSError:
                continue
            sig = (st.st_size, st.st_mtime_ns)
            if self.file_sigs.get(path) == sig:
                continue
            try:
                info, fills, bad = parse_file(path)
            except OSError as e:  # locked by Deep Charts or OneDrive; retry next poll
                log.debug("cannot read %s: %s", path.name, e)
                continue
            self.file_sigs[path] = sig
            if bad:
                self.bad_lines[path.name] = len(bad)
                log.warning("%s: %d unparseable line(s), first: %r", path.name, len(bad), bad[0])
            if not self.cfg["include_sim"] and info.firm.upper() == "SIM":
                continue
            added = self.state.add_fills(fills)
            if added:
                log.info("%s: %d new fill(s)", path.name, len(added))
            new += len(added)
        return new

    def stage(self):
        """Rebuild all trades from all known fills and queue whatever changed."""
        trades, owner = build_trades(self.state.all_fills(), self.cfg)
        trades = journaled(trades, self.cfg)
        kept = {t.trade_id for t in trades}
        self.trade_count = len(trades)
        self.open_trades = sum(t.status == "Open" for t in trades)
        self.warnings = {w for t in trades for w in t.warnings}
        if self.bad_lines:
            self.warnings.add(f"format changed? unparseable lines in {len(self.bad_lines)} file(s)")
        fills, skipped = [], []
        for f in self.state.unsent_fills():
            (fills if owner.get(f.key) in kept else skipped).append(f)
        if skipped:  # belong to trades opened before journal_since_ms; never send them
            self.state.queue([], skipped, [])
        changed = self.state.changed_trades(trades)
        if not fills and not changed:
            return 0, 0
        size = self.cfg["batch_size"]
        payloads = [{"fills": [fill_payload(f, owner.get(f.key, "")) for f in c], "trades": []} for c in chunks(fills, size)]
        live = [t for t in changed if not t.replayed_on]
        replay = [t for t in changed if t.replayed_on]
        payloads += [{"fills": [], "trades": [t.to_payload() for t in c]} for c in chunks(live, size)]
        payloads += [{"fills": [], "replay_trades": [t.to_payload() for t in c]} for c in chunks(replay, size)]
        self.state.queue(payloads, fills, changed)
        log.info("queued %d fill(s), %d trade update(s)", len(fills), len(changed))
        return len(fills), len(changed)

    def flush(self):
        """Deliver queued payloads oldest-first; stop at the first failure. Returns delivered count."""
        sent = 0
        while True:
            item = self.state.next_outbox()
            if item is None or item["next_try"] > time.time():
                return sent
            try:
                resp = self.client.post(item["payload"])
                # An old receiver ignores replay_trades but still answers ok; keep them queued instead.
                if "replay_trades" in item["payload"] and "replay_trades_upserted" not in resp:
                    raise SheetsError(OUTDATED_RECEIVER)
            except SheetsError as e:
                self.state.outbox_failed(item["id"], item["attempts"], e)
                log.warning("delivery failed (attempt %d): %s", item["attempts"] + 1, e)
                return sent
            self.state.outbox_done(item["id"])
            sent += 1
            log.info("delivered: %s", {k: v for k, v in resp.items() if k != "ok"})

    def heartbeat(self):
        c = self.state.counts()
        body = {"heartbeat": {
            "pc": self.pc, "version": __version__, "last_fill_ts": c["last_fill_ts"],
            "files": len(self.file_sigs), "fills": c["fills"], "trades": self.trade_count,
            "open_trades": self.open_trades, "outbox": c["outbox"],
            "warnings": sorted(self.warnings) + ([f"delivery error: {c['outbox_error']}"] if c["outbox_error"] else []),
        }}
        try:
            self.client.post(body)
        except SheetsError as e:
            log.warning("heartbeat failed: %s", e)

    def once(self):
        self.scan()
        self.stage()
        return self.flush()

    def run_forever(self):
        log.info("watching %s", [str(d) for d in existing_watch_dirs(self.cfg)])
        last_beat = 0
        first = True
        while True:
            try:
                # Always stage on the first cycle: picks up anything left from a crash or a config change.
                if self.scan() or first:
                    self.stage()
                    first = False
                self.flush()
                if time.time() - last_beat >= self.cfg["heartbeat_seconds"]:
                    self.heartbeat()
                    last_beat = time.time()
            except Exception:  # never let one bad cycle kill the background app
                log.exception("sync cycle failed")
            time.sleep(self.cfg["poll_seconds"])
