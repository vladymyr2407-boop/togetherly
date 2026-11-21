/**
 * Togetherly ⇄ Google Sheets — двостороння синхронізація робочих задач.
 *
 * Аркуш «Задачі» (рядок 1 — заголовки):
 *   A ID | B Задача | C Опис | D Строк | E Час | F Пріоритет | G Готово | H Змінено в Togetherly
 * Новий рядок можна додавати без ID — скрипт присвоїть його сам.
 * Прихований аркуш «_sync» зберігає відбитки рядків — не редагуй його.
 *
 * Налаштування: меню «Togetherly» → «Вказати API-ключ» (ключ створюється в
 * застосунку: Налаштування → Інтеграції · Google Sheets).
 */

const TGLY_API = 'https://fciaawuxdyeqhnbqhljs.supabase.co/functions/v1/togetherly-api/sync/sheet';
const SHEET = 'Задачі';
const META = '_sync';
const HEAD = ['ID', 'Задача', 'Опис', 'Строк', 'Час', 'Пріоритет', 'Готово', 'Змінено в Togetherly'];
const PRIO = { 1: 'Високий', 2: 'Середній', 3: 'Низький' };
const MAX_DELETES = 5; // захист: якщо з таблиці зникло більше рядків за раз — нічого не видаляємо

function onOpen() {
  SpreadsheetApp.getUi().createMenu('Togetherly')
    .addItem('🔄 Синхронізувати зараз', 'syncNow')
    .addSeparator()
    .addItem('🔑 Вказати API-ключ', 'setApiKey')
    .addItem('⏱ Увімкнути автосинхронізацію (кожні 10 хв)', 'enableAutoSync')
    .addItem('⏹ Вимкнути автосинхронізацію', 'disableAutoSync')
    .addItem('🧱 Підготувати аркуш', 'setupSheet')
    .addToUi();
}

function setApiKey() {
  const ui = SpreadsheetApp.getUi();
  const r = ui.prompt('API-ключ Togetherly', 'Встав ключ, що починається з tgly_', ui.ButtonSet.OK_CANCEL);
  if (r.getSelectedButton() !== ui.Button.OK) return;
  const key = r.getResponseText().trim();
  if (!/^tgly_[0-9a-f]{48}$/.test(key)) { ui.alert('Це не схоже на ключ Togetherly.'); return; }
  PropertiesService.getUserProperties().setProperty('TGLY_KEY', key);
  ui.alert('Ключ збережено. Тепер «Синхронізувати зараз».');
}

function enableAutoSync() {
  disableAutoSync();
  ScriptApp.newTrigger('syncNow').timeBased().everyMinutes(10).create();
  SpreadsheetApp.getActive().toast('Автосинхронізація кожні 10 хвилин увімкнена', 'Togetherly');
}
function disableAutoSync() {
  ScriptApp.getProjectTriggers().filter((t) => t.getHandlerFunction() === 'syncNow').forEach((t) => ScriptApp.deleteTrigger(t));
}

function setupSheet() {
  const ss = SpreadsheetApp.getActive();
  const sh = ss.getSheetByName(SHEET) || ss.insertSheet(SHEET, 0);
  sh.getRange(1, 1, 1, HEAD.length).setValues([HEAD]).setFontWeight('bold').setBackground('#dcebe4');
  sh.setFrozenRows(1);
  const n = Math.max(sh.getMaxRows() - 1, 500);
  sh.getRange(2, 4, n, 1).setNumberFormat('yyyy-mm-dd');
  sh.getRange(2, 5, n, 1).setNumberFormat('@'); // час як текст «09:30»
  sh.getRange(2, 6, n, 1).setDataValidation(SpreadsheetApp.newDataValidation()
    .requireValueInList(['', 'Високий', 'Середній', 'Низький'], true).setAllowInvalid(false).build());
  sh.getRange(2, 7, n, 1).insertCheckboxes();
  sh.setColumnWidth(1, 90); sh.setColumnWidth(2, 320); sh.setColumnWidth(3, 260); sh.setColumnWidth(8, 150);
  sh.getRange(1, 1).setNote('ID ставить скрипт. Не змінюй і не копіюй ID між рядками.');
  sh.getRange(1, 8).setNote('Заповнює скрипт — коли задачу востаннє змінили в Togetherly.');
  const rules = [
    ['Високий', '#fdebdc'], ['Середній', '#e2ecfa'], ['Низький', '#e3f2e6'],
  ].map(([v, c]) => SpreadsheetApp.newConditionalFormatRule()
    .whenFormulaSatisfied(`=$F2="${v}"`).setBackground(c).setRanges([sh.getRange(2, 1, n, HEAD.length)]).build());
  rules.push(SpreadsheetApp.newConditionalFormatRule().whenFormulaSatisfied('=$G2=TRUE')
    .setFontColor('#8d938d').setStrikethrough(true).setRanges([sh.getRange(2, 2, n, 1)]).build());
  sh.setConditionalFormatRules(rules);
  metaSheet_();
  ss.toast('Аркуш готовий', 'Togetherly');
}

