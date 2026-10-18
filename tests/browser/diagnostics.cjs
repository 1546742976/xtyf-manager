'use strict';
const fs = require('node:fs');
const path = require('node:path');

// Persist the first failure before touching a potentially crashed browser.
async function recordFailure(error, {output, page, engine, phase, passed, pageErrors, calls}, report = console.error) {
  const result = {
    ok: false, engine, phase, nativeRuntime: false,
    error: {name: error.name, message: error.message, stack: error.stack},
    passed, errors: pageErrors,
    calls: (calls || []).slice(-100).map(call => ({action: call.action, path: (call.args?.path || '').split('?')[0]})),
    diagnostics: [],
  };
  const attempt = async (name, action) => {
    try { await action(); }
    catch (failure) { result.diagnostics.push({name, error: failure.message}); report(name + ': ' + failure.message); }
  };
  const save = () => fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify(result, null, 2));
  await attempt('save original failure', save);
  if (page) {
    await attempt('screenshot', () => page.screenshot({path: path.join(output, 'failure.png')}));
    await attempt('shell state', async () => {
      result.shell = await page.evaluate(() => ({
        route: window.XTYF?.route, compatible: window.XTYF?.compatible,
        heading: document.querySelector('h1')?.textContent,
      }));
    });
  }
  await attempt('save diagnostics', save);
  return result;
}

async function cleanupResources(resources, report = console.error) {
  const entries = Object.entries(resources);
  const results = await Promise.allSettled(entries.map(([, close]) => Promise.resolve().then(close)));
  const failures = [];
  results.forEach((result, index) => {
    if (result.status === 'rejected') {
      const failure = {name: entries[index][0], error: result.reason?.message || String(result.reason)};
      failures.push(failure);
      report('Cleanup ' + failure.name + ': ' + failure.error);
    }
  });
  return failures;
}

module.exports = {recordFailure, cleanupResources};
