"use strict";

// Loaded before page scripts on every shell, including the standalone finance pages.
(() => {
  let token = document.querySelector('meta[name="csrf-token"]')?.content || "";
  const originalFetch = window.fetch.bind(window);
  let pageInactive = false;
  const activeReads = new Set();
  window.addEventListener("pagehide", () => {
    pageInactive = true;
    for (const controller of activeReads) controller.abort();
    activeReads.clear();
  });
  window.addEventListener("pageshow", () => { pageInactive = false; });
  window.fetch = async (input, options = {}) => {
    const url = new URL(input instanceof Request ? input.url : input, location.href);
    const method = String(options.method || (input instanceof Request ? input.method : "GET")).toUpperCase();
    const sameOrigin = url.origin === location.origin;
    // Late continuations (e.g. permissions -> audit) must not start a fresh read
    // from an inactive document. Keep write/recovery and CSRF behavior intact.
    if (sameOrigin && pageInactive && ["GET", "HEAD"].includes(method)) {
      throw new DOMException("页面已离开，取消后续读取", "AbortError");
    }
    if (sameOrigin && !["GET", "HEAD", "OPTIONS"].includes(method)) {
      const headers = new Headers(options.headers || (input instanceof Request ? input.headers : undefined));
      headers.set("X-CSRF-Token", token);
      options = { ...options, headers };
    }
    let controller, callerSignal, cancel;
    if (sameOrigin && ["GET", "HEAD"].includes(method)) {
      controller = new AbortController();
      callerSignal = options.signal || (input instanceof Request ? input.signal : null);
      cancel = () => controller.abort();
      if (callerSignal?.aborted) cancel();
      else callerSignal?.addEventListener("abort", cancel, {once: true});
      activeReads.add(controller);
      options = {...options, signal: controller.signal};
    }
    let response;
    try { response = await originalFetch(input, options); }
    finally {
      if (controller) activeReads.delete(controller);
      callerSignal?.removeEventListener("abort", cancel);
    }
    if (sameOrigin) {
      const renewed = response.headers.get("X-CSRF-Token");
      if (renewed) {
        token = renewed;
        const meta = document.querySelector('meta[name="csrf-token"]');
        if (meta) meta.content = token;
        document.querySelectorAll('input[name="csrf_token"]').forEach((field) => { field.value = token; });
      }
      if (response.status === 403) {
        const data = await response.clone().json().catch(() => ({}));
        if (data.reason === "password_change_required" && location.pathname !== "/account") location.assign("/account");
      }
    }
    return response;
  };
})();