// ---------- синхронізація ----------
function syncNow() {
  const lock = LockService.getDocumentLock();
  if (!lock.tryLock(20000)) return;
  try { doSync_(); } finally { lock.releaseLock(); }
}

function doSync_() {
  const key = PropertiesService.getUserProperties().getProperty('TGLY_KEY');
  if (!key) throw new Error('Спершу вкажи API-ключ: меню Togetherly → «Вказати API-ключ»');
  const ss = SpreadsheetApp.getActive();
  const sh = ss.getSheetByName(SHEET);
  if (!sh) throw new Error('Немає аркуша «' + SHEET + '» — запусти «Підготувати аркуш»');
  const tz = ss.getSpreadsheetTimeZone();
  const meta = readMeta_();                      // { since, hashes: {id: hash} }

  const last = sh.getLastRow();
  const values = last > 1 ? sh.getRange(2, 1, last - 1, HEAD.length).getValues() : [];
  const rowsById = {};
  const toSend = [];
  const seen = {};
  values.forEach((r, i) => {
    const title = String(r[1] || '').trim();
    if (!title) return;
    let id = String(r[0] || '').trim();
    if (!id || seen[id]) { id = Utilities.getUuid(); values[i][0] = id; sh.getRange(i + 2, 1).setValue(id); }
    seen[id] = true;
    const task = rowToTask_(r, id, tz);
    rowsById[id] = i;
    if (meta.hashes[id] !== hash_(task)) toSend.push(task);
  });

  // рядки, що зникли з таблиці
  const vanished = Object.keys(meta.hashes).filter((id) => !seen[id]);
  let note = '';
  if (vanished.length && vanished.length <= MAX_DELETES) vanished.forEach((id) => toSend.push({ ext_id: id, deleted: true }));
  else if (vanished.length) note = `Зникло ${vanished.length} рядків — видалення пропущено (захист). Якщо так і треба, видаляй по ${MAX_DELETES} за раз.`;

  const res = UrlFetchApp.fetch(TGLY_API, {
    method: 'post', contentType: 'application/json', muteHttpExceptions: true,
    headers: { 'x-api-key': key },
    payload: JSON.stringify({ since: meta.since, tasks: toSend }),
  });
  const code = res.getResponseCode();
  const body = JSON.parse(res.getContentText() || '{}');
  if (code !== 200) throw new Error('Togetherly: ' + (body.error || code));

  // надіслане вважаємо синхронізованим (крім рядків з помилками)
  const failed = {};
  (body.errors || []).forEach((e) => failed[e.ext_id] = e.error);
  toSend.forEach((t) => {
    if (failed[t.ext_id]) return;
    if (t.deleted) delete meta.hashes[t.ext_id]; else meta.hashes[t.ext_id] = hash_(t);
  });

  // зміни з Togetherly → таблиця
  const appends = [];
  (body.changes || []).forEach((c) => {
    const row = [c.ext_id, c.title, c.description || '', c.due_date ? parseDate_(c.due_date) : '', c.due_time || '',
      PRIO[c.priority] || '', c.status === 'done', Utilities.formatDate(new Date(c.updated_at), tz, 'yyyy-MM-dd HH:mm')];
    const i = rowsById[c.ext_id];
    if (i !== undefined) {
      sh.getRange(i + 2, 1, 1, HEAD.length).setValues([row]);
      values[i] = row;
    } else {
      appends.push(row);
    }
    meta.hashes[c.ext_id] = hash_(rowToTask_(row, c.ext_id, tz));
  });
  if (appends.length) {
    const start = sh.getLastRow() + 1;
    sh.getRange(start, 1, appends.length, HEAD.length).setValues(appends);
    sh.getRange(start, 7, appends.length, 1).insertCheckboxes();
    appends.forEach((r, k) => rowsById[r[0]] = start - 2 + k);
  }

  // видалені в Togetherly → прибираємо рядки (знизу вгору)
  const del = (body.deleted || []).filter((id) => rowsById[id] !== undefined).map((id) => rowsById[id] + 2).sort((a, b) => b - a);
  del.forEach((r) => sh.deleteRow(r));
  (body.deleted || []).forEach((id) => delete meta.hashes[id]);

  meta.since = body.now;
  writeMeta_(meta);
  const s = body.stats || {};
  const errs = Object.keys(failed).length;
  ss.toast(`↑ нових ${s.created || 0}, змін ${s.updated || 0}, видалено ${s.removed || 0} · ↓ ${(body.changes || []).length} змін, ${del.length} видалено`
    + (errs ? ` · помилок ${errs}` : '') + (note ? ' · ' + note : ''), 'Togetherly', 8);
  if (errs) Logger.log(JSON.stringify(body.errors));
}

