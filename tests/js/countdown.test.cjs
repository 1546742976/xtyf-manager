"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../../static/countdown.js"), "utf8");
const config = (values = {}) => ({title:"完整形态考核", target_date:"2026-10-20", version:1, configured:true, ...values});
const response = (countdown, status = 200, error = "失败") => ({ok:status < 400, status,
  json:async () => ({ok:status < 400, countdown, error})});
const flush = () => new Promise(resolve => setImmediate(resolve));

function harness(responses = []) {
  const documentEvents = {}, intervals = [], elements = new Map(), calls = [];
  function classes() {
    const items = new Set();
    return {add:name => items.add(name), remove:name => items.delete(name),
      contains:name => items.has(name), toggle(name, force) { if (force) items.add(name); else items.delete(name); }};
  }
  const document = {hidden:false, activeElement:null, body:{classList:classes()},
    addEventListener(name, fn) { documentEvents[name] = fn; },
    getElementById(id) { return elements.get(id); }};
  for (const id of ["workspace-countdown", "countdown-result", "countdown-retry", "countdown-dialog", "countdown-config",
    "countdown-form", "countdown-title-input", "countdown-date-input", "countdown-form-result", "countdown-save",
    "countdown-reload", "countdown-cancel", "countdown-title", "countdown-eyebrow", "countdown-date", "countdown-value",
    "countdown-unit", "countdown-number"]) {
    const listeners = {};
    const element = {id, value:"", textContent:"", disabled:false, hidden:id === "workspace-countdown", open:false, classList:classes(),
      addEventListener(name, fn) { listeners[name] = fn; },
      setAttribute() {}, toggleAttribute(name, enabled) { this[name] = enabled; },
      focus() { document.activeElement = this; }, showModal() { this.open = true; },
      close() { this.open = false; listeners.close?.(); },
      trigger(name) { return listeners[name]?.({preventDefault() {}}); }};
    elements.set(id, element);
  }
  const context = {document, Date, console, setInterval(fn) { intervals.push(fn); },
    BusinessUI:{fetch:async (url, options) => {
      calls.push({url, options});
      const next = responses.shift();
      if (next instanceof Error) throw next;
      return await next;
    }}};
  context.window = context;
  vm.createContext(context);
  vm.runInContext(source, context);
  return {api:context.WorkspaceCountdown, document, events:documentEvents, intervals, calls,
    element:id => elements.get(id), responses};
}

test("Beijing calendar changes at 16:00 UTC independently of device timezone", () => {
  const {api} = harness();
  assert.equal(api.beijingDate(new Date("2026-10-15T15:59:59Z")), "2026-10-15");
  assert.equal(api.beijingDate(new Date("2026-10-15T16:00:00Z")), "2026-10-16");
  assert.equal(api.beijingDate(new Date("2026-10-16T08:00:00-07:00")), "2026-10-16");
  assert.equal(api.countdownView(config(), new Date("2026-10-15T16:01:00Z")).days, 4);
  assert.equal(api.countdownView(config(), new Date("2026-10-16T15:59:59Z")).days, 4);
});

test("today, elapsed dates, leap years and unconfigured targets use precise copy", () => {
  const {api} = harness();
  const today = api.countdownView(config(), new Date("2026-10-20T00:00:00Z"));
  assert.equal(today.number, "今天"); assert.equal(today.unit, ""); assert.equal(today.days, 0);
  const past = api.countdownView(config(), new Date("2026-10-22T00:00:00Z"));
  assert.equal(past.number, "日期已过"); assert.equal(past.description, "目标日期已过 2 天");
  assert.doesNotMatch(past.description, /结束/);
  assert.equal(api.countdownView(config({target_date:"2028-03-01"}), new Date("2028-02-28T01:00:00Z")).days, 2);
  assert.equal(api.countdownView(config({configured:false})), null);
  for (const value of ["2026-02-30", "2026-2-01", "0000-01-01", "2026-13-01", "", null]) {
    assert.equal(api.dateOrdinal(value), null);
    assert.equal(api.countdownView(config({target_date:value})), null);
  }
  assert.equal(api.dateOrdinal("0099-01-02") - api.dateOrdinal("0099-01-01"), 1);
});

