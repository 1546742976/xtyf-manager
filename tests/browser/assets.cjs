'use strict';
// Real API and public pages, synthetic inventory only.
const assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs');
const {spawn,execFileSync}=require('node:child_process');
const http=require('node:http');
const {chromium,webkit,devices,request}=require('playwright');
const {recordFailure,cleanupResources}=require('./diagnostics.cjs');
const root=path.resolve(__dirname,'../..'),output=path.join(root,'.build-tools/assets-check');
fs.mkdirSync(output,{recursive:true});
const server=spawn(process.env.REVIEW_PYTHON||'python',['-B','tests/browser/server.py'],{cwd:root,env:{...process.env,MAIL_MODE:'preview'},stdio:['ignore','pipe','pipe']});
let browser,page,api,engineName,uiServer,fixtureRoot;const passed=[],errors=[],accounts=[];
async function main(){
  const base=await new Promise((resolve,reject)=>{
    let log='';const timer=setTimeout(()=>reject(Error('server timeout '+log)),30000);
    server.stderr.on('data',d=>log+=d);server.stdout.on('data',d=>{log+=d;const rootMatch=log.match(/BROWSER_ROOT=(.+)/);if(rootMatch)fixtureRoot=rootMatch[1].trim();const m=log.match(/BROWSER_URL=(\S+)/);if(m){clearTimeout(timer);resolve(m[1]);}});
    server.once('exit',code=>{clearTimeout(timer);reject(Error('server exited '+code+' '+log));});
  });
  api=await request.newContext({baseURL:base});
  let csrf=(await (await api.get('/api/csrf')).json()).csrf_token;
  async function post(url,data){
    const r=await api.post(url,{headers:{'X-CSRF-Token':csrf},data});
    csrf=r.headers()['x-csrf-token']||csrf;const result=await r.json();assert.equal(r.status(),200,JSON.stringify(result));return result;
  }
  await post('/api/client/login',{username:'admin',password:'browser-test-initial'});
  await post('/api/password',{old:'browser-test-initial',new:'assets-browser-password',confirm:'assets-browser-password'});
  const uid=(await (await api.get('/api/client/bootstrap')).json()).me.id;
  async function save(kind,data){return (await post('/api/assets/catalog/'+kind,{request_id:crypto.randomUUID(),...data})).id;}
  const campus=await save('campuses',{name:'合成东校区'});
  const west=await save('campuses',{name:'合成西校区'});
  const member=(await post('/api/member',{name:'合成资产成员',username:'assets_browser_member',password:'assets-member-password',role:'member'})).id;
  async function account(username,role){
    await post('/api/member',{name:username,username,password:'assets-initial-password',role});
    const client=await request.newContext({baseURL:base});accounts.push(client);
    let token=(await (await client.get('/api/csrf')).json()).csrf_token;
    for(const [url,data] of [
      ['/api/client/login',{username,password:'assets-initial-password'}],
      ['/api/password',{old:'assets-initial-password',new:'assets-registered-password',confirm:'assets-registered-password'}]
    ]){
      const response=await client.post(url,{headers:{'X-CSRF-Token':token},data});
      assert.equal(response.status(),200,await response.text());token=response.headers()['x-csrf-token']||token;
    }
    return client;
  }
  const managerApi=await account('assets_project_manager','manager');
  const ordinaryApi=await account('assets_ordinary_member','member');
  await post('/api/admin/permissions/'+member,{permissions:['invoices.manage']});
  for(const [label,engine,options] of [['chromium',chromium,{viewport:{width:1280,height:900}}],['webkit',webkit,devices['iPhone 13']]]){
    engineName=label;browser=await engine.launch();const context=await browser.newContext({...options,storageState:await api.storageState()});
    page=await context.newPage();page.setDefaultTimeout(15000);page.on('pageerror',e=>errors.push(e.message));
    await page.goto(base+'/admin/security');
    const permissionRow=page.locator(`[data-member-id="${member}"]`);
    await permissionRow.locator('[data-permission="assets.manage"]').check();
    await permissionRow.getByRole('button',{name:'保存权限',exact:true}).click();
    await page.locator('#permission-result').filter({hasText:'权限已保存'}).waitFor();
    // The success receipt precedes the permission/audit refresh. Finish this
    // setup action before navigating to the inventory scenario under test.
    await page.waitForFunction(id=>!document.querySelector(`[data-member-id="${id}"] [data-save-permissions]`)?.disabled,member);
    const permissionState=(await (await api.get('/api/admin/permissions')).json()).members.find(m=>m.id===member);
    const granted=permissionState.explicit_permissions;
    assert(granted.includes('assets.manage'));assert(granted.includes('invoices.manage'));
    assert.deepEqual(permissionState.permissions,[],'Unchanged initial password still blocks effective permissions');
    await page.goto(base+'/assets');await page.locator('#asset-status').filter({hasText:'数据已更新'}).waitFor();
    await page.locator('#asset-create').click();await page.locator('#asset-add-manual').click();
    await page.locator('#asset-form [name=name]').fill('测试电机 '+label);
    await page.locator('#asset-form [name=specification]').fill('3508 <测试>');
    await page.locator('#asset-form [name=returnable_mode]').selectOption('quantity');
    await page.locator('#asset-save').click();await page.locator('#asset-dialog').waitFor({state:'hidden'});
    // This suite retains the pre-migration quantity/loan contract. New unit behavior
    // is tested separately in assets_serialized; only this disposable DB is seeded.
    const legacy=(await(await api.get('/api/assets/items?q='+encodeURIComponent('测试电机 '+label))).json()).items[0];
    await page.reload();await page.locator('#asset-status').filter({hasText:'数据已更新'}).waitFor();
    await page.getByRole('link',{name:'测试电机 '+label,exact:true}).click();
    await page.locator('#asset-status').filter({hasText:'数据已更新'}).waitFor();
    const id=Number(new URL(page.url()).searchParams.get('id'));
    const eastCard=page.locator('.asset-card').filter({has:page.getByRole('heading',{name:'合成东校区',exact:true})});
    await eastCard.getByRole('button',{name:'补货入库',exact:true}).click();
    await page.locator('#asset-form [name=quantity]').fill('10');await page.locator('#asset-form [name=reason]').fill('合成入库');
    await page.locator('#asset-save').click();await page.locator('#asset-dialog').waitFor({state:'hidden'});
    await eastCard.getByText('总量 · 在库 10 / 借出 0',{exact:true}).waitFor();
    await eastCard.getByRole('button',{name:'登记借用',exact:true}).click();await page.locator('#asset-form [name=quantity]').fill('3');
    await page.locator('#asset-close').click();assert.equal((await (await api.get(`/api/assets/items/${id}`)).json()).campuses.find(c=>c.id===campus).available,10);
    await eastCard.getByRole('button',{name:'登记借用',exact:true}).click();await page.locator('#asset-form [name=quantity]').fill('3');
    // A committed response is lost: retry must return the existing result.
    await page.getByLabel('用途／备注（必填）',{exact:true}).fill('合成借用验证');
    let lost=true;
    await page.route('**/api/assets/operations',async route=>{
      if(lost){lost=false;await route.fetch();await route.abort();}else await route.continue();
    });
    await page.locator('#asset-save').click();await page.locator('#asset-form-status').filter({hasText:'结果尚未确认'}).waitFor();
    assert.equal(await page.locator('#asset-form [name=quantity]').inputValue(),'3');
    const borrowEventsBefore=(await (await api.get(`/api/assets/events?item_id=${id}`)).json()).events.filter(e=>e.action==='borrow').length;
    await page.locator('#asset-form [name=quantity]').fill('4');
    await page.locator('#asset-save').click();
    await page.locator('#asset-form-status').filter({hasText:'不能修改内容'}).waitFor();
    assert.equal((await (await api.get(`/api/assets/items/${id}`)).json()).campuses.find(c=>c.id===campus).available,7);
    // Reloading the page must not lose a committed-but-unacknowledged request.
    await page.reload();await page.locator('#asset-pending-retry').waitFor({state:'visible'});
    await page.locator('#asset-pending-retry').click();
    await page.getByRole('button',{name:'重试原请求',exact:true}).click();
    await page.locator('#asset-dialog').waitFor({state:'hidden'});
    await page.locator('#asset-pending').waitFor({state:'hidden'});
    await eastCard.getByText('总量 · 在库 7 / 借出 3',{exact:true}).waitFor();
    assert.equal((await (await api.get(`/api/assets/events?item_id=${id}`)).json()).events.filter(e=>e.action==='borrow').length,borrowEventsBefore);
    await page.unroute('**/api/assets/operations');
    await page.getByRole('link',{name:'我的借用',exact:true}).click();await page.locator('[data-asset-mine-view=quantity]').click();
    await page.locator('#asset-content [data-action=return]').first().click();
    await page.locator('#asset-form [name=quantity]').fill('1');await page.getByLabel('用途／备注（必填）',{exact:true}).fill('合成归还验证');await page.locator('#asset-save').click();await page.locator('#asset-dialog').waitFor({state:'hidden'});
    await page.locator('#asset-content .asset-card').filter({has:page.getByRole('link',{name:'测试电机 '+label,exact:true})}).getByText('未还 2 个',{exact:true}).waitFor();
    await page.locator('#asset-content[aria-busy=false]').waitFor();
    await page.goto(base+`/assets/detail?id=${id}`);await page.locator('#asset-status').filter({hasText:'数据已更新'}).waitFor();
    await page.reload();await page.locator('#asset-status').filter({hasText:'数据已更新'}).waitFor();
    await eastCard.locator('summary').click();await eastCard.getByRole('button',{name:'校区调拨',exact:true}).click();await page.locator('#asset-form [name=quantity]').fill('2');
    await page.locator('#asset-form [name=target_campus_id]').selectOption(String(west));await page.locator('#asset-form [name=reason]').fill('合成调拨');
    await page.locator('#asset-save').click();await page.locator('#asset-dialog').waitFor({state:'hidden'});
    await eastCard.getByText('总量 · 在库 6 / 借出 2',{exact:true}).waitFor();
    await page.locator('[data-asset-detail-view=history]').click();await page.locator('#asset-event-campus').selectOption(String(west));
    await page.waitForFunction(()=>document.querySelectorAll('.asset-log li').length===1);
    await page.locator('.asset-log').getByText('合成调拨',{exact:true}).waitFor();
    const loans=(await (await api.get(`/api/assets/loans?item_id=${id}`)).json()).loans;
    assert.equal(loans.length,1);assert.equal(loans[0].member_id,uid);assert.equal(loans[0].outstanding,2);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true,'no horizontal overflow');
    await page.screenshot({path:path.join(output,label+'.png'),fullPage:true});
    passed.push(label+': permission grants preserve invoice permissions, create, receive, cancel, lost response retry, partial return, transfer, campus log filter, reload, mobile layout');
    const managerContext=await browser.newContext({...options,storageState:await managerApi.storageState()});
    page=await managerContext.newPage();page.setDefaultTimeout(15000);page.on('pageerror',e=>errors.push(e.message));
    await page.goto(base+'/assets');await page.locator('#asset-categories').waitFor();
    await page.locator('#asset-categories').click();await page.getByRole('button',{name:'新增类别',exact:true}).click();
    await page.getByLabel('名称',{exact:true}).fill('登记分类 '+label);await page.locator('#asset-save').click();
    await page.locator('#asset-dialog').waitFor({state:'hidden'});
    await page.locator('#asset-directory-close').click();await page.locator('#asset-create').click();await page.locator('#asset-add-manual').click();await page.getByLabel('名称',{exact:true}).fill('自助耗材 '+label);
    await page.locator('#asset-form [name=category]').selectOption('登记分类 '+label);
    await page.locator('#asset-form [name=kind]').selectOption('consumable');
    await page.locator('#asset-form [name=consumable_mode]').selectOption('quantity');
    await page.locator('#asset-save').click();await page.locator('#asset-dialog').waitFor({state:'hidden'});
    await page.getByRole('link',{name:'自助耗材 '+label,exact:true}).waitFor();
    const supply=(await (await api.get('/api/assets/items?q='+encodeURIComponent('自助耗材 '+label))).json()).items[0];
    await post('/api/assets/operations',{request_id:crypto.randomUUID(),action:'receive',item_id:supply.id,campus_id:campus,quantity:10,reason:'合成库存'});
    await post('/api/assets/operations',{request_id:crypto.randomUUID(),action:'receive',item_id:supply.id,campus_id:west,quantity:5,reason:'合成库存'});
    const ordinaryContext=await browser.newContext({...options,storageState:await ordinaryApi.storageState()});
    page=await ordinaryContext.newPage();page.setDefaultTimeout(15000);page.on('pageerror',e=>errors.push(e.message));
    await page.goto(base+'/assets');await page.getByRole('link',{name:'自助耗材 '+label,exact:true}).waitFor();
    await page.locator('#asset-create').click();
    assert.equal(await page.locator('#asset-add-manual').isVisible(),false);
    await page.locator('#asset-scan').click();await page.locator('#asset-scan-mode').selectOption('general');await page.locator('#asset-scan-raw').fill('M:'+supply.id);await page.locator('#asset-scan-raw').press('Enter');
    await page.locator('#asset-scan-preview [data-scan-action=receive]').waitFor();assert.equal(await page.locator('#asset-scan-preview [data-scan-action=create]').count(),0);await page.locator('#asset-scan-close').click();await page.locator('#asset-add-dialog[open]').waitFor();await page.locator('#asset-add-close').click();
    assert.equal(await page.locator('#asset-categories').isVisible(),false);
    const supplyCard=page.locator('.asset-card').filter({has:page.getByRole('link',{name:'自助耗材 '+label,exact:true})});
    await page.locator('#asset-filter [name=campus_id]').selectOption(String(campus));
    await supplyCard.locator('.asset-count').filter({hasText:'10'}).waitFor();
    await page.locator('#asset-filter [name=campus_id]').selectOption(String(west));
    await supplyCard.locator('.asset-count').filter({hasText:'5'}).waitFor();
    await page.locator('#asset-filter [name=campus_id]').selectOption(String(campus));
    await supplyCard.locator('.asset-count').filter({hasText:'10'}).waitFor();
    // A stock-value tap must not open a destructive operation; use an explicit action.
    await supplyCard.locator('.asset-count').click();
    assert.equal(await page.locator('#asset-dialog').isVisible(),false);
    await supplyCard.locator('[data-action=register]').click();
    assert.equal(await page.locator('#asset-form [name=campus_id]').inputValue(),String(campus));
    await page.locator('#asset-form [name=quantity]').fill('2');
    await page.locator('#asset-form details summary').filter({hasText:'补登记时间'}).click();await page.getByLabel('登记时间（北京时间）',{exact:true}).fill('2026-01-01T09:30');
    await page.locator('#asset-save').click();
    assert.equal(await page.locator('#asset-dialog').isVisible(),true);
    assert.equal(await page.locator('#asset-form [name=reason]').evaluate(el=>el.validity.valueMissing),true);
    assert.equal((await (await api.get(`/api/assets/items/${supply.id}`)).json()).campuses.find(c=>c.id===campus).available,10);
    await page.getByLabel('用途／备注（必填）',{exact:true}).fill('合成装配领用验证');
    await page.locator('#asset-save').click();await page.locator('#asset-dialog').waitFor({state:'hidden'});
    await supplyCard.locator('.asset-count').filter({hasText:'8'}).waitFor();
    await supplyCard.locator('.asset-row-more summary').click();await supplyCard.locator('[data-action=quantity]').click();
    await page.locator('#asset-form [name=quantity]').fill('12');
    await page.getByLabel('原因（必填）',{exact:true}).fill('合成盘点');
    await page.locator('#asset-save').click();await page.locator('#asset-dialog').waitFor({state:'hidden'});
    await supplyCard.locator('.asset-count').filter({hasText:'12'}).waitFor();
    await supplyCard.getByRole('link',{name:'自助耗材 '+label,exact:true}).click();
    await page.locator('[data-asset-detail-view=history]').click();
    await page.getByText('登记时间：2026-01-01 09:30:00（北京时间）',{exact:true}).waitFor();
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
    await page.screenshot({path:path.join(output,label+'-registration.png'),fullPage:true});
    passed.push(label+': manager categories, ordinary member consumption and quantity adjustment, automatic campus filters, card dialog and Beijing registration time');
    await browser.close();browser=null;
  }
  const dist=path.join(output,'ui');
  execFileSync(process.env.REVIEW_PYTHON||'python',['-B','clients/build_frontend.py','--output-dir',dist],{cwd:root});
  uiServer=http.createServer((req,res)=>{
    const file=path.resolve(dist,'.'+new URL(req.url,'http://local').pathname.replace(/\/$/,'/index.html'));
    if(!file.startsWith(dist+path.sep)||!fs.existsSync(file)||!fs.statSync(file).isFile()){res.writeHead(404).end();return;}
    const mime={'.html':'text/html','.js':'application/javascript','.css':'text/css','.json':'application/json','.svg':'image/svg+xml'};
    res.setHeader('Content-Type',mime[path.extname(file)]||'application/octet-stream');fs.createReadStream(file).pipe(res);
  });
  await new Promise(resolve=>uiServer.listen(0,'127.0.0.1',resolve));
  const local='http://127.0.0.1:'+uiServer.address().port;
  for(const [label,engine] of [['chromium',chromium],['webkit',webkit]]){
    engineName=label+'-local';browser=await engine.launch();page=await browser.newPage({viewport:{width:1280,height:900}});
    page.setDefaultTimeout(15000);page.on('pageerror',e=>errors.push(e.message));
    const state={offline:false,old:false};const remoteHtml=[];
    page.on('request',r=>{if(r.url().startsWith(base))remoteHtml.push(r.url());});
    await page.exposeFunction('__assetsNative',async(action,args)=>{
      if(action==='get_config')return {serverUrl:base,version:'test',capabilities:[]};
      if(action==='cancel'||action==='ui_ready')return {};
      if(action!=='request'&&action!=='read_resource')throw Error('unexpected native action '+action);
      if(state.offline)throw Error('离线测试');
      const response=await api.fetch(args.path,{method:args.method||'GET',headers:{...args.headers,'X-XTYF-Client-Protocol':'1','X-CSRF-Token':csrf},...(args.body!==undefined?{data:args.body}:{})});
      const headers=response.headers();csrf=headers['x-csrf-token']||csrf;
      let body=await response.text();
      if(state.old&&args.path==='/api/client/bootstrap'){
        const parsed=JSON.parse(body);delete parsed.assets_enabled;body=JSON.stringify(parsed);
      }
      return {status:response.status(),headers,body,encoding:'text'};
    });
    await page.addInitScript(()=>{if(window===window.top)window.XTYFNative={invoke:(action,args)=>window.__assetsNative(action,args)};});
    await page.goto(local+'/#/assets');
    let frame=page.frameLocator('#page-host iframe');
    // The shell host ID is stable; fail clearly if it changes.
    if(await page.locator('#page-host').count()===0)frame=page.frameLocator('iframe');
    await frame.locator('#asset-status').filter({hasText:'数据已更新'}).waitFor();
    await frame.getByRole('link',{name:'测试电机 chromium',exact:true}).click();
    await frame.locator('#asset-status').filter({hasText:'数据已更新'}).waitFor();
    const east=frame.locator('.asset-card').filter({has:frame.getByRole('heading',{name:'合成东校区',exact:true})});
    await east.getByRole('button',{name:'登记借用',exact:true}).click();
    await frame.getByLabel('用途／备注（必填）',{exact:true}).fill('合成本地客户端借用');
    state.offline=true;await frame.locator('#asset-save').click();
    await frame.locator('#asset-form-status').filter({hasText:'结果尚未确认'}).waitFor();
    assert.equal(await frame.locator('#asset-form [name=quantity]').inputValue(),'1');
    state.offline=false;await frame.locator('#asset-save').click();
    await frame.locator('#asset-dialog').waitFor({state:'hidden'});
    await east.getByText('总量 · 在库 5 / 借出 3',{exact:true}).waitFor();
    // Restore fixture stock through the real own-loan return API for the next engine.
    const nativeLoan=(await (await api.get('/api/assets/loans?mine=1')).json()).loans.find(l=>l.quantity===1&&l.outstanding===1);
    await post('/api/assets/operations',{request_id:crypto.randomUUID(),action:'return',quantity:1,loan_id:nativeLoan.id,reason:'合成测试归还'});
    assert.deepEqual(remoteHtml,[],'local view must use native API bridge, not remote HTML');
    state.old=true;await page.goto(local+'/#/');await page.reload();await frame.locator('#workspace-sidebar').waitFor();
    assert.equal(await frame.locator('#workspace-sidebar a[href="/assets"]').count(),0,'old server hides navigation');
    await page.evaluate(()=>window.XTYF.navigate('/assets'));
    await page.getByRole('heading',{name:'服务器暂未启用资产管理'}).waitFor();
    passed.push(label+': packaged routes, native API simulation, offline draft and retry, old-server capability gate');
    await browser.close();browser=null;
  }
  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(output,'result.json'),JSON.stringify({ok:true,passed,errors,nativeRuntime:false},null,2));
  console.log(JSON.stringify({ok:true,passed}));
}
main().catch(async error=>{await recordFailure(error,{output,page,engine:engineName,phase:'assets',passed,pageErrors:errors});console.error(error);process.exitCode=1;})
  .finally(()=>cleanupResources({browser:()=>browser?.close(),api:()=>api?.dispose(),accounts:()=>Promise.all(accounts.map(a=>a.dispose())),server:()=>server.kill(),ui:()=>uiServer?.close()}));
