"use strict";
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

function setup() {
  const requests = [], listeners = {}, classes = new Set(['active']);
  const image = {style: {}, src: ''}, empty = {style: {}, textContent: ''};
  const button = {classList: {add() {}, remove() {}}};
  const nodes = {chartImg: image, chartEmp: empty, 't-charts': {classList: {contains: (v) => classes.has(v)}}};
  const context = {URLSearchParams, AbortController, setTimeout, clearTimeout,
    XTYF_USER: {name: 'Admin'},
    document: {documentElement: {dataset: {palette: 'silver'}},
      addEventListener(name, callback) { listeners[name] = callback; },
      querySelectorAll() { return [button]; }, querySelector() { return button; },
      getElementById(id) { return nodes[id]; }},
    BusinessUI: {guard(fn) { return fn; }, fetch(url, options) { return new Promise((resolve, reject) => requests.push({url, options, resolve, reject})); }}};
  context.window = context;
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../../static/funds.js'), 'utf8'), context);
  const resolve = (index, data) => requests[index].resolve({json: async () => ({code: 0, data})});
  const tick = () => new Promise(setImmediate);
  const theme = (palette) => { context.document.documentElement.dataset.palette = palette; listeners['workspace-theme-change'](); };
  return {context, requests, classes, image, empty, resolve, tick, theme};
}

test('theme event reloads only the visible selected chart with resolved palette', async () => {
  const f = setup();
  const first = f.context.loadChart('source');
  assert.match(f.requests[0].url, /type=source&palette=silver/);
  f.resolve(0, 'old-png'); await first;
  f.theme('xueli');
  assert.equal(f.requests.length, 2);
  assert.match(f.requests[1].url, /type=source&palette=xueli/);
  assert.equal(f.image.style.display, 'none');
  f.resolve(1, 'new-png'); await f.tick();
  assert.equal(f.image.src, 'new-png');
  f.theme('xueli'); assert.equal(f.requests.length, 2, 'duplicate apply does not request again');
});

test('out-of-order chart and theme responses never overwrite newest chart', async () => {
  const f = setup();
  const old = f.context.loadChart('monthly');
  f.theme('graphite');
  assert.equal(f.requests[0].options.signal.aborted, true);
  f.resolve(1, 'gold-png'); await f.tick();
  f.resolve(0, 'stale-png'); await old;
  assert.equal(f.image.src, 'gold-png');
  const category = f.context.loadChart('category');
  f.theme('paw');
  f.requests[2].reject(new Error('old request aborted'));
  await category;
  assert.doesNotMatch(f.empty.textContent, /old request aborted/);
  f.resolve(3, 'paw-png'); await f.tick();
  assert.equal(f.image.src, 'paw-png');
});

test('hidden charts are invalidated without unrelated fetch; first uninitialized chart does not load', async () => {
  const f = setup();
  f.theme('space'); assert.equal(f.requests.length, 0);
  const pending = f.context.loadChart('applicant');
  f.classes.delete('active');
  f.theme('rm-dark'); assert.equal(f.requests.length, 1);
  assert.equal(f.context.chartDirty, true);
  f.resolve(0, 'stale-hidden'); await pending;
  assert.equal(f.image.src, '');
  f.classes.add('active');
  const current = f.context.loadChart(f.context.ct);
  assert.match(f.requests[1].url, /type=applicant&palette=rm-dark/);
  f.resolve(1, 'current'); await current;
  assert.equal(f.context.chartDirty, false);
  assert.equal(f.image.src, 'current');
});
