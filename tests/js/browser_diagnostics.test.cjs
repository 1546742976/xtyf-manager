'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {test} = require('node:test');
const {recordFailure, cleanupResources} = require('../browser/diagnostics.cjs');

test('crashed browser diagnostics preserve the first error and continue after screenshot failure', async () => {
  const output = fs.mkdtempSync(path.join(os.tmpdir(), 'xtyf-diagnostics-'));
  try {
    let evaluated = false;
    const original = Error('page.reload: Page crashed');
    const result = await recordFailure(original, {
      output, engine: 'webkit', phase: 'incompatible bootstrap reload', passed: [], pageErrors: [],
      calls: [{action: 'request', args: {path: '/api/tasks?token=not-for-diagnostics', body: 'private'}}],
      page: {
        screenshot() {
          assert.equal(JSON.parse(fs.readFileSync(path.join(output, 'result.json'))).error.message, original.message);
          throw Error('Target crashed');
        },
        evaluate() { evaluated = true; throw Error('Target closed'); },
      },
    }, () => {});
    assert.equal(evaluated, true);
    assert.equal(result.error.message, original.message);
    assert.deepEqual(result.diagnostics.map(item => item.name), ['screenshot', 'shell state']);
    const persisted = JSON.parse(fs.readFileSync(path.join(output, 'result.json')));
    assert.equal(persisted.error.message, original.message);
    assert.deepEqual(persisted.calls, [{action: 'request', path: '/api/tasks'}]);
    assert.equal(JSON.stringify(persisted).includes('not-for-diagnostics'), false);
  } finally { fs.rmSync(output, {recursive: true, force: true}); }
});

test('each resource is cleaned even when another close throws or rejects', async () => {
  const closed = [];
  const failures = await cleanupResources({
    browser: () => { closed.push('browser'); throw Error('crashed'); },
    api: async () => { closed.push('api'); throw Error('disposed'); },
    backend: () => closed.push('backend'),
    server: async () => closed.push('server'),
  }, () => {});
  assert.deepEqual(closed, ['browser', 'api', 'backend', 'server']);
  assert.deepEqual(failures.map(item => item.name), ['browser', 'api']);
});
