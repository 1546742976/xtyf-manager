"use strict";

// Every dialog owns its requests and signed preview. Bypass api() URL-level
// coalescing so a new dialog can never inherit another dialog's save result.
function createScheduleSession(projectId, request, changed = () => {}, afterSave = async () => {}) {
  const context = {draft: {base_date: "", durations: {}}, result: null, display: null,
    previewPending: false, saving: false, closed: false, dirty: false, message: "", success: false, error: false};
  let sequence = 0;
  const emit = (render = false) => changed(context, render);
  const send = async (action, payload) => {
    try { return await request("/api/projects/" + Number(projectId) + "/schedule/" + action, payload); }
    catch (error) { return {ok: false, error: error.message || "网络连接失败"}; }
  };
  context.invalidate = () => {
    if (context.saving || context.closed) return;
    sequence += 1;
    context.previewPending = false;
    context.result = null;
    context.dirty = true;
    context.success = false;
    context.error = false;
    context.message = "选项已修改，请更新预览后确认。";
    emit();
  };
  context.preview = async () => {
    if (context.saving || context.closed) return;
    const current = ++sequence;
    context.result = null;
    context.success = false;
    context.error = false;
    context.previewPending = false;
    const durations = {};
    for (const [id, value] of Object.entries(context.draft.durations)) {
      if (context.draft.task_ids && !context.draft.task_ids.includes(Number(id))) continue;
      if (value === "" || value == null) continue;
      if (!/^[1-9]\d*$/.test(String(value)) || !Number.isSafeInteger(Number(value))) {
        context.message = "任务 #" + Number(id) + " 的工期必须是正整数天。";
        context.error = true;
        emit();
        return;
      }
      durations[id] = Number(value);
    }
    const payload = {algorithm_version: 2, durations};
    if (context.draft.base_date) payload.base_date = context.draft.base_date;
    if (context.draft.task_ids !== undefined) payload.task_ids = [...context.draft.task_ids];
    context.previewPending = true;
    context.message = "正在更新预览…";
    emit();
    const result = await send("preview", payload);
    if (context.closed || current !== sequence) return;
    context.previewPending = false;
    if (!result?.ok) {
      context.error = true;
      context.message = (result?.error || "生成预览失败") + "；当前输入已保留，请重试。";
      emit();
      return;
    }
    context.result = result;
    context.display = result;
    context.dirty = false;
    context.draft.base_date = result.base_date;
    context.draft.task_ids = [...result.task_ids];
    const liveIds = new Set(result.rows.filter(row => row.eligible && row.needs_duration).map(row => String(row.id)));
    context.draft.durations = Object.fromEntries(Object.entries({...context.draft.durations, ...result.durations})
      .filter(([id]) => liveIds.has(id)));
    context.message = result.errors?.length ? "请处理下方问题，然后更新预览。" : result.changed_count
      ? "预览将调整 " + Number(result.changed_count) + " 项任务，确认后保存。" : "当前方案无需调整日期。";
    context.error = !!result.errors?.length;
    emit(true);
  };
  context.apply = async () => {
    if (context.closed || context.saving || !context.result?.can_apply || !context.result?.changed_count || !context.result?.snapshot) return;
    const snapshot = context.result.snapshot;
    sequence += 1;
    context.result = null;
    context.previewPending = false;
    context.saving = true;
    context.error = false;
    context.message = "正在保存排期，请勿关闭窗口…";
    emit();
    const result = await send("apply", {snapshot});
    if (result?.ok) {
      context.success = true;
      context.message = "排期已保存，共更新 " + Number(result.changed_count) + " 项任务。";
      try { await afterSave(); }
      catch (_) { context.message += "列表刷新失败，请稍后刷新页面。"; }
    } else {
      context.dirty = true;
      context.error = true;
      context.message = (result?.error || "保存结果未确认") + "；当前输入已保留，请更新预览后再次确认。";
    }
    context.saving = false;
    emit();
  };
  context.close = () => {
    if (context.saving) return false;
    context.closed = true;
    sequence += 1;
    return true;
  };
  return context;
}

const scheduleStates = {changed: "将调整", unchanged: "保持", protected: "受保护", needs_duration: "待补工期", conflict: "冲突", excluded: "不参与"};
const scheduleSessions = new Map();

