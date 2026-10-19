"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

function modalHarness() {
  const classes = (...initial) => {
    const values = new Set(initial);
    return {
      contains: value => values.has(value),
      add: (...items) => items.forEach(item => values.add(item)),
      remove: (...items) => items.forEach(item => values.delete(item)),
    };
  };
  const outside = {focusCount: 0, focus() { this.focusCount++; }};
  const body = {classList: classes()};
  const mask = {classList: classes("hidden")};
  const modal = {
    className: "modal", classList: classes(), innerHTML: "", childrenCleared: 0,
    setAttribute(name, value) { this[name] = value; },
    querySelector() { return {focus() {}}; },
    replaceChildren() { this.innerHTML = ""; this.childrenCleared++; },
  };
  Object.defineProperty(modal, "className", {
    get() { return this._className || "modal"; },
    set(v) { this._className = v; this.classList = classes(...v.split(/\s+/)); },
  });
  const document = {
    activeElement: outside, body,
    getElementById: id => ({"modal": modal, "modal-mask": mask})[id],
    addEventListener() {},
  };
  const context = {document, console};
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../../static/ui-dialog.js"), "utf8"), context);
  return {context, modal, mask, outside, body};
}

test("modal protects pending writes against Escape, backdrop, and replacement", () => {
  const h = modalHarness();
  let saving = true, closed = 0, prevented = 0;
  assert.equal(h.context.showModal("first", "排期", {
    beforeClose: () => !saving,
    onClose: () => closed++,
    className: "schedule-dialog",
  }), true);
  assert.ok(h.modal.classList.contains("schedule-dialog"));
  assert.equal(h.context.hideModal(), false);
  h.context.modalKeydown({key:"Escape", preventDefault(){prevented++;}});
  assert.equal(prevented, 0);
  assert.equal(h.mask.classList.contains("hidden"), false);
  assert.equal(h.context.showModal("second", "其他"), false);
  assert.equal(closed, 0);
  saving = false;
  h.context.modalKeydown({key:"Escape", preventDefault(){prevented++;}});
  assert.equal(prevented, 1);
  assert.equal(closed, 1);
  assert.equal(h.mask.classList.contains("hidden"), true);
  assert.equal(h.outside.focusCount, 1);
  assert.equal(h.context.showModal("second", "其他"), true);
  assert.equal(h.modal.classList.contains("schedule-dialog"), false);
  assert.equal(h.context.hideModal(), true);
  assert.equal(h.context.hideModal(), false);
});

test("replacing a list with task detail preserves return-to-list navigation", () => {
  const h = modalHarness();
  let listClosed = 0, taskClosed = 0;
  const openList = () => h.context.showModal("list", "成员全部任务", {
    className: "member-task-list-dialog", onClose: () => listClosed++,
    returnFocus: () => h.outside,
  });
  assert.equal(openList(), true);
  assert.equal(h.context.showModal("task", "任务详情", {
    onClose: () => taskClosed++, afterClose: openList,
  }), true);
  assert.equal(listClosed, 1);
  assert.equal(h.modal.innerHTML, "task");
  assert.equal(h.context.hideModal(), true);
  assert.equal(taskClosed, 1);
  assert.equal(h.modal.innerHTML, "list");
  assert.equal(h.mask.classList.contains("hidden"), false);
  assert.ok(h.modal.classList.contains("member-task-list-dialog"));
  assert.equal(h.context.hideModal(), true);
  assert.equal(listClosed, 2);
});

test("custom returnFocus resolves against the current document after redraw", () => {
  const h = modalHarness();
  const target = {count: 0, focus() { this.count++; }};
  h.context.showModal("tree task", "详情", {returnFocus: () => target});
  assert.equal(h.context.hideModal(), true);
  assert.equal(target.count, 1);
  assert.equal(h.outside.focusCount, 0);
});
