'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const context = {window:{}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../static/money.js'), 'utf8'), context);
test('invoice input uses decimal cents, including HALF_UP and scientific notation', () => {
  for (const [text, expected] of [['0.004',0n],['0.005',1n],['1.005',101n],['5e-3',1n],['1e2',10000n],['.01',1n],['92233720368547758.07',9223372036854775807n]])
    assert.equal(context.window.Money.inputCents(text), expected, text);
});
test('invoice input rejects empty, negative, nonfinite and oversized amounts', () => {
  for (const text of ['', ' ', '-1', 'NaN', 'Infinity', '1e99999', '92233720368547758.08'])
    assert.throws(() => context.window.Money.inputCents(text), undefined, text);
});
