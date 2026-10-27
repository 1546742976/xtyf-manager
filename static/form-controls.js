/* Shared by server pages and the existing packaged-template builder. */
(() => {
  'use strict';
  if(window.FormControls){window.FormControls.refresh();return;}
  const state=new WeakMap(),watched=new Set();
  let frame=0,inactive=false;
  const number=(value,fallback=0)=>Number.parseFloat(value)||fallback;
  function fit(field){
    if(!field.getClientRects().length)return;
    const width=field.getBoundingClientRect().width;
    if(!width)return;
    const css=getComputedStyle(field),viewport=window.visualViewport?.height||window.innerHeight;
    const line=number(css.lineHeight,number(css.fontSize,16)*1.2);
    const border=number(css.borderTopWidth)+number(css.borderBottomWidth);
    const padding=number(css.paddingTop)+number(css.paddingBottom);
    const cap=Math.max(number(css.minHeight),Math.min(number(css.getPropertyValue('--textarea-max-height'),360),viewport*.45));
    const minimum=Math.min(cap,Math.max(number(css.minHeight),Math.max(1,field.rows)*line+padding+border));
    const key=[field.value,width,minimum,cap,css.font,css.letterSpacing,css.wordBreak,field.wrap].join('\u0000');
    if(state.get(field)?.key===key)return;
    const scroll=field.scrollTop;
    field.style.maxHeight=cap+'px';
    field.style.height='auto';
    const needed=Math.max(minimum,field.scrollHeight+border);
    field.style.height=Math.min(needed,cap)+'px';
    field.style.overflowY=needed>cap+1?'auto':'hidden';
    field.scrollTop=scroll;
    state.set(field,{key,width});
  }
  function flush(){
    frame=0;if(inactive)return;
    for(const field of watched)if(!field.isConnected){observer?.unobserve(field);watched.delete(field);}
    for(const field of document.querySelectorAll('textarea')){
      if(!watched.has(field)){watched.add(field);observer?.observe(field);}
      fit(field);
    }
  }
  function refresh(){if(!inactive&&!frame)frame=requestAnimationFrame(flush);}
  // Height changes caused by fit() must not start a ResizeObserver feedback loop.
  const observer=typeof ResizeObserver==='function'?new ResizeObserver(entries=>{
    if(entries.some(({target})=>Math.abs(target.getBoundingClientRect().width-(state.get(target)?.width||0))>.5))refresh();
  }):null;
  const mutations=new MutationObserver(refresh);
  function start(){
    mutations.observe(document.documentElement,{subtree:true,childList:true,attributes:true,attributeFilter:['open','hidden','class','rows','style']});
    refresh();
  }
  document.addEventListener('input',event=>{if(event.target.matches('textarea'))refresh();});
  document.addEventListener('reset',refresh,true);
  window.addEventListener('resize',refresh);
  window.visualViewport?.addEventListener('resize',refresh);
  document.fonts?.ready.then(refresh);
  window.addEventListener('pagehide',()=>{
    inactive=true;cancelAnimationFrame(frame);frame=0;mutations.disconnect();observer?.disconnect();watched.clear();
  });
  window.addEventListener('pageshow',()=>{inactive=false;start();});
  window.FormControls={refresh};
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});else start();
})();
