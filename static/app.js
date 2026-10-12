// ===== 全局状态（登录人 / 成员 / 任务 / 筛选条件 / 数据指纹）=====
const state = {
  me: null,
  members: [],
  tasks: [],
  statuses: [],
  priorities: [],
  view: 'person',
  filters: { group: '', status: '', q: '', mine: false },
  inited: false,
  fingerprint: '',
};

const STATUS_CLASS = { '未开始': 'gray', '进行中': 'blue', '已完成': 'green', '阻塞': 'red', '已取消': 'dark' };
const PRIORITY_CLASS = { '高': 'red', '中': 'orange', '低': 'gray' };

// ===== 轻量 fetch 封装（401 未登录自动跳登录页）=====
async function api(url, data) {
  const options = { credentials: 'same-origin' };
  if (data !== undefined) {
    options.method = 'POST';
    options.headers = { 'Content-Type': 'application/json' };
    options.body = JSON.stringify(data);
  }
  try {
    const res = await fetch(url, options);
    if (res.status === 401) {
      location.href = '/login';
      return null;
    }
    return await res.json();
  } catch (_) {
    return { ok: false, error: '网络连接失败，请重试' };
  }
}

// ===== HTML 转义（所有动态内容渲染前必须经过）=====
function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

function todayStr() {
  const d = new Date();
  return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
}

// ===== 逾期判断与"剩余天数"文案/颜色（与后端 days_left 字段配套）=====
function isOverdue(task) {
  return Number.isInteger(task.days_left) && task.days_left < 0 && !['已完成', '已取消'].includes(task.status);
}

function daysLeftText(task) {
  if (task.status === '已完成') return '已完成';
  if (task.status === '已取消') return '已取消';
  if (task.days_left === null || task.days_left === undefined) return '未设截止';
  if (task.days_left < 0) return '已逾期 ' + (-task.days_left) + ' 天';
  if (task.days_left === 0) return '今天截止';
  return '剩余 ' + task.days_left + ' 天';
}

function daysLeftClass(task) {
  if (['已完成', '已取消'].includes(task.status)) return 'gray';
  if (task.days_left === null || task.days_left === undefined) return 'gray';
  if (task.days_left < 0) return 'red';
  if (task.days_left <= 3) return 'orange';
  return 'blue';
}

function memberById(id) {
  return state.members.find((m) => m.id === id) || null;
}

function isManager() {
  return !!state.me && ['admin', 'manager'].includes(state.me.role);
}

function canEdit(task) {
  return isManager() || (!!state.me && task.owner_id === state.me.id);
}

function badge(text, cls) {
  return `<span class="badge ${cls}">${esc(text)}</span>`;
}

// ===== 数据轮询：同步反馈、避免请求重叠 =====
let refreshing = false;
function showSync(ok, message) {
  const el = document.getElementById('sync-status');
  if (!el) return;
  el.textContent = message;
  el.classList.toggle('sync-error', !ok);
}
async function refresh() {
  if (refreshing) return;
  refreshing = true;
  try {
    const data = await api('/api/data');
    if (!data || !data.ok) {
      showSync(false, (data && data.error) || '同步失败');
      return;
    }
    showSync(true, '已同步 · ' + new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }));
    updateFilterOptions(data);
  state.me = data.me;
  state.members = data.members;
  state.tasks = data.tasks;
  state.statuses = data.statuses;
  state.priorities = data.priorities;
  if (!state.inited) {
    initFilters();
    state.inited = true;
  }
  const fingerprint = JSON.stringify([data.members, data.tasks, data.me]);
  if (fingerprint !== state.fingerprint) {
    state.fingerprint = fingerprint;
    render();
    if (window.onData) window.onData();
  }
  } finally {
    refreshing = false;
  }
}

function render() {
  if (document.getElementById('stats')) renderStats();
  if (document.getElementById('board')) renderBoard();
}

