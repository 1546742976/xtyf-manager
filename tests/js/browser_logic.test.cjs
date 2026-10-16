"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const ROOT = path.resolve(__dirname, "../..");

test("timeline preserves parallel dates, shared endpoints, same-day markers and strict dates", () => {
  const page = browser(["app.js", "task-board.js", "task-timeline.js"]);
  const tasks = [{id:1,start_date:"2026-10-14",due_date:"2026-10-22"},
    {id:2,start_date:"2026-10-14",due_date:"2026-10-22"},
    {id:3,start_date:"2026-10-22",due_date:"2026-10-26"},
    {id:4,start_date:"2026-10-26",due_date:"2026-10-26"}];
  const range = page.value("timelineRange")(tasks), position = page.value("timelinePosition");
  assert.equal(position(tasks[0],range).left, position(tasks[1],range).left);
  assert.ok(Math.abs(position(tasks[0],range).left + position(tasks[0],range).width - position(tasks[2],range).left) < 1e-10);
  assert.equal(position(tasks[3],range).sameDay,true);
  assert.equal(position(tasks[3],range).width,0);
  const parse = page.value("timelineDay");
  for (const value of ["2026-02-30","2026-13-01","2026-2-03","0000-01-01","<svg>",null]) assert.equal(parse(value),null);
  assert.equal(parse("2024-02-29") + 1, parse("2024-03-01"));
  assert.equal(parse("2026-03-08") + 1, parse("2026-03-09"));
  assert.equal(page.value("timelinePosition")({start_date:"2026-10-22",due_date:"2026-10-14"},range),null);
});

test("timeline axis ignores row filters and other vehicle groups; missing dates and names are escaped", () => {
  const page = browser(["app.js", "task-board.js", "task-timeline.js"]), state = page.value("state");
  state.projects = [{id:10,name:"哨兵"}];
  state.filters.project = "10";
  state.tasks = [{id:1,project_id:10,title:"<svg>",owner_stage:"机械",start_date:"2026-10-14",due_date:"2026-10-22",status:"未开始"},
    {id:2,project_id:10,title:"电控",owner_stage:"电控",start_date:"2026-10-22",due_date:"2026-10-26"},
    {id:3,project_id:10,title:"待补日期",owner_stage:null,start_date:"",due_date:"2026-10-29"},
    {id:4,project_id:11,title:"其他车组",start_date:"2030-01-01",due_date:"2030-01-02"}];
  const render = page.value("renderSpecialtyTimeline");
  const full = render(state.tasks.filter(task=>task.project_id===10));
  const filtered = render([state.tasks[0]]);
  assert.match(full,/2026-10-14 — 2026-10-26/);
  assert.match(filtered,/2026-10-14 — 2026-10-26/);
  assert.match(full,/&lt;svg&gt;/); assert.doesNotMatch(full,/<svg>|2030-01-01/);
  assert.match(full,/未归类/); assert.match(full,/缺少开始日期/);
  assert.ok(full.indexOf("机械</h3>") < full.indexOf("电控</h3>"));
  assert.ok(full.indexOf("电控</h3>") < full.indexOf("视觉</h3>"));
});
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

test("specialty schedule: owner phases, stable dates and management order agree", () => {
  const page = browser(["app.js", "task-board.js", "manage-tasks.js"]);
  const state = page.value("state");
  state.groups = [{id:1,name:"机械"},{id:2,name:"电控"},{id:3,name:"视觉"}];
  state.members = [{id:1,group_ids:[1]}, {id:2,group_ids:[2]}, {id:3,group_ids:[3]}, {id:4,group_ids:[1,3]}];
  const tasks = [
    {id:6,owner_id:3,project_id:10,start_date:"2026-10-01",due_date:"2026-10-02"},
    {id:3,owner_id:1,project_id:10,start_date:"",due_date:""},
    {id:2,owner_id:2,project_id:10,start_date:"2026-10-01",due_date:"2026-10-02",participant_ids:[1]},
    {id:5,owner_id:4,project_id:10,start_date:"2026-10-01",due_date:"2026-10-02",schedule_stage:"视觉"},
    {id:1,owner_id:1,project_id:10,start_date:"2026-10-01",due_date:"2026-10-02"},
    {id:4,owner_id:4,project_id:10,start_date:"2026-10-01",due_date:"2026-10-02"},
    {id:7,owner_id:1,project_id:11,start_date:"2026-10-01",due_date:"2026-10-02"},
  ];
  state.tasks = tasks;
  state.filters.project = "10";
  page.value("taskFilters").project = "10";
  const ids = (items) => Array.from(items, x => x.id);
  assert.deepEqual(ids(page.value("filteredTasks")()), [1,3,2,5,6,4]);
  assert.deepEqual(ids(page.value("filteredManageTasks")()), [1,3,2,5,6,4]);
  assert.deepEqual(ids(state.tasks), [6,3,2,5,1,4,7]); // rendering must not mutate the source
  const lanes = page.value("specialtyLanes")(tasks.filter(x=>x.project_id===10));
  assert.deepEqual(Array.from(lanes,x=>x.title), ["机械","电控","视觉","未归类"]);
  assert.equal(page.value("specialtyTaskStage")({owner_id:4,schedule_stage:"电控"}), null);
  assert.equal(page.value("specialtyTaskStage")({owner_id:1,owner_stage:null}), null);
});

