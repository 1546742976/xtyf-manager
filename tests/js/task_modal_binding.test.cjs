"use strict";
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

function node(extra = {}) {
  return {events: {}, addEventListener(event, callback) { this.events[event] = callback; }, ...extra};
}

// Document order mirrors templates/base.html: <main> with the task table precedes #modal,
// and static/manage-tasks.js renders a [data-delete-task] button in every table row.
function fixture() {
  const tableDelete = node({dataset: {deleteTask: '2'}, isConnected: true});
  const modalClose = node({isConnected: true});
  const modalDelete = node({isConnected: true});
  const modal = {
    dataset: {taskId: '7', generation: '1'},
    contains: () => true,
    querySelector(selector) {
      if (selector === '[data-close]') return modalClose;
      if (selector === '[data-delete-task]') return modalDelete;
      return null;
    },
  };
  const calls = [];
  const context = {
    document: {
      getElementById: (id) => (id === 'modal' ? modal : null),
      querySelector(selector) {
        if (selector === '[data-close]') return modalClose;
        if (selector === '[data-delete-task]') return tableDelete; // first match in document order
        return null;
      },
      querySelectorAll: () => [],
    },
    state: {tasks: [{id: 7, title: 'Task', version: 3}], members: [], allMembers: []},
    bindTaskProgress() {},
    confirm: () => true,
    alert: (message) => calls.push(['alert', message]),
    refresh: async () => { calls.push(['refresh']); },
    hideModal: () => { calls.push(['hideModal']); },
    api: async (url, payload) => { calls.push(['api', url, payload]); return {ok: true}; },
  };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../../static/task-modal.js'), 'utf8'), context);
  return {context, tableDelete, modalDelete, modalClose, calls};
}

test('task modal delete binds to the modal button, never to a table row button', () => {
  const {context, tableDelete, modalDelete} = fixture();
  context.bindTaskActions({id: 7, version: 3}, 1);
  assert.equal(tableDelete.events.click, undefined,
    'the task table row button must not carry the modal delete handler');
  assert.equal(typeof modalDelete.events.click, 'function',
    'the modal delete button must carry the delete handler');
});

test('the modal delete button deletes the task the modal shows', async () => {
  const {context, modalDelete, calls} = fixture();
  context.bindTaskActions({id: 7, version: 3}, 1);
  await modalDelete.events.click({currentTarget: modalDelete});
  assert.equal(calls[0][0], 'api');
  assert.equal(calls[0][1], '/api/task/7/delete');
  assert.equal(calls[0][2].version, 3);
  assert.deepEqual(calls.slice(1).map((call) => call[0]), ['hideModal', 'refresh']);
});

test('the modal close button stays bound to the modal, not to an outer control', () => {
  const {context, modalDelete} = fixture();
  context.bindTaskActions({id: 7, version: 3}, 1);
  assert.equal(typeof modalDelete.events.click, 'function');
});
