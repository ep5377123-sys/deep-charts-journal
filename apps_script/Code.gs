/**
 * Deep Charts Journal — receiver for the Python watcher (python -m journal run).
 *
 * Setup (once): paste this file into Extensions → Apps Script of a blank Google Sheet, then
 *   Project Settings → Script Properties → add  TOKEN = <same token as config.json>
 *   Deploy → New deployment → Web app → Execute as: Me, Who has access: Anyone → copy the URL.
 *
 * Rules: Fills are append-only and de-duplicated by Fill Key. Trades are upserted by Trade ID,
 * only the auto columns are written (never the notes columns to their right), and a Closed
 * trade is never downgraded to Open. Replay (backtest) trades follow the same rules on their
 * own Replay tab, so they never reach Trades or Daily.
 */

const TZ = 'America/New_York';

// [header, payload field]; null field = the sending PC's name.
const TRADE_COLS = [
  ['Trade ID', 'trade_id'], ['Session Date', 'session_date'], ['Firm', 'firm'], ['Account', 'account'],
  ['Type', 'account_type'], ['Symbol', 'symbol'], ['Side', 'side'], ['Max Qty', 'max_qty'],
  ['Entry Time', 'entry_ts'], ['Entry Price', 'entry_price'], ['Exit Time', 'exit_ts'], ['Exit Price', 'exit_price'],
  ['Duration', 'duration_s'], ['Points', 'points'], ['Gross P&L', 'gross'], ['Fees', 'fees'],
  ['Net P&L', 'net'], ['Win', 'win'], ['# Fills', 'n_fills'], ['Status', 'status'], ['Source PC', null],
];
// Session Date on the Replay tab is the replayed market day; Replayed On is when you replayed it.
const REPLAY_COLS = TRADE_COLS.concat([['Replayed On', 'replayed_on']]);
const TRADE_FORMATS = {
  session_date: 'yyyy-mm-dd', replayed_on: 'yyyy-mm-dd', entry_ts: 'yyyy-mm-dd hh:mm:ss',
  exit_ts: 'yyyy-mm-dd hh:mm:ss', duration_s: '[h]:mm:ss', gross: '$#,##0.00', fees: '$#,##0.00', net: '$#,##0.00',
};
const USER_COLS = ['Setup', 'Grade', 'Mistakes', 'Emotion', 'Notes', 'Screenshot'];
const FILL_COLS = [
  ['Fill Key', 'key'], ['Time', 'ts_ms'], ['Session Date', 'session_date'], ['Firm', 'firm'], ['Account', 'account'],
  ['Symbol', 'symbol'], ['Qty', 'qty'], ['Price', 'price'], ['Flag', 'flag'], ['Position ID', 'position_id'],
  ['Close ID', 'close_id'], ['First Flag', 'first_flag'], ['Trade ID', 'trade_id'], ['Source File', 'source_file'],
  ['PC', null],
];
const STATUS_COLS = ['PC', 'Last Heartbeat', 'Last Fill', 'Files', 'Fills', 'Trades', 'Open Trades', 'Outbox', 'Version', 'Warnings'];
// Long ids must stay text, or Sheets rounds them to 15 significant digits.
const TEXT_FIELDS = ['key', 'trade_id', 'position_id', 'close_id', 'account'];

const DAILY_BY_ACCOUNT =
  '=QUERY(Trades!A2:U, "select B, C, D, count(A), sum(O), sum(P), sum(Q), avg(R) where T = \'Closed\' ' +
  'group by B, C, D order by B desc label B \'Session\', C \'Firm\', D \'Account\', count(A) \'Trades\', ' +
  'sum(O) \'Gross\', sum(P) \'Fees\', sum(Q) \'Net\', avg(R) \'Win %\' format B \'yyyy-mm-dd\', ' +
  'sum(O) \'$#,##0.00\', sum(P) \'$#,##0.00\', sum(Q) \'$#,##0.00\', avg(R) \'0%\'", 0)';
