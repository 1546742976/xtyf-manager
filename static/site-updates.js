"use strict";
document.addEventListener('DOMContentLoaded', () => {
  const banner = document.querySelector('[data-site-update-banner]');
  if (banner) {
    const storageKey = 'xtyf-site-release-seen:' + banner.dataset.userId;
    const close = banner.querySelector('[data-site-update-close]');
    const detail = banner.querySelector('[data-site-update-detail]');
    const summary = banner.querySelector('[data-site-update-summary]');
    let current = '';
    let acknowledged = '';
    try {
      acknowledged = window.localStorage.getItem(storageKey) || '';
    } catch (_) {
      // Private browsing may deny storage; the notice still works for this page.
    }

    const dismiss = () => {
      if (!current) return;
      acknowledged = current;
      try {
        window.localStorage.setItem(storageKey, current);
      } catch (_) { /* Storage is optional. */ }
      banner.hidden = true;
    };
    close?.addEventListener('click', dismiss);
    detail?.addEventListener('click', dismiss);

    const refresh = async () => {
      if (document.hidden) return;
      try {
        const response = await fetch(banner.dataset.siteUpdateApi, {
          credentials: 'same-origin',
          cache: 'no-store',
        });
        if (!response.ok) return;
        const data = await response.json();
        if (!data.available || !/^[0-9a-f]{40}$/.test(data.commit || '')) {
          banner.hidden = true;
          return;
        }
        current = data.commit;
        if (summary) summary.textContent = data.latest_subject || '新版本已上线，点击查看本次更新内容。';
        banner.hidden = current === acknowledged;
      } catch (_) {
        // A transient outage must not interrupt the rest of the workspace.
      }
    };
    void refresh();
    window.setInterval(refresh, 60000);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) void refresh(); });
    window.addEventListener('focus', () => { if (!document.hidden) void refresh(); });
  }

  // Render with DOM nodes and textContent, never HTML from Git commit messages.
  // The same script works in Flask pages and the native clients' local template shell.
  const root = document.querySelector('[data-site-update-timeline]');
  if (!root) return;
  const make = (tag, className, value) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (value !== undefined) node.textContent = value;
    return node;
  };
  const showMessage = message => root.replaceChildren(make('p', 'release-empty', message));
  const loadHistory = async () => {
    try {
      const response = await fetch(root.dataset.siteUpdateHistoryApi, {
        credentials: 'same-origin',
        cache: 'no-store',
      });
      if (!response.ok) throw new Error('HTTP ' + response.status);
      const data = await response.json();
      if (!data.available || !/^[0-9a-f]{40}$/.test(data.commit || '')) {
        showMessage('当前部署尚未包含更新日志快照。请在服务器使用新版发布脚本重新打包部署。');
        return;
      }
      const summary = make('section', 'release-summary');
      summary.setAttribute('aria-label', '当前网站版本');
      const left = make('div');
      left.append(make('span', 'release-eyebrow', '当前部署版本'),
        make('strong', '', data.commit.slice(0, 10)),
        make('p', '', '收录最近 ' + (Array.isArray(data.commits) ? data.commits.length : 0) + ' 条提交记录（最多 200 条）'));
      const right = make('div');
      right.append(make('span', 'release-eyebrow', '打包时间（UTC）'));
      const built = make('time', '', (data.built_at || '').slice(0, 19).replace('T', ' ') || '未知');
      built.dateTime = data.built_at || '';
      right.append(built);
      summary.append(left, right);
      const timeline = make('div', 'release-timeline');
      timeline.setAttribute('aria-label', '提交历史时间轴');
      let currentDay = null;
      let items = null;
      const commits = Array.isArray(data.commits) ? data.commits : [];
      for (const entry of commits) {
        if (!/^[0-9a-f]{40}$/.test(entry.sha || '')) continue;
        const day = String(entry.date || '').slice(0, 10);
        if (day !== currentDay) {
          currentDay = day;
          const section = make('section', 'release-day');
          section.append(make('h2', '', day || '日期未知'));
          items = make('ol', 'release-entries');
          section.append(items);
          timeline.append(section);
        }
        const item = make('li', 'release-entry');
        item.append(make('span', 'release-dot'));
        const body = make('div', 'release-entry-body');
        const meta = make('div', 'release-entry-meta');
        const when = make('time', '', String(entry.date || '').slice(11, 16));
        when.dateTime = entry.date || '';
        meta.append(when, make('span', '', entry.author || '未知作者'));
        body.append(meta, make('p', '', entry.subject || '未填写提交说明'));
        const link = make('a', '', entry.sha.slice(0, 10) + ' ↗');
        link.href = 'https://github.com/SHM-white/xtyf-manager/commit/' + entry.sha;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.setAttribute('aria-label', '在 GitHub 查看提交 ' + entry.sha.slice(0, 10));
        body.append(link);
        item.append(body);
        items.append(item);
      }
      if (!timeline.children.length) timeline.append(make('p', 'release-empty', '此版本没有可展示的提交记录。'));
      root.replaceChildren(summary, timeline);
    } catch (_) {
      showMessage('暂时无法加载更新日志，请检查服务器连接后刷新页面。');
    }
  };
  void loadHistory();
});
