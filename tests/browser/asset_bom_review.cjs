// Real BOM editor, isolated server and synthetic inventory only.
'use strict';
const assert=require('node:assert/strict'),{spawn}=require('node:child_process'),path=require('node:path'),fs=require('node:fs');
const {chromium,webkit,request}=require('playwright');
const {recordFailure,cleanupResources}=require('./diagnostics.cjs');
const root=path.resolve(__dirname,'../..'),output=path.join(root,'.build-tools/bom-review-check');
fs.mkdirSync(output,{recursive:true});
const server=spawn(process.env.REVIEW_PYTHON||'python',['-B','tests/browser/server.py'],{cwd:root,env:{...process.env,PYTHONUTF8:'1'}});
let browser,page,api,engineName;const passed=[];
async function main(){
  const base=await new Promise((resolve,reject)=>{let log='';const timer=setTimeout(()=>reject(Error(log)),30000);server.stderr.on('data',d=>log+=d);server.stdout.on('data',d=>{log+=d;const m=log.match(/BROWSER_URL=(\S+)/);if(m){clearTimeout(timer);resolve(m[1]);}});server.once('exit',code=>{clearTimeout(timer);reject(Error(code+' '+log));});});
  api=await request.newContext({baseURL:base});
  async function post(url,data){const token=(await(await api.get('/api/csrf')).json()).csrf_token;const r=await api.post(url,{headers:{'X-CSRF-Token':token},data});assert.equal(r.status(),200,await r.text());return r.json();}
  await post('/api/client/login',{username:'admin',password:'browser-test-initial'});
  await post('/api/password',{old:'browser-test-initial',new:'Synthetic-bom-review-2026'});
  const campus=(await post('/api/assets/catalog/campuses',{request_id:crypto.randomUUID(),name:'Synthetic BOM campus'})).id;
  for(const [name,engine] of [['chromium',chromium],['webkit',webkit]]){
    engineName=name;browser=await engine.launch({headless:true});
    const context=await browser.newContext({storageState:await api.storageState(),serviceWorkers:'block'});page=await context.newPage();
    const id=(await post('/api/assets/catalog/items',{request_id:crypto.randomUUID(),name:'Synthetic '+name,kind:'consumable',consumable_mode:'quantity',unit:'pcs'})).id;
    await page.goto(base+'/assets?view=table');
    await page.locator('#asset-bom-file').setInputFiles({name:'synthetic.csv',mimeType:'text/csv',buffer:Buffer.from('Code,Name,Qty\nM:'+id+',Synthetic,1\nM:'+id+',Synthetic,2\n')});
    await page.locator('#asset-bom-import').click();await page.locator('#asset-bom-editor').waitFor();
    await page.locator('#asset-bom-campus').selectOption(String(campus));await page.locator('#asset-bom-mode').selectOption('inbound');await page.locator('#asset-bom-reason').fill('Synthetic precise quantity');
    const quantity=page.getByLabel('表格第 2 行 每套或实收数量',{exact:true});
    await quantity.fill('1.00000000000000001');await page.locator('#asset-bom-match').click();
    await page.locator('#asset-bom-message').filter({hasText:'数量无效或超限'}).waitFor();assert.equal(await page.locator('#asset-bom-save').isDisabled(),true);
    await quantity.fill('1,000');await page.getByLabel('表格第 3 行 每套或实收数量',{exact:true}).fill('2e0');
    let matching,committed;
    await page.route('**/api/assets/bom/match',r=>{matching=r.request().postDataJSON();return r.continue();});
    await page.route('**/api/assets/bom/commit',r=>{committed=r.request().postDataJSON();return r.continue();});
    await page.locator('#asset-bom-match').click();await page.locator('#asset-bom-message').filter({hasText:'已匹配库存'}).waitFor();
    assert.equal(matching.rows[0].quantity,'1,000');assert.equal(matching.rows[1].quantity,'2e0');
    await page.locator('#asset-bom-save').click();await page.locator('#asset-bom-message').filter({hasText:'批量操作成功'}).waitFor();
    assert.deepEqual(committed.rows.map(r=>r.quantity),[1000,2]);
    const detail=await(await api.get('/api/assets/items/'+id)).json();assert.equal(detail.campuses.find(c=>c.id===campus).available,1002);
    passed.push(name+': exact rejection, raw matching, integer commit and same-item aggregation');await browser.close();browser=null;
  }
  const result={ok:true,passed};fs.writeFileSync(path.join(output,'result.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
}
main().catch(async error=>{await recordFailure(error,{output,page,engine:engineName,phase:'BOM review',passed});console.error(error);process.exitCode=1;})
.finally(()=>cleanupResources({browser:()=>browser?.close(),api:()=>api?.dispose(),server:()=>server.kill()}));
