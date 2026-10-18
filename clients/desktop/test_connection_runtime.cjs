'use strict';
// Actual Windows WebView2 and native IPC. All accounts/files are synthetic and
// the application runs against an ephemeral HTTP server and private profile.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),http=require('node:http');
const {spawn}=require('node:child_process'),{once}=require('node:events');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'../..'),output=path.join(root,'.build-tools/connection-delivery/webview2');
fs.mkdirSync(output,{recursive:true});
const profile=fs.mkdtempSync(path.join(output,'profile-'));
const executable=process.env.XTYF_TEST_EXE||path.join(root,'.build-tools/tauri-target-windows/debug/xtyf-manager.exe');
const checks=[],errors=[];let application,browser,server,outsideServer,origin,appLog='',uploaded=false,outsideCookie=false,slowStarted=false,slowTimer;
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function until(check,label,timeout=30000){const end=Date.now()+timeout;let error;while(Date.now()<end){try{const value=await check();if(value)return value;}catch(e){error=e;}await sleep(100);}throw new Error(label+': '+(error?.message||'timed out')+'\n'+appLog.slice(-1000));}
async function start(){
  application=spawn(executable,[],{cwd:root,windowsHide:true,stdio:['ignore','pipe','pipe'],env:{...process.env,HTTP_PROXY:'',HTTPS_PROXY:'',ALL_PROXY:'',NO_PROXY:'*',XTYF_SMOKE_DIR:profile,WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS:'--remote-debugging-port=9238'}});
  application.stdout.on('data',data=>appLog+=data);application.stderr.on('data',data=>appLog+=data);
  await until(async()=>{const response=await fetch('http://127.0.0.1:9238/json/version');return response.ok;},'WebView2 CDP');
  browser=await until(()=>chromium.connectOverCDP('http://127.0.0.1:9238',{timeout:2000}),'CDP connection');
  const session=await browser.newBrowserCDPSession();
  await session.send('Browser.setDownloadBehavior',{behavior:'default'});await session.detach();
  return pageMatching(page=>/^https?:\/\/tauri\.localhost\/(?:index.html)?(?:[?#].*)?$/.test(page.url()));
}
async function pageMatching(predicate){const page=await until(()=>browser.contexts().flatMap(context=>context.pages()).find(page=>!page.isClosed()&&predicate(page)),'WebView target');page.setDefaultTimeout(20000);return page;}
async function invoke(page,action,args={}){return page.evaluate(({action,args})=>window.__TAURI_INTERNALS__.invoke('native_call',{action,args}),{action,args});}
async function collapsedWebsiteMenu(toolbar,remote,local){
  await toolbar.getByRole('button',{name:'打开客户端菜单',exact:true}).waitFor();
  assert.equal(await toolbar.locator('#menu-toggle').getAttribute('aria-expanded'),'false');
  assert.equal(await toolbar.locator('#website-navigation').isVisible(),false);
  assert.equal(await toolbar.getByRole('button',{name:'设置',exact:true}).isVisible(),false);
  const fullHeight=await local.evaluate(()=>innerHeight);
  await until(async()=>Math.abs(await remote.evaluate(()=>innerHeight)-fullHeight)<=1,'collapsed menu leaves the website at full window height');
}
async function checkWebsiteMenu(toolbar,remote,local){
  await collapsedWebsiteMenu(toolbar,remote,local);
  const fullHeight=await remote.evaluate(()=>innerHeight);
  const documentMarker=await remote.evaluate(()=>{window.websiteMenuDocumentMarker=crypto.randomUUID();return window.websiteMenuDocumentMarker;});
  await remote.locator('#draft').fill('draft preserved while toggling client menu');
  let confirmations=0;
  const dismissUnexpected=dialog=>{confirmations++;return dialog.dismiss();};
  toolbar.on('dialog',dismissUnexpected);
  try{
    await toolbar.getByRole('button',{name:'打开客户端菜单',exact:true}).click();
    await toolbar.getByRole('button',{name:'收起客户端菜单',exact:true}).waitFor();
    assert.equal(await toolbar.locator('#menu-toggle').getAttribute('aria-expanded'),'true');
    assert.equal(await toolbar.locator('#website-navigation').isVisible(),true);
    await toolbar.getByRole('button',{name:'设置',exact:true}).waitFor();
    await until(async()=>Math.abs(await remote.evaluate(()=>innerHeight)-(fullHeight-80))<=1,'expanded toolbar uses 80 CSS pixels');
    assert.equal(await remote.evaluate(()=>window.websiteMenuDocumentMarker),documentMarker,'expanding the menu must not reload the website');
    assert.equal(await remote.locator('#draft').inputValue(),'draft preserved while toggling client menu');
    await toolbar.getByRole('button',{name:'收起客户端菜单',exact:true}).click();
    await collapsedWebsiteMenu(toolbar,remote,local);
    assert.equal(await remote.evaluate(()=>window.websiteMenuDocumentMarker),documentMarker,'collapsing the menu must not reload the website');
    assert.equal(await remote.locator('#draft').inputValue(),'draft preserved while toggling client menu');
    await toolbar.getByRole('button',{name:'打开客户端菜单',exact:true}).press('Enter');
    await toolbar.getByRole('button',{name:'收起客户端菜单',exact:true}).press('Escape');
    await collapsedWebsiteMenu(toolbar,remote,local);
    assert.equal(await remote.evaluate(()=>window.websiteMenuDocumentMarker),documentMarker,'keyboard toggling must keep the website document');
    assert.equal(await remote.locator('#draft').inputValue(),'draft preserved while toggling client menu');
    assert.equal(confirmations,0,'showing or hiding the menu must not prompt about unsaved content');
  }finally{toolbar.off('dialog',dismissUnexpected);}
}
async function stop(){
  if(browser){await browser.close().catch(()=>{});browser=null;}
  if(application){if(application.exitCode===null){application.kill();await Promise.race([once(application,'exit'),sleep(5000)]);}application=null;}
  await until(async()=>{try{await fetch('http://127.0.0.1:9238/json/version',{signal:AbortSignal.timeout(300)});return false;}catch(_){return true;}},'owned CDP shutdown',10000);
}
function html(authenticated,outside){return `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>隔离网站连接验收</title><body>
  <h1>${authenticated?'已登录测试网站':'测试网站登录'}</h1>
  ${authenticated?`<input id="image" type="file" accept="image/png"><button id="upload">上传合成图片</button><span id="upload-status"></span><a id="http-download" href="/download" download="synthetic.txt">下载文件</a><a id="slow-download" href="/slow-download" download="slow.txt">慢下载</a><button id="blob-download">下载生成文件</button><a id="popup" href="/next" target="_blank">站内新窗口</a><a id="outside" href="${outside}" target="_blank">站外网页</a><input id="draft" aria-label="草稿">`:'<form method="post" action="/login"><input name="username" value="fixture-user"><button id="login">登录</button></form>'}
  <script>document.getElementById('upload')?.addEventListener('click',async()=>{const body=new FormData();body.append('image',document.getElementById('image').files[0]);const response=await fetch('/upload',{method:'POST',body});document.getElementById('upload-status').textContent=response.ok?'上传成功':'上传失败';});document.getElementById('blob-download')?.addEventListener('click',()=>{const link=document.createElement('a');link.href=URL.createObjectURL(new Blob(['synthetic blob download'],{type:'text/plain'}));link.download='generated.txt';document.body.append(link);link.click();setTimeout(()=>{URL.revokeObjectURL(link.href);link.remove();},1000);});</script></body></html>`;}

// Drive the OS Save dialog owned by this test process. No developer-only save
// bypass is added to the product, and no user's unrelated window is touched.
const dialogScript=path.join(output,'save-owned-dialog.ps1');
fs.writeFileSync(dialogScript,'\uFEFF'+String.raw`param([int]$OwnerPid,[string]$TargetPath,[string]$Mode='save')
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type @'
using System;
using System.Runtime.InteropServices;
public class OwnedDialogMessages {
 [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern IntPtr SendMessage(IntPtr window, uint message, IntPtr wparam, string text);
 [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr window, uint message, IntPtr wparam, IntPtr lparam);
}
'@
$until=[DateTime]::UtcNow.AddSeconds(20)
while([DateTime]::UtcNow -lt $until){
  $pidCondition=New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty,$OwnerPid)
  $windows=[System.Windows.Automation.AutomationElement]::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children,$pidCondition)
  foreach($window in $windows){
    if($window.Current.Name -ne '保存网站文件'){continue}
    if($Mode -eq 'cancel'){
      $cancelCondition=New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::AutomationIdProperty,'2')
      $cancel=$window.FindAll([System.Windows.Automation.TreeScope]::Descendants,$cancelCondition) | Where-Object {$_.Current.Name -match '^(取消|Cancel)'} | Select-Object -First 1
      if($null -eq $cancel){continue}
      [void][OwnedDialogMessages]::PostMessage([IntPtr]$cancel.Current.NativeWindowHandle,245,[IntPtr]::Zero,[IntPtr]::Zero)
      exit 0
    }
    $editCondition=New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::AutomationIdProperty,'1001')
    $edit=$window.FindAll([System.Windows.Automation.TreeScope]::Descendants,$editCondition) | Where-Object {$_.Current.Name -notmatch '^(地址|Address)' -and $_.Current.NativeWindowHandle -ne 0} | Select-Object -First 1
    if($null -eq $edit){continue}
    [void][OwnedDialogMessages]::SendMessage([IntPtr]$edit.Current.NativeWindowHandle,12,[IntPtr]::Zero,$TargetPath)
    $saveCondition=New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::AutomationIdProperty,'1')
    $save=$window.FindAll([System.Windows.Automation.TreeScope]::Descendants,$saveCondition) | Where-Object {$_.Current.Name -match '^(保存|Save)'} | Select-Object -First 1
    if($null -eq $save){continue}
    [void][OwnedDialogMessages]::PostMessage([IntPtr]$save.Current.NativeWindowHandle,245,[IntPtr]::Zero,[IntPtr]::Zero)
    exit 0
  }
  Start-Sleep -Milliseconds 100
}
foreach($window in $windows){$window.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition) | ForEach-Object {$_.Current | Select-Object Name,AutomationId,ControlType} | ConvertTo-Json -Depth 3}
Write-Error 'Owned Save dialog did not appear';exit 1
`,'utf8');
async function saveDownload(page,selector,name,expected,cancel=false){
  const target=path.join(profile,name);
  const helper=spawn('powershell.exe',['-NoProfile','-NonInteractive','-File',dialogScript,'-OwnerPid',String(application.pid),'-TargetPath',target,'-Mode',cancel?'cancel':'save'],{windowsHide:true,stdio:['ignore','pipe','pipe']});
  const exited=once(helper,'exit');
  let log='';helper.stdout.on('data',data=>log+=data);helper.stderr.on('data',data=>log+=data);
  await page.locator(selector).click();
  const [code]=await exited;assert.equal(code,0,log);
  if(cancel){const toolbar=await pageMatching(page=>page.url().endsWith('/website-controls.html'));await until(async()=>/已取消保存/.test(await toolbar.locator('#status').innerText()),'cancelled native Save');assert.equal(fs.readFileSync(target,'utf8'),expected);return;}
  try {await until(()=>fs.existsSync(target),'native download '+name);}
  catch(error){const toolbar=await pageMatching(page=>page.url().endsWith('/website-controls.html'));throw new Error(error.message+'; toolbar: '+await toolbar.locator('#status').innerText());}
  assert.equal(fs.readFileSync(target,'utf8'),expected);
}

async function main(){
  if(process.env.XTYF_PUBLIC_SMOKE==='1'){
    const configured='http://manager.skywalker-rm.online';
    fs.writeFileSync(path.join(profile,'server.json'),JSON.stringify({serverUrl:configured}));
    const local=await start();const website=await pageMatching(page=>page.url().startsWith('https://manager.skywalker-rm.online/'));
    await website.locator('input[name="username"]').waitFor();await website.locator('input[name="password"]').waitFor();
    const config=await invoke(local,'get_config');assert.equal(config.serverUrl,configured);assert.equal(config.effectiveServerUrl,'https://manager.skywalker-rm.online');
    assert.equal(await website.locator('input[name="username"]').inputValue(),'');assert.equal(await website.locator('input[name="password"]').inputValue(),'');
    await website.screenshot({path:path.join(output,'public-login.png')});
    fs.writeFileSync(path.join(output,'public-result.json'),JSON.stringify({ok:true,engine:'actual Windows WebView2',configured:config.serverUrl,effective:config.effectiveServerUrl,url:website.url(),loginFormVisible:true,credentialsEntered:false,installerGenerated:false},null,2));console.log('Public default server opened its HTTPS login page in the actual client; no credentials entered.');return;
  }
  outsideServer=http.createServer((request,response)=>{outsideCookie=!!request.headers.cookie;response.end('<title>Independent external view</title>External');});
  await new Promise(resolve=>outsideServer.listen(0,'127.0.0.1',resolve));const outside='http://127.0.0.1:'+outsideServer.address().port;
  server=http.createServer((request,response)=>{
    const authenticated=request.headers.cookie?.includes('website_session=synthetic');
    if(request.url==='/api/client/bootstrap'){response.writeHead(404,{'Content-Type':'text/html'}).end('<title>Not Found</title>');return;}
    if(request.url==='/api/client/updates'){response.writeHead(200,{'Content-Type':'application/json'}).end('{"available":false}');return;}
    if(request.url==='/login'&&request.method==='POST'){response.writeHead(303,{'Location':'/','Set-Cookie':'website_session=synthetic; Path=/; HttpOnly; Max-Age=3600; SameSite=Lax'}).end();return;}
    if(request.url==='/upload'){let body='';request.on('data',chunk=>body+=chunk.toString('latin1'));request.on('end',()=>{uploaded=authenticated&&body.includes('synthetic.png')&&body.includes('PNG');response.writeHead(uploaded?200:400).end();});return;}
    if(request.url==='/download'){response.writeHead(authenticated?200:403,{'Content-Type':'text/plain','Content-Disposition':'attachment; filename="synthetic.txt"'}).end(authenticated?'synthetic authenticated download':'unauthorized');return;}
    if(request.url==='/slow-download'){slowStarted=true;response.writeHead(200,{'Content-Type':'text/plain','Content-Disposition':'attachment; filename="slow.txt"','Content-Length':'65536'});response.write('pending');slowTimer=setTimeout(()=>response.end(Buffer.alloc(65529,65)),5000);return;}
    response.writeHead(200,{'Content-Type':'text/html; charset=utf-8'}).end(html(authenticated,outside));
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));origin='http://127.0.0.1:'+server.address().port;
  fs.writeFileSync(path.join(profile,'server.json'),JSON.stringify({serverUrl:origin}));
  let local=await start();await local.waitForFunction(()=>!!window.XTYFNative);
  const config=await invoke(local,'get_config');assert(config.capabilities.includes('website_fallback'));assert.equal(config.effectiveServerUrl,origin);
  let remote=await pageMatching(page=>page.url().startsWith(origin));remote.on('pageerror',error=>errors.push(error.message));
  await remote.getByRole('heading',{name:'测试网站登录'}).waitFor();checks.push('404 bootstrap automatically opens a real website inside WebView2');
  const toolbar=await pageMatching(page=>page.url().endsWith('/website-controls.html'));
  await collapsedWebsiteMenu(toolbar,remote,local);checks.push('Website navigation starts hidden behind an accessible menu button and the website fills the window');
  await remote.locator('#login').click();await remote.getByRole('heading',{name:'已登录测试网站'}).waitFor();
  const denied=await remote.evaluate(async()=>{try{return {returned:await window.__TAURI_INTERNALS__.invoke('native_call',{action:'get_config',args:{}})};}catch(error){return {denied:String(error)};}});assert(denied.denied);assert(!denied.returned);checks.push('Website login works; remote document cannot call generic native IPC');
  await checkWebsiteMenu(toolbar,remote,local);checks.push('Opening and collapsing the client menu resizes the website without reloading, losing its draft, or showing a leave-page confirmation');
  const image=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=','base64');
  await remote.locator('#image').setInputFiles({name:'synthetic.png',mimeType:'image/png',buffer:image});await remote.locator('#upload').click();await remote.getByText('上传成功',{exact:true}).waitFor();assert(uploaded);checks.push('Real website file input uploads a synthetic image using website authentication');
  await saveDownload(remote,'#http-download','saved-http.txt','synthetic authenticated download');
  await saveDownload(remote,'#blob-download','saved-blob.txt','synthetic blob download');checks.push('HTTP and Blob downloads use the native Save dialog and complete intact');
  fs.writeFileSync(path.join(profile,'keep-existing.txt'),'unchanged original');await saveDownload(remote,'#http-download','keep-existing.txt','unchanged original',true);await until(()=>fs.readdirSync(path.join(profile,'website-downloads')).length===0,'cancelled download temporary cleanup');checks.push('Cancelling native Save preserves an existing file and removes the private temporary download');
  await remote.locator('#popup').click();await remote.waitForURL(origin+'/next');await remote.getByRole('heading',{name:'已登录测试网站'}).waitFor();checks.push('Same-origin new-window link remains inside the authenticated website view');
  await remote.locator('#outside').click();await pageMatching(page=>page.url().startsWith(outside));assert.equal(outsideCookie,false);checks.push('Offsite links open an independent view without website cookies');
  await remote.locator('#draft').fill('unsaved website draft');
  await toolbar.getByRole('button',{name:'打开客户端菜单',exact:true}).click();
  await toolbar.getByRole('button',{name:'设置',exact:true}).waitFor();
  toolbar.once('dialog',dialog=>dialog.dismiss());await toolbar.getByRole('button',{name:'设置',exact:true}).click();assert.equal(await remote.locator('#draft').inputValue(),'unsaved website draft');
  toolbar.once('dialog',dialog=>dialog.accept());await toolbar.getByRole('button',{name:'设置',exact:true}).click();await local.waitForFunction(()=>document.getElementById('client-settings').open);assert.equal(await local.locator('#server-url').inputValue(),origin);checks.push('Returning to local settings asks before leaving and keeps server configuration');
  await invoke(local,'open_website');remote=await pageMatching(page=>page.url().startsWith(origin));await remote.getByRole('heading',{name:'已登录测试网站'}).waitFor();
  const reopenedToolbar=await pageMatching(page=>page.url().endsWith('/website-controls.html'));await collapsedWebsiteMenu(reopenedToolbar,remote,local);checks.push('Reopening website mode resets the toolbar to its collapsed default');
  await reopenedToolbar.getByRole('button',{name:'打开客户端菜单',exact:true}).click();await reopenedToolbar.getByRole('button',{name:'设置',exact:true}).waitFor();
  await stop();local=await start();remote=await pageMatching(page=>page.url().startsWith(origin));await remote.getByRole('heading',{name:'已登录测试网站'}).waitFor();
  await collapsedWebsiteMenu(await pageMatching(page=>page.url().endsWith('/website-controls.html')),remote,local);checks.push('Website login survives a cold application restart and its toolbar starts collapsed');
  const before=await invoke(local,'probe_connection');assert.equal(before.response.status,404);const nativeCookieFiles=fs.readdirSync(profile).filter(name=>name.startsWith('session-'));for(const file of nativeCookieFiles)assert(!fs.readFileSync(path.join(profile,file),'utf8').includes('website_session'));checks.push('Website cookies remain independent of the native request session');
  await remote.locator('#slow-download').click();await until(()=>slowStarted&&fs.readdirSync(path.join(profile,'website-downloads')).length>0,'slow download has private partial bytes');
  const next='http://127.0.0.1:9',oldLocal=local;await invoke(local,'set_server',{serverUrl:next});await until(()=>remote.isClosed(),'old website closes on server switch');
  await until(()=>fs.readdirSync(path.join(profile,'website-downloads')).length===0,'server switch clears private partial downloads');checks.push('Switching servers during a slow download removes the old private partial file');
  await until(()=>oldLocal.isClosed(),'old local page closes before rebuilding for the new server');local=await pageMatching(page=>/^https?:\/\/tauri\.localhost\/(?:index.html)?(?:[?#].*)?$/.test(page.url()));await local.waitForFunction(()=>!!window.XTYFNative);assert.equal((await invoke(local,'get_config')).effectiveServerUrl,next);await local.locator('#client-settings-open').click();await local.waitForFunction(()=>document.getElementById('client-settings').open);checks.push('Server switch closes the old website and offline local settings remain available');
  assert.deepEqual(errors,[]);fs.writeFileSync(path.join(output,'result.json'),JSON.stringify({ok:true,engine:'actual Windows WebView2',checks,errors,installerGenerated:false,profile},null,2));console.log(JSON.stringify({ok:true,checks},null,2));
}
main().catch(error=>{console.error(error);fs.writeFileSync(path.join(output,process.env.XTYF_PUBLIC_SMOKE==='1'?'public-result.json':'result.json'),JSON.stringify({ok:false,checks,errors,error:error.stack,appLog,profile},null,2));process.exitCode=1;}).finally(async()=>{clearTimeout(slowTimer);await stop().catch(()=>{});for(const item of [server,outsideServer])if(item)await new Promise(resolve=>{item.closeAllConnections();item.close(resolve);});});
