"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const ROOT = path.resolve(__dirname, "../..");
const flush = () => new Promise((resolve) => setImmediate(resolve));

function fixture() {
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) {
      const handlers = {};
      elements.set(id, {
        value: "all", textContent: "", innerHTML: "", hidden: false, disabled: false,
        handlers,
        addEventListener(type, fn) { handlers[type] = fn; },
        dispatch(type) { assert.ok(handlers[type], id + " missing " + type); return handlers[type]({target:this}); },
        insertAdjacentHTML(_position, html) { this.innerHTML += html; },
        querySelectorAll() { return []; },
      });
    }
    return elements.get(id);
  }
  element("audit-level").value = "priority";
  const form = element("audit-retention-form");
  form.elements = Object.fromEntries(["critical","important","normal","routine"]
    .map((level) => [level, {value:""}]));
  const requests = [];
  const BusinessUI = {
    escape: (value) => String(value ?? "").replace(/[&<>"']/g,
      (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char])),
    fetch(url) {
      if (url.startsWith("/api/admin/audit?")) {
        return new Promise((resolve) => requests.push({url, resolve: (events, next_before = null) => resolve({
          ok:true, json: async () => ({ok:true, events, next_before}),
        })}));
      }
      const policy = {
        ok:true, days:{critical:90, important:180, normal:365, routine:30},
        sources:{critical:"default",important:"default",normal:"default",routine:"default"},
      };
      const payload = url === "/api/admin/permissions"
        ? {ok:true,members:[],permissions:{}} : policy;
      return Promise.resolve({ok:true,json:async()=>payload});
    },
  };
  const document = {
    addEventListener(type, fn) { if (type === "DOMContentLoaded") fn(); },
    getElementById: element,
  };
  const context = {document, BusinessUI, URLSearchParams, console};
  vm.runInNewContext(fs.readFileSync(path.join(ROOT,"static","security-admin.js"),"utf8"),
    context, {filename:"security-admin.js"});
  const event = (name, category, outcome="success") => ({
    created_at:"2026-10-31", actor_name:"管理员", actor_id:1,
    action:name, method:"POST", target:"/api/test", category,
    level:category==="finance"?"permanent":"normal", outcome, details:"{}", http_status:200,
  });
  return {element, requests, event};
}

test("rapid category and outcome changes during initial load keep only latest results", async () => {
  const {element, requests, event} = fixture();
  assert.equal(requests.length,1);
  assert.equal(new URL(requests[0].url,"http://local").searchParams.get("category"),"all");
  element("audit-category").value = "finance";
  element("audit-category").dispatch("change");
  element("audit-outcome").value = "failure";
  element("audit-outcome").dispatch("change");
  requests[0].resolve([event("old-task-record","tasks")],77);
  await flush();
  assert.equal(requests.length,2, "latest filters must be fetched after stale request");
  const url = new URL(requests[1].url,"http://local");
  assert.equal(url.searchParams.get("category"),"finance");
  assert.equal(url.searchParams.get("outcome"),"failure");
  assert.equal(url.searchParams.has("before"),false,"stale paging cursor must be reset");
  assert.doesNotMatch(element("audit-events").innerHTML,/old-task-record/);
  requests[1].resolve([event("new-finance-record","finance","failure")],12);
  await flush();
  assert.match(element("audit-events").innerHTML,/new-finance-record/);
  assert.doesNotMatch(element("audit-events").innerHTML,/old-task-record/);
  element("audit-more").dispatch("click");
  assert.equal(requests.length,3);
  const page = new URL(requests[2].url,"http://local").searchParams;
  assert.equal(page.get("category"),"finance");
  assert.equal(page.get("outcome"),"failure");
  assert.equal(page.get("before"),"12");
  requests[2].resolve([],null);
  await flush();
});

test("filter change during load-more cannot append records from old selection", async () => {
  const {element, requests, event} = fixture();
  requests[0].resolve([event("initial-task","tasks")],99);
  await flush();
  element("audit-more").dispatch("click");
  assert.equal(requests.length,2);
  element("audit-category").value="finance";
  element("audit-category").dispatch("change");
  requests[1].resolve([event("stale-paged-task","tasks")],88);
  await flush();
  assert.equal(requests.length,3);
  const url = new URL(requests[2].url,"http://local").searchParams;
  assert.equal(url.get("category"),"finance");
  assert.equal(url.has("before"),false);
  assert.doesNotMatch(element("audit-events").innerHTML,/stale-paged-task|initial-task/);
  requests[2].resolve([event("current-finance","finance")]);
  await flush();
  assert.match(element("audit-events").innerHTML,/current-finance/);
  assert.doesNotMatch(element("audit-events").innerHTML,/stale-paged-task|initial-task/);
  assert.equal(element("audit-more").hidden,true);
});
