"use strict";

const SPECIALTY_STAGES = ["机械", "电控", "视觉"];
function specialtyTaskStage(task) {
  if (Object.prototype.hasOwnProperty.call(task, "owner_stage")) return task.owner_stage;
  const owner = memberById(task.owner_id);
  const names = (owner?.group_ids || []).map((id) => groupById(id)?.name).filter(Boolean);
  if (!names.length) names.push(...(owner?.group_name || "").split(";"));
  const choices = SPECIALTY_STAGES.filter((stage) => names.includes(stage));
  return choices.length === 1 ? choices[0] : choices.includes(task.schedule_stage) ? task.schedule_stage : null;
}
function compareSpecialtyTasks(a, b) {
  const rank = (task) => {
    const index = SPECIALTY_STAGES.indexOf(specialtyTaskStage(task));
    return index < 0 ? SPECIALTY_STAGES.length : index;
  };
  return rank(a) - rank(b) || (a.start_date || "9999-99-99").localeCompare(b.start_date || "9999-99-99") ||
    (a.due_date || "9999-99-99").localeCompare(b.due_date || "9999-99-99") || Number(a.id) - Number(b.id);
}
function sortSpecialtyTasks(tasks) { return [...tasks].sort(compareSpecialtyTasks); }
function specialtyLanes(tasks) {
  const sorted = sortSpecialtyTasks(tasks);
  const lanes = SPECIALTY_STAGES.map((stage) => ({key: `stage-${stage}`, title: stage, sub: "负责人专业组", tasks: sorted.filter((task) => specialtyTaskStage(task) === stage)}));
  const unclassified = sorted.filter((task) => !SPECIALTY_STAGES.includes(specialtyTaskStage(task)));
  if (unclassified.length) lanes.push({key: "stage-other", title: "未归类", sub: "未指定或待选择阶段", tasks: unclassified});
  return lanes;
}

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
  const tasks = state.tasks.filter(taskMatches);
  return state.filters.project ? sortSpecialtyTasks(tasks) : tasks;
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
  const note = String(task.latest_note ?? task.latest_progress?.note ?? '').trim();
  const noteAuthor = task.latest_note != null ? task.latest_note_author : task.latest_progress?.actor_name;
  const lead = memberById(task.owner_id);
  const participants = taskParticipants(task).map((id) => memberById(id)?.name || `成员 #${id}`);
  const warnings = task.dependency_warnings || [];
  const meta = [taskProjectName(task), participants.join("、")].filter(Boolean);
  return `<article class="task ${task.status === "阻塞" ? "is-blocked" : ""} ${isOverdue(task) ? "overdue" : ""} ${isDueSoon(task) ? "is-due-soon" : ""} ${warnings.length ? "has-warning" : ""} ${extraClass}" data-task-id="${Number(task.id)}" role="button" tabindex="0" aria-label="查看任务 ${esc(task.title)}">
    <div class="task-head"><h3>${esc(task.title)}</h3>${badge(task.status, STATUS_CLASS[task.status] || "gray")}</div>
    <div class="task-meta">${badge(`${task.priority}优先`, PRIORITY_CLASS[task.priority] || "gray")}${meta.map((value) => `<span>${esc(value)}</span>`).join("")}</div>
    ${warnings.length ? `<p class="task-warning">依赖提醒：${esc(warnings.join("；"))}</p>` : ""}
    ${state.view === "specialty" ? `<p class="task-plan-dates">计划：${esc(task.start_date || "未设开始")} → ${esc(task.due_date || "未设截止")}</p>` : ""}
    <div class="task-progress-heading"><span>任务进度</span><strong>${Math.max(0, Math.min(100, Number(task.progress) || 0))}%</strong></div>
    <div class="progress" role="progressbar" aria-label="任务进度" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.max(0, Math.min(100, Number(task.progress) || 0))}"><div class="progress-inner blue" style="width:${Math.max(0, Math.min(100, Number(task.progress) || 0))}%"></div></div>
    <div class="task-note ${!note ? 'is-empty' : ''}"><span>${esc(note ? (noteAuthor || '成员') : '备注')}</span><p>${esc(note || '无备注')}</p></div>
    <div class="task-foot"><span>${badge(daysLeftText(task), daysLeftClass(task))}</span><span>更新 ${esc(task.updated_at)}</span></div>
  </article>`;
}
function participantLanes(tasks) {
  const active = state.members.filter((member) => member.role !== "admin").map((member) => ({
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
    if (member?.role === "admin") return;
    active.push({
      key: `m-${id}`,
      title: member?.name || `成员 #${id}`,
      sub: "历史成员",
      tasks: tasks.filter((task) => taskParticipants(task).includes(id)),
    });
  });
  const unassigned = tasks.filter((task) => !taskParticipants(task).length);
  if (unassigned.length)
    active.unshift({
      key: "unassigned",
      title: "未分配",
      sub: "",
      tasks: unassigned,
    });
  return active;
}
const expandedPersonLanes = new Set();
function renderLanes(tasks) {
  const lanes =
    state.view === "specialty" ? specialtyLanes(tasks) : state.view === "person"
      ? participantLanes(tasks)
      : state.statuses.map((status) => ({
          key: `s-${status}`,
          title: status,
          sub: "",
          tasks: tasks.filter((task) => task.status === status),
        }));
  const filtering = Object.values(state.filters).some(Boolean);
  const visible = filtering && state.view !== "specialty" ? lanes.filter((lane) => lane.tasks.length) : lanes;
  return (
    visible.map((lane) => {
      const person = state.view === 'person';
      const expanded = expandedPersonLanes.has(lane.key);
      const shown = person && !expanded ? lane.tasks.slice(0, 1) : lane.tasks;
      return `<section class="lane ${person ? 'person-lane' : ''} ${lane.key === "unassigned" ? "unassigned-lane" : ""}"><header class="lane-head"><h2>${esc(lane.title)}</h2>${lane.sub ? `<span class="lane-sub">${esc(lane.sub)}</span>` : ""}<span class="lane-count">${new Set(lane.tasks.map((task) => task.id)).size}</span></header><div class="lane-body" id="lane-${esc(lane.key)}">${shown.map((task) => taskCard(task)).join("") || '<p class="empty">暂无任务</p>'}</div>${person ? `<footer class="lane-actions">${lane.tasks.length > 1 ? `<button class="btn ghost sm" type="button" data-lane-toggle="${esc(lane.key)}" aria-expanded="${expanded}" aria-controls="lane-${esc(lane.key)}">${expanded ? '收起任务' : `展开其余 ${lane.tasks.length - 1} 项`}</button>` : '<span>已显示全部任务</span>'}</footer>` : ''}</section>`;
    })
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
  document.querySelectorAll("#view-seg button").forEach((button) => {
    const selected = button.dataset.view === state.view;
    button.classList.toggle("active", selected);
    button.setAttribute("aria-pressed", String(selected));
    if (button.dataset.view === "specialty") button.disabled = !state.filters.project;
  });
  const tasks = filteredTasks();
  target.classList.toggle("tree-board", state.view === "tree");
  target.classList.toggle("timeline-board", state.view === "specialty");
  const focused = target.contains(document.activeElement) ? document.activeElement : null;
  const focusLane = focused?.dataset.laneToggle;
  const focusTask = focused?.dataset.taskId;
  target.innerHTML = state.view === "tree" ? renderTree(tasks)
    : state.view === "specialty" ? renderSpecialtyTimeline(tasks) : renderLanes(tasks);
  if (focusLane) target.querySelector(`[data-lane-toggle="${CSS.escape(focusLane)}"]`)?.focus({preventScroll: true});
  else if (focusTask) target.querySelector(`[data-task-id="${Number(focusTask)}"]`)?.focus({preventScroll: true});
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
      if (key === "project") state.view = state.filters.project ? "specialty" : state.view === "specialty" ? "person" : state.view;
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
    const laneToggle = event.target.closest('[data-lane-toggle]');
    if (laneToggle) {
      const key = laneToggle.dataset.laneToggle;
      expandedPersonLanes.has(key) ? expandedPersonLanes.delete(key) : expandedPersonLanes.add(key);
      renderBoard();
      return;
    }
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
