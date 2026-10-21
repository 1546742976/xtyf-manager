'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {spawn}=require('node:child_process');
const {chromium,webkit,devices,request}=require('playwright');
const {recordFailure,cleanupResources}=require('./diagnostics.cjs');
const root=path.resolve(__dirname,'../..'),output=path.join(root,'.build-tools/assets-serialized-check');
fs.mkdirSync(output,{recursive:true});
const server=spawn(process.env.REVIEW_PYTHON||'python',['-B','tests/browser/server.py'],{cwd:root,env:{...process.env,MAIL_MODE:'preview'},stdio:['ignore','pipe','pipe']});
let browser,page,api,engineName;const errors=[],passed=[];
async function main(){
  const base=await new Promise((resolve,reject)=>{let log='';const timer=setTimeout(()=>reject(Error(log)),30000);server.stderr.on('data',d=>log+=d);server.stdout.on('data',d=>{log+=d;const m=log.match(/BROWSER_URL=(\S+)/);if(m){clearTimeout(timer);resolve(m[1]);}});server.once('exit',c=>{clearTimeout(timer);reject(Error(c+' '+log));});});
  api=await request.newContext({baseURL:base});let csrf=(await(await api.get('/api/csrf')).json()).csrf_token;
  async function post(url,data){const r=await api.post(url,{headers:{'X-CSRF-Token':csrf},data});csrf=r.headers()['x-csrf-token']||csrf;assert.equal(r.status(),200,await r.text());return r.json();}
  await post('/api/client/login',{username:'admin',password:'browser-test-initial'});
  await post('/api/password',{old:'browser-test-initial',new:'serialized-browser-password',confirm:'serialized-browser-password'});
  const campus=(await post('/api/assets/catalog/campuses',{request_id:crypto.randomUUID(),name:'合成校区'})).id;
  for(const [label,engine,options] of [['chromium',chromium,{viewport:{width:1280,height:900}}],['webkit',webkit,devices['iPhone 13']]]){
    engineName=label;browser=await engine.launch();const context=await browser.newContext({...options,storageState:await api.storageState()});page=await context.newPage();page.setDefaultTimeout(15000);page.on('pageerror',e=>errors.push(e.message));
    await page.goto(base+'/assets');await page.locator('#asset-status').filter({hasText:'数据已更新'}).waitFor();
    await page.locator('#asset-create').click();await page.getByLabel('名称',{exact:true}).fill('逐件测试 '+label);await page.locator('#asset-save').click();await page.locator('#asset-dialog').waitFor({state:'hidden'});
    await page.getByRole('link',{name:'逐件测试 '+label,exact:true}).click();await page.getByRole('button',{name:'批量建档',exact:true}).click();
    await page.locator('#asset-form [name=campus_id]').selectOption(String(campus));await page.getByLabel('实收件数',{exact:true}).fill('3');await page.getByLabel('原因（必填）',{exact:true}).fill('合成入库');await page.locator('#asset-save').click();await page.locator('#asset-dialog').waitFor({state:'hidden'});
    await page.waitForFunction(()=>document.querySelectorAll('[data-unit-select]').length===3);
    const checkboxes=page.locator('[data-unit-select]');await checkboxes.nth(0).check();await checkboxes.nth(1).check();
    const item=(await(await api.get('/api/assets/items?q='+encodeURIComponent('逐件测试 '+label))).json()).items[0];
    await page.locator('[data-unit-action=borrow]').click();await page.getByLabel('原因（必填）',{exact:true}).fill('合成借用');
    let lost=true;await page.route('**/api/assets/units/operate',async r=>{if(lost){lost=false;await r.fetch();await r.abort();}else await r.continue();});
    await page.locator('#asset-save').click();await page.locator('#asset-form-status').filter({hasText:'结果尚未确认'}).waitFor();
    assert.equal(await page.getByLabel('原因（必填）',{exact:true}).inputValue(),'合成借用');await page.locator('#asset-save').click();await page.locator('#asset-dialog').waitFor({state:'hidden'});await page.unroute('**/api/assets/units/operate');
    let units=(await(await api.get('/api/assets/units?item_id='+item.id)).json()).units;assert.equal(units.filter(u=>u.keeper_id).length,2);
    await page.waitForFunction(()=>document.querySelector('#asset-unit-selected').textContent.includes('0 件'));
    await page.waitForFunction(()=>document.querySelector('#asset-unit-drawer').getAttribute('aria-busy')==='false'&&![...document.querySelectorAll('[data-unit-select]')].some(x=>x.checked));
    await checkboxes.nth(0).check();await page.locator('[data-unit-action=print]').click();
    await page.locator('#asset-label-download').waitFor();const pdf=await api.get(await page.locator('#asset-label-download').getAttribute('href'));assert.equal(pdf.headers()['content-type'],'application/pdf');assert((await pdf.body()).subarray(0,4).equals(Buffer.from('%PDF')));
    const image=await page.locator('#asset-label-preview canvas').first().evaluate(c=>c.toDataURL('image/png').split(',')[1]);
    await page.locator('#asset-label-close').click();await page.locator('[data-unit-action=clear]').click();await page.locator('#asset-unit-close').click();
    await page.locator('#asset-scan').click();
    await page.evaluate(()=>{const deny=()=>Promise.reject(new DOMException('Synthetic denial','NotAllowedError'));if(navigator.mediaDevices)Object.defineProperty(navigator.mediaDevices,'getUserMedia',{configurable:true,value:deny});});
    await page.locator('#asset-camera-start').click();await page.locator('#asset-camera-status').filter({hasText:'相册'}).waitFor();
    await page.locator('#asset-camera-photo').setInputFiles({name:'synthetic-label.png',mimeType:'image/png',buffer:Buffer.from(image,'base64')});
    await page.locator('#asset-scan-status').filter({hasText:'已找到单件'}).waitFor();
    await page.locator('#asset-scan-mode').selectOption('units');await page.locator('#asset-scan-raw').fill('U:'+units[2].id);await page.locator('#asset-scan-raw').press('Enter');await page.locator('#asset-scan-status').filter({hasText:'已加入清单'}).waitFor();
    await page.locator('#asset-scan-raw').fill('U:'+units[2].id);await page.locator('#asset-scan-raw').press('Enter');await page.locator('#asset-scan-status').filter({hasText:'已在待提交清单'}).waitFor();
    await page.locator('#scan-queue-units').click();await page.locator('#asset-unit-close').click();
    const supply=(await post('/api/assets/catalog/items',{request_id:crypto.randomUUID(),name:'合成电阻 '+label,kind:'consumable',unit:'颗',supplier_code:label==='chromium'?'C10001':'C10002'})).id;
    await page.locator('#asset-scan').click();await page.locator('#asset-scan-mode').selectOption('query');
    await page.locator('#asset-scan-raw').fill(`{on:TEST-${label},pc:${label==='chromium'?'C10001':'C10002'},pm:SYNTHETIC,qty:100,mc:,pdi:unknown}`);await page.locator('#asset-scan-raw').press('Enter');await page.locator('#scan-receive').click();
    await page.getByLabel('实收数量',{exact:true}).fill('70');await page.locator('#asset-form [name=campus_id]').selectOption(String(campus));await page.getByLabel('用途／备注（必填）',{exact:true}).fill('合成开封包装');await page.locator('#asset-save').click();await page.locator('#asset-dialog').waitFor({state:'hidden'});
    const stock=(await(await api.get('/api/assets/items/'+supply)).json()).campuses;assert.equal(stock.find(c=>c.id===campus).available,70);
    await page.locator(`[data-action=print-item][data-id="${supply}"]`).click();await page.locator('#asset-label-download').waitFor();assert((await page.locator('#asset-label-download').getAttribute('href')).includes('kind=item'));await page.locator('#asset-label-close').click();
    await page.locator('#asset-objects').click();await page.getByRole('button',{name:'新增使用对象',exact:true}).click();await page.getByLabel('名称',{exact:true}).fill('合成机器人 '+label);await page.getByLabel('原因（必填）',{exact:true}).fill('合成建档');await page.locator('#asset-save').click();await page.locator('#asset-dialog').waitFor({state:'hidden'});await page.getByRole('heading',{name:'合成机器人 '+label,exact:true}).waitFor();await page.locator('#asset-lifecycle-close').click();
    await page.locator('#asset-takes').click();await page.getByRole('button',{name:'开始盘点',exact:true}).click();await page.locator(`#asset-form [name=items][value="${item.id}"]`).check();await page.locator('#asset-form [name=campus_id]').selectOption(String(campus));await page.getByLabel('原因（必填）',{exact:true}).fill('合成盘点');await page.locator('#asset-save').click();await page.locator('#asset-dialog').waitFor({state:'hidden'});await page.getByRole('button',{name:'查看盘点',exact:true}).first().click();await page.getByRole('button',{name:'核对差异并解冻',exact:true}).click();await page.getByLabel('原因（必填）',{exact:true}).fill('未扫描只记待核实');await page.locator('#asset-save').click();await page.locator('#asset-dialog').waitFor({state:'hidden'});await page.locator('#asset-take-detail h4').filter({hasText:'closed'}).waitFor();await page.locator('#asset-lifecycle-close').click();
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
    await page.screenshot({path:path.join(output,label+'.png'),fullPage:true});passed.push(label+': serialized drawer, lost response retry, camera denial fallback, QR photo decode, HID repeat, receipt actual quantity, unit/material PDF, object editor, stocktake closure, mobile layout');await browser.close();browser=null;
  }
  assert.deepEqual(errors,[]);fs.writeFileSync(path.join(output,'result.json'),JSON.stringify({ok:true,passed,nativeHardware:false},null,2));console.log(JSON.stringify({ok:true,passed}));
}
main().catch(async e=>{await recordFailure(e,{output,page,engine:engineName,phase:'serialized',passed,pageErrors:errors});console.error(e);process.exitCode=1;}).finally(()=>cleanupResources({browser:()=>browser?.close(),api:()=>api?.dispose(),server:()=>server.kill()}));