const DAILY_TOTAL =
  '=QUERY(Trades!A2:U, "select B, count(A), sum(Q), avg(R) where T = \'Closed\' group by B order by B desc ' +
  'label B \'Session (all accounts)\', count(A) \'Trades\', sum(Q) \'Net\', avg(R) \'Win %\' ' +
  'format B \'yyyy-mm-dd\', sum(Q) \'$#,##0.00\', avg(R) \'0%\'", 0)';
const REPLAY_DAILY =
  '=QUERY(Replay!A2:V, "select B, count(A), sum(O), sum(P), sum(Q), avg(R) where T = \'Closed\' ' +
  'group by B order by B desc label B \'Market Day\', count(A) \'Trades\', sum(O) \'Gross\', sum(P) \'Fees\', ' +
  'sum(Q) \'Net\', avg(R) \'Win %\' format B \'yyyy-mm-dd\', sum(O) \'$#,##0.00\', sum(P) \'$#,##0.00\', ' +
  'sum(Q) \'$#,##0.00\', avg(R) \'0%\'", 0)';

function doGet() {
  return ContentService.createTextOutput('Deep Charts Journal receiver is running.');
}

function doPost(e) {
  let body;
  try {
    body = JSON.parse(e.postData.contents);
  } catch (err) {
    return out_({ ok: false, error: 'bad json' });
  }
  const token = PropertiesService.getScriptProperties().getProperty('TOKEN');
  if (!token || body.token !== token) return out_({ ok: false, error: 'bad token' });

  const lock = LockService.getScriptLock();
  if (!lock.tryLock(30000)) return out_({ ok: false, error: 'busy, retry' });
  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const sh = setup_(ss);
    const tz = ss.getSpreadsheetTimeZone();
    const res = {
      ok: true,
      fills_added: addFills_(sh.fills, body.fills || [], body.pc || '', tz),
      trades_upserted: upsertTrades_(sh.trades, TRADE_COLS, body.trades || [], body.pc || '', tz),
      replay_trades_upserted: upsertTrades_(sh.replay, REPLAY_COLS, body.replay_trades || [], body.pc || '', tz),
    };
    if (body.heartbeat) heartbeat_(sh.status, body.heartbeat);
    SpreadsheetApp.flush();
    return out_(res);
  } catch (err) {
    return out_({ ok: false, error: String(err && err.stack || err) });
  } finally {
    lock.releaseLock();
  }
}

function out_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

function setup_(ss) {
  let trades = ss.getSheetByName('Trades');
  if (!trades) {
    const blank = ss.getSheets().find(s => s.getName() === 'Sheet1' && s.getLastRow() === 0);
    if (blank) {
      blank.setName('Trades');
      trades = blank;
    } else {
      trades = ss.insertSheet('Trades');
    }
    ss.setSpreadsheetTimeZone(TZ);
    initTradeSheet_(trades, TRADE_COLS);
  }
  let fills = ss.getSheetByName('Fills');
  if (!fills) {
    fills = ss.insertSheet('Fills');
    fills.getRange(1, 1, 1, FILL_COLS.length).setValues([FILL_COLS.map(c => c[0])]).setFontWeight('bold');
    fills.setFrozenRows(1);
    formatColumns_(fills, FILL_COLS, { ts_ms: 'yyyy-mm-dd hh:mm:ss.000', session_date: 'yyyy-mm-dd' });
  }
  if (!ss.getSheetByName('Daily')) {
    const daily = ss.insertSheet('Daily');
    daily.getRange(1, 1).setFormula(DAILY_BY_ACCOUNT);
    daily.getRange(1, 11).setFormula(DAILY_TOTAL);
  }
  let replay = ss.getSheetByName('Replay');
  if (!replay) {
    replay = ss.insertSheet('Replay');
    initTradeSheet_(replay, REPLAY_COLS);
  }
  if (!ss.getSheetByName('Replay Daily')) {
    ss.insertSheet('Replay Daily').getRange(1, 1).setFormula(REPLAY_DAILY);
  }
  let status = ss.getSheetByName('Status');
  if (!status) {
    status = ss.insertSheet('Status');
    status.getRange(1, 1, 1, STATUS_COLS.length).setValues([STATUS_COLS]).setFontWeight('bold');
    status.setFrozenRows(1);
  }
  return { trades, fills, replay, status };
}

