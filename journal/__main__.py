"""Command line: python -m journal <init|dry-run|start-fresh|ping|backfill|run|status>"""

import argparse
import logging
import logging.handlers
import os
import socket
import sys
import time
from collections import defaultdict
from datetime import datetime

from . import __version__
from .config import (APP_DIR, CONFIG_PATH, DB_PATH, LOG_PATH, existing_watch_dirs, load_config, set_value,
                     write_template)
from .parser import parse_file, scan_dirs
from .sheets import SheetsClient, SheetsError
from .state import State
from .sync import Syncer, journaled
from .trades import build_trades


def setup_logging(verbose=False):
    APP_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("journal")
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh = logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if sys.stderr is not None:  # None under pythonw.exe
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)


def pc_name():
    return os.environ.get("COMPUTERNAME") or socket.gethostname()


def make_client(cfg):
    return SheetsClient(cfg["webapp_url"], cfg["token"], pc_name(), __version__)


def fmt_ts(ms):
    return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M:%S") if ms else ""


def cmd_init(cfg, args):
    path = write_template()
    print(f"config: {path}")
    print("Set webapp_url and token (see README), then run: python -m journal ping")


def cmd_dry_run(cfg, args):
    """Parse every fills file and print the trades without touching state or the network."""
    dirs = existing_watch_dirs(cfg)
    files = scan_dirs(dirs)
    fills, bad = {}, 0
    for p in files:
        info, fl, b = parse_file(p)
        bad += len(b)
        if not cfg["include_sim"] and info.firm.upper() == "SIM":
            continue
        for f in fl:
            fills.setdefault(f.key, f)
    trades, _ = build_trades(list(fills.values()), cfg)
    if cfg.get("journal_since_ms"):
        print(f"journaling only trades opened since {fmt_ts(cfg['journal_since_ms'])} (journal_since_ms)")
    shown = [t for t in journaled(trades, cfg)
             if (not args.date or t.session_date == args.date)
             and (not args.account or args.account.lower() in f"{t.firm} {t.account}".lower())]

    if args.trades:
        print(f"{'session':10} {'account':38} {'symbol':15} {'side':5} {'qty':>3} {'entry time':19} "
              f"{'entry':>11} {'exit':>11} {'points':>8} {'gross':>9} {'net':>9} status")
        for t in shown:
            print(f"{t.session_date:10} {(t.firm + ' ' + t.account)[:38]:38} {t.symbol:15} {t.side:5} {t.max_qty:>3} "
                  f"{fmt_ts(t.entry_ts):19} {t.entry_price:>11.2f} {(t.exit_price or 0):>11.2f} "
                  f"{(t.points or 0):>8.2f} {(t.gross or 0):>9.2f} {(t.net or 0):>9.2f} {t.status}")
        print()

    daily = defaultdict(lambda: [0, 0.0, 0.0])
    for t in shown:
        if t.status == "Closed":
            d = daily[(t.session_date, f"{t.firm} {t.account}")]
            d[0] += 1
            d[1] += t.gross or 0
            d[2] += t.net or 0
    if args.daily:
        for (day, acct), (n, g, net) in sorted(daily.items()):
            print(f"{day}  {acct[:40]:40} trades {n:>3}  gross {g:>9.2f}  net {net:>9.2f}")
        print()

    warnings = sorted({w for t in shown for w in t.warnings})
    print(f"folders: {[str(d) for d in dirs]}")
    print(f"files: {len(files)}  fills: {len(fills)}  unparseable lines: {bad}")
    print(f"trades: {len(shown)}  closed: {sum(t.status == 'Closed' for t in shown)}  "
          f"open: {sum(t.status == 'Open' for t in shown)}  "
          f"gross: {sum(t.gross or 0 for t in shown):.2f}  net: {sum(t.net or 0 for t in shown):.2f}")
    if warnings:
        print("warnings:", "; ".join(warnings))


def cmd_start_fresh(cfg, args):
    now = int(time.time() * 1000)
    path = set_value("journal_since_ms", now)
    print(f"Only trades opened from {fmt_ts(now)} on will be journaled ({path}).")
    print("Restart the background app for it to take effect (scripts\\install.ps1 restarts it).")


def cmd_ping(cfg, args):
    resp = make_client(cfg).post({"ping": True})
    print("web app OK:", resp)


def _syncer(cfg):
    return Syncer(cfg, State(DB_PATH), make_client(cfg), pc_name())


def cmd_backfill(cfg, args):
    s = _syncer(cfg)
    new = s.scan()
    fills, trades = s.stage()
    print(f"new fills: {new}  queued fills: {fills}  queued trade updates: {trades}")
    total = 0
    while True:
        sent = s.flush()
        total += sent
        item = s.state.next_outbox()
        if item is None:
            break
        if not sent:
            print(f"delivery is failing; {s.state.counts()['outbox']} payload(s) left in the outbox. See {LOG_PATH}")
            sys.exit(1)
    s.heartbeat()
    print(f"delivered {total} payload(s)")


def cmd_run(cfg, args):
    lock_path = APP_DIR / "run.lock"
    lock = open(lock_path, "a+")
    try:
        import msvcrt
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except ImportError:
        pass
    except OSError:
        logging.getLogger("journal").error("already running (lock held: %s)", lock_path)
        sys.exit(1)
    _syncer(cfg).run_forever()


def cmd_status(cfg, args):
    st = State(DB_PATH)
    c = st.counts()
    print(f"config: {CONFIG_PATH} ({'found' if CONFIG_PATH.exists() else 'missing - run: python -m journal init'})")
    print(f"web app configured: {bool(cfg['webapp_url'] and cfg['token'])}")
    print(f"watching: {[str(d) for d in existing_watch_dirs(cfg)]}")
    print(f"fills known: {c['fills']}  not yet queued: {c['fills_unsent']}  trades sent: {c['trades_sent']}")
    print(f"outbox: {c['outbox']}  last error: {c['outbox_error']}")
    print(f"last fill: {fmt_ts(c['last_fill_ts'])}")
    print(f"log: {LOG_PATH}")


def main():
    ap = argparse.ArgumentParser(prog="python -m journal", description="Deep Charts -> Google Sheets trade journal")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="create config.json in %%LOCALAPPDATA%%\\DeepChartsJournal")
    dr = sub.add_parser("dry-run", help="parse all fills and print trades; no upload")
    dr.add_argument("--date", help="only this session date, YYYY-MM-DD")
    dr.add_argument("--account", help="only accounts containing this text")
    dr.add_argument("--trades", action="store_true", help="print each trade")
    dr.add_argument("--daily", action="store_true", help="print P&L per day and account")
    sub.add_parser("start-fresh", help="journal only trades opened from now on")
    sub.add_parser("ping", help="check the web app URL and token")
    sub.add_parser("backfill", help="send all history once, then exit")
    sub.add_parser("run", help="watch and sync forever (what autostart runs)")
    sub.add_parser("status", help="show local state")
    args = ap.parse_args()

    cfg = load_config()
    if args.cmd in ("ping", "backfill", "run"):
        setup_logging(args.verbose)
    handlers = {"init": cmd_init, "dry-run": cmd_dry_run, "start-fresh": cmd_start_fresh, "ping": cmd_ping,
                "backfill": cmd_backfill, "run": cmd_run, "status": cmd_status}
    try:
        handlers[args.cmd](cfg, args)
    except SheetsError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
