// Runs apps_script/Code.gs against an in-memory fake of the Sheets API.  Run: node --test
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class FakeRange {
  constructor(sheet, r, c, nr, nc) { Object.assign(this, { sheet, r, c, nr, nc }); }
  getValues() {
    const out = [];
    for (let i = 0; i < this.nr; i++) {
      const row = this.sheet.data[this.r - 1 + i] || [];
      out.push(Array.from({ length: this.nc }, (_, j) => (row[this.c - 1 + j] ?? '')));
    }
    return out;
  }
  setValues(values) {
    assert.strictEqual(values.length, this.nr, 'row count mismatch');
    values.forEach((vals, i) => {
      assert.strictEqual(vals.length, this.nc, 'column count mismatch');
      const idx = this.r - 1 + i;
      while (this.sheet.data.length <= idx) this.sheet.data.push([]);
      vals.forEach((v, j) => { this.sheet.data[idx][this.c - 1 + j] = v; });
    });
    return this;
  }
  setFormula(f) { this.sheet.formulas[`${this.r},${this.c}`] = f; return this; }
  setNumberFormat() { return this; }
  setFontWeight() { return this; }
}

class FakeSheet {
  constructor(name) { this.name = name; this.data = []; this.formulas = {}; }
  getName() { return this.name; }
  setName(n) { this.name = n; }
  getLastRow() {
    let n = this.data.length;
    while (n && !(this.data[n - 1] || []).some(v => v !== '' && v !== undefined)) n--;
    return n;
  }
  getMaxRows() { return 1000; }
  getRange(r, c, nr = 1, nc = 1) { return new FakeRange(this, r, c, nr, nc); }
  setFrozenRows() {}
}

function load(token = 'secret') {
  const sheets = [new FakeSheet('Sheet1')];
  const ss = {
    tz: 'UTC',
    getSheetByName: n => sheets.find(s => s.getName() === n) || null,
    getSheets: () => sheets,
    insertSheet: n => { const s = new FakeSheet(n); sheets.push(s); return s; },
    getSpreadsheetTimeZone() { return this.tz; },
    setSpreadsheetTimeZone(tz) { this.tz = tz; },
  };
  const ctx = {
    SpreadsheetApp: { getActiveSpreadsheet: () => ss, flush() {} },
    PropertiesService: { getScriptProperties: () => ({ getProperty: k => (k === 'TOKEN' ? token : null) }) },
    LockService: { getScriptLock: () => ({ tryLock: () => true, releaseLock() {} }) },
    ContentService: {
      MimeType: { JSON: 'json' },
      createTextOutput: s => ({ content: s, setMimeType() { return this; } }),
    },
    Utilities: { parseDate: s => new Date(`${s}T00:00:00Z`) },
  };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'apps_script', 'Code.gs'), 'utf8'), ctx);
  const post = body => JSON.parse(ctx.doPost({ postData: { contents: JSON.stringify({ token, pc: 'PC1', ...body }) } }).content);
  return { ss, post, sheet: n => ss.getSheetByName(n) };
}

const fill = (key, extra = {}) => ({
  key, firm: 'Apex', account: 'PA-1', symbol: 'MNQ-202612-CME', session_date: '2026-10-06', ts_ms: 1791306875338,
  price: 31505, qty: 3, flag: '0', position_id: '7330729834127616', close_id: '', first_flag: '1',
  source_file: 'x.fills', trade_id: 'Apex|PA-1|MNQ-202612-CME|1791306875338', ...extra,
});
const trade = (extra = {}) => ({
  trade_id: 'Apex|PA-1|MNQ-202612-CME|1791306875338', session_date: '2026-10-06', firm: 'Apex', account: 'PA-1',
  account_type: 'Prop', symbol: 'MNQ-202612-CME', side: 'Long', max_qty: 3, entry_ts: 1791306875338,
  entry_price: 31505, exit_ts: 1791307237618, exit_price: 31486.75, duration_s: 362.28, points: -18.25,
  gross: -109.5, fees: 3.06, net: -112.56, win: 0, n_fills: 2, status: 'Closed', ...extra,
});

test('bad token is refused and nothing is written', () => {
  const { post, sheet } = load('secret');
  const res = post({ token: 'wrong', fills: [fill('a#0')] });
  assert.deepStrictEqual(res, { ok: false, error: 'bad token' });
  assert.strictEqual(sheet('Fills'), null);
});

