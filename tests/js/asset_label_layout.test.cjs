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
  assert.throws(()=>Layout.compute(14,10),/15/);
  assert.throws(()=>Layout.compute(20,9),/10/);
  assert.throws(()=>Layout.compute(50,30,'garbage'),/排版/);
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