test("GET failure stays visible as a failure and explicit reload can recover", async () => {
  const page = harness([new Error("网络连接中断"), response(config())]);
  page.api.init(); await flush();
  assert.match(page.element("countdown-result").textContent, /网络连接中断/);
  assert.equal(page.element("workspace-countdown").hidden, true);
  assert.equal(page.element("countdown-retry").hidden, false);
  page.element("countdown-config").trigger("click");
  assert.equal(page.element("countdown-save").disabled, true);
  await page.element("countdown-reload").trigger("click");
  assert.equal(page.element("countdown-save").disabled, false);
  assert.equal(page.element("countdown-title-input").value, "完整形态考核");
});

test("save HTTP failure preserves inputs and a successful save closes with focus restored", async () => {
  const page = harness([response(config()), response(null, 500, "保存失败"),
    response(config({title:"新目标", version:2}))]);
  page.api.init(); await flush();
  const open = page.element("countdown-config"); open.focus(); open.trigger("click");
  page.element("countdown-title-input").value = "新目标";
  await page.element("countdown-form").trigger("submit");
  assert.equal(page.element("countdown-title-input").value, "新目标");
  assert.equal(page.element("countdown-save").disabled, false);
  assert.equal(page.element("countdown-dialog").open, true);
  assert.match(page.element("countdown-form-result").textContent, /保存失败.*输入已保留/);
  await page.element("countdown-form").trigger("submit");
  assert.equal(page.element("countdown-dialog").open, false);
  assert.equal(page.document.activeElement, open);
  assert.equal(JSON.parse(page.calls[2].options.body).title, "新目标");
});

test("409 preserves draft and blocks save until user explicitly loads newest configuration", async () => {
  const page = harness([response(config()), response(null, 409), response(config({title:"他人目标", version:5})),
    response(config({title:"重新确认目标", version:6}))]);
  page.api.init(); await flush();
  page.element("countdown-config").trigger("click");
  page.element("countdown-title-input").value = "我的草稿";
  await page.element("countdown-form").trigger("submit");
  assert.equal(page.element("countdown-title-input").value, "我的草稿");
  assert.equal(page.element("countdown-save").disabled, true);
  assert.equal(page.element("countdown-reload").hidden, false);
  await page.element("countdown-form").trigger("submit");
  assert.equal(page.calls.length, 2);
  await page.element("countdown-reload").trigger("click");
  assert.equal(page.element("countdown-title-input").value, "他人目标");
  assert.equal(page.element("countdown-save").disabled, false);
  page.element("countdown-title-input").value = "重新确认目标";
  await page.element("countdown-form").trigger("submit");
  assert.equal(JSON.parse(page.calls[3].options.body).version, 5);
});

test("pending writes cannot duplicate, and unknown results require verification", async () => {
  let reject;
  const pending = new Promise((resolve, fail) => { reject = fail; });
  const page = harness([response(config()), pending]);
  page.api.init(); await flush();
  page.element("countdown-config").trigger("click");
  page.element("countdown-title-input").value = "草稿";
  const saving = page.element("countdown-form").trigger("submit");
  await page.element("countdown-form").trigger("submit");
  assert.equal(page.calls.length, 2);
  assert.equal(page.element("countdown-save").disabled, true);
  reject(new Error("请求超时，结果尚未确认")); await saving;
  assert.equal(page.element("countdown-title-input").value, "草稿");
  assert.equal(page.element("countdown-save").disabled, true);
  assert.equal(page.element("countdown-reload").hidden, false);
});

test("minute and visibility updates never reload or replace an open editor", async () => {
  const page = harness([response(config())]);
  page.api.init(); await flush();
  page.element("countdown-config").trigger("click");
  page.element("countdown-title-input").value = "尚未保存";
  page.intervals[0](); page.events.visibilitychange();
  assert.equal(page.calls.length, 1);
  assert.equal(page.element("countdown-title-input").value, "尚未保存");
  assert.equal(page.element("countdown-dialog").open, true);
});
