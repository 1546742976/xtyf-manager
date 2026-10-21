'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),crypto=require('node:crypto');
const root=path.resolve(__dirname,'../../static/vendor/asset-tools'),z=require(path.join(root,'zxing.js'));
test('checked-in scanner dependencies match the exact upstream file digests',()=>{
  const lock=JSON.parse(fs.readFileSync(path.join(root,'lock.json')));
  assert.equal(Object.keys(lock).length,2);
  for(const entry of Object.values(lock))for(const [file,sha] of Object.entries(entry.files))assert.equal(crypto.createHash('sha256').update(fs.readFileSync(path.join(root,file))).digest('hex'),sha);
});
test('internal item and unit QR identities round-trip, including long IDs',()=>{
  for(const code of ['U:1','M:42','U:9007199254740991']){
    const matrix=new z.QRCodeWriter().encode(code,z.BarcodeFormat.QR_CODE,260,260,new Map());
    assert.equal(new z.QRCodeReader().decode({getBlackMatrix:()=>matrix}).getText(),code);
  }
});
test('Code128 labels are decoded by the independent ZXing reader',()=>{
  const scope={window:{},document:{}};vm.runInNewContext(fs.readFileSync(path.join(root,'barcode.js'),'utf8'),scope);
  for(const code of ['U:1','M:42','U:9007199254740991']){
    const output={};scope.window.JsBarcode(output,code,{format:'CODE128'});
    const bits='0'.repeat(20)+output.encodings[0].data+'0'.repeat(20),row=new z.BitArray(bits.length);
    [...bits].forEach((v,i)=>{if(v==='1')row.set(i);});
    assert.equal(new z.Code128Reader().decodeRow(0,row).getText(),code);
  }
});
