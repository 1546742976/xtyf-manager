"use strict";

// Loaded before page scripts on every shell, including the standalone finance pages.
(() => {
  let token = document.querySelector('meta[name="csrf-token"]')?.content || "";
  const originalFetch = window.fetch.bind(window);
  window.fetch = async (input, options = {}) => {
    const url = new URL(input instanceof Request ? input.url : input, location.href);
    const method = String(options.method || (input instanceof Request ? input.method : "GET")).toUpperCase();
    const sameOrigin = url.origin === location.origin;
    if (sameOrigin && !["GET", "HEAD", "OPTIONS"].includes(method)) {
      const headers = new Headers(options.headers || (input instanceof Request ? input.headers : undefined));
      headers.set("X-CSRF-Token", token);
      options = { ...options, headers };
    }
    const response = await originalFetch(input, options);
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
