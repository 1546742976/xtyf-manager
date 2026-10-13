"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const ROOT = path.resolve(__dirname, "../..");
function browser(scripts) {
  const context = {
    console,
    setTimeout() {},
    setInterval() {},
    clearTimeout() {},
    document: { addEventListener() {} },
    location: { href: "" },
    FormData: class FormData {},
  };
  context.window = context;
  vm.createContext(context);
  for (const script of scripts) {
    vm.runInContext(fs.readFileSync(path.join(ROOT, "static", script), "utf8"),
      context, { filename: script });
  }
  return {
    context,
    value(expr) { return vm.runInContext(expr, context); },
  };
}

test("frontend money: integer cents, comparisons and large values remain exact", () => {
  const page = browser(["money.js"]);
  const money = page.context.Money;
  assert.equal(money.cents("0.29"), 29n);
  assert.equal(money.cents("-1.25"), -125n);
  assert.equal(money.cents("9007199254740993.09"), 900719925474099309n);
  assert.equal(money.format("1234567.80"), "1,234,567.80");
  assert.equal(money.format("bad"), "—");
  assert.equal(money.compare("1.10", "1.09"), 1);
  assert.equal(money.compare("2.50", "2.50"), 0);
  assert.equal(money.compare("1.00", "1.01"), -1);
  assert.equal(money.max(["0.10", "5.05", "3.25"]), "5.05");
  assert.equal(money.percent("0.25", "1.00"), 25);
  assert.equal(money.percent("1.00", "0.00"), 0);
  for (const input of ["NaN", "1", "1.2", "1e5", "=1+1"]) {
    assert.throws(() => money.cents(input), /金额格式无效/);
  }
});

test("task board: escaping, access, status and overdue semantics", () => {
  const page = browser(["app.js", "task-board.js", "task-tree.js"]);
  const state = page.value("state");
  state.me = { id: 2, role: "member" };
  state.members = [{ id: 1, name: "负责人" }, { id: 2, name: "参与者" }];
  state.allMembers = [];
  const task = { id: 100, title: "<img src=x>", owner_id: 1,
    participant_ids: [2, 2], group_ids: [3], status: "进行中",
    priority: "中", days_left: -2, updated_at: "today" };
  assert.deepEqual(Array.from(page.value("taskParticipants")(task)), [1, 2]);
  assert.equal(page.value("canEdit")(task), true);
  assert.equal(page.value("isOverdue")(task), true);
  assert.equal(page.value("daysLeftText")(task), "已逾期 2 天");
  assert.equal(page.value("daysLeftClass")(task), "red");
  assert.equal(page.value("isOverdue")({ ...task, status: "已完成" }), false);
  assert.equal(page.value("daysLeftText")({ ...task, days_left: 0 }), "今天截止");
  assert.equal(page.value("daysLeftText")({ ...task, days_left: null }), "未设截止");
  const html = page.value("taskCard")(task);
  assert.ok(html.includes("&lt;img src=x&gt;"));
  assert.ok(!html.includes("<img"));
  state.me = { id: 999, role: "member" };
  assert.equal(page.value("canEdit")(task), false);
  state.me = { id: 999, role: "manager" };
  assert.equal(page.value("canEdit")(task), true);
});

test("task board: search and mine filters apply to people and tasks", () => {
  const page = browser(["app.js", "task-board.js"]);
  const state = page.value("state");
  state.me = { id: 2, role: "member" };
  state.members = [{ id: 2, name: "小明" }];
  state.allMembers = [];
  state.projects = [{ id: 3, name: "哨兵" }];
  state.tasks = [
    { id: 1, owner_id: 2, title: "开发", detail: "电控", project_id: 3, status: "进行中" },
    { id: 2, owner_id: 1, participant_ids: [], title: "修复", detail: "机械", project_id: 3, status: "未开始" },
  ];
  state.filters.project = "3";
  state.filters.mine = true;
  assert.deepEqual(Array.from(page.value("filteredTasks")()).map(t => t.id), [1]);
  state.filters.q = "小明";
  assert.deepEqual(Array.from(page.value("filteredTasks")()).map(t => t.id), [1]);
  state.filters.status = "已完成";
  assert.equal(page.value("filteredTasks")().length, 0);
});

