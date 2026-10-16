"use strict";
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

function fixture(saved = false) {
  const tasks = [{id: 1, due_date: '2024-03-02'}, {id: 2, due_date: '2026-01-01'}, {id: 3, due_date: ''}];
  const context = {queueMicrotask, state: {tasks}};
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../../static/task-date-defaults.js'), 'utf8'), context);
  const helper = context.TaskDateDefaults;
  function node(value = '') {
    return {value, events: {}, textContent: '', disabled: false,
      addEventListener(event, callback) { this.events[event] = callback; },
      emit(event) { this.events[event]?.(); }};
  }
  const start = node(saved ? '2026-06-01' : ''), parent = node(), note = node(), reset = node(), form = node();
  form.querySelector = (selector) => ({'[name="start_date"]': start, '[name="parent_id"]': parent,
    '[data-task-date-note]': note, '[data-task-date-reset]': reset})[selector];
  helper.bind(form, {saved});
  return {helper, form, start, parent, note, reset, tasks};
}

test('calendar suggestion handles month/leap/year/DST boundaries and rejects invalid dates', () => {
  const {helper} = fixture();
  for (const [source, expected] of [['2024-03-02','2024-02-29'], ['2026-03-01','2026-02-27'],
    ['2026-01-01','2025-12-30'], ['2026-03-09','2026-03-07']]) assert.equal(helper.minusTwoDays(source), expected);
  for (const bad of ['', null, '2026-02-30', '2026-3-01', '0001-01-01']) assert.equal(helper.minusTwoDays(bad), '');
});

test('unsaved main predecessor fills date but manual changes including clearing freeze it', () => {
  const {helper, form, parent, start, note} = fixture();
  parent.value = '1'; parent.emit('change');
  assert.equal(start.value, '2024-02-29');
  assert.equal(helper.payload(form, {}).reset_start_date, true);
  start.value = ''; start.emit('input');
  parent.value = '2'; parent.emit('change');
  assert.equal(start.value, '');
  assert.equal(helper.payload(form, {}).start_date_origin, 'manual');
  assert.match(note.textContent, /时间已自定义/);
});

test('unavailable main predecessor clears only an auto-filled draft and disables reset', () => {
  const {parent, start, reset} = fixture();
  parent.value = '1'; parent.emit('change');
  parent.value = '3'; parent.emit('change');
  assert.equal(start.value, ''); assert.equal(reset.disabled, true);
  start.value = '2026-08-01'; start.emit('input');
  parent.value = ''; parent.emit('change');
  assert.equal(start.value, '2026-08-01');
});

test('saved tasks preserve dates across parent changes and data refresh; explicit reset applies once', () => {
  const {helper, form, parent, start, reset, tasks, note} = fixture(true);
  parent.value = '1'; parent.emit('change');
  assert.equal(start.value, '2026-06-01');
  assert.match(note.textContent, /日期已锁定/);
  reset.emit('click');
  assert.equal(start.value, '2024-02-29');
  assert.equal(helper.payload(form, {}).reset_start_date, true);
  tasks[0].due_date = '2028-01-01'; form._taskDates.refresh();
  assert.equal(start.value, '2024-02-29');
  parent.value = '2'; parent.emit('change');
  assert.equal(start.value, '2024-02-29');
  assert.equal(helper.payload(form, {}).reset_start_date, undefined);
  assert.equal(helper.payload(form, {}).start_date_origin, 'manual');
});

test('retry retains customization, form reset clears it, duplicate binding does not replace state', async () => {
  const {helper, form, parent, start, note} = fixture();
  start.value = '2026-08-12'; start.emit('input');
  helper.bind(form, {saved: false});
  assert.equal(helper.payload(form, {}).start_date_origin, 'manual');
  assert.equal(helper.payload(form, {}).start_date_origin, 'manual');
  form.emit('reset'); start.value = ''; parent.value = '';
  await Promise.resolve();
  assert.equal(helper.payload(form, {}).start_date_origin, undefined);
  assert.match(note.textContent, /首次保存后固定/);
});
