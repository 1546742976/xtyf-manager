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
  const retentionLevels = ["critical", "important", "normal", "routine"];
  const retentionNames = {critical: "严重", important: "重要", normal: "一般", routine: "低"};
  const retentionSources = {admin: "管理员设置", file: "配置文件", environment: "环境变量", default: "系统默认"};
  const retentionForm = document.getElementById("audit-retention-form");
  const retentionResult = document.getElementById("audit-retention-result");
  function showRetention(policy) {
    for (const level of retentionLevels) retentionForm.elements[level].value = policy.days[level];
    document.getElementById("audit-retention-source").textContent =
      retentionLevels.map((level) => `${retentionNames[level]}：${retentionSources[policy.sources[level]] || "未知来源"}`).join(" · ");
  }
  async function loadRetention() {
    showRetention(await api("/api/admin/audit-retention"));
  }
  async function changeRetention(payload) {
    const save = document.getElementById("audit-retention-save");
    const reset = document.getElementById("audit-retention-reset");
    save.disabled = true;
    reset.disabled = true;
    retentionResult.textContent = "正在保存…";
    try {
      showRetention(await api("/api/admin/audit-retention", payload));
      retentionResult.textContent = "保留策略已保存；不会自动删除记录，人工清理前请先备份并预览。";
      await loadAudit(true);
    } catch (error) {
      retentionResult.textContent = error.message;
    } finally {
      save.disabled = false;
      reset.disabled = false;
    }
  }
  retentionForm.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!retentionForm.reportValidity()) return;
    const days = {};
    for (const level of retentionLevels) days[level] = Number(retentionForm.elements[level].value);
    changeRetention({days});
  });
  document.getElementById("audit-retention-reset").addEventListener("click", () => {
    changeRetention({reset: true});
  });
  loadRetention().catch((error) => { retentionResult.textContent = error.message; });

  const categories = {
    finance: "资金与发票", security: "登录与权限", assets: "资产与物资",
    members: "成员与账号", tasks: "任务与项目", collaboration: "协作与通知",
    settings: "系统设置", other: "未分类",
  };
  const priorities = {
    permanent: "永久", critical: "严重", important: "重要",
    normal: "一般", routine: "低",
  };
  const actionNames = {
    login: "登录", logout: "退出登录", api_password: "修改密码",
    api_member_reset: "重置成员密码", api_member_role: "调整成员角色",
    api_member_toggle: "调整成员状态", export_members_csv: "导出成员",
    "funds.create_record": "新增资金记录", "funds.update_record": "修改资金记录",
    "funds.delete_record": "删除资金记录", "funds.review_record": "审核资金记录",
    "funds.confirm_personal_reimbursement": "登记或更正个人报销",
    "funds.export_csv": "导出资金台账",
    "security.permissions_set": "更改业务授权",
  };
  function displayAction(event) {
    const name = actionNames[event.action];
    if (name) return name;
    const operation = String(event.action || "").split(".").pop();
    const verbs = {create:"创建",update:"修改",delete:"删除",restore:"恢复",
      export:"导出",upload:"上传",review:"审核",import:"导入",start:"启动",
      confirm:"确认",save:"保存",reset:"重置",operate:"执行操作"};
    const label = Object.entries(verbs).find(([key]) => operation.includes(key))?.[1];
    return label ? `${categories[event.category] || "业务"} · ${label}` : (categories[event.category] || "业务") + "操作";
  }
  let auditLoading = false;
  let auditRevision = 0;
  let auditQueuedRefresh = false;
  async function loadAudit(reset) {
    const status = document.getElementById("audit-result");
    const more = document.getElementById("audit-more");
    const host = document.getElementById("audit-events");
    if (reset) {
      // Invalidate in-flight responses and their paging cursors immediately.
      auditRevision += 1;
      nextBefore = null;
      more.hidden = true;
      host.innerHTML = '<tr><td colspan="6">正在加载…</td></tr>';
      status.textContent = "";
      if (auditLoading) {
        // Coalesce rapid filter changes, but always fetch the latest selection.
        auditQueuedRefresh = true;
        return;
      }
    } else if (auditLoading || !nextBefore) {
      return;
    }
    auditLoading = true;
    const revision = auditRevision;
    more.disabled = true;
    try {
      const params = new URLSearchParams({
        level: document.getElementById("audit-level")?.value || "priority",
        category: document.getElementById("audit-category")?.value || "all",
        outcome: document.getElementById("audit-outcome")?.value || "all",
      });
      if (!reset && nextBefore) params.set("before", String(nextBefore));
      const data = await api(`/api/admin/audit?${params}`);
      // Ignore responses for filters that the user has already changed.
      if (revision !== auditRevision) return;
      const rows = data.events.map((event) => `<tr>
        <td>${escape(event.created_at)}</td>
        <td>${escape(event.actor_name || "未登录")} ${event.actor_id ? `#${Number(event.actor_id)}` : ""}</td>
        <td>${escape(categories[event.category] || "未分类")}<br><small>${escape(priorities[event.level] || "严重")}</small></td>
        <td>${escape(displayAction(event))}<br><small>${escape(event.action)} · ${escape(event.method)} ${escape(event.target)}</small></td>
        <td>${escape({success: "成功", failure: "失败", partial: "部分成功", pending: "未完成"}[event.outcome] || event.outcome)} ${event.http_status || ""}</td>
        <td><details><summary>查看摘要</summary><pre class="audit-details">${escape(event.details)}</pre></details></td>
      </tr>`).join("");
      if (reset) host.innerHTML = rows || '<tr><td colspan="6">暂无操作记录</td></tr>';
      else host.insertAdjacentHTML("beforeend", rows);
      nextBefore = data.next_before;
      more.hidden = !nextBefore;
      status.textContent = "";
    } catch (error) {
      if (revision === auditRevision) status.textContent = error.message;
    } finally {
      auditLoading = false;
      if (auditQueuedRefresh) {
        auditQueuedRefresh = false;
        // A new request starts with freshly selected filters and no old cursor.
        void loadAudit(true);
      } else {
        more.disabled = false;
      }
    }
  }
  document.getElementById("audit-refresh").addEventListener("click", () => loadAudit(true));
  for (const id of ["audit-level", "audit-category", "audit-outcome"]) {
    document.getElementById(id)?.addEventListener("change", () => loadAudit(true));
  }
  document.getElementById("audit-more").addEventListener("click", () => loadAudit(false));
  loadMembers().catch((error) => { result.textContent = error.message; });
  loadAudit(true);
});
