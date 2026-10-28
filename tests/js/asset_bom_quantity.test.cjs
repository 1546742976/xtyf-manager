const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('static/asset-bom.js','utf8');
const ctx={};vm.createContext(ctx);
const start=source.indexOf('  function parseQuantity(');
assert.ok(start>=0,'exact parser exists');vm.runInContext(source.slice(start,source.indexOf('  const qty=',start)),ctx);
test('BOM quantities are exact integers rather than rounded floating point values',()=>{
  for(const [raw,number] of [['1',1],['1.0',1],['1e2',100],['1,000',1000],['+100',100],['.1e1',1],['100e-2',1],['1000000000',1e9]])assert.equal(ctx.parseQuantity(raw),number,raw);
  for(const raw of ['1.00000000000000001','999999999.99999999','1,2','NaN','Infinity','1_0','١','1e100000000000000000000','1000000001','0',true,null])assert.ok(Number.isNaN(ctx.parseQuantity(raw)),String(raw));
});
