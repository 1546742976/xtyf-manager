"use strict";

let allMembers = [];
const memberFilters = { q: "", group: "", project: "", role: "" };

function roleSelect(member) {
  const disabled = Number(member.id) === Number(state.me?.id) ? "disabled" : "";
  return `<select class="role-select" data-role-id="${Number(member.id)}" ${disabled}><option value="member" ${member.role === "member" ? "selected" : ""}>成员</option><option value="manager" ${member.role === "manager" ? "selected" : ""}>项管</option><option value="admin" ${member.role === "admin" ? "selected" : ""}>管理员</option></select>`;
}

function filteredMembers() {
  return allMembers.filter((member) => {
    if (memberFilters.group && !(member.group_ids || []).map(Number).includes(Number(memberFilters.group))) return false;
    if (memberFilters.project && !(member.project_ids || []).map(Number).includes(Number(memberFilters.project))) return false;
    if (memberFilters.role && member.role !== memberFilters.role) return false;
    const haystack = `${member.name} ${member.username || ""}`.toLowerCase();
    return !memberFilters.q || haystack.includes(memberFilters.q.toLowerCase());
  });
}

function renderMemberChoices() {
  const host = document.getElementById("member-create-groups");
  if (host) host.innerHTML = choiceGrid("group_ids", state.groups);
  const projects = document.getElementById("member-create-projects");
  if (projects) projects.innerHTML = choiceGrid("project_ids", state.projects);
  const imports = document.getElementById("member-import-projects");
  if (imports) {
    const selected = checkedValues(imports.closest("form"), "project_ids");
    imports.innerHTML = choiceGrid("project_ids", state.projects, selected);
  }
  const filter = document.getElementById("m-filter-group");
  if (filter) filter.innerHTML = options(state.groups, memberFilters.group, "全部专业组");
  const projectFilter = document.getElementById("m-filter-project");
  if (projectFilter) projectFilter.innerHTML = options(state.projects, memberFilters.project, "全部造车组别");
}

function renderGroupCatalog() {
  const host = document.getElementById("member-group-catalog");
  if (!host) return;
  host.innerHTML = state.groups.map((group) => {
    const count = allMembers.filter((member) => (member.group_ids || []).map(Number).includes(Number(group.id))).length;
    return `<li><span>${esc(group.name)}</span><small>${count} 人</small><button class="btn ghost sm" type="button" data-rename-member-group="${Number(group.id)}">重命名</button></li>`;
  }).join("") || '<li class="muted">暂无组别</li>';
}

function memberVehicleGroups(member) {
  return (member.project_ids || []).map((id) => projectById(id)?.name).filter(Boolean);
}

function renderVehicleCatalog() {
  const host = document.getElementById("member-project-catalog");
  if (!host) return;
  host.innerHTML = state.projects.map((project) => {
    const count = allMembers.filter((member) => (member.project_ids || []).map(Number).includes(Number(project.id))).length;
    return `<li><span>${esc(project.name)}</span><small>${count} 人</small><button class="btn ghost sm" type="button" data-rename-member-project="${Number(project.id)}">重命名</button></li>`;
  }).join("") || '<li class="muted">暂无造车组别</li>';
}

function renderMemberTable() {
  const body = document.querySelector("#member-table tbody");
  if (!body) return;
  const members = filteredMembers();
  const admin = state.me?.role === "admin";
  document.getElementById("member-count").textContent = `共 ${members.length} / ${allMembers.length} 人`;
  body.innerHTML =
    members
      .map(
        (member) =>
          `<tr><td>${Number(member.id)}</td><td>${esc(member.name)}</td><td>${esc(member.username)}</td><td>${esc(memberGroups(member).join("、") || "未分组")}</td><td>${esc(memberVehicleGroups(member).join("、") || "未分组")}</td><td>${admin ? roleSelect(member) : esc(roleName(member.role))}</td><td>${member.is_active ? "在职" : "停用"}</td><td class="nowrap"><button class="btn ghost sm" type="button" data-member-groups="${Number(member.id)}">专业组</button><button class="btn ghost sm" type="button" data-member-projects="${Number(member.id)}">造车组别</button><button class="btn ghost sm" type="button" data-reset="${Number(member.id)}">重置密码</button><button class="btn ghost sm" type="button" data-toggle="${Number(member.id)}">${member.is_active ? "停用" : "启用"}</button></td></tr>`,
      )
      .join("") || '<tr><td colspan="8" class="empty">没有符合条件的成员</td></tr>';
}

