/* One durable request per user intent. Recovery never invents a replacement id. */
(function(root) {
  'use strict';
  function create({storage, fetch, server, uuid}) {
    let scope = null, busy = false;
    const prefix = () => 'xtyf:funds:create:v1:' + encodeURIComponent(scope.server) + ':' + scope.member + ':';
    async function capability() {
      const response = await fetch('/funds/api/capabilities');
      const data = await response.json();
      if (!response.ok || data?.code !== 0 || data.create_idempotency !== 1 || !Number.isSafeInteger(data.member_id) || data.member_id < 1)
        throw Error('服务端尚未支持安全新增或当前登录已失效，请核对登录并升级服务端；其他既有操作仍可使用。');
      return data.member_id;
    }
    async function connect() {
      scope = null;
      const address = server(), member = await capability();
      if (address !== server()) throw Error('服务器已切换，请重新打开资金页面。');
      scope = {server:address, member};
      return pending();
    }
    function pending() {
      if (!scope) throw Error('尚未完成新增能力检查。');
      const entries = [];
      for (let i=0; i<storage.length; i++) {
        const key = storage.key(i);
        if (!key?.startsWith(prefix())) continue;
        const entry = JSON.parse(storage.getItem(key));
        if (!entry?.body || key !== prefix()+entry.body.request_id || entry.body.expected_member_id !== scope.member)
          throw Error('待确认记录格式异常，请保留本地数据并联系管理员核对。');
        entries.push(entry);
      }
      return entries.sort((a,b)=>a.created-b.created);
    }
    function prepare(body) {
      if (!scope || scope.server !== server()) throw Error('请重新检查服务器和账号。');
      if (busy || pending().length) throw Error('存在待确认记录，请先重试原请求。');
      const entry = {body:{...body, expected_member_id:scope.member, request_id:uuid()}, created:Date.now()};
      const key = prefix()+entry.body.request_id, text = JSON.stringify(entry);
      storage.setItem(key, text);
      if (storage.getItem(key) !== text) throw Error('本地存储未能保存请求，尚未提交。');
      return entry;
    }
    async function send(entry) {
      if (busy) return {state:'busy'};
      busy = true;
      try {
        if (!scope || scope.server !== server()) throw Error('服务器已切换，请回到原服务器核对。');
        const key = prefix()+entry.body.request_id;
        if (storage.getItem(key) !== JSON.stringify(entry)) throw Error('原请求记录已变化，请刷新核对。');
        if (await capability() !== scope.member || scope.server !== server()) throw Error('账号或服务器已变化，请使用原账号核对。');
        const response = await fetch('/funds/api/records', {
          method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(entry.body)
        });
        const data = await response.json();
        const matching = data?.request_id === entry.body.request_id;
        const success = response.ok && data?.code === 0 && matching && Number.isSafeInteger(data.id) && data.id > 0 &&
          Number.isSafeInteger(data.version) && data.version > 0 && typeof data.replayed === 'boolean' && ['ok','failed'].includes(data.ledger_sync);
        const rejected = response.status === 400 && data?.code === 1 && matching && data.reason === 'validation_failed' && typeof data.msg === 'string';
        if (!success && !rejected) throw Error([401,403].includes(response.status)
          ? '登录或权限已变化，请使用原账号重新登录后核对。'
          : response.status === 409 ? '请求标识与已保存内容冲突，请联系管理员核对。' : '没有收到有效创建回执。');
        storage.removeItem(key);
        if (storage.getItem(key) !== null) throw Error('回执已收到，但本地记录无法清理，请重试原请求。');
        return {state:success ? 'confirmed' : 'rejected', receipt:data};
      } catch (error) {
        return {state:'pending', message:'结果待确认，原请求已保留。' + error.message};
      } finally { busy = false; }
    }
    return {connect, pending, prepare, send};
  }
  const api = {create};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.FundsCreate = api;
})(typeof window === 'undefined' ? globalThis : window);
