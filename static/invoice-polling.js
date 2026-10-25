(function(root,factory){
  const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.InvoiceJobPolling=api;
})(typeof window==='undefined'?globalThis:window,function(){
  'use strict';
  const terminal=new Set(['done','error','interrupted']);
  const states=new Set(['queued','running',...terminal]);
  function create(options){
    const watched=new Map(),finished=new Set();
    const setTimer=options.setTimer||setTimeout,clearTimer=options.clearTimer||clearTimeout;
    let timer=null,running=false,again=false,epoch=0,controller=null,failures=0,stopped=false,disposed=false;
    const enabled=()=>!disposed&&!stopped&&options.enabled();
    function cancelTimer(){if(timer!==null)clearTimer(timer);timer=null;}
    function pause(){cancelTimer();epoch++;again=false;controller?.abort();}
    function validJob(job){return job&&typeof job.id==='string'&&states.has(job.status)&&
      Number.isFinite(job.progress)&&job.progress>=0&&job.progress<=100;}
    async function read(url,signal){
      const response=await options.fetch(url,{signal});
      if(!response.ok){const error=new Error(response.status===404?'任务暂不可见，请重新核对':'任务进度读取失败');error.status=response.status;throw error;}
      return response.json();
    }
    async function refresh(){
      cancelTimer();
      if(!enabled())return;
      if(running){again=true;return;}
      running=true;again=false;
      const current=epoch;controller=new AbortController();
      const signal=controller.signal,isCurrent=()=>current===epoch&&enabled();
      let delay=15000;
      try{
        const data=await read('/invoices/api/jobs',signal);
        if(!isCurrent())return;
        if(!Array.isArray(data.jobs)||!data.jobs.every(validJob))throw Error('任务列表响应格式异常');
        options.onList(data.jobs);
        for(const [id,callback] of watched){
          const listed=data.jobs.find(job=>job.id===id);
          if(listed&&!terminal.has(listed.status))continue;
          const detail=await read('/invoices/api/job/'+encodeURIComponent(id),signal);
          if(!isCurrent())return;
          if(!validJob(detail)||detail.id!==id)throw Error('任务详情响应格式异常');
          if(terminal.has(detail.status)){
            watched.delete(id);finished.add(id);
            options.onResult(id,detail,callback);
          }
        }
        failures=0;
        if(watched.size||data.jobs.some(job=>!terminal.has(job.status)))delay=3000;
      }catch(error){
        if(!isCurrent())return;
        if(error.status===401||error.status===403){stopped=true;options.onAuth();return;}
        delay=[5000,10000,20000,30000][Math.min(failures++,3)];
        options.onError(error);
      }finally{
        running=false;controller=null;
        if(enabled()){
          if(again){again=false;timer=setTimer(refresh,0);}
          else timer=setTimer(refresh,delay);
        }
      }
    }
    return {
      refresh,pause,
      sync(){if(enabled())return refresh();pause();},
      watch(id,callback){
        if(!finished.has(id)){
          if(!watched.has(id)||callback)watched.set(id,callback);
          return refresh();
        }
      },
      dispose(){disposed=true;pause();watched.clear();}
    };
  }
  return {create};
});
