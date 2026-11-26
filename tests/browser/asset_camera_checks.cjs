'use strict';
const assert=require('node:assert/strict');
module.exports=async function(page,unit){
  await page.locator('#asset-scan-general').click();
  await page.locator('#asset-scan-mode').selectOption('general');
  await page.evaluate(()=>{
    window.originalReader=ZXing.MultiFormatReader;
    // This test stubs ZXing pixels, so force the legacy engine in its isolated page.
    // Native BarcodeDetector / Worker have their own regression coverage.
    window.originalBarcodeDetector=window.BarcodeDetector;
    window.originalWorker=window.Worker;
    Object.defineProperty(window,'BarcodeDetector',{configurable:true,value:undefined});
    Object.defineProperty(window,'Worker',{configurable:true,value:undefined});
    const video=document.getElementById('asset-camera-video');
    Object.defineProperty(video,'srcObject',{configurable:true,get:()=>null,set(){}});
    for(const [key,value] of Object.entries({readyState:2,videoWidth:2,videoHeight:2}))Object.defineProperty(video,key,{configurable:true,value});
    video.play=async()=>{};video.pause=()=>{};
    window.originalDraw=CanvasRenderingContext2D.prototype.drawImage;
    CanvasRenderingContext2D.prototype.drawImage=function(source,...args){if(source!==video)return window.originalDraw.call(this,source,...args);};
    Object.defineProperty(navigator,'mediaDevices',{configurable:true,value:{getUserMedia:async()=>({getTracks:()=>[{stop(){}}]})}});
    window.syntheticScan=result=>{window.syntheticLabel=result.getText();};
    ZXing.MultiFormatReader=class{reset(){} decode(){if(!window.syntheticLabel)throw Error('no synthetic frame');return {getText:()=>window.syntheticLabel};}};
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
  await page.evaluate(()=>{
    ZXing.MultiFormatReader=window.originalReader;
    CanvasRenderingContext2D.prototype.drawImage=window.originalDraw;
    Object.defineProperty(window,'BarcodeDetector',{configurable:true,value:window.originalBarcodeDetector});
    Object.defineProperty(window,'Worker',{configurable:true,value:window.originalWorker});
  });
};
