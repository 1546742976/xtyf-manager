"use strict";

// Every predecessor and legacy parent is an upstream parent in the graph.
function graphParents(task) {
  return [...new Set([task.parent_id, ...(task.predecessor_ids || [])].map(Number).filter((id) => id > 0 && id !== Number(task.id)))];
}

function treeVisibleSet(matches) {
  const byId = new Map(state.tasks.map((task) => [Number(task.id), task]));
  const visible = new Set(matches.map((task) => Number(task.id)));
  const context = new Set();
  const stack = [...visible];
  while (stack.length) {
    const task = byId.get(stack.pop());
    if (!task) continue;
    for (const id of graphParents(task)) {
      if (!byId.has(id) || visible.has(id)) continue;
      visible.add(id);
      context.add(id);
      stack.push(id);
    }
  }
  return { visible, context, byId };
}

function graphLayout(nodes, links) {
  const incoming = new Map(nodes.map((task) => [Number(task.id), []]));
  const outgoing = new Map(nodes.map((task) => [Number(task.id), []]));
  const indegree = new Map(nodes.map((task) => [Number(task.id), 0]));
  links.forEach(([from, to]) => {
    incoming.get(to).push(from);
    outgoing.get(from).push(to);
    indegree.set(to, indegree.get(to) + 1);
  });
  const queue = nodes.filter((t) => indegree.get(Number(t.id)) === 0).map((t) => Number(t.id));
  const order = [];
  const depth = new Map(nodes.map((t) => [Number(t.id), 0]));
  while (queue.length) {
    const id = queue.shift();
    order.push(id);
    for (const child of outgoing.get(id)) {
      depth.set(child, Math.max(depth.get(child), depth.get(id) + 1));
      indegree.set(child, indegree.get(child) - 1);
      if (indegree.get(child) === 0) queue.push(child);
    }
  }
  const cycles = order.length !== nodes.length;
  // Corrupt legacy edges must not hang the renderer.
  nodes.forEach((task) => { if (!order.includes(Number(task.id))) order.push(Number(task.id)); });
  const levels = new Map();
  order.forEach((id) => {
    const layer = depth.get(id);
    if (!levels.has(layer)) levels.set(layer, []);
    levels.get(layer).push(id);
  });
  const cardWidth = 300, cardHeight = 220, gapX = 36, gapY = 100, margin = 28;
  const widest = Math.max(1, ...[...levels.values()].map((ids) => ids.length));
  const width = Math.max(700, margin * 2 + widest * cardWidth + (widest - 1) * gapX);
  const maxLayer = Math.max(0, ...levels.keys());
  const height = margin * 2 + (maxLayer + 1) * cardHeight + maxLayer * gapY;
  const positions = new Map();
  [...levels.entries()].sort((a, b) => a[0] - b[0]).forEach(([layer, ids]) => {
    ids.sort((a, b) => {
      const avg = (id) => {
        const parents = incoming.get(id).map((p) => positions.get(p)?.x).filter((x) => x !== undefined);
        return parents.length ? parents.reduce((x, y) => x + y, 0) / parents.length : id;
      };
      return avg(a) - avg(b);
    });
    const rowWidth = ids.length * cardWidth + (ids.length - 1) * gapX;
    const startX = (width - rowWidth) / 2;
    ids.forEach((id, index) => positions.set(id, {
      x: startX + index * (cardWidth + gapX), y: margin + layer * (cardHeight + gapY),
    }));
  });
  return { positions, width, height, cardWidth, cardHeight, cycles };
}

function renderTree(tasks) {
  const filtered = Object.values(state.filters).some(Boolean);
  const { visible, context, byId } = treeVisibleSet(tasks);
  const matches = new Set(tasks.map((task) => Number(task.id)));
  const nodes = state.tasks.filter((task) => !filtered || visible.has(Number(task.id)));
  if (!nodes.length) return '<p class="empty">没有符合条件的任务</p>';
  const edges = [];
  nodes.forEach((task) => {
    const to = Number(task.id);
    graphParents(task).forEach((from) => {
      if (byId.has(from) && (!filtered || visible.has(from))) edges.push([from, to]);
    });
  });
  const graph = graphLayout(nodes, edges);
  const arrowPaths = edges.map(([from, to]) => {
    const p = graph.positions.get(from), c = graph.positions.get(to);
    if (!p || !c) return "";
    const x1 = p.x + graph.cardWidth / 2, y1 = p.y + graph.cardHeight;
    const x2 = c.x + graph.cardWidth / 2, y2 = c.y;
    const bend = Math.max(35, Math.abs(y2 - y1) / 2);
    return `<path d="M ${x1} ${y1} C ${x1} ${y1 + bend}, ${x2} ${y2 - bend}, ${x2} ${y2}" />`;
  }).join("");
  const cards = nodes.map((task) => {
    const id = Number(task.id), pos = graph.positions.get(id);
    const contextual = context.has(id) && !matches.has(id);
    return `<div class="dependency-node${contextual ? " is-context" : ""}" style="left:${pos.x}px;top:${pos.y}px;width:${graph.cardWidth}px;height:${graph.cardHeight}px">${taskCard(task, contextual ? "context-task" : "")}</div>`;
  }).join("");
  return `<div class="graph-caption">箭头表示前置任务 → 后续任务；父任务也视为前置节点。可滚动查看，点击节点编辑关系。</div>
    ${graph.cycles ? '<p class="tree-cycle" role="alert">检测到循环关系：请检查已有任务依赖。</p>' : ""}
    <div class="dependency-scroll" role="region" aria-label="任务依赖关系图" tabindex="0">
      <div class="dependency-graph" style="width:${graph.width}px;height:${graph.height}px">
        <svg class="dependency-lines" viewBox="0 0 ${graph.width} ${graph.height}" aria-hidden="true" xmlns="http://www.w3.org/2000/svg"><defs><marker id="dep-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor" /></marker></defs><g fill="none" stroke="currentColor" stroke-width="2" marker-end="url(#dep-arrow)">${arrowPaths}</g></svg>
        ${cards}
      </div>
    </div>`;
}
