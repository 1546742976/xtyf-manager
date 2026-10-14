"use strict";

function applyWorkspaceTheme(theme) {
  document.documentElement.dataset.theme = theme;
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = theme === "dark" ? "#0b0b0c" : "#f5f5f7";
  document.querySelectorAll('[name="workspace-theme"]').forEach((input) => {
    input.checked = input.value === theme;
  });
}

async function saveThemePreference(theme) {
  const response = await fetch("/api/account/preferences", {
    method: "POST", credentials: "same-origin",
    headers: {"Content-Type": "application/json"}, body: JSON.stringify({theme}),
  });
  const data = await response.json().catch(() => ({}));
  if (response.status === 401) location.assign("/login");
  if (!response.ok || !data.ok || !["light", "dark"].includes(data.theme)) {
    throw new Error(data.error || "主题保存失败，请重试");
  }
  return data.theme;
}

document.addEventListener("DOMContentLoaded", () => {
  const choices = [...document.querySelectorAll('[name="workspace-theme"]')];
  const result = document.getElementById("theme-result");
  const retry = document.getElementById("theme-retry");
  if (!result || !retry || !choices.length) return;
  let pendingTheme = null;
  let saving = false;
  async function save(theme) {
    if (saving) return;
    saving = true;
    pendingTheme = theme;
    const previous = document.documentElement.dataset.theme;
    applyWorkspaceTheme(theme);
    choices.forEach((input) => { input.disabled = true; });
    retry.classList.add("hidden");
    result.classList.remove("error");
    result.textContent = "正在保存外观…";
    try {
      applyWorkspaceTheme(await saveThemePreference(theme));
      result.textContent = "外观已保存，其他设备下次打开或刷新页面时同步。";
      pendingTheme = null;
    } catch (error) {
      applyWorkspaceTheme(previous);
      result.classList.add("error");
      result.textContent = `${error.message}，已恢复原主题。`;
      retry.classList.remove("hidden");
    } finally {
      saving = false;
      choices.forEach((input) => { input.disabled = false; });
    }
  }
  choices.forEach((input) => input.addEventListener("change", () => save(input.value)));
  retry.addEventListener("click", () => { if (pendingTheme) save(pendingTheme); });
});
