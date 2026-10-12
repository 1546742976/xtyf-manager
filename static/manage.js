
let allMembers = [];

function roleName(role) {
  return { admin: '管理员', manager: '项管', member: '成员' }[role] || role;
}

function renderCreateOwner() {
  const select = document.getElementById('create-owner');
  if (!select) return;
  select.innerHTML = '<option value="">未分配</option>' + state.members.map((m) =>
    `<option value="${m.id}">${esc(m.name)}${m.group_name ? '（' + esc(m.group_name) + '）' : ''}</option>`
  ).join('');
}

const taskFilters = { q: '', group: '', status: '' };
const PRIORITY_LEVEL = { '高': 'high', '中': 'mid', '低': 'low' };

function priorityCell(priority) {
  const level = PRIORITY_LEVEL[priority] || 'mid';
  return `<span class="prio prio-${level}"><i class="dot"></i>${esc(priority)}</span>`;
}

function filteredTasksForTable() {
  const { q, group, status } = taskFilters;
  return state.tasks.filter((t) => {
    if (group && t.group_name !== group) return false;
    if (status && t.status !== status) return false;
    if (q) {
      const owner = memberById(t.owner_id);
      const hay = [t.title, t.project, t.detail, owner ? owner.name : ''].join(' ').toLowerCase();
      if (!hay.includes(q.toLowerCase())) return false;
    }
    return true;
  });
}

function buildTaskFilterOptions() {
  const groupSelect = document.getElementById('t-filter-group');
  if (!groupSelect) return;
  const groups = [...new Set(state.tasks.map((t) => t.group_name).filter(Boolean))].sort();
  groupSelect.innerHTML = '<option value="">全部组别</option>' + groups.map((g) => `<option>${esc(g)}</option>`).join('');
  if (groups.includes(taskFilters.group)) groupSelect.value = taskFilters.group;

  const statusSelect = document.getElementById('t-filter-status');
  const statuses = [...new Set(state.tasks.map((t) => t.status))];
  statusSelect.innerHTML = '<option value="">全部状态</option>' + statuses.map((s) => `<option>${esc(s)}</option>`).join('');
  if (statuses.includes(taskFilters.status)) statusSelect.value = taskFilters.status;
}

function renderTaskTable() {
  const tasks = filteredTasksForTable();
  const tbody = document.querySelector('#task-table tbody');
  if (!tbody) return;
  document.getElementById('task-count').textContent = `共 ${tasks.length} / ${state.tasks.length} 条`;
  tbody.innerHTML = tasks.map((t) => {
    const owner = memberById(t.owner_id);
    return `<tr>
      <td>${t.id}</td>
      <td class="cell-title">${esc(t.title)}</td>
      <td>${esc(owner ? owner.name : '未分配')}</td>
      <td>${esc(t.group_name)}</td>
      <td>${esc(t.project)}</td>
      <td>${priorityCell(t.priority)}</td>
      <td>${esc(t.status)}</td>
      <td>${badge(daysLeftText(t), daysLeftClass(t))}</td>
      <td>${esc(t.due_date)}</td>
      <td class="nowrap">
        <button class="btn ghost sm" type="button" data-edit="${t.id}">编辑</button>
        <button class="btn ghost sm danger-text" type="button" data-del="${t.id}">删除</button>
      </td>
    </tr>`;
  }).join('');
}

function roleSelect(member) {
  const disabled = state.me && member.id === state.me.id ? ' disabled' : '';
  return `<select class="role-select" data-role-id="${member.id}"${disabled}>
    <option value="member" ${member.role === 'member' ? 'selected' : ''}>成员</option>
    <option value="manager" ${member.role === 'manager' ? 'selected' : ''}>项管</option>
    <option value="admin" ${member.role === 'admin' ? 'selected' : ''}>管理员</option>
  </select>`;
}

const memberFilters = { q: '', group: '', role: '' };

function filteredMembers() {
  const { q, group, role } = memberFilters;
  return allMembers.filter((m) => {
    if (group && m.group_name !== group) return false;
    if (role && m.role !== role) return false;
    if (q && ![m.name, m.username || ''].join(' ').toLowerCase().includes(q.toLowerCase())) return false;
    return true;
  });
}

function buildMemberFilterOptions() {
  const select = document.getElementById('m-filter-group');
  if (!select) return;
  const groups = [...new Set(allMembers.map((m) => m.group_name).filter(Boolean))].sort();
  select.innerHTML = '<option value="">全部组别</option>' + groups.map((g) => `<option>${esc(g)}</option>`).join('');
  if (groups.includes(memberFilters.group)) select.value = memberFilters.group;
}

