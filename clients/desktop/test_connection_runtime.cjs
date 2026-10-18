'use strict';
// Actual Windows WebView2 and native IPC. All accounts/files are synthetic and
// the application runs against an ephemeral HTTP server and private profile.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),http=require('node:http');
const {spawn}=require('node:child_process'),{once}=require('node:events');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'../..'),output=process.env.XTYF_TEST_OUTPUT||path.join(root,'.build-tools/connection-delivery/webview2');
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
async function fullWebsite(remote,local){
  assert.equal(browser.contexts().flatMap(context=>context.pages()).filter(page=>!page.isClosed()&&page.url().endsWith('/website-controls.html')).length,0,'website mode must not create a toolbar WebView or transparent menu overlay');
  const native=await ownedWindow();let actual;
  try{await until(async()=>{actual=await remote.evaluate(()=>({width:innerWidth*devicePixelRatio,height:innerHeight*devicePixelRatio}));return Math.abs(actual.width-native.clientWidth)<=1&&Math.abs(actual.height-native.clientHeight)<=1;},'website fills the complete native content area');}
  catch(error){throw new Error(`${error.message}; dimensions: ${JSON.stringify({actual,native,local:await local.evaluate(()=>({width:innerWidth,height:innerHeight,scale:devicePixelRatio}))})}`);}
  assert.equal(await remote.locator('#menu-toggle, #website-navigation').count(),0);
}
async function checkWebsiteResize(remote,local){
  const original=await ownedWindow();
  const documentMarker=await remote.evaluate(()=>{window.websiteDocumentMarker=crypto.randomUUID();return window.websiteDocumentMarker;});
  await remote.locator('#draft').fill('draft preserved while resizing website');
  try{
    const resized=await ownedWindow(760,640);
    assert.notEqual(resized.width,original.width,'fixture must actually resize the native test window');
    await fullWebsite(remote,local);
    assert.equal(await remote.evaluate(()=>window.websiteDocumentMarker),documentMarker);
    assert.equal(await remote.locator('#draft').inputValue(),'draft preserved while resizing website');
  }finally{await ownedWindow(original.width,original.height);}
  await fullWebsite(remote,local);
  assert.equal(await remote.evaluate(()=>window.websiteDocumentMarker),documentMarker);
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

// Drive only OS dialogs and the main window owned by this private test process.
// No developer-only save bypass or product menu is added.
const windowScript=path.join(output,'resize-owned-window.ps1');
fs.writeFileSync(windowScript,'\uFEFF'+String.raw`param([int]$OwnerPid,[int]$Width=0,[int]$Height=0)
Add-Type @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public class OwnedWindowBounds {
 [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left,Top,Right,Bottom; }
 private delegate bool WindowCallback(IntPtr window,IntPtr state);
 [DllImport("user32.dll")] private static extern bool EnumWindows(WindowCallback callback,IntPtr state);
 [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr window,out uint pid);
 [DllImport("user32.dll",CharSet=CharSet.Unicode)] private static extern int GetWindowText(IntPtr window,StringBuilder title,int size);
 [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
 [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr window,out RECT rectangle);
 [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr window,out RECT rectangle);
 [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr window,IntPtr after,int x,int y,int width,int height,uint flags);
 public static IntPtr MainWindow(uint owner) {
   IntPtr found=IntPtr.Zero;
   EnumWindows((window,state)=>{uint pid;GetWindowThreadProcessId(window,out pid);if(pid!=owner)return true;
     var title=new StringBuilder(256);GetWindowText(window,title,title.Capacity);
     if(title.ToString()!="巡天御风管理系统")return true;found=window;return false;},IntPtr.Zero);
   return found;
 }
}
'@
[void][OwnedWindowBounds]::SetProcessDPIAware()
$handle=[OwnedWindowBounds]::MainWindow($OwnerPid)
if($handle -eq 0){throw 'Owned main window not found'}
$bounds=New-Object OwnedWindowBounds+RECT
if(-not [OwnedWindowBounds]::GetWindowRect($handle,[ref]$bounds)){throw 'Cannot read owned window bounds'}
if($Width -gt 0 -and $Height -gt 0){
  if(-not [OwnedWindowBounds]::SetWindowPos($handle,[IntPtr]::Zero,$bounds.Left,$bounds.Top,$Width,$Height,20)){throw 'Cannot resize owned window'}
  if(-not [OwnedWindowBounds]::GetWindowRect($handle,[ref]$bounds)){throw 'Cannot read resized owned window bounds'}
}
$client=New-Object OwnedWindowBounds+RECT
if(-not [OwnedWindowBounds]::GetClientRect($handle,[ref]$client)){throw 'Cannot read owned content area'}
@{width=$bounds.Right-$bounds.Left;height=$bounds.Bottom-$bounds.Top;clientWidth=$client.Right-$client.Left;clientHeight=$client.Bottom-$client.Top} | ConvertTo-Json -Compress
`,'utf8');
async function powershell(script,args){
  const helper=spawn('powershell.exe',['-NoProfile','-NonInteractive','-File',script,'-OwnerPid',String(application.pid),...args],{windowsHide:true,stdio:['ignore','pipe','pipe']});
  const exited=once(helper,'exit');let log='';helper.stdout.on('data',data=>log+=data);helper.stderr.on('data',data=>log+=data);
  const [code]=await exited;assert.equal(code,0,log);return log;
}
async function ownedWindow(width=0,height=0){return JSON.parse(await powershell(windowScript,['-Width',String(width),'-Height',String(height)]));}
const dialogScript=path.join(output,'save-owned-dialog.ps1');
fs.writeFileSync(dialogScript,'\uFEFF'+String.raw`param([int]$OwnerPid,[string]$TargetPath,[string]$Mode='save',[string]$SourceDirectory)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public class OwnedDialogMessages {
 [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern IntPtr SendMessage(IntPtr window, uint message, IntPtr wparam, string text);
 [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr window, uint message, IntPtr wparam, IntPtr lparam);
 private delegate bool WindowCallback(IntPtr window,IntPtr state);
 [DllImport("user32.dll")] private static extern bool EnumWindows(WindowCallback callback,IntPtr state);
 [DllImport("user32.dll")] private static extern bool EnumChildWindows(IntPtr parent,WindowCallback callback,IntPtr state);
 [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr window,out uint pid);
 [DllImport("user32.dll",CharSet=CharSet.Unicode)] private static extern int GetWindowText(IntPtr window,StringBuilder title,int size);
 public static IntPtr FindError(uint owner) {
   IntPtr found=IntPtr.Zero;
   EnumWindows((window,state)=>{uint pid;GetWindowThreadProcessId(window,out pid);if(pid!=owner)return true;
     var title=new StringBuilder(256);GetWindowText(window,title,title.Capacity);
     if(title.ToString()!="网站文件下载失败")return true;found=window;return false;},IntPtr.Zero);
   return found;
 }
 public static string Text(IntPtr window) {
   var text=new StringBuilder();
   EnumChildWindows(window,(child,state)=>{var title=new StringBuilder(4096);GetWindowText(child,title,title.Capacity);text.AppendLine(title.ToString());return true;},IntPtr.Zero);
   return text.ToString();
 }
}
'@
$until=[DateTime]::UtcNow.AddSeconds(20)
$submitted=$false
while([DateTime]::UtcNow -lt $until){
  $errorWindow=[OwnedDialogMessages]::FindError($OwnerPid)
  if($errorWindow -ne [IntPtr]::Zero){
    if($Mode -ne 'save-error'){throw 'Unexpected native download error dialog'}
    if([OwnedDialogMessages]::Text($errorWindow) -notmatch '文件保存失败'){throw 'Missing save failure explanation'}
    # Acknowledge only this verified error window. Modern message dialogs may
    # render their OK button without a direct child HWND.
    if(-not [OwnedDialogMessages]::PostMessage($errorWindow,16,[IntPtr]::Zero,[IntPtr]::Zero)){throw 'Cannot dismiss owned error dialog'}
    exit 0
  }
  $pidCondition=New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty,$OwnerPid)
  $windows=[System.Windows.Automation.AutomationElement]::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children,$pidCondition)
  foreach($window in $windows){
    if($window.Current.Name -ne '保存网站文件'){continue}
    if($Mode -eq 'none'){throw 'Unexpected native Save dialog'}
    if($submitted){continue}
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
    if($Mode -eq 'save-error'){
      # Inject failure only after the browser has completed this synthetic
      # download and opened its native Save dialog in the private profile.
      $sources=@(Get-ChildItem -LiteralPath $SourceDirectory -File -Filter '*.part')
      if($sources.Count -ne 1){throw 'Expected exactly one completed fixture download'}
      Remove-Item -LiteralPath $sources[0].FullName -ErrorAction Stop
      $submitted=$true
    }
    [void][OwnedDialogMessages]::PostMessage([IntPtr]$save.Current.NativeWindowHandle,245,[IntPtr]::Zero,[IntPtr]::Zero)
    if($Mode -eq 'save-error'){continue}
    exit 0
  }
  if($Mode -eq 'none'){exit 0}
  Start-Sleep -Milliseconds 100
}
foreach($window in $windows){$window.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition) | ForEach-Object {$_.Current | Select-Object Name,AutomationId,ControlType} | ConvertTo-Json -Depth 3}
Write-Error 'Expected owned native dialog did not appear';exit 1
`,'utf8');
async function saveDownload(page,selector,name,expected,cancel=false){
  const target=path.join(profile,name);
  const helper=spawn('powershell.exe',['-NoProfile','-NonInteractive','-File',dialogScript,'-OwnerPid',String(application.pid),'-TargetPath',target,'-Mode',cancel?'cancel':'save'],{windowsHide:true,stdio:['ignore','pipe','pipe']});
  const exited=once(helper,'exit');
  let log='';helper.stdout.on('data',data=>log+=data);helper.stderr.on('data',data=>log+=data);
  await page.locator(selector).click();
  const [code]=await exited;assert.equal(code,0,log);
  if(cancel){await until(()=>fs.readdirSync(path.join(profile,'website-downloads')).length===0,'cancelled native Save cleanup');assert.equal(fs.readFileSync(target,'utf8'),expected);await powershell(dialogScript,['-Mode','none']);return;}
  await until(()=>fs.existsSync(target),'native download '+name);
  assert.equal(fs.readFileSync(target,'utf8'),expected);
}
async function failedSave(page){
  const target=path.join(profile,'failed-save.txt');
  const dialog=powershell(dialogScript,['-Mode','save-error','-TargetPath',target,'-SourceDirectory',path.join(profile,'website-downloads')]);
  await page.locator('#http-download').click();
  await dialog;
  assert.equal(fs.existsSync(target),false,'failed saving must not leave a corrupt destination');
  await until(()=>fs.readdirSync(path.join(profile,'website-downloads')).length===0,'failed Save temporary cleanup');
  await powershell(dialogScript,['-Mode','none']);
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
  outsideServer=http.createServer((request,response)=>{outsideCookie=!!request.headers.cookie;response.end('<title>Independent external view</title>External <a href="/next">External next</a>');});
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
  await fullWebsite(remote,local);checks.push('Website fills the native content area without creating a toolbar WebView or menu button');
  await remote.locator('#login').click();await remote.getByRole('heading',{name:'已登录测试网站'}).waitFor();
  const denied=await remote.evaluate(async()=>{try{return {returned:await window.__TAURI_INTERNALS__.invoke('native_call',{action:'get_config',args:{}})};}catch(error){return {denied:String(error)};}});assert(denied.denied);assert(!denied.returned);checks.push('Website login works; remote document cannot call generic native IPC');
  await checkWebsiteResize(remote,local);checks.push('Resizing the actual native window and restoring it keeps the website full-size and preserves its document and draft');
  const image=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=','base64');
  await remote.locator('#image').setInputFiles({name:'synthetic.png',mimeType:'image/png',buffer:image});await remote.locator('#upload').click();await remote.getByText('上传成功',{exact:true}).waitFor();assert(uploaded);checks.push('Real website file input uploads a synthetic image using website authentication');
  await saveDownload(remote,'#http-download','saved-http.txt','synthetic authenticated download');
  await saveDownload(remote,'#blob-download','saved-blob.txt','synthetic blob download');checks.push('HTTP and Blob downloads use the native Save dialog and complete intact');
  fs.writeFileSync(path.join(profile,'keep-existing.txt'),'unchanged original');await saveDownload(remote,'#http-download','keep-existing.txt','unchanged original',true);await until(()=>fs.readdirSync(path.join(profile,'website-downloads')).length===0,'cancelled download temporary cleanup');checks.push('Cancelling native Save preserves an existing file and removes the private temporary download');
  await failedSave(remote);checks.push('A synthetic save failure shows an owned native error dialog and leaves no corrupt destination or temporary file');
  await remote.locator('#popup').click();await remote.waitForURL(origin+'/next');await remote.getByRole('heading',{name:'已登录测试网站'}).waitFor();checks.push('Same-origin new-window link remains inside the authenticated website view');
  await remote.locator('#outside').click();const outsidePage=await pageMatching(page=>page.url().startsWith(outside));assert.equal(outsideCookie,false);
  const outsideControls=await pageMatching(page=>page.url().endsWith('/external.html'));await outsideControls.getByRole('button',{name:'返回',exact:true}).waitFor();await outsidePage.getByRole('link',{name:'External next'}).click();await outsidePage.waitForURL(outside+'/next');await outsideControls.getByRole('button',{name:'返回',exact:true}).click();await outsidePage.waitForURL(outside+'/');await outsideControls.getByRole('button',{name:'关闭',exact:true}).click();await until(()=>outsidePage.isClosed(),'external view closes');checks.push('Offsite links keep an independent cookie-free view with working Return and Close controls');
  // Exercise the retained trusted bridge lifecycle directly. Website mode has
  // no user-facing menu, settings shortcut, or replacement hidden gesture.
  await invoke(local,'return_local',{intent:'settings'});await until(()=>remote.isClosed(),'trusted return closes website view');await local.waitForFunction(()=>document.getElementById('client-settings').open);assert.equal(await local.locator('#server-url').inputValue(),origin);checks.push('Test-only invocation from the trusted main view still returns to local settings and keeps server configuration; no user menu is provided');
  await invoke(local,'open_website');remote=await pageMatching(page=>page.url().startsWith(origin));await remote.getByRole('heading',{name:'已登录测试网站'}).waitFor();
  await fullWebsite(remote,local);checks.push('Reopening website mode remains full-window without recreating menu controls');
  await stop();local=await start();remote=await pageMatching(page=>page.url().startsWith(origin));await remote.getByRole('heading',{name:'已登录测试网站'}).waitFor();
  await fullWebsite(remote,local);checks.push('Website login survives a cold application restart and no client menu or toolbar is created');
  const before=await invoke(local,'probe_connection');assert.equal(before.response.status,404);const nativeCookieFiles=fs.readdirSync(profile).filter(name=>name.startsWith('session-'));for(const file of nativeCookieFiles)assert(!fs.readFileSync(path.join(profile,file),'utf8').includes('website_session'));checks.push('Website cookies remain independent of the native request session');
  await remote.locator('#slow-download').click();await until(()=>slowStarted&&fs.readdirSync(path.join(profile,'website-downloads')).length>0,'slow download has private partial bytes');
  const next='http://127.0.0.1:9',oldLocal=local;await invoke(local,'set_server',{serverUrl:next});await until(()=>remote.isClosed(),'old website closes on server switch');
  await until(()=>fs.readdirSync(path.join(profile,'website-downloads')).length===0,'server switch clears private partial downloads');checks.push('Switching servers during a slow download removes the old private partial file');
  await until(()=>oldLocal.isClosed(),'old local page closes before rebuilding for the new server');local=await pageMatching(page=>/^https?:\/\/tauri\.localhost\/(?:index.html)?(?:[?#].*)?$/.test(page.url()));await local.waitForFunction(()=>!!window.XTYFNative);assert.equal((await invoke(local,'get_config')).effectiveServerUrl,next);await local.locator('#client-settings-open').click();await local.waitForFunction(()=>document.getElementById('client-settings').open);checks.push('Server switch closes the old website and offline local settings remain available');
  assert.deepEqual(errors,[]);fs.writeFileSync(path.join(output,'result.json'),JSON.stringify({ok:true,engine:'actual Windows WebView2',node:process.version,playwright:require('playwright/package.json').version,clientVersion:config.version,checks,errors,installerGenerated:false,profile},null,2));console.log(JSON.stringify({ok:true,checks},null,2));
}
main().catch(error=>{console.error(error);fs.writeFileSync(path.join(output,process.env.XTYF_PUBLIC_SMOKE==='1'?'public-result.json':'result.json'),JSON.stringify({ok:false,checks,errors,error:error.stack,appLog,profile},null,2));process.exitCode=1;}).finally(async()=>{clearTimeout(slowTimer);await stop().catch(()=>{});for(const item of [server,outsideServer])if(item)await new Promise(resolve=>{item.closeAllConnections();item.close(resolve);});});
