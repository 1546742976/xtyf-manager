"use strict";
(() => {
  const badge=document.getElementById('collab-unread');if(!badge)return;
  let pending=false,timer;
  async function refresh() {
    clearTimeout(timer);if(document.hidden||pending)return;
    pending=true;const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),15000);
    try {
      const response=await fetch('/discussions/api/unread',{signal:controller.signal});
      if(!response.ok)return;
      const data=await response.json();if(!data.ok)return;
      const total=Math.max(0,Number(data.total)||0);badge.textContent=total>99?'99+':String(total);
      badge.hidden=!total;badge.style.display=total?'':'none';badge.setAttribute('aria-label',`${total} 条协作未读消息`);
    } catch (_) { /* Keep the last known count when the read-only refresh fails. */ }
    finally {clearTimeout(timeout);pending=false;if(!document.hidden)timer=setTimeout(refresh,30000);}
  }
  document.addEventListener('visibilitychange',()=>{clearTimeout(timer);if(!document.hidden)refresh();});
  document.addEventListener('collaboration:unread',refresh);refresh();
})();