function rowToTask_(r, id, tz) {
  const d = r[3];
  let due = '';
  if (d instanceof Date) due = Utilities.formatDate(d, tz, 'yyyy-MM-dd');
  else if (d) due = String(d).trim();
  let time = r[4];
  if (time instanceof Date) time = Utilities.formatDate(time, tz, 'HH:mm');
  const p = String(r[5] || '').trim();
  return {
    ext_id: id,
    title: String(r[1] || '').trim(),
    description: String(r[2] || '').trim(),
    due_date: due,
    due_time: String(time || '').trim(),
    priority: p === 'Високий' ? 1 : p === 'Середній' ? 2 : p === 'Низький' ? 3 : null,
    status: r[6] === true || String(r[6]).toLowerCase() === 'true' ? 'done' : 'open',
  };
}
function parseDate_(iso) { const [y, m, d] = iso.split('-').map(Number); return new Date(y, m - 1, d); }
function hash_(t) {
  const s = [t.title, t.description, t.due_date, t.due_time, t.priority || '', t.status].join('\u0001');
  return Utilities.base64Encode(Utilities.computeDigest(Utilities.DigestAlgorithm.MD5, s, Utilities.Charset.UTF_8));
}

// ---------- службовий аркуш ----------
function metaSheet_() {
  const ss = SpreadsheetApp.getActive();
  let m = ss.getSheetByName(META);
  if (!m) { m = ss.insertSheet(META); m.getRange(1, 1, 1, 2).setValues([['since', '']]); m.hideSheet(); }
  return m;
}
function readMeta_() {
  const m = metaSheet_();
  const last = m.getLastRow();
  const vals = last > 0 ? m.getRange(1, 1, last, 2).getValues() : [];
  const meta = { since: null, hashes: {} };
  vals.forEach((r, i) => { if (i === 0) meta.since = r[1] ? String(r[1]) : null; else if (r[0]) meta.hashes[String(r[0])] = String(r[1]); });
  return meta;
}
function writeMeta_(meta) {
  const m = metaSheet_();
  m.clearContents();
  const rows = [['since', meta.since || '']].concat(Object.keys(meta.hashes).map((id) => [id, meta.hashes[id]]));
  m.getRange(1, 1, rows.length, 2).setNumberFormat('@').setValues(rows);
}