test("schedule preview: escaped content, dependency cards and protected participation controls", () => {
  const page = browser(["app.js", "task-schedule.js"]);
  const row = {id:1,title:'<img src=x>',owner_name:'<script>',status:'未开始',eligible:true,
    stage:"视觉",note:'<svg>',selected:false,protected:true,protection_reason:'固定日期',state:'protected',duration_days:3,
    predecessors:[{id:2,title:'<svg>',external:true,boundary_date:'2026-10-22'}],
    before:{start_date:"2026-10-14",due_date:"2026-10-16"},after:{start_date:"2026-10-22",due_date:"2026-10-24"}};
  const html = page.value("schedulePreviewRows")([row]);
  assert.match(html,/&lt;img src=x&gt;/);
  assert.match(html,/&lt;script&gt;/);
  assert.match(html,/data-schedule-task="1"/);
  assert.match(html,/允许本次调整/);
  assert.match(html,/受保护/);
  assert.match(html,/跨车组/);
  assert.doesNotMatch(html,/data-phase-choice|data-schedule-duration|<select/);
  assert.match(html,/原：2026-10-14/);
  assert.match(html,/新：2026-10-22/);
  assert.match(page.value("schedulePreviewRows")([{...row,eligible:false}]), /disabled/);
  const missing = page.value("schedulePreviewRows")([{...row,eligible:true,selected:true,before:{},needs_duration:true,state:"changed",shift_days:null}],{task_ids:[1],durations:{1:4}});
  assert.match(missing,/data-schedule-duration="1" value="4"/);
  assert.match(missing,/补齐日期/);
  assert.doesNotMatch(missing,/日期保持 \/ 待计算/);
});

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

test("person board hides admin lanes and places filtered unassigned tasks first", () => {
  const page = browser(["app.js", "task-board.js"]), state = page.value("state");
  state.view = "person";
  state.members = [{id:1,name:"管理员",role:"admin"},{id:2,name:"队员",role:"member"}];
  state.allMembers = [{id:3,name:"历史管理员",role:"admin"}];
  state.tasks = [
    {id:1,title:"管理员任务",owner_id:1,status:"未开始",priority:"中"},
    {id:2,title:"队员任务",owner_id:2,status:"进行中",priority:"中"},
    {id:3,title:"待认领 A",owner_id:null,status:"未开始",priority:"中"},
    {id:4,title:"待认领 B",owner_id:null,status:"未开始",priority:"中"},
    {id:5,title:"历史管理员任务",owner_id:3,status:"未开始",priority:"中"},
  ];
  const lanes = page.value("participantLanes")(state.tasks);
  assert.deepEqual(Array.from(lanes,lane=>lane.key), ["unassigned","m-2"]);
  assert.deepEqual(Array.from(lanes[0].tasks,task=>task.id), [3,4]);
  const html = page.value("renderLanes")(page.value("filteredTasks")());
  assert.ok(html.indexOf('unassigned-lane') < html.indexOf('队员</h2>'));
  assert.doesNotMatch(html, /管理员<\/h2>/);
  state.filters.status = "进行中";
  assert.doesNotMatch(page.value("renderLanes")(page.value("filteredTasks")()), /unassigned-lane|待认领/);
  state.filters.status = "";
  state.filters.q = "待认领 B";
  const searchHtml = page.value("renderLanes")(page.value("filteredTasks")());
  assert.match(searchHtml, /未分配<\/h2>/);
  assert.match(searchHtml, /待认领 B/);
  assert.doesNotMatch(searchHtml, /待认领 A/);
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
