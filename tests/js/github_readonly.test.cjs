"use strict";
const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

test("GitHub collaboration board fetches read-only data and opens GitHub for participation", async () => {
  const script = fs.readFileSync(path.resolve(__dirname, "../../static/discussions.js"), "utf8");
  assert.doesNotMatch(script, /\/discussions\/api\/post/);
  const listeners = {};
  const root = { dataset: {
    newIssue: "https://github.com/SHM-white/xtyf-manager/issues/new/choose",
    newDiscussion: "https://github.com/SHM-white/xtyf-manager/discussions/new/choose",
  }, addEventListener: (type, fn) => { listeners[type] = fn; } };
  const list = { innerHTML: "" }, detail = { innerHTML: "" };
  const button = { href: "", textContent: "" };
  const note = { textContent: "" }, pages = { hidden: false };
  const prev = { disabled: true, addEventListener() {} };
  const next = { disabled: false, addEventListener() {} };
  const number = { textContent: "" };
  const elements = {
    "github-hub": root, "github-topic-list": list, "github-topic-detail": detail,
    "github-new": button, "github-prev": prev, "github-next": next,
    "github-page": number, "github-source-note": note,
  };
  const tabs = ["issues", "discussions"].map((kind) => ({
    dataset: { githubKind: kind },
    classList: { toggle() {} },
    setAttribute() {},
  }));
  const requests = [];
  const context = {
    document: {
      getElementById: (id) => elements[id],
      querySelector: () => pages,
      querySelectorAll: () => tabs,
    },
    location: { assign() { throw Error("Unexpected login redirect"); } },
    fetch: (url, options) => {
      requests.push({ url, options });
      if (url.includes("kind=discussions")) return Promise.resolve({
        status: 200, json: async () => ({ ok: true, source: "atom", items: [
          { number: 42, title: "公开讨论", author: "reader", body: "仅预览",
            url: "https://github.com/SHM-white/xtyf-manager/discussions/42",
            comments_count: null },
        ] }),
      });
      return Promise.resolve({ status: 200, json: async () => ({
        ok: true, source: "rest", items: [{ number: 17, title: "<unsafe>",
          author: "reader", comments_count: 1 }],
      }) });
    },
  };
  context.BusinessUI = {fetch: context.fetch};
  vm.createContext(context);
  vm.runInContext(script, context, { filename: "discussions.js" });
  const flush = async () => { await new Promise(setImmediate); };
  await flush();
  assert.equal(button.href, root.dataset.newIssue);
  assert.match(list.innerHTML, /&lt;unsafe&gt;/);
  assert.equal(requests[0].options.method, undefined);
  listeners.click({ target: { closest: (s) =>
    s === "[data-github-kind]" ? { dataset: { githubKind: "discussions" } } : null } });
  await flush();
  assert.equal(button.href, root.dataset.newDiscussion);
  assert.match(note.textContent, /订阅源/);
  assert.doesNotMatch(list.innerHTML, /回复 0/);
  assert.ok(requests.every(({options}) => !options.method || options.method === "GET"));
});