function schedulePreviewRows(rows, draft = {}) {
  return rows.map((row) => {
    const id = Number(row.id), selected = draft.task_ids ? draft.task_ids.includes(id) : !!row.selected;
    const stateName = Object.hasOwn(scheduleStates, row.state) ? row.state : "unchanged";
    const shift = row.state === "changed" && (row.needs_duration || !row.before.start_date || !row.before.due_date) ? "补齐日期"
      : Number(row.shift_days) > 0 ? "顺延 " + Number(row.shift_days) + " 天"
      : ["needs_duration", "conflict"].includes(row.state) ? "待计算" : "顺延 0 天";
    const dates = (key, label) => '<div><span class="schedule-field-name">' + label + '</span><span class="schedule-old">原：' + esc(row.before[key] || "未设置") + '</span><strong>新：' + esc(row.after[key] || "未设置") + '</strong></div>';
    const needsDuration = row.eligible && (row.needs_duration || !row.before.start_date || !row.before.due_date);
    const duration = needsDuration
      ? '<label class="schedule-duration">工期（天，含首尾）<input type="number" inputmode="numeric" min="1" step="1" data-schedule-duration="' + id + '" value="' + esc(draft.durations?.[id] ?? row.duration_days ?? "") + '" aria-label="任务 ' + esc(row.title) + ' 的工期天数" ' + (selected ? "" : "disabled") + '></label>'
      : '<span class="schedule-duration">工期：' + (row.duration_days ? Number(row.duration_days) + " 天（含首尾）" : "未设置") + '</span>';
    const predecessors = (row.predecessors || []).map((item) => '<li>#' + Number(item.id) + ' ' + esc(item.title) + (item.external ? " · 跨车组" : "") + (item.boundary_date ? " · 衔接 " + esc(item.boundary_date) : "") + '</li>').join("");
    return '<article class="schedule-task" id="schedule-task-' + id + '" tabindex="-1" aria-label="任务 ' + esc(row.title) + '">' +
      '<div class="schedule-task-main"><label class="schedule-selection"><input type="checkbox" data-schedule-task="' + id + '" ' + (selected ? "checked" : "") + ' ' + (row.eligible ? "" : "disabled") + ' aria-label="' + (row.protected ? "允许本次调整" : "参与排期") + '：' + esc(row.title) + '"><span><strong>' + esc(row.title) + '</strong><small>#' + id + ' · ' + esc(row.status) + ' · ' + esc(row.owner_name || "未指定负责人") + (row.stage ? " · " + esc(row.stage) : "") + '</small></span></label>' +
      (row.protected ? '<p class="schedule-protection">' + esc(row.protection_reason || "日期受保护") + ' · ' + (row.eligible ? "勾选可允许本次调整，保留长期固定标记" : "保留原日期") + '</p>' : "") +
      '<div class="schedule-predecessors"><span>前置任务</span>' + (predecessors ? '<ul>' + predecessors + '</ul>' : "：无") + '</div></div>' +
      '<div class="schedule-dates">' + dates("start_date", "开始") + dates("due_date", "截止") + duration + '</div>' +
      '<div class="schedule-reason"><span class="schedule-state schedule-state-' + stateName + '">' + scheduleStates[stateName] + '</span><span class="schedule-shift">' + shift + '</span><p>' + esc(row.note || "") + '</p></div></article>';
  }).join("");
}

function scheduleProblemList(items, title, warning = false) {
  if (!items?.length) return "";
  return '<section class="warning-box schedule-problems ' + (warning ? "schedule-warnings" : "") + '" aria-label="' + title + '"><strong>' + title + '</strong><ul>' +
    items.map((item) => {
      const external = (item.related_tasks || []).filter(task => task.external).map(task =>
        '<div>外部参考：<a href="/tasks?task=' + Number(task.id) + '" target="_blank" rel="noopener">#' + Number(task.id) + ' ' + esc(task.title) + '</a> · ' + esc(task.project_name) + ' · ' + esc(task.start_date) + ' 至 ' + esc(task.due_date) + '（不在本次调整范围）</div>').join("");
      return '<li>' + (item.task_id ? '<button class="schedule-problem-link" type="button" data-schedule-focus="' + Number(item.task_id) + '">任务 #' + Number(item.task_id) + '</button>：' : "") + esc(item.message || item) + external + '</li>';
    }).join("") + '</ul></section>';
}

