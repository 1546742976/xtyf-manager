"use strict";
const state = {
  me: null,
  members: [],
  allMembers: [],
  tasks: [],
  groups: [],
  projects: [],
  statuses: [],
  priorities: [],
  view: "person",
  filters: { group: "", project: "", status: "", q: "", mine: false },
  fingerprint: "",
  expanded: new Set(),
  listeners: [],
};
const STATUS_CLASS = {
  未开始: "gray",
  进行中: "blue",
  已完成: "green",
  阻塞: "red",
  已取消: "dark",
};
const PRIORITY_CLASS = { 高: "red", 中: "orange", 低: "gray" };

function api(url, data) {
  if (data === undefined) return apiRequest(url, data);
  const active = document.activeElement;
  const host = active?.closest("form,tr");
  const controls = host ? host.querySelectorAll("button,select") : active?.matches("button,select") ? [active] : [];
  return BusinessUI.action("api:"+url, controls, () => apiRequest(url, data));
}
async function apiRequest(url, data) {
  const init = {
    credentials: "same-origin",
    method: data === undefined ? "GET" : "POST",
  };
  if (data !== undefined) {
    if (data instanceof FormData) init.body = data;
    else {
      init.headers = { "Content-Type": "application/json" };
      init.body = JSON.stringify(data);
    }
  }
  try {
    const response = await BusinessUI.fetch(url, init);
    if (response.status === 401) {
      return {ok:false, error:"登录已失效，请重新登录后再提交；当前输入已保留。", httpStatus:401};
    }
    const payload = await response.json().catch(() => ({ ok: false, error: `请求失败（${response.status}）` }));
    payload.httpStatus = response.status;
    return payload;
  } catch (error) {
    return { ok: false, error: error.message || "网络连接失败，请重试", httpStatus: 0 };
  }
}
function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
}
function memberById(id) {
  return [...state.members, ...state.allMembers].find((member) => Number(member.id) === Number(id)) || null;
}
function groupById(id) {
  return state.groups.find((group) => Number(group.id) === Number(id)) || null;
}
function projectById(id) {
  return state.projects.find((project) => Number(project.id) === Number(id)) || null;
}
function taskParticipants(task) {
  return [...new Set([task.owner_id, ...(task.participant_ids || [])].filter(Boolean).map(Number))];
}
function taskGroups(task) {
  return (task.group_ids || []).map(Number);
}
function isManagement() {
  return !!state.me && ["admin", "manager"].includes(state.me.role);
}
function canEdit(task) {
  return isManagement() || (!!state.me && taskParticipants(task).includes(Number(state.me.id)));
}
// The same 0–3 day threshold is used by both the task center and dashboard.
function isDueSoon(task) {
  return Number.isInteger(task.days_left) && task.days_left >= 0 && task.days_left <= 3 &&
    !["已完成", "已取消"].includes(task.status);
}
function isOverdue(task) {
  return Number.isInteger(task.days_left) && task.days_left < 0 && !["已完成", "已取消"].includes(task.status);
}
function isFinishedTask(task) {
  return task.status === "已完成" || task.status === "已取消";
}
// Capped urgency scores: ownership 28, deadline 44, priority 20, state 8 (max 100).
function taskResponsibilityWeight(task, memberId = null, teamTaskWeight = 0) {
  const id = Number(memberId);
  if (memberId == null || !Number.isSafeInteger(id) || id <= 0) return 0;
  if (Number(task.owner_id) === id) return 28;
  return taskParticipants(task).includes(id) ? 8 : teamTaskWeight;
}
function taskDeadlineWeight(daysLeft) {
  if (!Number.isSafeInteger(daysLeft)) return 0;
  if (daysLeft < 0) return 38 + Math.min(6, -daysLeft);
  if (daysLeft === 0) return 36;
  if (daysLeft <= 3) return 36 - daysLeft * 4;
  if (daysLeft <= 7) return 20 - (daysLeft - 4) * 2;
  if (daysLeft <= 14) return 12 - (daysLeft - 8);
  if (daysLeft <= 30) return Math.max(0, 5 - Math.floor((daysLeft - 15) / 3));
  return 0;
}
function taskUrgencyScore(task, memberId = null, teamTaskWeight = 0) {
  if (isFinishedTask(task)) return 0;
  const priority = { "高": 20, "中": 10, "低": 0 }[task.priority] ?? 0;
  const workState = { "阻塞": 8, "进行中": 4, "未开始": 0 }[task.status] ?? 0;
  return taskResponsibilityWeight(task, memberId, teamTaskWeight) +
    taskDeadlineWeight(task.days_left) + priority + workState;
}
function compareTaskUrgency(a, b, memberId = null, teamTaskWeight = 0) {
  const closedDiff = Number(isFinishedTask(a)) - Number(isFinishedTask(b));
  if (closedDiff) return closedDiff;
  const scoreDiff = taskUrgencyScore(b, memberId, teamTaskWeight) -
    taskUrgencyScore(a, memberId, teamTaskWeight);
  if (scoreDiff) return scoreDiff;
  // Stable tie-breaking never restores unlimited bias for severely overdue tasks.
  const roleDiff = taskResponsibilityWeight(b, memberId, teamTaskWeight) -
    taskResponsibilityWeight(a, memberId, teamTaskWeight);
  if (roleDiff) return roleDiff;
  const priority = (task) => ({ "高": 20, "中": 10, "低": 0 })[task.priority] ?? 0;
  const priorityDiff = priority(b) - priority(a);
  if (priorityDiff) return priorityDiff;
  const workState = (task) => ({ "阻塞": 8, "进行中": 4, "未开始": 0 })[task.status] ?? 0;
  return workState(b) - workState(a) || Number(a.id) - Number(b.id);
}
function daysLeftText(task) {
  if (task.status === "已完成") return "已完成";
  if (task.status === "已取消") return "已取消";
  if (task.days_left == null) return "未设截止";
  if (task.days_left < 0) return `已逾期 ${-task.days_left} 天`;
  return task.days_left === 0 ? "今天截止" : `剩余 ${task.days_left} 天`;
}
function daysLeftClass(task) {
  if (["已完成", "已取消"].includes(task.status) || task.days_left == null) return "gray";
  if (task.days_left < 0) return "red";
  return task.days_left <= 3 ? "orange" : "blue";
}
function badge(text, cls = "gray") {
  return `<span class="badge ${esc(cls)}">${esc(text)}</span>`;
}
function options(items, selected, emptyLabel = "") {
  const empty = emptyLabel ? `<option value="">${esc(emptyLabel)}</option>` : "";
  return empty + items.filter((item) => item.is_active !== 0)
    .map((item) => `<option value="${Number(item.id)}" ${Number(item.id) === Number(selected) ? "selected" : ""}>${esc(item.name)}</option>`).join("");
}

