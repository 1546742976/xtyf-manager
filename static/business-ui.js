"use strict";

// Shared transport and action boundaries; writes are never automatically retried.
window.BusinessUI = (() => {
  const pending = new Map();
  function message(error) { return error?.message || "操作失败，请稍后重试"; }
  function notify(text, login = false) {
    let box = document.getElementById("business-feedback");
    if (!box) {
      box = document.createElement("div");
      box.id = "business-feedback";
      box.className = "business-feedback";
      box.setAttribute("role", "alert");
      const content = document.createElement("span");
      const relogin = document.createElement("a");
      relogin.href="/login"; relogin.target="_blank"; relogin.rel="noopener"; relogin.textContent="重新登录";
      const close = document.createElement("button");
      close.type = "button"; close.textContent = "关闭";
      close.addEventListener("click", () => { box.hidden = true; });
      box.append(content, relogin, close); document.body.append(box);
    }
    box.querySelector("span").textContent = text;
    box.querySelector("a").hidden = !login;
    box.hidden = false;
  }
  async function consume(url, options, read) {
    const {timeoutMs = options.body instanceof FormData ? 180000 : 15000, ...init} = options;
    const controller = new AbortController();
    const mutating = !["GET", "HEAD"].includes((init.method || "GET").toUpperCase());
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
    const abort = () => controller.abort();
    if (init.signal?.aborted) abort();
    init.signal?.addEventListener("abort", abort, {once:true});
    try {
      const response = await fetch(url, {...init, signal:controller.signal});
      return await read(response);
    } catch (error) {
      if (timedOut || error instanceof TypeError || error.name === "AbortError") {
        throw new Error((timedOut ? "请求超时" : "网络连接中断") +
          (mutating ? "，结果尚未确认，请先刷新核对后再提交，避免重复操作。" : "，请检查网络后重试。"));
      }
      throw error;
    } finally {
      clearTimeout(timer);
      init.signal?.removeEventListener("abort", abort);
    }
  }
  async function request(url, options = {}) {
    return consume(url, options, async response => {
      let data;
      try { data = await response.json(); }
      catch (error) {
        if (error.name === "AbortError" || error instanceof TypeError) throw error;
        if (response.ok) throw new Error("服务器响应格式异常，请刷新核对操作结果后再重试。");
        data = {};
      }
      if (!data || typeof data !== "object" || Array.isArray(data)) throw new Error("服务器响应格式异常，请刷新核对操作结果。");
      if (!response.ok) {
        const fallback = ({401:"登录已失效，请重新登录",403:"没有操作权限或会话校验失败，请刷新页面",409:"数据已变化，请刷新核对后重新操作",429:"操作过于频繁，请稍后重试"})[response.status] || `服务器请求失败（${response.status}），请稍后重试`;
        data = {...data, ok:false, success:false, error:data.error || data.msg || fallback, msg:data.msg || data.error || fallback};
        if (data.code === 0 || data.code == null) data.code = response.status;
        if(response.status===401 && location.pathname!=="/login") notify("登录已失效，当前输入已保留。请在新标签页重新登录。", true);
      }
      return {ok:response.ok, status:response.status, headers:response.headers, json:async () => data};
    });
  }
  function action(key, controls, operation, report = notify) {
    if (pending.has(key)) return pending.get(key);
    const elements = [...(typeof controls === "string" ? document.querySelectorAll(controls) : controls || [])];
    const before = elements.map(el => ({disabled:el.disabled, busy:el.getAttribute("aria-busy")}));
    elements.forEach(el => { el.disabled = true; el.setAttribute("aria-busy", "true"); });
    // Capture live event/FormData/FileList values before the handler returns.
    let outcome;
    try { outcome = operation(); } catch (error) { outcome = Promise.reject(error); }
    const promise = Promise.resolve(outcome).catch(error => { report(message(error)); }).finally(() => {
      elements.forEach((el,i) => {
        el.disabled = before[i].disabled;
        if (before[i].busy == null) el.removeAttribute("aria-busy"); else el.setAttribute("aria-busy", before[i].busy);
      });
      pending.delete(key);
    });
    pending.set(key, promise);
    return promise;
  }
  function guard(fn, key, controls, report) {
    return function(...args) {
      // Inline submit handlers must cancel before the first await.
      if (args[0]?.type === "submit") args[0].preventDefault();
      return action(typeof key === "function" ? key(...args) : key,
        typeof controls === "function" ? controls(...args) : controls, () => fn.apply(this,args), report);
    };
  }
  async function download(url, filename = "download") {
    return action("download:"+url, document.activeElement?.matches("button,a") ? [document.activeElement] : [], async () => {
      const blob = await consume(url, {timeoutMs:180000}, async response => {
        if (!response.ok || /text\/html|application\/json/.test(response.headers.get("Content-Type") || "")) {
          const data = await response.json().catch(() => ({}));
          throw new Error(data.error || data.msg || `下载失败（${response.status}），请检查登录状态和权限`);
        }
        return response.blob();
      });
      const href = URL.createObjectURL(blob), link = document.createElement("a");
      link.href = href; link.download = filename; document.body.append(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(href), 60000);
    });
  }
  document.addEventListener("click", event => {
    const link = event.target.closest('a[data-download-name]');
    if (!link) return;
    event.preventDefault(); download(link.href, link.dataset.downloadName);
  });
  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll('form[action="/logout"]').forEach(form => form.addEventListener("submit", event => {
      event.preventDefault();
      action("logout", form.querySelectorAll("button"), async () => {
        const response = await consume(form.action, {method:"POST", body:new FormData(form)}, async value => value);
        if (!response.ok) throw new Error("退出登录失败，请刷新后重试");
        location.assign("/login");
      });
    }));
  });
  function updateTextCount(input) {
    if(!input?.matches?.('[data-count-text][maxlength]'))return;
    let count=input.nextElementSibling;
    if(!count?.classList.contains('text-counter')){count=document.createElement('small');count.className='text-counter';input.after(count);}
    count.textContent=`${Array.from(input.value).length} / ${input.maxLength} 字`;
  }
  document.addEventListener('input',event=>updateTextCount(event.target));
  document.addEventListener('DOMContentLoaded',()=>document.querySelectorAll('[data-count-text]').forEach(updateTextCount));
  // 唯一实现：页面脚本通过 window.BusinessUI.escape 共用。
  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]
    ));
  }
  return {fetch:request, action, guard, download, notify, message, escape: escapeHtml, toast:{hide(){const box=document.getElementById("business-feedback");if(box)box.hidden=true;}}};
})();
