"use strict";

// The native client keeps its existing initial avatar until its image transport is supported.
(() => {
  const isNative = () => typeof window.XTYF?.invoke === "function";
  function safeUrl(value) {
    if (typeof value !== "string" || !value) return null;
    try {
      const parsed = new URL(value, location.href);
      return parsed.origin === location.origin && parsed.pathname === "/api/account/avatar"
        ? parsed.pathname + parsed.search : null;
    } catch (_) { return null; }
  }
  function render(host, value) {
    const url = isNative() ? null : safeUrl(value);
    host.dataset.avatarUrl = url || "";
    host.querySelector("[data-avatar-image]")?.remove();
    const initial = host.querySelector("[data-avatar-initial]");
    initial.hidden = false;
    if (!url) return;
    const image = new Image();
    image.alt = "";
    image.dataset.avatarImage = "";
    image.hidden = true;
    image.addEventListener("load", () => {
      if (!host.contains(image)) return;
      initial.hidden = true;
      image.hidden = false;
    });
    image.addEventListener("error", () => {
      if (!host.contains(image)) return;
      initial.hidden = false;
      image.remove();
    });
    host.append(image);
    image.src = url;
  }
  function setUrl(value) {
    document.querySelectorAll("[data-account-avatar]").forEach(host => render(host, value));
  }
  window.AvatarDisplay = {setUrl, isNative, safeUrl};
  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-account-avatar]").forEach(host => render(host, host.dataset.avatarUrl));
  });
})();
