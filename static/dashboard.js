"use strict";

function dashboardTaskRow(task) {
  const percent = Math.max(0, Math.min(100, Number(task.progress) || 0));
  const people = taskParticipants(task)
    .map((id) => memberById(id)?.name || `#${id}`).join("、") || "未分配";
  const latest = task.latest_progress;
  const activity = latest
    ? `<span class="ws-task-activity">
        <span class="ws-task-activity-head">近期动态 · ${esc(latest.actor_name || "成员")} · ${esc(latest.created_at || "")}</span>
        <span class="ws-task-activity-note">${esc(latest.note || `更新进度至 ${Number(latest.progress) || 0}%`)}</span>
        ${(latest.image_ids || []).slice(0, 2).map((id) =>
          `<span class="ws-task-activity-image"><img src="/api/task/${Number(task.id)}/attachments/${Number(id)}" alt="进度截图" loading="lazy"></span>`,
        ).join("")}
      </span>`
    : '<span class="ws-task-activity-empty">暂无进度动态</span>';
  return `<button type="button" class="ws-task-row${isDueSoon(task) ? " is-due-soon" : ""}${isOverdue(task) ? " is-overdue" : ""}"
      data-dashboard-task="${Number(task.id)}" aria-label="查看任务 ${esc(task.title)} 的只读详情">
    <span class="ws-task-row-main">
      <span class="ws-task-row-top">
        <span class="prio prio-${task.priority === "高" ? "high" : task.priority === "低" ? "low" : "mid"}"><i class="dot"></i></span>
        <span class="ws-task-row-title"><strong>${esc(task.title)}</strong></span>
        ${badge(task.status, STATUS_CLASS[task.status] || "gray")}
        ${badge(daysLeftText(task), daysLeftClass(task))}
      </span>
      <span class="ws-task-row-sub">${esc(taskProjectName(task) || "未归属造车组别")} · ${esc(people)}</span>
      <span class="ws-task-progress-head"><span>任务进度</span><strong>${percent}%</strong></span>
      <span class="progress" role="progressbar" aria-label="任务进度" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${percent}"><span class="progress-inner blue" style="display:block;width:${percent}%"></span></span>
      ${activity}
    </span>
  </button>`;
}

function renderWorkspaceDashboard() {
  if (!state.me) return;
  const active = state.tasks.filter((task) => !["已完成", "已取消"].includes(task.status));
  const mine = active.filter((task) => taskParticipants(task).includes(Number(state.me.id)));
  const dueSoon = active.filter((task) => Number.isInteger(task.days_left) && task.days_left >= 0 && task.days_left <= 7);
  const overdue = active.filter(isOverdue);
  const metrics = [
    ["我的待办", mine.length, "含负责人及参与任务"],
    ["进行中任务", active.filter((task) => task.status === "进行中").length, "团队实时数据"],
    ["7 天内到期", dueSoon.length, "请及时确认进度"],
    ["逾期任务", overdue.length, overdue.length ? "需要优先处理" : "当前无逾期"],
  ];
  const stats = document.getElementById("dashboard-stats");
  if (stats)
    stats.innerHTML = metrics.map(([label, value, note]) =>
      `<div class="ws-dashboard-stat"><span class="ws-label">${esc(label)}</span><strong class="ws-value">${value}</strong><span class="ws-footnote">${esc(note)}</span></div>`).join("");
  const ranked = [...new Map([...mine, ...overdue, ...dueSoon, ...active].map((task) => [task.id, task])).values()].slice(0, 8);
  const target = document.getElementById("dashboard-task-list");
  if (target) target.innerHTML = ranked.map(dashboardTaskRow).join("") || '<p class="empty">当前没有待处理任务</p>';
}
addDataListener(renderWorkspaceDashboard);
document.addEventListener("DOMContentLoaded", () =>
  document.getElementById("dashboard-task-list")?.addEventListener("click", (event) => {
    const row = event.target.closest("[data-dashboard-task]");
    if (row) openTaskPreview(Number(row.dataset.dashboardTask));
  }),
);
