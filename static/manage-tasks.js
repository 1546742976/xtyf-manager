"use strict";

const taskFilters = { q: "", project: "", status: "" };

function renderCreateChoices() {
  const owner = document.getElementById("create-owner");
  const project = document.getElementById("create-project");
  refreshDraftSelect(owner, state.members, "未指定");
  refreshDraftSelect(project, state.projects, "未选择");
  const participants = document.getElementById("create-participants");
  const dependencies = document.getElementById("create-dependencies");
  const parent = document.getElementById("create-parent");
  refreshDraftChoices(participants, "participant_ids", state.members, (member) => member.name);
  refreshDraftChoices(dependencies, "predecessor_ids", state.tasks, taskLabel);
  refreshDraftSelect(parent, state.tasks, "无父任务");
}

function fillTaskFilters() {
  const project = document.getElementById("t-filter-project");
  const status = document.getElementById("t-filter-status");
  if (project) project.innerHTML = options(state.projects, taskFilters.project, "全部组别");
  if (status)
    status.innerHTML =
      '<option value="">全部状态</option>' +
      state.statuses.map((value) => `<option ${value === taskFilters.status ? "selected" : ""}>${esc(value)}</option>`).join("");
}

function filteredManageTasks() {
  const tasks = state.tasks.filter((task) => {
    if (taskFilters.project && Number(task.project_id) !== Number(taskFilters.project)) return false;
    if (taskFilters.status && task.status !== taskFilters.status) return false;
    const people = taskParticipants(task)
      .map((id) => memberById(id)?.name || "")
      .join(" ");
    const haystack = `${task.title} ${task.detail || ""} ${taskProjectName(task)} ${people}`.toLowerCase();
    return !taskFilters.q || haystack.includes(taskFilters.q.toLowerCase());
  });
  return taskFilters.project ? sortSpecialtyTasks(tasks) : tasks;
}

function renderTaskTable() {
  const body = document.querySelector("#task-table tbody");
  if (!body) return;
  const tasks = filteredManageTasks();
  const scheduleButton = document.getElementById("schedule-project");
  if (scheduleButton) scheduleButton.disabled = !taskFilters.project || !isManagement() || projectById(taskFilters.project)?.is_active === 0;
  document.getElementById("task-count").textContent = `共 ${tasks.length} / ${state.tasks.length} 条`;
  body.innerHTML =
    tasks
      .map((task) => {
        const additional = (task.participant_ids || []).map((id) => memberById(id)?.name || `#${id}`).join("、") || "—";
        return `<tr><td>${Number(task.id)}</td><td class="cell-title">${esc(task.title)}</td><td>${esc(memberById(task.owner_id)?.name || "未指定")}</td><td>${esc(specialtyTaskStage(task) || "未归类")}</td><td>${esc(additional)}</td><td>${esc(taskProjectName(task) || "—")}</td><td class="nowrap">${esc(task.start_date || "未设置")}</td><td class="nowrap">${esc(task.due_date || "未设置")}</td><td>${priorityCell(task.priority)}</td><td>${esc(task.status)}</td><td>${badge(daysLeftText(task), daysLeftClass(task))}</td><td class="nowrap"><button class="btn ghost sm" type="button" data-edit-task="${Number(task.id)}">编辑</button><button class="btn ghost sm danger-text" type="button" data-delete-task="${Number(task.id)}">删除</button></td></tr>`;
      })
      .join("") || '<tr><td colspan="12" class="empty">没有符合条件的任务</td></tr>';
}

function catalogPanel(kind, items) {
  const label = kind === "projects" ? "造车组别" : "专业组";
  const rows =
    items
      .map(
        (item) =>
          `<li><span>${esc(item.name)}</span><small>#${Number(item.id)}</small><button class="btn ghost sm" type="button" data-edit-catalog="${kind}" data-catalog-id="${Number(item.id)}" data-catalog-name="${esc(item.name)}">重命名</button></li>`,
      )
      .join("") || '<li class="muted">暂无条目</li>';
  return `<section class="catalog-block"><h3>${label}</h3><form class="catalog-form" data-catalog-form="${kind}"><label><span>新增${label}</span><input name="name" maxlength="80" required></label><button class="btn" type="submit">添加</button><p class="form-message" role="alert"></p></form><ul class="catalog-list">${rows}</ul></section>`;
}

