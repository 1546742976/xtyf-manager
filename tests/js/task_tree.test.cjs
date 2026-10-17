"use strict";
const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const root = path.resolve(__dirname, "../..");

function fixture(tasks = []) {
  const context = { console, setInterval() {}, document: { addEventListener() {} }, location: { href: "" } };
  context.window = context;
  vm.createContext(context);
  for (const script of ["app.js", "task-board.js", "task-tree.js"]) {
    vm.runInContext(fs.readFileSync(path.join(root, "static", script), "utf8"), context, { filename: script });
  }
  const get = (name) => vm.runInContext(name, context);
  const state = get("state");
  state.tasks = tasks;
  state.members = [{ id: 1, name: "负责人甲" }, { id: 2, name: "负责人乙" }];
  return { state, get };
}

function task(id, extra = {}) {
  return { id, title: `任务 ${id}`, owner_id: 1, status: "进行中", priority: "中", progress: 35,
    parent_id: null, predecessor_ids: [], participant_ids: [], ...extra };
}

function count(html, className) {
  return [...html.matchAll(/class="([^"]*)"/g)].filter((match) => match[1].split(/\s+/).includes(className)).length;
}

function checkGeometry(graph, ids) {
  assert.equal(graph.cardWidth, 240);
  assert.equal(graph.cardHeight, 132);
  assert.ok(Number.isFinite(graph.width) && graph.width >= 0);
  assert.ok(Number.isFinite(graph.height) && graph.height >= 0);
  for (const id of ids) {
    const p = graph.positions.get(id);
    assert.ok(p, `position exists for task ${id}`);
    assert.ok(Number.isFinite(p.x) && p.x >= 0);
    assert.ok(Number.isFinite(p.y) && p.y >= 0);
    assert.ok(p.x + graph.cardWidth <= graph.width);
    assert.ok(p.y + graph.cardHeight <= graph.height);
  }
  for (let i = 0; i < ids.length; i++) {
    for (let j = i + 1; j < ids.length; j++) {
      const a = graph.positions.get(ids[i]), b = graph.positions.get(ids[j]);
      assert.ok(a.x + graph.cardWidth <= b.x || b.x + graph.cardWidth <= a.x ||
        a.y + graph.cardHeight <= b.y || b.y + graph.cardHeight <= a.y,
      `tasks ${ids[i]} and ${ids[j]} do not overlap`);
    }
  }
}

test("single chain and multi-parent graph place every upstream node above its downstream node", () => {
  const { get } = fixture();
  const layout = get("graphLayout");
  for (const edges of [[[1, 2], [2, 3]], [[1, 3], [2, 3]], [[1, 2], [1, 3], [2, 4], [3, 4]]]) {
    const ids = [...new Set(edges.flat())];
    const graph = layout(ids.map((id) => task(id)), edges, 1000);
    checkGeometry(graph, ids);
    assert.equal(graph.cycles, false);
    for (const [from, to] of edges) {
      assert.ok(graph.positions.get(from).y + graph.cardHeight < graph.positions.get(to).y);
    }
  }
});

test("disconnected relationship blocks wrap to available width instead of becoming a long row", () => {
  const { get } = fixture();
  const nodes = Array.from({ length: 20 }, (_, i) => task(i + 1));
  const edges = Array.from({ length: 10 }, (_, i) => [i * 2 + 1, i * 2 + 2]);
  const layout = get("graphLayout");
  const narrow = layout(nodes, edges, 760), wide = layout(nodes, edges, 1864);
  checkGeometry(narrow, nodes.map((t) => t.id));
  checkGeometry(wide, nodes.map((t) => t.id));
  assert.ok(narrow.width <= 760);
  assert.ok(wide.width <= 1864);
  assert.ok(narrow.height > wide.height, "narrow viewport uses more component rows");
  assert.ok(new Set(nodes.filter((t) => t.id % 2).map((t) => narrow.positions.get(t.id).y)).size > 1);
});

test("layout handles multi-branch graphs and cycles with finite nonoverlapping nodes", () => {
  const { get } = fixture();
  const layout = get("graphLayout");
  const nodes = Array.from({ length: 9 }, (_, i) => task(i + 1));
  const edges = nodes.slice(1).map((t) => [1, t.id]);
  const graph = layout(nodes, edges, 390);
  checkGeometry(graph, nodes.map((t) => t.id));
  assert.ok(graph.width > 390, "a wide connected branch remains scrollable instead of overlapping");
  const cycle = layout([task(1), task(2), task(3)], [[1, 2], [2, 1], [2, 3]], 760);
  checkGeometry(cycle, [1, 2, 3]);
  assert.equal(cycle.cycles, true);
});

test("compact nodes retain task identity, owner, status and percentage without detailed notes", () => {
  const tasks = [task(1, { title: "哨兵", latest_note: "这条长备注只应在详情显示" }), task(2, { title: "哨兵视觉", parent_id: 1 })];
  const { get } = fixture(tasks);
  const html = get("renderTree")(tasks, 1000);
  assert.equal(count(html, "tree-task-card"), 2);
  assert.match(html, /data-task-id="1"/);
  assert.match(html, /role="button"/);
  assert.match(html, /tabindex="0"/);
  assert.match(html, /负责人甲/);
  assert.match(html, /进行中/);
  assert.match(html, /role="progressbar"/);
  assert.match(html, /35%/);
  assert.doesNotMatch(html, /这条长备注只应在详情显示/);
});

