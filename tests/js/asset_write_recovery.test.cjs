'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('static/assets.js','utf8');
function fixture(storage=new Map()){
  const nodes=new Map(),events=[];
  const ctx={TextEncoder,catalog:{member_id:1,write_receipts:true},pending:false,pendingKey:'synthetic-server',
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
test('BOM receipts must account for original rows, aggregate quantities and newly created IDs',()=>{
  const {ctx}=fixture(),body={request_id:'original',mode:'inbound',sets:1,rows:[{item_id:8,quantity:2},{item_id:8,quantity:3}]};
  const base={ok:true,request_id:'original',rows:2,sets:1,created:[],row_items:[8,8],operations:[{item_id:8,quantity:5,action:'receive',event_id:9}]};
  assert.equal(ctx.validWriteReceipt('bom/commit',body,base),true);
  for(const change of [{rows:1},{sets:2},{operations:[]},{row_items:[8]},{operations:[{...base.operations[0],quantity:4}]},{operations:[base.operations[0],base.operations[0]]}]){
    assert.equal(ctx.validWriteReceipt('bom/commit',body,{...base,...change}),false);
  }
  const {row_items,...legacy}=base;assert.equal(ctx.validWriteReceipt('bom/commit',body,legacy),true);
});
test('scan batches and unit operations reject truncated, duplicate and wrong identities',()=>{
  const {ctx}=fixture(),body={request_id:'original',receipts:[{entry_id:'a',item_id:1,quantity:2},{entry_id:'b',item_id:2,quantity:3}]};
  const receipts=[{receipt_id:1,event_id:4,item_id:1,entry_id:'a',actual_quantity:2},{receipt_id:2,event_id:5,item_id:2,entry_id:'b',actual_quantity:3}];
  const base={ok:true,request_id:'original'};
  assert.equal(ctx.validWriteReceipt('scan/batch',body,{...base,receipts}),true);
  for(const bad of [receipts.slice(0,1),[receipts[0],receipts[0]],[receipts[0],{...receipts[1],actual_quantity:2}]])assert.equal(ctx.validWriteReceipt('scan/batch',body,{...base,receipts:bad}),false);
  const units={request_id:'original',units:[{id:1},{id:2}]};
  assert.equal(ctx.validWriteReceipt('units/operate',units,{...base,unit_ids:[2,1],event_ids:[4,5]}),true);
  for(const ids of [[1],[1,1],[1,3]])assert.equal(ctx.validWriteReceipt('units/operate',units,{...base,unit_ids:ids,event_ids:[4,5]}),false);
});
test('category no-op and stocktake batch receipts retain their legitimate shapes',()=>{
  const {ctx}=fixture(),base={ok:true,request_id:'original'};
  const body={request_id:'original',items:[{id:1},{id:2}],category_id:null};
  for(const changed of [[],[1,2]])assert.equal(ctx.validWriteReceipt('categories/assign',body,{...base,changed,selected:2,category_id:null}),true);
  for(const change of [{selected:1},{changed:[3]},{changed:[1,1]}])assert.equal(ctx.validWriteReceipt('categories/assign',body,{...base,changed:[],selected:2,category_id:null,...change}),false);
  const take={request_id:'original',unit_ids:[1,2]};
  assert.equal(ctx.validWriteReceipt('units/stocktakes/scan-batch',take,{...base,events:[{event_id:3,unit_id:1},{event_id:4,unit_id:2}]}),true);
  for(const events of [[],[{event_id:3}],[{event_id:3},{event_id:3}]])assert.equal(ctx.validWriteReceipt('units/stocktakes/scan-batch',take,{...base,events}),false);
});
test('only first-attempt marked prewrite 413 clears; reload and earlier uncertainty never do',async()=>{
  const f=fixture(),saved={actor_id:1,path:'operations',request_id:'original',body:{quantity:3}};
  f.ctx.rememberPending(saved);
  const rejection=async()=>({ok:false,status:413,json:async()=>({ok:false,reason:'request_too_large',not_applied:true,error:'too large'})});
  f.ctx.fetch=rejection;
  await assert.rejects(f.ctx.api(saved.path,{...saved.body,request_id:'original'}),e=>e.businessRejected===true);
  await assert.rejects(f.ctx.api(saved.path,{...saved.body,request_id:'original'}),e=>!e.businessRejected);
  const reloaded=fixture(f.storage);reloaded.ctx.fetch=rejection;
  await assert.rejects(reloaded.ctx.api(saved.path,{...saved.body,request_id:'original'}),e=>!e.businessRejected);
});
test('preflight uses UTF-8 limits and does not persist oversized originals',()=>{
  const f=fixture();assert.throws(()=>f.ctx.rememberPending({actor_id:1,path:'bom/commit',request_id:'original',body:{reason:'文'.repeat(6000)}}),/大小/);
  assert.equal(f.storage.size,0);
});
