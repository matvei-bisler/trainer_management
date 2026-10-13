/**
 * Хранилище для тренажёров по ФОС. Версия 2.
 * Одна таблица может обслуживать несколько тренажёров: у каждого свой префикс листов.
 *
 * Вставляется в Google-таблицу: Расширения → Apps Script.
 * Публикуется как веб-приложение (выполнять от имени «Я», доступ «Все»).
 * Токен задаётся в Настройки проекта → Свойства скрипта → TOKEN.
 *
 * Столбцы задаёт приложение: новое поле в записи само добавляется в заголовок листа,
 * поэтому при развитии приложения этот скрипт менять не нужно.
 *
 * Запрос (POST, JSON): {token, action: "read" | "upsert", table, key, row}
 * Ответ (JSON):        {ok: true, version, rows?: [[...], ...]} или {ok: false, error}
 */

const VERSION = 2;
const TABLE_NAME = /^[a-z_]{1,40}$/;
// ключи по умолчанию — для запросов от версии приложения без поля key
const DEFAULT_KEYS = {reviews: 'qid', tests: 'test_id', attempts: 'attempt_id'};


function doPost(e) {
  let req;
  try {
    req = JSON.parse(e.postData.contents);
  } catch (err) {
    return out({ok: false, error: 'bad json'});
  }

  const token = PropertiesService.getScriptProperties().getProperty('TOKEN');
  if (!token) return out({ok: false, error: 'TOKEN is not set in script properties'});
  if (req.token !== token) return out({ok: false, error: 'forbidden'});
  if (!TABLE_NAME.test(req.table || '')) return out({ok: false, error: 'bad table name'});

  // очередь: одновременные отправки тестов пишутся по одной
  const lock = LockService.getScriptLock();
  lock.waitLock(25000);
  try {
    const sh = getSheet(req.table);

    if (req.action === 'read') {
      return out({ok: true, version: VERSION, rows: sh.getDataRange().getDisplayValues()});
    }
    if (req.action !== 'upsert' && req.action !== 'append') {
      return out({ok: false, error: 'unknown action: ' + req.action});
    }

    const src = req.row || {};
    const key = req.key || DEFAULT_KEYS[req.table];
    if (!key || !(key in src)) return out({ok: false, error: 'row has no key column'});

    const header = ensureColumns(sh, Object.keys(src));
    const row = header.map(c => (src[c] === null || src[c] === undefined) ? '' : String(src[c]));
    const k = header.indexOf(key);
    const last = sh.getLastRow();
    if (last > 1) {
      const keys = sh.getRange(2, k + 1, last - 1, 1).getDisplayValues().map(r => r[0]);
      const i = keys.indexOf(row[k]);
      if (i >= 0) {
        // столбцы, которых нет в запросе (например, добавленные вручную), сохраняем
        const old = sh.getRange(i + 2, 1, 1, header.length).getDisplayValues()[0];
        header.forEach((c, j) => { if (!(c in src)) row[j] = old[j]; });
        writeRow(sh, i + 2, row);
        return out({ok: true, version: VERSION, updated: true});
      }
    }
    writeRow(sh, last + 1, row);
    return out({ok: true, version: VERSION});
  } finally {
    lock.releaseLock();
  }
}


/** Открыть адрес /exec в браузере — проверка, что развёртывание живо. Данные не отдаёт. */
function doGet() {
  return out({ok: true, service: 'trainer-storage', version: VERSION});
}


/** Запустить один раз вручную из редактора: запрашивает доступ к таблице. */
function setup() {
  SpreadsheetApp.getActiveSpreadsheet();
  const token = PropertiesService.getScriptProperties().getProperty('TOKEN');
  Logger.log(token ? 'Готово: доступ выдан, TOKEN задан.'
                   : 'Доступ выдан, но TOKEN не задан — добавьте его в свойства скрипта.');
}


function getSheet(name) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  return ss.getSheetByName(name) || ss.insertSheet(name);
}


/** Добавляет в заголовок недостающие столбцы (в конец) и возвращает заголовок. */
function ensureColumns(sh, columns) {
  const width = Math.max(sh.getLastColumn(), 1);
  let header = sh.getRange(1, 1, 1, width).getDisplayValues()[0];
  while (header.length && header[header.length - 1] === '') header.pop();
  const missing = columns.filter(c => header.indexOf(c) < 0);
  if (missing.length) {
    header = header.concat(missing);
    writeRow(sh, 1, header);
    sh.setFrozenRows(1);
  }
  return header;
}


/** Пишет строку как текст: иначе ID вроде «3e104567» станет числом, а «2026-09-30» — датой. */
function writeRow(sh, r, row) {
  if (r > sh.getMaxRows()) sh.insertRowsAfter(sh.getMaxRows(), 500);
  if (row.length > sh.getMaxColumns()) sh.insertColumnsAfter(sh.getMaxColumns(), row.length - sh.getMaxColumns());
  sh.getRange(r, 1, 1, row.length).setNumberFormat('@').setValues([row]);
}


function out(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
                       .setMimeType(ContentService.MimeType.JSON);
}
