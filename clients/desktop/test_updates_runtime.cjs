'use strict';
// Real Windows WebView2 executable and native transport; no simulated IPC.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),http=require('node:http');
const {spawn,execFileSync}=require('node:child_process'),{once}=require('node:events');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'../..'),output=path.join(root,'.build-tools/client-updates-20261018/windows-runtime');
fs.mkdirSync(output,{recursive:true});
const fixtures=path.join(output,'fixtures');
execFileSync(process.env.REVIEW_PYTHON||path.join(root,'.venv/Scripts/python.exe'),[path.join(__dirname,'test_update_fixture.py'),fixtures],{cwd:root,stdio:'inherit',windowsHide:true});
const executable=process.env.XTYF_TEST_EXE||path.join(root,'.build-tools/tauri-target-windows/debug/xtyf-manager.exe');
let app,browser,activeRelease='A',server,offline=false;const checks=[],requests=[],errors=[];
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function until(check,label,timeout=30000){const end=Date.now()+timeout;let last;while(Date.now()<end){try{const result=await check();if(result)return result;}catch(error){last=error;}await sleep(100);}throw new Error(label+': '+(last?.message||'timeout'));}
function payload(label){const envelope=JSON.parse(fs.readFileSync(path.join(fixtures,label,'update.json')));return JSON.parse(Buffer.from(envelope.payload,'base64'));}
async function start(profile){
  const log=fs.openSync(path.join(output,'application.log'),'a');
  app=spawn(executable,[],{cwd:root,windowsHide:true,stdio:['ignore',log,log],env:{...process.env,HTTP_PROXY:'',HTTPS_PROXY:'',ALL_PROXY:'',NO_PROXY:'*',XTYF_SMOKE_DIR:profile,XTYF_UI_TEST_PUBLIC_KEY:path.join(fixtures,'public.pem'),WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS:'--remote-debugging-port=9226'}});
  fs.closeSync(log);
  await until(async()=>{const response=await fetch('http://127.0.0.1:9226/json/version');return response.ok;},'CDP endpoint');
  browser=await until(()=>chromium.connectOverCDP('http://127.0.0.1:9226',{timeout:2000}),'CDP connect');
  const page=await until(()=>browser.contexts().flatMap(context=>context.pages()).find(page=>/tauri\.localhost/.test(page.url())),'trusted main');
  page.on('pageerror',error=>errors.push(error.message));return page;
}
async function currentPage(){return until(()=>browser.contexts().flatMap(context=>context.pages()).find(page=>!page.isClosed()&&/tauri\.localhost/.test(page.url())),'current main');}
async function stop(){if(browser){await browser.close().catch(()=>{});browser=null;}if(app){if(app.exitCode===null){app.kill();await Promise.race([once(app,'exit'),sleep(5000)]);}app=null;}await until(async()=>{try{await fetch('http://127.0.0.1:9226/json/version',{signal:AbortSignal.timeout(300)});return false;}catch(_){return true;}},'CDP shutdown',10000);}
async function invoke(page,action,args={}){return page.evaluate(({action,args})=>XTYFNative.invoke(action,args),{action,args});}
async function healthy(page,label){await page.waitForFunction(()=>!!window.XTYFNative);await until(async()=>{const status=await invoke(page,'update_status');return status.currentVersion===(label?'native-smoke-'+label:'1.0.10');},'active version '+label);await page.waitForFunction(()=>document.querySelector('#page-host iframe'));await until(async()=>{const data=JSON.parse(fs.readFileSync(stateFile));return data.trial===null;},'health confirmation');}
let stateFile;
async function profile(name,origin){const directory=fs.mkdtempSync(path.join(output,name+'-'));fs.writeFileSync(path.join(directory,'server.json'),JSON.stringify({serverUrl:origin}));const hash=require('node:crypto').createHash('sha256').update(origin).digest('hex');stateFile=path.join(directory,'ui-updates',hash,'state.json');return directory;}
async function main(){
  server=http.createServer((request,response)=>{requests.push(request.url);if(offline){request.socket.destroy();return;}if(request.url==='/api/client/updates'){response.setHeader('Content-Type','application/json');response.end(fs.readFileSync(path.join(fixtures,activeRelease,'update.json')));return;}if(request.url.startsWith('/api/client/ui/')){const zip=path.join(fixtures,activeRelease,'bundles',path.basename(request.url));if(!fs.existsSync(zip)){response.writeHead(404).end();return;}response.setHeader('Content-Type','application/zip');response.end(fs.readFileSync(zip));return;}response.setHeader('Content-Type','application/json');response.end(JSON.stringify({ok:true,protocolVersion:1,uiCompatibility:{protocolMin:1,protocolMax:1},authenticated:false,me:null,user:null,permissions:{},role_names:{admin:'管理员',manager:'管理人员',member:'成员'},statuses:[],priorities:[]}));});
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const origin='http://127.0.0.1:'+server.address().port;
  const first=await profile('successful',origin);let page=await start(first);await page.waitForFunction(()=>!!window.XTYFNative);
  const config=await invoke(page,'get_config');assert.equal(config.bridgeVersion,2);assert(config.capabilities.includes('image_preview'));assert.equal(config.platform,'windows-x86_64');
  assert.equal((await invoke(page,'check_updates')).status,'pending');assert.equal(await page.locator('body').getAttribute('data-update-fixture'),null);checks.push('Signed A downloaded without replacing bundled active view');
  await stop();page=await start(first);await healthy(page,'A');assert.equal(await page.locator('body').getAttribute('data-update-fixture'),'A');
  activeRelease='B';assert.equal((await invoke(page,'check_updates')).status,'pending');assert.equal(await page.locator('body').getAttribute('data-update-fixture'),'A');checks.push('A remains visible while B is downloaded');
  await stop();page=await start(first);await healthy(page,'B');assert.equal(await page.locator('body').getAttribute('data-update-fixture'),'B');await page.screenshot({path:path.join(output,'B-active.png')});checks.push('Cold restart runs B and marks it healthy');
  await stop();activeRelease='A';const fallback=await profile('rollback',origin);page=await start(fallback);await page.waitForFunction(()=>!!window.XTYFNative);assert.equal((await invoke(page,'check_updates')).status,'pending');await stop();page=await start(fallback);await healthy(page,'A');
  activeRelease='broken-B';assert.equal((await invoke(page,'check_updates')).status,'pending');const oldToken=await page.evaluate(()=>window.__XTYF_VIEW_TOKEN__);await invoke(page,'apply_update');
  await until(async()=>{const next=await currentPage();return await next.locator('body').getAttribute('data-update-fixture')==='broken-B';},'intentionally broken B');
  await until(async()=>{const next=await currentPage();return await next.locator('body').getAttribute('data-update-fixture')==='A';},'15 second watchdog returns A',25000);page=await currentPage();await healthy(page,'A');
  await assert.rejects(invoke(page,'ui_ready',{viewToken:oldToken}),/旧页面/);assert(JSON.parse(fs.readFileSync(stateFile)).quarantined.includes(payload('broken-B').bundle.id));await page.screenshot({path:path.join(output,'A-after-rollback.png')});checks.push('Real broken B document fails readiness and watchdog recreates healthy A; stale token rejected');
  await stop();offline=true;page=await start(fallback);await healthy(page,'A');await page.locator('#client-settings-open').click();assert(await page.locator('#client-settings').evaluate(dialog=>dialog.open));assert.equal(await page.locator('#server-url').inputValue(),origin);await page.screenshot({path:path.join(output,'offline-settings.png')});checks.push('Offline startup preserves cached A and local settings');
  assert.deepEqual(errors,[]);fs.writeFileSync(path.join(output,'result.json'),JSON.stringify({ok:true,engine:'Windows WebView2',checks,errors,requests,testKey:'independent ephemeral RSA3072',installerGenerated:false},null,2));console.log(JSON.stringify({ok:true,checks},null,2));
}
main().catch(error=>{console.error(error);fs.writeFileSync(path.join(output,'result.json'),JSON.stringify({ok:false,checks,errors,error:error.stack,requests},null,2));process.exitCode=1;}).finally(async()=>{await stop().catch(()=>{});if(server)await new Promise(resolve=>server.close(resolve));});