function updateFilterOptions(data) {
  const group = document.getElementById('f-group');
  if (group) {
    const groups = [...new Set([
      ...data.members.map((m) => m.group_name), ...data.tasks.map((t) => t.group_name),
    ].filter(Boolean))].sort();
    group.innerHTML = '<option value="">全部组别</option>' +
      groups.map((g) => `<option value="${esc(g)}">${esc(g)}</option>`).join('');
    if (!groups.includes(state.filters.group)) state.filters.group = '';
    group.value = state.filters.group;
  }
  const status = document.getElementById('f-status');
  if (status) {
    status.innerHTML = '<option value="">全部状态</option>' +
      data.statuses.map((v) => `<option value="${esc(v)}">${esc(v)}</option>`).join('');
    if (!data.statuses.includes(state.filters.status)) state.filters.status = '';
    status.value = state.filters.status;
  }
}
function initFilters() {
  const groupSel = document.getElementById('f-group');
  if (groupSel) groupSel.addEventListener('change', () => { state.filters.group = groupSel.value; renderBoard(); });
  const statusSel = document.getElementById('f-status');
  if (statusSel) statusSel.addEventListener('change', () => { state.filters.status = statusSel.value; renderBoard(); });
  const q = document.getElementById('f-q');
  if (q) q.addEventListener('input', () => { state.filters.q = q.value.trim(); renderBoard(); });
  const mine = document.getElementById('f-mine');
  if (mine) mine.addEventListener('change', () => { state.filters.mine = mine.checked; renderBoard(); });
  document.querySelectorAll('#view-seg button').forEach((btn) => {
    btn.addEventListener('click', () => {
      state.view = btn.dataset.view;
      document.querySelectorAll('#view-seg button').forEach((b) => b.classList.toggle('active', b === btn));
      renderBoard();
    });
  });
}

