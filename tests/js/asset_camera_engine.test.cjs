'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

function setup({native=false,worker=true}={}){
  const nodes=new Map(),timers=new Map(),reads=[],workers=[],accepted=[];
  let next=0,canvasReads=0;
  function node(id){
    if(!nodes.has(id))nodes.set(id,{
      id,hidden:false,disabled:false,open:true,dataset:{},value:'',textContent:'',innerHTML:'',options:[],
      classList:{add(){},remove(){}},setAttribute(){},addEventListener(){},focus(){},
      replaceChildren(){},pause(){},play:async()=>{},
      srcObject:null,readyState:2,videoWidth:1280,videoHeight:720
    });
    return nodes.get(id);
  }
  const document={
    currentScript:{src:'https://test.example/static/asset-camera.js'},
    getElementById:node,createElement:()=>({width:0,height:0,getContext:()=>({
      drawImage(){},getImageData:(_x,_y,w,h)=>{canvasReads++;return {data:new Uint8ClampedArray(w*h*4)};}
    })}),
    querySelectorAll:()=>[],addEventListener(){}
  };
  const stream={getTracks:()=>[{stop(){}}],getVideoTracks:()=>[{getSettings:()=>({deviceId:'rear'})}]};
  const context={
    document,Map,Promise,Date,ArrayBuffer,Uint8ClampedArray,Int32Array,console,
    window:{isSecureContext:true,AssetScan:{task:'receive',resolve(text){accepted.push(text);return Promise.resolve({status:'success'});},retry(){return Promise.resolve({status:'success'});}},addEventListener(){}},
    navigator:{mediaDevices:{getUserMedia:async()=>stream,enumerateDevices:async()=>[]}},
    location:{href:'https://test.example/assets',origin:'https://test.example'},
    HTMLMediaElement:{HAVE_CURRENT_DATA:2},matchMedia:()=>({matches:false}),
    Option:class{constructor(text,value){this.text=text;this.value=value;}},
    URL,setTimeout:(fn,ms)=>{const id=++next;timers.set(id,{fn,ms});return id;},
    clearTimeout:id=>timers.delete(id),
    ZXing:{MultiFormatReader:class{reset(){}decode(){return {getText:()=> 'OLD'};}},RGBLuminanceSource:class{},HybridBinarizer:class{},BinaryBitmap:class{}}
  };
  if(worker)context.Worker=class{
    constructor(url){this.url=url;this.terminated=false;workers.push(this);}
    postMessage(data,transfers){reads.push({data,transfers,instance:this});}
    terminate(){this.terminated=true;}
  };
  if(native){
    let pending=[];
    context.window.BarcodeDetector=class{
      static async getSupportedFormats(){return ['qr_code','code_128','ean_13'];}
      detect(){return new Promise(resolve=>pending.push(resolve));}
      static finish(result){pending.shift()(result);}
      static get pending(){return pending.length;}
    };
  }
  vm.runInNewContext(fs.readFileSync('static/asset-camera.js','utf8'),context);
  function fire(ms){const entry=[...timers].find(([,timer])=>timer.ms===ms);if(!entry)return false;timers.delete(entry[0]);entry[1].fn();return true;}
  return {node,context,timers,fire,reads,workers,accepted,get canvasReads(){return canvasReads;}};
}
const flush=()=>new Promise(r=>setImmediate(r));

test('worker receives transferable, cropped frames and stops without queuing more work',async()=>{
  const f=setup();
  await f.node('asset-camera-start').onclick();
  assert.equal(f.workers.length,1);
  assert.match(f.workers[0].url,/\/static\/asset-zxing-worker\.js$/);
  assert.equal(f.fire(260),true);
  assert.equal(f.reads.length,1);
  assert.equal(f.reads[0].data.width,800);
  assert(f.reads[0].data.height<720);
  assert.equal(f.reads[0].data.rgba,f.reads[0].transfers[0]);
  assert.equal(f.fire(260),false,'worker must pace the decode, not fill a queue');
  f.workers[0].onmessage({data:{generation:f.reads[0].data.generation,text:'SERIAL-A'}});
  await flush();
  assert.deepEqual(f.accepted,['SERIAL-A']);
  assert.equal(f.fire(260),true);
  f.node('asset-camera-stop').onclick();
  assert.equal(f.workers[0].terminated,true);
  f.workers[0].onmessage({data:{generation:f.reads[0].data.generation,text:'LATE'}});
  assert.deepEqual(f.accepted,['SERIAL-A']);
});

test('native detector reads video directly and periodically falls back to worker for other formats',async()=>{
  const f=setup({native:true});
  await f.node('asset-camera-start').onclick();
  assert.equal(f.fire(120),true);
  assert.equal(f.context.window.BarcodeDetector.pending,1);
  f.context.window.BarcodeDetector.finish([{rawValue:'QR-A'}]);
  await flush();
  assert.deepEqual(f.accepted,['QR-A']);
  assert.equal(f.canvasReads,0,'native decode should avoid getImageData');
  for(let i=0;i<7;i++){
    assert.equal(f.fire(120),true);
    f.context.window.BarcodeDetector.finish([]);
    await flush();
  }
  assert.equal(f.reads.length,1,'worker scans periodically after native misses');
  f.workers[0].onmessage({data:{generation:f.reads[0].data.generation,text:'UNCOMMON-B'}});
  await flush();
  assert.deepEqual(f.accepted,['QR-A','UNCOMMON-B']);
  f.node('asset-camera-stop').onclick();
});

test('worker failure safely switches to bounded legacy decoding',async()=>{
  const f=setup();
  await f.node('asset-camera-start').onclick();
  f.workers[0].onerror(new Error('blocked worker'));
  assert.equal(f.workers[0].terminated,true);
  assert.equal(f.fire(260),true);
  assert.deepEqual(f.accepted,['OLD']);
});
