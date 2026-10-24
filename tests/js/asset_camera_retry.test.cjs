'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function fixture(){
  const listeners={},windowListeners={},nodes=new Map();
  const get=id=>{
    if(!nodes.has(id))nodes.set(id,{value:'general',hidden:false,open:true,innerHTML:'',textContent:'',dataset:{},options:[],listeners:{},classList:{add(){},remove(){},toggle(){}},replaceChildren(){this.innerHTML='';},append(){},parentNode:{insertBefore(){}},setAttribute(){},addEventListener(k,f){(this.listeners[k]??=[]).push(f);},focus(){},close(){this.open=false;for(const f of this.listeners.close||[])f();},showModal(){this.open=true;},files:[]});
    return nodes.get(id);
  };
  let calls=0,decode,vibrations=0,stopped=0,respond=async()=>{throw Error('network unavailable');};
  const context={
    document:{createElement:()=>get('created'),getElementById:get,querySelectorAll:()=>[],addEventListener(k,f){(listeners[k]??=[]).push(f);},dispatchEvent(e){for(const f of listeners[e.type]||[])f(e);}},
    window:{AssetLedger:{catalog:{can_manage:true,campuses:[]},showDialog(){},esc:String,input(){},reason(){},options(){},api:async(...args)=>{calls++;return respond(...args);}},AssetUnits:{selection:[]},isSecureContext:true,addEventListener(k,f){(windowListeners[k]??=[]).push(f);}},
    navigator:{vibrate(){vibrations++;},mediaDevices:{getUserMedia:async()=>({getTracks:()=>[{stop(){stopped++;}}]})}},
    ZXing:{BrowserMultiFormatReader:class{reset(){} async decodeFromStream(s,v,cb){decode=cb;}}},
    CustomEvent:class{constructor(type,options){this.type=type;this.detail=options.detail;}},setTimeout(){},clearTimeout(){},
    matchMedia:()=>({matches:false}),Map,console
  };
  vm.createContext(context);
  for(const f of ['static/asset-scan.js','static/asset-camera.js'])vm.runInContext(fs.readFileSync(f,'utf8'),context,{filename:f});
  return {get,context,set respond(f){respond=f;},get calls(){return calls;},get vibrations(){return vibrations;},get stopped(){return stopped;},emit:name=>{for(const f of windowListeners[name]||[])f();},frame:()=>decode({getText:()=>'{on:SYNTHETIC,pc:C123,pm:TEST,qty:2}'}),tick:()=>new Promise(r=>setImmediate(r))};
}

test('camera releases tracks when a departing frame no longer exposes scanner globals',async()=>{
  const f=fixture();await f.get('asset-camera-start').onclick();
  delete f.context.window.AssetScan;
  assert.doesNotThrow(()=>f.emit('pagehide'));
  assert.equal(f.stopped,1);
});
test('failed camera label remains retryable without success or frame request storm',async()=>{
  const f=fixture();await f.get('asset-camera-start').onclick();f.frame();await f.tick();
  assert.equal(f.vibrations,0);assert.doesNotMatch(f.get('asset-camera-status').textContent,/已识别/);
  f.frame();f.frame();await f.tick();assert.equal(f.calls,1);assert.equal(f.get('asset-camera-retry').hidden,false);
  f.respond=async()=>({parsed:{type:'unit'},item:{name:'合成电机'},unit:{id:1}});
  await f.get('asset-camera-retry').onclick();assert.equal(f.calls,2);assert.equal(f.vibrations,1);
  assert.equal(f.get('asset-camera-retry').hidden,true);
});
test('closing scanner invalidates pending resolution and camera feedback',async()=>{
  const f=fixture();let finish;f.respond=()=>new Promise(r=>finish=r);
  await f.get('asset-camera-start').onclick();f.frame();await f.tick();
  f.get('asset-scan-dialog').close();finish({parsed:{type:'unit'},item:{name:'late'},unit:{id:9}});await f.tick();
  assert.equal(f.vibrations,0);assert.doesNotMatch(f.get('asset-scan-preview').innerHTML,/late/);
  assert.equal(f.context.window.AssetScan.resolved,null);
});
test('stopping camera also invalidates its outstanding lookup while dialog stays open',async()=>{
  const f=fixture();let finish;f.respond=()=>new Promise(r=>finish=r);
  await f.get('asset-camera-start').onclick();f.frame();await f.tick();
  f.get('asset-camera-stop').onclick();finish({parsed:{type:'unit'},item:{name:'late'},unit:{id:9}});await f.tick();
  assert.equal(f.vibrations,0);assert.doesNotMatch(f.get('asset-scan-preview').innerHTML,/late/);
  assert.equal(f.get('asset-camera-status').textContent,'相机已停止');
});

test('receipt recognition failure preserves one queued label and retries without duplicate success',async()=>{
  const f=fixture();
  f.context.window.AssetScan.open('receive');
  await f.get('asset-camera-start').onclick();f.frame();await f.tick();
  assert.equal(f.vibrations,0);assert.equal(f.calls,1);
  assert.match(f.get('asset-scan-queue').innerHTML,/1 \/ 20/);
  f.frame();await f.tick();assert.equal(f.calls,1);
  f.respond=async()=>({parsed:{type:'package',raw:'{on:SYNTHETIC,pc:C123,pm:TEST,qty:2}',label_quantity:2},item:{id:1,name:'合成物料',active:true,tracking:'quantity'}});
  await f.get('asset-camera-retry').onclick();
  assert.equal(f.calls,2);assert.equal(f.vibrations,1);
  assert.match(f.get('asset-scan-queue').innerHTML,/1 \/ 20/);
  assert.equal(f.get('asset-camera-retry').hidden,true);
});

test('receipt response after scanner closes does not change the retained queue or signal success',async()=>{
  const f=fixture();let finish;f.respond=()=>new Promise(r=>finish=r);
  f.context.window.AssetScan.open('receive');
  await f.get('asset-camera-start').onclick();f.frame();await f.tick();
  f.get('asset-scan-dialog').close();const queued=f.get('asset-scan-queue').innerHTML;
  finish({parsed:{type:'package',raw:'late'},item:{name:'late'}});await f.tick();
  assert.equal(f.vibrations,0);assert.equal(f.get('asset-scan-queue').innerHTML,queued);
});
