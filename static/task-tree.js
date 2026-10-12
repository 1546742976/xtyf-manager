"use strict";

function treeVisibleSet(matches) {
  const byId = new Map(state.tasks.map((task) => [Number(task.id), task]));
  const visible = new Set(matches.map((task) => Number(task.id)));
  const context = new Set();

  matches.forEach((task) => {
    let current = task;
    const seen = new Set([Number(task.id)]);
    while (current?.parent_id) {
      const parentId = Number(current.parent_id);
      if (seen.has(parentId)) break;
      seen.add(parentId);
      visible.add(parentId);
      context.add(parentId);
      current = byId.get(parentId);
    }
  });
  return { visible, context, byId };
}

function renderTree(tasks) {
  const filtered = Object.values(state.filters).some(Boolean);
  const { visible, context, byId } = treeVisibleSet(tasks);
  const matchedIds = new Set(tasks.map((task) => Number(task.id)));
  const children = new Map();

  state.tasks.forEach((task) => {
    const parent = Number(task.parent_id) || 0;
    if (!children.has(parent)) children.set(parent, []);
    children.get(parent).push(task);
  });

  const renderNode = (task, depth, path) => {
    const id = Number(task.id);
    if (path.has(id)) {
      return `<div class="tree-cycle" role="alert">检测到循环关系：${esc(task.title)}</div>`;
    }
    if (filtered && !visible.has(id)) return "";

    const kids = (children.get(id) || []).filter((child) => !filtered || visible.has(Number(child.id)));
    const expanded = state.expanded.has(id) || filtered;
    const contextual = context.has(id) && !matchedIds.has(id);
    const next = new Set(path);
    next.add(id);

    return `<div class="tree-node" style="--tree-depth:${depth}"><div class="tree-row ${contextual ? "is-context" : ""}">${kids.length ? `<button class="tree-toggle" type="button" data-tree-toggle="${id}" aria-expanded="${expanded}" aria-label="${expanded ? "折叠" : "展开"} ${esc(task.title)}">${expanded ? "−" : "+"}</button>` : '<span class="tree-spacer"></span>'}<div class="tree-card">${taskCard(task, contextual ? "context-task" : "")}${contextual ? '<span class="context-label">上下文</span>' : ""}</div></div>${expanded ? `<div class="tree-children">${kids.map((child) => renderNode(child, depth + 1, next)).join("")}</div>` : ""}</div>`;
  };

  const roots = state.tasks.filter((task) => !task.parent_id || !byId.has(Number(task.parent_id)));
  const seeds = roots.length ? roots : state.tasks;
  const output = seeds.map((task) => renderNode(task, 0, new Set())).join("");
  return `<div class="tree-list">${output || '<p class="empty">没有符合条件的任务</p>'}</div>`;
}