function initTradeSheet_(sheet, cols) {
  const headers = cols.map(c => c[0]).concat(USER_COLS);
  sheet.getRange(1, 1, 1, headers.length).setValues([headers]).setFontWeight('bold');
  sheet.setFrozenRows(1);
  formatColumns_(sheet, cols, TRADE_FORMATS);
}

function formatColumns_(sheet, cols, formats) {
  cols.forEach(([, field], i) => {
    const fmt = TEXT_FIELDS.indexOf(field) >= 0 ? '@' : formats[field];
    if (fmt) sheet.getRange(2, i + 1, sheet.getMaxRows() - 1, 1).setNumberFormat(fmt);
  });
}

function cell_(field, value, tz) {
  if (value === null || value === undefined) return '';
  if (field === 'entry_ts' || field === 'exit_ts' || field === 'ts_ms') return new Date(value);
  if (field === 'session_date' || field === 'replayed_on') return Utilities.parseDate(value, tz, 'yyyy-MM-dd');
  if (field === 'duration_s') return value / 86400;
  if (TEXT_FIELDS.indexOf(field) >= 0) return String(value);
  return value;
}

function rowFor_(cols, obj, pc, tz) {
  return cols.map(([, field]) => (field === null ? pc : cell_(field, obj[field], tz)));
}

function addFills_(sheet, fills, pc, tz) {
  if (!fills.length) return 0;
  const last = sheet.getLastRow();
  const keys = new Set(last > 1 ? sheet.getRange(2, 1, last - 1, 1).getValues().map(r => String(r[0])) : []);
  const rows = [];
  fills.forEach(f => {
    if (keys.has(f.key)) return;
    keys.add(f.key);
    rows.push(rowFor_(FILL_COLS, f, pc, tz));
  });
  if (rows.length) sheet.getRange(last + 1, 1, rows.length, FILL_COLS.length).setValues(rows);
  return rows.length;
}

function upsertTrades_(sheet, cols, trades, pc, tz) {
  if (!trades.length) return 0;
  const n = cols.length;
  const statusIdx = cols.findIndex(c => c[1] === 'status');
  const last = sheet.getLastRow();
  const existing = last > 1 ? sheet.getRange(2, 1, last - 1, n).getValues() : [];
  const where = new Map();
  existing.forEach((r, i) => where.set(String(r[0]), { rows: existing, i: i, sheetRow: i + 2 }));
  const appends = [];
  let count = 0;
  trades.forEach(t => {
    const row = rowFor_(cols, t, pc, tz);
    const hit = where.get(String(t.trade_id));
    if (!hit) {
      where.set(String(t.trade_id), { rows: appends, i: appends.length, sheetRow: null });
      appends.push(row);
      count++;
      return;
    }
    if (hit.rows[hit.i][statusIdx] === 'Closed' && t.status === 'Open') return;
    hit.rows[hit.i] = row;
    if (hit.sheetRow) sheet.getRange(hit.sheetRow, 1, 1, n).setValues([row]);
    count++;
  });
  if (appends.length) sheet.getRange(last + 1, 1, appends.length, n).setValues(appends);
  return count;
}

function heartbeat_(sheet, hb) {
  const last = sheet.getLastRow();
  const names = last > 1 ? sheet.getRange(2, 1, last - 1, 1).getValues().map(r => String(r[0])) : [];
  const i = names.indexOf(String(hb.pc));
  const row = [
    hb.pc, new Date(), hb.last_fill_ts ? new Date(hb.last_fill_ts) : '', hb.files, hb.fills, hb.trades,
    hb.open_trades, hb.outbox, hb.version, (hb.warnings || []).join('; '),
  ];
  sheet.getRange(i >= 0 ? i + 2 : last + 1, 1, 1, row.length).setValues([row]);
}
