const {test}=require('node:test');
const assert=require('node:assert/strict');
const {prepare}=require('../../static/daily-images.js');
test('opaque client handles remain handles with explicit original notice',async()=>{
  const file={id:'session-image',size:50,mime:'image/png',name:'sample.png'};
  assert.deepEqual(await prepare(file),{file,original:true});
});
test('reject oversized and unsupported images before decoding',async()=>{
  await assert.rejects(prepare({size:11*1024*1024,type:'image/png'}),/10 MiB/);
  await assert.rejects(prepare({size:5,type:'text/html'}),/PNG/);
});
test('decode failure propagates for retained form retry',async()=>{
  global.createImageBitmap=async()=>{throw new Error('decode failed');};
  try{await assert.rejects(prepare(new Blob(['invalid'],{type:'image/png'})),/decode failed/);}finally{delete global.createImageBitmap;}
});
test('pixel bounds close decoded bitmap before any canvas allocation',async()=>{
  let closed=0;global.createImageBitmap=async()=>({width:10001,height:1,close(){closed++;}});
  try{await assert.rejects(prepare(new Blob(['image'],{type:'image/png'})),/尺寸/);assert.equal(closed,1);}finally{delete global.createImageBitmap;}
});
