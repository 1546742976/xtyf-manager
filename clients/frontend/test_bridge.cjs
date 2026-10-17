const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const source = name => fs.readFileSync(path.join(__dirname, name), 'utf8');

function loadBridge(nativeInvoke, existing = false) {
  const listeners = new Map();
  const context = vm.createContext({
    Error, Promise, Map, setTimeout, clearTimeout, URL, DOMException,
    addEventListener: (name, callback) => listeners.set(name, callback),
  });
  context.window = context;
  if (existing) context.XTYFNative = { invoke: nativeInvoke };
  else context.__TAURI_INTERNALS__ = { invoke: nativeInvoke };
  vm.runInContext(source('bridge.js'), context, { filename: 'bridge.js' });
  return { context, listeners };
}

for (const [label, failure, expected] of [
  ['string', '禁止向站外地址转发业务请求', '禁止向站外地址转发业务请求'],
  ['object', { message: '证书验证失败' }, '证书验证失败'],
  ['Error', new Error('请求超时'), '请求超时'],
  ['unknown object', { code: 7 }, '原生操作失败'],
]) {
  test(`real Tauri bridge normalizes ${label} rejection`, async () => {
    const { context } = loadBridge(async (command, payload) => {
      assert.equal(command, 'native_call');
      assert.equal(payload.action, 'request');
      throw failure;
    });
    await assert.rejects(context.XTYFNative.invoke('request', { path: '/api/client/bootstrap' }), error => {
      assert.ok(error instanceof Error);
      assert.equal(error.message, expected);
      if (failure instanceof Error) assert.equal(error, failure);
      return true;
    });
  });
}

test('existing native transport receives the same error normalization', async () => {
  const { context } = loadBridge(async () => { throw '本地请求失败'; }, true);
  await assert.rejects(context.XTYFNative.invoke('request'), error => error instanceof Error && error.message === '本地请求失败');
});

test('native success response is preserved', async () => {
  const response = { status: 200, body: '{}' };
  const { context } = loadBridge(async () => response);
  assert.equal(await context.XTYFNative.invoke('request'), response);
});

function element(tag = 'div') {
  return {
    tag, children: [], textContent: '', hidden: true,
    setAttribute() {}, addEventListener() {}, remove() {},
    append(...children) { this.children.push(...children); },
    replaceChildren(...children) { this.children = children; },
    querySelector() { return this.span ||= element('span'); },
  };
}

async function startShell(bootstrap) {
  const elements = new Map();
  const get = id => {
    if (!elements.has(id)) elements.set(id, element());
    return elements.get(id);
  };
  const { context } = loadBridge(async (_command, { action }) => {
    if (action === 'get_config') return { serverUrl: 'https://server.invalid', version: 'test' };
    return bootstrap();
  });
  Object.assign(context, {
    document: { getElementById: get, createElement: element },
    location: { protocol: 'https:', host: 'tauri.localhost', hash: '' },
    history: { replaceState() {}, pushState() {} },
    XTYFTemplates: { render: () => '<html><head></head><body></body></html>' },
  });
  vm.runInContext(source('shell.js'), context, { filename: 'shell.js' });
  await new Promise(resolve => setImmediate(resolve));
  return { context, get };
}

test('local bootstrap failure displays the actual native error', async () => {
  const { get } = await startShell(async () => { throw '禁止向站外地址转发业务请求'; });
  assert.match(get('connection-status').querySelector('span').textContent, /禁止向站外地址转发业务请求/);
  assert.equal(get('page-host').children[0].tag, 'iframe');
});

test('old-server 404 still shows local server-update page', async () => {
  const { get } = await startShell(async () => ({ status: 404, body: '<html>Old login page</html>' }));
  assert.equal(get('page-host').children[0].children[0].textContent, '服务器需要更新');
});
