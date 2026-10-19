"use strict";
const assert = require("node:assert/strict"), test = require("node:test");
const fs = require("node:fs"), vm = require("node:vm"), path = require("node:path");
const escape = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const context = {esc: escape, BusinessUI: {escape}, document: {addEventListener(){}}};
vm.createContext(context);
for (const file of ["task-schedule.js", "task-tree.js"])
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../../static", file), "utf8"), context);

test("external owner conflict links stay outside the schedule's editable rows and escape labels", () => {
  const html = context.scheduleProblemList([{task_id: 7, message: "日期重叠", related_tasks: [
    {id: 7, external: false}, {id: 21, external: true, title: '<img src=x onerror="bad()">',
      project_name: "外组<&", start_date: "2026-10-20", due_date: "2026-10-22"}
  ]}], "排期提醒", true);
  assert.match(html, /data-schedule-focus="7"/);
  assert.match(html, /href="\/tasks\?task=21" target="_blank" rel="noopener"/);
  assert.match(html, /外组&lt;&amp;/);
  assert.match(html, /2026-10-20 至 2026-10-22（不在本次调整范围）/);
  assert.doesNotMatch(html, /<img|data-schedule-task=|data-schedule-focus="21"/);
});

test("dependency edge descriptions consistently distinguish main and additional predecessors", () => {
  const tasks = new Map([[1, {title: "A"}], [2, {title: "B", parent_id: 1, predecessor_ids: []}]]);
  assert.equal(context.treeEdgeLabel(1, 2, tasks), "A（主前置） → B（后续）");
  tasks.get(2).predecessor_ids = [1];
  assert.equal(context.treeEdgeLabel(1, 2, tasks), "A（主前置） → B（后续）（同时列为其他前置）");
  tasks.get(2).parent_id = null;
  assert.equal(context.treeEdgeLabel(1, 2, tasks), "A（其他前置） → B（后续）");
});
