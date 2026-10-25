"use strict";

let allMembers = [];
const memberFilters = { q: "", group: "", project: "", role: "", showDeleted: false };

function roleSelect(member) {
  const disabled = Number(member.id) === Number(state.me?.id) ? "disabled" : "";
  return `<select aria-label="${esc(member.name)}的系统角色" class="role-select" data-role-id="${Number(member.id)}" ${disabled}><option value="member" ${member.role === "member" ? "selected" : ""}>成员</option><option value="manager" ${member.role === "manager" ? "selected" : ""}>项管</option><option value="admin" ${member.role === "admin" ? "selected" : ""}>超级管理员</option></select>`;
}

function filteredMembers() {
  return allMembers.filter((member) => {
    if (memberFilters.group && !(member.group_ids || []).map(Number).includes(Number(memberFilters.group))) return false;
    if (memberFilters.project && !(member.project_ids || []).map(Number).includes(Number(memberFilters.project))) return false;
    if (memberFilters.role && member.role !== memberFilters.role) return false;
    if (member.is_deleted && !memberFilters.showDeleted) return false;
    const haystack = `${member.name} ${member.username || ""}`.toLowerCase();
    return !memberFilters.q || haystack.includes(memberFilters.q.toLowerCase());
  });
}

function renderMemberChoices() {
  const host = document.getElementById("member-create-groups");
  refreshDraftChoices(host, "group_ids", state.groups);
  const projects = document.getElementById("member-create-projects");
  refreshDraftChoices(projects, "project_ids", state.projects);
  const imports = document.getElementById("member-import-projects");
  refreshDraftChoices(imports, "project_ids", state.projects);
  memberFilters.group = activeCatalogFilter(state.groups, memberFilters.group);
  memberFilters.project = activeCatalogFilter(state.projects, memberFilters.project);
  const filter = document.getElementById("m-filter-group");
  if (filter) filter.innerHTML = options(state.groups, memberFilters.group, "全部专业组");
  const projectFilter = document.getElementById("m-filter-project");
  if (projectFilter) projectFilter.innerHTML = options(state.projects, memberFilters.project, "全部造车组别");
}

function renderGroupCatalog() {
  const host = document.getElementById("member-group-catalog");
  if (!host) return;
  const admin = state.me?.role === "admin";
  host.innerHTML = state.groups.map((group) => {
    const count = allMembers.filter((member) => (member.group_ids || []).map(Number).includes(Number(group.id))).length;
    return `<li><span>${esc(group.name)}${group.is_active === 0 ? "（已删除）" : ""}</span><small>${count} 人</small>${group.is_active === 0
      ? admin ? `<button class="btn ghost sm" type="button" data-restore-member-group="${Number(group.id)}">恢复</button>` : ""
      : `<button class="btn ghost sm" type="button" data-rename-member-group="${Number(group.id)}">重命名</button>${admin ? `<button class="btn ghost sm" type="button" data-delete-member-group="${Number(group.id)}">删除</button>` : ""}`}</li>`;
  }).join("") || '<li class="muted">暂无专业组</li>';
}

function memberVehicleGroups(member) {
  return (member.project_ids || []).map((id) => projectById(id)?.name).filter(Boolean);
}