function filteredTasks() {
  const { group, status, q, mine } = state.filters;
  return state.tasks.filter((t) => {
    if (group && t.group_name !== group) return false;
    if (status && t.status !== status) return false;
    if (mine && (!state.me || t.owner_id !== state.me.id)) return false;
    if (q) {
      const owner = memberById(t.owner_id);
      const hay = [t.title, t.project, t.detail, owner ? owner.name : ''].join(' ');
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

// ===== 统计卡渲染 ===== 
function renderStats() {
  const active = state.tasks.filter((t) => t.status !== '已取消');
  const stats = [
    { label: '总任务', value: active.length, cls: '' },
    { label: '进行中', value: active.filter((t) => t.status === '进行中').length, cls: 'blue' },
    { label: '已完成', value: active.filter((t) => t.status === '已完成').length, cls: 'green' },
    { label: '阻塞', value: active.filter((t) => t.status === '阻塞').length, cls: 'red' },
    { label: '逾期', value: active.filter(isOverdue).length, cls: 'orange' },
  ];
  document.getElementById('stats').innerHTML = stats.map((s) =>
    `<div class="stat ${s.cls}"><div class="stat-value">${s.value}</div><div class="stat-label">${s.label}</div></div>`
  ).join('');
}

function groupTasksByPerson(tasks) {
  const groups = state.members.map((m) => ({
    key: `m-${m.id}`,
    title: m.name,
    sub: m.group_name,
    tasks: tasks.filter((t) => t.owner_id === m.id),
  }));
  const orphans = tasks.filter((t) => !memberById(t.owner_id));
  if (orphans.length) groups.push({ key: 'unassigned', title: '未分配', sub: '', tasks: orphans });
  return groups;
}

function groupTasksByStatus(tasks) {
  return state.statuses.map((s) => ({
    key: `s-${s}`,
    title: s,
    sub: '',
    tasks: tasks.filter((t) => t.status === s),
  }));
}

// ===== 任务卡片渲染（状态 / 优先级 / 剩余天数徽章 / 逾期红线）=====
function taskCard(task) {
  const owner = memberById(task.owner_id);
  const overdue = isOverdue(task);
  const meta = [];
  if (task.project) meta.push(esc(task.project));
  meta.push(esc(owner ? owner.name : '未分配'));
  if (task.due_date) meta.push(`截止 ${esc(task.due_date)}`);
  return `<article class="task ${overdue ? 'overdue' : ''}" data-id="${task.id}" role="button" tabindex="0" aria-label="查看任务 ${esc(task.title)}">
    <div class="task-head">
      <h3>${esc(task.title)}</h3>
      ${badge(task.status, STATUS_CLASS[task.status] || 'gray')}
    </div>
    <div class="task-meta">
      ${badge(task.priority + '优先', PRIORITY_CLASS[task.priority] || 'gray')}
      ${meta.map((m) => `<span>${m}</span>`).join('')}
      ${overdue ? '<span class="overdue-text">已逾期</span>' : ''}
    </div>
    <div class="task-foot"><span>${badge(daysLeftText(task), daysLeftClass(task))}</span><span>更新 ${esc(task.updated_at)}</span></div>
  </article>`;
}

// ===== 看板渲染：按人 / 按状态 两种视图 ===== 
function renderBoard() {
  const tasks = filteredTasks();
  const groups = state.view === 'person' ? groupTasksByPerson(tasks) : groupTasksByStatus(tasks);
  const hasFilter = !!(state.filters.group || state.filters.status || state.filters.q || state.filters.mine);
  const visible = state.view === 'person' && hasFilter
    ? groups.filter((g) => g.tasks.length)
    : groups;
  document.getElementById('board').innerHTML = visible.map((g) => `
    <section class="lane">
      <header class="lane-head">
        <h2>${esc(g.title)}</h2>
        ${g.sub ? `<span class="lane-sub">${esc(g.sub)}</span>` : ''}
        <span class="lane-count">${g.tasks.length}</span>
      </header>
      <div class="lane-body">${g.tasks.map(taskCard).join('') || '<p class="empty">暂无任务</p>'}</div>
    </section>`).join('') || '<p class="empty">暂无数据</p>';
  document.querySelectorAll('.task').forEach((el) => {
    el.addEventListener('click', () => openTaskModal(parseInt(el.dataset.id, 10)));
    el.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        openTaskModal(parseInt(el.dataset.id, 10));
      }
    });
  });
}

// ===== 弹窗工具 ===== 
function showModal(html) {
  document.getElementById('modal').innerHTML = html;
  document.getElementById('modal-mask').classList.remove('hidden');
  document.getElementById('modal').querySelector('button, input, select')?.focus();
}

function hideModal() {
  document.getElementById('modal-mask').classList.add('hidden');
  document.getElementById('modal').innerHTML = '';
}

function ownerOptions(selected) {
  return state.members.map((m) =>
    `<option value="${m.id}" ${m.id === selected ? 'selected' : ''}>${esc(m.name)}${m.group_name ? '（' + esc(m.group_name) + '）' : ''}</option>`
  ).join('');
}

// ===== 任务详情弹窗（本人可改状态；manager+ 全字段；含更新历史）=====
async function openTaskModal(id) {
  const task = state.tasks.find((t) => t.id === id);
  if (!task) return;
  const editable = canEdit(task);
  const manager = isManager();
  let form = '';
  if (editable) {
    if (manager) {
      form += `
        <label>标题<input name="title" value="${esc(task.title)}"></label>
        <div class="grid-2">
          <label>负责人<select name="owner_id"><option value="">未分配</option>${ownerOptions(task.owner_id)}</select></label>
          <label>组别<input name="group_name" value="${esc(task.group_name)}"></label>
        </div>
        <div class="grid-2">
          <label>项目<input name="project" value="${esc(task.project)}"></label>
          <label>优先级<select name="priority">${state.priorities.map((p) => `<option ${p === task.priority ? 'selected' : ''}>${p}</option>`).join('')}</select></label>
        </div>`;
    }
    form += `
      <div class="grid-2">
        <label>状态<select name="status">${state.statuses.map((s) => `<option ${s === task.status ? 'selected' : ''}>${s}</option>`).join('')}</select></label>
        <label>剩余天数（自动计算）<input value="${daysLeftText(task)}" readonly></label>
      </div>`;
    if (manager) {
      form += `
        <div class="grid-2">
          <label>开始日期<input name="start_date" type="date" value="${esc(task.start_date)}"></label>
          <label>截止日期<input name="due_date" type="date" value="${esc(task.due_date)}"></label>
        </div>
        <label>说明<textarea name="detail" rows="3">${esc(task.detail)}</textarea></label>`;
    }
    form += `
      <label>本次更新备注<input name="note" placeholder="可留空"></label>
      <div class="modal-actions">
        ${manager ? '<button type="button" class="btn danger" id="task-delete">删除任务</button>' : ''}
        <button type="submit" class="btn primary">保存更新</button>
      </div>`;
  }

  const owner = memberById(task.owner_id);
  const info = [
    task.project ? `项目：${esc(task.project)}` : '',
    `优先级：${esc(task.priority)}`,
    task.start_date ? `开始：${esc(task.start_date)}` : '',
    task.due_date ? `截止：${esc(task.due_date)}` : '',
  ].filter(Boolean).join(' · ');

  showModal(`
    <div class="modal-head">
      <h2>${esc(task.title)}</h2>
      <button type="button" class="btn ghost sm" id="modal-close">关闭</button>
    </div>
    <div class="modal-info">
      ${badge(task.status, STATUS_CLASS[task.status] || 'gray')}
      ${badge('负责人 ' + (owner ? owner.name : '未分配'), 'outline')}
      <span class="muted">${info}</span>
    </div>
    ${task.detail ? `<p class="task-detail">${esc(task.detail)}</p>` : ''}
    ${editable ? `<form id="task-form">${form}</form>` : '<p class="muted">你没有编辑权限，仅可查看。</p>'}
    <h3 class="log-title">更新历史</h3>
    <div id="task-logs" class="logs">加载中…</div>
  `);

  document.getElementById('modal-close').addEventListener('click', hideModal);

  const logsBox = document.getElementById('task-logs');
  const logsData = await api(`/api/task/${task.id}/logs`);
  if (logsData && logsData.ok) {
    logsBox.innerHTML = logsData.logs.map((l) => `
      <div class="log">
        <div class="log-head"><b>${esc(l.member_name || '系统')}</b><span>${esc(l.created_at)}</span></div>
        ${l.changes ? `<div class="log-changes">${esc(l.changes)}</div>` : ''}
        ${l.note ? `<div class="log-note">${esc(l.note)}</div>` : ''}
      </div>`).join('') || '<p class="empty">暂无记录</p>';
  }

  if (manager) {
    const delBtn = document.getElementById('task-delete');
    if (delBtn) {
      delBtn.addEventListener('click', async () => {
        if (!confirm('确定删除该任务？此操作不可恢复')) return;
        const res = await api(`/api/task/${task.id}/delete`, {});
        if (res && res.ok) {
          hideModal();
          refresh();
        } else {
          alert((res && res.error) || '删除失败');
        }
      });
    }
  }

  const formEl = document.getElementById('task-form');
  if (formEl) {
    formEl.addEventListener('submit', async (event) => {
      event.preventDefault();
      const payload = {};
      new FormData(formEl).forEach((value, key) => { payload[key] = value; });
      const res = await api(`/api/task/${task.id}`, payload);
      if (res && res.ok) {
        hideModal();
        refresh();
      } else {
        alert((res && res.error) || '保存失败');
      }
    });
  }
}

// ===== 修改密码弹窗 ===== 
function bindPasswordButton() {
  const btn = document.getElementById('btn-password');
  if (!btn) return;
  btn.addEventListener('click', () => {
    showModal(`
      <div class="modal-head">
        <h2>修改密码</h2>
        <button type="button" class="btn ghost sm" id="modal-close">关闭</button>
      </div>
      <form id="pw-form">
        <label>原密码<input name="old" type="password" required></label>
        <label>新密码（至少 6 位）<input name="new" type="password" required minlength="6"></label>
        <div class="modal-actions"><button type="submit" class="btn primary">保存</button></div>
      </form>
    `);
    document.getElementById('modal-close').addEventListener('click', hideModal);
    document.getElementById('pw-form').addEventListener('submit', async (event) => {
      event.preventDefault();
      const form = new FormData(event.target);
      const res = await api('/api/password', { old: form.get('old'), new: form.get('new') });
      if (res && res.ok) {
        hideModal();
        alert('密码已修改');
      } else {
        alert((res && res.error) || '修改失败');
      }
    });
  });
}

// ===== 资金待审角标（仅 chy 显示，30 秒轮询 /funds/api/pending_review）=====
async function refreshFundsBadge() {
  const badge = document.getElementById('fundsPendingBadge');
  if (!badge) return;
  const data = await api('/funds/api/pending_review');
  if (data && data.code === 0 && data.count > 0) {
    badge.textContent = data.count;
    badge.style.display = '';
  } else {
    badge.style.display = 'none';
  }
}

// ===== 页面初始化（遮罩关闭 / 改密按钮 / 角标轮询 / 看板轮询）=====
document.addEventListener('DOMContentLoaded', () => {
  const mask = document.getElementById('modal-mask');
  if (mask) {
    mask.addEventListener('click', (event) => {
      if (event.target.id === 'modal-mask') hideModal();
    });
  }
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && mask && !mask.classList.contains('hidden')) hideModal();
  });
  document.getElementById('refresh-now')?.addEventListener('click', refresh);
  bindPasswordButton();
  if (document.getElementById('fundsPendingBadge')) {
    refreshFundsBadge();
    setInterval(refreshFundsBadge, 30000);
  }
  if (document.getElementById('board')) {
    refresh();
    setInterval(refresh, 5000);
  }
});
