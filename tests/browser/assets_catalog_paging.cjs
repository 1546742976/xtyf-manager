'use strict';
// Disposable API fixture, real shared inventory UI, and simulated packaged IPC.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {spawn,execFileSync}=require('node:child_process'),http=require('node:http');
const {chromium,webkit,devices,request}=require('playwright');
const {recordFailure,cleanupResources}=require('./diagnostics.cjs');
const root=path.resolve(__dirname,'../..'),output=path.join(root,'.build-tools/assets-catalog-paging-check');
fs.mkdirSync(output,{recursive:true});
const python=process.env.REVIEW_PYTHON||'python';
const server=spawn(python,['-B','tests/browser/server.py'],{cwd:root,env:{...process.env,MAIL_MODE:'preview'},stdio:['ignore','pipe','pipe']});
let browser,page,api,engineName,uiServer;const passed=[],errors=[];
async function main(){
  const base=await new Promise((resolve,reject)=>{let log='';const timer=setTimeout(()=>reject(Error('server timeout '+log)),30000);server.stderr.on('data',d=>log+=d);server.stdout.on('data',d=>{log+=d;const m=log.match(/BROWSER_URL=(\S+)/);if(m){clearTimeout(timer);resolve(m[1]);}});server.once('exit',code=>{clearTimeout(timer);reject(Error('server exit '+code+' '+log));});});
  api=await request.newContext({baseURL:base});let csrf=(await(await api.get('/api/csrf')).json()).csrf_token;
  async function post(url,data){const r=await api.post(url,{headers:{'X-CSRF-Token':csrf},data});csrf=r.headers()['x-csrf-token']||csrf;assert.equal(r.status(),200,await r.text());return r.json();}
  await post('/api/client/login',{username:'admin',password:'browser-test-initial'});
  await post('/api/password',{old:'browser-test-initial',new:'catalog-browser-password',confirm:'catalog-browser-password'});
  const category=(await post('/api/assets/catalog/categories',{request_id:crypto.randomUUID(),name:'合成整理类别'})).id;
  const ids=[];
  for(let i=1;i<=25;i++)ids.push((await post('/api/assets/catalog/items',{request_id:crypto.randomUUID(),name:'分页合成 '+String(i).padStart(2,'0'),specification:'合成规格',unit:'个',kind:'consumable',consumable_mode:'quantity'})).id);
  async function ready(ui){await ui.locator('#asset-content[aria-busy=false]').waitFor();assert.equal(await ui.locator('#asset-status[data-state=error]').count(),0);}
  async function query(ui,value){await ui.locator('#asset-search-query').fill(value);await ui.locator('#asset-filter').evaluate(form=>form.requestSubmit());await ready(ui);}
  async function organize(ui){await ui.locator('#asset-categories').click();await ui.locator('[data-action=category-organize]').click();await ready(ui);await ui.locator('#asset-category-organizer').waitFor({state:'visible'});}
  async function jump(ui,p){const nav=ui.locator('[data-asset-pager=items]');await nav.locator('[data-asset-page-input]').fill(String(p));await nav.locator('[data-asset-page-input]').press('Enter');await ready(ui);}
  async function checkPager(ui){
    const nav=ui.locator('[data-asset-pager=items]');
    assert.equal(await ui.locator('[data-category-item]').count(),20);
    await jump(ui,2);assert.equal(await ui.locator('[data-category-item]').count(),5);
    await nav.locator('[data-asset-page="1"]').first().click();await ready(ui);
    await nav.locator('[data-asset-page-size]').selectOption('50');await ready(ui);assert.equal(await ui.locator('[data-category-item]').count(),25);
    assert.equal(await nav.locator('[data-asset-page-input]').inputValue(),'1');
    await nav.locator('[data-asset-page-size]').selectOption('100');await ready(ui);assert.equal(await ui.locator('[data-category-item]').count(),25);
    await nav.locator('[data-asset-page-size]').selectOption('20');await ready(ui);
  }
  async function checkCategories(ui,label){
    await query(ui,'分页合成');await organize(ui);await checkPager(ui);
    await ui.locator('[data-category-item="'+ids[0]+'"]').check();
    await jump(ui,2);await ui.locator('[data-category-item="'+ids[24]+'"]').check();
    await ui.locator('#asset-category-selection').filter({hasText:'已选 2 / 100'}).waitFor();
    await query(ui,'分页合成 03');await ui.locator('[data-category-item="'+ids[2]+'"]').check();
    await ui.locator('#asset-category-selection').filter({hasText:'2 种不在当前页'}).waitFor();
    await ui.locator('#asset-category-organizer').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(output,label+'-categories.png')});
    await ui.locator('[data-action=category-assign]').click();await ui.locator('#asset-form [name=category_id]').selectOption(String(category));
    await ui.locator('#asset-category-confirmation').filter({hasText:'3 种物料设置为“合成整理类别”'}).waitFor();
    await ui.locator('#asset-save').click();await ui.locator('#asset-dialog').waitFor({state:'hidden'});await ready(ui);
    await ui.locator('#asset-category-selection').filter({hasText:'已选 0 / 100'}).waitFor();
    for(const id of [ids[0],ids[2],ids[24]])assert.equal((await(await api.get('/api/assets/items/'+id)).json()).item.category,'合成整理类别');
    assert.equal((await(await api.get('/api/assets/items/'+ids[1])).json()).item.category,'');
    await ui.locator('[data-category-item="'+ids[2]+'"]').check();await ui.locator('[data-action=category-assign]').click();
    await ui.locator('#asset-form [name=category_id]').selectOption('');await ui.locator('#asset-save').click();
    await ui.locator('#asset-dialog').waitFor({state:'hidden'});await ready(ui);
    await ui.locator('#asset-filter [name=category]').selectOption('__uncategorized__');await ready(ui);
    assert.equal(await ui.locator('[data-category-item="'+ids[2]+'"]').count(),1);
    await query(ui,'不会存在的合成物料');await ui.locator('.asset-empty').filter({hasText:'没有找到'}).waitFor();
    assert.equal(await ui.locator('[data-asset-pager=items] [data-asset-page="1"]').first().isDisabled(),true,'empty list has no previous page');
    await ui.locator('#asset-filter-reset').click();await ready(ui);
    await query(ui,'分页合成');
    await ui.locator('#asset-filter [name=category]').selectOption('__uncategorized__');await ready(ui);
    await jump(ui,2);
    const tail=await ui.locator('[data-category-item]').evaluateAll(inputs=>inputs.map(input=>Number(input.dataset.categoryItem)));
    const versions=async values=>Promise.all(values.map(async id=>{const item=(await(await api.get('/api/assets/items/'+id)).json()).item;return {id,version:item.version};}));
    const categoryRow=(await(await api.get('/api/assets/catalog')).json()).categories.find(c=>c.id===category);
    await post('/api/assets/categories/assign',{request_id:crypto.randomUUID(),items:await versions(tail),category_id:category,category_version:categoryRow.version});
    await ui.locator('#asset-refresh').click();await ready(ui);
    assert.equal(await ui.locator('[data-asset-pager=items] [data-asset-page-input]').inputValue(),'1','refresh uses clamped server page');
    await post('/api/assets/categories/assign',{request_id:crypto.randomUUID(),items:await versions(tail),category_id:null,category_version:null});
    await ui.locator('[data-action=category-organize-close]').click();await ready(ui);
    passed.push(label+': cross-page/search selection, atomic category write, unclassified filter, 20/50/100 and page jump');
  }
  async function checkDuplicate(ui,label){
    await ui.locator('#asset-create').click();await ui.locator('#asset-add-manual').click();
    await ui.locator('#asset-form [name=name]').fill('分页合成 01');await ui.locator('#asset-form [name=specification]').fill('合成规格');
    await ui.locator('#asset-form [name=kind]').selectOption('consumable');await ui.locator('#asset-form [name=consumable_mode]').selectOption('quantity');
    await ui.locator('#asset-save').click();await ui.locator('#asset-form-status').filter({hasText:'同名同规格'}).waitFor();
    assert.equal(await ui.locator('#asset-form [name=name]').inputValue(),'分页合成 01');
    await ui.getByText('与现有档案重名时',{exact:true}).click();await ui.locator('#asset-form [name=force_new]').check();
    await ui.locator('#asset-form [name=reason]').fill('合成另建确认 '+label);await ui.locator('#asset-save').click();
    await ui.locator('#asset-dialog').waitFor({state:'hidden'});await ready(ui);
    await ui.locator('#asset-add-close').click();
    const found=(await(await api.get('/api/assets/items?q='+encodeURIComponent('分页合成 01'))).json()).items;
    assert(found.length>=2);assert(new Set(found.map(v=>v.id)).size===found.length);
    passed.push(label+': ordinary duplicate blocked; explicit force-new reason creates distinct identity');
  }
  const engines=[['chromium',chromium,{viewport:{width:1280,height:900}}],['webkit',webkit,devices['iPhone 13']]];
  for(const [label,engine,options] of engines){
    engineName=label;browser=await engine.launch();const context=await browser.newContext({...options,storageState:await api.storageState()});page=await context.newPage();page.setDefaultTimeout(15000);page.on('pageerror',e=>errors.push(e.message));
    await page.goto(base+'/assets');await ready(page);await checkCategories(page,label);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true,'inventory and pager stay inside viewport');
    await page.locator('[data-asset-pager=items]').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(output,label+'.png')});
    // Isolate duplicate creation from the 25-row pagination fixture.
    await checkDuplicate(page,label);await browser.close();browser=null;
    // Keep exactly 25 records and clear categories for the next engine.
    const records=(await(await api.get('/api/assets/items?per_page=100&q='+encodeURIComponent('分页合成'))).json()).items;
    for(const item of records.filter(v=>!ids.includes(v.id)))await post('/api/assets/catalog/items',{id:item.id,version:item.version,name:'已验收独立档案 '+item.id,specification:item.specification,kind:item.kind,unit:item.unit,request_id:crypto.randomUUID(),active:false,reason:'合成测试清理',consumable_mode:'quantity'});
    const originals=records.filter(v=>ids.includes(v.id));await post('/api/assets/categories/assign',{request_id:crypto.randomUUID(),items:originals.map(v=>({id:v.id,version:v.version})),category_id:null,category_version:null});
  }
  const dist=path.join(output,'ui');execFileSync(python,['-B','clients/build_frontend.py','--output-dir',dist],{cwd:root});
  uiServer=http.createServer((req,res)=>{const file=path.resolve(dist,'.'+new URL(req.url,'http://local').pathname.replace(/\/$/,'/index.html'));if(!file.startsWith(dist+path.sep)||!fs.existsSync(file)||!fs.statSync(file).isFile()){res.writeHead(404).end();return;}res.setHeader('Content-Type',({'.html':'text/html','.js':'application/javascript','.css':'text/css','.json':'application/json','.svg':'image/svg+xml'})[path.extname(file)]||'application/octet-stream');fs.createReadStream(file).pipe(res);});
  await new Promise(resolve=>uiServer.listen(0,'127.0.0.1',resolve));
  for(const [label,engine,options] of engines){
    engineName=label+'-local';browser=await engine.launch();page=await browser.newPage(options);page.setDefaultTimeout(15000);page.on('pageerror',e=>errors.push(e.message));const remote=[];
    page.on('request',r=>{if(r.url().startsWith(base))remote.push(r.url());});
    await page.exposeFunction('__catalogNative',async(action,args)=>{if(action==='get_config')return {serverUrl:base,version:'test',capabilities:[]};if(['cancel','ui_ready'].includes(action))return {};if(!['request','read_resource'].includes(action))throw Error('unexpected native action '+action);const r=await api.fetch(args.path,{method:args.method||'GET',headers:{...args.headers,'X-XTYF-Client-Protocol':'1','X-CSRF-Token':csrf},...(args.body!==undefined?{data:args.body}:{})});const headers=r.headers();csrf=headers['x-csrf-token']||csrf;return {status:r.status(),headers,body:await r.text(),encoding:'text'};});
    await page.addInitScript(()=>{if(window===window.top)window.XTYFNative={invoke:(action,args)=>window.__catalogNative(action,args)};});
    await page.goto('http://127.0.0.1:'+uiServer.address().port+'/#/assets');const frame=page.frameLocator('#page-host iframe');await ready(frame);await query(frame,'分页合成');await organize(frame);await checkPager(frame);
    await frame.locator('[data-category-item="'+ids[1]+'"]').check();await frame.locator('[data-action=category-assign]').click();await frame.locator('#asset-form [name=category_id]').selectOption(String(category));await frame.locator('#asset-save').click();await frame.locator('#asset-dialog').waitFor({state:'hidden'});await ready(frame);
    assert.equal(await frame.locator('html').evaluate(el=>el.scrollWidth<=el.clientWidth+1),true);assert.deepEqual(remote,[],'packaged UI uses native API only');
    await page.screenshot({path:path.join(output,label+'-local.png'),fullPage:true});passed.push(label+': packaged shared pager and category write through simulated native IPC');await browser.close();browser=null;
  }
  assert.deepEqual(errors,[]);fs.writeFileSync(path.join(output,'result.json'),JSON.stringify({ok:true,passed,errors,nativeRuntime:false},null,2));console.log(JSON.stringify({ok:true,passed}));
}
main().catch(async error=>{await recordFailure(error,{output,page,engine:engineName,phase:'catalog-paging',passed,pageErrors:errors});console.error(error);process.exitCode=1;})
  .finally(()=>cleanupResources({browser:()=>browser?.close(),api:()=>api?.dispose(),server:()=>server.kill(),ui:()=>uiServer?.close()}));
