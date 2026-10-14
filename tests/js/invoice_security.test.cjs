"use strict";
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

test('invoice row delegates raw paths safely and preview encodes exactly once', async () => {
  const source = fs.readFileSync(path.join(__dirname, '../../static/invoices.js'), 'utf8');
  const elements = {};
  const element = id => elements[id] ||= {value: '', style: {}, checked: false, innerHTML: '',
    listeners: {}, addEventListener(type, callback) { this.listeners[type] = callback; }};
  const raw = `2026.10/未报销/商家'-alert(1)-'% +\"<test>.pdf`;
  const invoice = {id: raw, rel_path: raw, month: '2026.10', amount: '1.00',
    status: '未报销', source: '人工确认', submitter: 'member', submitter_id: 2,
    vendor: '<vendor>', invoice_number: '', date: '', category: 'other'};
  let requested, opened;
  const ctx = {console, URLSearchParams, encodeURIComponent, setTimeout() {}, clearTimeout() {},
    IS_FINANCE: true, Money: {compare: () => 1, format: value => value},
    document: {getElementById: element}, fetch: async url => {
      requested = url; return {ok: true, json: async () => url.includes('open_file')
        ? {success: false} : {data: [invoice], total: 1, per_page: 50}};
    }};
  vm.createContext(ctx);
  vm.runInContext(source.slice(0, source.indexOf('// ====== 拖入')), ctx);
  ctx.renderPagination = () => {}; ctx.updateBatchBar = () => {};
  await ctx.loadInvoices(1);
  const row = element('invoiceTableBody').innerHTML;
  assert.ok(!/\bon(?:click|change)\s*=/.test(row));
  assert.ok(row.includes('&#39;')); assert.ok(row.includes('&quot;'));
  assert.ok(!row.includes('<vendor>'));
  ctx.openFile = value => {opened = value;};
  ctx.bindInvoiceTable();
  element('invoiceTableBody').listeners.click({target: {closest: selector =>
    selector === '[data-invoice-file]' ? {dataset: {invoiceFile: raw}} : null}});
  assert.equal(opened, raw);
  vm.runInContext(source.slice(source.indexOf('async function openFile('),
                              source.indexOf('function openFolder(')), ctx);
  await ctx.openFile(raw);
  assert.equal(new URL(requested, 'http://localhost').searchParams.get('rel_path'), raw);
});
