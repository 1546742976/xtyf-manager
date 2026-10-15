"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const root = path.resolve(__dirname, "../..");
const script = fs.readFileSync(path.join(root, "static/invoices.js"), "utf8");
const template = fs.readFileSync(path.join(root, "templates/invoices.html"), "utf8");

function invoicePage(invoice) {
  const elements = new Map();
  const element = (id) => {
    if (!elements.has(id)) elements.set(id, {
      value: "", checked: false, innerHTML: "", textContent: "",
      style: {}, classList: { add() {}, remove() {} },
      options: [{ value: "" }], appendChild() {}, addEventListener() {},
    });
    return elements.get(id);
  };
  const context = {
    console, URLSearchParams, encodeURIComponent,
    setTimeout() {}, clearTimeout() {},
    IS_FINANCE: false, ME_ID: 1, ME_NAME: "测试成员",
    Money: { compare() { return 1; }, format(value) { return value; } },
    document: { getElementById: element, querySelectorAll() { return []; } },
    fetch: async () => ({ ok: true, json: async () => ({
      data: [invoice], page: 1, total: 1, per_page: 50,
    }) }),
  };
  context.window = context;
  context.BusinessUI = {fetch: context.fetch};
  vm.createContext(context);
  // Skip app startup (which triggers unrelated stats queries); keep all
  // actual rendering, escaping and openFile functions under test.
  const startup = script.indexOf("window.IS_FINANCE=window.IS_FINANCE||false");
  const afterStartup = script.indexOf("function invoiceOwnerOptions(");
  assert.ok(startup > 0 && afterStartup > startup);
  vm.runInContext(script.slice(0, startup) + script.slice(afterStartup), context,
    { filename: "invoices.js" });
  return { context, element };
}

test("invoice detail month and date remain editable and are sent to update API", () => {
  assert.match(template, /id="detMonth"[^>]*placeholder="YYYY\.M"/);
  assert.match(template, /type="date" id="detDate"/);
  assert.match(script, /month:document\.getElementById\('detMonth'\)\.value\.trim\(\)/);
  assert.match(script, /date:document\.getElementById\('detDate'\)\.value/);
});

test("invoice row passes a safely escaped raw path to delegated openFile handler", async () => {
  const { context, element } = invoicePage({
    id: '2026.10/未报销/发票 50% #测试.pdf',
    rel_path: '2026.10/未报销/发票 50% #测试.pdf',
    month: "2026.10", date: "2026-10-03", status: "未报销",
    vendor: "测试", amount: "12.50", category: "其它", source: "人工确认",
    submitter: "测试成员", submitter_id: 1,
  });
  await vm.runInContext("loadInvoices(1)", context);
  const html = element("invoiceTableBody").innerHTML;
  assert.match(html, /data-invoice-file="2026\.10\/未报销\/发票 50% #测试\.pdf"/);
  assert.doesNotMatch(html, /%252F|%2F/);
  assert.equal(vm.runInContext("encodeURIComponent('2026.10/未报销/发票 50% #测试.pdf')", context),
    "2026.10%2F%E6%9C%AA%E6%8A%A5%E9%94%80%2F%E5%8F%91%E7%A5%A8%2050%25%20%23%E6%B5%8B%E8%AF%95.pdf");
});

test("invoice row does not turn hostile filenames into HTML attributes", async () => {
  const hostile = '2026.10/未报销/a" onmouseover="alert(1).pdf';
  const { context, element } = invoicePage({
    id: hostile, rel_path: hostile, month: "2026.10", date: "",
    status: "未报销", vendor: "安全检查", amount: "0.00",
    submitter: "", category: "", source: "",
  });
  await vm.runInContext("loadInvoices(1)", context);
  const html = element("invoiceTableBody").innerHTML;
  assert.match(html, /&quot; onmouseover=&quot;/);
  assert.doesNotMatch(html, /openFile\('.*" onmouseover="/);
});
