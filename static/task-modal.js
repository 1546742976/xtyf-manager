"use strict";
let taskModalGeneration = 0;
function modalMembers(task) {
  const map = new Map([...state.members, ...state.allMembers].map((member) => [Number(member.id), member]));
  taskParticipants(task).forEach((id) => {
    if (!map.has(id)) map.set(id, { id, name: `成员 #${id}`, is_active: 0 });
  });
  return [...map.values()].filter((member) => (member.is_active !== 0 && !member.deleted_at) || taskParticipants(task).includes(Number(member.id)));
}
function memberLabel(member) {
  return `${member.name}${(member.group_names || [member.group_name]).filter(Boolean).length ? ` · ${(member.group_names || [member.group_name]).filter(Boolean).join("/")}` : ""}`;
}
function memberOptions(members, selected) {
  return (
    '<option value="">未指定</option>' +
    members
      .map(
        (member) =>
          `<option ${member.is_active === 0 && Number(member.id) !== Number(selected) ? "disabled" : ""} value="${Number(member.id)}" ${Number(member.id) === Number(selected) ? "selected" : ""}>${esc(memberLabel(member))}${member.is_active === 0 ? "（已停用）" : ""}</option>`,
      )
      .join("")
  );
}
function taskLabel(task) {
  return `#${task.id} ${task.title}`;
}
function taskReadOnly(task) {
  const participants =
    taskParticipants(task)
      .map((id) => memberById(id)?.name || `成员 #${id}`)
      .join("、") || "未指定";
  const predecessors =
    (task.predecessor_ids || []).map((id) => state.tasks.find((item) => Number(item.id) === Number(id))?.title || `任务 #${id}`).join("、") || "无";
  return `<div class="readonly-settings"><p class="permission-note">你可以查看此任务，但不在编辑参与范围内。</p><dl><div><dt>负责人及参与成员</dt><dd>${esc(participants)}</dd></div><div><dt>造车组别</dt><dd>${esc(taskProjectName(task) || "未选择")}</dd></div><div><dt>优先级 / 状态</dt><dd>${esc(task.priority)} / ${esc(task.status)}</dd></div><div><dt>计划日期</dt><dd>${esc(task.start_date || "未设置")} 至 ${esc(task.due_date || "未设置")}</dd></div><div><dt>前置任务</dt><dd>${esc(predecessors)}</dd></div></dl>${task.detail ? `<div class="task-detail">${esc(task.detail)}</div>` : ""}${task.suggested_start_date ? `<p class="advisory">根据主前置任务日期，建议开始日期：<strong>${esc(task.suggested_start_date)}</strong>（不会自动填写）</p>` : ""}${(task.dependency_warnings || []).length ? `<div class="warning-box" role="status"><strong>依赖提醒</strong><ul>${task.dependency_warnings.map((item) => `<li>${esc(item)}</li>`).join("")}</ul></div>` : ""}</div>`;
}
function taskForm(task) {
  const editable = canEdit(task),
    members = modalMembers(task),
    others = state.tasks.filter((item) => Number(item.id) !== Number(task.id));
  if (!editable) return taskReadOnly(task);
  return `<form id="task-form" class="task-settings"><input type="hidden" name="version" value="${Number(task.version || 0)}">
    <label>标题<input name="title" value="${esc(task.title)}" required></label>
    <div class="grid-2"><label>负责人（主负责人）<select name="owner_id">${memberOptions(members, task.owner_id)}</select></label><label>造车组别<select name="project_id">${options(state.projects, task.project_id, "未选择")}</select></label></div>
    <fieldset><legend>参与成员（可多选）</legend><p class="field-help">负责人和参与成员都可编辑任务设置。</p>${choiceGrid("participant_ids", members, task.participant_ids || [], memberLabel)}</fieldset>
    <div class="grid-2"><label>优先级<select name="priority">${state.priorities.map((value) => `<option ${value === task.priority ? "selected" : ""}>${esc(value)}</option>`).join("")}</select></label><label>状态<select name="status">${state.statuses.map((value) => `<option ${value === task.status ? "selected" : ""}>${esc(value)}</option>`).join("")}</select></label></div>
    <div class="grid-2"><label>开始日期<input name="start_date" type="date" value="${esc(task.start_date)}"></label><label>截止日期<input name="due_date" type="date" value="${esc(task.due_date)}"></label></div>
    <label class="choice-item schedule-lock-choice"><input type="checkbox" name="schedule_locked" ${task.schedule_locked ? "checked" : ""}><span>自动排期时固定日期</span></label>
    ${`<label>主前置任务<select name="parent_id">${options(others.map((item) => ({...item, name: taskLabel(item)})), task.parent_id, "无主前置任务")}</select></label>`}
    <div class="task-date-guidance"><p class="field-help" data-task-date-note aria-live="polite"></p><button class="btn ghost sm" type="button" data-task-date-reset>重置为依赖时间</button></div>
    <fieldset><legend>其他前置任务（支持多选）</legend>${choiceGrid("predecessor_ids", others, task.predecessor_ids || [], taskLabel)}</fieldset>
    ${(task.dependency_warnings || []).length ? `<div class="warning-box" role="status"><strong>依赖提醒</strong><ul>${task.dependency_warnings.map((item) => `<li>${esc(item)}</li>`).join("")}</ul></div>` : ""}
    <label>说明<textarea name="detail" rows="3">${esc(task.detail)}</textarea></label><label>本次更新备注<input name="note" placeholder="选填，记录本次调整原因"></label>
    <p id="task-form-message" class="form-message" role="alert"></p><div class="modal-actions">${isManagement() ? '<button type="button" class="btn danger" data-delete-task>删除任务</button>' : ""}<button class="btn primary" type="submit">保存更新</button></div>
  </form>`;
}
function taskModalActive(box, generation, taskId) {
  const modal = document.getElementById("modal");
  return !!box?.isConnected && modal?.contains(box) && Number(modal.dataset.taskId) === Number(taskId) && Number(modal.dataset.generation) === generation;
}
function taskModalShell(task) {
  const lead = memberById(task.owner_id);
  return `<div class="modal-head"><div><p class="ux-eyebrow">TASK #${Number(task.id)}</p><h2>${esc(task.title)}</h2></div><button class="btn ghost sm" type="button" data-close>关闭</button></div>
    <div class="modal-info">${badge(task.status, STATUS_CLASS[task.status] || "gray")}${badge(`负责人 ${lead?.name || "未指定"}`, "outline")}<span class="muted">${esc(taskProjectName(task) || "未归属造车组别")} · 版本 ${Number(task.version || 0)}</span></div>
    ${taskForm(task)}
    ${progressSection(task)}
    <section class="modal-section"><h3>截图附件</h3>${canEdit(task) ? '<form id="attachment-form" class="attachment-form"><label>选择图片（PNG / JPEG / WebP；每张不超过 10 MiB，最多 5 张）<input name="files" type="file" accept="image/png,image/jpeg,image/webp" multiple required></label><label>说明<input name="description" maxlength="300" placeholder="截图内容或验收场景"></label><button class="btn" type="submit">上传截图</button><p class="form-message" role="alert"></p></form>' : ""}<div id="attachment-list"></div></section>
    <section class="modal-section"><h3>版本记录</h3><div id="task-revisions"><p class="empty">正在加载版本记录…</p></div></section>
    <section class="modal-section"><h3>动态与备注</h3><div id="task-logs" class="logs"><p class="empty">正在加载动态…</p></div></section>`;
}
function bindTaskForm(task, generation) {
  const form = document.getElementById("task-form");
  if (!form) return;
  window.TaskDateDefaults?.bind(form, {saved: true});
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = Object.fromEntries(new FormData(form));
    window.TaskDateDefaults?.payload(form, payload);
    payload.participant_ids = checkedValues(form, "participant_ids");
    payload.predecessor_ids = checkedValues(form, "predecessor_ids");
    payload.schedule_locked = !!form.querySelector('[name="schedule_locked"]')?.checked;
    const projectSelect = form.querySelector('[name="project_id"]');
    if (projectSelect) payload.project_id = retainArchivedCatalogSelection(
      projectSelect.value, task.project_id, state.projects, projectSelect.dataset.edited === "1",
    );
    payload.version = Number(payload.version);
    if (!payload.owner_id && payload.participant_ids.length) {
      form.querySelector("[name=owner_id]").setCustomValidity("选择参与成员后必须指定负责人");
      form.querySelector("[name=owner_id]").reportValidity();
      return;
    }
    const button = form.querySelector("button[type=submit]");
    button.disabled = true;
    const result = await api(`/api/task/${task.id}`, payload);
    if (!taskModalActive(form, generation, task.id)) return;
    button.disabled = false;
    if (result?.ok) {
      hideModal();
      await refresh();
    } else {
      form.querySelector("#task-form-message").textContent =
        (result?.httpStatus === 409 ? "任务已被他人更新，请关闭窗口刷新后重试：" : "") + (result?.error || "保存失败");
    }
  });
  form.querySelector("[name=owner_id]")?.addEventListener("change", (event) => event.target.setCustomValidity(""));
  form.querySelector('[name="project_id"]')?.addEventListener("change", (event) => { event.target.dataset.edited = "1"; });
}
function bindTaskActions(task, generation) {
  // Scope every action to the open modal: the task table renders its own
  // [data-delete-task] buttons before #modal in document order, so a
  // document-wide query binds the first table row instead of the modal button.
  const modal = document.getElementById("modal") || document;
  modal.querySelector("[data-close]")?.addEventListener("click", hideModal);
  bindTaskProgress(task, generation);
  modal.querySelector("[data-delete-task]")?.addEventListener("click", async (event) => {
    if (!confirm("确定删除该任务？存在子任务或依赖引用时将无法删除。")) return;
    const button = event.currentTarget,
      result = await api(`/api/task/${task.id}/delete`, { version: Number(task.version) });
    if (!taskModalActive(button, generation, task.id)) return;
    if (result?.ok) {
      hideModal();
      refresh();
    } else if (result?.httpStatus === 409) {
      alert(`任务已被他人更新，未删除。请关闭窗口并刷新后重试。${result.error ? `\n${result.error}` : ""}`);
      await refresh();
    } else alert(result?.error || "删除失败");
  });
  document.getElementById("task-revisions")?.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-revert]");
    if (!button || !confirm("撤销这次变更？系统会创建一条新的撤销版本，之后的字段冲突会阻止操作。")) return;
    button.disabled = true;
    const result = await api(`/api/task/${task.id}/revisions/${button.dataset.revert}/revert`, { version: Number(task.version || 0) });
    if (!taskModalActive(button, generation, task.id)) return;
    if (result?.ok) {
      hideModal();
      await refresh();
    } else {
      button.disabled = false;
      alert((result?.httpStatus === 409 ? "版本冲突，请刷新任务后重试。" : "") + (result?.error || "撤销失败"));
    }
  });
  document.getElementById("attachment-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget,
      files = form.elements.files.files,
      message = form.querySelector(".form-message");
    if (files.length > 5) {
      message.textContent = "每次最多上传 5 张图片";
      return;
    }
    const data = new FormData(form),
      button = form.querySelector("button");
    button.disabled = true;
    message.textContent = "正在上传…";
    const result = await api(`/api/task/${task.id}/attachments`, data);
    if (!taskModalActive(form, generation, task.id)) return;
    button.disabled = false;
    if (result?.ok) {
      form.reset();
      message.textContent = "上传成功";
        await Promise.all([loadAttachments(task, generation), loadLogs(task, generation)]);
    } else message.textContent = result?.error || "上传失败，请检查格式和大小";
  });
  document.getElementById("attachment-list")?.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-delete-attachment]");
    if (!button || !confirm("删除这张截图？")) return;
    button.disabled = true;
    const result = await api(`/api/task/${task.id}/attachments/${button.dataset.deleteAttachment}/delete`, {});
    if (!taskModalActive(button, generation, task.id)) return;
      if (result?.ok) await Promise.all([loadAttachments(task, generation), loadLogs(task, generation)]);
    else {
      button.disabled = false;
      alert(result?.error || "删除失败");
    }
  });
}
async function openTaskModal(id) {
  const task = state.tasks.find((item) => Number(item.id) === Number(id));
  if (!task) return;
  const generation = ++taskModalGeneration;
  if (showModal(taskModalShell(task), `任务：${task.title}`) === false) return;
  const modal = document.getElementById("modal");
  modal.classList.remove("task-preview");
  modal.dataset.taskId = String(task.id);
  modal.dataset.generation = String(generation);
  bindTaskForm(task, generation);
  bindTaskActions(task, generation);
  await Promise.all([loadTaskProgress(task, generation), loadAttachments(task, generation), loadRevisions(task, generation), loadLogs(task, generation)]);
}

