"use strict";

function taskProjectName(task) {
  return projectById(task.project_id)?.name || task.project_name || task.project || "";
}
function taskGroupNames(task) {
  const names = taskGroups(task)
    .map((id) => groupById(id)?.name)
    .filter(Boolean);
  if (!names.length && task.group_name) names.push(task.group_name);
  return names;
}
function taskMatches(task) {
  const filter = state.filters;
  if (filter.project && Number(task.project_id) !== Number(filter.project) && taskProjectName(task) !== filter.project) return false;
  if (filter.status && task.status !== filter.status) return false;
  if (filter.mine && (!state.me || !taskParticipants(task).includes(Number(state.me.id)))) return false;
  if (filter.q) {
    const people = taskParticipants(task).map((id) => memberById(id)?.name || "");
    const hay = [task.title, task.detail, taskProjectName(task), ...people].join(" ").toLowerCase();
    if (!hay.includes(filter.q.toLowerCase())) return false;
  }
  return true;
}
function filteredTasks() {
  return state.tasks.filter(taskMatches);
}
function renderStats() {
  const active = state.tasks.filter((task) => task.status !== "已取消");
  const metrics = [
    ["总任务", active.length, ""],
    ["进行中", active.filter((task) => task.status === "进行中").length, "blue"],
    ["已完成", active.filter((task) => task.status === "已完成").length, "green"],
    ["阻塞", active.filter((task) => task.status === "阻塞").length, "red"],
    ["逾期", active.filter(isOverdue).length, "orange"],
  ];
  const target = document.getElementById("stats");
  if (target)
    target.innerHTML = metrics
      .map(([label, value, cls]) => `<div class="stat ${cls}"><div class="stat-value">${value}</div><div class="stat-label">${label}</div></div>`)
      .join("");
}
function taskCard(task, extraClass = "") {
  const lead = memberById(task.owner_id);
  const participants = taskParticipants(task).map((id) => memberById(id)?.name || `成员 #${id}`);
  const warnings = task.dependency_warnings || [];
  const meta = [taskProjectName(task), participants.join("、")].filter(Boolean);
  return `<article class="task ${isOverdue(task) ? "overdue" : ""} ${isDueSoon(task) ? "is-due-soon" : ""} ${warnings.length ? "has-warning" : ""} ${extraClass}" data-task-id="${Number(task.id)}" role="button" tabindex="0" aria-label="查看任务 ${esc(task.title)}">
    <div class="task-head"><h3>${esc(task.title)}</h3>${badge(task.status, STATUS_CLASS[task.status] || "gray")}</div>
    <div class="task-meta">${badge(`${task.priority}优先`, PRIORITY_CLASS[task.priority] || "gray")}${meta.map((value) => `<span>${esc(value)}</span>`).join("")}</div>
    ${warnings.length ? `<p class="task-warning">依赖提醒：${esc(warnings.join("；"))}</p>` : ""}
    <div class="task-progress-heading"><span>任务进度</span><strong>${Math.max(0, Math.min(100, Number(task.progress) || 0))}%</strong></div>
    <div class="progress" role="progressbar" aria-label="任务进度" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.max(0, Math.min(100, Number(task.progress) || 0))}"><div class="progress-inner blue" style="width:${Math.max(0, Math.min(100, Number(task.progress) || 0))}%"></div></div>
    ${task.latest_progress ? `<div class="progress-last"><span>${esc(task.latest_progress.actor_name || "成员")} · ${esc(task.latest_progress.created_at || "")}</span>${task.latest_progress.note ? `<p>${esc(task.latest_progress.note.slice(0, 150))}</p>` : ""}${(task.latest_progress.image_ids || []).slice(0, 2).map((id) => `<img src="/api/task/${Number(task.id)}/attachments/${Number(id)}" alt="进度截图" loading="lazy">`).join("")}</div>` : ""}
    <div class="task-foot"><span>${badge(daysLeftText(task), daysLeftClass(task))}</span><span>更新 ${esc(task.updated_at)}</span></div>
  </article>`;
}
function participantLanes(tasks) {
  const active = state.members.map((member) => ({
    key: `m-${member.id}`,
    title: member.name,
    sub: (member.group_names || [member.group_name]).filter(Boolean).join("、"),
    tasks: tasks.filter((task) => taskParticipants(task).includes(Number(member.id))),
  }));
  const known = new Set(state.members.map((member) => Number(member.id)));
  const historical = [];
  tasks.forEach((task) =>
    taskParticipants(task).forEach((id) => {
      if (!known.has(id) && !historical.includes(id)) historical.push(id);
    }),
  );
  historical.forEach((id) => {
    const member = memberById(id);
    active.push({
      key: `m-${id}`,
      title: member?.name || `成员 #${id}`,
      sub: "历史成员",
      tasks: tasks.filter((task) => taskParticipants(task).includes(id)),
    });
  });
  const unassigned = tasks.filter((task) => !taskParticipants(task).length);
  if (unassigned.length)
    active.push({
      key: "unassigned",
      title: "未分配",
      sub: "",
      tasks: unassigned,
    });
  return active;
}
function renderLanes(tasks) {
  const lanes =
    state.view === "person"
      ? participantLanes(tasks)
      : state.statuses.map((status) => ({
          key: `s-${status}`,
          title: status,
          sub: "",
          tasks: tasks.filter((task) => task.status === status),
        }));
  const filtering = Object.values(state.filters).some(Boolean);
  const visible = filtering ? lanes.filter((lane) => lane.tasks.length) : lanes;
  return (
    visible
      .map(
        (lane) =>
          `<section class="lane"><header class="lane-head"><h2>${esc(lane.title)}</h2>${lane.sub ? `<span class="lane-sub">${esc(lane.sub)}</span>` : ""}<span class="lane-count">${new Set(lane.tasks.map((task) => task.id)).size}</span></header><div class="lane-body">${lane.tasks.map((task) => taskCard(task)).join("") || '<p class="empty">暂无任务</p>'}</div></section>`,
      )
      .join("") || '<p class="empty">没有符合条件的任务</p>'
  );
}
function fillBoardFilters() {
  state.filters.project = activeCatalogFilter(state.projects, state.filters.project);
  const project = document.getElementById("f-project"),
    status = document.getElementById("f-status");
  if (project) {
    project.innerHTML = options(state.projects, state.filters.project, "全部组别");
    project.value = state.filters.project;
  }
  if (status) {
    status.innerHTML =
      '<option value="">全部状态</option>' +
      state.statuses.map((item) => `<option ${item === state.filters.status ? "selected" : ""}>${esc(item)}</option>`).join("");
  }
}
function renderBoard() {
  const target = document.getElementById("board");
  if (!target) return;
  const tasks = filteredTasks();
  target.classList.toggle("tree-board", state.view === "tree");
  target.innerHTML = state.view === "tree" ? renderTree(tasks) : renderLanes(tasks);
  document.getElementById("match-count").textContent = `${new Set(tasks.map((task) => task.id)).size} 个匹配任务`;
}
function bindBoard() {
  const target = document.getElementById("board");
  if (!target) return;
  [
    ["f-project", "project", "change"],
    ["f-status", "status", "change"],
    ["f-q", "q", "input"],
  ].forEach(([id, key, event]) =>
    document.getElementById(id)?.addEventListener(event, (e) => {
      state.filters[key] = e.target.value.trim();
      renderBoard();
    }),
  );
  document.getElementById("f-mine")?.addEventListener("change", (event) => {
    state.filters.mine = event.target.checked;
    renderBoard();
  });
  document.getElementById("view-seg")?.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-view]");
    if (!button) return;
    state.view = button.dataset.view;
    document.querySelectorAll("#view-seg button").forEach((item) => {
      item.classList.toggle("active", item === button);
      item.setAttribute("aria-pressed", String(item === button));
    });
    renderBoard();
  });
  target.addEventListener("click", (event) => {
    const toggle = event.target.closest("[data-tree-toggle]");
    if (toggle) {
      const id = Number(toggle.dataset.treeToggle);
      state.expanded.has(id) ? state.expanded.delete(id) : state.expanded.add(id);
      renderBoard();
      return;
    }
    const card = event.target.closest("[data-task-id]");
    if (card) openTaskModal(Number(card.dataset.taskId));
  });
  target.addEventListener("keydown", (event) => {
    const card = event.target.closest("[data-task-id]");
    if (card && ["Enter", " "].includes(event.key)) {
      event.preventDefault();
      openTaskModal(Number(card.dataset.taskId));
    }
  });
  addDataListener(() => {
    renderStats();
    fillBoardFilters();
    renderBoard();
  });
}
document.addEventListener("DOMContentLoaded", bindBoard);
