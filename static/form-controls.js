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
    const previous=state.get(field);
    if(previous?.key===key)return;
    // Auto-fit remains capped, but a user can drag a textarea taller than that
    // cap (up to a viewport-safe limit). Do not undo a manual height on input.
    const resizeCap=Math.max(cap,Math.min(number(css.getPropertyValue('--textarea-resize-max-height'),800),viewport*.75));
    field.style.maxHeight=resizeCap+'px';
    if(previous?.manual){
      field.style.overflowY='auto';
      state.set(field,{...previous,key,width,height:field.getBoundingClientRect().height});
      return;
    }
    const scroll=field.scrollTop;
    field.style.height='auto';
    const needed=Math.max(minimum,field.scrollHeight+border);
    field.style.height=Math.min(needed,cap)+'px';
    field.style.overflowY=needed>cap+1?'auto':'hidden';
    field.scrollTop=scroll;
    state.set(field,{key,width,height:field.getBoundingClientRect().height,manual:false});
  }
  const dirty=new Set();
  function fields(root){
    if(root?.matches?.('textarea'))return [root];
    return root?.querySelectorAll?root.querySelectorAll('textarea'):[];
  }
  function refresh(root=document){
    if(inactive)return;
    for(const field of fields(root)){
      if(!watched.has(field)){watched.add(field);observer?.observe(field);}
      dirty.add(field);
    }
    if(dirty.size&&!frame)frame=requestAnimationFrame(flush);
  }
  function flush(){
    frame=0;if(inactive)return;
    for(const field of dirty){
      if(field.isConnected)fit(field);
      else {observer?.unobserve(field);watched.delete(field);}
    }
    dirty.clear();
  }
  const observer=typeof ResizeObserver==='function'?new ResizeObserver(entries=>{
    for(const {target} of entries){
      const previous=state.get(target);
      if(!previous)continue;
      const rect=target.getBoundingClientRect();
      if(Math.abs(rect.width-previous.width)>.5){refresh(target);continue;}
      // A height-only change outside fit() is a native drag resize. Ignore
      // our own height writes because fit() records their resulting height.
      if(Math.abs(rect.height-previous.height)>1){
        state.set(target,{...previous,manual:true,height:rect.height});
        target.style.overflowY='auto';
      }
    }
  }):null;
  const mutations=new MutationObserver(records=>{
    for(const record of records){
      if(record.type==='childList'){
        for(const node of record.addedNodes)refresh(node);
        for(const node of record.removedNodes)for(const field of fields(node)){
          if(!field.isConnected){observer?.unobserve(field);watched.delete(field);dirty.delete(field);}
        }
      }else{
        // Unrelated tooltips/animations must not scan the document. fit's own
        // height writes do not change its sizing key, so ignore their records.
        if(record.target.matches?.('textarea')&&record.attributeName==='style')continue;
        for(const field of watched)if(record.target===field||record.target.contains(field))refresh(field);
      }
    }
  });
  function start(){
    mutations.observe(document.documentElement,{subtree:true,childList:true,attributes:true,attributeFilter:['open','hidden','class','rows','style']});
    refresh();
  }
  document.addEventListener('input',event=>{if(event.target.matches('textarea'))refresh(event.target);});
  document.addEventListener('reset',event=>{
    // Reset should restore automatic sizing, including after a manual drag.
    for(const field of fields(event.target)){
      state.delete(field);
      field.style.height='';
      field.style.overflowY='';
    }
    refresh(event.target);
  },true);
  window.addEventListener('resize',()=>refresh());
  window.visualViewport?.addEventListener('resize',()=>refresh());
  document.fonts?.ready.then(()=>refresh());
  window.addEventListener('pagehide',()=>{
    inactive=true;cancelAnimationFrame(frame);frame=0;mutations.disconnect();observer?.disconnect();watched.clear();dirty.clear();
  });
  window.addEventListener('pageshow',()=>{inactive=false;start();});
  window.FormControls={refresh};
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});else start();
})();