test('ping creates the tabs, reusing the blank Sheet1 as Trades', () => {
  const { post, ss, sheet } = load();
  assert.strictEqual(post({ ping: true }).ok, true);
  assert.deepStrictEqual(ss.getSheets().map(s => s.getName()).sort(),
    ['Daily', 'Fills', 'Replay', 'Replay Daily', 'Status', 'Trades']);
  assert.strictEqual(sheet('Trades').data[0][0], 'Trade ID');
  assert.strictEqual(sheet('Trades').data[0][21], 'Setup');
  assert.strictEqual(sheet('Replay').data[0][21], 'Replayed On');
  assert.strictEqual(sheet('Replay').data[0][22], 'Setup');
  assert.strictEqual(ss.tz, 'America/New_York');
  assert.match(sheet('Daily').formulas['1,1'], /^=QUERY\(Trades!A2:U/);
  assert.match(sheet('Replay Daily').formulas['1,1'], /^=QUERY\(Replay!A2:V/);
});

test('same payload twice gives one row per fill and per trade', () => {
  const { post, sheet } = load();
  const body = { fills: [fill('a#0'), fill('a#1'), fill('b#0')], trades: [trade()] };
  assert.deepStrictEqual(post(body), { ok: true, fills_added: 3, trades_upserted: 1, replay_trades_upserted: 0 });
  assert.deepStrictEqual(post(body), { ok: true, fills_added: 0, trades_upserted: 1, replay_trades_upserted: 0 });
  assert.strictEqual(sheet('Fills').getLastRow(), 4);
  assert.strictEqual(sheet('Trades').getLastRow(), 2);
});

test('trade update keeps the user notes columns', () => {
  const { post, sheet } = load();
  post({ trades: [trade({ status: 'Open', gross: null, net: null, exit_ts: null })] });
  const t = sheet('Trades');
  t.getRange(2, 22, 1, 2).setValues([['ORB breakout', 'A']]);
  post({ trades: [trade()] });
  const row = t.data[1];
  assert.strictEqual(row[19], 'Closed');
  assert.strictEqual(row[14], -109.5);
  assert.deepStrictEqual(row.slice(21, 23), ['ORB breakout', 'A']);
});

test('a Closed trade is never downgraded to Open', () => {
  const { post, sheet } = load();
  post({ trades: [trade()] });
  post({ trades: [trade({ status: 'Open', gross: null })] });
  assert.strictEqual(sheet('Trades').data[1][19], 'Closed');
  assert.strictEqual(sheet('Trades').data[1][14], -109.5);
});

test('long ids are written as text and times as dates', () => {
  const { post, sheet } = load();
  post({ fills: [fill('a#0')], trades: [trade()] });
  const f = sheet('Fills').data[1];
  assert.strictEqual(f[9], '7330729834127616');
  assert.strictEqual(Object.prototype.toString.call(f[1]), "[object Date]");
  assert.strictEqual(f[14], 'PC1');
  const t = sheet('Trades').data[1];
  assert.strictEqual(Object.prototype.toString.call(t[8]), "[object Date]");
  assert.strictEqual(t[12], 362.28 / 86400);
});

const replay = (extra = {}) => trade({
  trade_id: 'SIM|SIM 1|NQ-202603-CME|1767627131991|replay 2026-10-23', session_date: '2026-01-05', firm: 'SIM',
  account: 'SIM 1', account_type: 'Replay', symbol: 'NQ-202603-CME', replayed_on: '2026-10-23', ...extra,
});

test('replay trades go to the Replay tab only and keep their notes', () => {
  const { post, sheet } = load();
  const res = post({ replay_trades: [replay({ status: 'Open', gross: null })] });
  assert.deepStrictEqual(res, { ok: true, fills_added: 0, trades_upserted: 0, replay_trades_upserted: 1 });
  assert.strictEqual(sheet('Trades').getLastRow(), 1);
  const r = sheet('Replay');
  assert.strictEqual(r.data[1][0], 'SIM|SIM 1|NQ-202603-CME|1767627131991|replay 2026-10-23');
  assert.strictEqual(r.data[1][4], 'Replay');
  assert.deepStrictEqual([r.data[1][1], r.data[1][21]].map(d => d.toISOString().slice(0, 10)), ['2026-01-05', '2026-10-23']);
  r.getRange(2, 23, 1, 1).setValues([['ORB retest']]);
  post({ replay_trades: [replay()] });
  post({ replay_trades: [replay({ status: 'Open', gross: null })] });
  assert.strictEqual(r.getLastRow(), 2);
  assert.strictEqual(r.data[1][19], 'Closed');
  assert.strictEqual(r.data[1][14], -109.5);
  assert.strictEqual(r.data[1][22], 'ORB retest');
});

test('heartbeat upserts one row per PC', () => {
  const { post, sheet } = load();
  post({ heartbeat: { pc: 'PC1', fills: 1, warnings: ['x'] } });
  post({ heartbeat: { pc: 'PC2', fills: 2, warnings: [] } });
  post({ heartbeat: { pc: 'PC1', fills: 5, warnings: [] } });
  const s = sheet('Status');
  assert.strictEqual(s.getLastRow(), 3);
  assert.strictEqual(s.data[1][4], 5);
});
