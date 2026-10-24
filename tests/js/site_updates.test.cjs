const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const script = fs.readFileSync(path.join(__dirname, '../../static/site-updates.js'), 'utf8');
const first = 'a'.repeat(40);
const second = 'b'.repeat(40);

function setup(previous = '') {
  let loaded;
  let interval;
  let respond = { available: true, commit: first, latest_subject: '第一版更新' };
  const values = new Map();
  if (previous) values.set('xtyf-site-release-seen:7', previous);
  const listeners = {};
  const detailListeners = {};
  const closeListeners = {};
  const banner = {
    hidden: true,
    dataset: { userId: '7', siteUpdateApi: '/api/site-release' },
    querySelector(selector) {
      if (selector === '[data-site-update-close]') return { addEventListener: (name, fn) => closeListeners[name] = fn };
      if (selector === '[data-site-update-detail]') return { addEventListener: (name, fn) => detailListeners[name] = fn };
      if (selector === '[data-site-update-summary]') return summary;
      return null;
    },
  };
  const summary = { textContent: '' };
  const document = {
    hidden: false,
    querySelector: selector => selector === '[data-site-update-banner]' ? banner : null,
    addEventListener: (name, fn) => { if (name === 'DOMContentLoaded') loaded = fn; else listeners[name] = fn; },
  };
  const window = {
    localStorage: { getItem: key => values.get(key) || null, setItem: (key, value) => values.set(key, value) },
    setInterval: fn => { interval = fn; },
    addEventListener: (name, fn) => { listeners[name] = fn; },
  };
  vm.runInNewContext(script, { document, window, fetch: async () => ({
    ok: true, json: async () => respond,
  }) });
  loaded();
  return { banner, summary, values, clickClose: () => closeListeners.click(), clickDetail: () => detailListeners.click(),
    poll: () => interval(), change: value => { respond = value; } };
}

test('announces initial release and stores dismissed commit per member', async () => {
  const app = setup();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(app.banner.hidden, false);
  assert.equal(app.summary.textContent, '第一版更新');
  app.clickClose();
  assert.equal(app.banner.hidden, true);
  assert.equal(app.values.get('xtyf-site-release-seen:7'), first);
  app.change({ available: true, commit: second, latest_subject: '第二版更新' });
  await app.poll();
  assert.equal(app.banner.hidden, false);
  assert.equal(app.summary.textContent, '第二版更新');
  app.clickDetail();
  assert.equal(app.values.get('xtyf-site-release-seen:7'), second);
});

test('previously acknowledged release stays hidden', async () => {
  const app = setup(first);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(app.banner.hidden, true);
  app.change({ available: false, commit: null });
  await app.poll();
  assert.equal(app.banner.hidden, true);
});

test('timeline renders cached commit subject as text without HTML interpolation', async () => {
  let loaded;
  const node = (tag) => ({
    tag, children: [], textContent: '', className: '', dataset: {},
    append(...children) { this.children.push(...children); },
    replaceChildren(...children) { this.children = children; },
    setAttribute() {},
  });
  const root = node('div');
  root.dataset.siteUpdateHistoryApi = '/api/site-release/history';
  const document = {
    querySelector: selector => selector === '[data-site-update-timeline]' ? root : null,
    addEventListener(name, fn) { if (name === 'DOMContentLoaded') loaded = fn; },
    createElement: node,
  };
  const subject = '<img src=x onerror=alert(1)>';
  vm.runInNewContext(script, {
    document,
    window: {},
    fetch: async () => ({ ok: true, json: async () => ({
      available: true, commit: first, built_at: '2026-10-23T09:00:00Z',
      commits: [{ sha: first, author: 'Tester', date: '2026-10-23T12:30:00+08:00', subject }],
    }) }),
  });
  loaded();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(root.children.length, 2);
  const allNodes = [];
  const visit = current => { allNodes.push(current); current.children.forEach(visit); };
  root.children.forEach(visit);
  assert.equal(allNodes.find(x => x.tag === 'p' && x.textContent === subject).textContent, subject);
  assert.equal(allNodes.find(x => x.tag === 'a').href,
    'https://github.com/SHM-white/xtyf-manager/commit/' + first);
});