function renderVehicleCatalog() {
  const host = document.getElementById("member-project-catalog");
  if (!host) return;
  const admin = state.me?.role === "admin";
  host.innerHTML = state.projects.map((project) => {
    const count = allMembers.filter((member) => (member.project_ids || []).map(Number).includes(Number(project.id))).length;
    return `<li><span>${esc(project.name)}${project.is_active === 0 ? "（已删除）" : ""}</span><small>${count} 人</small>${project.is_active === 0
      ? admin ? `<button class="btn ghost sm" type="button" data-restore-member-project="${Number(project.id)}">恢复</button>` : ""
      : `<button class="btn ghost sm" type="button" data-rename-member-project="${Number(project.id)}">重命名</button>${admin ? `<button class="btn ghost sm" type="button" data-delete-member-project="${Number(project.id)}">删除</button>` : ""}`}</li>`;
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
          `<tr><td>${Number(member.id)}</td><td>${esc(member.name)}</td><td>${esc(member.username)}</td><td>${esc(memberGroups(member).join("、") || "未分组")}</td><td>${esc(memberVehicleGroups(member).join("、") || "未分组")}</td><td>${member.is_deleted ? esc(roleName(member.role)) : (admin ? roleSelect(member) : esc(roleName(member.role)))}</td><td>${member.is_deleted ? "已删除" : (member.is_active ? "在职" : "停用")}${member.must_change_password ? " / 待改初始密码" : ""}</td><td class="nowrap">${member.is_deleted
            ? admin ? `<button class="btn ghost sm" type="button" data-member-restore="${Number(member.id)}">恢复账号</button>` : ""
            : admin
              ? `<button class="btn ghost sm" type="button" data-member-profile="${Number(member.id)}">编辑资料</button>${Number(member.id) !== Number(state.me?.id) ? `<button class="btn ghost sm" type="button" data-member-delete="${Number(member.id)}">删除</button>` : ""}`
              : `<button class="btn ghost sm" type="button" data-member-groups="${Number(member.id)}">专业组</button><button class="btn ghost sm" type="button" data-member-projects="${Number(member.id)}">造车组别</button><button class="btn ghost sm" type="button" data-reset="${Number(member.id)}">重置密码</button><button class="btn ghost sm" type="button" data-toggle="${Number(member.id)}">${member.is_active ? "停用" : "启用"}</button>`}</td></tr>`,
      )
      .join("") || '<tr><td colspan="8" class="empty">没有符合条件的成员</td></tr>';
}

async function loadMembers() {
  const data = await api(state.me?.role === "admin" ? "/api/members?include_deleted=1" : "/api/members");
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

function editMemberProfile(member) {
  const isSelf = Number(member.id) === Number(state.me?.id);
  const roleOptions = [
    ["member", "成员"], ["manager", "项管"], ["admin", "超级管理员"],
  ].map(([value, label]) => `<option value="${value}" ${member.role === value ? "selected" : ""}>${label}</option>`).join("");
  showModal(
    `<div class="modal-head"><h2>编辑成员：${esc(member.name)}</h2><button class="btn ghost sm" type="button" data-close>关闭</button></div>
    <form id="member-profile-form" class="form-grid">
      <label>成员 ID（不可更改）<input value="${Number(member.id)}" readonly disabled></label>
      <label>姓名（超级管理员维护）<input name="name" maxlength="64" value="${esc(member.name)}" maxlength="80" required></label>
      <label>登录用户名<input name="username" maxlength="64" value="${esc(member.username)}" maxlength="64" required autocomplete="off"></label>
      ${isSelf
        ? '<p class="ux-note span-2">不能调整自己的系统角色或停用自己的账号；修改本人密码请到「账户设置」。</p>'
        : `<label>系统角色<select name="role">${roleOptions}</select></label>
           <label>账号状态<select name="is_active"><option value="1" ${member.is_active ? "selected" : ""}>启用</option><option value="0" ${!member.is_active ? "selected" : ""}>停用</option></select></label>
           <label class="span-2">重设密码（留空保持不变）<input name="new_password" type="password" minlength="8" autocomplete="new-password" placeholder="至少 8 位"></label>`}
      <fieldset class="span-2"><legend>专业组（可多选）</legend>${catalogChoices("group_ids", state.groups, member.group_ids || [])}</fieldset>
      <fieldset class="span-2"><legend>造车组别（可多选）</legend>${catalogChoices("project_ids", state.projects, member.project_ids || [])}</fieldset>
      <p class="form-message span-2" role="alert"></p>
      <div class="modal-actions span-2"><button class="btn primary" type="submit">保存全部修改</button></div>
    </form>`,
    `编辑 ${member.name} 的资料`,
  );
  document.querySelector("#modal [data-close]")?.addEventListener("click", hideModal);
  document.getElementById("member-profile-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    const payload = {
      name: values.get("name"),
      username: values.get("username"),
      group_ids: checkedValues(form, "group_ids"),
      project_ids: checkedValues(form, "project_ids"),
    };
    if (!isSelf) {
      payload.role = values.get("role");
      payload.is_active = values.get("is_active") === "1";
      if (values.get("new_password")) payload.new_password = values.get("new_password");
    }
    const button = form.querySelector('[type="submit"]');
    button.disabled = true;
    const result = await api(`/api/member/${Number(member.id)}/profile`, payload);
    button.disabled = false;
    if (result?.ok) {
      hideModal();
      await refresh();
      await loadMembers();
    } else form.querySelector(".form-message").textContent = result?.error || "保存失败";
  });
}

