"""Configuration: lives outside OneDrive and the repo, in %LOCALAPPDATA%\\DeepChartsJournal."""

import copy
import json
import os
from pathlib import Path

APP_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "DeepChartsJournal"
CONFIG_PATH = APP_DIR / "config.json"
DB_PATH = APP_DIR / "state.db"
LOG_PATH = APP_DIR / "journal.log"

_HOME = Path.home()
DEFAULT_WATCH_DIRS = [
    str(_HOME / "OneDrive" / "Documents" / "Deepchart" / "Hist Fills"),
    str(_HOME / "OneDrive" / "Documents" / "Deepdom" / "Hist Fills"),
    str(_HOME / "Documents" / "Deepchart" / "Hist Fills"),
    str(_HOME / "Documents" / "Deepdom" / "Hist Fills"),
]

DEFAULTS = {
    # Apps Script web-app URL and the shared secret set as Script Property TOKEN.
    "webapp_url": "",
    "token": "",
    # Folders that do not exist on this PC are skipped.
    "watch_dirs": DEFAULT_WATCH_DIRS,
    # Dollars per 1.00 price move, per contract, keyed by symbol root ("MNQ" from "MNQ-202612-CME").
    "multipliers": {
        "MNQ": 2, "NQ": 20, "MES": 5, "ES": 50, "M2K": 5, "RTY": 50,
        "MYM": 0.5, "YM": 5, "MCL": 100, "CL": 1000, "MGC": 10, "GC": 100,
    },
    # Commission + fees per contract per side. "default" applies to every firm unless the
    # firm name (the part of the file name before the first "_") has its own entry.
    # MNQ 0.51 was derived from Apex DailyPL vs DailyNetPL; anything missing counts as 0 and is flagged.
    "fees_per_side": {
        "default": {"MNQ": 0.51},
    },
    # Only trades opened at or after this time (epoch ms) are journaled; 0 = all history.
    # Set it to now with: python -m journal start-fresh
    "journal_since_ms": 0,
    "include_sim": True,
    "poll_seconds": 3,
    "heartbeat_seconds": 300,
    "batch_size": 200,
}


def _merge(base, override):
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path=CONFIG_PATH):
    path = Path(path)
    if not path.exists():
        return copy.deepcopy(DEFAULTS)
    with open(path, encoding="utf-8") as f:
        return _merge(DEFAULTS, json.load(f))


def write_template(path=CONFIG_PATH):
    """Create config.json with defaults if it does not exist; return its path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with open(path, "w", encoding="utf-8") as f:
            json.dump(DEFAULTS, f, indent=2)
    return path


def set_value(key, value, path=CONFIG_PATH):
    """Change one key in config.json, keeping everything else as written."""
    path = write_template(path)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    data[key] = value
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return path


def existing_watch_dirs(cfg):
    return [Path(d) for d in cfg["watch_dirs"] if Path(d).is_dir()]
