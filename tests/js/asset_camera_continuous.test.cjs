'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'../..');

function deferred(){
  let resolve,reject;
  const promise=new Promise((a,b)=>{resolve=a;reject=b;});
  return {promise,resolve,reject};
}
function fixture(){
  const nodes=new Map(),timers=new Map(),events=[];
  let timerId=0;
  function node(id){
    if(!nodes.has(id)){
      const handlers=new Map();
      nodes.set(id,{
        id,hidden:false,disabled:false,open:true,textContent:'',value:'',dataset:{},options:[],
        classList:{add(){},remove(){},toggle(){}},
        parentNode:{insertBefore(){}},
        addEventListener(type,fn){handlers.set(type,fn);},
        emit(type){handlers.get(type)?.();},
        setAttribute(){},replaceChildren(...children){this.options=children;this.innerHTML='';},
        scrollIntoView(){},focus(){},pause(){},play:async()=>{},
        srcObject:null,readyState:2,videoWidth:320,videoHeight:240,
      });
    }
    return nodes.get(id);
  }
  const document={
    hidden:false,getElementById:node,
    addEventListener(){},
    dispatchEvent:event=>{events.push(event);},
    querySelectorAll:()=>[],
    createElement(tag){
      assert.equal(tag,'canvas');
      return {
        width:0,height:0,
        getContext:()=>({drawImage(){},getImageData:(x,y,w,h)=>({data:new Uint8ClampedArray(w*h*4)})}),
      };
    },
  };
  const setTimeout=(fn,ms)=>{const id=++timerId;timers.set(id,{fn,ms});return id;};
  const clearTimeout=id=>timers.delete(id);
  const fireTimer=ms=>{
    for(const [id,item] of timers)if(item.ms===ms){timers.delete(id);item.fn();return true;}
    return false;
  };
  const context={
    window:{addEventListener(){}},document,console,Promise,Map,Date,AbortController,
    setTimeout,clearTimeout,CustomEvent:class{constructor(type,opts){this.type=type;this.detail=opts.detail;}},
    matchMedia:()=>({matches:true}),HTMLMediaElement:{HAVE_CURRENT_DATA:2},
    Option:class{constructor(label,value){this.label=label;this.value=value;}},
    navigator:{},location:{origin:'https://localhost'},
  };
  return {node,nodes,context,events,fireTimer,timers};
}
async function flush(){await new Promise(resolve=>setImmediate(resolve));}

test('camera decodes the next label while the previous network request is unresolved',async()=>{
  const f=fixture(),codes=[],pending=[];
  const track={stop(){}},stream={getTracks:()=>[track],getVideoTracks:()=>[{getSettings:()=>({deviceId:'camera'})}]};
  f.context.window.isSecureContext=true;
  f.context.navigator.mediaDevices={
    getUserMedia:async()=>stream,
    enumerateDevices:async()=>[],
  };
  f.context.ZXing={
    MultiFormatReader:class{
      decode(){if(!codes.length)throw Error('no label');return {getText:()=>codes.shift()};}
      reset(){}
    },
    RGBLuminanceSource:class{},
    BinaryBitmap:class{},
    HybridBinarizer:class{},
    BrowserMultiFormatReader:class{},
  };
  const read=text=>{const d=deferred();pending.push({text,...d});return d.promise;};
  let cancellations=0;
  f.context.window.AssetScan={task:'receive',resolve:read,retry:read,cancel(){cancellations++;}};
  vm.runInNewContext(fs.readFileSync(path.join(root,'static/asset-camera.js'),'utf8'),f.context);
  await f.node('asset-camera-start').onclick();
  for(const code of ['A','B','A','B','C']){
    codes.push(code);
    assert.equal(f.fireTimer(260),true,'decoding should stay scheduled');
  }
  assert.deepEqual(pending.map(x=>x.text),['A','B','C']);
  assert.equal(f.node('asset-camera-video').srcObject,stream);
  pending[2].resolve({status:'failure',message:'模拟超时'});
  await flush();
  assert.match(f.node('asset-camera-status').textContent,/模拟超时/);
  assert.equal(f.node('asset-camera-retry').hidden,false,'failed latest label offers retry');
  // Retry existing receipts using the retained entry rather than adding a duplicate.
  f.node('asset-camera-retry').onclick();
  assert.deepEqual(pending.map(x=>x.text),['A','B','C','C']);
  codes.push('C');f.fireTimer(260);
  assert.equal(pending.length,4,'stationary label must not be requeued while retry is pending');
  f.node('asset-camera-stop').onclick();
  assert.equal(cancellations,0,'turning off video must not cancel background receipt lookups');
  pending[3].reject(Error('late failure'));
  await flush();
  assert.equal(f.node('asset-camera-status').textContent,'相机已停止');
  assert.equal(f.node('asset-camera-video').srcObject,null);
});

