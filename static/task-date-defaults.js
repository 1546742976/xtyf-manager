"use strict";

// A suggestion is copied into the draft once. Saved dates are never live links.
(function (root) {
  function minusTwoDays(value) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value || "")) return "";
    const date = new Date(`${value}T12:00:00Z`);
    if (!Number.isFinite(date.getTime()) || date.toISOString().slice(0, 10) !== value) return "";
    date.setUTCDate(date.getUTCDate() - 2);
    return date.getUTCFullYear() >= 1 ? date.toISOString().slice(0, 10) : "";
  }
  function bind(form, config = {}) {
    if (!form || form._taskDates) return;
    const start = form.querySelector('[name="start_date"]');
    const parent = form.querySelector('[name="parent_id"]');
    if (!start || !parent) return;
    const note = form.querySelector("[data-task-date-note]");
    const reset = form.querySelector("[data-task-date-reset]");
    const saved = !!config.saved;
    const getTasks = config.getTasks || (() => state.tasks);
    const draft = {customized: false, reset: false, suggested: false};
    const suggestion = () => minusTwoDays(getTasks().find((task) => String(task.id) === parent.value)?.due_date);
    function refresh() {
      const date = suggestion();
      if (reset) reset.disabled = !date;
      if (!note) return;
      const explanation = date ? `主前置任务截止前 2 天：${date}。` : "选择有截止日期的主前置任务后可使用建议日期。";
      const mode = draft.customized ? "时间已自定义。" : saved ? "日期已锁定，不随前置任务变更。" : "新建时默认参考主前置任务，首次保存后固定。";
      note.textContent = `${draft.reset && saved ? "已采用本次依赖日期，保存后固定。" : mode}${explanation}`;
    }
    function applyDefault() {
      if (!saved && !draft.customized) {
        const date = suggestion();
        if (date || draft.suggested) start.value = date;
        draft.suggested = !!date;
      }
      refresh();
    }
    const customize = () => {
      draft.customized = true;
      draft.reset = false;
      draft.suggested = false;
      refresh();
    };
    start.addEventListener("input", customize);
    start.addEventListener("change", customize);
    parent.addEventListener("change", () => {
      if (saved && draft.reset) {
        draft.reset = false;
        draft.customized = true;
      }
      applyDefault();
    });
    reset?.addEventListener("click", () => {
      const date = suggestion();
      if (!date) return;
      start.value = date;
      draft.customized = false;
      draft.reset = true;
      draft.suggested = true;
      refresh();
    });
    form.addEventListener("reset", () => {
      draft.customized = false;
      draft.reset = false;
      draft.suggested = false;
      // Native form reset runs after its event listeners.
      queueMicrotask(refresh);
    });
    form._taskDates = {draft, saved, refresh};
    refresh();
  }
  function payload(form, data) {
    const context = form?._taskDates;
    if (!context) return data;
    if (context.draft.reset || (!context.saved && context.draft.suggested && !context.draft.customized)) data.reset_start_date = true;
    else if (context.draft.customized) data.start_date_origin = "manual";
    return data;
  }
  root.TaskDateDefaults = {bind, payload, minusTwoDays};
})(typeof window !== "undefined" ? window : globalThis);