function activeCatalogFilter(items, value) {
  return value && !items.some((item) => item.is_active !== 0 && String(item.id) === String(value)) ? "" : value;
}

function retainArchivedCatalogSelection(value, previousId, items, changed) {
  // Editing unrelated task fields must not silently erase archived historical ownership.
  return !changed && !value && items.some((item) => item.is_active === 0 && Number(item.id) === Number(previousId))
    ? String(previousId) : value;
}

function catalogChoices(name, items, selected = []) {
  const selectedIds = new Set((selected || []).map(Number));
  const active = items.filter((item) => item.is_active !== 0);
  // Preserve archived memberships in submitted forms without displaying them as choices.
  const historical = items.filter((item) => item.is_active === 0 && selectedIds.has(Number(item.id)))
    .map((item) => `<input type="checkbox" name="${esc(name)}" value="${Number(item.id)}" checked hidden>`).join("");
  return choiceGrid(name, active, selected) + historical;
}
function checkedValues(form, name) {
  return [...form.querySelectorAll(`[name="${name}"]:checked`)].map((input) => Number(input.value));
}
function choiceGrid(name, items, selected = [], labeler = (item) => item.name, disabled = false) {
  const values = new Set((selected || []).map(Number));
  return `<div class="choice-grid">${items.map((item) => `<label class="choice-item${item.is_active === 0 ? " is-inactive" : ""}"><input type="checkbox" name="${esc(name)}" value="${Number(item.id)}" ${values.has(Number(item.id)) ? "checked" : ""} ${disabled || (item.is_active === 0 && !values.has(Number(item.id))) ? "disabled" : ""}><span>${esc(labeler(item))}${item.is_active === 0 ? "（已删除）" : ""}</span></label>`).join("") || '<span class="muted">暂无可选项</span>'}</div>`;
}