function scannerFixture(){
  const f=fixture(),requests=[];
  const core={
    catalog:{intake_enabled:false,can_manage:true},
    esc:String,input(){return '';},reason(){return '';},options(){return '';},
    showDialog(dialog){dialog.open=true;},
    api(path,data,opts){
      assert.equal(path,'scan/resolve');
      const d=deferred();requests.push({raw:data.raw,signal:opts?.signal,...d});
      return d.promise;
    },
  };
  f.context.window.AssetLedger=core;
  vm.runInNewContext(fs.readFileSync(path.join(root,'static/asset-scan.js'),'utf8'),f.context);
  f.context.window.AssetScan.open('receive');
  return {...f,requests,scan:f.context.window.AssetScan};
}
function lookupValue(raw){
  return {parsed:{raw,type:'package',label_quantity:1},item:{id:1,name:raw,active:true,tracking:'quantity',unit:'个'}};
}

test('receive mode admits scans immediately and uses four concurrent background lookups',async()=>{
  const f=scannerFixture();
  const issued=Array.from({length:6},(_,i)=>f.scan.resolve('code-'+i));
  assert.equal((await f.scan.resolve('code-0')).status,'duplicate','same raw label must be retained once');
  await flush();
  assert.deepEqual(f.requests.map(x=>x.raw),['code-0','code-1','code-2','code-3']);
  assert.match(f.node('asset-scan-queue').innerHTML,/6 \/ 20/);
  f.requests[0].resolve(lookupValue('code-0'));
  await flush();
  assert.equal(f.requests.length,5,'free slot must start the next lookup');
  f.requests[1].resolve(lookupValue('code-1'));
  await flush();
  assert.equal(f.requests.length,6);
  f.node('asset-scan-dialog').emit('close');
  await flush();
  assert.equal(f.requests.slice(2).every(x=>x.signal.aborted),true,'closing dialog aborts all active lookups');
  const outcomes=await Promise.all(issued);
  assert.equal(outcomes[0].status,'success');
  assert.equal(outcomes[1].status,'success');
  assert(outcomes.slice(2).every(x=>x.status==='stale'),'closed session ignores old results');
});

test('lookup timeout frees a slot and late results cannot overwrite the error',async()=>{
  const f=scannerFixture();
  const issued=Array.from({length:5},(_,i)=>f.scan.resolve('slow-'+i));
  await flush();
  assert.equal(f.requests.length,4);
  assert.equal(f.fireTimer(15000),true,'total timeout starts while queued');
  await flush();
  assert.equal(f.requests.length,5,'timeout should free one of four slots');
  assert.match(f.node('asset-scan-queue').innerHTML,/查询超时/);
  f.requests[0].resolve(lookupValue('slow-0'));
  await flush();
  assert.match(f.node('asset-scan-queue').innerHTML,/查询超时/);
  f.node('asset-scan-dialog').emit('close');
  const results=await Promise.all(issued);
  assert.equal(results[0].status,'failure');
});
