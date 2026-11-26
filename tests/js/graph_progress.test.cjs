"use strict";
const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const root = path.resolve(__dirname, "../..");

function page(scripts) {
  const context = {
    BusinessUI: { escape: (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])) }, console, setInterval() {}, document: { addEventListener() {} },
    location: { href: "" },
  };
  context.window = context;
  vm.createContext(context);
  scripts.forEach((script) => vm.runInContext(
    fs.readFileSync(path.join(root, "static", script), "utf8"),
    context, { filename: script },
  ));
  return { context, evaluate: (src) => vm.runInContext(src, context) };
}

test("compact progress card displays percentage, escaped note and latest images", () => {
  const { evaluate } = page(["app.js", "task-board.js"]);
  const task = {
    id: 11, title: "完成驱动", priority: "高", status: "进行中",
    progress: 48, updated_at: "today", participant_ids: [], owner_id: 1,
    latest_progress: { actor_name: "成员A", created_at: "now",
      note: "<script>alert(1)</script>", image_ids: [21] },
  };
  const html = evaluate("taskCard") (task);
  assert.match(html, /48%/);
  assert.match(html, /role="progressbar"/);
  assert.match(html, /&lt;script&gt;/);
  assert.match(html, /<span>成员A<\/span>/);
  assert.doesNotMatch(html, /<script>/);
  assert.ok(html.includes('src="/api/task/11/attachments/21"'));
  const imageOnly = evaluate("taskCard")({...task, latest_note:'',latest_progress:{actor_name:'成员A',note:'',image_ids:[21]}});
  assert.match(imageOnly, /task-note-images/);
  assert.doesNotMatch(imageOnly, /<p><\/p>/);
  const withoutNote = evaluate("taskCard")({...task, latest_progress:null});
  assert.doesNotMatch(withoutNote, /task-note|无备注/);
  assert.match(withoutNote, /role="progressbar"/);
});

test("task note label uses the nonempty note author and escapes names", () => {
  const { evaluate } = page(["app.js", "task-board.js"]);
  const card = evaluate("taskCard");
  const task = {
    id: 12, title: "备注作者", priority: "中", status: "进行中",
    owner_id: 1, participant_ids: [], latest_note: "保留这条备注",
    latest_note_author: '<img src=x onerror="alert(1)">',
    latest_progress: { actor_name: "仅更新进度者", note: "   " },
  };
  const html = card(task);
  assert.ok(html.includes('&lt;img src=x onerror=&quot;alert(1)&quot;&gt;'));
  assert.match(html, /保留这条备注/);
  assert.doesNotMatch(html, /<img|仅更新进度者/);
  const empty = card({ ...task, latest_note: "", latest_note_author: "",
    latest_progress: { actor_name: "旧备注者", note: "旧备注" } });
  assert.doesNotMatch(empty, /task-note|无备注/);
  assert.doesNotMatch(empty, /旧备注者|旧备注/);
});

test("person lanes keep one task and expose a dialog independently from tree expansion", () => {
  const { evaluate } = page(["app.js", "task-board.js"]);
  const state = evaluate("state");
  state.view = 'person';
  state.members = [{id:2,name:'测试成员',role:'member'}];
  const tasks = [1,2,3].map(id => ({id,title:'任务'+id,owner_id:2,status:'未开始',priority:'中'}));
  const render = evaluate('renderLanes');
  assert.equal((render(tasks).match(/data-task-id=/g)||[]).length,1);
  assert.match(render(tasks), /查看全部 3 项/);
  assert.match(render(tasks), /aria-haspopup="dialog"/);
  assert.equal((render(tasks).match(/data-task-id=/g)||[]).length,1);
  assert.doesNotMatch(render(tasks), /aria-expanded/);
  assert.equal(state.expanded.size,0);
});

