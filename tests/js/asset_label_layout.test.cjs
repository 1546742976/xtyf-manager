'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const path=require('node:path');
const Layout=require('../../static/asset-label-layout.js');
const z=require(path.resolve(__dirname,'../../static/vendor/asset-tools/zxing.js'));

test('layout uses millimetres and switches correctly for wide/square/small formats',()=>{
  for(const [w,h,expected] of [[50,30,'wide'],[30,30,'compact'],[25,15,'compact'],[20,12,'compact']]){
    const l=Layout.compute(w,h);
    assert.equal(l.mode,expected);
    for(const key of ['text','qr','barcode']){
      const b=l[key];
      assert.ok(b.w>0&&b.h>0,key+' has a positive area');
      assert.ok(b.x>=0&&b.y>=0&&b.x+b.w<=w+1e-7&&b.y+b.h<=h+1e-7);
    }
    assert.equal(l.qr.w,l.qr.h);
  }
});
test('manual layout overrides automatic selection on any supported size',()=>{
  assert.equal(Layout.compute(50,30,'compact').mode,'compact');
  assert.equal(Layout.compute(30,30,'wide').mode,'wide');
  assert.throws(()=>Layout.compute(5,10),/6/);
  assert.throws(()=>Layout.compute(20,5),/6/);
  assert.throws(()=>Layout.compute(50,30,'garbage'),/排版/);
});
test('all seven content combinations reclaim space on tiny and large labels',()=>{
  for(const [w,h] of [[6,6],[12,8],[20,12],[50,30],[40,270]])for(let mask=1;mask<8;mask++){
    const opts={show_text:!!(mask&1),show_qr:!!(mask&2),show_barcode:!!(mask&4)};
    const g=Layout.compute(w,h,'auto',opts),boxes=['text','qr','barcode'].map(k=>g[k]).filter(Boolean);
    assert.equal(boxes.length,Object.values(opts).filter(Boolean).length);
    for(const b of boxes)assert(b.w>0&&b.h>0&&b.x>=0&&b.y>=0&&b.x+b.w<=w+1e-7&&b.y+b.h<=h+1e-7);
    if(g.qr)assert.equal(g.qr.w,g.qr.h);
    assert.doesNotThrow(()=>Layout.warnings(g));
    for(const [key,size] of Object.entries(Layout.recommend(w,h,'auto',opts)))assert(size>=(key==='font'?6:4)&&size<=18);
  }
  assert.throws(()=>Layout.compute(20,12,'auto',{show_text:false,show_qr:false,show_barcode:false}),/至少/);
});
test('the bounded-frame scan decoder can read a generated real QR image',()=>{
  const code='M:128',matrix=new z.QRCodeWriter().encode(code,z.BarcodeFormat.QR_CODE,192,192,new Map());
  const width=matrix.getWidth(),pixels=new Uint8ClampedArray(width*width*4);
  for(let y=0;y<width;y++)for(let x=0;x<width;x++){
    const i=4*(y*width+x),v=matrix.get(x,y)?0:255;
    pixels[i]=pixels[i+1]=pixels[i+2]=v;pixels[i+3]=255;
  }
  const source=new z.RGBLuminanceSource(new Int32Array(pixels.buffer),width,width);
  const result=new z.MultiFormatReader().decode(new z.BinaryBitmap(new z.HybridBinarizer(source)));
  assert.equal(result.getText(),code);
});
