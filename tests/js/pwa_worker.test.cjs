'use strict';
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm'), crypto = require('node:crypto');
const origin = 'https://manager.example.invalid';
const offline = '/static/pwa/offline.html', publicJS = '/static/pwa.js';
function fixture() {
  const bodies = {[offline]: fs.readFileSync('static/pwa/offline.html'), [publicJS]: fs.readFileSync('static/pwa.js')};
  const types = {[offline]:'text/html', [publicJS]:'text/javascript'};
  const config = {version:'test-one', resources:Object.fromEntries(Object.entries(bodies).map(([name,data])=>[name,{
    sha256:crypto.createHash('sha256').update(data).digest('hex'),size:data.length,type:types[name],
  }]))};
  const handlers = {}, stores = new Map(), deleted = [], calls = [];
  const key = url => new URL(typeof url==='string'?url:url.url, origin).href;
  const caches = {
    async open(name) {
      if(!stores.has(name)) stores.set(name,new Map());
      const rows = stores.get(name);
      return {async put(url,response) { rows.set(key(url),response.clone()); },async match(url) { return rows.get(key(url))?.clone(); }};
    },
    async keys() { return [...stores.keys()]; },
    async match(url,{cacheName}) { return stores.get(cacheName)?.get(key(url))?.clone(); },
    async delete(name) { deleted.push(name); return stores.delete(name); },
  };
  let network = async url => new Response(bodies[new URL(url,origin).pathname],{headers:{'Content-Type':types[new URL(url,origin).pathname]}});
  vm.runInNewContext(fs.readFileSync('static/pwa/sw.js','utf8').replace('__PWA_CONFIG__',JSON.stringify(config)),{
    self:{location:{origin},addEventListener:(name,fn)=>handlers[name]=fn,clients:{claim:async()=>{}}},
    caches,URL,Response,Uint8Array,crypto:crypto.webcrypto,
    fetch:async (input,options)=>{calls.push({input,options});return network(input,options);},
  });
  const dispatch = async (type,request) => {
    let response;const pending=[];
    handlers[type]({request,waitUntil:p=>pending.push(p),respondWith:p=>{response=p;}});
    await Promise.all(pending);
    return response;
  };
  const request = (path,options={})=>({url:new URL(path,origin).href,method:'GET',mode:'cors',destination:'',headers:new Headers(),...options});
  return {caches,stores,deleted,calls,config,request,dispatch,setNetwork:fn=>network=fn,
    read:async (path,options)=>dispatch('fetch',request(path,options)),url:path=>path+'?v=test-one'};
}
test('only exact revisioned public GETs enter the finite cache; credentials are omitted',async()=>{
 const f=fixture();await f.dispatch('install');
 const response=await f.read(f.url(publicJS));assert.equal(response.status,200);
 assert.equal(f.stores.get('xtyf-pwa-public-test-one').size,2);
 assert.ok(f.calls.every(c=>c.options.credentials==='omit'&&c.options.cache==='no-cache'));
 for(const [path,options] of [
  ['/',{}],['/login',{}],['/api/data',{}],['/api/account/preferences',{}],['/logout',{}],
  ['/invoices/api/preview/1',{}],['/api/task/1/attachments/1',{}],['/static/avatars/1.png',{}],
  ['/static/asset-camera.js',{}],[publicJS,{}],[publicJS+'?v=old',{}],[f.url(publicJS)+'&user=1',{}],
  [f.url(publicJS),{method:'POST'}],[f.url(publicJS),{headers:new Headers({Range:'bytes=0-4'})}],
  [f.url(publicJS),{headers:new Headers({Authorization:'Bearer synthetic'})}],
  ['https://other.example.invalid'+f.url(publicJS),{}],
  ['/api/data',{mode:'navigate',destination:'document'}],
  ['/invoices/api/export/download/1',{mode:'navigate',destination:'document'}],
 ]) assert.equal(await f.read(path,options),undefined,path);
 assert.equal(f.stores.get('xtyf-pwa-public-test-one').size,2);
});
test('dynamic navigation never caches HTML and offline navigation has no account data; APIs fail normally',async()=>{
 const f=fixture();await f.dispatch('install');
 f.setNetwork(async()=>new Response('PRIVATE ACCOUNT A',{headers:{'Content-Type':'text/html','Cache-Control':'no-store'}}));
 assert.equal(await (await f.read('/tasks',{mode:'navigate',destination:'document'})).text(),'PRIVATE ACCOUNT A');
 assert.equal(f.calls.at(-1).options.cache,'no-store');
 assert.equal(f.stores.get('xtyf-pwa-public-test-one').size,2);
 f.setNetwork(async()=>{throw new TypeError('offline');});
 const fallback=await f.read('/tasks?private=account-A',{mode:'navigate',destination:'document'});
 assert.match(await fallback.text(),/当前离线，业务操作需联网/);
 assert.equal(await f.read('/api/data'),undefined);
 assert.equal(await f.read('/api/csrf'),undefined);
 assert.ok([...f.stores.get('xtyf-pwa-public-test-one').keys()].every(url=>!url.includes('private')));
});
test('wrong hash, MIME, cookies, privacy directives, errors and oversized responses prevent installation',async()=>{
 const cases=[{body:'untrusted'}, {headers:{'Content-Type':'text/html'}}, {headers:{'Set-Cookie':'synthetic=1'}},
  {headers:{'Cache-Control':'no-store'}},{headers:{'Cache-Control':'private'}},{headers:{'Vary':'Cookie'}},
  {headers:{'Vary':'Authorization'}},{headers:{'X-CSRF-Token':'synthetic'}},{headers:{'Vary':'*'}},
  {body:Buffer.alloc(1024*1024)},{headers:{'Content-Length':'9999999'}},{status:503}];
 for(const item of cases){
  const f=fixture();
  f.setNetwork(async url=>new URL(url,origin).pathname===offline
   ?new Response(fs.readFileSync('static/pwa/offline.html'),{headers:{'Content-Type':'text/html'}})
   :new Response(item.body||fs.readFileSync('static/pwa.js'),{status:item.status||200,headers:{'Content-Type':'text/javascript',...item.headers}}));
  await assert.rejects(f.dispatch('install'),/Invalid public install resource/);
  assert.equal(f.stores.size,0,JSON.stringify(item.headers||item.status||'bytes'));
 }
});
test('compressed transport lengths do not reject correct decoded public bytes',async()=>{
 const f=fixture();
 f.setNetwork(async url=>{
  const name=new URL(url,origin).pathname;
  return new Response(fs.readFileSync('static/'+name.slice('/static/'.length)),{headers:{
   'Content-Type':name===offline?'text/html':'application/javascript','Content-Encoding':'gzip','Content-Length':'1',
  }});
 });
 await f.dispatch('install');assert.equal(f.stores.get('xtyf-pwa-public-test-one').size,2);
});
test('offline public fallback is revision-bound, HTTP failures are preserved, cache quota failures keep online reads usable',async()=>{
 const f=fixture();await f.dispatch('install');await f.read(f.url(publicJS));
 f.setNetwork(async()=>{throw new TypeError('offline');});
 assert.match(await (await f.read(f.url(publicJS))).text(),/Installation uses/);
 assert.equal(await f.read(publicJS+'?v=another-deployment'),undefined);
 f.setNetwork(async()=>new Response('unavailable',{status:503}));
 assert.equal((await f.read(f.url(publicJS))).status,503);
 f.caches.open=async()=>{throw Error('quota');};
 f.setNetwork(async()=>new Response(fs.readFileSync('static/pwa.js'),{headers:{'Content-Type':'text/javascript'}}));
 assert.equal((await f.read(f.url(publicJS))).status,200);
});
test('failed installation removes partial cache; activation removes only owned previous versions',async()=>{
 const f=fixture();f.setNetwork(async()=>new Response('bad offline shell',{headers:{'Content-Type':'text/html'}}));
 await assert.rejects(f.dispatch('install'),/Invalid public install resource/);
 assert.deepEqual(await f.caches.keys(),[]);
 await f.caches.open('xtyf-pwa-public-old');await f.caches.open('unrelated-storage');await f.caches.open('xtyf-pwa-public-test-one');
 await f.dispatch('activate');
 assert.deepEqual(await f.caches.keys(),['unrelated-storage','xtyf-pwa-public-test-one']);
});
test('an in-flight public read cannot recreate a named cache deleted during worker retirement',async()=>{
 const f=fixture();await f.dispatch('install');
 let release,started;
 const began=new Promise(resolve=>started=resolve),hold=new Promise(resolve=>release=resolve);
 f.setNetwork(async()=>{started();await hold;return new Response(fs.readFileSync('static/pwa.js'),{headers:{'Content-Type':'text/javascript'}});});
 const pending=f.read(f.url(publicJS));await began;
 await f.caches.delete('xtyf-pwa-public-test-one');release();
 assert.equal((await pending).status,200);assert.deepEqual(await f.caches.keys(),[]);
});
test('a partial install quota failure removes every owned partial entry',async()=>{
 const f=fixture(),open=f.caches.open;
 f.caches.open=async name=>{
  const cache=await open(name),put=cache.put;let writes=0;
  cache.put=async(...args)=>{if(++writes===2)throw Error('quota');return put(...args);};return cache;
 };
 await assert.rejects(f.dispatch('install'),/quota/);assert.equal(f.stores.size,0);
});
