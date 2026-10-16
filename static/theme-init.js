/* Resolve system mode before stylesheets paint. */
(function () {
  const root = document.documentElement;
  if (root.dataset.themeMode === 'system' && window.matchMedia) {
    root.dataset.theme = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  root.dataset.palette = root.dataset.theme === 'dark' ? (root.dataset.darkTheme || 'space') : (root.dataset.lightTheme || 'silver');
})();
