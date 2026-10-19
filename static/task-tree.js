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

function treeGraphData(tasks) {
  const { visible, context, byId } = treeVisibleSet(tasks);
  const matches = new Set(tasks.map((task) => Number(task.id)));
  const related = new Set();
  const allEdges = [];
  state.tasks.forEach((task) => {
    const to = Number(task.id);
    graphParents(task).forEach((from) => {
      if (!byId.has(from)) return;
      related.add(from);
      related.add(to);
      allEdges.push([from, to]);
    });
  });
  // A filtered-out child must not turn its matching parent into an isolated task.
  const nodes = state.tasks.filter((task) => visible.has(Number(task.id)) && related.has(Number(task.id)));
  const edges = allEdges.filter(([from, to]) => visible.has(from) && visible.has(to));
  const isolated = tasks.filter((task) => !related.has(Number(task.id)));
  return { nodes, edges, isolated, context, matches, byId };
}

function graphLayout(nodes, links, availableWidth = 1000) {
  const cardWidth = 240, cardHeight = 132, gapX = 32, gapY = 72, margin = 24, componentGap = 48;
  const ids = [...new Set(nodes.map((task) => Number(task.id)))].sort((a, b) => a - b);
  const incoming = new Map(ids.map((id) => [id, []]));
  const outgoing = new Map(ids.map((id) => [id, []]));
  const neighbors = new Map(ids.map((id) => [id, []]));
  const seenEdges = new Set();
  links.forEach(([rawFrom, rawTo]) => {
    const from = Number(rawFrom), to = Number(rawTo), key = `${from}:${to}`;
    if (!incoming.has(from) || !incoming.has(to) || seenEdges.has(key)) return;
    seenEdges.add(key);
    incoming.get(to).push(from);
    outgoing.get(from).push(to);
    neighbors.get(from).push(to);
    neighbors.get(to).push(from);
  });

  const components = [];
  const visited = new Set();
  ids.forEach((start) => {
    if (visited.has(start)) return;
    const component = [], pending = [start];
    visited.add(start);
    while (pending.length) {
      const id = pending.pop();
      component.push(id);
      neighbors.get(id).forEach((neighbor) => {
        if (visited.has(neighbor)) return;
        visited.add(neighbor);
        pending.push(neighbor);
      });
    }
    components.push(component.sort((a, b) => a - b));
  });

  let cycles = false;
  const blocks = components.map((component) => {
    const indegree = new Map(component.map((id) => [id, incoming.get(id).length]));
    const depth = new Map(component.map((id) => [id, 0]));
    const queue = component.filter((id) => indegree.get(id) === 0);
    const order = [];
    while (queue.length) {
      const id = queue.shift();
      order.push(id);
      outgoing.get(id).forEach((child) => {
        depth.set(child, Math.max(depth.get(child), depth.get(id) + 1));
        indegree.set(child, indegree.get(child) - 1);
        if (indegree.get(child) === 0) {
          queue.push(child);
          queue.sort((a, b) => a - b);
        }
      });
    }
    if (order.length !== component.length) cycles = true;
    // Keep corrupt legacy cycles visible without repeatedly traversing them.
    const ordered = new Set(order);
    component.forEach((id) => { if (!ordered.has(id)) order.push(id); });
    const levels = new Map();
    order.forEach((id) => {
      const layer = depth.get(id);
      if (!levels.has(layer)) levels.set(layer, []);
      levels.get(layer).push(id);
    });
    const widest = Math.max(1, ...[...levels.values()].map((level) => level.length));
    const width = widest * cardWidth + (widest - 1) * gapX;
    const maxLayer = Math.max(0, ...levels.keys());
    const height = (maxLayer + 1) * cardHeight + maxLayer * gapY;
    const positions = new Map();
    [...levels.entries()].sort((a, b) => a[0] - b[0]).forEach(([layer, level]) => {
      const parentCenter = (id) => {
        const parents = incoming.get(id).map((parent) => positions.get(parent)?.x).filter((x) => x !== undefined);
        return parents.length ? parents.reduce((sum, x) => sum + x, 0) / parents.length : null;
      };
      level.sort((a, b) => {
        const first = parentCenter(a), second = parentCenter(b);
        return first !== null && second !== null ? first - second || a - b : a - b;
      });
      const rowWidth = level.length * cardWidth + (level.length - 1) * gapX;
      const startX = (width - rowWidth) / 2;
      level.forEach((id, index) => positions.set(id, {
        x: startX + index * (cardWidth + gapX), y: layer * (cardHeight + gapY),
      }));
    });
    return { width, height, positions };
  });

  const viewportWidth = Number.isFinite(Number(availableWidth)) && Number(availableWidth) > 0 ? Number(availableWidth) : 1000;
  const rowLimit = Math.max(cardWidth, viewportWidth - margin * 2);
  const positions = new Map();
  let x = margin, y = margin, rowHeight = 0, right = margin;
  blocks.forEach((block) => {
    if (x > margin && x + block.width > margin + rowLimit) {
      x = margin;
      y += rowHeight + componentGap;
      rowHeight = 0;
    }
    block.positions.forEach((position, id) => positions.set(id, { x: x + position.x, y: y + position.y }));
    right = Math.max(right, x + block.width);
    rowHeight = Math.max(rowHeight, block.height);
    x += block.width + componentGap;
  });
  const width = Math.max(cardWidth + margin * 2, right + margin);
  const height = blocks.length ? y + rowHeight + margin : margin * 2;
  return { positions, width, height, cardWidth, cardHeight, cycles };
}

