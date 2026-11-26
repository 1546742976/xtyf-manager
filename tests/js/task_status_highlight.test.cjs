"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function fixture() {
  const root = path.resolve(__dirname, "../..");
  const context = {
    BusinessUI: { escape: (value) => String(value ?? "").replace(/[&<>"']/g,
      (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]) },
    console, setInterval() {}, setTimeout() {}, document: { addEventListener() {} },
    location: { href: "" }, FormData: class FormData {},
  };
  context.window = context;
  vm.createContext(context);
  for (const file of ["app.js", "task-board.js", "task-tree.js", "task-timeline.js", "dashboard.js"]) {
    vm.runInContext(fs.readFileSync(path.join(root, "static", file), "utf8"), context, {filename:file});
  }
  const get = (expression) => vm.runInContext(expression, context);
  const state = get("state");
  state.me = {id:1, role:"member"};
  state.members = [{id:1,name:"负责人"}];
  return {get};
}
const task = (fields = {}) => ({
  id: 42, title: "测试任务", status:"进行中", days_left:10, priority:"中",
  progress:45, owner_id:1, participant_ids:[], due_date:"2026-11-07",
  start_date:"2026-10-29", updated_at:"今天", ...fields,
});

test("one visual precedence, with deadline flags retained for combined exceptional states", () => {
  const {get} = fixture();
  const tone = get("taskHighlightTone"), classes = get("taskHighlightClasses");
  const cases = [
    [{status:"阻塞",days_left:-2,dependency_warnings:["缺失前置"]}, "blocked", ["is-blocked","overdue","has-warning"], ["is-due-soon"]],
    [{status:"阻塞",days_left:1}, "blocked", ["is-blocked","is-due-soon"], ["overdue"]],
    [{days_left:-1}, "overdue", ["overdue"], ["is-due-soon"]],
    [{days_left:0}, "due-soon", ["is-due-soon"], ["overdue"]],
    [{days_left:3}, "due-soon", ["is-due-soon"], ["overdue"]],
    [{days_left:4,dependency_warnings:["检查依赖"]}, "dependency", ["has-warning"], ["overdue"]],
    [{days_left:4}, "in-progress", [], ["is-due-soon"]],
    [{status:"未开始",days_left:4}, "not-started", [], ["is-blocked"]],
    [{status:"已完成",days_left:-3,dependency_warnings:["过期提示"]}, "completed", [], ["overdue","has-warning"]],
    [{status:"已取消",days_left:0}, "cancelled", [], ["is-due-soon"]],
  ];
  for (const [fields, expected, present, absent] of cases) {
    const sample = task(fields);
    assert.equal(tone(sample),expected);
    const names = classes(sample).split(/\s+/);
    assert.ok(names.includes("task-tone-"+expected));
    for(const name of present) assert.ok(names.includes(name),name+" missing");
    for(const name of absent) assert.ok(!names.includes(name),name+" should not appear");
  }
});

test("board, dashboard, dependency graph and timeline share the same signal", () => {
  const {get} = fixture();
  for(const fields of [{status:"阻塞",days_left:-3}, {days_left:2},
    {days_left:-1}, {status:"已完成",days_left:-8}, {status:"未开始",days_left:null}]) {
    const item = task(fields);
    const tone = get("taskHighlightTone")(item);
    const rendered = [
      get("taskCard")(item),
      get("treeTaskCard")(item),
      get("timelineTaskRow")(item,{start:1,end:20}),
      get("dashboardTaskRow")(item),
    ];
    for(const html of rendered) {
      assert.ok(html.includes("task-tone-"+tone),tone+" missing in "+html.slice(0,90));
      if(fields.status==="阻塞") {
        assert.match(html,/is-blocked/);
        assert.match(html,/逾期/);
      }
    }
  }
});

test("day-zero, finished and dependency labels stay readable without relying on color", () => {
  const {get} = fixture();
  const label = get("taskAttentionBadge");
  assert.match(label(task({days_left:0}),true),/今日/);
  assert.match(label(task({days_left:3}),true),/临期/);
  assert.match(label(task({days_left:-4}),false),/已逾期 4 天/);
  assert.match(label(task({days_left:20,dependency_warnings:["检查依赖"]})),/依赖提醒/);
  assert.equal(label(task({status:"已完成",days_left:-2})), "");
  assert.equal(label(task({status:"已取消",days_left:0})), "");
});

