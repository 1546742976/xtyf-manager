"use strict";
(() => {
  const root = document.getElementById("github-hub");
  if (!root) return;
  const listBox = document.getElementById("github-topic-list");
  const detailBox = document.getElementById("github-topic-detail");
  const newButton = document.getElementById("github-new");
  const pages = document.querySelector(".github-pages");
  const safe = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[ch]);
  const safeLink = (value) => /^https:\/\/github\.com\/[A-Za-z0-9_.\/#?=&%-]+$/.test(String(value || "")) ? value : "";
  const sourceNote = document.getElementById("github-source-note");
  const state = { kind: "issues", page: 1, number: null, generation: 0 };

  async function request(url) {
    try {
      const res = await BusinessUI.fetch(url, { credentials: "same-origin" });
      const payload = await res.json().catch(() => ({ ok: false, error: "响应格式不正确" }));
      return payload;
    } catch (error) {
      return { ok: false, error: error.message || "网络请求失败，请稍后重试" };
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
    newButton.href = state.kind === "issues" ? root.dataset.newIssue : root.dataset.newDiscussion;
    newButton.textContent = state.kind === "issues" ? "去 GitHub 创建 Issue ↗" : "去 GitHub 发起讨论 ↗";
    pages.hidden = state.kind !== "issues";
    document.getElementById("github-prev").disabled = state.page <= 1;
  }
  let retryAt = 0;
  function externalList() {
    const base = safeLink(root.dataset.repository);
    return base ? `${base}/${state.kind}` : '';
  }
  function failureView(message, detail = false) {
    const url = externalList();
    return `<p class="form-message" role="alert">${safe(message)}</p><div class="github-error-actions"><button class="btn" type="button" ${detail ? 'data-github-retry-detail' : 'data-github-retry'}>重新加载</button>${url ? `<a class="btn primary" href="${safe(url)}" target="_blank" rel="noopener noreferrer">前往 GitHub 查看 ↗</a>` : ''}</div>`;
  }
  async function loadItems() {
    const generation = ++state.generation;
    state.number = null;
    updateTab();
    root.classList?.toggle('is-unavailable', false);
    document.getElementById("github-next").disabled = true;
    sourceNote.textContent = "";
    listBox.innerHTML = '<p class="empty">正在读取 GitHub 主题…</p>';
    detailBox.innerHTML = '<p class="empty">选择主题查看正文与回复。</p>';
    const data = await request(`/discussions/api/items?kind=${state.kind}&page=${state.page}`);
    if (generation !== state.generation) return;
    if (!data?.ok) {
      retryAt = Date.now() + (Number(data?.retry_after) || 0) * 1000;
      root.classList?.toggle('is-unavailable', true);
      pages.hidden = true;
      listBox.innerHTML = failureView(data?.error || "加载失败");
      if (retryAt > Date.now()) sourceNote.textContent = `预计 ${new Date(retryAt).toLocaleTimeString('zh-CN', {hour:'2-digit', minute:'2-digit'})} 后可重试。`;
      return;
    }
    sourceNote.textContent = state.kind === "discussions" && data.source === "atom"
      ? "已同步公开 Discussions 订阅源的近期主题；订阅源不包含完整回复，查看与参与请前往 GitHub。"
      : "内容来自 GitHub，所有创建及回复操作均在 GitHub 完成。";
    const items = data.items || [];
    listBox.innerHTML = items.map((item) => `<button type="button" class="github-topic" data-github-number="${Number(item.number)}">
      <strong>#${Number(item.number)} · ${safe(item.title)}</strong>
      <small>${meta(item)}${item.category ? " · " + safe(item.category) : ""} ${item.comments_count == null ? "" : " · 回复 " + Number(item.comments_count)}</small>
    </button>`).join("") || '<p class="empty">此板块暂无主题。</p>';
    document.getElementById("github-next").disabled = state.kind !== "issues" || !(data.has_next ?? items.length >= 30);
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
      detailBox.innerHTML = failureView(data?.error || "加载详情失败", true);
      return;
    }
    const item = data.item;
    const external = safeLink(item.url);
    detailBox.innerHTML = `<article class="github-thread">
      <h2>${safe(item.title)}</h2><p class="github-topic-meta">#${Number(item.number)} · ${meta(item)}</p>
      <div class="github-body">${safe(item.body || "（无正文）")}</div>
      ${external ? `<a href="${safe(external)}" target="_blank" rel="noopener noreferrer" class="btn primary github-external">去 GitHub 查看、评论或回复 ↗</a>` : ""}
      <h3>回复</h3>
      <div class="github-replies">${data.comments_available === false
        ? '<p class="empty">公开订阅源暂不提供回复内容，请前往 GitHub 阅读完整讨论。</p>'
        : (data.comments || []).map((comment) => `<div class="github-reply"><strong>${safe(comment.author)}</strong><small>${safe((comment.created_at || "").replace("T", " ").slice(0, 16))}</small><p>${safe(comment.body)}</p></div>`).join("") || '<p class="empty">暂无回复</p>'}</div>
    </article>`;
  }
  root.addEventListener("click", (event) => {
    if (event.target.closest('[data-github-retry]')) { if (Date.now() < retryAt) { sourceNote.textContent = 'GitHub 额度尚未恢复，请稍后重试或使用直达入口。'; return; } loadItems(); return; }
    if (event.target.closest('[data-github-retry-detail]')) { loadDetail(state.number); return; }
    const tab = event.target.closest("[data-github-kind]");
    if (tab) { state.kind = tab.dataset.githubKind; state.page = 1; loadItems(); return; }
    const item = event.target.closest("[data-github-number]");
    if (item) { loadDetail(Number(item.dataset.githubNumber)); return; }
  });
  document.getElementById("github-prev").addEventListener("click", () => {
    if (state.page > 1) { state.page -= 1; loadItems(); }
  });
  document.getElementById("github-next").addEventListener("click", () => {
    if (state.page < 20) { state.page += 1; loadItems(); }
  });
  loadItems();
})();
