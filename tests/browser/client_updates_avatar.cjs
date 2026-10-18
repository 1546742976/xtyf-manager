'use strict';
// Real packaged pages + isolated Flask data. Native IPC is emulated, not a native OS acceptance run.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const {spawn, execFileSync} = require('node:child_process');
const {chromium, webkit, devices, request} = require('playwright');
const root = path.resolve(__dirname, '../..');
const output = path.join(root, '.build-tools/client-updates-avatar-check');
const dist = path.join(output, 'ui');
const python = process.env.REVIEW_PYTHON || 'python';
fs.mkdirSync(output, {recursive:true});
execFileSync(python, ['-B','clients/build_frontend.py','--output-dir',dist], {cwd:root});
const fixture = execFileSync(python, ['-c','from PIL import Image; import sys; im=Image.new("RGB",(96,64),"#268eb0"); im.paste("#e8a070",(20,10,60,40)); im.save(sys.stdout.buffer,format="PNG")']);
// DocumentsProvider metadata can be generic; the native reader identifies the original bytes.
const file = {handle:'session-avatar-1', name:'synthetic.png', mime:'application/octet-stream', size:fixture.length};
const mime = {'.js':'application/javascript','.css':'text/css','.html':'text/html','.png':'image/png','.svg':'image/svg+xml','.woff2':'font/woff2'};
const server = http.createServer((req,res) => {
  const relative = decodeURIComponent(new URL(req.url,'http://local').pathname).slice(1) || 'index.html';
  const filename = path.resolve(dist, relative);
  if (!filename.startsWith(dist + path.sep)) { res.writeHead(403).end(); return; }
  fs.readFile(filename, (error, data) => {
    if (error) { res.writeHead(404).end(); return; }
    res.writeHead(200, {'Content-Type':mime[path.extname(filename)]||'application/octet-stream',
      'Content-Security-Policy':"default-src 'self' data: blob:; script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval' blob:; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-src 'self' blob:; object-src 'none'; base-uri 'self'; form-action 'none'"}); res.end(data);
  });
});
const backend = spawn(python, ['-B', path.join(__dirname,'server.py')], {cwd:root,env:{...process.env,MAIL_MODE:'preview'},stdio:['ignore','pipe','pipe']});
const passed = [], errors = [];
let browser,activePage,activeState;
async function main() {
  const base = await new Promise((resolve,reject) => {
    let log=''; const timer=setTimeout(()=>reject(Error(log)),30000);
    backend.stderr.on('data',data=>log+=data);
    backend.stdout.on('data',data=>{log+=data; const found=log.match(/BROWSER_URL=(http:\/\/127\.0\.0\.1:\d+)/);if(found){clearTimeout(timer);resolve(found[1]);}});
    backend.once('exit',code=>{clearTimeout(timer);reject(Error('fixture stopped '+code+' '+log));});
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const local='http://127.0.0.1:'+server.address().port;
  const api=await request.newContext({baseURL:base});
  let csrf='';
  async function transport(args) {
    const options={method:args.method||'GET',headers:{...args.headers,'X-XTYF-Client-Protocol':'1',...(csrf?{'X-CSRF-Token':csrf}:{})}};
    if(args.multipart){
      options.multipart=Object.fromEntries(args.multipart.fields.map(field=>[field.name,field.value]));
      for(const item of args.multipart.files){assert.equal(item.handle,file.handle,'must upload original selected handle');options.multipart[item.field]={name:file.name,mimeType:file.mime,buffer:fixture};}
    }else if(args.body!==undefined)options.data=args.body;
    const response=await api.fetch(args.path,options),headers=response.headers();
    csrf=headers['x-csrf-token']||csrf;
    const body=await response.body(),binary=!(headers['content-type']||'').startsWith('text/')&&!(headers['content-type']||'').includes('json');
    return {status:response.status(),headers,body:body.toString(binary?'base64':'utf8'),encoding:binary?'base64':'text'};
  }
  await transport({path:'/api/client/bootstrap'});
  assert.equal((await transport({path:'/api/client/login',method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:'admin',password:'123456'})})).status,200);
  assert.equal((await transport({path:'/api/password',method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({old:'123456',new:'client-avatar-test-password',confirm:'client-avatar-test-password'})})).status,200);
  for(const [label,engine,options] of [['chromium',chromium,{viewport:{width:1280,height:900}}],['webkit',webkit,devices['iPhone 13']]]) {
    browser=await engine.launch({headless:true});
    const context=await browser.newContext(options),page=await context.newPage();activePage=page;
    const state={current:'A',pending:'B',calls:[],failAvatar:false,offline:false,incompatible:false,capable:true,applies:0};activeState=state;
    page.on('pageerror',error=>errors.push(label+': '+error.message));
    await page.exposeFunction('__testNative',async(action,args)=>{
      state.calls.push({action,args});
      const update=()=>({status:state.pending?'pending':'current',currentVersion:state.current,pendingVersion:state.pending,message:state.pending?'新页面已下载，将在下次启动生效。':'当前页面已是最新版本。',installer:{version:'1.0.11',url:'/api/client/installers/test',sha256:'a'.repeat(64)}});
      if(action==='get_config')return {serverUrl:base,version:'1.0.10',bridgeVersion:2,capabilities:state.capable?['ui_updates','image_preview']:['ui_updates'],platform:label==='webkit'?'android':'windows-x86_64'};
      if(action==='check_updates'||action==='update_status')return update();
      if(action==='apply_update'){state.applies++;state.current=state.pending;state.pending=null;return {};}
      if(action==='ui_ready'||action==='cancel')return {};
      if(action==='download')return {cancelled:true};
      if(action==='pick_files')return [file];
      if(action==='read_selected_image'){assert.equal(args.handle,file.handle);return {body:fixture.toString('base64'),mime:'image/png',size:file.size};}
      if(action==='request'||action==='read_resource'){
        if(state.offline)throw Error('isolated offline test');
        if(state.incompatible&&args.path==='/api/client/bootstrap')return {status:200,body:JSON.stringify({ok:true,protocolVersion:1,uiCompatibility:{protocolMin:2,protocolMax:2}})};
        if(args.path==='/api/account/avatar'&&args.method==='POST'&&state.failAvatar){state.failAvatar=false;return {status:500,body:JSON.stringify({ok:false,error:'模拟保存失败'})};}
        return transport(args);
      }
      throw Error('Unexpected native action '+action);
    });
    await page.addInitScript(()=>{
      Object.defineProperty(window,'__XTYF_VIEW_TOKEN__',{value:'fixture-view-token',writable:false,configurable:false});
      window.XTYFNative={invoke:(action,args)=>window.__testNative(action,args)};
    });
    const account=()=>page.frameLocator('#page-host iframe');
    const loaded=async count=>{
      await page.waitForFunction(count=>{const doc=document.querySelector('#page-host iframe').contentDocument,images=[...doc.querySelectorAll('[data-avatar-image]')];return images.length===count&&images.every(img=>img.naturalWidth===512&&!img.hidden&&img.parentElement.querySelector('[data-avatar-initial]').hidden);},count);
    };
    await page.goto(local+'/index.html#/account');
    console.log(label+' packaged account startup');
    await account().locator('#avatar-change').waitFor({state:'visible'});
    await page.waitForFunction(()=>XTYF.compatible);
    assert.ok(state.calls.some(call=>call.action==='ui_ready'&&call.args.viewToken==='fixture-view-token'),'readiness belongs to native-injected view token');
    await page.locator('#client-settings-open').click();
    await page.locator('#updates-apply').waitFor({state:'visible'});
    assert.equal(state.current,'A');assert.equal(state.applies,0);
    await page.locator('#updates-installer').click();await page.waitForFunction(()=>!document.getElementById('updates-installer').disabled);
    assert.deepEqual(state.calls.find(call=>call.action==='download').args,{path:'/api/client/installers/test',filename:'xtyf-manager.'+(label==='webkit'?'apk':'exe'),expectedSha256:'a'.repeat(64)});
    await page.locator('#settings-cancel').click();
    const draft=account().locator('#account-profile-form [name=username]');
    await draft.fill('unsaved-avatar-draft');
    await account().locator('#avatar-change').click();
    await account().locator('#avatar-crop-dialog').waitFor({state:'visible'});
    await account().locator('#avatar-crop-canvas').press('Shift+ArrowLeft');
    await account().locator('#avatar-zoom').fill('2');
    await account().locator('#avatar-cancel').click();
    assert.equal(state.calls.filter(call=>call.action==='request'&&call.args.method==='POST'&&call.args.path==='/api/account/avatar').length,0);
    await account().locator('#avatar-resume').click();state.failAvatar=true;
    await account().locator('#avatar-save').click();
    await account().locator('#avatar-crop-result').filter({hasText:'模拟保存失败'}).waitFor();
    assert.equal(await account().locator('#avatar-zoom').inputValue(),'2');
    await account().locator('#avatar-save').click();
    await account().locator('#avatar-crop-dialog').waitFor({state:'hidden'});await loaded(3);
    assert.equal(await draft.inputValue(),'unsaved-avatar-draft');
    assert.ok(await page.evaluate(()=>XTYF.bootstrap.me.avatar_url));
    const uploads=state.calls.filter(call=>call.action==='request'&&call.args.path==='/api/account/avatar'&&call.args.method==='POST');
    assert.deepEqual(uploads[0].args.multipart,uploads[1].args.multipart,'retry keeps original handle and crop');
    await page.locator('#client-settings-open').click();
    page.once('dialog',dialog=>dialog.dismiss());await page.locator('#updates-apply').click();
    assert.equal(state.applies,0);await page.locator('#settings-cancel').click();
    assert.equal(await draft.inputValue(),'unsaved-avatar-draft');
    await page.locator('#client-settings-open').click();page.once('dialog',dialog=>dialog.accept());await page.locator('#updates-apply').click();
    await page.waitForFunction(()=>!document.getElementById('updates-apply').disabled);
    assert.equal(state.applies,1);assert.equal(state.current,'B');
    await page.reload();await account().locator('#avatar-change').waitFor({state:'visible'});await loaded(3);
    for(const route of ['/invoices/','/funds/']){await page.evaluate(route=>XTYF.navigate(route),route);await account().locator('h1').first().waitFor();await loaded(2);}
    await page.evaluate(()=>XTYF.navigate('/account'));await account().locator('#avatar-remove').waitFor({state:'visible'});
    await account().locator('#avatar-remove').click();await account().locator('#avatar-result').filter({hasText:'已恢复'}).waitFor();
    assert.equal(await account().locator('[data-avatar-image]').count(),0);assert.equal(await page.evaluate(()=>XTYF.bootstrap.me.avatar_url),null);
    await page.screenshot({path:path.join(output,label+'-account.png'),fullPage:true});
    state.capable=false;await page.reload();await account().locator('h1').waitFor();await page.waitForFunction(()=>XTYF.compatible);
    assert.equal(await account().locator('#avatar-editor').isVisible(),false);
    state.incompatible=true;await page.reload();await page.getByRole('heading',{name:'服务器需要更新'}).waitFor();
    assert.match(await page.evaluate(()=>XTYF.request('/api/tasks').then(()=>'',error=>error.message)),/协议不兼容/);
    await page.locator('#client-settings-open').click();await page.locator('#updates-check').waitFor({state:'visible'});await page.locator('#settings-cancel').click();
    state.incompatible=false;state.offline=true;const before=state.calls.filter(call=>call.action==='ui_ready').length;
    await page.reload();await account().locator('#login-form').waitFor();await page.locator('#connection-status').waitFor({state:'visible'});
    assert.ok(state.calls.filter(call=>call.action==='ui_ready').length>before,'local render marks ready even offline');
    await page.locator('#client-settings-open').click();await page.locator('#server-url').waitFor({state:'visible'});
    passed.push({engine:label,version:browser.version(),checks:['pending without runtime reload','installer save with signed SHA256','reload confirmation preserves drafts','emulated A/B restart','native generic MIME identified from bytes','native handle crop and failed retry','three avatars and refresh','invoices/funds','restore default','capability gate','incompatible business gate','offline ui_ready/settings']});
    await context.close();await browser.close();browser=null;
  }
  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(output,'result.json'),JSON.stringify({ok:true,passed,errors,nativeRuntime:false},null,2));
  console.log(JSON.stringify({ok:true,passed,errors},null,2));await api.dispose();
}
main().catch(async error=>{console.error(error);console.error('PAGE_ERRORS',errors);console.error('BRIDGE_CALLS',activeState?.calls.map(call=>[call.action,call.args.path]));if(activePage){await activePage.screenshot({path:path.join(output,'failure.png')});console.error('SHELL',await activePage.evaluate(()=>({route:window.XTYF?.route,bootstrap:window.XTYF?.bootstrap,body:document.body.innerText})));}process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();backend.kill();server.close();});
