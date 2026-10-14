"use strict";

function schedulePreviewRows(rows) {
  return rows.map((row) => {
    const choices = row.stage_options || [];
    const stage = row.eligible && choices.length > 1
      ? `<select data-phase-choice="${Number(row.id)}" aria-label="任务 ${esc(row.title)} 的专业阶段"><option value="">请选择阶段</option>${choices.map((item) => `<option ${row.stage === item ? "selected" : ""}>${esc(item)}</option>`).join("")}</select>`
      : esc(row.stage || "未归类");
    const dates = (key) => `<span class="schedule-old">原：${esc(row.before[key] || "未设置")}</span><strong>新：${esc(row.after[key] || "未设置")}</strong>`;
    return `<tr><td><strong>${esc(row.title)}</strong><small>#${Number(row.id)} · ${esc(row.status)}</small></td><td>${esc(row.owner_name)}</td><td>${stage}</td><td class="schedule-date">${dates("start_date")}</td><td class="schedule-date">${dates("due_date")}</td><td class="schedule-note">${esc(row.note)}</td></tr>`;
  }).join("");
}

async function openSchedulePreview(projectId) {
  if (!isManagement() || !projectId) return;
  const context = {choices: {}, result: null};
  showModal(`<div data-schedule-root><div class="modal-head"><div><p class="ux-eyebrow">TEAM / SCHEDULE</p><h2>整组排期预览</h2></div><button class="btn ghost sm" type="button" data-schedule-close>关闭</button></div><p class="ux-note">仅调整该造车组别的未开始、进行中和阻塞任务。同阶段并行，后一阶段从前一阶段最晚截止日期开始，保持原工期。</p><div id="schedule-body">正在生成预览…</div><p id="schedule-feedback" class="form-message" role="alert"></p><div class="modal-actions"><button class="btn" type="button" id="schedule-repreview">重新预览</button><button class="btn primary" type="button" id="schedule-apply" disabled>确认应用排期</button></div></div>`, "整组排期预览");
  const root = document.querySelector("[data-schedule-root]");
  const alive = () => document.querySelector("[data-schedule-root]") === root;
  const feedback = root.querySelector("#schedule-feedback");
  const apply = root.querySelector("#schedule-apply");
  const repreview = root.querySelector("#schedule-repreview");
  root.querySelector("[data-schedule-close]").addEventListener("click", hideModal);
  function busy(value) {
    root.setAttribute("aria-busy", String(value));
    repreview.disabled = value;
    root.querySelectorAll("[data-phase-choice]").forEach((select) => { select.disabled = value; });
    apply.disabled = value || !context.result?.can_apply || !context.result?.changed_count;
  }
  async function preview(focusTask = null) {
    busy(true);
    context.result = null;
    feedback.textContent = "";
    feedback.classList.remove("schedule-success");
    feedback.setAttribute("role", "alert");
    const result = await api(`/api/projects/${Number(projectId)}/schedule/preview`, {phase_choices: context.choices});
    if (!alive()) return;
    if (!result?.ok) {
      if (result?.httpStatus === 400 || result?.httpStatus === 409) context.choices = {};
      feedback.textContent = result?.error || "生成预览失败，请重试";
      root.querySelector("#schedule-body").innerHTML = '<p class="empty">尚未生成有效预览</p>';
      busy(false);
      return;
    }
    context.result = result;
    const problems = result.errors.length ? `<div class="warning-box"><strong>请先处理以下问题</strong><ul>${result.errors.map((error) => `<li>${esc(error)}</li>`).join("")}</ul></div>` : "";
    const summary = result.can_apply ? result.changed_count ? `将更新 ${Number(result.changed_count)} 项任务的日期或阶段选择。` : "当前日期已符合阶段衔接，无需调整。" : result.errors.length ? "补齐日期或选择阶段后，重新查看预览。" : "当前造车组别没有可排期任务。";
    root.querySelector("#schedule-body").innerHTML = `<p class="schedule-summary" role="status"><strong>${esc(result.project.name)}</strong> · ${esc(summary)}</p>${problems}<div class="table-wrap schedule-table"><table><thead><tr><th>任务</th><th>负责人</th><th>专业阶段</th><th>开始日期</th><th>截止日期</th><th>说明</th></tr></thead><tbody>${schedulePreviewRows(result.rows) || '<tr><td colspan="6" class="empty">当前造车组别没有任务</td></tr>'}</tbody></table></div>`;
    root.querySelectorAll("[data-phase-choice]").forEach((select) => {
      select.addEventListener("change", () => {
        const id = select.dataset.phaseChoice;
        if (select.value) context.choices[id] = select.value;
        else delete context.choices[id];
        preview(id);
      });
    });
    busy(false);
    if (focusTask) root.querySelector(`[data-phase-choice="${Number(focusTask)}"]`)?.focus();
  }
  repreview.addEventListener("click", () => preview());
  apply.addEventListener("click", async () => {
    if (!context.result?.snapshot || apply.disabled) return;
    const snapshot = context.result.snapshot;
    busy(true);
    feedback.textContent = "正在保存排期…";
    const result = await api(`/api/projects/${Number(projectId)}/schedule/apply`, {snapshot});
    if (alive()) {
      context.result = null;
      busy(false);
      if (result?.ok) {
        feedback.classList.add("schedule-success");
        feedback.setAttribute("role", "status");
        feedback.textContent = `排期已保存，共更新 ${Number(result.changed_count)} 项任务。`;
        root.querySelectorAll("[data-phase-choice]").forEach((select) => { select.disabled = true; });
      } else {
        feedback.classList.remove("schedule-success");
        feedback.setAttribute("role", "alert");
        feedback.textContent = result?.error || "保存失败，请重新预览";
        if (result?.httpStatus === 409) context.choices = {};
      }
    }
    if (result?.ok) await refresh();
  });
  await preview();
}

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("schedule-project")?.addEventListener("click", () => openSchedulePreview(taskFilters.project));
});
