"use strict";

window.WorkspaceCountdown = (() => {
  const DAY = 86400000;
  function dateOrdinal(value) {
    if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
    const [year, month, day] = value.split("-").map(Number);
    if (year < 1) return null;
    const parsed = new Date(0);
    parsed.setUTCFullYear(year, month - 1, day);
    parsed.setUTCHours(0, 0, 0, 0);
    return parsed.toISOString().slice(0, 10) === value ? parsed.getTime() / DAY : null;
  }
  function beijingDate(now = new Date()) {
    return new Date(now.getTime() + 8 * 3600000).toISOString().slice(0, 10);
  }
  function countdownView(config, now = new Date()) {
    const target = dateOrdinal(config?.target_date);
    if (!config?.configured || !config.title || target === null) return null;
    const days = target - dateOrdinal(beijingDate(now));
    return {
      title: config.title,
      targetDate: config.target_date,
      days,
      number: days > 0 ? String(days) : days === 0 ? "今天" : "日期已过",
      unit: days > 0 ? "天" : "",
      heading: days > 0 ? `距离${config.title}还有` : config.title,
      description: days > 0 ? "目标倒计时" : days === 0 ? "今天是目标日期" : `目标日期已过 ${-days} 天`,
    };
  }
  function init() {
    const $ = id => document.getElementById(id);
    const banner = $("workspace-countdown");
    if (!banner) return;
    const status = $("countdown-result"), retry = $("countdown-retry");
    const dialog = $("countdown-dialog"), open = $("countdown-config");
    const form = $("countdown-form"), title = $("countdown-title-input"), target = $("countdown-date-input");
    const result = $("countdown-form-result"), save = $("countdown-save"), reload = $("countdown-reload");
    let config = null, loading = false, saving = false, blocked = false, returnFocus = null;

    function render() {
      const view = countdownView(config);
      banner.hidden = !view;
      if (!view) return;
      $("countdown-title").textContent = view.heading;
      $("countdown-eyebrow").textContent = view.description;
      $("countdown-date").textContent = `目标日期 · ${view.targetDate} · 北京时间`;
      $("countdown-value").textContent = view.number;
      $("countdown-unit").textContent = view.unit;
      $("countdown-number").classList.toggle("is-date-label", view.days <= 0);
    }
    function controls() {
      const busy = saving || loading;
      if (save) save.disabled = busy || blocked || !config;
      if (reload) reload.disabled = busy;
      if (title) title.disabled = busy;
      if (target) target.disabled = busy;
      if (form) form.setAttribute("aria-busy", String(busy));
      if (open) open.disabled = busy;
      retry.disabled = busy;
      $("countdown-cancel")?.toggleAttribute("disabled", saving);
    }
    function writeDraft() {
      if (!title || !target || !config) return;
      title.value = config.title;
      target.value = config.target_date;
    }
    function formMessage(message, error = false) {
      if (!result) return;
      result.textContent = message;
      result.classList.toggle("error", error);
    }
    async function request(options) {
      const response = await BusinessUI.fetch("/api/workspace/countdown", options);
      const data = await response.json();
      if (!response.ok || !data.ok) {
        const error = new Error(data.error || "目标设置请求失败，请重试");
        error.status = response.status;
        throw error;
      }
      const next = data.countdown;
      if (!next || !Number.isInteger(next.version) || typeof next.title !== "string"
          || typeof next.target_date !== "string" || typeof next.configured !== "boolean") {
        throw new Error("服务器响应格式异常，请加载最新设置核对");
      }
      return next;
    }
    async function load(replaceDraft = false) {
      if (loading || saving || (dialog?.open && !replaceDraft)) return;
      loading = true;
      status.textContent = "正在读取目标日期…";
      retry.hidden = true;
      if (replaceDraft) formMessage("正在加载最新设置…");
      controls();
      try {
        config = await request({credentials: "same-origin"});
        blocked = false;
        status.textContent = "";
        render();
        if (replaceDraft) {
          writeDraft();
          reload.hidden = true;
          formMessage("已加载最新设置，请确认后保存。");
        }
      } catch (error) {
        status.textContent = error.message || "目标日期读取失败，请重试";
        retry.hidden = false;
        if (replaceDraft) formMessage(`${status.textContent}，当前输入已保留。`, true);
      } finally {
        loading = false;
        controls();
      }
    }
    async function submit(event) {
      event.preventDefault();
      if (saving || loading || blocked || !config) return;
      saving = true;
      formMessage("正在保存…");
      controls();
      try {
        config = await request({method: "PUT", credentials: "same-origin",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({title: title.value.trim(), target_date: target.value, version: config.version})});
        blocked = false;
        reload.hidden = true;
        status.textContent = config.configured ? "目标倒计时已保存" : "目标倒计时已隐藏";
        retry.hidden = true;
        render();
        dialog.close();
      } catch (error) {
        if (error.status === 409) {
          blocked = true;
          reload.hidden = false;
          formMessage("目标已被其他管理员修改。当前输入已保留，请加载最新设置并重新确认后保存。", true);
        } else if (!error.status) {
          blocked = true;
          reload.hidden = false;
          formMessage(`${error.message} 当前输入已保留，请加载最新设置核对结果。`, true);
        } else {
          formMessage(`${error.message}，当前输入已保留。`, true);
        }
      } finally {
        saving = false;
        controls();
      }
    }
    open?.addEventListener("click", () => {
      returnFocus = document.activeElement;
      writeDraft();
      reload.hidden = Boolean(config) && !blocked;
      formMessage(blocked ? "请加载最新设置并重新确认后保存。" : config ? "同时清空名称和日期可隐藏横幅。" : "尚未读取目标设置，请加载最新设置。", blocked || !config);
      controls();
      dialog.showModal();
      document.body.classList.add("modal-open");
      title.focus();
    });
    form?.addEventListener("submit", submit);
    reload?.addEventListener("click", () => load(true));
    $("countdown-cancel")?.addEventListener("click", () => { if (!saving) dialog.close(); });
    dialog?.addEventListener("cancel", event => { if (saving) event.preventDefault(); });
    dialog?.addEventListener("close", () => {
      document.body.classList.remove("modal-open");
      returnFocus?.focus();
    });
    retry.addEventListener("click", () => load());
    document.addEventListener("visibilitychange", () => { if (!document.hidden) render(); });
    setInterval(() => { if (!document.hidden) render(); }, 60000);
    load();
  }
  document.addEventListener("DOMContentLoaded", init);
  return {dateOrdinal, beijingDate, countdownView, init};
})();
