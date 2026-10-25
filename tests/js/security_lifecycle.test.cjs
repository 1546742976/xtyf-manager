'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function fixture(){
  const listeners={},calls=[];
  const window={addEventListener(name,fn){listeners[name]=fn;},fetch:async(input,options)=>{
    calls.push({input,options});return new Response('{}',{status:200});
  }};
  vm.runInNewContext(fs.readFileSync('static/security.js','utf8'),{
    window,URL,Request,Headers,DOMException,location:{href:'http://local/assets',origin:'http://local',pathname:'/assets'},
    document:{querySelector:()=>({content:'synthetic-csrf'}),querySelectorAll:()=>[]},
  });
  return {calls,fetch:window.fetch,emit:name=>listeners[name]?.()};
}
test('late same-origin reads never reach native fetch after pagehide; pageshow restores reads',async()=>{
  const f=fixture();await f.fetch('/api/assets/catalog');f.emit('pagehide');
  for(const method of ['GET','HEAD'])await assert.rejects(f.fetch('/api/admin/audit',{method}),{name:'AbortError'});
  assert.equal(f.calls.length,1);
  f.emit('pageshow');await f.fetch('/api/assets/items');assert.equal(f.calls.length,2);
});
test('navigation guard preserves write CSRF and existing read cancellation signals',async()=>{
  const f=fixture(),controller=new AbortController();
  await f.fetch('/api/assets/items',{signal:controller.signal});
  assert.equal(f.calls[0].options.signal,controller.signal);
  f.emit('pagehide');await f.fetch('/api/assets/operations',{method:'POST',body:'{}'});
  assert.equal(f.calls[1].options.headers.get('X-CSRF-Token'),'synthetic-csrf');
});
