"use strict";
(() => {
  const root = document.getElementById("github-hub");
  if (!root) return;
  const listBox = document.getElementById("github-topic-list");
  const detailBox = document.getElementById("github-topic-detail");
  const compose = document.getElementById("github-compose");
  const newButton = document.getElementById("github-new");
  const pages = document.querySelector(".github-pages");
  const safe = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[ch]);
  const safeLink = (value) => /^https:\/\/github\.com\/[A-Za-z0-9_.\/#?=&%-]+$/.test(String(value || "")) ? value : "";
  const state = { kind: "issues", page: 1, number: null, categories: [], canWrite: root.dataset.canWrite === "true", generation: 0 };

  async function request(url, data) {
    try {
      const res = await fetch(url, {
        credentials: "same-origin", method: data === undefined ? "GET" : "POST",
        ...(data === undefined ? {} : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) }),
      });
      const payload = await res.json().catch(() => ({ ok: false, error: "响应格式不正确" }));
      if (res.status === 401) { location.assign("/login"); return null; }
      return payload;
    } catch (_) {
      return { ok: false, error: "网络请求失败，请稍后重试" };
    }
  }
  function meta(item) {
    return safe(item.author || "未知") + " · " + safe((item.updated_at || "").replace("T", " ").slice(0, 16));
  }
  function updateTab() {
    document.querySelectorAll("[data-github-kind]").forEach((button) => {
      const chosen = button.dataset.githubKind === state.kind;
      button.classList.toggle("active", chosen);
      button.setAttribute("aria-selected", String(chosen));
    });
    document.getElementById("github-page").textContent = `第 ${state.page} 页`;
    pages.hidden = state.kind !== "issues";
    document.getElementById("github-prev").disabled = state.page <= 1;
  }
  function renderCategories() {
    return state.kind === "discussions"
      ? `<label>讨论分类<select name="category_id" required>${state.categories.map((cat) => `<option value="${safe(cat.id)}">${safe(cat.name)}</option>`).join("")}</select></label>`
      : "";
  }
  function showComposer() {
    if (!state.canWrite) return;
    compose.hidden = false;
    compose.innerHTML = `<form id="github-create-form" class="github-composer">
      <h3>新建${state.kind === "issues" ? " Issue" : " Discussion"}</h3>
      <label>标题<input name="title" maxlength="180" required></label>
      ${renderCategories()}
      <label>正文<textarea name="body" maxlength="8000" rows="5" required></textarea></label>
      <div class="github-form-actions"><button type="submit" class="btn primary">发布到 GitHub</button><button type="button" class="btn" data-github-cancel>取消</button><span class="form-message" role="alert"></span></div>
    </form>`;
    compose.querySelector("input")?.focus();
  }
  async function loadItems() {
    const generation = ++state.generation;
    state.number = null;
    compose.hidden = true;
    compose.replaceChildren();
    updateTab();
    listBox.innerHTML = '<p class="empty">正在读取 GitHub 主题…</p>';
    detailBox.innerHTML = '<p class="empty">选择主题查看正文与回复。</p>';
    const data = await request(`/discussions/api/items?kind=${state.kind}&page=${state.page}`);
    if (generation !== state.generation) return;
    if (!data?.ok) {
      listBox.innerHTML = `<p class="form-message" role="alert">${safe(data?.error || "加载失败")}</p>`;
      return;
    }
    state.categories = data.categories || [];
    state.canWrite = !!data.can_write;
    newButton.disabled = !state.canWrite;
    const items = data.items || [];
    listBox.innerHTML = items.map((item) => `<button type="button" class="github-topic" data-github-number="${Number(item.number)}">
      <strong>#${Number(item.number)} · ${safe(item.title)}</strong>
      <small>${meta(item)}${item.category ? " · " + safe(item.category) : ""} · 回复 ${Number(item.comments_count || 0)}</small>
    </button>`).join("") || '<p class="empty">此板块暂无主题。</p>';
    document.getElementById("github-next").disabled = state.kind !== "issues" || items.length < 30;
  }
  async function loadDetail(number) {
    state.number = number;
    const generation = ++state.generation;
    document.querySelectorAll("[data-github-number]").forEach((button) => {
      button.classList.toggle("active", Number(button.dataset.githubNumber) === number);
    });
    detailBox.innerHTML = '<p class="empty">正在读取正文与回复…</p>';
    const data = await request(`/discussions/api/item/${state.kind}/${number}`);
    if (generation !== state.generation) return;
    if (!data?.ok) {
      detailBox.innerHTML = `<p class="form-message" role="alert">${safe(data?.error || "加载详情失败")}</p>`;
      return;
    }
    const item = data.item;
    const external = safeLink(item.url);
    detailBox.innerHTML = `<article class="github-thread">
      <h2>${safe(item.title)}</h2><p class="github-topic-meta">#${Number(item.number)} · ${meta(item)}</p>
      <div class="github-body">${safe(item.body || "（无正文）")}</div>
      ${external ? `<a href="${safe(external)}" target="_blank" rel="noopener noreferrer" class="github-external">在 GitHub 中查看原帖</a>` : ""}
      <h3>回复</h3>
      <div class="github-replies">${(data.comments || []).map((comment) => `<div class="github-reply"><strong>${safe(comment.author)}</strong><small>${safe(comment.created_at.replace("T", " ").slice(0, 16))}</small><p>${safe(comment.body)}</p></div>`).join("") || '<p class="empty">暂无回复</p>'}</div>
      ${state.canWrite ? `<form id="github-reply-form" class="github-composer"><label>添加回复<textarea name="body" maxlength="8000" rows="3" required></textarea></label><div class="github-form-actions"><button class="btn primary" type="submit">回复</button><span class="form-message" role="alert"></span></div></form>` : ""}
    </article>`;
  }
  async function submitForm(form, action) {
    const button = form.querySelector('[type="submit"]');
    const message = form.querySelector(".form-message");
    const data = Object.fromEntries(new FormData(form));
    data.kind = state.kind;
    data.action = action;
    if (action === "comment") data.number = state.number;
    button.disabled = true;
    message.textContent = "正在提交…";
    const result = await request("/discussions/api/post", data);
    if (!form.isConnected) return;
    button.disabled = false;
    if (!result?.ok) { message.textContent = result?.error || "提交失败"; return; }
    if (action === "create") {
      await loadItems();
      // Open the newly created issue/discussion even if it isn't among the first page.
      await loadDetail(Number(result.number));
    } else {
      await loadDetail(state.number);
    }
  }

  root.addEventListener("click", (event) => {
    const tab = event.target.closest("[data-github-kind]");
    if (tab) { state.kind = tab.dataset.githubKind; state.page = 1; loadItems(); return; }
    const item = event.target.closest("[data-github-number]");
    if (item) { loadDetail(Number(item.dataset.githubNumber)); return; }
    if (event.target.closest("[data-github-cancel]")) { compose.hidden = true; compose.replaceChildren(); }
  });
  newButton.addEventListener("click", showComposer);
  compose.addEventListener("submit", (event) => {
    if (event.target.id !== "github-create-form") return;
    event.preventDefault();
    submitForm(event.target, "create");
  });
  detailBox.addEventListener("submit", (event) => {
    if (event.target.id !== "github-reply-form") return;
    event.preventDefault();
    submitForm(event.target, "comment");
  });
  document.getElementById("github-prev").addEventListener("click", () => {
    if (state.page > 1) { state.page -= 1; loadItems(); }
  });
  document.getElementById("github-next").addEventListener("click", () => {
    if (state.page < 20) { state.page += 1; loadItems(); }
  });
  loadItems();
})();
