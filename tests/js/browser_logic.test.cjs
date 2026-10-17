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

test("person board includes admin owners and places filtered unassigned tasks first", () => {
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
  assert.deepEqual(Array.from(lanes,lane=>lane.key), ["unassigned","m-1","m-2","m-3"]);
  assert.deepEqual(Array.from(lanes[0].tasks,task=>task.id), [3,4]);
  const html = page.value("renderLanes")(page.value("filteredTasks")());
  assert.ok(html.indexOf('unassigned-lane') < html.indexOf('队员</h2>'));
  assert.match(html, /管理员<\/h2>/);
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

test("person lanes prioritize scored active tasks and fold completed work", () => {
  const page = browser(["app.js", "task-board.js"]), state = page.value("state");
  state.view = "person";
  state.members = [{id:7,name:"小七",role:"member"},{id:2,name:"队友",role:"member"}];
  state.tasks = [
    {id:8,title:"已完成",owner_id:7,status:"已完成",days_left:-60,priority:"高"},
    {id:5,title:"协助逾期",owner_id:2,participant_ids:[7],status:"阻塞",days_left:-3,priority:"高"},
    {id:3,title:"主责五天",owner_id:7,status:"进行中",days_left:5,priority:"低"},
    {id:4,title:"主责今天",owner_id:7,status:"进行中",days_left:0,priority:"中"},
    {id:2,title:"主责明天",owner_id:7,status:"进行中",days_left:1,priority:"高"},
    {id:6,title:"协助今天",owner_id:2,participant_ids:[7],status:"进行中",days_left:0,priority:"高"},
    {id:9,title:"已取消",owner_id:7,status:"已取消",days_left:-99,priority:"高"},
    {id:1,title:"主责逾期",owner_id:7,status:"阻塞",days_left:-1,priority:"低"},
    {id:7,title:"主责无期限",owner_id:7,status:"未开始",days_left:null,priority:"高"},
  ];
  const originalIds = state.tasks.map((task) => task.id);
  const lane = page.value("participantLanes")(state.tasks).find((item) => item.key === "m-7");
  assert.deepEqual(Array.from(lane.tasks, (task) => task.id), [2,4,5,1,6,3,7,8,9]);
  assert.deepEqual(state.tasks.map((task) => task.id), originalIds);
  let html = page.value("renderPersonLane")(lane);
  assert.match(html, /主责明天/);
  assert.doesNotMatch(html, /主责逾期|主责今天|协助逾期|已完成<\/h3>|已取消<\/h3>/);
  assert.match(html, /展开其余 6 项/);
  assert.match(html, /查看已完成\/取消 2 项/);
  assert.match(html, /id="lane-finished-m-7" hidden/);
  page.value("expandedPersonLanes").add("m-7");
  html = page.value("renderPersonLane")(lane);
  assert.match(html, /主责今天|协助逾期/);
  assert.doesNotMatch(html, /已完成<\/h3>|已取消<\/h3>/);
  page.value("expandedFinishedLanes").add("m-7");
  html = page.value("renderPersonLane")(lane);
  assert.match(html, /已完成<\/h3>/);
  assert.match(html, /已取消<\/h3>/);
  assert.doesNotMatch(html, /id="lane-finished-m-7" hidden/);
  state.filters.status = "已完成";
  const filtered = page.value("participantLanes")(page.value("filteredTasks")()).find((item) => item.key === "m-7");
  page.value("expandedFinishedLanes").delete("m-7");
  assert.match(page.value("renderPersonLane")(filtered), /已完成<\/h3>/);
});

test("urgency comparator weighs priority, deadline and status jointly", () => {
  const page = browser(["app.js", "task-board.js"]);
  const rank = page.value("compareTaskUrgency");
  const items = [
    {id:5,owner_id:7,days_left:null,status:"未开始",priority:"高"},
    {id:4,owner_id:7,days_left:3,status:"进行中",priority:"高"},
    {id:3,owner_id:7,days_left:3,status:"进行中",priority:"低"},
    {id:2,owner_id:7,days_left:-2,status:"进行中",priority:"低"},
    {id:1,owner_id:7,days_left:3,status:"阻塞",priority:"高"},
  ];
  assert.deepEqual(Array.from([...items].sort((a,b) => rank(a,b,7)), x=>x.id), [1,4,2,3,5]);
  assert.deepEqual(items.map(x=>x.id), [5,4,3,2,1]);
  assert.equal(rank({id:1,status:"已完成",days_left:-999,owner_id:7},
    {id:2,status:"进行中",days_left:null,owner_id:3,participant_ids:[7]},7) > 0, true);
});

test("composite urgency allows an urgent participant task to beat undated ownership", () => {
  const page=browser(["app.js","task-board.js"]);
  const score=page.value("taskUrgencyScore"),rank=page.value("compareTaskUrgency");
  const owner={id:1,owner_id:7,status:"阻塞",priority:"高",days_left:null};
  const participation={id:2,owner_id:2,participant_ids:[7],status:"阻塞",priority:"高",days_left:1};
  const lowOwnerToday={id:3,owner_id:7,status:"进行中",priority:"低",days_left:0};
  const highParticipantToday={id:4,owner_id:2,participant_ids:[7],status:"阻塞",priority:"高",days_left:0};
  const lowOwnerOverdue={id:5,owner_id:7,status:"进行中",priority:"低",days_left:-3};
  assert.equal(score(owner,7),56);
  assert.equal(score(participation,7),68);
  assert.equal(score(highParticipantToday,7),72);
  assert.equal(score(lowOwnerOverdue,7),73);
  assert.ok(rank(participation,owner,7)<0);
  assert.ok(rank(highParticipantToday,lowOwnerToday,7)<0);
  assert.ok(rank(lowOwnerOverdue,highParticipantToday,7)<0);
  assert.equal(page.value("taskResponsibilityWeight")({...owner,participant_ids:[7]},7),28);
  assert.equal(page.value("taskResponsibilityWeight")(participation,7),8);
  assert.equal(page.value("taskResponsibilityWeight")(participation,4),0);
  assert.equal(page.value("taskResponsibilityWeight")(participation,null),0);
  assert.equal(page.value("taskResponsibilityWeight")(participation,"invalid"),0);
});

test("composite urgency deadline weights are monotone, capped, and null safe", () => {
  const page=browser(["app.js"]);
  const weight=page.value("taskDeadlineWeight");
  const cases=[-10000,-100,-7,-6,-5,-2,-1,0,1,2,3,4,5,6,7,8,9,14,15,18,29,30,31,100];
  for(const day of cases) assert.ok(Number.isInteger(weight(day))&&weight(day)>=0&&weight(day)<=44);
  for(let i=1;i<cases.length;i++) assert.ok(weight(cases[i-1])>=weight(cases[i]),"non-monotone date weight");
  const expected=[[-10000,44],[-6,44],[-1,39],[0,36],[1,32],[3,24],[4,20],[7,14],
    [8,12],[14,6],[15,5],[30,0],[31,0]];
  for(const [day,value] of expected) assert.equal(weight(day),value);
  for(const invalid of [null,undefined,"","0","2026-10-17",NaN,Infinity,-Infinity,0.5,{},[]])
    assert.equal(weight(invalid),0,"invalid date must receive no deadline bonus");
});

test("composite urgency statuses, priorities, closures and equal-score tie breakers", () => {
  const page=browser(["app.js"]);
  const score=page.value("taskUrgencyScore"),rank=page.value("compareTaskUrgency");
  const top={id:1,owner_id:7,status:"阻塞",priority:"高",days_left:-100};
  assert.equal(score(top,7),100);
  assert.equal(score({...top,priority:"中"},7),90);
  assert.equal(score({...top,priority:"低"},7),80);
  assert.equal(score({...top,status:"进行中"},7),96);
  assert.equal(score({...top,status:"未开始"},7),92);
  assert.equal(score({...top,days_left:null,priority:"低",status:"未开始"},7),28);
  assert.equal(score({...top,days_left:null,priority:"低",status:"未开始"},null),0);
  assert.equal(score({...top,status:"已完成"},7),0);
  assert.equal(score({...top,status:"已取消"},7),0);
  assert.ok(rank({...top,status:"已完成"},{id:2,owner_id:2,status:"未开始",priority:"低",days_left:null},7)>0);
  assert.ok(rank({...top,status:"已取消"},{id:2,owner_id:2,status:"未开始",priority:"低",days_left:null},7)>0);
  assert.equal(rank({...top,id:3,days_left:-1000},{...top,id:2,days_left:-6},7),1,
    "severely overdue tasks should not sort by unbounded age");
  assert.ok(rank({...top,id:9,priority:"低",days_left:0},
    {id:10,owner_id:2,participant_ids:[7],status:"阻塞",priority:"高",days_left:0},7)<0,
    "ownership breaks real score ties");
  assert.ok(rank({...top,id:20,priority:"高",days_left:1},
    {...top,id:10,priority:"中",days_left:-4},7)<0,
    "priority breaks real score ties");
});

test("composite urgency gives deterministic, transitive ranking for varied task records", () => {
  const page=browser(["app.js"]);
  const score=page.value("taskUrgencyScore"),rank=page.value("compareTaskUrgency");
  const states=["阻塞","进行中","未开始","已完成","已取消"],priorities=["高","中","低"];
  const deadlines=[-200,-6,-1,0,1,3,7,14,30,null];
  const tasks=Array.from({length:160},(_,i)=>({
    id:i+1,owner_id:i%3===0?7:2,participant_ids:i%3===1?[7]:[],
    status:states[i%5],priority:priorities[(i*7)%3],
    days_left:deadlines[(i*11+Math.floor(i/10))%deadlines.length],
  }));
  const before=tasks.map(x=>x.id),sorted=[...tasks].sort((a,b)=>rank(a,b,7));
  assert.deepEqual(tasks.map(x=>x.id),before,"sort must not mutate state.tasks");
  for(const item of tasks) assert.ok(Number.isInteger(score(item,7))&&score(item,7)>=0&&score(item,7)<=100);
  for(let i=0;i<sorted.length;i++) for(let j=i+1;j<sorted.length;j++) {
    assert.ok(rank(sorted[i],sorted[j],7)<=0,"comparator is not transitive");
    assert.ok(rank(sorted[j],sorted[i],7)>=0,"reverse order is inconsistent");
  }
  assert.deepEqual(Array.from([...tasks].reverse().sort((a,b)=>rank(a,b,7)),x=>x.id),
    Array.from(sorted,x=>x.id),"tie-breaking must be input-order independent");
});


test("dashboard ranks everyone by composite urgency with owner 28, participant 8, team 4", () => {
  const page=browser(["app.js","dashboard.js"]);
  const score=page.value("taskUrgencyScore");
  const rank=page.value("rankDashboardTasks");
  const role=page.value("taskResponsibilityWeight");
  const teamWeight=page.value("DASHBOARD_TEAM_TASK_WEIGHT");
  assert.equal(teamWeight,4);
  const base={status:"进行中",priority:"中",days_left:1};
  const owner={...base,id:91,owner_id:7};
  const participant={...base,id:92,owner_id:9,participant_ids:[7]};
  const unrelated={...base,id:93,owner_id:9,participant_ids:[]};
  assert.equal(role(owner,7,teamWeight),28);
  assert.equal(role(participant,7,teamWeight),8);
  assert.equal(role(unrelated,7,teamWeight),4);
  assert.equal(score(owner,7,teamWeight)-score(participant,7,teamWeight),20);
  assert.equal(score(participant,7,teamWeight)-score(unrelated,7,teamWeight),4);
  assert.equal(score(unrelated,7),score(unrelated,7,0)-0);
  assert.equal(role({...owner,participant_ids:[7]},7,teamWeight),28,
    "self-owned tasks do not receive another participant bonus");
  assert.equal(role({id:1,owner_id:null,participant_ids:[]},7,teamWeight),4,
    "unassigned active work remains eligible for team-wide ranking");
  const ordered=Array.from(rank([unrelated,participant,owner],7),task=>task.id);
  assert.deepEqual(ordered,[91,92,93]);
  // The dashboard-specific four points must never leak into person-lane scoring.
  assert.equal(score(unrelated,7),score(unrelated,7,0));
  assert.equal(role(unrelated,7),0);
});

test("dashboard mixed urgency ranks unrelated urgent work without displacing every personal task", () => {
  const page=browser(["app.js","dashboard.js"]);
  const rank=page.value("rankDashboardTasks");
  const tasks=[
    {id:1,title:"owner undated",owner_id:7,status:"阻塞",priority:"高",days_left:null},
    {id:2,title:"participant tomorrow",owner_id:9,participant_ids:[7],status:"阻塞",priority:"高",days_left:1},
    {id:3,title:"team tomorrow",owner_id:9,status:"阻塞",priority:"高",days_left:1},
    {id:4,title:"team today",owner_id:9,status:"阻塞",priority:"高",days_left:0},
    {id:5,title:"participant today",owner_id:9,participant_ids:[7],status:"阻塞",priority:"高",days_left:0},
    {id:6,title:"owner today",owner_id:7,status:"进行中",priority:"低",days_left:0},
    {id:7,title:"team overdue",owner_id:9,status:"阻塞",priority:"高",days_left:-6},
    {id:8,title:"finished",owner_id:7,status:"已完成",priority:"高",days_left:-100},
    {id:9,title:"cancelled",owner_id:7,status:"已取消",priority:"高",days_left:-100},
    {id:10,title:"team unscheduled",owner_id:9,status:"未开始",priority:"低",days_left:null},
    {id:11,title:"unassigned",owner_id:null,status:"未开始",priority:"低",days_left:null},
    {id:12,title:"participant undated",owner_id:9,participant_ids:[7],status:"未开始",priority:"低",days_left:null},
  ];
  const original=tasks.map(task=>task.id);
  const order=Array.from(rank(tasks,7),task=>task.id);
  assert.deepEqual(order,[7,5,6,2,4,3,1,12],
    "homepage top eight should be ranked across the whole team");
  assert.deepEqual(Array.from(rank([...tasks].reverse(),7),task=>task.id),order);
  assert.deepEqual(tasks.map(task=>task.id),original,"sorting must not change API data");
  assert.deepEqual(Array.from(rank(tasks,7,100),task=>task.id),[7,5,6,2,4,3,1,12,10,11],
    "finished and cancelled tasks never occupy dashboard slots");
  assert.deepEqual(Array.from(rank([tasks[7],tasks[8]],7),task=>task.id),[]);
  assert.deepEqual(Array.from(rank(tasks,7,0),task=>task.id),[]);
});
