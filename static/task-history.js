"use strict";

const FIELD_LABELS = {
  title: "标题",
  detail: "说明",
  owner_id: "负责人",
  participant_ids: "参与成员",
  group_ids: "专业组",
  project_id: "项目",
  priority: "优先级",
  status: "状态",
  completed_at: "完成时间",
  start_date: "开始日期",
  start_date_origin: "日期来源",
  due_date: "截止日期",
  schedule_stage: "排期阶段",
  schedule_locked: "自动排期时固定日期",
  parent_id: "主前置任务",
  predecessor_ids: "前置任务",
};
const REVISION_KIND_LABELS = {
  create: "创建",
  update: "更新",
  revert: "撤销",
  delete: "删除",
};

function revisionValue(field, value) {
  if (field === "schedule_locked") return value ? "是" : "否";
  if (value == null || value === "") return "未设置";
  if (field === "start_date_origin") return ({frozen: "已有日期（固定）", manual: "手动设置", dependency: "主前置任务建议（固定）", schedule: "确认应用排期"})[value] || String(value);
  if (field === "owner_id") return memberById(value)?.name || `成员 #${value}`;
  if (field === "participant_ids") {
    return (value || []).map((id) => memberById(id)?.name || `成员 #${id}`).join("、") || "无";
  }
  if (field === "group_ids") {
    return (value || []).map((id) => groupById(id)?.name || `组 #${id}`).join("、") || "无";
  }
  if (field === "project_id") return projectById(value)?.name || `项目 #${value}`;
  if (field === "parent_id") {
    return state.tasks.find((task) => Number(task.id) === Number(value))?.title || `任务 #${value}`;
  }
  if (field === "predecessor_ids") {
    return (value || []).map((id) => state.tasks.find((task) => Number(task.id) === Number(id))?.title || `任务 #${id}`).join("、") || "无";
  }
  return String(value);
}

function revisionDiff(revision) {
  const before = revision.before || {};
  const after = revision.after || {};
  const fields = [...new Set([...Object.keys(before), ...Object.keys(after)])];
  return (
    fields
      .map(
        (field) =>
          `<div class="diff-row"><strong>${esc(FIELD_LABELS[field] || field)}</strong><span>${esc(revisionValue(field, before[field]))}</span><span aria-hidden="true">→</span><span>${esc(revisionValue(field, after[field]))}</span></div>`,
      )
      .join("") || '<p class="muted">没有字段差异</p>'
  );
}

async function loadRevisions(task, generation) {
  const box = document.getElementById("task-revisions");
  if (!box) return;
  const data = await api(`/api/task/${task.id}/revisions`);
  if (!taskModalActive(box, generation, task.id)) return;
  if (!data?.ok) {
    box.innerHTML = `<p class="form-message">${esc(data?.error || "版本记录加载失败")}</p>`;
    return;
  }
  box.innerHTML =
    (data.revisions || [])
      .map((revision) => {
        const reversible = ["update", "revert"].includes(revision.kind);
        const revertButton =
          state.me?.role === "admin" && !revision.reverted_by && reversible
            ? `<button class="btn ghost sm" type="button" data-revert="${Number(revision.id)}">撤销这次变更</button>`
            : "";
        const reverted = revision.reverted_by ? ` · 已撤销（修订 #${Number(revision.reverted_by)}）` : "";
        return `<article class="revision"><div class="revision-head"><strong>${esc(revision.actor_name || "系统")} · v${Number(revision.version || 0)}</strong><time>${esc(revision.created_at)}</time></div><p class="revision-kind">${esc(REVISION_KIND_LABELS[revision.kind] || revision.kind || "更新")}${reverted}</p>${revisionDiff(revision)}${revertButton}</article>`;
      })
      .join("") || '<p class="empty">暂无结构化版本记录</p>';
}

async function loadLogs(task, generation) {
  const box = document.getElementById("task-logs");
  if (!box) return;
  const data = await api(`/api/task/${task.id}/logs`);
  if (!taskModalActive(box, generation, task.id)) return;
  if (!data?.ok) {
    box.textContent = data?.error || "动态加载失败";
    return;
  }
  box.innerHTML =
    (data.logs || [])
      .map(
        (log) =>
          `<div class="log"><div class="log-head"><b>${esc(log.member_name || "系统")}</b><span>${esc(log.created_at)}</span></div>${log.changes ? `<div class="log-changes">${esc(log.changes)}</div>` : ""}${log.note ? `<div class="log-note">${esc(log.note)}</div>` : ""}</div>`,
      )
      .join("") || '<p class="empty">暂无动态</p>';
}

function attachmentCard(file) {
  const remove = file.can_delete ? `<button class="btn ghost sm danger-text" type="button" data-delete-attachment="${Number(file.id)}">删除</button>` : "";
  return `<article class="attachment-card"><a href="${esc(file.url)}" target="_blank" rel="noopener" aria-label="预览截图 ${esc(file.original_name)}"><img src="${esc(file.url)}" width="${Number(file.width) || 320}" height="${Number(file.height) || 180}" alt="${esc(file.description || file.original_name)}" loading="lazy"></a><div><strong>${esc(file.original_name)}</strong><p>${esc(file.description || "无说明")} · ${Math.ceil((file.size_bytes || 0) / 1024)} KB</p><small>${esc(file.member_name)} · ${esc(file.created_at)}</small>${remove}</div></article>`;
}

async function loadAttachments(task, generation) {
  const box = document.getElementById("attachment-list");
  if (!box) return;
  box.innerHTML = '<p class="empty">正在加载截图…</p>';
  const data = await api(`/api/task/${task.id}/attachments`);
  if (!taskModalActive(box, generation, task.id)) return;
  box.innerHTML = data?.ok
    ? (data.attachments || []).map(attachmentCard).join("") || '<p class="empty">暂无截图附件</p>'
    : `<p class="form-message">${esc(data?.error || "截图加载失败")}</p>`;
}
