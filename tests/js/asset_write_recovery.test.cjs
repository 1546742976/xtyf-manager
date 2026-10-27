'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('static/assets.js','utf8');
function fixture(storage=new Map()){
  const nodes=new Map(),events=[];
  const ctx={catalog:{member_id:1,write_receipts:true},pending:false,pendingKey:'synthetic-server',
    $:id=>{if(!nodes.has(id))nodes.set(id,{});return nodes.get(id);},
    document:{dispatchEvent:e=>events.push(e)},CustomEvent:class{constructor(type,o){this.type=type;this.detail=o.detail;}},
    sessionStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
    fetch:async()=>({ok:true,status:200,json:async()=>({ok:true,event_id:1,request_id:'original'})})};
  vm.createContext(ctx);
  vm.runInContext(source.slice(source.indexOf('  function readPending'),source.indexOf('  const dialog=')),ctx);
  vm.runInContext(source.slice(source.indexOf('  function validWriteReceipt'),source.indexOf('  const button=')),ctx);
  return {ctx,storage,events};
}
test('authentication, permissions, throttling and server errors retain original request across reload',async()=>{
  const f=fixture();const saved={actor_id:1,path:'operations',request_id:'original',body:{quantity:3}};
  f.ctx.rememberPending(saved);
  for(const status of [401,403,404,408,429,500]){
    f.ctx.fetch=async()=>({ok:false,status,json:async()=>({ok:false,error:'synthetic rejection'})});
    await assert.rejects(f.ctx.api(saved.path,{...saved.body,request_id:saved.request_id}),e=>!e.businessRejected);
    const reload=fixture(f.storage);assert.equal(reload.ctx.readPending().request_id,'original');
  }
});
test('only definite business refusals permit clearing',async()=>{
  const {ctx}=fixture();ctx.fetch=async()=>({ok:false,status:409,json:async()=>({ok:false,error:'stock changed'})});
  await assert.rejects(ctx.api('operations',{request_id:'original'}),e=>e.businessRejected===true);
});
test('invalid, mismatched and incomplete success receipts remain uncertain',async()=>{
  const {ctx}=fixture();
  for(const data of [{ok:true},{ok:true,event_id:1},{ok:true,event_id:1,request_id:'other'},
    {ok:true,request_id:'original'},{ok:true,request_id:'original',event_id:-1}]){
    ctx.fetch=async()=>({ok:true,status:200,json:async()=>data});
    await assert.rejects(ctx.api('operations',{request_id:'original'}),e=>!e.businessRejected);
  }
  ctx.fetch=async()=>({ok:true,status:200,json:async()=>{throw Error('truncated JSON');}});
  await assert.rejects(ctx.api('operations',{request_id:'original'}),e=>!e.businessRejected);
});
test('current receipts correlate; complete legacy server receipts remain usable',async()=>{
  const {ctx}=fixture();assert.equal((await ctx.api('operations',{request_id:'original'})).event_id,1);
  ctx.catalog.write_receipts=false;
  ctx.fetch=async()=>({ok:true,status:200,json:async()=>({ok:true,event_id:2})});
  assert.equal((await ctx.api('operations',{request_id:'original'})).event_id,2);
});
test('BOM and ordinary writes cannot overwrite one another; clearing notifies the editor',()=>{
  const f=fixture();const saved={actor_id:1,path:'bom/commit',request_id:'bom-original',body:{rows:[{quantity:3}]}};
  f.ctx.rememberPending(saved);
  assert.throws(()=>f.ctx.rememberPending({...saved,request_id:'replacement'}));
  assert.equal(f.ctx.readPending().path,'bom/commit');
  f.ctx.forgetPending();assert.equal(f.ctx.readPending(),null);
  assert.equal(f.events[0].detail.request_id,'bom-original');
});
test('BOM acknowledgement requires its actual operation results',()=>{
  const {ctx}=fixture(),body={request_id:'original'},base={ok:true,request_id:'original'};
  assert.equal(ctx.validWriteReceipt('bom/commit',body,base),false);
  assert.equal(ctx.validWriteReceipt('bom/commit',body,{...base,rows:1,operations:[{event_id:2}]}),true);
});

test('category and stocktake receipts follow the existing API result shapes',()=>{
  const {ctx}=fixture(),body={request_id:'original'},base={ok:true,request_id:'original'};
  for(const changed of [[],[1,2]])assert.equal(ctx.validWriteReceipt('categories/assign',body,{...base,changed,selected:2}),true);
  for(const changed of [0,2,[-1],[null]])assert.equal(ctx.validWriteReceipt('categories/assign',body,{...base,changed,selected:2}),false);
  assert.equal(ctx.validWriteReceipt('units/stocktakes/scan-batch',body,{...base,events:[{event_id:3},{event_id:4}]}),true);
  for(const events of [[],[3],[{}],[{event_id:0}]])assert.equal(ctx.validWriteReceipt('units/stocktakes/scan-batch',body,{...base,events}),false);
});
