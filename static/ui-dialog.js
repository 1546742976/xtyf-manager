"use strict";

let modalReturnFocus = null;
let modalLifecycle = null;
let modalBaseClass = null;

/** One shared lifecycle for buttons, Escape, backdrop clicks and replacement. */
function showModal(html, label = "任务操作窗口", options = {}) {
  const modal = document.getElementById("modal");
  const mask = document.getElementById("modal-mask");
  if (!modal || !mask) return false;
  if (!mask.classList.contains("hidden")) {
    // Replacing a pending save is as dangerous as closing it.
    if (modalLifecycle?.beforeClose?.() === false) return false;
    const previous = modalLifecycle;
    modalLifecycle = null;
    previous?.onClose?.();
  }
  if (modalBaseClass === null) modalBaseClass = modal.className;
  modal.className = modalBaseClass;
  if (options.className) {
    for (const name of String(options.className).split(/\s+/).filter(Boolean)) modal.classList.add(name);
  }
  modalReturnFocus = document.activeElement;
  modalLifecycle = options;
  modal.innerHTML = html;
  modal.setAttribute("aria-label", label);
  mask.classList.remove("hidden");
  document.body.classList.add("modal-open");
  modal.querySelector("button, input, select, textarea, a[href]")?.focus();
  return true;
}

function hideModal() {
  const mask = document.getElementById("modal-mask");
  const modal = document.getElementById("modal");
  if (!mask || !modal || mask.classList.contains("hidden")) return false;
  if (modalLifecycle?.beforeClose?.() === false) return false;
  const lifecycle = modalLifecycle || {};
  const returnFocus = lifecycle.returnFocus ?? modalReturnFocus;
  modalLifecycle = null;
  mask.classList.add("hidden");
  modal.replaceChildren();
  if (modalBaseClass !== null) modal.className = modalBaseClass;
  document.body.classList.remove("modal-open");
  lifecycle.onClose?.();
  if (typeof lifecycle.afterClose === "function") {
    // A nested task dialog can recreate its parent list and manage focus itself.
    lifecycle.afterClose();
  } else {
    const target = typeof returnFocus === "function" ? returnFocus() : returnFocus;
    target?.focus?.();
  }
  return true;
}

function modalKeydown(event) {
  const mask = document.getElementById("modal-mask");
  if (!mask || mask.classList.contains("hidden")) return;
  if (event.key === "Escape") {
    if (hideModal()) event.preventDefault();
    return;
  }
  if (event.key !== "Tab") return;
  const focusable = [
    ...document
      .getElementById("modal")
      .querySelectorAll("button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),a[href]"),
  ];
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
    showModal(
      `<div class="modal-head"><h2>修改密码</h2><button class="btn ghost sm" type="button" data-close>关闭</button></div><form id="pw-form"><label>原密码<input name="old" type="password" required></label><label>新密码（至少 8 位）<input name="new" type="password" minlength="8" required></label><div class="modal-actions"><button class="btn primary">保存</button></div><p class="form-message" role="alert"></p></form>`,
      "修改密码",
    );
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