async function loadMembers() {
  const data = await api("/api/members");
  if (!data?.ok) return;
  const expanded = new Map(state.allMembers.map((member) => [Number(member.id), member]));
  allMembers = (data.members || []).map((member) => ({
    ...expanded.get(Number(member.id)),
    ...member,
    group_ids: member.group_ids || expanded.get(Number(member.id))?.group_ids || [],
    project_ids: member.project_ids || expanded.get(Number(member.id))?.project_ids || [],
  }));
  state.allMembers = allMembers;
  renderMemberChoices();
  renderMemberTable();
  renderGroupCatalog();
  renderVehicleCatalog();
}

function editMemberProjects(member) {
  showModal(
    `<div class="modal-head"><h2>编辑 ${esc(member.name)} 的造车组别</h2><button class="btn ghost sm" type="button" data-close>关闭</button></div><form id="member-projects-form"><fieldset><legend>参加的造车组别（可多选）</legend>${choiceGrid("project_ids", state.projects, member.project_ids || [])}</fieldset><p class="form-message" role="alert"></p><div class="modal-actions"><button class="btn primary">保存组别</button></div></form>`,
    `编辑 ${member.name} 的造车组别`,
  );
  document.querySelector("[data-close]").addEventListener("click", hideModal);
  document.getElementById("member-projects-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const result = await api(`/api/member/${member.id}/projects`, { project_ids: checkedValues(form, "project_ids") });
    if (result?.ok) {
      hideModal();
      await refresh();
      await loadMembers();
    } else form.querySelector(".form-message").textContent = result?.error || "保存失败";
  });
}

function editMemberGroups(member) {
  showModal(
    `<div class="modal-head"><h2>编辑 ${esc(member.name)} 的专业组</h2><button class="btn ghost sm" type="button" data-close>关闭</button></div><form id="member-groups-form"><fieldset><legend>专业组（可多选）</legend>${choiceGrid("group_ids", state.groups, member.group_ids || [])}</fieldset><p class="form-message" role="alert"></p><div class="modal-actions"><button class="btn primary">保存组别</button></div></form>`,
    `编辑 ${member.name} 的专业组`,
  );
  document.querySelector("[data-close]").addEventListener("click", hideModal);
  document.getElementById("member-groups-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const result = await api(`/api/member/${member.id}/groups`, { group_ids: checkedValues(form, "group_ids") });
    if (result?.ok) {
      hideModal();
      await refresh();
      await loadMembers();
    } else form.querySelector(".form-message").textContent = result?.error || "保存失败";
  });
}

function bindMemberForms() {
  [
    ["m-filter-q", "q", "input"],
    ["m-filter-group", "group", "change"],
    ["m-filter-project", "project", "change"],
    ["m-filter-role", "role", "change"],
  ].forEach(([id, key, event]) => {
    document.getElementById(id)?.addEventListener(event, (change) => {
      memberFilters[key] = change.target.value.trim();
      renderMemberTable();
    });
  });
  document.getElementById("member-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const payload = Object.fromEntries(new FormData(form));
    payload.group_ids = checkedValues(form, "group_ids");
    payload.project_ids = checkedValues(form, "project_ids");
    const result = await api("/api/member", payload);
    if (result?.ok) {
      form.reset();
      if (result.initial_password) alert(`添加成功，请记录初始密码：\n${result.initial_password}`);
      loadMembers();
    } else form.querySelector(".form-message").textContent = result?.error || "添加失败";
  });
  document.getElementById("import-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const result = await api("/api/members/import", { text: new FormData(form).get("text"), project_ids: checkedValues(form, "project_ids") });
    if (result?.ok) {
      alert(`导入完成，共 ${result.created.length} 人：\n${result.created.join("\n")}`);
      form.reset();
      loadMembers();
    } else form.querySelector(".form-message").textContent = result?.error || "导入失败";
  });
}