async function openSchedulePreview(projectId) {
  if (!isManagement() || !projectId) return;
  const projectKey = Number(projectId), previous = scheduleSessions.get(projectKey);
  if (previous && !previous.closed) {
    document.querySelector("[data-schedule-root]")?.querySelector("[data-schedule-close]")?.focus();
    return;
  }
  let root;
  const session = createScheduleSession(projectKey, apiRequest, update, refresh);
  const opened = showModal('<div data-schedule-root class="schedule-shell"><header class="modal-head schedule-head"><div><p class="ux-eyebrow">TEAM / SCHEDULE</p><h2>整组排期预览</h2></div><button class="btn ghost sm" type="button" data-schedule-close>关闭</button></header>' +
    '<div class="schedule-scroll"><p class="ux-note">按所有前置依赖计算，允许提前 2 个日历日衔接，且不早于前置开始日；默认只顺延。范围包含整组任务，不受列表筛选影响。进行中和固定日期的任务默认受保护；确认后保存日期，不持续自动联动。同一负责人跨车组及未归属车组任务的日期重叠仅提醒，外部任务不在本次调整范围。</p>' +
    '<div class="schedule-controls"><label>基准日期<input type="date" id="schedule-base-date" required></label><p id="schedule-summary" class="schedule-summary" role="status">正在读取整组任务…</p></div>' +
    '<p id="schedule-stale" class="schedule-stale hidden" role="status">选项已修改，下方仍为上次预览；请更新预览。</p><div id="schedule-body"><p class="empty">正在生成预览…</p></div></div>' +
    '<footer class="schedule-footer"><p id="schedule-feedback" class="form-message" role="status" aria-live="polite"></p><div class="modal-actions"><button class="btn" type="button" id="schedule-repreview">更新预览</button><button class="btn primary" type="button" id="schedule-apply" disabled>确认应用排期</button></div></footer></div>', "整组排期预览", {
      className: "schedule-dialog", beforeClose: () => !session.saving,
      onClose: () => { session.close(); scheduleSessions.delete(projectKey); },
    });
  if (opened === false) return;
  scheduleSessions.set(projectKey, session);
  root = document.querySelector("[data-schedule-root]");
  const alive = () => !session.closed && document.querySelector("[data-schedule-root]") === root;
  const base = root.querySelector("#schedule-base-date");
  let baseEdited = false;
  function update(context, render) {
    if (!root || !alive()) return;
    const result = context.display;
    if (render && result) {
      base.value = context.draft.base_date;
      root.querySelector("#schedule-body").innerHTML = scheduleProblemList(result.errors, "请先处理以下问题")
        + scheduleProblemList(result.warnings, "排期提醒（不阻止应用）", true)
        + '<div class="schedule-tasks">' + (schedulePreviewRows(result.rows, context.draft) || '<p class="empty">当前造车组别没有任务</p>') + '</div>';
      root.querySelectorAll("[data-schedule-task]").forEach(input => input.addEventListener("change", () => {
        context.draft.task_ids = [...root.querySelectorAll("[data-schedule-task]:checked")].map(item => Number(item.dataset.scheduleTask));
        context.invalidate();
      }));
      root.querySelectorAll("[data-schedule-duration]").forEach(input => input.addEventListener("input", () => {
        context.draft.durations[input.dataset.scheduleDuration] = input.value;
        context.invalidate();
      }));
      root.querySelectorAll("[data-schedule-focus]").forEach(button => button.addEventListener("click", () => {
        const target = root.querySelector("#schedule-task-" + Number(button.dataset.scheduleFocus));
        target?.scrollIntoView({block: "center", behavior: "smooth"});
        target?.focus({preventScroll: true});
      }));
    }
    if (result) {
      const selected = new Set(context.draft.task_ids || []);
      const protectedCount = result.rows.filter(row => row.protected && !selected.has(Number(row.id))).length;
      root.querySelector("#schedule-summary").textContent = result.project.name + " · 参与 " + selected.size + " 项 · 受保护 " + protectedCount + " 项 · 共 " + result.rows.length + " 项";
      root.querySelectorAll("[data-schedule-duration]").forEach(input => {
        input.disabled = context.saving || !selected.has(Number(input.dataset.scheduleDuration));
      });
      root.querySelectorAll("[data-schedule-task]").forEach(input => {
        input.disabled = context.saving || !result.rows.find(row => Number(row.id) === Number(input.dataset.scheduleTask))?.eligible;
      });
    }
    root.setAttribute("aria-busy", String(context.saving || context.previewPending));
    root.querySelector("#schedule-stale").classList.toggle("hidden", !context.dirty || !result);
    const feedback = root.querySelector("#schedule-feedback");
    feedback.textContent = context.message;
    feedback.classList.toggle("schedule-success", context.success);
    feedback.classList.toggle("schedule-error", context.error);
    feedback.setAttribute("role", context.error ? "alert" : "status");
    base.disabled = context.saving;
    root.querySelector("[data-schedule-close]").disabled = context.saving;
    root.querySelector("#schedule-repreview").disabled = context.saving || context.previewPending;
    root.querySelector("#schedule-apply").disabled = context.saving || context.previewPending || !context.result?.can_apply || !context.result?.changed_count;
  }
  root.querySelector("[data-schedule-close]").addEventListener("click", hideModal);
  base.addEventListener("input", () => { baseEdited = true; session.draft.base_date = base.value; session.invalidate(); });
  root.querySelector("#schedule-repreview").addEventListener("click", () => {
    // Before the first successful response, the server has not supplied today's
    // default yet. Retrying that request must not require inventing a date.
    if ((session.display || baseEdited) && !base.reportValidity()) return;
    session.preview();
  });
  root.querySelector("#schedule-apply").addEventListener("click", () => session.apply());
  await session.preview();
}

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("schedule-project")?.addEventListener("click", () => openSchedulePreview(taskFilters.project));
});
