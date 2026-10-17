'use strict';
// Exercises the actual Tauri/WebView2 executable. No simulated native IPC is injected.
const assert = require('node:assert/strict');
const {spawn, execFileSync} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const crypto = require('node:crypto');
const {once} = require('node:events');
let playwright;
try { playwright = require('playwright'); }
catch (_) {
  const driver = execFileSync(process.env.PLAYWRIGHT_PYTHON || 'python', ['-c', 'import pathlib,playwright; print(pathlib.Path(playwright.__file__).parent/"driver/package")'], {encoding:'utf8'}).trim();
  playwright = require(driver);
}
const root = path.resolve(__dirname, '../..');
const fixturePython = process.env.REVIEW_PYTHON || (fs.existsSync(path.join(root,'.venv/Scripts/python.exe')) ? path.join(root,'.venv/Scripts/python.exe') : 'python');
const executable = path.resolve(process.env.XTYF_TEST_EXE || path.join(root,'.build-tools/tauri-target-windows/debug/xtyf-manager.exe'));
const output = path.join(root,'.build-tools/tauri-runtime-check');
fs.mkdirSync(output,{recursive:true});
const userData = fs.mkdtempSync(path.join(output,'profile-'));
const results = [], pageErrors = [], webRequests = [], externalRequests = [], nativeMenus = [];
const uiChecks = {platform:'Windows WebView2',androidDeviceRuntimeVerified:false};
let backend, application, browser, externalServer, backendLog = '', appLog = '';
const sleep = ms => new Promise(resolve=>setTimeout(resolve,ms));
async function waitFor(check,label,timeout=30000) {
  const end = Date.now()+timeout;
  while(Date.now()<end) { try { const value=await check(); if(value)return value; } catch(_) {} await sleep(150); }
  throw new Error('Timed out: '+label+'\n'+appLog.slice(-2000));
}
async function startApp() {
  application=spawn(executable,[],{cwd:root,windowsHide:true,env:{...process.env,XTYF_SMOKE_DIR:userData,WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS:'--remote-debugging-port=9224'},stdio:['ignore','pipe','pipe']});
  application.stdout.on('data',data=>appLog+=data);application.stderr.on('data',data=>appLog+=data);
  await waitFor(async()=>{const response=await fetch('http://127.0.0.1:9224/json/version');return response.ok;},'WebView2 CDP',45000);
  browser=await waitFor(()=>playwright.chromium.connectOverCDP('http://127.0.0.1:9224',{timeout:3000}),'WebView2 CDP handshake');
  const page=await waitFor(()=>browser.contexts().flatMap(context=>context.pages()).find(page=>/tauri\.localhost/.test(page.url())),'packaged root page');
  page.on('pageerror',error=>pageErrors.push(error.message));
  page.on('request',request=>{if(/^https?:/.test(request.url())&&!/^https?:\/\/(tauri|ipc|asset)\.localhost/.test(request.url()))webRequests.push(request.url());});
  page.setDefaultTimeout(20000);
  return page;
}
async function stopApp() {
  if(browser){await browser.close().catch(()=>{});browser=null;}
  if(application){
    if(application.exitCode===null){application.kill();await Promise.race([once(application,'exit'),sleep(5000)]).catch(()=>{});}
    application=null;
  }
  await waitFor(async()=>{try{await fetch('http://127.0.0.1:9224/json/version',{signal:AbortSignal.timeout(500)});return false;}catch(_){return true;}},'owned WebView2 shutdown',10000).catch(()=>{});
}
function inspectNativeMenus(pid) {
  const code=`Add-Type @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
public class MenuProbe {
 public delegate bool Callback(IntPtr window, IntPtr data);
 [DllImport("user32.dll")] public static extern bool EnumWindows(Callback callback, IntPtr data);
 [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr window, out uint process);
 [DllImport("user32.dll")] public static extern IntPtr GetMenu(IntPtr window);
 [DllImport("user32.dll",CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr window, StringBuilder text, int count);
 public static string[] Inspect(uint expected) {var result=new List<string>(); EnumWindows((window,data)=>{uint process;GetWindowThreadProcessId(window,out process);if(process==expected){var text=new StringBuilder(1024);GetWindowText(window,text,1024);if(text.Length>0)result.Add(GetMenu(window).ToInt64()+"|"+text.ToString());}return true;},IntPtr.Zero);return result.ToArray();}
}
'@
[MenuProbe]::Inspect(${pid}) | ConvertTo-Json -Compress`;
  const raw=execFileSync('powershell.exe',['-NoProfile','-NonInteractive','-Command',code],{encoding:'utf8',windowsHide:true}).trim();
  const values=JSON.parse(raw);return (Array.isArray(values)?values:[values]).map(value=>({menuHandle:Number(value.split('|')[0]),title:value.split('|').slice(1).join('|')}));
}
async function targetEvaluate(target,expression) {
  const socket=new WebSocket(target.webSocketDebuggerUrl);await once(socket,'open');
  try {
    return await new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>reject(new Error('Remote view evaluation timed out')),10000);
      socket.addEventListener('message',event=>{const message=JSON.parse(event.data);if(message.id!==1)return;clearTimeout(timer);if(message.error||message.result?.exceptionDetails)reject(new Error(JSON.stringify(message)));else resolve(message.result.result.value);});
      socket.send(JSON.stringify({id:1,method:'Runtime.evaluate',params:{expression,returnByValue:true,awaitPromise:true}}));
    });
  } finally {socket.close();}
}
function pass(text) {results.push(text);console.log(text);}
async function nativeAPI(page, apiPath, data) {
  const response=await page.evaluate(async({apiPath,data})=>{
    const result=await XTYF.request(apiPath,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
    return {status:result.status,body:await result.json()};
  },{apiPath,data});
  assert(response.status>=200&&response.status<300,`Isolated fixture API ${apiPath}: ${JSON.stringify(response.body)}`);
  assert.notEqual(response.body.ok,false,`Isolated fixture API ${apiPath} failed`);
  return response.body;
}
async function accountGeometry(frame) {
  return frame.locator('.ux-account-grid').evaluate(grid=>({
    viewport:innerWidth,
    panels:Object.fromEntries([...grid.children].filter(node=>node.matches('.panel')).map(node=>{
      const name=[...node.classList].find(value=>/^ux-account-(profile|email|appearance|editor|password)$/.test(value));
      const {x,y,width,height}=node.getBoundingClientRect();return [name,{x,y,width,height}];
    }))
  }));
}
function assertAccountRow(geometry,leftName,rightName) {
  assert(geometry.viewport>760,'Account desktop assertion needs a desktop viewport');
  const left=geometry.panels[leftName],right=geometry.panels[rightName];
  assert(left&&right,`Account row needs ${leftName} and ${rightName}`);
  assert(Math.abs(left.y-right.y)<=1,'Account panels must share their row');
  assert(Math.abs(left.height-right.height)<=1,'Account panels in one row must have equal height');
  assert(right.x>left.x+left.width,'Account panels must have a column gap');
  assert(right.width/left.width>=1.55&&right.width/left.width<=1.8,'Account editor column should remain wider than the profile column');
}
async function verifyMemberTaskNavigation(page,frame) {
  const member=await nativeAPI(page,'/api/member',{name:'本地运行验收成员',username:'native_ui_member',password:'fixture-member-password'});
  const created=[];
  for(const [title,status] of [['本地运行验收·进行中','进行中'],['本地运行验收·待办','未开始'],['本地运行验收·已完成','已完成']]) {
    created.push(await nativeAPI(page,'/api/task',{title,status,owner_id:member.id}));
  }
  await page.evaluate(()=>XTYF.navigate('/tasks'));
  await frame.locator('#view-seg [data-view="person"]').click();
  await frame.locator('#f-q').fill('本地运行验收');
  const trigger=frame.locator(`[data-lane-tasks="m-${member.id}"]`);
  await trigger.waitFor();
  const before=await frame.locator('body').evaluate(()=>({filters:{...state.filters},view:state.view}));
  await trigger.click();
  await frame.locator('#modal.member-task-list-dialog').waitFor();
  assert.equal(await frame.locator('#modal [data-task-group="active"] [data-task-id]').count(),2);
  assert.equal(await frame.locator('#modal [data-task-group="finished"] [data-task-id]').count(),1);
  for(const task of created)assert.equal(await frame.locator(`#modal [data-task-id="${task.id}"]`).count(),1);
  await frame.locator('#modal [data-member-task-scroll]').waitFor();
  await page.screenshot({path:path.join(output,'windows-member-task-list.png')});
  await frame.locator(`#modal [data-task-id="${created[0].id}"]`).click();
  await frame.locator('#modal [data-close]').filter({hasText:'返回任务列表'}).waitFor();
  assert.equal(await page.evaluate(()=>window.XTYFClient.back()),true);
  await frame.locator('#modal.member-task-list-dialog').waitFor();
  assert.equal(await frame.locator('#modal [data-task-id]').count(),3);
  assert.equal(await page.evaluate(()=>window.XTYFClient.back()),true);
  await frame.locator('#modal-mask').waitFor({state:'hidden'});
  assert.deepEqual(await frame.locator('body').evaluate(()=>({filters:{...state.filters},view:state.view})),before);
  assert.equal(await frame.locator('#f-q').inputValue(),'本地运行验收');
  assert.equal(await page.evaluate(()=>XTYF.route),'/tasks');
  uiChecks.memberTaskList={source:'real API fixture via native transport',active:2,finished:1,backSequence:['detail','member-task-list','person-lane'],filtersPreserved:true,host:'Windows WebView2; shared Android frontend back handler only'};
  pass('Member task list includes active and completed tasks; shared back handler returns detail → list → lane on Windows');
}
async function verifyAccountGrid(page,frame,forcedGeometry) {
  await page.evaluate(()=>XTYF.navigate('/account'));
  await frame.locator('#account-profile-form').waitFor();
  await waitFor(async()=>!(await frame.locator('#account-group-choices').innerText()).includes('加载中'),'account choices');
  const normal=await accountGeometry(frame);
  assertAccountRow(normal,'ux-account-profile','ux-account-email');
  assertAccountRow(normal,'ux-account-appearance','ux-account-editor');
  assertAccountRow(forcedGeometry,'ux-account-profile','ux-account-password');
  assert(!forcedGeometry.panels['ux-account-email']&&!forcedGeometry.panels['ux-account-editor']);
  await page.screenshot({path:path.join(output,'windows-account-layout.png')});
  uiChecks.accountLayout={normal,forcedPassword:forcedGeometry,equalRowHeights:true,widerRightColumn:true};
  pass('Packaged account panels share row heights and retain the wider editor column, including first-password layout');
}
async function verifyInvoiceButtons(page,frame) {
  // This creates only a queued job in the isolated fixture; its worker is deliberately disabled.
  const job=await nativeAPI(page,'/invoices/api/job/start',{type:'scan_dups'});
  assert(job.job_id,'Isolated invoice job should have an ID');
  await page.evaluate(()=>XTYF.navigate('/invoices/'));
  const selectors=[`#invoiceJobsList [data-job-id="${job.job_id}"]`,'#invoiceSortDirection'];
  await frame.locator(selectors[0]).waitFor();
  const palettes=[];
  for(const theme of ['light','dark']) {
    if(await frame.locator('html').getAttribute('data-theme')!==theme)await frame.locator('[data-theme-toggle]').first().click();
    await waitFor(async()=>await frame.locator('html').getAttribute('data-theme')===theme,'invoice '+theme+' theme');
    for(const selector of selectors) {
      const button=frame.locator(selector);
      await frame.locator('h1').first().hover();
      const readColors=()=>button.evaluate(node=>{
        const probe=document.createElement('span');probe.style.cssText='background:var(--card);color:var(--text);border:1px solid var(--border)';document.body.append(probe);
        const expected=getComputedStyle(probe),actual=getComputedStyle(node);
        const result={background:actual.backgroundColor,text:actual.color,border:actual.borderTopColor,expected:{background:expected.backgroundColor,text:expected.color,border:expected.borderTopColor}};probe.remove();return result;
      });
      await waitFor(async()=>{const colors=await readColors();return colors.background===colors.expected.background&&colors.text===colors.expected.text&&colors.border===colors.expected.border;},`${theme} invoice palette colors`);
      const basic=await readColors();
      assert.deepEqual({background:basic.background,text:basic.text,border:basic.border},basic.expected,`${theme} invoice button colors`);
      await button.hover();
      await waitFor(()=>button.evaluate(node=>{
        const probe=document.createElement('span');probe.style.background='var(--surface-hover)';document.body.append(probe);
        const matches=getComputedStyle(node).backgroundColor===getComputedStyle(probe).backgroundColor;probe.remove();return matches;
      }),theme+' invoice button hover');
      await page.keyboard.press('Tab');await button.focus();
      assert(await button.evaluate(node=>node.matches(':focus-visible')&&parseFloat(getComputedStyle(node).outlineWidth)>0),theme+' invoice button visible keyboard focus');
      await button.evaluate(node=>{node.disabled=true;});
      await waitFor(()=>button.evaluate((node,expected)=>{const style=getComputedStyle(node);return Number(style.opacity)<1&&style.backgroundColor===expected.background&&style.color===expected.text;},basic),theme+' disabled invoice colors');
      const disabled=await button.evaluate(node=>{const style=getComputedStyle(node);return {opacity:Number(style.opacity),background:style.backgroundColor,text:style.color};});
      assert(disabled.opacity<1,theme+' disabled invoice action should be visibly disabled');
      assert.equal(disabled.background,basic.background);assert.equal(disabled.text,basic.text);
      await button.evaluate(node=>{node.disabled=false;node.blur();});
      palettes.push({theme,selector,colors:basic,hover:true,keyboardFocus:true,disabled:true});
    }
    await page.screenshot({path:path.join(output,`windows-invoice-buttons-${theme}.png`)});
  }
  uiChecks.invoiceButtons={fixture:'queued API job; worker disabled',palettes};
  pass('Invoice job and sort buttons follow light/dark palette colors with hover, focus and disabled states');
}
async function main() {
  assert(fs.existsSync(executable),'Build the actual Tauri executable first');
  const binarySha256=crypto.createHash('sha256').update(fs.readFileSync(executable)).digest('hex');
  const buildInfoPath=path.join(output,'runtime-build-info.json');
  execFileSync(executable,['--build-info',buildInfoPath],{cwd:root,windowsHide:true});
  const buildInfo=JSON.parse(fs.readFileSync(buildInfoPath,'utf8'));
  const frontendManifest=fs.readFileSync(process.env.XTYF_FRONTEND_MANIFEST||path.join(root,'clients/frontend/dist/asset-manifest.json'));
  assert.deepEqual(buildInfo.frontend,JSON.parse(frontendManifest),'Executable must contain the expected shared frontend manifest');
  const frontendManifestSha256=crypto.createHash('sha256').update(frontendManifest).digest('hex');
  backend=spawn(fixturePython,['-B',path.join(root,'tests/browser/server.py')],{cwd:root,windowsHide:true,env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'},stdio:['ignore','pipe','pipe']});
  backend.stderr.on('data',data=>backendLog+=data);
  let serverUrl='';backend.stdout.on('data',data=>{const match=String(data).match(/BROWSER_URL=(\S+)/);if(match)serverUrl=match[1];});
  await waitFor(()=>{if(backend.exitCode!==null)throw new Error('Flask fixture failed: '+backendLog);return serverUrl;},'isolated Flask fixture');
  fs.writeFileSync(path.join(userData,'server.json'),JSON.stringify({serverUrl}));
  let page=await startApp();
  await waitFor(()=>page.evaluate(()=>window.XTYF?.compatible),'native bootstrap');
  assert.match(page.url(),/^https?:\/\/tauri\.localhost/);
  assert.equal(await page.evaluate(()=>typeof window.__TAURI_INTERNALS__.invoke),'function');
  assert(await page.locator('#page-host iframe').getAttribute('srcdoc'),'Business UI is a local document');
  const config=await page.evaluate(()=>XTYFNative.invoke('get_config',{}));assert.equal(config.serverUrl,serverUrl);
  pass('Actual Windows Tauri executable and native IPC load local login');
  nativeMenus.push(...inspectNativeMenus(application.pid));assert(nativeMenus.length);assert(nativeMenus.every(window=>window.menuHandle===0));
  pass('Actual native Windows application windows have no attached menu');
  let frame=page.frameLocator('#page-host iframe');
  await frame.locator('input[name=username]').fill('admin');await frame.locator('input[name=password]').fill('browser-test-initial');await frame.locator('button[type=submit]').click();
  await waitFor(()=>page.evaluate(()=>XTYF.bootstrap.authenticated&&XTYF.route==='/account'),'initial-password gate');
  await frame.locator('#account-password-form').waitFor();
  const forcedAccountGeometry=await accountGeometry(frame);
  await frame.locator('#account-password-form [name=old]').fill('browser-test-initial');
  await frame.locator('#account-password-form [name=new]').fill('local-runtime-password-2026');
  await frame.locator('#account-password-form [name=confirm]').fill('local-runtime-password-2026');
  await frame.locator('#account-password-form button[type=submit]').click();
  await waitFor(()=>page.evaluate(()=>XTYF.bootstrap.authenticated&&!XTYF.bootstrap.me.must_change_password),'password change');
  pass('Native JSON login, CSRF and forced password change against isolated Flask database');
  for(const [route,label] of [['/','工作台'],['/tasks','任务中心'],['/manage','任务管理'],['/members','成员管理'],['/discussions/','协作'],['/funds/','资金'],['/invoices/','发票'],['/account','账户'],['/admin/mail','邮件'],['/admin/security','权限']]) {
    await page.evaluate(route=>XTYF.navigate(route),route);await frame.locator('h1').first().waitFor();
    await waitFor(async()=>(await frame.locator('h1').first().innerText()).includes(label),'local route '+route);
  }
  pass('Ten business routes render as packaged local documents through native API transport');
  await verifyMemberTaskNavigation(page,frame);
  await verifyAccountGrid(page,frame,forcedAccountGeometry);
  await verifyInvoiceButtons(page,frame);
  await page.evaluate(()=>XTYF.navigate('/tasks'));await frame.locator('h1').first().waitFor();await page.screenshot({path:path.join(output,'windows-tasks.png')});
  await page.keyboard.press('Alt');await page.screenshot({path:path.join(output,'windows-alt.png')});await page.keyboard.press('Escape');
  assert.deepEqual(webRequests,[],'Privileged main WebView must not fetch remote business pages or CDN assets');
  pass('Privileged WebView keeps all page resources local');
  externalServer=http.createServer((request,response)=>{externalRequests.push(request.headers);response.setHeader('Content-Type','text/html');response.end('<!doctype html><h1 id="external-check">Isolated external view</h1>');});
  externalServer.listen(0,'127.0.0.1');await once(externalServer,'listening');
  const externalUrl='http://127.0.0.1:'+externalServer.address().port+'/';
  await page.evaluate(url=>XTYFNative.invoke('open_external',{url}),externalUrl);
  // Incognito native WebViews are separate browser contexts. Read their actual CDP target directly.
  const external=await waitFor(async()=>{const targets=await(await fetch('http://127.0.0.1:9224/json/list')).json();return targets.find(target=>target.url===externalUrl);},'native external WebView');
  assert.equal(await targetEvaluate(external,"Boolean(document.getElementById('external-check'))"),true);
  const remoteCall=await targetEvaluate(external,"(async()=>{if(!window.__TAURI_INTERNALS__?.invoke)return 'bridge-unavailable';try{await window.__TAURI_INTERNALS__.invoke('native_call',{action:'get_config',args:{}});return 'unexpected-access';}catch(_){return 'denied';}})()");
  assert.notEqual(remoteCall,'unexpected-access');assert(externalRequests.length);for(const headers of externalRequests){assert(!headers.cookie);assert(!headers['x-csrf-token']);assert(!headers.authorization);}
  pass('External page opens in app without native business access or authentication credentials');
  const controls=browser.contexts().flatMap(context=>context.pages()).find(page=>page.url().includes('external.html'));
  if(controls)await controls.evaluate(()=>window.__TAURI_INTERNALS__.invoke('native_call',{action:'external_close',args:{}})).catch(()=>{});
  await stopApp();page=await startApp();frame=page.frameLocator('#page-host iframe');
  await waitFor(()=>page.evaluate(()=>XTYF.bootstrap?.authenticated),'persistent login after app restart');pass('Native server session persists across application restart');
  await page.evaluate(()=>XTYF.navigate('/account'));await frame.locator('form[action="/logout"] button').click();
  await waitFor(()=>page.evaluate(()=>!XTYF.bootstrap.authenticated&&XTYF.route.startsWith('/login')),'native logout');pass('Native logout returns local login');
  backend.kill();await Promise.race([once(backend,'exit'),sleep(3000)]).catch(()=>{});backend=null;
  await page.reload();await frame.locator('#login-form').waitFor();await page.locator('#connection-status').waitFor({state:'visible'});
  await page.locator('#client-settings-open').click();await page.locator('#server-url').waitFor({state:'visible'});await page.screenshot({path:path.join(output,'windows-offline-settings.png')});await page.locator('#settings-cancel').click();
  pass('With server stopped, local login and server settings remain usable');
  assert.deepEqual(pageErrors,[],'No JavaScript runtime errors');
  return {runtime:'actual Windows Tauri executable with Microsoft WebView2',executable,binarySha256,frontendManifestSha256,version:config.version,passed:results,pageErrors,remoteMainWebRequests:webRequests,externalCredentialLeak:false,nativeMenus,uiChecks};
}
(async()=>{
  let report;
  try{report=await main();console.log(JSON.stringify({passed:results.length,report:path.join(output,'result.json')}));}
  catch(error){let debugTargets=[];try{debugTargets=await(await fetch('http://127.0.0.1:9224/json/list')).json();}catch(_){}report={runtime:'actual Windows Tauri executable with Microsoft WebView2',passed:results,error:error.stack,pageErrors,remoteMainWebRequests:webRequests,externalRequests,debugTargets,uiChecks};console.error(error);process.exitCode=1;}
  finally{fs.writeFileSync(path.join(output,'result.json'),JSON.stringify(report,null,2));fs.writeFileSync(path.join(output,'application.log'),appLog);fs.writeFileSync(path.join(output,'backend.log'),backendLog);await stopApp();if(backend)backend.kill();if(externalServer)externalServer.close();}
})();
