# Deep Charts Journal

Automatically journals every trade you place in **Deep Charts** (and DeepDOM) into a Google Sheet.

A small background app watches the fill files Deep Charts writes in real time
(`Documents\Deepchart\Hist Fills\*.fills`), groups fills into trades (flat → flat per account and
symbol), works out P&L and fees, and sends them to your Sheet. Nothing inside Deep Charts is changed.

Your Sheet gets four tabs:

| Tab | What's in it |
|---|---|
| **Trades** | One row per trade: account, side, size, entry/exit time and price, duration, points, gross, fees, net. Columns **Setup, Grade, Mistakes, Emotion, Notes, Screenshot** are yours: the app never writes to them. |
| **Fills** | Every raw fill (audit trail; de-duplicated). |
| **Daily** | P&L, trade count and win % per day and account, plus all accounts combined. |
| **Status** | One row per PC: last heartbeat, last fill seen, queued uploads, warnings. |

Requires Python 3.10+ (Windows). No packages to install.

## One-time setup

1. **Create the Sheet.** Open <https://sheets.new> and name it (e.g. *Trading Journal*).
2. **Add the receiver script.** In the Sheet: *Extensions → Apps Script*. Delete what's in `Code.gs`,
   paste the contents of [apps_script/Code.gs](apps_script/Code.gs), and save.
3. **Set a token.** Generate one:
   ```
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
   In Apps Script: *Project Settings (gear) → Script Properties → Add script property*:
   name `TOKEN`, value = that string.
4. **Deploy.** *Deploy → New deployment → Select type: Web app*. Set *Execute as: Me*, *Who has access:
   Anyone*. Click *Deploy*, approve the permissions, and copy the **Web app URL** (ends in `/exec`).
5. **Configure this PC.**
   ```
   python -m journal init
   ```
   Then edit `%LOCALAPPDATA%\DeepChartsJournal\config.json`: set `webapp_url` and `token`, and set
   your commission per contract per side under `fees_per_side`. MNQ defaults to $0.51, which matches
   Apex's numbers. NQ is unset, and the Status tab flags any symbol whose fee is missing. Use a firm
   name (the part of the fill file name before the first `_`, e.g. `"Lucid"`) for per-firm rates:
   ```json
   "fees_per_side": { "default": { "MNQ": 0.51, "NQ": 1.95 }, "Lucid": { "MNQ": 0.50 } }
   ```
6. **Check, then load your history.**
   ```
   python -m journal ping            # creates the tabs; must print "web app OK"
   python -m journal dry-run --daily # what will be sent (nothing is uploaded)
   python -m journal backfill        # uploads all past trades
   ```
7. **Run it in the background, now and at every logon.**
   ```
   powershell -ExecutionPolicy Bypass -File scripts\install.ps1
   ```

Run all commands from this folder.

### Second PC

Get this repo onto the other PC with `git clone` or a copy, into its own folder and not inside
another project. Install Python, then copy `config.json` into that PC's
`%LOCALAPPDATA%\DeepChartsJournal\` and repeat steps 6–7. Both PCs can run at the same time. Every
fill has a fixed key, so a trade seen by both PCs (or by an OneDrive conflict copy of a fill file)
is still journaled exactly once.

## Everyday use

There's nothing to do. Trades show up in the Sheet within a few seconds of the fill, while the PC is
online. If the internet or Google is down, uploads wait in a local queue and go through later.
Open trades show `Status = Open` and update once you're flat.

| Command | Purpose |
|---|---|
| `python -m journal status` | Local state, queue, last error, log path |
| `python -m journal dry-run --trades --date 2026-10-16` | Inspect trades for a day without uploading |
| `python -m journal ping` | Test the URL and token |
| `python -m journal start-fresh` | Journal only trades opened from now on (skip `backfill` if you use this) |
| `powershell -File scripts\uninstall.ps1 [-Purge]` | Stop and remove autostart (`-Purge` also deletes local state) |

Log file: `%LOCALAPPDATA%\DeepChartsJournal\journal.log`.

### Updating the receiver script

After editing `Code.gs` in Apps Script: *Deploy → Manage deployments → ✏️ → Version: New version →
Deploy*. The URL stays the same.

## Notes and limits

- Only fills that Deep Charts or DeepDOM writes on a PC running this app are captured. Trades placed
  on a phone or a broker's web platform are not.
- The fill file format is undocumented. If a Deep Charts update changes it, unreadable lines are
  skipped and reported in the Status tab and the log rather than guessed at.
- The web app URL and token are both needed to write to your Sheet. Keep `config.json` private;
  it's stored outside OneDrive and is git-ignored.

## Development

```
python -m unittest discover -s tests -t .   # parser, trade builder, sync loop, HTTP client
node --test                                 # Apps Script receiver against a fake Sheets API
```
