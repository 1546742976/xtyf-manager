"use strict";
function renderWorkspaceDashboard() {
  const all = state.tasks.filter(t => t.status !== '已取消');
  const active = all.filter(t => t.status !== '已完成');
  const mine = active.filter(t => t.owner_id === state.me.id);
  const inProgress = all.filter(t => t.status === '进行中');
  const dueSoon = active.filter(t => Number.isInteger(t.days_left) && t.days_left >= 0 && t.days_left <= 7);
  const overdue = active.filter(isOverdue);
  const metrics = [
    ['我的待办', mine.length, '属于你的未完成任务'],
    ['进行中任务', inProgress.length, '团队当前进行中'],
    ['7 天内到期', dueSoon.length, '未来一周需要关注'],
    ['逾期任务', overdue.length, '已超过截止日期'],
  ];
  document.getElementById('dashboard-stats').innerHTML = metrics.map(([label, number, note]) =>
    `<div class="ws-dashboard-stat"><span class="ws-label">${label}</span><strong class="ws-value">${number}</strong><span class="ws-footnote">${note}</span></div>`).join('');
  const ranked = [...active].sort((a,b) => {
    const rank = t => isOverdue(t) ? 0 : (Number.isInteger(t.days_left) ? t.days_left + 1 : 100000);
    return rank(a)-rank(b) || (b.updated_at || '').localeCompare(a.updated_at || '');
  }).slice(0, 7);
  const target = document.getElementById('dashboard-task-list');
  target.innerHTML = ranked.map(t => {
    const owner = memberById(t.owner_id);
    return `<button type="button" class="ws-task-row" data-task-id="${t.id}"><span class="ws-task-row-title"><strong>${esc(t.title)}</strong><small>${esc(t.group_name || '未分组')} · ${esc(owner ? owner.name : '未分配')}</small></span>${badge(t.status, STATUS_CLASS[t.status] || 'gray')}<span class="ws-task-date">${esc(daysLeftText(t))}</span></button>`;
  }).join('') || '<p class="empty">目前没有待处理任务</p>';
}
window.onData = renderWorkspaceDashboard;
document.getElementById('dashboard-task-list').addEventListener('click', event => {
  const button = event.target.closest('[data-task-id]');
  if (button) openTaskModal(Number(button.dataset.taskId));
});
document.addEventListener('DOMContentLoaded', () => {
  refresh();
  setInterval(refresh, 15000);
});
