const { test } = require('node:test');
const assert = require('node:assert/strict');
const { normalizeServer } = require('./url.cjs');
test('accepts HTTP, HTTPS, LAN and explicit ports', () => {
  for (const url of ['http://manager.skywalker-rm.online', 'https://example.com', 'http://192.168.1.8:8520']) assert.equal(normalizeServer(` ${url}/ `), url);
});
test('rejects credentials, scripts and non-root deployment URLs', () => {
  for (const url of ['javascript:alert(1)', 'file:///a', 'https://user:pass@example.com', 'https://example.com/a', 'https://example.com/?a=b', 'https://example.com/#a', 'example.com', '']) assert.throws(() => normalizeServer(url));
});
