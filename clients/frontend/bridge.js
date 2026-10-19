/* Only the packaged root document receives a native message port. */
(() => {
  const pending=new Map();let port=null,sequence=0,readyResolve;
  const ready=new Promise(resolve=>{readyResolve=resolve;});
  function nativeError(error){
    if(error instanceof Error)return error;
    const message=typeof error==='string'?error:error?.message;
    return new Error(typeof message==='string'&&message?message:'原生操作失败');
  }
  window.addEventListener('message',event=>{
    if(event.data!=='xtyf-native-port'||!event.ports?.[0]||port)return;
    // Android's native postWebMessage has an empty/null source. A web frame may not replace it.
    if(event.source&&event.source!==window)return;
    port=event.ports[0];
    port.onmessage=event=>{
      let message;try{message=JSON.parse(event.data);}catch(_){return;}
      const callback=pending.get(message.id);if(!callback)return;
      pending.delete(message.id);
      if(message.error)callback.reject(nativeError(message.error));
      else callback.resolve(message.result);
    };port.start?.();readyResolve();
  });
  const existing=window.XTYFNative;
  async function invoke(action,args={}) {
    try{
      if(existing?.invoke)return await existing.invoke(action,args);
      if(window.__TAURI_INTERNALS__?.invoke)return await window.__TAURI_INTERNALS__.invoke('native_call',{action,args});
      if(!port)await Promise.race([ready,new Promise((_,reject)=>setTimeout(()=>reject(new Error('客户端连接尚未就绪，请重试。')),12000))]);
      return await new Promise((resolve,reject)=>{
        const id=String(++sequence);pending.set(id,{resolve,reject});
        try{port.postMessage(JSON.stringify({id,action,args}));}catch(error){pending.delete(id);reject(error);}
      });
    }catch(error){throw nativeError(error);}
  }
  window.XTYFNative={invoke};
})();
