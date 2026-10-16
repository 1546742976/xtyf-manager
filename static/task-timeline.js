"use strict";

const TIMELINE_DAY_MS = 86400000;
function timelineDay(value) {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value) || value.startsWith("0000")) return null;
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value
    ? date.getTime() / TIMELINE_DAY_MS : null;
}
function timelineDates(task) {
  if (!task.start_date || !task.due_date) {
    return {error: !task.start_date && !task.due_date ? "未设置计划日期" : !task.start_date ? "缺少开始日期" : "缺少截止日期"};
  }
  const start = timelineDay(task.start_date), end = timelineDay(task.due_date);
  if (start === null || end === null) return {error: "计划日期格式非法"};
  if (end < start) return {error: "截止日期早于开始日期"};
  return {start, end};
}
function timelineDateLabel(day) { return new Date(day * TIMELINE_DAY_MS).toISOString().slice(0, 10); }
function timelineRange(tasks) {
  const dates = tasks.map(timelineDates).filter((date) => !date.error);
  if (!dates.length) return null;
  const start = Math.min(...dates.map((date) => date.start));
  const end = Math.max(...dates.map((date) => date.end));
  // One day's padding keeps same-day markers visible at either boundary.
  return {start: start - 1, end: end + 1, first: start, last: end};
}
function timelinePosition(task, range) {
  const dates = timelineDates(task);
  if (dates.error || !range) return null;
  return {left: (dates.start - range.start) / (range.end - range.start) * 100,
    width: (dates.end - dates.start) / (range.end - range.start) * 100, sameDay: dates.start === dates.end};
}
function timelineTicks(range) {
  if (!range) return [];
  const span = range.end - range.start, count = Math.min(8, span + 1);
  return Array.from({length: count}, (_, index) => {
    const day = range.start + Math.round(span * index / (count - 1));
    return {label: timelineDateLabel(day).slice(5).replace("-", "."), left: (day - range.start) / span * 100};
  });
}
function timelineTaskRow(task, range) {
  const dates = timelineDates(task), position = timelinePosition(task, range);
  const owner = memberById(task.owner_id)?.name || "未指定负责人";
  const status = task.status || "未开始";
  const dateText = dates.error || `${task.start_date} → ${task.due_date}${position?.sameDay ? " · 同日任务" : ""}`;
  const bar = position
    ? `<span class="timeline-bar ${position.sameDay ? "same-day" : ""} ${["已完成", "已取消"].includes(status) ? "is-finished" : ""}" style="left:${position.left}%;width:${position.width}%"></span>`
    : '<span class="timeline-no-date">日期待完善</span>';
  return `<button type="button" class="timeline-row ${status === "阻塞" ? "is-blocked" : ""}" data-task-id="${Number(task.id)}" aria-label="查看任务 ${esc(task.title)}，${esc(owner)}，${esc(status)}，${esc(dateText)}">
    <span class="timeline-task-info"><span class="timeline-task-title">${esc(task.title)}</span>
      <span class="timeline-task-meta"><span>${esc(owner)}</span>${badge(status, STATUS_CLASS[status] || "gray")}</span>
      <span class="timeline-task-dates ${dates.error ? "has-date-error" : ""}">${esc(dateText)}</span></span>
    <span class="timeline-track" aria-hidden="true">${timelineTicks(range).map((tick) => `<i class="timeline-gridline" style="left:${tick.left}%"></i>`).join("")}${bar}</span></button>`;
}
function renderSpecialtyTimeline(tasks) {
  const projectTasks = state.tasks.filter((task) =>
    Number(task.project_id) === Number(state.filters.project) || taskProjectName(task) === state.filters.project);
  const range = timelineRange(projectTasks), ticks = timelineTicks(range);
  const project = projectById(state.filters.project)?.name || state.filters.project;
  const span = range ? `${timelineDateLabel(range.first)} — ${timelineDateLabel(range.last)}` : "暂无有效计划日期";
  return `<section class="timeline-panel" aria-label="${esc(project)}专业组任务时间轴">
    <header class="timeline-head"><div><p class="ux-eyebrow">PROJECT TIMELINE</p><h2>${esc(project)}排期</h2><p>${esc(span)}</p></div>
      <span class="timeline-legend"><i aria-hidden="true"></i>计划时间 · 点击任务查看详情</span></header>
    <div class="timeline-axis" aria-hidden="true"><span>任务 / 负责人</span><div>${ticks.map((tick) => `<span style="left:${tick.left}%">${esc(tick.label)}</span>`).join("")}</div></div>
    ${specialtyLanes(tasks).map((lane) => `<section class="timeline-stage"><header class="timeline-stage-head"><h3>${esc(lane.title)}</h3><span>${lane.tasks.length} 项任务</span>${lane.title === "未归类" ? '<small>未指定专业组或待选择阶段</small>' : ""}</header>
      ${lane.tasks.map((task) => timelineTaskRow(task, range)).join("") || '<p class="timeline-empty">暂无匹配任务</p>'}</section>`).join("")}
    <footer class="timeline-foot">机械 → 电控 → 视觉 · 时间轴展示已保存日期，调整排期请到任务管理。</footer>
    </section>`;
}
