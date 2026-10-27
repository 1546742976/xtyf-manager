// Isolated real funds page; requests are controlled only for fault injection.
'use strict';
const assert=require('node:assert/strict'),{spawn}=require('node:child_process'),path=require('node:path');
const {chromium,webkit,request}=require('playwright');
const {recordFailure,cleanupResources}=require('./diagnostics.cjs');
const root=path.resolve(__dirname,'../..'),output=path.join(root,'.build-tools/funds-filter-review');
const server=spawn(process.env.REVIEW_PYTHON||'python',['-B','tests/browser/server.py'],{cwd:root,env:{...process.env,PYTHONUTF8:'1'}});
let browser,page,api,engineName;const passed=[],navigation=[];
async function main(){
  const base=await new Promise((resolve,reject)=>{let log='';const timer=setTimeout(()=>reject(Error(log)),30000);server.stderr.on('data',d=>log+=d);server.stdout.on('data',d=>{log+=d;const m=log.match(/BROWSER_URL=(\S+)/);if(m){clearTimeout(timer);resolve(m[1]);}});server.once('exit',code=>{clearTimeout(timer);reject(Error(code+' '+log));});});
  api=await request.newContext({baseURL:base});
  async function post(url,data){const token=(await(await api.get('/api/csrf')).json()).csrf_token;const r=await api.post(url,{headers:{'X-CSRF-Token':token},data});assert.equal(r.status(),200,await r.text());return r.json();}
  await post('/api/client/login',{username:'admin',password:'browser-test-initial'});await post('/api/password',{old:'browser-test-initial',new:'Synthetic-funds-filter-2026'});
  for(const [name,engine] of [['chromium',chromium],['webkit',webkit]]){
    engineName=name;browser=await engine.launch({headless:true});
    const context=await browser.newContext({storageState:await api.storageState(),serviceWorkers:'block'});page=await context.newPage();
    await page.addInitScript(()=>addEventListener('pageshow',event=>{const events=JSON.parse(sessionStorage.getItem('synthetic-pageshow')||'[]');events.push({path:location.pathname,persisted:event.persisted});sessionStorage.setItem('synthetic-pageshow',JSON.stringify(events));}));
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.goto(base+'/funds/');await page.waitForFunction(()=>document.getElementById('funds-list-loading').hidden);
    const held=new Map();
    await page.route('**/funds/api/records?*',route=>{held.set(new URL(route.request().url()).searchParams.get('keyword')||'',route);});
    async function query(keyword){await page.locator('#fkw').fill(keyword);await page.locator('button[onclick="reload()"]').first().click();await assertEventually(()=>held.has(keyword));}
    async function reply(keyword,id){const route=held.get(keyword);held.delete(keyword);await route.fulfill({contentType:'application/json',body:JSON.stringify({code:0,data:[{id,record_type:'进',amount:1,record_date:'2026-10-27',notes:keyword}]})}).catch(()=>{});}
    async function assertEventually(fn){for(let i=0;i<100;i++){if(fn())return;await new Promise(r=>setTimeout(r,20));}assert.ok(fn(),'request was not observed');}
    await query('first');await reply('first',111);await page.getByRole('cell',{name:'111',exact:true}).waitFor();
    await query('old');assert.equal(await page.locator('#tbody tr').count(),0);assert.equal(await page.locator('#rcnt').textContent(),'');assert.equal(await page.locator('#funds-list-loading').isVisible(),true);
    await query('new');await reply('new',222);await page.getByRole('cell',{name:'222',exact:true}).waitFor();await reply('old',111);assert.equal(await page.locator('#tbody tr').count(),1);assert.equal(await page.getByRole('cell',{name:'111',exact:true}).count(),0);
    await query('failure');const failed=held.get('failure');held.delete('failure');await failed.fulfill({status:503,contentType:'application/json',body:JSON.stringify({code:1,msg:'Synthetic latest failure'})});
    await page.locator('#funds-list-error').waitFor();assert.equal(await page.locator('#tbody tr').count(),0);assert.equal(await page.locator('#rcnt').textContent(),'');assert.equal(await page.locator('#empty').isVisible(),false);assert.equal(await page.locator('#fkw').inputValue(),'failure');
    await page.getByRole('button',{name:'重试当前筛选'}).click();await assertEventually(()=>held.has('failure'));await reply('failure',333);await page.getByRole('cell',{name:'333',exact:true}).waitFor();
    for(const keyword of ['slow1','slow2','slow3'])await query(keyword);
    await reply('slow2',442);assert.equal(await page.locator('#funds-list-loading').isVisible(),true);await reply('slow3',443);await page.getByRole('cell',{name:'443',exact:true}).waitFor();await reply('slow1',441);
    await page.unroute('**/funds/api/records?*');
    // Real history navigation, no synthetic pageshow event. Record whether BFCache occurred.
    await page.goto(base+'/account');await page.goBack();await page.locator('#funds-records').waitFor();await page.waitForFunction(()=>document.getElementById('funds-list-loading').hidden);
    await page.goForward();await page.locator('#member-introductions').waitFor();await page.goBack();await page.locator('#funds-records').waitFor();await page.waitForFunction(()=>document.getElementById('funds-list-loading').hidden);
    navigation.push({engine:name,events:await page.evaluate(()=>JSON.parse(sessionStorage.getItem('synthetic-pageshow')))});
    assert.deepEqual(errors,[]);passed.push(name+': reversed responses, failure/retry, slow switches, actual history navigation');await browser.close();browser=null;
  }
  console.log(JSON.stringify({ok:true,passed,navigation}));
}
main().catch(async error=>{await recordFailure(error,{output,page,engine:engineName,phase:'funds filters',passed});console.error(error);process.exitCode=1;})
.finally(()=>cleanupResources({browser:()=>browser?.close(),api:()=>api?.dispose(),server:()=>server.kill()}));