test("deadline and dependency warnings both remain visible in each task view", () => {
  const {get} = fixture();
  const attention = get("taskAttentionBadge");
  const samples = [
    [task({status:"阻塞",days_left:-2,dependency_warnings:["待修复前置"]}), /逾期/],
    [task({days_left:1,dependency_warnings:["待确认前置"]}), /临期/],
    [task({days_left:0,dependency_warnings:["待确认前置"]}), /今日/],
  ];
  for (const [item, deadline] of samples) {
    for (const compact of [true, false]) {
      const labels = attention(item, compact);
      if (compact) assert.match(labels, deadline);
      else assert.ok(labels.includes(get("daysLeftText")(item)), "missing full deadline label");
      assert.match(labels, /依赖/);
      assert.match(labels, /task-dependency/);
    }
    const views = [
      get("taskCard")(item),
      get("treeTaskCard")(item),
      get("timelineTaskRow")(item, {start:1,end:20}),
      get("dashboardTaskRow")(item),
    ];
    for (const html of views) {
      assert.match(html, /task-dependency/, "missing dependency badge");
      assert.match(html, /逾期|临期|今日|天/, "missing deadline label");
    }
  }
  assert.equal(attention(task({status:"已完成",days_left:-2,dependency_warnings:["历史警告"]})), "");
  assert.equal(attention(task({status:"已取消",days_left:0,dependency_warnings:["历史警告"]})), "");
});

test("exceptional states breathe at distinct strengths while routine tasks stay still", () => {
  const css = fs.readFileSync(path.join(path.resolve(__dirname, "../.."), "static", "task-center.css"), "utf8").replace(/\r\n/g, "\n");
  const legacy = fs.readFileSync(path.join(path.resolve(__dirname, "../.."), "static", "collaboration.css"), "utf8");
  assert.doesNotMatch(legacy, /task-due-breathe|\.task\.is-due-soon\s*,\s*\.ws-task-row\.is-due-soon/);
  const options = [
    ["blocked", "3.2s", "23%"],
    ["overdue", "3.8s", "18%"],
    ["due-soon", "4.6s", "14%"],
  ];
  for (const [name, duration, opacity] of options) {
    const selector = `.task-tone-${name} {\n  --task-breathe-duration:`;
    const start = css.indexOf(selector);
    assert.notEqual(start, -1, `missing breathing variables for ${name}`);
    const declaration = css.slice(start, css.indexOf("}", start));
    assert.ok(declaration.includes(`--task-breathe-duration:${duration}`), `duration: ${name}`);
    assert.ok(declaration.includes(`--task-breathe-opacity:${opacity}`), `strength: ${name}`);
  }
  assert.match(css, /:where\(\.task,\.ws-task-row,\.timeline-row\):is\(\.task-tone-blocked,\.task-tone-overdue,\.task-tone-due-soon\)\s*\{\s*animation:task-state-breathe/);
  assert.match(css, /@keyframes task-state-breathe/);
  assert.match(css, /@media\s*\(prefers-reduced-motion:reduce\)[\s\S]*?animation:none/);
  assert.doesNotMatch(css, /:is\(\.task-tone-completed|:is\(\.task-tone-in-progress|:is\(\.task-tone-dependency/);
});

test("dashboard quick filters agree with the four overview metric definitions", () => {
  const {get} = fixture();
  const state = get("state"), matches = get("taskMatches");
  const cases = [
    task({id:1,status:"进行中",days_left:0,owner_id:1}),
    task({id:2,status:"未开始",days_left:7,owner_id:2,participant_ids:[1]}),
    task({id:3,status:"阻塞",days_left:-2,owner_id:2}),
    task({id:4,status:"已完成",days_left:-2,owner_id:1}),
    task({id:5,status:"未开始",days_left:8,owner_id:2}),
  ];
  for (const [focus, expected] of [
    ["mine",[1,2]],
    ["ongoing",[1]],
    ["due7",[1,2]],
    ["overdue",[3]],
  ]) {
    state.filters={project:"",status:"",q:"",mine:false,focus};
    assert.deepEqual(cases.filter(matches).map(item=>item.id),expected,focus);
  }
});
