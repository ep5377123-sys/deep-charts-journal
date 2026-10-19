"""Parse Deep Charts "Hist Fills" files.

File name: <Firm>_<Account>_<YYYY-MM-DD>[suffix].fills  (suffix covers OneDrive conflict copies)
Line (9 fields, ';'-separated, CRLF-terminated):
  0 symbol        MNQ-202612-CME
  1 time          epoch milliseconds, UTC
  2 flag          0/1/2, meaning unknown (stored raw)
  3 price         may be a fractional average
  4 qty           signed: + buy, - sell
  5 position id   id of the entry order this fill belongs to
  6 close id      blank on opening/adding fills
  7 first flag    1 on the first fill after the platform loads
  8 always 0
"""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

FILENAME_RE = re.compile(
    r"^(?P<firm>[^_]+)_(?P<account>.+)_(?P<date>\d{4}-\d{2}-\d{2})(?P<suffix>.*)\.fills$"
)
FIELD_COUNT = 9


@dataclass(frozen=True)
class FileInfo:
    firm: str
    account: str
    session_date: str
    name: str


@dataclass(frozen=True)
class Fill:
    key: str
    firm: str
    account: str
    session_date: str
    symbol: str
    ts_ms: int
    price: float
    qty: int
    flag: str
    position_id: str
    close_id: str
    first_flag: str
    source_file: str
    line_no: int

    @property
    def root(self):
        return self.symbol.split("-", 1)[0]


def parse_filename(name):
    m = FILENAME_RE.match(Path(name).name)
    if not m:
        return None
    return FileInfo(m["firm"], m["account"], m["date"], Path(name).name)


def fill_key(firm, account, raw_line, occurrence):
    """Same fill -> same key on any PC. Identical lines in one file are distinct partial
    fills, so the occurrence index of the line within its file is part of the key."""
    digest = hashlib.sha1(f"{firm}|{account}|{raw_line}".encode("utf-8")).hexdigest()[:20]
    return f"{digest}#{occurrence}"


def parse_text(text, info):
    """Return (fills, bad_lines). A trailing line without its newline is still being
    written and is skipped until the next read."""
    fills, bad = [], []
    seen = {}
    lines = text.split("\n")
    complete = lines[:-1]  # the last element is "" when the text ends with "\n"
    for line_no, line in enumerate(complete):
        raw = line.rstrip("\r").strip()
        if not raw:
            continue
        f = raw.split(";")
        if len(f) != FIELD_COUNT:
            bad.append(raw)
            continue
        try:
            ts_ms = int(f[1])
            price = float(f[3])
            qty = int(f[4])
        except ValueError:
            bad.append(raw)
            continue
        occurrence = seen.get(raw, 0)
        seen[raw] = occurrence + 1
        fills.append(Fill(
            key=fill_key(info.firm, info.account, raw, occurrence),
            firm=info.firm, account=info.account, session_date=info.session_date,
            symbol=f[0], ts_ms=ts_ms, price=price, qty=qty, flag=f[2],
            position_id=f[5], close_id=f[6], first_flag=f[7],
            source_file=info.name, line_no=line_no,
        ))
    return fills, bad


def parse_file(path):
    """Return (FileInfo|None, fills, bad_lines)."""
    info = parse_filename(path)
    if info is None:
        return None, [], []
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        text = f.read()
    fills, bad = parse_text(text, info)
    return info, fills, bad


def scan_dirs(dirs):
    """Every *.fills file in the given folders, oldest session first."""
    paths = []
    for d in dirs:
        paths.extend(p for p in Path(d).glob("*.fills") if parse_filename(p.name))
    return sorted(paths, key=lambda p: (parse_filename(p.name).session_date, p.name))