function bindGroupCatalog() {
  document.getElementById("member-group-create-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector("button[type=submit]");
    button.disabled = true;
    const result = await api("/api/groups", { name: new FormData(form).get("name") });
    button.disabled = false;
    if (!result?.ok) {
      form.querySelector(".form-message").textContent = result?.error || "新增失败";
      return;
    }
    form.reset();
    form.querySelector(".form-message").textContent = "组别已添加";
    await refresh();
    await loadMembers();
  });
  document.getElementById("member-group-catalog")?.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-rename-member-group]");
    if (!button) return;
    const group = groupById(button.dataset.renameMemberGroup);
    if (!group) return;
    const name = prompt("修改专业组名称（成员和任务的归属会保留）", group.name);
    if (name === null || name.trim() === group.name) return;
    if (!name.trim()) return alert("名称不能为空");
    button.disabled = true;
    const result = await api(`/api/groups/${Number(group.id)}`, { name: name.trim() });
    button.disabled = false;
    if (!result?.ok) return alert(result?.error || "重命名失败");
    await refresh();
    await loadMembers();
  });
}

function bindVehicleCatalog() {
  document.getElementById("member-project-create-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const result = await api("/api/projects", { name: new FormData(form).get("name") });
    if (!result?.ok) {
      form.querySelector(".form-message").textContent = result?.error || "添加失败";
      return;
    }
    form.reset();
    await refresh();
    await loadMembers();
  });
  document.getElementById("member-project-catalog")?.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-rename-member-project]");
    if (!button) return;
    const project = projectById(button.dataset.renameMemberProject);
    if (!project) return;
    const name = prompt("重命名造车组别（成员与任务关联不变）", project.name);
    if (name === null || !name.trim() || name.trim() === project.name) return;
    const result = await api(`/api/projects/${Number(project.id)}`, { name: name.trim() });
    if (!result?.ok) return alert(result?.error || "重命名失败");
    await refresh();
    await loadMembers();
  });
}

function bindMemberTable() {
  const table = document.getElementById("member-table");
  table?.addEventListener("change", async (event) => {
    if (!event.target.matches(".role-select")) return;
    const result = await api(`/api/member/${event.target.dataset.roleId}/role`, { role: event.target.value });
    if (!result?.ok) alert(result?.error || "角色修改失败");
    loadMembers();
  });
  table?.addEventListener("click", async (event) => {
    const group = event.target.closest("[data-member-groups]");
    const project = event.target.closest("[data-member-projects]");
    const reset = event.target.closest("[data-reset]");
    const toggle = event.target.closest("[data-toggle]");
    if (group) {
      const member = allMembers.find((item) => Number(item.id) === Number(group.dataset.memberGroups));
      if (member) editMemberGroups(member);
    }
    if (project) {
      const member = allMembers.find((item) => Number(item.id) === Number(project.dataset.memberProjects));
      if (member) editMemberProjects(member);
    }
    if (reset) {
      const password = prompt("输入新密码（至少 8 位；留空则随机生成）", "");
      if (password === null) return;
      const result = await api(`/api/member/${reset.dataset.reset}/reset`, { password });
      if (result?.ok) alert(result.initial_password ? `重置成功，请记录新密码：\n${result.initial_password}` : "密码重置成功");
      else alert(result?.error || "重置失败");
    }
    if (toggle) {
      const member = allMembers.find((item) => Number(item.id) === Number(toggle.dataset.toggle));
      if (!member || !confirm(`${member.is_active ? "停用" : "启用"}成员：${member.name}？`)) return;
      const result = await api(`/api/member/${member.id}/toggle`, {});
      if (result?.ok) loadMembers();
      else alert(result?.error || "操作失败");
    }
  });
}

addDataListener(() => {
  renderMemberChoices();
  renderMemberTable();
  renderGroupCatalog();
  renderVehicleCatalog();
});
document.addEventListener("DOMContentLoaded", () => {
  bindMemberForms();
  bindMemberTable();
  bindGroupCatalog();
  bindVehicleCatalog();
  loadMembers();
});