function treeTaskCard(task, contextual = false) {
  const owner = memberById(task.owner_id);
  const ownerName = owner?.name || (task.owner_id ? `成员 #${Number(task.owner_id)}` : "未分配");
  const progress = Math.max(0, Math.min(100, Number(task.progress) || 0));
  const classes = ["task", "tree-task-card", task.status === "阻塞" ? "is-blocked" : "", isOverdue(task) ? "overdue" : "", isDueSoon(task) ? "is-due-soon" : "", contextual ? "context-task" : ""].filter(Boolean).join(" ");
  return `<article class="${classes}" data-task-id="${Number(task.id)}" role="button" tabindex="0" aria-label="查看任务 ${esc(task.title)}${contextual ? "（筛选外关联任务）" : ""}">
    <h3 class="tree-task-title" title="${esc(task.title)}">${esc(task.title)}</h3>
    <div class="tree-task-meta"><span title="负责人：${esc(ownerName)}">${esc(ownerName)}</span>${badge(task.status, STATUS_CLASS[task.status] || "gray")}</div>
    <div class="tree-task-progress"><div class="task-progress-heading"><span>任务进度</span><strong>${progress}%</strong></div>
      <div class="progress" role="progressbar" aria-label="任务进度" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${progress}"><div class="progress-inner blue" style="width:${progress}%"></div></div>
    </div>
  </article>`;
}

function treeEdgeLabel(from, to, byId) {
  const parent = byId.get(from), child = byId.get(to);
  const isParent = Number(child.parent_id) === from;
  const isPredecessor = (child.predecessor_ids || []).some((id) => Number(id) === from);
  if (isParent) return `${parent.title}（主前置） → ${child.title}（后续）${isPredecessor ? "（同时列为其他前置）" : ""}`;
  return `${parent.title}（其他前置） → ${child.title}（后续）`;
}

