"use strict";

document.addEventListener("DOMContentLoaded", () => {
  let members = [];
  let permissionNames = {};
  let nextBefore = null;
  const memberHost = document.getElementById("permission-members");
  const result = document.getElementById("permission-result");
  const escape = (value) => BusinessUI.escape(value);
  async function api(path, data) {
    const response = await BusinessUI.fetch(path, data === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data),
    });
    const payload = await response.json();
    if (!response.ok || !payload.ok) throw new Error(payload.error || "请求失败");
    return payload;
  }
  function renderMembers() {
    const query = document.getElementById("permission-search").value.trim().toLowerCase();
    memberHost.innerHTML = members.filter((m) => `${m.id} ${m.name} ${m.username}`.toLowerCase().includes(query)).map((m) => {
      const inactive = !m.is_active || m.deleted_at;
      const checkbox = (permission) => {
        if (!Object.hasOwn(permissionNames, permission)) return "服务器暂不支持";
        const checked = (m.explicit_permissions || m.permissions).includes(permission) || m.permissions.includes(permission);
        const disabled = m.role === "admin" || !checked && (inactive || (permission === "funds.review" && m.role === "member"));
        return `<input type="checkbox" data-permission="${permission}" aria-label="${escape(m.name)} ${escape(permissionNames[permission])}" ${checked ? "checked" : ""} ${disabled ? "disabled" : ""}>`;
      };
      return `<tr data-member-id="${Number(m.id)}"><td>${escape(m.name)}<br><small>ID ${Number(m.id)} · ${escape(m.username)}</small></td><td>${escape({admin: "超级管理员", manager: "项管", member: "成员"}[m.role])} / ${inactive ? "停用或删除" : "启用"}</td><td>${checkbox("funds.review")}</td><td>${checkbox("invoices.manage")}</td><td>${checkbox("assets.manage")}${["admin","manager"].includes(m.role)?"<small>该角色默认拥有</small>":""}</td><td>${m.role === "admin" ? "<span>角色自带、不可单独撤销</span>" : '<button type="button" class="btn sm" data-save-permissions>保存权限</button>'}</td></tr>`;
    }).join("") || '<tr><td colspan="6">没有匹配成员</td></tr>';
  }
  async function loadMembers() {
    const data = await api("/api/admin/permissions");
    members = data.members;
    permissionNames = data.permissions;
    renderMembers();
  }
  document.getElementById("permission-search").addEventListener("input", renderMembers);
  memberHost.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-save-permissions]");
    if (!button || button.disabled) return;
    const row = button.closest("tr");
    button.disabled = true;
    result.textContent = "正在保存…";
    try {
      const permissions = [...row.querySelectorAll("[data-permission]:checked")].map((el) => el.dataset.permission);
      const displayed = new Set([...row.querySelectorAll("[data-permission]")].map(el=>el.dataset.permission));
      permissions.push(...(members.find(m=>String(m.id)===row.dataset.memberId)?.permissions||[]).filter(p=>!displayed.has(p)));
      await api(`/api/admin/permissions/${row.dataset.memberId}`, { permissions });
      result.textContent = "权限已保存，后续请求立即生效";
      await loadMembers();
      await loadAudit(true);
    } catch (error) {
      result.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  });
  async function loadAudit(reset) {
    if (auditLoading) return;
    auditLoading=true;
    const status = document.getElementById("audit-result");
    const more = document.getElementById("audit-more");
    more.disabled = true;
    try {
      const level = document.getElementById("audit-level")?.value || "critical";
      const data = await api(`/api/admin/audit?level=${encodeURIComponent(level)}${!reset && nextBefore ? `&before=${nextBefore}` : ""}`);
      const rows = data.events.map((event) => `<tr><td>${escape(event.created_at)}</td><td>${escape(event.actor_name || "未登录")} ${event.actor_id ? `#${Number(event.actor_id)}` : ""}</td><td>${escape(event.action)}<br><small>${escape(event.method)} ${escape(event.target)}</small></td><td>${escape({success: "成功", failure: "失败", partial: "部分成功", pending: "未完成"}[event.outcome] || event.outcome)} ${event.http_status || ""}</td><td><details><summary>查看摘要</summary><pre class="audit-details">${escape(event.details)}</pre></details></td></tr>`).join("");
      const host = document.getElementById("audit-events");
      if (reset) host.innerHTML = rows || '<tr><td colspan="5">暂无操作记录</td></tr>';
      else host.insertAdjacentHTML("beforeend", rows);
      nextBefore = data.next_before;
      more.hidden = !nextBefore;
      status.textContent = "";
    } catch (error) {
      status.textContent = error.message;
    } finally {
      more.disabled = false;
      auditLoading=false;
    }
  }
  let auditLoading=false;
  document.getElementById("audit-refresh").addEventListener("click", () => loadAudit(true));
  document.getElementById("audit-level")?.addEventListener("change", () => loadAudit(true));
  document.getElementById("audit-more").addEventListener("click", () => loadAudit(false));
  loadMembers().catch((error) => { result.textContent = error.message; });
  loadAudit(true);
});
