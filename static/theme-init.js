/* Resolve system mode and the per-account custom color before stylesheets paint. */
(function () {
  const root = document.documentElement;
  if (root.dataset.themeMode === 'system' && window.matchMedia) {
    root.dataset.theme = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  const mode = root.dataset.theme === 'dark' ? 'dark' : 'light';
  const custom = root.dataset[mode + 'Custom'] === 'true';
  root.dataset.palette = custom ? 'custom-' + mode : (root.dataset[mode + 'Theme'] || (mode === 'dark' ? 'space' : 'silver'));
  if (custom) root.style?.setProperty?.('--custom-accent', root.dataset[mode + 'Accent']);
  else root.style?.removeProperty?.('--custom-accent');
})();