test("dependency graph draws multiple upstream parents and keeps filtered ancestors", () => {
  const { evaluate } = page(["app.js", "task-board.js", "task-tree.js"]);
  const state = evaluate("state");
  state.tasks = [
    { id: 1, title: "父任务", parent_id: null, status: "未开始", priority: "中" },
    { id: 2, title: "另一前置", parent_id: null, status: "进行中", priority: "中" },
    { id: 3, title: "汇合任务", parent_id: 1, predecessor_ids: [2], status: "未开始", priority: "中" },
  ];
  const visible = evaluate("treeVisibleSet")([state.tasks[2]]);
  assert.deepEqual(Array.from(visible.visible).sort(), [1, 2, 3]);
  assert.deepEqual(Array.from(visible.context).sort(), [1, 2]);
  const html = evaluate("renderTree")(state.tasks);
  assert.match(html, /<svg/);
  assert.match(html, /marker-end="url\(#dep-arrow\)"/);
  // Hit paths deliberately duplicate geometry; only count the visible edges.
  assert.equal((html.match(/class="dependency-edge"/g) || []).length, 2);
  assert.match(html, /data-task-id="3"/);
});

test("editable task progress form has slider, numeric input, note and image upload", () => {
  const { evaluate } = page(["app.js", "task-progress.js"]);
  const state = evaluate("state");
  state.me = { id: 1, role: "admin" };
  const html = evaluate("progressSection")({ id: 3, version: 1, progress: 65 });
  assert.match(html, /name="progress_slider"/);
  assert.match(html, /name="progress"/);
  assert.match(html, /name="note"/);
  assert.match(html, /name="files"/);
  state.me = { id: 999, role: "member" };
  const readOnly = evaluate("progressSection")({ id: 3, version: 1, progress: 65, owner_id: 1 });
  assert.doesNotMatch(readOnly, /id="progress-form"/);
});

test("due-soon border applies only to unfinished tasks due within three days", () => {
  const { evaluate } = page(["app.js", "task-board.js"]);
  const task = { id: 10, title: "联调", owner_id: 1, status: "进行中", priority: "中", days_left: 3 };
  assert.equal(evaluate("isDueSoon")(task), true);
  assert.match(evaluate("taskCard")(task), /is-due-soon/);
  const blocked = evaluate("taskCard")({ ...task, status: "阻塞" });
  assert.match(blocked, /is-blocked/);
  assert.match(blocked, /is-due-soon/);
  const overdue = evaluate("taskCard")({ ...task, status: "阻塞", days_left: -1 });
  assert.match(overdue, /is-blocked/);
  assert.match(overdue, /overdue/);
  assert.doesNotMatch(overdue, /is-due-soon/);
  for (const changed of [{ days_left: -1 }, { days_left: 4 }, { days_left: null }, { status: "已完成" }, { status: "已取消" }]) {
    assert.equal(evaluate("isDueSoon")({ ...task, ...changed }), false);
  }
});

test("dashboard cards show progress and escaped updates; preview never shows mutation controls", () => {
  const { context, evaluate } = page(["app.js", "task-board.js", "task-progress.js", "task-modal.js", "dashboard.js"]);
  const state = evaluate("state");
  state.me = { id: 1, role: "admin" };
  state.members = [{ id: 1, name: "组员甲" }];
  const task = { id: 5, title: "<b>测试</b>", owner_id: 1, participant_ids: [],
    status: "进行中", priority: "高", progress: 55, days_left: 2,
    due_date: "2026-10-15", detail: "任务介绍", latest_progress: {
      progress: 55, note: "<script>unsafe</script>", actor_name: "组员甲",
      created_at: "2026-10-13", image_ids: [7],
    },
  };
  state.tasks = [task];
  const row = evaluate("dashboardTaskRow")(task);
  assert.match(row, /is-due-soon/);
  assert.match(row, /role="progressbar"/);
  assert.match(row, /55%/);
  assert.match(row, /近期动态/);
  assert.ok(row.includes("&lt;script&gt;unsafe&lt;/script&gt;"));
  assert.ok(!row.includes("<script>unsafe"));
  assert.ok(row.includes("/api/task/5/attachments/7"));
  assert.match(row, /data-dashboard-task="5"/);
  const preview = evaluate("taskPreviewShell")(task);
  assert.match(preview, /当前进度|进度动态与截图/);
  assert.match(preview, /55%/);
  assert.doesNotMatch(preview, /<form|data-delete-task|data-revert|保存更新/);
  assert.match(preview, /组员甲/);
  const elements = { "dashboard-stats": { innerHTML: "" }, "dashboard-task-list": { innerHTML: "" } };
  context.document.getElementById = (id) => elements[id] || null;
  evaluate("renderWorkspaceDashboard")();
  assert.match(elements["dashboard-task-list"].innerHTML, /55%/);
  assert.match(elements["dashboard-task-list"].innerHTML, /is-due-soon/);
});
