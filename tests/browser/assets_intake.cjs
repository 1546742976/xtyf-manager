'use strict';
// Real shared scan UI and API; all inventory is synthetic and the DB is temporary.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {spawn}=require('node:child_process');
const {chromium,webkit,devices,request}=require('playwright');
const {recordFailure,cleanupResources}=require('./diagnostics.cjs');
const root=path.resolve(__dirname,'../..'),output=path.join(root,'.build-tools/assets-intake-check');
fs.mkdirSync(output,{recursive:true});
// Install the offline supplier fixture only after server.py has selected its isolated paths.
const runner=[
  'import runpy, werkzeug.serving',
  'original = werkzeug.serving.make_server',
  'def isolated_server(*args, **kwargs):',
  '    from modules import asset_scan',
  '    asset_scan.lookup_lcsc = lambda code: {"configured": True, "status": "unavailable", "product": None, "message": "合成离线供应商资料"}',
  '    return original(*args, **kwargs)',
  'werkzeug.serving.make_server = isolated_server',
  'runpy.run_path("tests/browser/server.py", run_name="__main__")',
].join('\n');
const server=spawn(process.env.REVIEW_PYTHON||'python',['-B','-c',runner],{cwd:root,env:{...process.env,MAIL_MODE:'preview'},stdio:['ignore','pipe','pipe']});
let browser,page,api,engineName,phase='startup';const errors=[],passed=[];
async function main(){
  const base=await new Promise((resolve,reject)=>{let log='';const timer=setTimeout(()=>reject(Error(log)),30000);server.stderr.on('data',d=>log+=d);server.stdout.on('data',d=>{log+=d;const m=log.match(/BROWSER_URL=(\S+)/);if(m){clearTimeout(timer);resolve(m[1]);}});server.once('exit',c=>{clearTimeout(timer);reject(Error(c+' '+log));});});
  api=await request.newContext({baseURL:base});let csrf=(await(await api.get('/api/csrf')).json()).csrf_token;
  async function post(url,data){const r=await api.post(url,{headers:{'X-CSRF-Token':csrf},data});csrf=r.headers()['x-csrf-token']||csrf;assert.equal(r.status(),200,await r.text());return r.json();}
  async function get(url){const r=await api.get(url);assert.equal(r.status(),200,await r.text());return r.json();}
  const save=(kind,data)=>post('/api/assets/catalog/'+kind,{request_id:crypto.randomUUID(),...data});
  await post('/api/client/login',{username:'admin',password:'browser-test-initial'});
  await post('/api/password',{old:'browser-test-initial',new:'intake-browser-password',confirm:'intake-browser-password'});
  const campus=(await save('campuses',{name:'合成入库东校区'})).id;
  const west=(await save('campuses',{name:'合成入库西校区'})).id;
  async function scan(raw){
    await page.evaluate(raw=>window.AssetScan.resolve(raw),raw);
    if(await page.evaluate(()=>window.AssetScan.task)==='receive')await page.waitForFunction(()=>!document.querySelector('#asset-scan-queue').textContent.includes('正在查询'));
  }
  async function openScan(mode='receive',context={}){await page.evaluate(({mode,context})=>window.AssetScan.open(mode,context),{mode,context});await page.locator('#asset-scan-dialog[open]').waitFor();}
  async function click(action,index=0){await page.locator('[data-scan-action="'+action+'"]').nth(index).click();}
  async function submit(reason){await page.locator('#asset-form [name=reason]').fill(reason);await page.locator('#asset-save').click();await page.locator('#asset-dialog').waitFor({state:'hidden'});await page.locator('#asset-scan-dialog[open]').waitFor();}
  async function itemDetail(id){return get('/api/assets/items/'+id);}
  async function units(id){return (await get('/api/assets/units?per_page=100&item_id='+id)).units;}
  async function chooseBatch(){await click('batch-receive');await page.locator('#asset-dialog[open]').waitFor();await page.locator('#scan-batch-campus').selectOption(String(campus));for(const field of await page.locator('#asset-form textarea[name^=d]').all())await field.fill('已核对合成重码是另一件实物');}
  async function assertNoOverflow(){assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2),true,'page fits viewport');}
  for(const [label,engine,options] of [['chromium',chromium,{viewport:{width:1280,height:900}}],['webkit',webkit,devices['iPhone 13']]]){
    engineName=label;phase='launch';browser=await engine.launch();
    // Fault-injection routes bypass Service Workers; pwa.cjs exercises the real worker.
    const context=await browser.newContext({...options,serviceWorkers:'block',storageState:await api.storageState()});
    page=await context.newPage();page.setDefaultTimeout(15000);page.on('pageerror',e=>errors.push(label+': '+e.message));
    await page.goto(base+'/assets');await page.locator('#asset-status').filter({hasText:'数据已更新'}).waitFor();
    assert.equal(await page.evaluate(()=>window.AssetLedger.catalog.intake_enabled),true);

    phase='component draft and cancel';
    const componentName='合成离线电阻 '+label,componentCode=label==='chromium'?'C98765001':'C98765002';
    const raw1=`{on:SYNTHETIC-${label}-A,pc:${componentCode},pm:${componentName},qty:12}`;
    const raw2=`{on:SYNTHETIC-${label}-B,pc:${componentCode},pm:${componentName},qty:8}`;
    await openScan();await scan(raw1);await scan(raw2);await scan(raw1);
    assert.equal(await page.locator('[data-scan-select]').count(),2,'repeat scan does not duplicate or sum');
    assert.match(await page.locator('#asset-scan-queue').textContent(),/将自动建档/);
    assert.equal((await get('/api/assets/items?q='+encodeURIComponent(componentName))).total,0);
    await click('edit-package');await page.locator('#asset-dialog[open]').waitFor();
    assert.equal(await page.locator('#asset-form [name=name]').inputValue(),componentName,'offline uses label model');
    assert.equal(await page.locator('#asset-form [name=kind]').inputValue(),'consumable');
    assert.equal(await page.locator('#asset-form [name=unit]').inputValue(),'个');
    const renamed=componentName+' 已核对';
    await page.locator('#asset-form [name=name]').fill(renamed);await page.locator('#asset-save').click();
    await page.locator('#asset-dialog').waitFor({state:'hidden'});await page.locator('#asset-scan-dialog[open]').waitFor();
    assert.equal(await page.locator('#asset-scan-queue li').filter({hasText:renamed}).count(),2,'supplier draft edit is shared');
    assert.equal((await get('/api/assets/items?q='+encodeURIComponent(renamed))).total,0,'draft editor writes nothing');
    await chooseBatch();await page.locator('#asset-close').click();await page.locator('#asset-scan-dialog[open]').waitFor();
    assert.equal((await get('/api/assets/items?q='+encodeURIComponent(renamed))).total,0,'cancel confirmation writes nothing');
    await page.locator('#asset-scan-close').click();await openScan();
    assert.equal(await page.locator('[data-scan-select]').count(),2,'close/reopen preserves queue');
    await chooseBatch();
    assert.equal(await page.locator('#asset-form [name=q0]').inputValue(),'12');
    assert.equal(await page.locator('#asset-form [name=q1]').inputValue(),'8');
    let lost=true;
    await page.route('**/api/assets/scan/batch',async route=>{if(lost){lost=false;await route.fetch();await route.abort();}else await route.continue();});
    await page.locator('#asset-form [name=reason]').fill('合成元件整批入库');await page.locator('#asset-save').click();
    await page.locator('#asset-form-status').filter({hasText:'结果尚未确认'}).waitFor();
    await page.unroute('**/api/assets/scan/batch');await page.locator('#asset-save').click();
    await page.locator('#asset-dialog').waitFor({state:'hidden'});await page.locator('#asset-scan-dialog[open]').waitFor();
    const components=(await get('/api/assets/items?q='+encodeURIComponent(renamed))).items;
    assert.equal(components.length,1,'two wrappers create one item despite uncertain response retry');
    const component=components[0],stock=(await itemDetail(component.id)).campuses;
    assert.equal(stock.reduce((n,c)=>n+c.available,0),20,'retry does not double inventory');
    assert.equal(await page.locator('[data-scan-select]').count(),0);
    passed.push(label+': offline automatic shared component drafts, cancel/reopen and lost-response retry');

    phase='invalid quantity';
    const invalid=`{on:SYNTHETIC-${label}-INVALID,pc:${componentCode},pm:${componentName},qty:bad}`;
    await scan(invalid);await chooseBatch();assert.equal(await page.locator('#asset-form [name=q0]').inputValue(),'');
    await page.locator('#asset-form [name=reason]').fill('合成缺失数量');await page.locator('#asset-save').click();
    assert.equal(await page.locator('#asset-dialog').getAttribute('open'),'');
    assert.equal((await itemDetail(component.id)).campuses.reduce((n,c)=>n+c.available,0),20);
    await page.locator('#asset-close').click();await page.locator('#asset-scan-dialog[open]').waitFor();await click('clear-packages');
    passed.push(label+': invalid label quantity stays editable and cannot submit');

    phase='explicit independent shared draft';
    const sharedName='合成另建共享 '+label,sharedCode=label==='chromium'?'C98765101':'C98765102';
    const originalShared=await save('items',{name:sharedName,specification:sharedName,unit:'个',kind:'consumable'});
    await scan(`{on:FORCE-${label}-A,pc:${sharedCode},pm:${sharedName},qty:2}`);
    await scan(`{on:FORCE-${label}-B,pc:${sharedCode},pm:${sharedName},qty:3}`);
    assert.equal(await page.locator('#asset-scan-queue li').filter({hasText:'将自动建档'}).count(),2,'same-name suggestion does not silently associate');
    await click('edit-package');await page.locator('#asset-dialog[open]').waitFor();
    await page.locator('#asset-form [name=force_new]').check();
    await page.locator('#asset-form [name=conflict_reason]').fill('相同名称规格，但已核对为另一厂家档案');
    await page.locator('#asset-save').click();await page.locator('#asset-dialog').waitFor({state:'hidden'});
    await page.locator('#asset-scan-dialog[open]').waitFor();await chooseBatch();await submit('合成确认另建共享型号');
    const sharedItems=(await get('/api/assets/items?q='+encodeURIComponent(sharedName))).items;
    assert.equal(sharedItems.length,2,'force-new propagates to all wrappers sharing the same draft');
    const distinctShared=sharedItems.find(i=>i.id!==originalShared.id);assert(distinctShared);
    assert.equal((await itemDetail(distinctShared.id)).campuses.reduce((n,c)=>n+c.available,0),5);
    assert.equal((await itemDetail(originalShared.id)).campuses.reduce((n,c)=>n+c.available,0),0);
    passed.push(label+': explicit independent same-name draft stays shared across wrappers without merging existing catalog');

    phase='motor grouping';
    const motorName='合成原码电机 '+label,serials=['MOTOR-SYNTHETIC-'+label+'-A','MOTOR-SYNTHETIC-'+label+'-B'];
    await scan(serials[0]);await scan(serials[1]);
    assert.equal(await page.locator('[data-scan-action=edit-package]').count(),2,'unknown code remains editable');
    for(const checkbox of await page.locator('[data-scan-select]').all())await checkbox.check();
    await click('edit-selected');await page.locator('#asset-dialog[open]').waitFor();
    assert.equal(await page.locator('#asset-form [name=kind]').inputValue(),'returnable');
    assert.equal(await page.locator('#asset-form [name=returnable_mode]').inputValue(),'unit');
    assert.equal(await page.locator('#asset-form [name=name]').inputValue(),'','manufacturer raw text is not guessed as a name');
    await page.locator('#asset-form [name=name]').fill(motorName);
    await page.locator('#asset-form [name=specification]').fill('SYNTHETIC-24V');
    await page.locator('#asset-form [name=unit]').fill('台');await page.locator('#asset-save').click();
    await page.locator('#asset-dialog').waitFor({state:'hidden'});await page.locator('#asset-scan-dialog[open]').waitFor();
    assert.equal((await get('/api/assets/items?q='+encodeURIComponent(motorName))).total,0);
    await chooseBatch();assert.equal(await page.locator('#asset-form [name=q0]').inputValue(),'1');
    await submit('合成厂家原码两台电机入库');
    const motorItems=(await get('/api/assets/items?q='+encodeURIComponent(motorName))).items;
    assert.equal(motorItems.length,1);const motor=motorItems[0];let motorUnits=await units(motor.id);
    assert.equal(motorUnits.length,2);assert.notEqual(motorUnits[0].id,motorUnits[1].id);
    const primary=(await post('/api/assets/scan/resolve',{raw:serials[0]})).unit;
    assert(primary&&motorUnits.some(u=>u.id===primary.id));
    await scan(serials[0]);await scan(serials[0]);assert.equal(await page.locator('[data-scan-select]').count(),1);
    assert.equal(await page.locator('[data-scan-action=batch-receive]').isDisabled(),true,'already received unit requires explicit new identity');
    assert.equal((await units(motor.id)).length,2);await click('clear-packages');
    passed.push(label+': grouped manufacturer serials create one model, distinct U identities and block duplicate intake');

    phase='motor operations';
    for(const operation of ['borrow','return','move']){
      await openScan(operation);await scan(serials[0]);
      assert.equal(await page.locator('#asset-scan-queue li').count(),1);
      assert.match(await page.locator('#asset-scan-queue li strong').textContent(),new RegExp('U:'+primary.id+'$'));
      await click('submit-units');await page.locator('#asset-dialog[open]').waitFor();
      if(operation==='return')await page.locator('#asset-form [name=campus_id]').selectOption(String(campus));
      if(operation==='move'){await page.locator('#asset-form [name=campus_id]').selectOption(String(west));await page.locator('#asset-form [name=location]').fill('合成电机柜');}
      await submit('合成原码'+operation);motorUnits=await units(motor.id);const u=motorUnits.find(u=>u.id===primary.id);
      if(operation==='borrow')assert(u.keeper_id);else assert.equal(u.keeper_id,null);
      if(operation==='move')assert.equal(u.campus_id,west);
    }
    const take=await post('/api/assets/units/stocktakes',{request_id:crypto.randomUUID(),campus_id:west,item_ids:[motor.id],scope:'warehouse',reason:'合成原码盘点'});
    await openScan('count',{takeId:take.id,unitIds:[primary.id]});await scan(serials[0]);await click('submit-units');await submit('合成原码已见实物');
    const observation=(await get('/api/assets/units/stocktakes/'+take.id)).units.find(u=>u.unit_id===primary.id);
    assert(observation.seen_at,'stocktake records a seen timestamp for the actual unit');
    const takeRevision=(await get('/api/assets/units/stocktakes/'+take.id)).revision;
    await post('/api/assets/units/stocktakes/record',{request_id:crypto.randomUUID(),take_id:take.id,action:'close',revision:takeRevision,reason:'合成盘点完成'});
    passed.push(label+': manufacturer raw code resolves the actual unit for borrow, return, move and stocktake');

    phase='existing model selection';
    await openScan('receive');await scan('MOTOR-SYNTHETIC-'+label+'-C');await click('edit-package');
    await page.locator('#asset-dialog[open]').waitFor();await page.locator('#asset-form [name=draft_mode]').selectOption('existing');
    await page.locator('#scan-draft-query').fill(motorName);await page.locator('#scan-draft-search').click();
    await page.locator('#asset-form [name=draft_item] option[value="'+motor.id+'"]').waitFor({state:'attached'});
    await page.locator('#asset-form [name=draft_item]').selectOption(String(motor.id));await page.locator('#asset-save').click();
    await page.locator('#asset-dialog').waitFor({state:'hidden'});await page.locator('#asset-scan-dialog[open]').waitFor();
    await chooseBatch();await submit('合成新序列码归入已有电机型号');
    assert.equal((await units(motor.id)).length,3);assert.equal((await get('/api/assets/items?q='+encodeURIComponent(motorName))).total,1);
    passed.push(label+': unknown manufacturer code can search and select an existing model');

    phase='collision';
    await openScan('receive');await scan(serials[0]);await click('review-package');await click('new-draft');
    await page.locator('#asset-dialog[open]').waitFor();
    await page.locator('#asset-form [name=name]').fill(motorName+' 独立档案');
    await page.locator('#asset-form [name=conflict_reason]').fill('另一厂商重复使用此二维码，已核对为独立设备');
    assert.equal(await page.locator('#asset-form [name=force_new]').isChecked(),true);
    await page.locator('#asset-save').click();await page.locator('#asset-dialog').waitFor({state:'hidden'});
    await page.locator('#asset-scan-dialog[open]').waitFor();await chooseBatch();await submit('合成重码另建入库');
    const ambiguous=await post('/api/assets/scan/resolve',{raw:serials[0]});
    assert.equal(ambiguous.matches.length,2);assert.equal(ambiguous.item,null);assert.equal(ambiguous.unit,null);
    await openScan('general');await scan(serials[0]);
    assert.equal(await page.locator('[data-scan-action=select-match]').count(),2,'collision requires explicit target each scan');
    await click('select-match');assert((await page.evaluate(()=>window.AssetScan.resolved)).target_id);
    await scan(serials[0]);assert.equal((await page.evaluate(()=>window.AssetScan.resolved)).target_id,undefined,'scan does not remember previous choice');
    assert.equal((await units(motor.id)).length,3,'collision did not change the original model');
    await assertNoOverflow();await page.locator('[data-scan-action=select-match]').first().scrollIntoViewIfNeeded();await page.screenshot({path:path.join(output,label+'-collision.png')});
    passed.push(label+': collision can create an independent archive and always asks for target again');

    phase='reserved internal prefix collision';
    const reserved='M:'+component.id,otherName='<img src=x onerror=window.__bad=1>';
    await openScan('receive');await scan(reserved);await click('review-package');await click('new-draft');
    await page.locator('#asset-dialog[open]').waitFor();await page.locator('#asset-form [name=name]').fill(otherName);
    await page.locator('#asset-form [name=conflict_reason]').fill('厂家原码与内部物料编号同文，已核对另一实物');
    await page.locator('#asset-save').click();await page.locator('#asset-dialog').waitFor({state:'hidden'});
    await page.locator('#asset-scan-dialog[open]').waitFor();await chooseBatch();await page.locator('#asset-form [name=q0]').fill('4');await submit('合成前缀重码另建入库');
    const prefixed=await post('/api/assets/scan/resolve',{raw:reserved});
    assert.equal(prefixed.matches.length,2);assert.equal(prefixed.item,null);
    assert(prefixed.matches.some(m=>m.item.id===component.id),'original internal target remains');
    assert(prefixed.matches.some(m=>m.item.name===otherName),'external target also resolves despite internal prefix');
    await openScan('general');await scan(reserved);assert.equal(await page.locator('[data-scan-action=select-match]').count(),2);
    assert.equal(await page.locator('#asset-scan-preview img').count(),0,'untrusted archive names remain text in candidate buttons');
    assert.equal(await page.evaluate(()=>window.__bad),undefined,'candidate names do not execute markup');
    assert((await page.locator('#asset-scan-preview').textContent()).includes(otherName));
    await page.locator('[data-scan-action=select-match]').first().scrollIntoViewIfNeeded();await page.screenshot({path:path.join(output,label+'-reserved-prefix.png')});
    passed.push(label+': raw code matching an internal M prefix retains both internal and external targets');
    await page.locator('#asset-scan-close').click();await assertNoOverflow();await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:path.join(output,label+'.png')});
    await browser.close();browser=null;
  }
  assert.deepEqual(errors,[]);const result={ok:true,passed,nativeHardware:false,supplierLookup:'isolated offline fixture'};fs.writeFileSync(path.join(output,'result.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
}
main().catch(async e=>{await recordFailure(e,{output,page,engine:engineName,phase,passed,pageErrors:errors});console.error(e);process.exitCode=1;}).finally(()=>cleanupResources({browser:()=>browser?.close(),api:()=>api?.dispose(),server:()=>server.kill()}));
