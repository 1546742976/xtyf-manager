'use strict';
// Real API and public pages, synthetic inventory only.
const assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs');
const {spawn}=require('node:child_process');
const {chromium,webkit,devices,request}=require('playwright');
const {recordFailure,cleanupResources}=require('./diagnostics.cjs');
const root=path.resolve(__dirname,'../..'),output=path.join(root,'.build-tools/assets-check');
fs.mkdirSync(output,{recursive:true});
const server=spawn(process.env.REVIEW_PYTHON||'python',['-B','tests/browser/server.py'],{cwd:root,env:{...process.env,MAIL_MODE:'preview'},stdio:['ignore','pipe','pipe']});
let browser,page,api,engineName;const passed=[],errors=[];
async function main(){
  const base=await new Promise((resolve,reject)=>{
    let log='';const timer=setTimeout(()=>reject(Error('server timeout '+log)),30000);
    server.stderr.on('data',d=>log+=d);server.stdout.on('data',d=>{log+=d;const m=log.match(/BROWSER_URL=(\S+)/);if(m){clearTimeout(timer);resolve(m[1]);}});
    server.once('exit',code=>{clearTimeout(timer);reject(Error('server exited '+code+' '+log));});
  });
  api=await request.newContext({baseURL:base});
  let csrf=(await (await api.get('/api/csrf')).json()).csrf_token;
  async function post(url,data){
    const r=await api.post(url,{headers:{'X-CSRF-Token':csrf},data});
    csrf=r.headers()['x-csrf-token']||csrf;const result=await r.json();assert.equal(r.status(),200,JSON.stringify(result));return result;
  }
  await post('/api/client/login',{username:'admin',password:'123456'});
  await post('/api/password',{old:'123456',new:'assets-browser-password',confirm:'assets-browser-password'});
  const uid=(await (await api.get('/api/client/bootstrap')).json()).me.id;
  async function save(kind,data){return (await post('/api/assets/catalog/'+kind,{request_id:crypto.randomUUID(),...data})).id;}
  const campus=await save('campuses',{name:'合成东校区'});
  const west=await save('campuses',{name:'合成西校区'});
  for(const [label,engine,options] of [['chromium',chromium,{viewport:{width:1280,height:900}}],['webkit',webkit,devices['iPhone 13']]]){
    engineName=label;browser=await engine.launch();const context=await browser.newContext({...options,storageState:await api.storageState()});
    page=await context.newPage();page.setDefaultTimeout(15000);page.on('pageerror',e=>errors.push(e.message));
    await page.goto(base+'/assets');await page.locator('#asset-status').filter({hasText:'数据已更新'}).waitFor();
    await page.locator('#asset-create').click();
    await page.locator('#asset-form [name=name]').fill('测试电机 '+label);
    await page.locator('#asset-form [name=specification]').fill('3508 <测试>');
    await page.locator('#asset-save').click();await page.locator('#asset-dialog').waitFor({state:'hidden'});
    await page.getByRole('link',{name:'测试电机 '+label,exact:true}).click();
    await page.locator('#asset-status').filter({hasText:'数据已更新'}).waitFor();
    const id=Number(new URL(page.url()).searchParams.get('id'));
    const eastCard=page.locator('.asset-card').filter({has:page.getByRole('heading',{name:'合成东校区',exact:true})});
    await eastCard.getByRole('button',{name:'入库',exact:true}).click();
    await page.locator('#asset-form [name=quantity]').fill('10');await page.locator('#asset-form [name=reason]').fill('合成入库');
    await page.locator('#asset-save').click();await page.locator('#asset-dialog').waitFor({state:'hidden'});
    await eastCard.getByText('总量 · 在库 10 / 借出 0',{exact:true}).waitFor();
    await eastCard.getByRole('button',{name:'借用',exact:true}).click();await page.locator('#asset-form [name=quantity]').fill('3');
    await page.locator('#asset-close').click();assert.equal((await (await api.get(`/api/assets/items/${id}`)).json()).campuses.find(c=>c.id===campus).available,10);
    await eastCard.getByRole('button',{name:'借用',exact:true}).click();await page.locator('#asset-form [name=quantity]').fill('3');
    // A committed response is lost: retry must return the existing result.
    let lost=true;
    await page.route('**/api/assets/operations',async route=>{
      if(lost){lost=false;await route.fetch();await route.abort();}else await route.continue();
    });
    await page.locator('#asset-save').click();await page.locator('#asset-form-status').filter({hasText:'结果尚未确认'}).waitFor();
    assert.equal(await page.locator('#asset-form [name=quantity]').inputValue(),'3');
    await page.locator('#asset-save').click();await page.locator('#asset-dialog').waitFor({state:'hidden'});
    await eastCard.getByText('总量 · 在库 7 / 借出 3',{exact:true}).waitFor();
    await page.unroute('**/api/assets/operations');
    await page.getByRole('link',{name:'我的借用',exact:true}).click();
    await page.locator('#asset-content [data-action=return]').first().click();
    await page.locator('#asset-form [name=quantity]').fill('1');await page.locator('#asset-save').click();await page.locator('#asset-dialog').waitFor({state:'hidden'});
    await page.locator('#asset-content .asset-card').filter({has:page.getByRole('link',{name:'测试电机 '+label,exact:true})}).getByText('未还 2 个',{exact:true}).waitFor();
    await page.goto(base+`/assets/detail?id=${id}`);await page.locator('#asset-status').filter({hasText:'数据已更新'}).waitFor();
    await page.reload();await page.locator('#asset-status').filter({hasText:'数据已更新'}).waitFor();
    await eastCard.getByRole('button',{name:'校区调拨',exact:true}).click();await page.locator('#asset-form [name=quantity]').fill('2');
    await page.locator('#asset-form [name=target_campus_id]').selectOption(String(west));await page.locator('#asset-form [name=reason]').fill('合成调拨');
    await page.locator('#asset-save').click();await page.locator('#asset-dialog').waitFor({state:'hidden'});
    await eastCard.getByText('总量 · 在库 6 / 借出 2',{exact:true}).waitFor();
    const loans=(await (await api.get(`/api/assets/loans?item_id=${id}`)).json()).loans;
    assert.equal(loans.length,1);assert.equal(loans[0].member_id,uid);assert.equal(loans[0].outstanding,2);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true,'no horizontal overflow');
    await page.screenshot({path:path.join(output,label+'.png'),fullPage:true});
    passed.push(label+': create, receive, cancel, lost response retry, partial return, transfer, reload, mobile layout');
    await browser.close();browser=null;
  }
  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(output,'result.json'),JSON.stringify({ok:true,passed,errors,nativeRuntime:false},null,2));
  console.log(JSON.stringify({ok:true,passed}));
}
main().catch(async error=>{await recordFailure(error,{output,page,engine:engineName,phase:'assets',passed,pageErrors:errors});console.error(error);process.exitCode=1;})
  .finally(()=>cleanupResources({browser:()=>browser?.close(),api:()=>api?.dispose(),server:()=>server.kill()}));
