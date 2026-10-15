"use strict";

function applyWorkspaceTheme(theme) {
  document.documentElement.dataset.theme = theme;
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = theme === "dark" ? "#0b0b0c" : "#f5f5f7";
  document.querySelectorAll('[name="workspace-theme"]').forEach((input) => {
    input.checked = input.value === theme;
  });
  document.querySelectorAll('[data-theme-toggle]').forEach((button) => {
    const label = theme === "dark" ? "切换为银白主题" : "切换为黑色主题";
    button.setAttribute("aria-label", label);
    button.title = label;
  });
}

async function saveThemePreference(theme) {
  const response = await BusinessUI.fetch("/api/account/preferences", {
    method: "POST", credentials: "same-origin",
    headers: {"Content-Type": "application/json"}, body: JSON.stringify({theme}),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || !data.ok || !["light", "dark"].includes(data.theme)) {
    throw new Error(data.error || "主题保存失败，请重试");
  }
  return data.theme;
}

document.addEventListener("DOMContentLoaded", () => {
  const choices = [...document.querySelectorAll('[name="workspace-theme"]')];
  const toggles = [...document.querySelectorAll('[data-theme-toggle]')];
  const sidebarResult = document.getElementById("sidebar-theme-result");
  const results = [document.getElementById("theme-result"), sidebarResult].filter(Boolean);
  const retries = [document.getElementById("theme-retry"), document.getElementById("sidebar-theme-retry")].filter(Boolean);
  const controls = [...choices, ...toggles];
  if (!controls.length) return;
  let pendingTheme = null;
  let saving = false;
  async function save(theme) {
    if (saving) return;
    saving = true;
    pendingTheme = theme;
    const previous = document.documentElement.dataset.theme;
    applyWorkspaceTheme(theme);
    const disabledBefore = controls.map((input) => input.disabled);
    controls.forEach((input) => { input.disabled = true; });
    retries.forEach((retry) => retry.classList.add("hidden"));
    if (sidebarResult) sidebarResult.classList.add("sr-only");
    results.forEach((result) => {
      result.classList.remove("error");
      result.textContent = "正在保存外观…";
    });
    try {
      applyWorkspaceTheme(await saveThemePreference(theme));
      results.forEach((result) => { result.textContent = "外观已保存，其他设备下次打开或刷新页面时同步。"; });
      pendingTheme = null;
    } catch (error) {
      applyWorkspaceTheme(previous);
      results.forEach((result) => {
        result.classList.add("error");
        result.textContent = `${error.message}，已恢复原主题。`;
      });
      if (sidebarResult) sidebarResult.classList.remove("sr-only");
      retries.forEach((retry) => retry.classList.remove("hidden"));
    } finally {
      saving = false;
      controls.forEach((input, index) => { input.disabled = disabledBefore[index]; });
    }
  }
  choices.forEach((input) => input.addEventListener("change", () => save(input.value)));
  toggles.forEach((button) => button.addEventListener("click", () => save(document.documentElement.dataset.theme === "dark" ? "light" : "dark")));
  retries.forEach((retry) => retry.addEventListener("click", () => { if (pendingTheme) save(pendingTheme); }));
});
