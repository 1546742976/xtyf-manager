"use strict";
document.addEventListener('DOMContentLoaded', () => {
  const button = document.querySelector('[data-ws-menu]');
  const overlay = document.querySelector('[data-ws-close]');
  if (!button) return;
  const setOpen = (open) => {
    document.body.classList.toggle('ws-menu-open', open);
    button.setAttribute('aria-expanded', String(open));
    button.setAttribute('aria-label', open ? '关闭主导航' : '打开主导航');
  };
  button.addEventListener('click', () => setOpen(!document.body.classList.contains('ws-menu-open')));
  overlay?.addEventListener('click', () => setOpen(false));
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') setOpen(false);
  });
  window.addEventListener('resize', () => {
    if (window.innerWidth > 850) setOpen(false);
  });
  // The task pages load app.js, which already manages this badge. Other pages do not.
  const badge = document.getElementById('fundsPendingBadge');
  if (badge && typeof window.refreshFundsBadge !== 'function') {
    const updateBadge = async () => {
      try {
        const response = await fetch('/funds/api/pending_review', {credentials: 'same-origin'});
        if (!response.ok) throw new Error('pending review unavailable');
        const data = await response.json();
        badge.textContent = data.count > 0 ? String(data.count) : '0';
        badge.style.display = data.code === 0 && data.count > 0 ? '' : 'none';
      } catch (_) {
        badge.style.display = 'none';
      }
    };
    updateBadge();
    setInterval(updateBadge, 30000);
  }
});