function renderMemberTable() {
  const canEditRole = !!state.me && state.me.role === 'admin';
  const members = filteredMembers();
  document.getElementById('member-count').textContent = `共 ${members.length} / ${allMembers.length} 人`;
  const tbody = document.querySelector('#member-table tbody');
  if (!tbody) return;
  tbody.innerHTML = members.map((m) => `<tr>
    <td>${m.id}</td>
    <td>${esc(m.name)}</td>
    <td>${esc(m.username)}</td>
    <td>${esc(m.group_name)}</td>
    <td>${canEditRole ? roleSelect(m) : roleName(m.role)}</td>
    <td>${m.is_active ? '在职' : '停用'}</td>
    <td class="nowrap">
      <button class="btn ghost sm" type="button" data-reset="${m.id}">重置密码</button>
      <button class="btn ghost sm" type="button" data-toggle="${m.id}">${m.is_active ? '停用' : '启用'}</button>
    </td>
  </tr>`).join('');
}

async function loadManage() {
  await refresh();
  const data = await api('/api/members');
  if (data && data.ok) {
    allMembers = data.members;
    buildMemberFilterOptions();
    renderMemberTable();
  }
}

window.onData = () => {
  renderTaskTable();
  buildTaskFilterOptions();
  renderCreateOwner();
};

document.getElementById('t-filter-q')?.addEventListener('input', (event) => {
  taskFilters.q = event.target.value.trim();
  renderTaskTable();
});
document.getElementById('t-filter-group')?.addEventListener('change', (event) => {
  taskFilters.group = event.target.value;
  renderTaskTable();
});
document.getElementById('t-filter-status')?.addEventListener('change', (event) => {
  taskFilters.status = event.target.value;
  renderTaskTable();
});
document.getElementById('m-filter-q')?.addEventListener('input', (event) => {
  memberFilters.q = event.target.value.trim();
  renderMemberTable();
});
document.getElementById('m-filter-group')?.addEventListener('change', (event) => {
  memberFilters.group = event.target.value;
  renderMemberTable();
});
document.getElementById('m-filter-role')?.addEventListener('change', (event) => {
  memberFilters.role = event.target.value;
  renderMemberTable();
});

document.getElementById('create-form')?.addEventListener('submit', async (event) => {
  event.preventDefault();
  const payload = {};
  new FormData(event.target).forEach((value, key) => { payload[key] = value; });
  const res = await api('/api/task', payload);
  if (res && res.ok) {
    event.target.reset();
    loadManage();
  } else {
    alert((res && res.error) || '创建失败');
  }
});

document.getElementById('member-form')?.addEventListener('submit', async (event) => {
  event.preventDefault();
  const payload = {};
  new FormData(event.target).forEach((value, key) => { payload[key] = value; });
  const res = await api('/api/member', payload);
  if (res && res.ok) {
    event.target.reset();
    if (res.initial_password) alert('添加成功。请保存新成员的初始密码并通知本人：\n' + res.initial_password);
    loadManage();
  } else {
    alert((res && res.error) || '添加失败');
  }
});

document.getElementById('import-form')?.addEventListener('submit', async (event) => {
  event.preventDefault();
  const text = new FormData(event.target).get('text');
  const res = await api('/api/members/import', { text });
  if (res && res.ok) {
    alert(`导入完成，共 ${res.created.length} 人：\n` + res.created.join('\n'));
    event.target.reset();
    loadManage();
  } else {
    alert((res && res.error) || '导入失败');
  }
});

document.querySelector('#task-table')?.addEventListener('click', async (event) => {
  const editId = event.target.dataset.edit;
  const delId = event.target.dataset.del;
  if (editId) {
    openTaskModal(parseInt(editId, 10));
  }
  if (delId && confirm('确定删除该任务？此操作不可恢复')) {
    const res = await api(`/api/task/${delId}/delete`, {});
    if (res && res.ok) loadManage();
    else alert((res && res.error) || '删除失败');
  }
});

document.querySelector('#member-table')?.addEventListener('change', async (event) => {
  const target = event.target;
  if (!target.classList.contains('role-select')) return;
  const res = await api(`/api/member/${target.dataset.roleId}/role`, { role: target.value });
  if (res && res.ok) {
    loadManage();
  } else {
    alert((res && res.error) || '角色修改失败');
    loadManage();
  }
});

document.querySelector('#member-table')?.addEventListener('click', async (event) => {
  const resetId = event.target.dataset.reset;
  const toggleId = event.target.dataset.toggle;
  if (resetId) {
    const password = prompt('输入新密码（至少 8 位；留空则随机生成）', '');
    if (password === null) return;
    const res = await api(`/api/member/${resetId}/reset`, { password: password || '' });
    if (res && res.ok) {
      alert(res.initial_password ? '重置成功。请记录新密码：\n' + res.initial_password : '密码重置成功');
    } else {
      alert((res && res.error) || '重置失败');
    }
  }
  if (toggleId) {
    const member = allMembers.find((m) => String(m.id) === toggleId);
    if (member && !confirm((member.is_active ? '停用' : '启用') + '成员：' + member.name + '？')) return;
    const res = await api(`/api/member/${toggleId}/toggle`, {});
    if (res && res.ok) loadManage();
    else alert((res && res.error) || '操作失败');
  }
});

loadManage();
