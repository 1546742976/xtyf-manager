'use strict';
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), os = require('node:os');
const {spawn} = require('node:child_process');
const {chromium} = require('playwright');
const {recordFailure} = require('./diagnostics.cjs');
const root = path.resolve(__dirname,'../..'), output = path.join(root,'.build-tools/pwa-check');
fs.mkdirSync(output,{recursive:true});
const profile = fs.mkdtempSync(path.join(os.tmpdir(),'xtyf-pwa-profile-'));
const server = spawn(process.env.REVIEW_PYTHON||'python',['-B','tests/browser/pwa_server.py'],{cwd:root,env:process.env,stdio:['ignore','pipe','pipe']});
const launch = {headless:true,...(process.env.REVIEW_CHROMIUM_PATH?{executablePath:process.env.REVIEW_CHROMIUM_PATH}:{}),args:['--no-sandbox']};
let context, browser, other, page, log='', phase='startup';
const passed=[], errors=[];
async function login(target,base,username='admin',password='pwa-browser-password',destination='/') {
  await target.goto(base+'/login');
  await target.locator('[name=username]').fill(username);await target.locator('[name=password]').fill(password);
  await target.locator('#login-form button[type=submit]').click();await target.waitForURL(base+destination);
}
async function api(target,url,data) {
 return target.evaluate(async({url,data})=>{
  const response=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  return {status:response.status,...await response.json()};
 },{url,data});
}
async function controlled(target) {
 await target.evaluate(async()=>{await navigator.serviceWorker.ready;if(!navigator.serviceWorker.controller)await new Promise(resolve=>navigator.serviceWorker.addEventListener('controllerchange',resolve,{once:true}));});
}
async function until(check,label) {
 const deadline=Date.now()+20000;
 do {if(await check())return;await new Promise(resolve=>setTimeout(resolve,50));}while(Date.now()<deadline);
 throw Error('Timed out waiting for '+label);
}
async function cacheSnapshot(target) {
 return target.evaluate(async()=>{
  const result={};for(const name of await caches.keys()){
   const cache=await caches.open(name);result[name]=[];
   for(const request of await cache.keys()){
    const response=await cache.match(request);
    result[name].push({url:request.url,type:response.headers.get('Content-Type'),size:(await response.arrayBuffer()).byteLength});
   }
  }return result;
 });
}
(async()=>{
 try {
  const base=await new Promise((resolve,reject)=>{
   const timer=setTimeout(()=>reject(Error('server timeout '+log)),30000);
   server.stderr.on('data',d=>log+=d);server.stdout.on('data',d=>{log+=d;const match=log.match(/PWA_URL=(\S+)/);if(match){clearTimeout(timer);resolve(match[1]);}});
   server.once('exit',code=>{clearTimeout(timer);reject(Error('server exited '+code+' '+log));});
  });
  const open=()=>chromium.launchPersistentContext(profile,{...launch,viewport:{width:1280,height:900},acceptDownloads:true});
  context=await open();page=context.pages()[0]||await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  await login(page,base);await controlled(page);
  const fixture=await page.evaluate(async()=>await (await fetch('/__pwa_fixture__/data')).json());
  phase='metadata and same-origin icons';
  const manifest=await page.evaluate(async()=>{
   const link=document.querySelector('link[rel=manifest]');const response=await fetch(link.href);
   return {data:await response.json(),type:response.headers.get('Content-Type'),url:link.href,secure:isSecureContext};
  });
  assert.equal(manifest.secure,true);assert.match(manifest.type,/application\/manifest\+json/);
  for(const field of ['id','start_url','scope'])assert.equal(manifest.data[field],'/');
  assert.equal(manifest.data.display,'standalone');assert.equal(manifest.data.icons.length,3);
  for(const icon of manifest.data.icons){
   const loaded=await page.evaluate(async src=>{const image=new Image();image.src=src;await image.decode();return [image.naturalWidth,image.naturalHeight];},icon.src);
   assert.deepEqual(loaded,icon.sizes.split('x').map(Number));
  }
  assert.equal((await page.evaluate(async()=>{const r=await navigator.serviceWorker.getRegistration();return r.scope;})),base+'/');
  passed.push('manifest, real icons, secure loopback and root service-worker registration');

  phase='persistent login and shared account theme';
  const cookies=await context.cookies();assert.ok(cookies.some(c=>c.name==='session'&&c.httpOnly&&c.expires>Date.now()/1000+86400));
  await context.close();context=await open();page=context.pages()[0]||await context.newPage();await page.goto(base+'/tasks');await controlled(page);
  assert.equal(new URL(page.url()).pathname,'/tasks');
  browser=await chromium.launch(launch);other=await browser.newContext();const second=await other.newPage();await login(second,base);
  await page.goto(base+'/account');assert.equal((await api(page,'/api/account/preferences',{theme:'dark'})).ok,true);
  await second.reload();assert.equal(await second.locator('html').getAttribute('data-theme'),'dark');
  // All server-rendered shells consume the same preferences and install metadata.
  for(const url of ['/','/tasks','/assets','/members','/funds/','/invoices/','/account']){
   await page.goto(base+url);assert.equal(await page.locator('link[rel=manifest]').count(),1);
   assert.equal(await page.locator('html').getAttribute('data-theme'),'dark');
  }
  await page.goto(base+'/account');await page.setViewportSize({width:375,height:812});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  assert.match(await page.locator('#pwa-status').textContent(),/浏览器|安装|独立窗口/);
  await page.evaluate(()=>{
   window.pwaSyntheticPromptCalls=0;
   const event=new Event('beforeinstallprompt',{cancelable:true});
   event.prompt=async()=>{window.pwaSyntheticPromptCalls++;};event.userChoice=Promise.resolve({outcome:'dismissed'});
   window.dispatchEvent(event);
  });
  assert.equal(await page.evaluate(()=>window.pwaSyntheticPromptCalls),0,'installation is never prompted automatically');
  await page.locator('#pwa-install').click();
  await page.locator('#pwa-status').filter({hasText:'已取消安装'}).waitFor();
  assert.equal(await page.evaluate(()=>window.pwaSyntheticPromptCalls),1);
  assert.equal(await page.locator('#pwa-install').isVisible(),false);
  await page.screenshot({path:path.join(output,'account-mobile.png'),fullPage:true});
  passed.push('persistent-profile close/reopen preserves session; independent login refresh receives account theme; mobile account UI');

  phase='camera denial and file controls under the worker';
  await page.goto(base+'/assets');await page.locator('#asset-content[aria-busy=false]').waitFor();
  await page.locator('#asset-scan-general').click();await page.locator('[data-scan-input=camera]').click();
  await page.evaluate(()=>Object.defineProperty(navigator,'mediaDevices',{configurable:true,value:{getUserMedia:async()=>{throw new DOMException('denied','NotAllowedError');}}}));
  await page.locator('#asset-camera-start').click();await page.locator('#asset-camera-status').filter({hasText:/权限|授权|允许|拒绝/}).waitFor();
  assert.equal(await page.locator('#asset-camera-photo').getAttribute('type'),'file');
  await page.locator('[data-scan-input=gun]').click();assert.equal(await page.locator('#asset-scan-raw').isVisible(),true);
  await page.locator('#asset-scan-close').click();
  await page.evaluate(async values=>await AssetLabels.open(values),fixture.units);
  await page.locator('#asset-label-preview canvas').first().waitFor();
  const pdfBytes=Buffer.from(await page.locator('#asset-label-download').evaluate(async link=>Array.from(new Uint8Array(await (await fetch(link.href)).arrayBuffer()))));
  assert.equal(pdfBytes.subarray(0,5).toString(),'%PDF-');
  const downloaded=page.waitForEvent('download');await page.locator('#asset-label-download').click();
  assert.deepEqual(fs.readFileSync(await (await downloaded).path()),pdfBytes);
  await page.locator('#asset-label-close').click();
  const attachment=await page.evaluate(async task=>{
   const canvas=document.createElement('canvas');canvas.width=32;canvas.height=32;
   canvas.getContext('2d').fillRect(0,0,32,32);
   const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));
   const form=new FormData();form.append('files',blob,'pwa-synthetic.png');form.append('description','PWA synthetic upload');
   const response=await fetch(`/api/task/${task}/attachments`,{method:'POST',body:form});
   return {status:response.status,...await response.json()};
  },fixture.task_id);
  assert.equal(attachment.status,200,JSON.stringify(attachment));
  const attachmentURL=attachment.attachments[0].url;
  const image=await page.evaluate(async url=>{
   const response=await fetch(url);return {status:response.status,type:response.headers.get('Content-Type'),size:(await response.arrayBuffer()).byteLength};
  },attachmentURL);
  assert.equal(image.status,200);assert.match(image.type,/image\//);assert.ok(image.size>0);
  await page.evaluate(url=>{const link=document.createElement('a');link.id='pwa-test-download';link.href=url;link.download='pwa-synthetic.png';link.textContent='下载合成截图';document.body.append(link);},attachmentURL);
  const imageDownload=page.waitForEvent('download');await page.locator('#pwa-test-download').click();
  assert.ok(fs.statSync(await (await imageDownload).path()).size>0);
  // The actual authenticated PDF generator still returns bytes, never cached.
  const pdf=await page.evaluate(async()=>{
   const response=await fetch('/api/assets/labels.pdf?item_ids=999999');return {status:response.status,type:response.headers.get('Content-Type')};
  });
  assert.ok(pdf.status>=400,'invalid private PDF target retains the server error');
  assert.equal(await page.locator('#asset-label-download').getAttribute('download'),'asset-labels.pdf');
  await page.goto(base+'/invoices/');assert.ok(await page.locator('input[type=file]').count()>0);
  passed.push('camera denial keeps photo/manual inputs; actual label PDF preview/download and multipart image upload/protected download work under worker; private errors retain meaning');

  phase='private-cache isolation and offline navigation';
  await page.goto(base+'/tasks');
  const before=await cacheSnapshot(page);const owned=Object.keys(before).filter(name=>name.startsWith('xtyf-pwa-public-'));
  assert.equal(owned.length,1);assert.ok(before[owned[0]].length>=2);
  for(const rows of Object.values(before))for(const row of rows){
   assert.match(new URL(row.url).pathname,/^\/static\/(pwa\/|pwa\.(js|css)$)/);
   assert.ok(row.size<=512*1024);
  }
  await context.setOffline(true);
  const apiOffline=await page.evaluate(async()=>{try{await fetch('/api/data');return false;}catch(_){return true;}});assert.equal(apiOffline,true);
  await page.goto(base+'/tasks');assert.match(await page.locator('h1').textContent(),/当前离线/);
  assert.equal(await page.locator('.task,[data-task-id]').count(),0);
  assert.equal(await page.evaluate(()=>document.documentElement.textContent.includes('PWA-PRIVATE-TASK-SENTINEL')),false);
  await page.screenshot({path:path.join(output,'offline.png'),fullPage:true});
  await context.setOffline(false);await page.locator('a').click();await page.locator('.workspace-sidebar').waitFor();
  await page.evaluate(async()=>{for(const name of await caches.keys())await caches.delete(name);});
  await page.reload();assert.equal(new URL(page.url()).pathname,'/tasks');
  passed.push('only bounded public resources cached; offline APIs fail; navigation gets data-free notice; online retry and cache deletion preserve session');

  phase='deployment update waits without discarding drafts';
  await other.close();other=null;await browser.close();browser=null;
  await page.goto(base+'/account');
  const oldVersion=await page.evaluate(async()=>{const r=await fetch('/sw.js');return (await r.text()).match(/"version":\s*"([^"]+)"/)[1];});
  await page.locator('#account-profile-form [name=username]').fill('unsaved-pwa-draft');
  const release=await api(page,'/__pwa_fixture__/release',{});assert.equal(release.ok,true);assert.notEqual(release.version,oldVersion);
  await page.evaluate(async()=>{await (await navigator.serviceWorker.getRegistration()).update();});
  await until(()=>page.evaluate(async()=>Boolean((await navigator.serviceWorker.getRegistration())?.waiting)),'waiting update');
  assert.equal(await page.locator('#account-profile-form [name=username]').inputValue(),'unsaved-pwa-draft');
  await page.locator('#pwa-status').filter({hasText:'更新已准备好'}).waitFor();
  await context.close();context=await open();page=context.pages()[0]||await context.newPage();await page.goto(base+'/account');await controlled(page);
  await until(()=>page.evaluate(async version=>{
   const names=(await caches.keys()).filter(n=>n.startsWith('xtyf-pwa-public-'));return names.length===1&&names[0].endsWith(version);
  },release.version),'new cache activation');
  passed.push('new worker waits with draft intact; closing all windows activates new content version and removes old owned cache');

  phase='logout and account switch';
  await page.locator('form[action="/logout"] button').click();await page.waitForURL(/\/login/);
  assert.equal(await page.evaluate(async url=>(await fetch(url)).status,attachmentURL),401);
  await page.goto(base+'/tasks');await page.waitForURL(/\/login/);
  await login(page,base,'pwa_member_b','pwa-member-initial','/account');
  assert.equal((await api(page,'/api/password',{old:'pwa-member-initial',new:'pwa-member-password'})).ok,true);
  await page.goto(base+'/account');assert.equal(await page.locator('html').getAttribute('data-theme'),'light');
  for(const rows of Object.values(await cacheSnapshot(page)))for(const row of rows)assert.ok(new URL(row.url).pathname.startsWith('/static/'));
  passed.push('logout denies protected pages; another account has its own server theme and no private cache');

  phase='worker retirement leaves unrelated storage and cookies intact';
  await page.evaluate(async()=>{await caches.open('unrelated-test-cache');localStorage.setItem('pwa-test-preserved','yes');});
  assert.equal((await api(page,'/__pwa_fixture__/retire',{})).ok,true);
  await page.evaluate(async()=>{await (await navigator.serviceWorker.getRegistration()).update();});
  // Unregister visibility can change while the activate event is still running.
  // Wait for both observable parts of the asynchronous retirement to finish.
  await until(()=>page.evaluate(async()=>!(await navigator.serviceWorker.getRegistration())&&
    !(await caches.keys()).some(name=>name.startsWith('xtyf-pwa-public-'))),'complete retirement');
  const retired=await page.evaluate(async()=>({names:await caches.keys(),value:localStorage.getItem('pwa-test-preserved')}));
  assert.deepEqual(retired.names,['unrelated-test-cache']);assert.equal(retired.value,'yes');
  await page.reload();assert.equal(new URL(page.url()).pathname,'/account');
  assert.equal(await page.locator('link[rel=manifest]').count(),0);
  passed.push('retirement unregisters and removes only PWA caches, retaining session and unrelated local state');
  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(output,'result.json'),JSON.stringify({ok:true,passed,realDevices:false,production:false,actualInstallation:false},null,2));
  console.log('PWA_OK '+JSON.stringify({passed,realDevices:false,production:false,actualInstallation:false}));
 }catch(error){
  await recordFailure(error,{output,page,engine:'Chromium',phase,passed,pageErrors:errors});console.error(log.slice(-3500));process.exitCode=1;
 }finally{
  if(other)await other.close();if(browser)await browser.close();if(context)await context.close();server.kill();fs.rmSync(profile,{recursive:true,force:true});
 }
})();
