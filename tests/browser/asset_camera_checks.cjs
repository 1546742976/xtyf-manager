'use strict';
const assert=require('node:assert/strict');
module.exports=async function(page,unit){
  await page.locator('#asset-scan-general').click();
  await page.locator('#asset-scan-mode').selectOption('general');
  await page.evaluate(()=>{
    window.originalReader=ZXing.BrowserMultiFormatReader;
    Object.defineProperty(navigator,'mediaDevices',{configurable:true,value:{getUserMedia:async()=>({getTracks:()=>[{stop(){}}]})}});
    ZXing.BrowserMultiFormatReader=class{reset(){} async decodeFromStream(s,v,cb){window.syntheticScan=cb;}};
  });
  let calls=0;
  const handler=async r=>{calls++;if(calls===1)await r.fulfill({status:503,contentType:'application/json',body:'{"ok":false,"error":"synthetic failure"}'});else await r.continue();};
  await page.route('**/api/assets/scan/resolve',handler);
  await page.locator('[data-scan-input=camera]').click();await page.locator('#asset-camera-start').click();
  await page.evaluate(id=>window.syntheticScan({getText:()=>`U:${id}`}),unit.id);
  await page.locator('#asset-camera-retry').waitFor();
  assert(!((await page.locator('#asset-camera-status').textContent()).includes('已识别')));
  await page.evaluate(id=>{for(let i=0;i<5;i++)window.syntheticScan({getText:()=>`U:${id}`});},unit.id);
  await page.locator('#asset-camera-retry').click();await page.locator('[data-scan-action=unit-details]').waitFor();
  assert.equal(calls,2);await page.locator('#asset-camera-retry').waitFor({state:'hidden'});
  await page.locator('#asset-camera-stop').click();
  await page.locator('#asset-scan-close').click();await page.unroute('**/api/assets/scan/resolve',handler);
  await page.evaluate(()=>{ZXing.BrowserMultiFormatReader=window.originalReader;});
};
