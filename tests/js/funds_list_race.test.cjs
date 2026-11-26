'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function fixture(){
  const requests=[],events={},nodes=new Map(),messages=[];
  const get=id=>{if(!nodes.has(id))nodes.set(id,{value:'',style:{},hidden:true,attributes:{},setAttribute(name,value){this.attributes[name]=value;},innerHTML:'',textContent:''});return nodes.get(id);};
  const c={URLSearchParams,AbortController,XTYF_USER:{name:'Synthetic'},Money:{format:String},
    addEventListener:(name,f)=>events[name]=f,
    document:{addEventListener(){},getElementById:get},
    BusinessUI:{escape:String,guard:f=>f,fetch:(url,options)=>new Promise((resolve,reject)=>requests.push({url,options,resolve,reject}))}};
  c.window=c;vm.createContext(c);vm.runInContext(fs.readFileSync('static/funds.js','utf8'),c);
  c.loadLedgerStatus=()=>{};c.fundsToast=message=>messages.push(message);
  const respond=(index,id)=>requests[index].resolve({json:async()=>({code:0,data:[{id,amount:1,record_date:'2026-10-27'}]})});
  return {c,get,requests,events,messages,respond};
}
test('late old-filter success never replaces newer rows even if abort is ignored',async()=>{
  const f=fixture();f.get('fkw').value='old';const old=f.c.reload();
  f.get('fkw').value='new';const current=f.c.reload();assert.equal(f.requests[0].options.signal.aborted,true);
  f.respond(1,222);await current;f.respond(0,111);await old;
  assert.match(f.get('tbody').innerHTML,/<td>222<\/td>/);assert.doesNotMatch(f.get('tbody').innerHTML,/<td>111<\/td>/);
  assert.match(f.requests[1].url,/keyword=new/);
});
test('stale errors stay quiet while current errors are recoverable',async()=>{
  const f=fixture(),old=f.c.reload(),current=f.c.reload();
  f.requests[0].reject(Error('old'));await old;assert.equal(f.messages.length,0);
  f.requests[1].reject(Error('current'));await current;assert.match(f.get('funds-list-error-message').textContent,/current/);
  const retry=f.c.reload();f.respond(2,333);await retry;assert.match(f.get('tbody').innerHTML,/<td>333<\/td>/);
});
test('leaving cancels reads, ignores late results and back-forward restore reloads',async()=>{
  const f=fixture(),old=f.c.reload();f.events.pagehide();
  assert.equal(f.requests[0].options.signal.aborted,true);f.respond(0,111);await old;
  assert.equal(f.get('tbody').innerHTML,'');await f.c.reload();assert.equal(f.requests.length,1);
  f.events.pageshow({persisted:true});assert.equal(f.requests.length,2);
  f.respond(1,222);await new Promise(setImmediate);assert.match(f.get('tbody').innerHTML,/<td>222<\/td>/);
});


test('new filters clear old rows and count, show loading then independent retryable failure',async()=>{
  const f=fixture(),initial=f.c.reload();f.respond(0,111);await initial;
  const next=f.c.reload();assert.equal(f.get('tbody').innerHTML,'');assert.equal(f.get('rcnt').textContent,'');
  assert.equal(f.get('funds-list-loading').hidden,false);assert.equal(f.get('empty').style.display,'none');
  f.requests[1].reject(Error('synthetic latest error'));await next;
  assert.equal(f.get('funds-list-loading').hidden,true);assert.equal(f.get('funds-list-error').hidden,false);
  assert.equal(f.get('rcnt').textContent,'');assert.equal(f.get('tbody').innerHTML,'');
  const retry=f.c.reload();assert.equal(f.get('funds-list-error').hidden,true);
  f.respond(2,222);await retry;assert.match(f.get('tbody').innerHTML,/<td>222<\/td>/);
});

test('late success and error cannot hide latest loading or overwrite latest error',async()=>{
  const f=fixture(),old=f.c.reload(),current=f.c.reload();
  f.respond(0,111);await old;assert.equal(f.get('funds-list-loading').hidden,false);
  f.requests[1].reject(Error('latest'));await current;
  assert.equal(f.get('funds-list-error').hidden,false);assert.match(f.get('funds-list-error-message').textContent,/latest/);
});
