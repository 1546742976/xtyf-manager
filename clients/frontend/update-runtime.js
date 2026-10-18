/* Local update lifecycle. Downloading never reloads the running page. */
(() => {
  'use strict';
  function supports(config, capability) {
    return Number(config?.bridgeVersion) >= 2 && Array.isArray(config.capabilities) && config.capabilities.includes(capability);
  }
  function compatibleServer(data) {
    const range = data?.uiCompatibility;
    return data?.protocolVersion === 1 && (!range ||
      (Number.isInteger(range.protocolMin) && Number.isInteger(range.protocolMax) && range.protocolMin <= 1 && range.protocolMax >= 1));
  }
  function create({invoke, visible, changed, now = Date.now, setTimer = setTimeout, clearTimer = clearTimeout}) {
    let enabled = false, epoch = 0, timer = null, running = null, lastCheck = null;
    function stopTimer() { if (timer !== null) clearTimer(timer); timer = null; }
    function schedule() {
      stopTimer();
      if (!enabled || !visible()) return;
      const delay = lastCheck === null ? 0 : Math.max(0, 30 * 60 * 1000 - (now() - lastCheck));
      timer = setTimer(() => { timer = null; if (visible()) check(false); }, delay);
    }
    async function check(manual = true) {
      if (!enabled || (!manual && !visible())) return;
      if (running) return running;
      const own = epoch;
      lastCheck = now();
      changed({status:'checking', message:'正在检查页面更新…'});
      const job = (async () => {
        try {
          const result = await invoke('check_updates', {retry:manual});
          if (own === epoch) changed(result);
          return result;
        } catch (error) {
          if (own === epoch) changed({status:'failed', message:error.message || '检查更新失败，请稍后重试。'});
        } finally {
          if (own === epoch) { running = null; schedule(); }
        }
      })();
      running = job;
      return job;
    }
    function configure(config) {
      epoch++; stopTimer(); running = null; lastCheck = null;
      enabled = supports(config, 'ui_updates');
      if (!enabled) return;
      const own = epoch;
      invoke('update_status', {}).then(result => { if (own === epoch && lastCheck === null) changed(result); }).catch(() => {});
      if (visible()) check(false);
    }
    function visibility() {
      stopTimer();
      if (!enabled || !visible()) return;
      if (lastCheck === null || now() - lastCheck > 5 * 60 * 1000) check(false);
      else schedule();
    }
    function dispose() { enabled = false; epoch++; running = null; stopTimer(); }
    return {configure, check, visibility, dispose};
  }
  const api = {supports, compatibleServer, create};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof window !== 'undefined') window.XTYFUpdates = api;
})();
