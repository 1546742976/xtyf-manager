"use strict";
const test=require('node:test'), assert=require('node:assert/strict');
const fs=require('node:fs'), path=require('node:path'), vm=require('node:vm');
function setup(fetch) {
  const ctx={fetch, FormData, AbortController, TypeError, Error, setTimeout, clearTimeout,
    location:{pathname:'/login'}, document:{addEventListener(){},querySelectorAll(){return [];}}, URL};
  ctx.window=ctx;vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../../static/business-ui.js'),'utf8'),ctx);
  return ctx.BusinessUI;
}
test('HTTP failures cannot masquerade as successful business operations',async()=>{
  for(const status of [401,403,409,429,500,503]) {
    const ui=setup(async()=>new Response(JSON.stringify({ok:true,success:true,code:0}),{status}));
    const response=await ui.fetch('/api/example',{method:'POST'}), result=await response.json();
    assert.equal(response.status,status);assert.equal(result.ok,false);assert.equal(result.success,false);
    assert.notEqual(result.code,0);assert.ok(result.error);
  }
});
test('malformed successful response and non-JSON HTTP errors produce readable feedback',async()=>{
  const invalid=setup(async()=>new Response('<html>error</html>'));
  await assert.rejects(invalid.fetch('/api/example'),/响应格式异常/);
  const failed=setup(async()=>new Response('<html>error</html>',{status:503}));
  assert.match((await (await failed.fetch('/api/example')).json()).error,/503/);
});
test('network failures distinguish uncertain writes and never retry automatically',async()=>{
  let calls=0;
  const ui=setup(async()=>{calls++;throw new TypeError('Failed to fetch');});
  await assert.rejects(ui.fetch('/api/example',{method:'POST'}),/结果尚未确认.*避免重复操作/);
  assert.equal(calls,1);
  await assert.rejects(ui.fetch('/api/example'),/检查网络后重试/);
});
test('deadline covers a stalled response body, not only response headers',async()=>{
  const ui=setup(async(_url,{signal})=>({ok:true,status:200,json:()=>new Promise((_,reject)=>{
    signal.addEventListener('abort',()=>reject(Object.assign(new Error('abort'),{name:'AbortError'})));
  })}));
  await assert.rejects(ui.fetch('/api/example',{timeoutMs:10}),/请求超时/);
});
test('action boundary blocks duplicate work and restores controls after failure',async()=>{
  const ui=setup(), attributes={};
  const button={disabled:false,getAttribute:key=>attributes[key]??null,setAttribute:(key,value)=>attributes[key]=value,removeAttribute:key=>delete attributes[key]};
  let reject, calls=0, feedback='';
  const operation=()=>{calls++;return new Promise((_,fail)=>reject=fail);};
  const one=ui.action('save',[button],operation,text=>feedback=text);
  assert.equal(calls,1,'operation must capture live event/file values before returning');
  const two=ui.action('save',[button],operation);
  assert.equal(one,two);assert.equal(button.disabled,true);
  await Promise.resolve();reject(new Error('保存失败'));await one;
  assert.equal(calls,1);assert.equal(button.disabled,false);assert.equal(attributes['aria-busy'],undefined);
  assert.equal(feedback,'保存失败');
  await ui.action('save',[button],async()=>{calls++;});assert.equal(calls,2);
});
