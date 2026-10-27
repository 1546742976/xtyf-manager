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
  for(const [width,height] of [[40,270],[100,30],[20,12]]){
    await page.evaluate(({width,height})=>{const f=document.querySelector('#asset-label-form');f.elements.paper.value='thermal';f.elements.width.value=width;f.elements.height.value=height;f.requestSubmit();},{width,height});
    await page.locator('#asset-label-download').waitFor();assert.deepEqual(await content(),generated);
    ratio=await page.locator('#asset-label-preview canvas').evaluate(c=>c.width/c.height);assert(Math.abs(ratio-width/height)<0.02);
    await page.locator('#asset-label-page').filter({hasText:'第 1 / 25 页'}).waitFor();
  }
  await page.evaluate(()=>{const f=document.querySelector('#asset-label-form');f.elements.layout.value='compact';f.elements.font.value='8';f.elements.font_width.value='75';f.requestSubmit();});
  await page.locator('#asset-label-download').waitFor();assert.deepEqual(await content(),generated);
  const remembered=await page.evaluate(()=>JSON.parse(localStorage.getItem('xtyf.asset.labels.v3')));
  assert.equal(remembered.layout,'compact');assert.equal(remembered.font,8);assert.equal(remembered.font_width,75);
  // Independent content switches must produce genuinely code-only PDFs and
  // release the disabled text area's space. Decode both actual rendered codes.
  for(const kind of ['qr','barcode']){
    await page.evaluate(kind=>{
      const f=document.querySelector('#asset-label-form');
      f.elements.show_text.checked=false;f.elements.show_qr.checked=kind==='qr';f.elements.show_barcode.checked=kind==='barcode';
      f.elements.width.value=kind==='qr'?'12':'40';f.elements.height.value=kind==='qr'?'12':'10';
      f.dispatchEvent(new Event('input',{bubbles:true}));f.requestSubmit();
    },kind);
    await page.locator('#asset-label-download').waitFor();assert.deepEqual(await content(),generated);
    assert.equal(await page.locator('#asset-label-form [name=name_font]').isDisabled(),true);
    assert.equal(await page.locator('#asset-label-text-settings').isVisible(),false);
    const result=await page.locator('#asset-label-preview canvas').evaluate(async c=>{
      const image=c.getContext('2d').getImageData(0,0,c.width,c.height);
      const z=window.ZXing,source=new z.RGBLuminanceSource(new Int32Array(image.data.buffer),c.width,c.height);
      return new z.MultiFormatReader().decode(new z.BinaryBitmap(new z.HybridBinarizer(source))).getText();
    });
    assert.equal(result,`M:${id}/U:${units[0].id}`);
    const text=await page.locator('#asset-label-download').evaluate(async a=>{
      const pdf=await import('/vendor/pdfjs/pdf.mjs');
      const doc=await pdf.getDocument({data:new Uint8Array(await(await fetch(a.href)).arrayBuffer())}).promise;
      const first=await doc.getPage(1),text=(await first.getTextContent()).items.map(i=>i.str).join('');await doc.destroy();return text;
    });
    assert.equal(text,'','text toggle must remove every caption');
    const scan=await post('/api/assets/scan/resolve',{raw:result});assert.equal(scan.unit.id,units[0].id);
    await page.screenshot({path:path.resolve(__dirname,`../../.build-tools/assets-review-fixes/pdf-${kind}-${label}.png`)});
  }
  await page.locator('#asset-label-form [name=show_barcode]').uncheck();
  await page.locator('#asset-label-status').filter({hasText:'至少保留一项'}).waitFor();
  assert.equal(await page.locator('#asset-label-download').isVisible(),false);
  await page.locator('#asset-label-form [name=show_text]').check();
  await page.locator('#asset-label-download').waitFor();
  assert.equal(await page.locator('#asset-label-form [name=name_font]').isDisabled(),false);
  await page.evaluate(()=>{
    const f=document.querySelector('#asset-label-form');f.elements.width.value='50';f.elements.height.value='30';
    f.elements.name_font.value='14';f.elements.spec_font.value='9';f.elements.serial_font.value='7';f.elements.code_font.value='6';
    f.elements.name_overflow.value='truncate';f.elements.spec_overflow.value='wrap';f.elements.auto_fit.checked=false;f.requestSubmit();
  });
  await page.locator('#asset-label-download').waitFor();
  const independent=await page.evaluate(()=>JSON.parse(localStorage.getItem('xtyf.asset.labels.v3')));
  assert.equal(independent.name_font,14);assert.equal(independent.spec_font,9);assert.equal(independent.auto_fit,false);
  assert.equal(independent.name_overflow,'truncate');assert.equal(independent.spec_overflow,'wrap');
  await page.locator('#asset-label-recommend').click();await page.locator('#asset-label-download').waitFor();
  assert.equal(await page.locator('#asset-label-form [name=auto_fit]').isChecked(),true);
  // Restore a full-content label for the authenticated native-save regression.
  await page.locator('#asset-label-form [name=show_qr]').check();
  await page.locator('#asset-label-form [name=show_barcode]').check();
  await page.locator('#asset-label-download').waitFor();
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
  // Restore independent settings after a page reload, then migrate the old
  // v2 font/size preferences without disabling any newly introduced content.
  await page.evaluate(value=>localStorage.setItem('xtyf.asset.labels.v3',JSON.stringify(value)),independent);
  await page.reload();await page.locator('#asset-status').filter({hasText:'数据已更新'}).waitFor();
  assert.equal(await page.locator('#asset-label-form [name=name_font]').inputValue(),'14');
  assert.equal(await page.locator('#asset-label-form [name=spec_font]').inputValue(),'9');
  assert.equal(await page.locator('#asset-label-form [name=name_overflow]').inputValue(),'truncate');
  assert.equal(await page.locator('#asset-label-form [name=show_qr]').isChecked(),false);
  assert.equal(await page.locator('#asset-label-form [name=auto_fit]').isChecked(),false);
  await page.evaluate(async values=>{await AssetLabels.open(values);},units);
  await page.locator('#asset-label-download').waitFor();await page.locator('#asset-label-close').click();
  await page.evaluate(()=>{
    localStorage.removeItem('xtyf.asset.labels.v3');
    localStorage.setItem('xtyf.asset.labels.v2',JSON.stringify({paper:'thermal',width:20,height:12,layout:'compact',font:8,font_width:75}));
  });
  await page.reload();await page.locator('#asset-status').filter({hasText:'数据已更新'}).waitFor();
  assert.equal(await page.locator('#asset-label-form [name=name_font]').inputValue(),'8');
  assert.equal(await page.locator('#asset-label-form [name=font_width]').inputValue(),'75');
  for(const name of ['show_text','show_qr','show_barcode'])assert.equal(await page.locator(`#asset-label-form [name=${name}]`).isChecked(),true);
  await page.evaluate(async values=>{await AssetLabels.open(values);},units);
  await page.locator('#asset-label-download').waitFor();await page.locator('#asset-label-close').click();
};
