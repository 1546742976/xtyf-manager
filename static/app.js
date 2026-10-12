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

async function api(url, data) {
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
    const response = await fetch(url, init);
    if (response.status === 401) {
      location.href = "/login";
      return null;
    }
    const payload = await response.json().catch(() => ({ ok: false, error: `请求失败（${response.status}）` }));
    payload.httpStatus = response.status;
    return payload;
  } catch (_) {
    return { ok: false, error: "网络连接失败，请重试", httpStatus: 0 };
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
function isOverdue(task) {
  return Number.isInteger(task.days_left) && task.days_left < 0 && !["已完成", "已取消"].includes(task.status);
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
  return (
    empty +
    items.map((item) => `<option value="${Number(item.id)}" ${Number(item.id) === Number(selected) ? "selected" : ""}>${esc(item.name)}</option>`).join("")
  );
}
function checkedValues(form, name) {
  return [...form.querySelectorAll(`[name="${name}"]:checked`)].map((input) => Number(input.value));
}
function choiceGrid(name, items, selected = [], labeler = (item) => item.name, disabled = false) {
  const values = new Set((selected || []).map(Number));
  return `<div class="choice-grid">${items.map((item) => `<label class="choice-item${item.is_active === 0 ? " is-inactive" : ""}"><input type="checkbox" name="${esc(name)}" value="${Number(item.id)}" ${values.has(Number(item.id)) ? "checked" : ""} ${disabled ? "disabled" : ""}><span>${esc(labeler(item))}${item.is_active === 0 ? "（已停用）" : ""}</span></label>`).join("") || '<span class="muted">暂无可选项</span>'}</div>`;
}

let refreshing = false;
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
      showSync(false, data?.error || "同步失败");
      return;
    }
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
  window.setInterval(refresh, 5000);
});
