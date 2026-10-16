/* Compatibility boundary for packaged legacy business modules. No network in this view. */
(() => {
  'use strict';
  const app=parent.XTYF,OriginalFormData=window.FormData,selected=new WeakMap(),localOrigin=app.localBase;
  const own=app.generation,controllers=new Set();
  const current=()=>new URL(app.route,localOrigin);
  window.XTYFLocation={
    get href(){return current().href;},set href(value){app.navigate(value);},
    get origin(){return localOrigin;},get pathname(){return current().pathname;},get search(){return current().search;},get hash(){return current().hash;},
    assign:value=>app.navigate(value),replace:value=>app.navigate(value,true),reload:()=>app.navigate(app.route,true)
  };
  window.XTYFHistory={replaceState:(_state,_title,url)=>app.replaceRoute(url),pushState:(_state,_title,url)=>app.navigate(url),back:()=>parent.XTYFClient.back()};
  window.XTYF={...app,pickInvoiceFiles:async()=>{
    const files=await app.invoke('pick_files',{multiple:true,accept:'.pdf'});if(files.length)window.handleFiles(files.map(file=>({...file,type:file.mime})));
  }};
  window.fetch=async(input,options={})=>{
    const controller=new AbortController();controllers.add(controller);
    const abort=()=>controller.abort();options.signal?.addEventListener('abort',abort,{once:true});
    if(options.signal?.aborted)controller.abort();
    try{return await app.request(input,{...options,signal:controller.signal});}
    finally{controllers.delete(controller);options.signal?.removeEventListener('abort',abort);}
  };
  window.addEventListener('pagehide',()=>{for(const c of controllers)c.abort();});
  class NativeFormData {
    constructor(form){
      this._nativeMultipart=true;this.values=[];
      if(form){
        for(const [key,value] of new OriginalFormData(form))if(typeof value==='string')this.append(key,value);
        for(const input of form.querySelectorAll('input[type=file][name]:not(:disabled)'))for(const file of input.files)this.append(input.name,file);
      }
    }
    append(name,value,filename){this.values.push([String(name),value&&typeof value==='object'?filename?{...value,name:filename}:value:String(value)]);}
    set(name,value,filename){this.delete(name);this.append(name,value,filename);}
    get(name){return this.values.find(pair=>pair[0]===name)?.[1]??null;}
    getAll(name){return this.values.filter(pair=>pair[0]===name).map(pair=>pair[1]);}
    has(name){return this.values.some(pair=>pair[0]===name);}
    delete(name){this.values=this.values.filter(pair=>pair[0]!==name);}
    *entries(){yield* this.values;} *keys(){for(const pair of this.values)yield pair[0];} *valuesIterator(){for(const pair of this.values)yield pair[1];}
    [Symbol.iterator](){return this.entries();}forEach(callback){for(const [name,value] of this.values)callback(value,name,this);}
  }
  window.FormData=NativeFormData;
  class NativeXHR extends EventTarget {
    constructor(){super();this.upload={};this.headers={};this.status=0;this.readyState=0;this.responseText='';this.timeout=0;}
    open(method,url){this.method=method;this.url=url;this.readyState=1;}
    setRequestHeader(name,value){this.headers[name]=value;}
    getResponseHeader(name){return this.responseHeaders?.get(name)||null;}
    getAllResponseHeaders(){return [...(this.responseHeaders||[])].map(([k,v])=>k+': '+v).join('\r\n');}
    event(name){const event=new Event(name);this['on'+name]?.(event);this.dispatchEvent(event);}
    abort(){this.aborted=true;this.controller?.abort();}
    async send(body){
      this.controller=new AbortController();let timedOut=false;
      const timer=this.timeout?setTimeout(()=>{timedOut=true;this.controller.abort();},this.timeout):null;
      this.upload.onprogress?.({lengthComputable:false,loaded:0,total:0});
      try{
        const result=await window.fetch(this.url,{method:this.method,headers:this.headers,body,signal:this.controller.signal});
        this.status=result.status;this.responseHeaders=result.headers;this.responseText=await result.text();this.response=this.responseText;this.readyState=4;
        this.event('readystatechange');this.event('load');
      }catch(error){this.event(timedOut?'timeout':this.aborted?'abort':'error');}
      finally{clearTimeout(timer);this.event('loadend');}
    }
  }
  window.XMLHttpRequest=NativeXHR;
  class JobPoller {
    constructor(id){this.closed=false;this.id=id;this.controller=null;this.timer=setTimeout(()=>this.poll(),0);}
    async poll(){
      if(this.closed)return;this.controller=new AbortController();
      try{
        const response=await window.fetch('/invoices/api/job/'+encodeURIComponent(this.id),{signal:this.controller.signal});
        if(!response.ok)throw new Error('任务状态读取失败');
        const data=await response.json();if(!this.closed)this.onmessage?.({data:JSON.stringify(data)});
      }catch(error){if(!this.closed)this.onerror?.(error);}
      if(!this.closed)this.timer=setTimeout(()=>this.poll(),3000);
    }
    close(){this.closed=true;clearTimeout(this.timer);this.controller?.abort();}
  }
  window.XTYFJobPoller=JobPoller;
  const inputValue=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value');
  function fileInput(input){
    if(selected.has(input))return;selected.set(input,[]);
    Object.defineProperty(input,'files',{configurable:true,get:()=>selected.get(input)});
    Object.defineProperty(input,'value',{configurable:true,get:()=>inputValue.get.call(input),set:value=>{inputValue.set.call(input,value);if(value==='')selected.set(input,[]);}});
  }
  async function choose(input){
    if(input.disabled)return;fileInput(input);
    try{
      const files=await app.invoke('pick_files',{multiple:input.multiple,accept:input.accept||''});
      if(own!==app.generation||!files.length)return;
      selected.set(input,files.map(file=>({...file,type:file.mime||'application/octet-stream'})));
      input.dispatchEvent(new Event('change',{bubbles:true}));
    }catch(error){app.notify(error.message);}
  }
  function dispatchLink(link,event){
    const raw=link.getAttribute('href');if(!raw)return;
    if(raw.startsWith('#')){
      event.preventDefault();event.stopImmediatePropagation();
      const target=document.getElementById(decodeURIComponent(raw.slice(1)));
      if(target){target.scrollIntoView();if(!target.hasAttribute('tabindex'))target.tabIndex=-1;target.focus({preventScroll:true});}
      return;
    }
    let url;try{url=new URL(raw,window.XTYFLocation.href);}catch(_){return;}
    if(url.protocol==='blob:')return;
    event.preventDefault();event.stopImmediatePropagation();
    if(!['http:','https:','tauri:'].includes(url.protocol)){app.notify('此链接类型无法在应用内打开，可长按复制链接。');return;}
    const same=app.isLocal(url)||(app.config.serverUrl&&url.origin===new URL(app.config.serverUrl).origin);
    if(!same)return app.openExternal(url.href).catch(error=>app.notify(error.message));
    const path=url.pathname+url.search+url.hash;
    if(app.isPage(path))return app.navigate(path);
    const name=link.dataset.downloadName||link.download||link.getAttribute('aria-label')||link.textContent.trim()||'附件';
    const operation=link.hasAttribute('download')||link.dataset.downloadName||/\/export|\.csv(?:\?|$)/.test(path)?app.download(path,name):app.preview(path,name);
    operation.catch(error=>app.notify(error.message));
  }
  document.addEventListener('click',event=>{
    const input=event.target.closest?.('input[type=file]');
    if(input){event.preventDefault();event.stopImmediatePropagation();choose(input);return;}
    const link=event.target.closest?.('a[href]');if(link)dispatchLink(link,event);
  },true);
  // Keyboard, programmatic click(), and label activation all pass through the capture handler.
  document.addEventListener('reset',event=>{for(const input of event.target.querySelectorAll('input[type=file]'))selected.set(input,[]);});
  window.open=(url)=>{const link=document.createElement('a');link.href=url;dispatchLink(link,{preventDefault(){},stopImmediatePropagation(){}});return null;};
  function loadImages(root){
    const images=[...(root.querySelectorAll?.('img[src]')||[])];if(root.matches?.('img[src]'))images.push(root);
    for(const img of images){
      const src=img.getAttribute('src');if(!src||/^(data:|blob:)/.test(src)||src.startsWith('/static/')||img.dataset.nativeLoading===src)continue;
      const u=new URL(src,window.XTYFLocation.href);
      if(!app.isLocal(u)&&(!app.config.serverUrl||u.origin!==new URL(app.config.serverUrl).origin)){img.removeAttribute('src');img.alt+='（外部图片未加载）';continue;}
      img.dataset.nativeLoading=src;
      app.imageURL(u.pathname+u.search).then(url=>{if(own===app.generation&&img.isConnected)img.src=url;}).catch(error=>{if(error.name!=='AbortError'){img.alt+='（点击重试）';img.onclick=()=>{delete img.dataset.nativeLoading;loadImages(img);};}});
    }
    for(const input of root.querySelectorAll?.('input[type=file]')||[])fileInput(input);
    if(root.matches?.('input[type=file]'))fileInput(root);
  }
  const observer=new MutationObserver(items=>{for(const item of items){if(item.type==='attributes')loadImages(item.target);else for(const node of item.addedNodes)if(node.nodeType===1)loadImages(node);}});
  observer.observe(document.documentElement,{subtree:true,childList:true,attributes:true,attributeFilter:['src']});
  document.addEventListener('DOMContentLoaded',()=>{
    loadImages(document);
    // Keep client settings reachable without introducing a second navigation toolbar.
    if(window.XTYFLocation.pathname==='/login'){
      const form=document.getElementById('login-form'),link=document.createElement('button');link.type='button';link.className='btn block';link.textContent='服务器地址设置';link.onclick=app.openSettings;form?.append(link);
    }
  });
})();