test("task tree: matching child retains ancestors without infinite loops", () => {
  const page = browser(["app.js", "task-board.js", "task-tree.js"]);
  const state = page.value("state");
  state.tasks = [
    { id: 1, title: "根", parent_id: null },
    { id: 2, title: "中间", parent_id: 1 },
    { id: 3, title: "匹配项", parent_id: 2 },
  ];
  const visible = page.value("treeVisibleSet")([state.tasks[2]]);
  assert.deepEqual(Array.from(visible.visible).sort(), [1, 2, 3]);
  assert.deepEqual(Array.from(visible.context).sort(), [1, 2]);
  state.tasks = [{ id: 1, title: "循环甲", parent_id: 2 },
                 { id: 2, title: "循环乙", parent_id: 1 }];
  const cyclic = page.value("treeVisibleSet")([state.tasks[0]]);
  assert.equal(cyclic.visible.size, 2);
  state.expanded.add(1);
  state.expanded.add(2);
  assert.ok(page.value("renderTree")(state.tasks).includes("检测到循环关系"));
});

test("member management: multi-group filters, inactive users and role UI", () => {
  const page = browser(["app.js", "manage-members.js"]);
  const state = page.value("state");
  const filters = page.value("memberFilters");
  state.me = { id: 5, role: "admin" };
  const members = page.value("allMembers");
  members.push(
    { id: 5, name: "管理员", username: "admin", group_ids: [1, 2],
      project_ids: [10], role: "admin", is_deleted: false },
    { id: 6, name: "成员乙", username: "person", group_ids: [2],
      project_ids: [11], role: "member", is_deleted: false },
    { id: 7, name: "已删除", username: "old", group_ids: [2],
      project_ids: [11], role: "member", is_deleted: true }
  );
  const filtered = page.value("filteredMembers");
  assert.equal(filtered().length, 2);
  filters.group = "2";
  filters.project = "11";
  assert.deepEqual(Array.from(filtered()).map(x => x.id), [6]);
  filters.showDeleted = true;
  assert.deepEqual(Array.from(filtered()).map(x => x.id), [6, 7]);
  filters.q = "person";
  assert.deepEqual(Array.from(filtered()).map(x => x.id), [6]);
  const roleSelect = page.value("roleSelect");
  assert.match(roleSelect(members[0]), /disabled/);
  assert.doesNotMatch(roleSelect(members[1]), /data-role-id="6" disabled/);
});

test("archived catalog entries are dictionary-only choices without destroying saved assignments", () => {
  const page = browser(["app.js", "manage-tasks.js", "account.js"]);
  const items = [
    { id: 1, name: "被删除的组", is_active: 0 },
    { id: 2, name: "当前有效组", is_active: 1 },
  ];
  const dropdown = page.value("options")(items, 1, "全部组别");
  assert.doesNotMatch(dropdown, /被删除的组|value="1"/);
  assert.match(dropdown, /当前有效组/);
  assert.equal(page.value("activeCatalogFilter")(items, "1"), "");
  assert.equal(page.value("activeCatalogFilter")(items, "2"), "2");

  const choices = page.value("catalogChoices")("group_ids", items, [1, 2]);
  assert.doesNotMatch(choices, />被删除的组/);
  assert.match(choices, /name="group_ids" value="1" checked hidden/);
  assert.match(choices, />当前有效组/);
  assert.doesNotMatch(page.value("catalogChoices")("group_ids", items, []), /value="1"/);

  const host = { innerHTML: "" };
  page.value("accountChoices")(host, "project_ids", items, [1]);
  assert.doesNotMatch(host.innerHTML, />被删除的组/);
  assert.match(host.innerHTML, /name="project_ids" value="1" checked hidden/);
  assert.match(host.innerHTML, />当前有效组/);

  // The catalog itself continues to show history and deletion status.
  assert.match(page.value("catalogPanel")("projects", items), /被删除的组（已删除）/);
  assert.equal(page.value("retainArchivedCatalogSelection")("", 1, items, false), "1");
  assert.equal(page.value("retainArchivedCatalogSelection")("", 1, items, true), "");
  assert.equal(page.value("retainArchivedCatalogSelection")("2", 1, items, true), "2");
});
