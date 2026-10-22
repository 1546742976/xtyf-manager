'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function fixture(){
  const nodes=new Map(),get=id=>{if(!nodes.has(id))nodes.set(id,{value:'query',hidden:false,innerHTML:'',textContent:'',listeners:{},replaceChildren(){},append(){},addEventListener(k,f){(this.listeners[k]??=[]).push(f);},focus(){},close(){for(const f of this.listeners.close||[])f();},showModal(){},files:[]});return nodes.get(id);};
  let calls=0,decode,vibrations=0,respond=async()=>{throw Error('network unavailable');};
  const context={document:{createElement:()=>get("asset-camera-retry"),getElementById:get,addEventListener(){}},window:{AssetLedger:{esc:String,input(){},reason(){},options(){},api:async(...args)=>{calls++;return respond(...args);}},AssetUnits:{selection:[]},isSecureContext:true,addEventListener(){}},navigator:{vibrate(){vibrations++;},mediaDevices:{getUserMedia:async()=>({getTracks:()=>[{stop(){}}]})}},ZXing:{BrowserMultiFormatReader:class{reset(){} async decodeFromStream(s,v,cb){decode=cb;}}},matchMedia:()=>({matches:false}),Map,console};
  vm.createContext(context);
  for(const f of ['static/asset-scan.js','static/asset-camera.js'])vm.runInContext(fs.readFileSync(f,'utf8'),context,{filename:f});
  return {get,context,set respond(f){respond=f;},get calls(){return calls;},get vibrations(){return vibrations;},frame:()=>decode({getText:()=>'{on:SYNTHETIC,pc:C123,pm:TEST,qty:2}'}),tick:()=>new Promise(r=>setImmediate(r))};
}
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
