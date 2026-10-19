import copy
from pathlib import Path

from journal.config import DEFAULTS
from journal.parser import FileInfo, parse_file, parse_text

FIXTURES = Path(__file__).parent / "fixtures"


def cfg(**overrides):
    c = copy.deepcopy(DEFAULTS)
    c.update(overrides)
    return c


def fixture_fills(name):
    _, fills, bad = parse_file(FIXTURES / name)
    assert not bad, bad
    return fills


def text_fills(lines, firm="SIM", account="SIM 1", date="2026-10-18", name="SIM_SIM 1_2026-10-18.fills"):
    """Fills from synthetic lines given as (ts_ms, price, qty) or full raw strings."""
    raw = []
    for ln in lines:
        if isinstance(ln, str):
            raw.append(ln)
        else:
            ts, price, qty = ln
            raw.append(f"MNQ-202612-CME;{ts};0;{price};{qty};1;;0;0")
    fills, bad = parse_text("\r\n".join(raw) + "\r\n", FileInfo(firm, account, date, name))
    assert not bad, bad
    return fills