/** Dashboard preview deliberately contains no task mutation actions or forms. */
function taskPreviewShell(task) {
  const percent = Math.max(0, Math.min(100, Number(task.progress) || 0));
  const people = taskParticipants(task)
    .map((id) => memberById(id)?.name || `成员 #${id}`).join("、") || "未分配";
  return `<div class="modal-head">
    <div><p class="ux-eyebrow">TASK #${Number(task.id)} / 只读预览</p><h2>${esc(task.title)}</h2></div>
    <button class="btn ghost sm" type="button" data-close>关闭</button>
  </div>
  <div class="modal-info">
    ${badge(task.status, STATUS_CLASS[task.status] || "gray")}
    ${badge(`${task.priority}优先`, PRIORITY_CLASS[task.priority] || "gray")}
    ${badge(daysLeftText(task), daysLeftClass(task))}
  </div>
  <div class="readonly-settings">
    <dl>
      <div><dt>造车组别</dt><dd>${esc(taskProjectName(task) || "未归属")}</dd></div>
      <div><dt>负责人及参与者</dt><dd>${esc(people)}</dd></div>
      <div><dt>开始日期</dt><dd>${esc(task.start_date || "未设置")}</dd></div>
      <div><dt>截止日期</dt><dd>${esc(task.due_date || "未设置")}</dd></div>
    </dl>
    ${task.detail ? `<div class="task-detail">${esc(task.detail)}</div>` : ""}
    ${(task.dependency_warnings || []).length ? `<p class="task-warning">${esc(task.dependency_warnings.join("；"))}</p>` : ""}
  </div>
  <section class="modal-section">
    <div class="task-progress-heading"><span>当前进度</span><strong>${percent}%</strong></div>
    <div class="progress" role="progressbar" aria-label="当前进度" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${percent}"><div class="progress-inner blue" style="width:${percent}%"></div></div>
  </section>
  <section class="modal-section"><h3>进度动态与截图</h3>
    <div id="task-progress-list" class="progress-timeline"><p class="empty">正在加载进度记录…</p></div>
  </section>
  <section class="modal-section"><h3>近期任务动态</h3>
    <div id="task-logs" class="logs"><p class="empty">正在加载动态…</p></div>
  </section>`;
}

async function openTaskPreview(id) {
  const task = state.tasks.find((item) => Number(item.id) === Number(id));
  if (!task) return;
  const generation = ++taskModalGeneration;
  if (showModal(taskPreviewShell(task), `任务状态：${task.title}`) === false) return;
  const modal = document.getElementById("modal");
  modal.classList.add("task-preview");
  modal.dataset.taskId = String(task.id);
  modal.dataset.generation = String(generation);
  modal.querySelector("[data-close]")?.addEventListener("click", hideModal);
  await Promise.all([loadTaskProgress(task, generation), loadLogs(task, generation)]);
}