const DRAFT_INVALID_MESSAGE = "已选项已删除或停用，请重新选择后提交。";
function updateDraftMessage(host, invalid) {
  host.dataset.draftInvalid = invalid ? "1" : "0";
  const form = host.closest("form");
  const message = form?.querySelector(".form-message");
  if (!message) return;
  if (form.querySelector('[data-draft-invalid="1"]')) message.textContent = DRAFT_INVALID_MESSAGE;
  else if (message.textContent === DRAFT_INVALID_MESSAGE) message.textContent = "";
}
function bindDraftValidation(host, validate) {
  host._draftValidate = validate;
  if (!host.dataset.draftBound) {
    host.dataset.draftBound = "1";
    host.addEventListener("change", () => host._draftValidate());
    host.closest("form")?.addEventListener("reset", () => setTimeout(() => {
      if (host.tagName === "SELECT") host.value = "";
      else host.querySelectorAll('input[type="checkbox"]').forEach((input) => { input.checked = false; });
      host._draftValidate();
    }, 0));
  }
  validate();
}
function refreshDraftSelect(host, items, emptyLabel) {
  if (!host) return;
  const selected = host.value;
  const active = items.filter((item) => item.is_active !== 0);
  const allowed = new Set(active.map((item) => String(item.id)));
  host.innerHTML = options(active, selected, emptyLabel);
  if (selected && !allowed.has(selected)) {
    const prior = items.find((item) => String(item.id) === selected);
    host.insertAdjacentHTML("beforeend", `<option value="${esc(selected)}" selected>${esc(prior?.name || `#${selected}`)}（已失效，请重新选择）</option>`);
  }
  host.value = selected;
  bindDraftValidation(host, () => {
    const invalid = !!host.value && !allowed.has(host.value);
    host.setCustomValidity(invalid ? DRAFT_INVALID_MESSAGE : "");
    updateDraftMessage(host, invalid);
  });
}
function refreshDraftChoices(host, name, items, labeler = (item) => item.name) {
  if (!host) return;
  const selected = checkedValues(host, name);
  const active = items.filter((item) => item.is_active !== 0);
  const allowed = new Set(active.map((item) => Number(item.id)));
  const missing = selected.filter((id) => !allowed.has(id)).map((id) => ({
    id, name: `${items.find((item) => Number(item.id) === id)?.name || `#${id}`}（已失效，请取消选择）`, is_active: 0,
  }));
  host.innerHTML = choiceGrid(name, [...active, ...missing], selected,
    (item) => item.is_active === 0 ? item.name : labeler(item));
  bindDraftValidation(host, () => {
    const invalid = checkedValues(host, name).some((id) => !allowed.has(id));
    host.querySelector("input")?.setCustomValidity(invalid ? DRAFT_INVALID_MESSAGE : "");
    updateDraftMessage(host, invalid);
  });
}

let refreshing = false;
// Polling bookkeeping: hidden tabs stop polling, repeated failures back off.
let consecutiveFailures = 0;
let nextSyncAt = 0;
function showSync(ok, message) {
  const element = document.getElementById("sync-status");
  if (!element) return;
  element.textContent = message;
  element.classList.toggle("sync-error", !ok);
}
async function refresh() {
  if (refreshing) return;
  refreshing = true;
  try {
    const data = await api("/api/data");
    if (!data?.ok) {
      consecutiveFailures += 1;
      showSync(false, data?.error || "同步失败");
      return;
    }
    consecutiveFailures = 0;
    state.me = data.me;
    state.members = data.members || [];
    state.allMembers = data.all_members || state.allMembers || [];
    state.tasks = data.tasks || [];
    state.groups = (data.groups || []).map((group) => (typeof group === "string" ? { id: group, name: group } : group));
    state.projects = (data.projects || []).map((project) => (typeof project === "string" ? { id: project, name: project } : project));
    state.statuses = data.statuses || [];
    state.priorities = data.priorities || [];
    showSync(true, `已同步 · ${new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}`);
    const fingerprint = JSON.stringify([data.me, data.members, data.all_members, data.tasks, data.groups, data.projects]);
    if (fingerprint !== state.fingerprint) {
      state.fingerprint = fingerprint;
      state.listeners.forEach((listener) => listener());
    }
  } finally {
    refreshing = false;
  }
}
function addDataListener(listener) {
  state.listeners.push(listener);
}

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("refresh-now")?.addEventListener("click", refresh);
  refresh();
  window.setInterval(() => {
    if (document.hidden) return;                       // 后台标签页停止全量轮询
    if (Date.now() < nextSyncAt) return;               // 连续失败后退避到 30 秒
    nextSyncAt = Date.now() + (consecutiveFailures >= 3 ? 30000 : 0);
    refresh();
  }, 5000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refresh();                   // 回到前台立即同步一次
  });
});
