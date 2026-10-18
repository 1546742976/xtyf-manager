'use strict';
// Packaged UI + isolated HTTP endpoint; native window behavior is checked separately.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const {execFileSync} = require('node:child_process');
const {chromium, webkit, devices} = require('playwright');
const root = path.resolve(__dirname, '../..');
const output = path.join(root, '.build-tools/client-connection-check');
const dist = path.join(output, 'ui');
fs.mkdirSync(output, {recursive:true});
execFileSync(process.env.REVIEW_PYTHON || 'python', ['-B','clients/build_frontend.py','--output-dir',dist], {cwd:root});
const mime = {'.js':'application/javascript','.css':'text/css','.html':'text/html','.png':'image/png','.svg':'image/svg+xml','.woff2':'font/woff2'};
const bootstrap = {ok:true,protocolVersion:1,authenticated:false,permissions:{},role_names:{},statuses:[],priorities:[]};
const api = http.createServer((req,res) => {
  const scenario = req.headers['x-test-scenario'];
  if (scenario === 'offline' || scenario === 'tls') { req.socket.destroy(); return; }
  const status = {'404':404,'405':405,'503':503}[scenario] || 200;
  const body = scenario === 'html' ? '<html>Existing website</html>' : scenario === 'invalid' ? '{}' : JSON.stringify(bootstrap);
  res.writeHead(status,{'Content-Type':scenario === 'html' ? 'text/html' : 'application/json'}).end(body);
});
const server = http.createServer((req,res) => {
  const filename = path.resolve(dist, '.' + new URL(req.url,'http://local').pathname);
  if (!filename.startsWith(dist + path.sep)) return res.writeHead(403).end();
  fs.readFile(filename,(error,body) => {
    if (error) return res.writeHead(404).end();
    res.writeHead(200,{'Content-Type':mime[path.extname(filename)] || 'application/octet-stream'}).end(body);
  });
});
let browser,activePage;
const passed = [],errors = [];
const listen = server => new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
async function main() {
  await listen(api); await listen(server);
  const apiUrl = 'http://127.0.0.1:' + api.address().port;
  const local = 'http://127.0.0.1:' + server.address().port;
  for (const [label,engine,options] of [['chromium',chromium,{viewport:{width:1280,height:900}}],['webkit',webkit,devices['iPhone 13']]]) {
    browser = await engine.launch({headless:true});
    async function fixture(scenario, capable=true) {
      const context = await browser.newContext(options),page=await context.newPage(); activePage=page;
      const state = {scenario,opened:0,calls:[],saved:'http://configured.invalid/',effective:apiUrl};
      page.on('pageerror',error=>errors.push(label+': '+error.message));
      await page.exposeFunction('__connectionNative',async(action,args)=>{
        state.calls.push(action);
        const config=()=>({serverUrl:state.saved,effectiveServerUrl:state.effective,version:'test',bridgeVersion:2,capabilities:['ui_updates',...(capable?['website_fallback']:[])]});
        if(action==='get_config')return config();
        if(action==='set_server'){state.saved=args.serverUrl;state.scenario='compatible';return config();}
        if(action==='ui_ready'||action==='cancel')return {};
        if(action==='update_status'||action==='check_updates')return {status:'unavailable'};
        if(action==='open_website'){state.opened++;return {};}
        if(action==='read_resource')return {status:200,headers:{'Content-Type':'image/png'},encoding:'base64',body:'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a8V8AAAAASUVORK5CYII='};
        if(action==='probe_connection'||action==='request'){
          if(state.scenario==='tls')throw new Error('证书校验失败（隔离模拟）');
          const response=await fetch(apiUrl+'/api/client/bootstrap',{headers:{'X-Test-Scenario':state.scenario}});
          const value={status:response.status,headers:Object.fromEntries(response.headers),body:await response.text()};
          return action==='probe_connection'?{...config(),response:value}:value;
        }
        throw Error('Unexpected native action '+action);
      });
      await page.addInitScript(()=>{
        window.__websiteOpened=0;
        window.XTYFNative={invoke:async(action,args)=>{
          const result=await window.__connectionNative(action,args);
          if(action==='open_website')window.__websiteOpened++;
          return result;
        }};
      });
      await page.goto(local+'/index.html#/login');
      return {page,context,state};
    }
    for(const scenario of ['404','405','html','invalid']){
      const {page,context,state}=await fixture(scenario);
      await page.waitForFunction(()=>window.__websiteOpened===1);
      assert.equal(state.opened,1);
      assert.ok(state.calls.includes('ui_ready'),'API absence does not quarantine a healthy local resource');
      await page.evaluate(()=>window.dispatchEvent(new CustomEvent('xtyf-website-return',{detail:{intent:'settings'}})));
      await page.locator('#client-settings').waitFor({state:'visible'});
      assert.equal(await page.locator('#server-url').inputValue(),state.saved,'entered URL is retained');
      assert.ok((await page.locator('#effective-server-url').innerText()).includes(apiUrl));
      await page.locator('#settings-cancel').click();
      await page.evaluate(()=>window.dispatchEvent(new CustomEvent('xtyf-website-return',{detail:{intent:'retry'}})));
      await page.locator('#connection-status').filter({hasText:'当前输入已保留'}).waitFor();
      assert.equal(state.opened,1,'return/retry never creates an automatic fallback loop');
      await context.close();
      passed.push(label+' cold fallback '+scenario);
    }
    for(const scenario of ['503','offline','tls']){
      const {page,context,state}=await fixture(scenario);
      await page.locator('#connection-status').filter({hasText:'暂时无法连接服务器'}).waitFor();
      assert.equal(state.opened,0,'transient/TLS failure must not auto open website');
      await page.locator('#client-settings-open').click();
      await page.locator('#client-settings').waitFor({state:'visible'});
      page.once('dialog',dialog=>dialog.accept());
      await page.locator('#settings-website').click();
      await page.waitForFunction(()=>window.__websiteOpened===1);
      await context.close();passed.push(label+' recoverable '+scenario);
    }
    {
      const {page,context,state}=await fixture('compatible');
      await page.waitForFunction(()=>window.XTYF?.compatible);
      const input=page.frameLocator('#page-host iframe').locator('input[name=username]');
      await input.fill('unsaved-login-draft');
      await page.evaluate(async()=>{window.__draftImage=await XTYF.imageURL('/api/avatar');});
      state.scenario='503';
      await page.evaluate(()=>document.getElementById('connection-retry').onclick());
      await page.locator('#connection-status').filter({hasText:'暂时无法连接服务器'}).waitFor();
      state.scenario='compatible';await page.locator('#connection-retry').click();
      await page.locator('#connection-status').waitFor({state:'hidden'});
      assert.equal(await input.inputValue(),'unsaved-login-draft','successful reconnect preserves the document');
      state.scenario='404';
      await page.locator('#client-settings-open').click();
      await page.locator('#settings-cancel').click();
      await page.evaluate(()=>document.getElementById('connection-retry').click());
      await page.locator('#connection-status').filter({hasText:'当前输入已保留'}).waitFor();
      assert.equal(state.opened,0);assert.equal(await input.inputValue(),'unsaved-login-draft');
      page.once('dialog',dialog=>dialog.dismiss());await page.locator('#connection-website').click();
      assert.equal(state.opened,0);assert.equal(await input.inputValue(),'unsaved-login-draft');
      page.once('dialog',dialog=>dialog.accept());await page.locator('#connection-website').click();
      await page.waitForFunction(()=>window.__websiteOpened===1);
      await page.evaluate(()=>window.dispatchEvent(new CustomEvent('xtyf-website-return',{detail:{intent:'settings'}})));
      assert.equal(await input.inputValue(),'unsaved-login-draft','website return preserves local draft');
      assert.equal(await page.evaluate(async()=>(await fetch(window.__draftImage)).ok),true,'website handoff does not revoke retained local image URLs');
      await page.locator('#server-url').fill('https://new-server.invalid');
      await page.locator('#server-form [type=submit]').click();
      await page.waitForFunction(()=>window.XTYF?.compatible);
      assert.equal(state.saved,'https://new-server.invalid');assert.equal(state.opened,1);
      await context.close();passed.push(label+' compatible local UI, draft protection, manual fallback and server change');
    }
    {
      const {page,context,state}=await fixture('404',false);
      await page.getByRole('heading',{name:'服务器需要更新'}).waitFor();
      assert.equal(state.opened,0);assert.ok(!state.calls.includes('probe_connection'));
      await context.close();passed.push(label+' old bridge does not receive unsupported actions');
    }
    await browser.close();browser=null;activePage=null;
  }
  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(output,'result.json'),JSON.stringify({ok:true,passed,errors},null,2));
  console.log('Client connection browser checks OK: '+passed.length);
}
main().catch(async error=>{
  process.exitCode=1;
  console.error(error);
  fs.writeFileSync(path.join(output,'result.json'),JSON.stringify({ok:false,passed,errors,error:String(error)},null,2));
  if(activePage)await activePage.screenshot({path:path.join(output,'failure.png')}).catch(()=>{});
}).finally(async()=>{
  if(browser)await browser.close().catch(()=>{});
  api.closeAllConnections();api.close();server.closeAllConnections();server.close();
});
