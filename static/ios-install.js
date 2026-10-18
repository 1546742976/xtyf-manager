"use strict";
// Safari owns installation; the site only explains its Share menu action.
document.querySelectorAll('[data-ios-install]').forEach(element => {
  const iphone = /iPhone|iPod/.test(navigator.userAgent);
  const installed = navigator.standalone === true || matchMedia('(display-mode: standalone)').matches;
  const native = typeof window.XTYF?.invoke === 'function';
  element.hidden = !iphone || installed || native;
});