test("isolated tasks use a closed details list and do not inflate the graph", () => {
  const tasks = [task(1), task(2, { parent_id: 1 }), task(3), task(4, { status: "已完成" })];
  const { get } = fixture(tasks);
  const html = get("renderTree")(tasks, 1000);
  const details = html.match(/<details\b[^>]*class="[^"]*tree-isolated[^>]*>[\s\S]*?<\/details>/)?.[0];
  assert.ok(details, "independent tasks have a disclosure list");
  assert.doesNotMatch(details.match(/^<details[^>]*>/)[0], /\bopen(?:\s|=|>)/);
  assert.match(details, /独立任务[（\s]*2\s*项/);
  assert.match(details, /data-task-id="3"/);
  assert.match(details, /data-task-id="4"/);
  assert.doesNotMatch(html.replace(details, ""), /data-task-id="[34]"/);
});

test("filtering a parent without its child never reclassifies the parent as independent", () => {
  const tasks = [task(1, { title: "匹配父任务" }), task(2, { parent_id: 1 }), task(3)];
  const { state, get } = fixture(tasks);
  state.filters.q = "匹配父任务";
  const html = get("renderTree")([tasks[0]], 760);
  assert.match(html, /data-task-id="1"/);
  assert.doesNotMatch(html, /data-task-id="[23]"/);
  assert.doesNotMatch(html, /独立任务[（\s]*1\s*项/);
});

test("filtering a child retains all authorized upstream context but excludes unrelated tasks", () => {
  const tasks = [task(1), task(2), task(3, { parent_id: 1, predecessor_ids: [2] }), task(4)];
  const { state, get } = fixture(tasks);
  state.filters.q = "任务 3";
  const html = get("renderTree")([tasks[2]], 760);
  for (const id of [1, 2, 3]) assert.match(html, new RegExp(`data-task-id="${id}"`));
  assert.doesNotMatch(html, /data-task-id="4"/);
  assert.equal(count(html, "dependency-edge"), 2);
  assert.match(html, /is-context/);
});

test("all-independent and empty selections provide the relation-empty message", () => {
  const tasks = [task(1), task(2)];
  const { state, get } = fixture(tasks);
  const html = get("renderTree")(tasks, 390);
  assert.match(html, /当前筛选下暂无任务关系/);
  assert.match(html, /独立任务[（\s]*2\s*项/);
  assert.equal(count(html, "dependency-edge"), 0);
  state.filters.q = "不匹配";
  const empty = get("renderTree")([], 390);
  assert.match(empty, /当前筛选下暂无任务关系/);
  assert.doesNotMatch(empty, /data-task-id=/);
});

test("parallel parent/dependency relationships produce one accessible labeled edge", () => {
  const tasks = [task(1, { title: "哨兵" }), task(2, { title: "哨兵视觉", parent_id: 1, predecessor_ids: [1, 1] })];
  const { get } = fixture(tasks);
  const html = get("renderTree")(tasks, 1000);
  assert.equal(count(html, "dependency-edge"), 1);
  assert.equal(count(html, "dependency-edge-hit"), 1);
  const hit = html.match(/<path\b[^>]*class="dependency-edge-hit[^>]*>/)?.[0];
  assert.ok(hit);
  assert.match(hit, /data-edge-from="1"/);
  assert.match(hit, /data-edge-to="2"/);
  assert.match(hit, /data-edge-key=/);
  assert.match(hit, /data-edge-label="[^"]*哨兵[^"]*哨兵视觉/);
  assert.match(hit, /父子[／/]前置/);
  assert.match(hit, /tabindex="0"/);
});

test("edge labels distinguish parenthood from prerequisite relationships and escape task titles", () => {
  const dangerous = '<img src=x onerror="alert(1)">';
  const tasks = [task(1, { title: dangerous }), task(2, { title: "视觉", parent_id: 1 }), task(3, { title: "联调", predecessor_ids: [2] })];
  const { get } = fixture(tasks);
  const html = get("renderTree")(tasks, 1000);
  assert.match(html, /（父）/);
  assert.match(html, /（子）/);
  assert.match(html, /（前置）/);
  assert.match(html, /（后续）/);
  assert.match(html, /&lt;img src=x onerror=&quot;alert\(1\)&quot;&gt;/);
  assert.doesNotMatch(html, /<img|<script/);
});

test("rendering a cycle preserves all task identities and warns without hanging", () => {
  const tasks = [task(1, { parent_id: 2 }), task(2, { parent_id: 1 })];
  const { get } = fixture(tasks);
  const html = get("renderTree")(tasks, 760);
  assert.match(html, /检测到循环关系/);
  assert.match(html, /data-task-id="1"/);
  assert.match(html, /data-task-id="2"/);
});

test("zoom limits remain usable and pointer anchors preserve the same graph coordinate", () => {
  const context = { console };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(root, "static/task-tree-interactions.js"), "utf8"), context);
  const clamp = vm.runInContext("treeClampZoom", context);
  const anchor = vm.runInContext("treeZoomAnchor", context);
  assert.equal(clamp(.01), .25);
  assert.equal(clamp(3), 2);
  assert.equal(clamp(1.25), 1.25);
  for (const [scroll, pointer, previous, next] of [[200, 120, 1, 2], [400, 60, 2, 1], [40, 10, .25, 1]]) {
    const after = anchor(scroll, pointer, previous, next);
    assert.equal((scroll + pointer) / previous, (after + pointer) / next);
  }
  assert.equal(anchor(0, 200, 1, .25), 0, "zooming out never scrolls past the origin");
});
