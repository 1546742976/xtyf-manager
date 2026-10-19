/* Bootstrap classification is independent of native transport and page updates. */
(() => {
  'use strict';
  function classify(response, compatibleServer) {
    const status = Number(response.status);
    if (status === 404 || status === 405) {
      return {mode:'website', message:'服务器未提供客户端接口，可以在客户端内打开网站。'};
    }
    let data;
    try { data = JSON.parse(response.body); } catch (_) { data = null; }
    if (status < 200 || status >= 300 || !Number.isFinite(status)) {
      return {mode:'error', message:typeof data?.error === 'string' ? data.error : '服务器暂时不可用（'+status+'）'};
    }
    if (!data || typeof data !== 'object' || Array.isArray(data) || !compatibleServer(data)) {
      return {mode:'website', message:'服务器协议不兼容，可以在客户端内打开网站，或检查客户端更新。'};
    }
    if (status !== 200 || !data.ok) return {mode:'error', message:typeof data.error === 'string' ? data.error : '服务器暂时不可用'};
    return {mode:'local', data};
  }
  const api = {classify};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof window !== 'undefined') window.XTYFConnection = api;
})();