function renderTree(tasks, availableWidth = 1000) {
  const { nodes, edges, isolated, context, matches, byId } = treeGraphData(tasks);
  const graph = graphLayout(nodes, edges, availableWidth);
  const disabled = nodes.length ? "" : " disabled";
  const arrowPaths = [], hitPaths = [];
  edges.forEach(([from, to]) => {
    const parent = graph.positions.get(from), child = graph.positions.get(to);
    if (!parent || !child) return;
    const x1 = parent.x + graph.cardWidth / 2, y1 = parent.y + graph.cardHeight;
    const x2 = child.x + graph.cardWidth / 2, y2 = child.y;
    const bend = Math.max(35, Math.abs(y2 - y1) / 2);
    const path = `M ${x1} ${y1} C ${x1} ${y1 + bend}, ${x2} ${y2 - bend}, ${x2} ${y2}`;
    const label = esc(treeEdgeLabel(from, to, byId));
    arrowPaths.push(`<path d="${path}" class="dependency-edge" data-edge-key="${from}:${to}" />`);
    hitPaths.push(`<path class="dependency-edge-hit" d="${path}" data-edge-key="${from}:${to}" data-edge-from="${from}" data-edge-to="${to}" data-edge-label="${label}" role="button" tabindex="0" aria-label="${label}" aria-describedby="tree-edge-tooltip" />`);
  });
  const cards = nodes.map((task) => {
    const id = Number(task.id), position = graph.positions.get(id);
    const contextual = context.has(id) && !matches.has(id);
    return `<div class="dependency-node${contextual ? " is-context" : ""}" data-node-id="${id}" style="left:${position.x}px;top:${position.y}px;width:${graph.cardWidth}px;height:${graph.cardHeight}px">${treeTaskCard(task, contextual)}</div>`;
  }).join("");
  const empty = !tasks.length ? "当前筛选下暂无任务关系（没有符合条件的任务）" : "当前筛选下暂无任务关系，独立任务可在下方展开查看。";
  return `<div class="task-tree-view">
    <div class="tree-toolbar" role="toolbar" aria-label="任务关系图缩放">
      <button type="button" class="btn secondary" data-tree-action="out" aria-label="缩小任务关系图"${disabled}>−</button>
      <output data-tree-zoom aria-live="polite" aria-label="当前缩放比例">100%</output>
      <button type="button" class="btn secondary" data-tree-action="in" aria-label="放大任务关系图"${disabled}>+</button>
      <button type="button" class="btn secondary" data-tree-action="reset"${disabled}>100%</button>
      <button type="button" class="btn secondary" data-tree-action="fit"${disabled}>适应视图</button>
    </div>
    <p class="graph-caption">悬停、聚焦或点按连线查看关系；点击任务查看详情。拖动空白处平移，Ctrl＋滚轮或双指缩放。</p>
    ${graph.cycles ? '<p class="tree-cycle" role="alert">检测到循环关系：请检查已有任务依赖。</p>' : ""}
    ${nodes.length && !edges.length ? '<p class="graph-caption">当前筛选未显示连线，调整筛选可查看完整关系。</p>' : ""}
    <div class="dependency-viewport">
      ${nodes.length ? `<div class="dependency-scroll" role="region" aria-label="任务依赖关系图" tabindex="0">
        <div class="dependency-spacer" style="width:${graph.width}px;height:${graph.height}px">
          <div class="dependency-graph" data-graph-width="${graph.width}" data-graph-height="${graph.height}" style="width:${graph.width}px;height:${graph.height}px">
            <svg class="dependency-lines" viewBox="0 0 ${graph.width} ${graph.height}" xmlns="http://www.w3.org/2000/svg">
              <defs><marker id="dep-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor" /></marker></defs>
              <g aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" marker-end="url(#dep-arrow)">${arrowPaths.join("")}</g>
              <g fill="none" stroke="transparent" stroke-width="16">${hitPaths.join("")}</g>
            </svg>
            ${cards}
          </div>
        </div>
      </div>` : `<p class="empty">${empty}</p>`}
      <div data-tree-tooltip role="tooltip" id="tree-edge-tooltip" hidden></div>
    </div>
    ${isolated.length ? `<details class="tree-isolated"><summary data-tree-isolated-toggle>独立任务（${isolated.length} 项）</summary><div class="tree-isolated-list">${isolated.map((task) => treeTaskCard(task)).join("")}</div></details>` : ""}
  </div>`;
}
