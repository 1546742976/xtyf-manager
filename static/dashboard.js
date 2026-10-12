"use strict";
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
    stats.innerHTML = metrics
      .map(
        ([label, value, note]) =>
          `<div class="ws-dashboard-stat"><span class="ws-label">${esc(label)}</span><strong class="ws-value">${value}</strong><span class="ws-footnote">${esc(note)}</span></div>`,
      )
      .join("");
  const ranked = [...new Map([...mine, ...overdue, ...dueSoon, ...active].map((task) => [task.id, task])).values()].slice(0, 8),
    target = document.getElementById("dashboard-task-list");
  if (target)
    target.innerHTML =
      ranked
        .map(
          (task) =>
            `<button type="button" class="ws-task-row" data-dashboard-task="${Number(task.id)}"><span class="prio prio-${task.priority === "高" ? "high" : task.priority === "低" ? "low" : "mid"}"><i class="dot"></i></span><span class="ws-task-row-title"><strong>${esc(task.title)}</strong><small>${esc(taskProjectName(task) || "未归属项目")} · ${esc(
              taskParticipants(task)
                .map((id) => memberById(id)?.name || `#${id}`)
                .join("、") || "未分配",
            )}</small></span><span>${badge(task.status, STATUS_CLASS[task.status] || "gray")}</span><span class="ws-task-date">${esc(daysLeftText(task))}</span></button>`,
        )
        .join("") || '<p class="empty">当前没有待处理任务</p>';
}
addDataListener(renderWorkspaceDashboard);
document.addEventListener("DOMContentLoaded", () =>
  document.getElementById("dashboard-task-list")?.addEventListener("click", (event) => {
    const row = event.target.closest("[data-dashboard-task]");
    if (row) openTaskModal(Number(row.dataset.dashboardTask));
  }),
);
