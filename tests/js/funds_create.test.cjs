const test = require('node:test');
const assert = require('node:assert/strict');
const {create} = require('../../static/funds-create.js');

function storage() {
  const values = new Map();
  return {get length(){return values.size;}, key(i){return [...values.keys()][i];},
    getItem(k){return values.get(k) ?? null;}, setItem(k,v){values.set(k,v);}, removeItem(k){values.delete(k);}};
}
function response(status, data) { return {ok:status<400,status,json:async()=>data}; }
function setup() {
  const store=storage(), posts=[];
  let server='https://a.invalid', member=1, mode='lost', serial=0;
  const fetch=async(url,init)=>{
    if(!init)return response(200,{code:0,create_idempotency:1,member_id:member});
    const body=JSON.parse(init.body);posts.push(body);
    if(mode==='lost')throw Error('truncated JSON');
    if(mode==='denied')return response(403,{code:1,msg:'权限已变化'});
    if(mode==='invalid')return response(200,{code:0});
    if(mode==='rejected')return response(400,{code:1,request_id:body.request_id,reason:'validation_failed',msg:'合成业务拒绝'});
    return response(200,{code:0,id:7,version:1,request_id:body.request_id,replayed:posts.length>1,ledger_sync:'ok'});
  };
  const factory=()=>create({storage:store,fetch,server:()=>server,uuid:()=>String(++serial).padStart(32,'0')});
  return {factory,store,posts,setMode:v=>mode=v,setServer:v=>server=v,setMember:v=>member=v};
}

test('lost response survives refresh with its immutable request id and body',async()=>{
  const env=setup();let client=env.factory();await client.connect();
  const entry=client.prepare({amount:'10.00'});
  assert.equal((await client.send(entry)).state,'pending');
  client=env.factory();await client.connect();env.setMode('ok');
  assert.equal((await client.send(client.pending()[0])).state,'confirmed');
  assert.deepEqual(env.posts[0],env.posts[1]);assert.equal(client.pending().length,0);
});

test('invalid receipt or permission failure keeps the original request',async()=>{
  for(const mode of ['invalid','denied']){
    const env=setup(), client=env.factory();await client.connect();const entry=client.prepare({amount:'10'});
    env.setMode(mode);assert.equal((await client.send(entry)).state,'pending');assert.equal(client.pending().length,1);
  }
});

test('storage failure prevents submission; account and server changes cannot reuse pending requests',async()=>{
  const env=setup(),client=env.factory();await client.connect();const entry=client.prepare({amount:'10'});
  env.setMember(2);assert.equal((await client.send(entry)).state,'pending');assert.equal(env.posts.length,0);
  const another=env.factory();await another.connect();assert.equal(another.pending().length,0);
  env.setServer('https://b.invalid');env.setMember(1);await another.connect();assert.equal(another.pending().length,0);
  env.store.setItem=()=>{throw Error('quota');};assert.throws(()=>another.prepare({amount:'10'}));assert.equal(env.posts.length,0);
});

test('only explicit business rejection clears the pending request and allows a new intent',async()=>{
  const env=setup(),client=env.factory();await client.connect();const first=client.prepare({amount:'0'});
  env.setMode('rejected');assert.equal((await client.send(first)).state,'rejected');
  assert.equal(client.pending().length,0);
  assert.notEqual(client.prepare({amount:'10'}).body.request_id,first.body.request_id);
});

test('concurrent clicks share one in-flight submission',async()=>{
  const env=setup(),client=env.factory();await client.connect();const entry=client.prepare({amount:'10'});
  env.setMode('ok');const results=await Promise.all([client.send(entry),client.send(entry)]);
  assert.deepEqual(results.map(r=>r.state),['confirmed','busy']);assert.equal(env.posts.length,1);
});
