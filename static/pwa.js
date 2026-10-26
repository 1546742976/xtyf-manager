/* Installation uses the browser's session and UI. No native bridge or auth store. */
(() => {
  'use strict';
  if (window.XTYFClient || window.__TAURI_INTERNALS__ || location.protocol === 'tauri:') return;
  const button = document.getElementById('pwa-install');
  const status = document.getElementById('pwa-status');
  const say = text => { if (status) status.textContent = text; };
  const standalone = () => window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
  let installPrompt = null;
  const installed = () => {
    installPrompt = null;
    if (button) button.hidden = true;
    say('已在独立窗口运行或已完成安装；也可继续在浏览器使用。');
  };
  if (standalone()) installed();
  window.addEventListener('beforeinstallprompt', event => {
    // Never prompt on page load. The account page is the deliberate install entry.
    if (!button || standalone()) return;
    event.preventDefault();
    installPrompt = event;
    button.hidden = false;
    say('此浏览器支持安装。安装后仍需联网处理业务。');
  });
  window.addEventListener('appinstalled', installed);
  button?.addEventListener('click', async () => {
    if (!installPrompt) return;
    const prompt = installPrompt;
    installPrompt = null;
    button.disabled = true;
    button.hidden = true;
    try {
      await prompt.prompt();
      const choice = await prompt.userChoice;
      say(choice.outcome === 'accepted' ? '已接受安装请求，请以浏览器的安装结果为准。' : '已取消安装，可继续在浏览器使用。');
    } catch (_) { say('浏览器未完成安装，请使用浏览器菜单，或继续在网页使用。'); }
    finally { button.disabled = false; }
  });

  let notice;
  const connection = () => {
    if (!notice && !navigator.onLine) {
      notice = document.createElement('p');
      notice.className = 'pwa-network-notice';
      notice.setAttribute('role', 'alert');
      notice.textContent = '当前离线，业务操作需联网。已显示的信息可能不再准确；未提交的输入仍保留，请恢复网络后核对再提交。';
      document.body.append(notice);
    }
    if (notice) notice.hidden = navigator.onLine;
  };
  window.addEventListener('online', connection);
  window.addEventListener('offline', connection);
  connection();

  if (!window.isSecureContext || !('serviceWorker' in navigator)) {
    say('当前环境不支持安装；请使用受信任的 HTTPS 地址及支持的浏览器。仍可直接使用网页。');
    return;
  }
  navigator.serviceWorker.register('/sw.js', {scope: '/', updateViaCache: 'none'}).then(registration => {
    const updateReady = () => say('更新已准备好。保存工作并关闭所有本站窗口后重新打开，即可使用新版。');
    const waiting = () => {
      if (registration.waiting && navigator.serviceWorker.controller) updateReady();
    };
    waiting();
    registration.addEventListener('updatefound', () => {
      const worker = registration.installing;
      worker?.addEventListener('statechange', () => {
        // The waiting property may be published after the installed event.
        if (worker.state === 'installed' && navigator.serviceWorker.controller) updateReady();
      });
    });
    let lastCheck = Date.now();
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden && navigator.onLine && Date.now() - lastCheck > 3600000) {
        lastCheck = Date.now();
        registration.update().catch(() => {});
      }
    });
  }).catch(() => say('安装资源暂不可用，可继续在浏览器使用；请联网重试或联系管理员。'));
})();
