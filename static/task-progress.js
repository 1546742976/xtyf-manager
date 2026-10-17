"use strict";

function progressSection(task) {
  const percent = Math.max(0, Math.min(100, Number(task.progress) || 0));
  return `<section class="modal-section task-progress-section">
    <h3>任务进度 · ${percent}%</h3>
    <div class="progress" role="progressbar" aria-label="当前进度" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${percent}"><div class="progress-inner blue" style="width:${percent}%"></div></div>
    ${canEdit(task) ? `<form id="progress-form" class="task-progress-form" enctype="multipart/form-data">
      <input name="version" type="hidden" value="${Number(task.version)}">
      <div class="progress-edit-row"><label>手动设置进度 <input type="range" name="progress_slider" min="0" max="100" step="1" value="${percent}"></label><label>百分比 <input type="number" name="progress" min="0" max="100" step="1" value="${percent}" required></label></div>
      <label>进度备注（完成了什么工作）<textarea name="note" rows="3" maxlength="2000" placeholder="例如：完成电机驱动联调、通过实车测试…"></textarea></label>
      <label>附图（可选，最多 5 张）<input type="file" name="files" accept="image/png,image/jpeg,image/webp" multiple></label>
      <div class="progress-submit"><button type="submit" class="btn primary">提交进度记录</button><p class="form-message" role="alert"></p></div>
    </form>` : '<p class="muted">仅负责人、参与成员或管理员可更新进度。</p>'}
    <div id="task-progress-list" class="progress-timeline"><p class="empty">正在加载进度记录…</p></div>
  </section>`;
}

function bindTaskProgress(task, generation, navigation = {}) {
  const form = document.getElementById("progress-form");
  if (!form) return;
  const slider = form.elements.progress_slider, number = form.elements.progress;
  slider.addEventListener("input", () => { number.value = slider.value; });
  number.addEventListener("input", () => {
    if (number.value !== "" && Number(number.value) >= 0 && Number(number.value) <= 100) slider.value = number.value;
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const files = form.elements.files.files, message = form.querySelector(".form-message");
    if (files.length > 5) { message.textContent = "一次最多 5 张图片"; return; }
    const data = new FormData(form);
    data.delete("progress_slider");
    // No selected screenshots: don't submit the browser's empty file placeholder.
    if (!files.length) data.delete("files");
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    message.textContent = "正在保存进度…";
    const result = await api(`/api/task/${Number(task.id)}/progress`, data);
    if (!taskModalActive(form, generation, task.id)) return;
    button.disabled = false;
    if (result?.ok) {
      await finishTaskModalUpdate(form, generation, task.id, navigation);
    } else {
      message.textContent = (result?.httpStatus === 409 ? "版本冲突，请关闭任务并刷新：" : "") + (result?.error || "保存失败");
    }
  });
}

async function loadTaskProgress(task, generation) {
  const box = document.getElementById("task-progress-list");
  if (!box) return;
  const result = await api(`/api/task/${Number(task.id)}/progress`);
  if (!taskModalActive(box, generation, task.id)) return;
  if (!result?.ok) {
    box.innerHTML = `<p class="form-message">${esc(result?.error || "加载进度记录失败")}</p>`;
    return;
  }
  box.innerHTML = (result.updates || []).map((entry) => `<article class="progress-entry">
    <div class="progress-entry-head"><strong>${Number(entry.progress)}% · ${esc(entry.actor_name)}</strong><time>${esc(entry.created_at)}</time></div>
    ${entry.note ? `<p class="progress-entry-note">${esc(entry.note)}</p>` : ""}
    ${(entry.images || []).length ? `<div class="progress-entry-images">${entry.images.map((file) => `<a href="${esc(file.url)}" target="_blank" rel="noopener noreferrer" aria-label="查看进度图片"><img src="${esc(file.url)}" alt="${esc(file.name)}" loading="lazy"></a>`).join("")}</div>` : ""}
  </article>`).join("") || '<p class="empty">还没有进度记录，可以提交第一条。</p>';
}
