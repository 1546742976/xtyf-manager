'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
module.exports=async function({page,api,post,campus,label}){
  const id=(await post('/api/assets/catalog/items',{request_id:crypto.randomUUID(),name:'中文标签测试 '+label,kind:'returnable',unit:'台'})).id;
  await post('/api/assets/units/receive',{request_id:crypto.randomUUID(),item_id:id,campus_id:campus,quantity:25,reason:'合成多页标签'});
  const units=[];for(const p of [1,2])units.push(...(await(await api.get(`/api/assets/units?item_id=${id}&page=${p}`)).json()).units);
  let generated;
  const capture=async r=>{const response=await r.fetch();generated=await response.body();await r.fulfill({response});};
  await page.route('**/api/assets/labels.pdf?*',capture);
  await page.evaluate(async values=>{
    const form=document.querySelector('#asset-label-form');form.elements.paper.value='a4';form.elements.width.value='100';form.elements.height.value='100';await AssetLabels.open(values);
  },units);
  await page.locator('#asset-label-page').filter({hasText:'第 1 / 13 页'}).waitFor();
  const content=async()=>Buffer.from(await page.locator('#asset-label-download').evaluate(async a=>Array.from(new Uint8Array(await(await fetch(a.href)).arrayBuffer()))));
  assert.deepEqual(await content(),generated);
  const downloadPromise=page.waitForEvent('download');await page.locator('#asset-label-download').click();
  const download=await downloadPromise;assert.deepEqual(fs.readFileSync(await download.path()),generated);
  await page.screenshot({path:path.resolve(__dirname,`../../.build-tools/assets-review-fixes/pdf-${label}.png`)});
  const metadata=await page.locator('#asset-label-download').evaluate(async a=>{
    const pdf=await import('/vendor/pdfjs/pdf.mjs');const doc=await pdf.getDocument({data:new Uint8Array(await(await fetch(a.href)).arrayBuffer()),cMapUrl:'/vendor/pdfjs/cmaps/',cMapPacked:true}).promise;
    const first=await doc.getPage(1);const text=(await first.getTextContent()).items.map(i=>i.str).join('');const result={pages:doc.numPages,text,view:first.view};await doc.destroy();return result;
  });
  assert.equal(metadata.pages,13);assert(metadata.text.includes('中文标签测试'));assert(metadata.text.includes('U:'+units[0].id));
  let ratio=await page.locator('#asset-label-preview canvas').evaluate(c=>c.width/c.height);assert(Math.abs(ratio-210/297)<0.01);
  await page.locator('#asset-label-next').click();await page.locator('#asset-label-page').filter({hasText:'第 2 / 13 页'}).waitFor();
  for(const [width,height] of [[40,270],[100,30]]){
    await page.evaluate(({width,height})=>{const f=document.querySelector('#asset-label-form');f.elements.paper.value='thermal';f.elements.width.value=width;f.elements.height.value=height;f.requestSubmit();},{width,height});
    await page.locator('#asset-label-download').waitFor();assert.deepEqual(await content(),generated);
    ratio=await page.locator('#asset-label-preview canvas').evaluate(c=>c.width/c.height);assert(Math.abs(ratio-width/height)<0.02);
    await page.locator('#asset-label-page').filter({hasText:'第 1 / 25 页'}).waitFor();
  }
  // The local client reads authenticated bytes and saves those exact bytes without a second GET.
  const encoded=generated.toString('base64');await page.unroute('**/api/assets/labels.pdf?*',capture);
  await page.evaluate(async ({encoded,units})=>{
    document.querySelector('#asset-label-dialog').close();
    window.XTYF={config:{capabilities:['pdf_save']},readResource:async path=>{window.pdfReadPath=path;return {status:200,body:encoded};},invoke:async(action,args)=>{window.pdfSaved={action,args};return {cancelled:false};}};
    await AssetLabels.open(units);
  },{encoded,units});
  await page.locator('#asset-label-download').click();
  const saved=await page.evaluate(()=>window.pdfSaved);assert.equal(saved.action,'download');assert.equal(saved.args.pdfBody,encoded);
  assert((await page.evaluate(()=>window.pdfReadPath)).startsWith('/api/assets/labels.pdf?'));
  await page.evaluate(()=>{delete window.XTYF;document.querySelector('#asset-label-dialog').close();});
  // A slow obsolete response must not overwrite the latest failed preview.
  let release,started;const began=new Promise(r=>started=r),hold=new Promise(r=>release=r);let calls=0;
  const race=async r=>{calls++;if(calls===1){started();await hold;await r.fulfill({status:200,contentType:'application/pdf',body:generated});}else await r.fulfill({status:500,body:'synthetic PDF failure'});};
  await page.route('**/api/assets/labels.pdf?*',race);
  await page.evaluate(values=>{void AssetLabels.open(values);},units);await began;
  await page.evaluate(()=>document.querySelector('#asset-label-form').requestSubmit());
  await page.locator('#asset-label-status').filter({hasText:'PDF 预览失败'}).waitFor();release();
  assert.equal(await page.locator('#asset-label-preview canvas').count(),0);assert.equal(await page.locator('#asset-label-download').isVisible(),false);
  await page.locator('#asset-label-close').click();await page.unroute('**/api/assets/labels.pdf?*',race);
};
