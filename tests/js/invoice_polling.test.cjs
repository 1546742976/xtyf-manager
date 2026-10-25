const test=require('node:test');
const assert=require('node:assert/strict');
const {create}=require('../../static/invoice-polling.js');

function fixture(){
  const timers=[],requests=[],results=[],errors=[],lists=[];
  let jobs=[],detail={id:'job-1',status:'done',progress:100,result:{}},visible=true,handler=null;
  const api=create({fetch:async url=>{
    requests.push(url);if(handler)return handler(url);
    return {ok:true,status:200,json:async()=>url.endsWith('/jobs')?{jobs}:detail};
  },enabled:()=>visible,setTimer:(fn,ms)=>{timers.push({fn,ms});return timers.length;},clearTimer:()=>{},
  onList:v=>lists.push(v),onResult:(id,data,callback)=>{results.push(data);callback?.(data);},
  onError:e=>errors.push(e.message),onAuth:()=>errors.push('auth')});
  return {api,timers,requests,results,errors,lists,setJobs:v=>jobs=v,setDetail:v=>detail=v,setVisible:v=>visible=v,setFetch:v=>handler=v};
}

test('a watched job missing from the list is resolved by detail, once only',async()=>{
  const f=fixture();let callbacks=0;
  await f.api.watch('job-1',()=>callbacks++);
  assert.deepEqual(f.requests,['/invoices/api/jobs','/invoices/api/job/job-1']);
  assert.equal(callbacks,1);assert.equal(f.results.length,1);
  await f.api.refresh();await f.api.watch('job-1',()=>callbacks++);
  assert.equal(callbacks,1);assert.equal(f.timers.at(-1).ms,15000);
});

test('active tasks use three seconds; errors back off and success resets the delay',async()=>{
  const f=fixture();f.setJobs([{id:'job-2',status:'running',progress:5}]);
  await f.api.refresh();assert.equal(f.timers.at(-1).ms,3000);
  f.setFetch(()=>{throw Error('offline');});
  for(const ms of [5000,10000,20000,30000,30000]){
    await f.api.refresh();assert.equal(f.timers.at(-1).ms,ms);
  }
  f.setFetch(null);await f.api.refresh();assert.equal(f.timers.at(-1).ms,3000);
  f.setFetch(()=>{throw Error('offline');});await f.api.refresh();assert.equal(f.timers.at(-1).ms,5000);
});

test('hidden/offline pause ignores late responses and never overlaps requests on resume',async()=>{
  const f=fixture();let resolve;
  f.setFetch(()=>new Promise(r=>resolve=r));
  const first=f.api.refresh();await f.api.refresh();
  assert.equal(f.requests.length,1);
  f.setVisible(false);f.api.sync();await f.api.refresh();
  f.setVisible(true);f.api.sync();assert.equal(f.requests.length,1);
  resolve({ok:true,status:200,json:async()=>({jobs:[]})});await first;
  assert.equal(f.lists.length,0);assert.equal(f.timers.at(-1).ms,0);
  f.setFetch(null);await f.timers.at(-1).fn();assert.equal(f.requests.length,2);
});

test('authentication failure stops polling, invalid data retains watched jobs for retry',async()=>{
  for(const status of [401,403]){
    const f=fixture();f.setFetch(()=>({ok:false,status}));
    await f.api.refresh();await f.api.refresh();
    assert.deepEqual(f.errors,['auth']);assert.equal(f.requests.length,1);assert.equal(f.timers.length,0);
  }
  const f=fixture();f.setDetail({id:'wrong',status:'done',progress:100});
  await f.api.watch('job-1');assert.equal(f.results.length,0);assert.equal(f.timers.at(-1).ms,5000);
  f.setDetail({id:'job-1',status:'done',progress:100});await f.api.refresh();assert.equal(f.results.length,1);
});

test('dispose aborts an in-flight request and a late terminal response has no effect',async()=>{
  let resolve,signal;const results=[];
  const api=create({enabled:()=>true,fetch:(_,init)=>{signal=init.signal;return new Promise(r=>resolve=r);},
    onList:()=>results.push('list'),onResult:()=>results.push('result'),onError:()=>results.push('error'),onAuth:()=>{},
    setTimer:()=>results.push('timer'),clearTimer:()=>{}});
  const pending=api.watch('job-1');api.dispose();assert.equal(signal.aborted,true);
  resolve({ok:true,status:200,json:async()=>({jobs:[{id:'job-1',status:'done',progress:100}]})});
  await pending;assert.deepEqual(results,[]);
});