function renderCatalogs() {
  const target = document.getElementById("catalog-grid");
  if (target) target.innerHTML = catalogPanel("projects", state.projects);
}

function taskPayload(form) {
  const payload = Object.fromEntries(new FormData(form));
  payload.participant_ids = checkedValues(form, "participant_ids");
  payload.predecessor_ids = checkedValues(form, "predecessor_ids");
  if (state.me?.role !== "admin") delete payload.parent_id;
  return payload;
}

function bindTaskManagement() {
  [
    ["t-filter-q", "q", "input"],
    ["t-filter-project", "project", "change"],
    ["t-filter-status", "status", "change"],
  ].forEach(([id, key, event]) => {
    document.getElementById(id)?.addEventListener(event, (change) => {
      taskFilters[key] = change.target.value.trim();
      renderTaskTable();
    });
  });
  document.getElementById("create-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    if (!form.reportValidity()) return;
    const payload = taskPayload(form);
    if (!payload.owner_id && payload.participant_ids.length) {
      form.elements.owner_id.setCustomValidity("选择参与成员后必须指定负责人");
      form.elements.owner_id.reportValidity();
      return;
    }
    const button = form.querySelector("button[type=submit]");
    button.disabled = true;
    const result = await api("/api/task", payload);
    button.disabled = false;
    if (result?.ok) {
      form.reset();
      form.querySelector(".form-message").textContent = "任务已创建";
      refresh();
    } else form.querySelector(".form-message").textContent = result?.error || "创建失败";
  });
  document.getElementById("create-owner")?.addEventListener("change", (event) => event.target._draftValidate?.());
  document.getElementById("task-table")?.addEventListener("click", async (event) => {
    const edit = event.target.closest("[data-edit-task]");
    const remove = event.target.closest("[data-delete-task]");
    if (edit) openTaskModal(Number(edit.dataset.editTask));
    if (!remove || !confirm("确定删除该任务？存在子任务或依赖引用时将无法删除。")) return;
    const task = state.tasks.find((item) => Number(item.id) === Number(remove.dataset.deleteTask));
    if (!task) {
      alert("任务数据已变化，请刷新后重试。");
      await refresh();
      return;
    }
    remove.disabled = true;
    const result = await api(`/api/task/${task.id}/delete`, { version: Number(task.version) });
    if (result?.ok) await refresh();
    else {
      remove.disabled = false;
      if (result?.httpStatus === 409) {
        alert(`任务已被他人更新，未删除。页面将刷新，请重试。${result.error ? `\n${result.error}` : ""}`);
        await refresh();
      } else alert(result?.error || "删除失败");
    }
  });
}

function bindCatalogs() {
  const grid = document.getElementById("catalog-grid");
  grid?.addEventListener("submit", async (event) => {
    const form = event.target.closest("[data-catalog-form]");
    if (!form) return;
    event.preventDefault();
    const result = await api(`/api/${form.dataset.catalogForm}`, { name: new FormData(form).get("name") });
    if (result?.ok) {
      form.reset();
      await refresh();
    } else form.querySelector(".form-message").textContent = result?.error || "添加失败";
  });
  grid?.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-edit-catalog]");
    if (!button) return;
    const name = prompt("输入新名称", button.dataset.catalogName);
    if (name === null || !name.trim()) return;
    const result = await api(`/api/${button.dataset.editCatalog}/${button.dataset.catalogId}`, { name: name.trim() });
    if (result?.ok) refresh();
    else alert(result?.error || "重命名失败");
  });
}

addDataListener(() => {
  renderCreateChoices();
  fillTaskFilters();
  renderTaskTable();
  renderCatalogs();
});
document.addEventListener("DOMContentLoaded", () => {
  bindTaskManagement();
  bindCatalogs();
});