function editMemberProjects(member) {
  showModal(
    `<div class="modal-head"><h2>编辑 ${esc(member.name)} 的造车组别</h2><button class="btn ghost sm" type="button" data-close>关闭</button></div><form id="member-projects-form"><fieldset><legend>参加的造车组别（可多选）</legend>${catalogChoices("project_ids", state.projects, member.project_ids || [])}</fieldset><p class="form-message" role="alert"></p><div class="modal-actions"><button class="btn primary">保存组别</button></div></form>`,
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
    `<div class="modal-head"><h2>编辑 ${esc(member.name)} 的专业组</h2><button class="btn ghost sm" type="button" data-close>关闭</button></div><form id="member-groups-form"><fieldset><legend>专业组（可多选）</legend>${catalogChoices("group_ids", state.groups, member.group_ids || [])}</fieldset><p class="form-message" role="alert"></p><div class="modal-actions"><button class="btn primary">保存组别</button></div></form>`,
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
  document.getElementById("m-show-deleted")?.addEventListener("change", (event) => {
    memberFilters.showDeleted = event.target.checked;
    renderMemberTable();
  });
  document.getElementById("member-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const payload = Object.fromEntries(new FormData(form));
    if (!form.reportValidity()) return;
    payload.group_ids = checkedValues(form, "group_ids");
    payload.project_ids = checkedValues(form, "project_ids");
    const result = await api("/api/member", payload);
    if (result?.ok) {
      form.reset();
      if (result.initial_password) alert(`添加成功，请通过可信渠道私下交付初始密码；首次登录必须改密：\n${result.initial_password}`);
      loadMembers();
    } else form.querySelector(".form-message").textContent = result?.error || "添加失败";
  });
  document.getElementById("import-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    if (!form.reportValidity()) return;
    const result = await api("/api/members/import", { text: new FormData(form).get("text"), project_ids: checkedValues(form, "project_ids") });
    if (result?.ok || result?.failures) {
      let box = document.getElementById("member-import-result");
      if (!box) {box=document.createElement("section");box.id="member-import-result";box.setAttribute("aria-live","polite");form.after(box);}
      box.innerHTML=`<h3>${result.ok ? `整批导入成功：${result.created.length} 人` : `整批拒绝，未创建账号：${(result.failures||[]).length} 行需修正`}</h3><p>请通过可信渠道逐人交付凭据，首次登录必须改密。</p><pre>${esc(result.created.join("\n"))}</pre>`+
        ((result.failures||[]).length?`<table><thead><tr><th>行号</th><th>失败原因</th></tr></thead><tbody>${result.failures.map(item=>`<tr><td>${Number(item.line)}</td><td>${esc(item.reason)}</td></tr>`).join("")}</tbody></table>`:"");
      if (!(result.failures||[]).length) form.reset();
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
    const erase = event.target.closest("[data-delete-member-group]");
    const restore = event.target.closest("[data-restore-member-group]");
    if (erase || restore) {
      const group = groupById((erase || restore).dataset[erase ? "deleteMemberGroup" : "restoreMemberGroup"]);
      if (!group) return;
      if (erase) {
        const entered = prompt(`删除专业组“${group.name}”后无法再新分配，历史记录保留。请输入组别名称确认：`, "");
        if (entered !== group.name) return;
      } else if (!confirm(`恢复专业组：${group.name}？`)) return;
      const result = await api(`/api/groups/${Number(group.id)}/${erase ? "delete" : "restore"}`,
        erase ? { confirm_name: group.name } : {});
      if (!result?.ok) return alert(result?.error || "操作失败");
      await refresh();
      await loadMembers();
      return;
    }
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
    const erase = event.target.closest("[data-delete-member-project]");
    const restore = event.target.closest("[data-restore-member-project]");
    if (erase || restore) {
      const project = projectById((erase || restore).dataset[erase ? "deleteMemberProject" : "restoreMemberProject"]);
      if (!project) return;
      if (erase) {
        const entered = prompt(`删除造车组别“${project.name}”后无法再新分配，历史任务关联保留。请输入组别名称确认：`, "");
        if (entered !== project.name) return;
      } else if (!confirm(`恢复造车组别：${project.name}？`)) return;
      const result = await api(`/api/projects/${Number(project.id)}/${erase ? "delete" : "restore"}`,
        erase ? { confirm_name: project.name } : {});
      if (!result?.ok) return alert(result?.error || "操作失败");
      await refresh();
      await loadMembers();
      return;
    }
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
    if (!result?.ok) {
      const original = allMembers.find(item=>Number(item.id)===Number(event.target.dataset.roleId));
      if(original)event.target.value=original.role;
      alert(result?.error || "角色修改失败");
    }
    loadMembers();
  });
  table?.addEventListener("click", async (event) => {
    const erase = event.target.closest("[data-member-delete]");
    const restore = event.target.closest("[data-member-restore]");
    if (erase || restore) {
      const member = allMembers.find((item) => Number(item.id) === Number((erase || restore).dataset[erase ? "memberDelete" : "memberRestore"]));
      if (!member) return;
      if (erase) {
        const entered = prompt(`删除成员“${member.name}”后无法登录，历史任务与发票仍保留。请输入登录用户名 ${member.username} 确认：`, "");
        if (entered !== member.username) return;
      } else if (!confirm(`恢复成员“${member.name}”？恢复后仍需手动启用账号。`)) return;
      const result = await api(`/api/member/${Number(member.id)}/${erase ? "delete" : "restore"}`,
        erase ? { confirm_username: member.username } : {});
      if (!result?.ok) return alert(result?.error || "操作失败");
      await refresh();
      await loadMembers();
      return;
    }
    const profile = event.target.closest("[data-member-profile]");
    const group = event.target.closest("[data-member-groups]");
    const project = event.target.closest("[data-member-projects]");
    const reset = event.target.closest("[data-reset]");
    const toggle = event.target.closest("[data-toggle]");
    if (profile) {
      const member = allMembers.find((item) => Number(item.id) === Number(profile.dataset.memberProfile));
      if (member) editMemberProfile(member);
    }
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
      if (result?.ok) alert(result.initial_password ? `重置成功，请私下交付新密码；下次登录必须改密：\n${result.initial_password}` : "密码重置成功");
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
