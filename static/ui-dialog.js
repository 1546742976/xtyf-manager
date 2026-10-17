"use strict";

let modalReturnFocus = null;
let modalOptions = {};

function showModal(html, label = "任务操作窗口", options = {}) {
  if (modalOptions.beforeClose?.() === false) return false;
  modalOptions.onClose?.();
  const modal = document.getElementById("modal");
  if (modalOptions.className) modal.classList.remove(modalOptions.className);
  modalOptions = options;
  if (options.className) modal.classList.add(options.className);
  modalReturnFocus = options.returnFocus || document.activeElement;
  modal.innerHTML = html;
  modal.setAttribute("aria-label", label);
  document.getElementById("modal-mask").classList.remove("hidden");
  document.body.classList.add("modal-open");
  modal.querySelector("button, input, select, textarea, a[href]")?.focus();
  return true;
}

function hideModal() {
  if (modalOptions.beforeClose?.() === false) return false;
  const closing = modalOptions, returnFocus = modalReturnFocus;
  closing.onClose?.();
  if (closing.className) document.getElementById("modal")?.classList.remove(closing.className);
  modalOptions = {};
  modalReturnFocus = null;
  document.getElementById("modal-mask")?.classList.add("hidden");
  document.getElementById("modal")?.replaceChildren();
  document.body.classList.remove("modal-open");
  (typeof returnFocus === "function" ? returnFocus() : returnFocus)?.focus?.({preventScroll: true});
  // A return view must be opened only after the old dialog has been removed.
  // showModal replacements run onClose for cleanup, but never this navigation hook.
  closing.afterClose?.();
  return true;
}

function modalKeydown(event) {
  const mask = document.getElementById("modal-mask");
  if (!mask || mask.classList.contains("hidden")) return;
  if (event.key === "Escape") {
    event.preventDefault();
    hideModal();
    return;
  }
  if (event.key !== "Tab") return;
  const focusable = [
    ...document
      .getElementById("modal")
      .querySelectorAll('button:not([disabled]),input:not([disabled]):not([type="hidden"]),select:not([disabled]),textarea:not([disabled]),a[href],[tabindex]:not([tabindex="-1"]):not([disabled])'),
  ].filter((element) => element.getClientRects().length);
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable.at(-1);
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

function bindPasswordButton() {
  document.getElementById("btn-password")?.addEventListener("click", () => {
    if (showModal(
      `<div class="modal-head"><h2>修改密码</h2><button class="btn ghost sm" type="button" data-close>关闭</button></div><form id="pw-form"><label>原密码<input name="old" type="password" required></label><label>新密码（至少 8 位）<input name="new" type="password" minlength="8" required></label><div class="modal-actions"><button class="btn primary">保存</button></div><p class="form-message" role="alert"></p></form>`,
      "修改密码",
    ) === false) return;
    document.querySelector("[data-close]").addEventListener("click", hideModal);
    document.getElementById("pw-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const result = await api("/api/password", Object.fromEntries(new FormData(form)));
      if (result?.ok) hideModal();
      else form.querySelector(".form-message").textContent = result?.error || "修改失败";
    });
  });
}

document.addEventListener("DOMContentLoaded", () => {
  const mask = document.getElementById("modal-mask");
  mask?.addEventListener("click", (event) => {
    if (event.target === mask) hideModal();
  });
  document.addEventListener("keydown", modalKeydown);
  bindPasswordButton();
});
