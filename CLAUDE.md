# Deep Charts Journal

Background app that auto-journals Deep Charts / DeepDOM trades into a Google Sheet. Windows,
Python 3.10+ stdlib only (no pip deps), plus an Apps Script receiver bound to the Sheet.

## Commands
- Tests (Python): `python -m unittest discover -s tests -t .`
- Tests (Apps Script, Node 18+): `node --test` (not `node --test tests/`)
- Inspect real data without uploading: `python -m journal dry-run [--trades] [--daily] [--date D] [--account X]`
- Other CLI: `init | ping | backfill | run | status` (see `journal/__main__.py`)

## Layout
- `journal/parser.py`: `.fills` file name and line parsing, `fill_key`
- `journal/trades.py`: flat-to-flat trade builder and P&L
- `journal/state.py`: SQLite state and outbox in `%LOCALAPPDATA%\DeepChartsJournal`
- `journal/sync.py`: poll loop (scan → stage → flush → heartbeat)
- `journal/sheets.py`: urllib client for the web app
- `apps_script/Code.gs`: Sheet receiver; tested by `tests/apps_script.test.js` with a fake SpreadsheetApp
- `scripts/install.ps1`, `scripts/uninstall.ps1`: Startup-folder autostart via pythonw

## Data facts (verified against real files 2026-10-18)
- Files: `Documents\Deepchart\Hist Fills\<Firm>_<Account>_<YYYY-MM-DD>.fills` (also `Deepdom\`), in OneDrive.
  Firm may contain spaces ("Lucid Account"); so may the account ("SIM 1").
- Line: 9 `;` fields, CRLF: symbol; epoch ms UTC; flag; price; signed qty; position id; close id; first-after-load flag; 0.
- Identical lines are distinct partial fills. Never collapse them (`fill_key` includes an occurrence index).
- Replay (backtest) fills go to the SIM file named for the wall-clock day, but their timestamps are the
  replayed market time (verified 2026-10-23). `is_replay` = fill's local date earlier than the file date.
  Replay trades are grouped per file date, `trade_id` gets a `|replay <date>` suffix, and they're sent as
  `replay_trades` to the Replay tab (A–V; notes from W). Same-day replays are not detected.
- Don't group by position id: scale-ins get their own id, and a platform restart re-IDs the exit. Group flat-to-flat.
- `Logs\*.log` are encrypted and `.perf3` is binary; neither is usable. `Trading Account\*.info` JSON has
  DailyPL/DailyNetPL (Apex 2026-10-06: 16.5 / 10.38), which is the P&L reference in tests.

## Invariants
- Never write to Deep Charts' folders; read only.
- `fill_key` and `trade_id` must stay deterministic across PCs. Changing either duplicates rows in existing Sheets.
- The Sheet receiver writes only Trades columns A–U; user note columns start at V.
- Test fixtures are real fills with anonymized account names in the file names. Keep real account ids out of the repo.
