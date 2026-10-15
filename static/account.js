"use strict";

async function accountApi(path, data) {
  const init = { credentials: "same-origin" };
  if (data !== undefined) {
    init.method = "POST";
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(data);
  }
  const response = await BusinessUI.fetch(path, init);
  if (response.status === 401) {
    throw new Error("登录已失效，请重新登录后再提交；当前输入已保留。");
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || !payload.ok) throw new Error(payload.error || "请求失败");
  return payload;
}

function accountEscape(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]
  ));
}

function accountChoices(host, name, choices, selected) {
  const selectedIds = new Set((selected || []).map(Number));
  const active = choices.filter((item) => item.is_active !== 0);
  const historical = choices.filter((item) => item.is_active === 0 && selectedIds.has(Number(item.id)))
    .map((item) => `<input type="checkbox" name="${name}" value="${Number(item.id)}" checked hidden>`).join("");
  host.innerHTML = (active.map((item) =>
    `<label class="choice-item"><input type="checkbox" name="${name}" value="${Number(item.id)}" ${selectedIds.has(Number(item.id)) ? "checked" : ""}><span>${accountEscape(item.name)}</span></label>`
  ).join("") || '<span class="muted">暂无可选组别</span>') + historical;
}

function accountSelected(form, name) {
  return [...form.querySelectorAll(`[name="${name}"]:checked`)].map((input) => Number(input.value));
}

document.addEventListener("DOMContentLoaded", () => {
  const profileForm = document.getElementById("account-profile-form");
  const profileResult = document.getElementById("account-profile-result");
  let groups = [];
  let projects = [];
  let profile = null;
  if (profileForm) profileForm.querySelector('[type="submit"]').disabled = true;

  function showProfile() {
    if (!profile) return;
    profileForm.elements.username.value = profile.username;
    accountChoices(document.getElementById("account-group-choices"), "group_ids", groups, profile.group_ids);
    accountChoices(document.getElementById("account-project-choices"), "project_ids", projects, profile.project_ids);
    document.getElementById("account-id").textContent = profile.id;
    document.getElementById("account-name").textContent = profile.name;
    document.getElementById("account-login-name").textContent = profile.username;
    const names = (items, ids) => items.filter((item) => (ids || []).map(Number).includes(Number(item.id)))
      .map((item) => item.name).join("、") || "未分组";
    document.getElementById("account-group-names").textContent = names(groups, profile.group_ids);
    document.getElementById("account-project-names").textContent = names(projects, profile.project_ids);
  }

  function loadProfile() { return Promise.all([
    accountApi("/api/account/profile"),
    accountApi("/api/groups"),
    accountApi("/api/projects"),
  ]).then(([myProfile, groupData, projectData]) => {
    if (!myProfile || !groupData || !projectData) return;
    profile = myProfile.profile;
    groups = groupData.groups || [];
    projects = projectData.projects || [];
    showProfile();
    profileForm.querySelector('[type="submit"]').disabled = false;
    document.getElementById('profile-retry')?.remove();
  }).catch((error) => {
    profileResult.classList.add("error");
    profileResult.textContent = error.message || "个人资料加载失败";
    if (!document.getElementById('profile-retry')) {
      const retry = document.createElement('button');
      retry.id = 'profile-retry'; retry.type = 'button'; retry.className = 'btn'; retry.textContent = '重新加载资料';
      retry.addEventListener('click', async () => { retry.disabled=true; try { await loadProfile(); } finally { retry.disabled=false; } });
      profileResult.after(retry);
    }
  });
  }
  if(profileForm)loadProfile();

  profileForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = profileForm.querySelector('[type="submit"]');
    if (button.disabled) return;
    button.disabled = true;
    profileResult.textContent = "正在保存…";
    profileResult.classList.remove("error");
    try {
      const result = await accountApi("/api/account/profile", {
        username: profileForm.elements.username.value,
        group_ids: accountSelected(profileForm, "group_ids"),
        project_ids: accountSelected(profileForm, "project_ids"),
      });
      if (!result) return;
      profile = result.profile;
      showProfile();
      profileResult.textContent = "个人资料已更新，现有任务和成员 ID 不变";
    } catch (error) {
      profileResult.classList.add("error");
      profileResult.textContent = error.message || "保存失败";
    } finally {
      button.disabled = false;
    }
  });

  const form = document.getElementById("account-password-form");
  const result = document.getElementById("account-result");
  form?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const values = new FormData(form);
    if (values.get("new") !== values.get("confirm")) {
      result.textContent = "两次输入的新密码不一致";
      result.classList.add("error");
      return;
    }
    const button = form.querySelector('[type="submit"]');
    if (button.disabled) return;
    button.disabled = true;
    result.textContent = "正在保存…";
    result.classList.remove("error");
    try {
      const saved = await accountApi("/api/password", { old: values.get("old"), new: values.get("new") });
      if (!saved) return;
      form.reset();
      result.textContent = "密码已更新，请妥善保存新密码";
      if (form.dataset.requiredChange === "1") location.assign("/");
    } catch (error) {
      result.classList.add("error");
      result.textContent = error.message || "网络异常，请重试";
    } finally {
      button.disabled = false;
    }
  });
});
