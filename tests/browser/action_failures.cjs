"use strict";
const assert=require('node:assert/strict'), fs=require('node:fs'), path=require('node:path');
const {spawn}=require('node:child_process');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'../..');
async function api(page,url,data,method='POST') {
  return page.evaluate(async({url,data,method})=>{
    const r=await fetch(url,data===undefined?{}:{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
    return {status:r.status,body:await r.json()};
  },{url,data,method});
}
(async()=>{
  const server=spawn(process.env.REVIEW_PYTHON||'python',['-B',path.join(__dirname,'server.py')],{cwd:root,env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'},stdio:['ignore','pipe','pipe']});
  let browser,log=''; const checks=[],errors=[],alerts=[];
  try {
    const base=await new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>reject(new Error('server timeout '+log)),30000);
      server.stderr.on('data',data=>log+=data);
      server.stdout.on('data',data=>{const m=String(data).match(/BROWSER_URL=(\S+)/);if(m){clearTimeout(timer);resolve(m[1]);}});
      server.once('exit',code=>{clearTimeout(timer);reject(new Error('server exit '+code));});
    });
    browser=await chromium.launch({headless:true});
    const context=await browser.newContext({viewport:{width:1280,height:900}}), page=await context.newPage();
    page.setDefaultTimeout(12000);
    page.on('pageerror',error=>errors.push(error.message));
    page.on('dialog',async d=>{alerts.push(d.message());await d.accept();});
    const reject=async(pattern,status=500)=>page.route(pattern,r=>r.fulfill({status,json:{ok:false,success:false,code:status,error:'模拟操作失败',msg:'模拟操作失败'}}));
    const idle=selector=>page.waitForFunction(selector=>!document.querySelector(selector).disabled,selector);

    await page.goto(base+'/login');
    await page.locator('[name=username]').fill('admin');await page.locator('[name=password]').fill('browser-test-initial');
    await page.route('**/login',r=>r.request().method()==='POST'?r.abort():r.continue());
    await page.locator('button[type=submit]').click();await page.locator('#login-error').filter({hasText:'网络'}).waitFor();
    assert.equal(await page.locator('[name=password]').inputValue(),'browser-test-initial');await idle('button[type=submit]');
    await page.unroute('**/login');await page.locator('button[type=submit]').click();await page.waitForURL('**/account');
    await page.locator('#account-password-form [name=old]').fill('browser-test-initial');
    await page.locator('#account-password-form [name=new]').fill('action-test-password');
    await page.locator('#account-password-form [name=confirm]').fill('action-test-password');
    await reject('**/api/password',403);await page.locator('#account-password-form button').click();
    await page.locator('#account-result').filter({hasText:'模拟操作失败'}).waitFor();assert.equal(await page.locator('#account-password-form [name=new]').inputValue(),'action-test-password');
    await page.unroute('**/api/password');await page.locator('#account-password-form button').click();await page.waitForURL(base+'/');
    checks.push('login network failure and password-save rejection retain inputs and recover');
    assert.equal((await api(page,'/api/admin/permissions/1',{permissions:['funds.review','invoices.manage']})).status,409);
    const task=(await api(page,'/api/task',{title:'按钮失败测试任务'})).body;

    await page.goto(base+'/tasks');await page.locator('#new-task-toggle').click();await page.locator('#create-form [name=title]').fill('保留任务输入');
    let release,calls=0;const held=new Promise(resolve=>release=resolve);
    await page.route('**/api/task',async r=>{calls++;await held;await r.fulfill({status:500,json:{ok:false,error:'模拟创建失败'}});});
    await page.evaluate(()=>{const form=document.getElementById('create-form');form.requestSubmit();form.requestSubmit();});
    await page.waitForFunction(()=>document.querySelector('#create-form button[type=submit]').disabled);
    await page.waitForTimeout(80);assert.equal(calls,1);release();
    await page.locator('#create-form .form-message').filter({hasText:'模拟创建失败'}).waitFor();
    assert.equal(await page.locator('#create-form [name=title]').inputValue(),'保留任务输入');await idle('#create-form button[type=submit]');
    await page.unroute('**/api/task');checks.push('duplicate task submit sends one request; failure restores controls and draft');
    await page.goto(base+'/manage');
    await reject(`**/api/task/${task.id}/delete`);
    await page.locator(`[data-delete-task="${task.id}"]`).click();await idle(`[data-delete-task="${task.id}"]`);
    assert.ok((await api(page,'/api/data')).body.tasks.some(t=>t.id===task.id));await page.unroute(`**/api/task/${task.id}/delete`);
    await page.locator(`[data-edit-task="${task.id}"]`).click();
    await page.locator('#task-form [name=title]').fill('冲突时保留标题');
    await reject(`**/api/task/${task.id}`,409);await page.locator('#task-form button[type=submit]').click();
    await page.locator('#task-form-message').filter({hasText:'任务已被他人更新'}).waitFor();
    assert.equal(await page.locator('#task-form [name=title]').inputValue(),'冲突时保留标题');
    await page.unroute(`**/api/task/${task.id}`);checks.push('task deletion failure and edit conflict preserve task and draft');

    await page.goto(base+'/account');await idle('#account-profile-form button[type=submit]');
    await page.locator('#account-profile-form [name=username]').fill('draft-admin');await reject('**/api/account/profile',401);
    await page.locator('#account-profile-form button[type=submit]').click();
    await page.locator('#account-profile-result').filter({hasText:'登录已失效'}).waitFor();
    assert.equal(await page.locator('#account-profile-form [name=username]').inputValue(),'draft-admin');assert.equal(page.url(),base+'/account');
    assert.equal(await page.locator('#business-feedback a').getAttribute('target'),'_blank');await page.unroute('**/api/account/profile');
    checks.push('expired login retains current page and offers separate relogin');

    await page.goto(base+'/members');await page.locator('#member-create-groups input').first().waitFor();
    await page.locator('#member-form [name=name]').fill('保留成员');await reject('**/api/member',403);
    await page.locator('#member-form button[type=submit]').click();await page.locator('#member-form .form-message').filter({hasText:'模拟操作失败'}).waitFor();
    assert.equal(await page.locator('#member-form [name=name]').inputValue(),'保留成员');await idle('#member-form button[type=submit]');
    await page.unroute('**/api/member');checks.push('member creation rejection retains form');

    await page.goto(base+'/funds/');await page.waitForFunction(()=>typeof bootstrap!=='undefined');
    await page.locator('[data-bs-target="#t-add"]').click();await page.locator('#aT').selectOption('进');await page.locator('#aAmt').fill('123.45');
    await reject('**/funds/api/records',500);await page.locator('#addF button[type=submit]').click();
    await page.locator('#funds-feedback').filter({hasText:'结果待确认'}).waitFor();assert.equal(await page.locator('#aAmt').inputValue(),'123.45');
    assert.ok(await page.locator('#addF button[type=submit]').isDisabled());await idle('#funds-create-retry');
    await page.unroute('**/funds/api/records');
    const record=(await api(page,'/funds/api/records',{request_id:require('node:crypto').randomUUID(),record_type:'进',amount:'5.00',record_date:'2026-10-15'})).body;
    await page.goto(base+'/funds/');await page.locator(`#tbody button[onclick="edR(${record.id})"]`).click();
    await page.locator('#eAmt').fill('88.88');await reject(`**/funds/api/records/${record.id}`,500);
    await page.locator('#editMd [onclick="saveE()"] ').click();await page.locator('#funds-feedback').filter({hasText:'模拟操作失败'}).waitFor();
    assert.equal(await page.locator('#eAmt').inputValue(),'88.88');assert.ok(await page.locator('#editMd').isVisible());
    await page.locator('#editMd [data-bs-dismiss=modal]').first().click();
    // DELETE now carries ?version=..., unlike PUT. Mock the query-bearing URL
    // explicitly or this test would perform a real irreversible deletion.
    const deletePattern = `**/funds/api/records/${record.id}?version=*`;
    await reject(deletePattern, 500);
    await page.locator(`#tbody button[onclick^="delR(${record.id},"]`).click();await idle(`#tbody button[onclick^="delR(${record.id},"]`);
    assert.ok((await api(page,'/funds/api/records')).body.data.some(r=>r.id===record.id));
    await page.unroute(deletePattern);
    await page.unroute(`**/funds/api/records/${record.id}`);
    await api(page,`/funds/api/records/${record.id}`,{amount:'5.01',version:record.version},'PUT');await page.reload();
    const reviewButton=page.locator(`#tbody button[onclick^="reviewRecord(${record.id},"]`);
    await reject(`**/funds/api/records/${record.id}/review`,403);await reviewButton.click();
    await page.locator('#funds-feedback').filter({hasText:'模拟操作失败'}).waitFor();
    await idle(`#tbody button[onclick^="reviewRecord(${record.id},"]`);
    assert.equal((await api(page,'/funds/api/records')).body.data.find(r=>r.id===record.id).review_status,'待审核');
    await page.unroute(`**/funds/api/records/${record.id}/review`);
    checks.push('funds add/edit/delete/review failures retain inputs and records');

    await page.goto(base+'/invoices/');
    await page.route('**/invoices/api/upload',r=>r.abort());
    await page.locator('#fileInput').setInputFiles({name:'failure.pdf',mimeType:'application/pdf',buffer:Buffer.from('%PDF-1.4\naction-failures-test')});
    await page.locator('#toast').filter({hasText:'上传失败'}).waitFor();await idle('#fileInput');await page.unroute('**/invoices/api/upload');
    await page.waitForFunction(()=>Array.isArray(App.owners));
    await page.evaluate(()=>{App.owners=null;});await reject('**/invoices/api/owners',503);
    await page.locator('#fileInput').setInputFiles({name:'failure.pdf',mimeType:'application/pdf',buffer:Buffer.from('%PDF-1.4\naction-failures-test')});
    await page.locator('[data-retry-upload-owners]').waitFor();await page.unroute('**/invoices/api/owners');await page.locator('[data-retry-upload-owners]').click();
    await page.locator('.bu-amount-0').waitFor();await page.locator('#buSubmitter').selectOption('1');await page.locator('.bu-amount-0').fill('9.99');
    await page.locator('.bu-date-0').fill('2026-10-15');await page.locator('.bu-month-0').fill('2026.10');
    await reject('**/invoices/api/upload/confirm/batch',500);await page.locator('#batchUploadModal .btn-save').click();
    await page.locator('#toast').filter({hasText:'模拟操作失败'}).waitFor();await idle('#batchUploadModal .btn-save');
    assert.equal(await page.locator('.bu-amount-0').inputValue(),'9.99');assert.ok(await page.locator('#batchUploadModal[open]').isVisible());
    await page.unroute('**/invoices/api/upload/confirm/batch');
    await page.route('**/invoices/api/upload/confirm/batch',r=>r.fulfill({json:{success:true,message:'部分失败',results:r.request().postDataJSON().items.map(item=>({tmp_path:item.tmp_path,status:'error'}))}}));
    await page.locator('#batchUploadModal .btn-save').click();await page.locator('#toast').filter({hasText:'失败条目及已填内容已保留'}).waitFor();
    assert.equal(await page.locator('.bu-amount-0').inputValue(),'9.99');assert.equal(await page.locator('.bu-sub-0').inputValue(),'1');
    await page.unroute('**/invoices/api/upload/confirm/batch');await page.locator('#batchUploadModal .btn-save').click();await page.locator('#batchUploadModal[open]').waitFor({state:'hidden'});
    await page.locator('[data-invoice-detail]').first().click();await page.locator('#detNotes').fill('保留发票备注');await reject('**/invoices/api/invoice/update',403);
    await page.locator('#detailModal .btn-save').click();await page.locator('#toast').filter({hasText:'模拟操作失败'}).waitFor();assert.equal(await page.locator('#detNotes').inputValue(),'保留发票备注');await idle('#detailModal .btn-save');
    await page.locator('#detailModal .btn-danger').click();await idle('#detailModal .btn-danger');assert.ok(await page.locator('#detailModal[open]').isVisible());
    await page.locator('#detailModal .btn-cancel').click();
    const status=page.locator('[data-invoice-status]').first();await status.selectOption('已报销');await idle('[data-invoice-status]');assert.equal(await status.inputValue(),'未报销');
    await page.unroute('**/invoices/api/invoice/update');
    await page.locator('[data-invoice-detail]').first().click();await page.locator('#detNotes').fill('等待失败时保留');
    let releaseDetail;const detailHeld=new Promise(resolve=>releaseDetail=resolve);
    await page.route('**/invoices/api/invoice/update',async r=>{await detailHeld;await r.fulfill({status:500,json:{error:'模拟保存失败'}});});
    await page.locator('#detailModal .btn-save').click();await page.locator('#detailModal').click({position:{x:2,y:2}});
    assert.ok(await page.locator('#detailModal[open]').isVisible());releaseDetail();await idle('#detailModal .btn-save');
    assert.equal(await page.locator('#detNotes').inputValue(),'等待失败时保留');await page.unroute('**/invoices/api/invoice/update');await page.locator('#detailModal .btn-cancel').click();
    await page.evaluate(()=>{App.stats=null;});await reject('**/invoices/api/stats',503);
    await page.locator('[onclick="StatsModal.show()"]').click();await page.locator('#toast').filter({hasText:'模拟操作失败'}).waitFor();
    assert.equal(await page.locator('#statsModal[open]').count(),0);assert.equal(await page.evaluate(()=>App.stats),null);
    await page.unroute('**/invoices/api/stats');
    let jobs=0;await page.route('**/invoices/api/job/start',r=>{jobs++;return r.abort();});await reject('**/invoices/api/organize/preview');
    await page.locator('#organizeBtn').click();await page.locator('#toast').filter({hasText:'未启动整理'}).waitFor();assert.equal(jobs,0);await idle('#organizeBtn');
    await page.unroute('**/invoices/api/organize/preview');await page.unroute('**/invoices/api/job/start');
    checks.push('invoice upload/owner-loading/partial-confirm/edit/clear/status failures retain data; pending dialog and statistics errors stay recoverable; failed organize preview never starts a job');
    await reject('**/invoices/api/export/download',403);await page.locator('[onclick="exportCSV()"]').click();await page.locator('#business-feedback').filter({hasText:'模拟操作失败'}).waitFor();
    assert.equal(page.url(),base+'/invoices/');await page.unroute('**/invoices/api/export/download');
    const [download]=await Promise.all([page.waitForEvent('download'),page.locator('[onclick="exportCSV()"]').click()]);assert.equal(download.suggestedFilename(),'invoices.csv');
    checks.push('failed download preserves page and subsequent successful download works');

    const permissionMember=(await api(page,'/api/member',{name:'权限失败样本',username:'permission-failure',password:'synthetic-password',role:'member'})).body;
    await page.goto(base+'/admin/security');await page.locator('[data-save-permissions]').first().waitFor();await reject(`**/api/admin/permissions/${permissionMember.id}`,403);
    await page.locator(`[data-member-id="${permissionMember.id}"] [data-save-permissions]`).click();await page.locator('#permission-result').filter({hasText:'模拟操作失败'}).waitFor();await idle(`[data-member-id="${permissionMember.id}"] [data-save-permissions]`);
    await page.unroute(`**/api/admin/permissions/${permissionMember.id}`);checks.push('permission-save rejection restores button');
    await reject('**/discussions/api/topics?*',503);await page.goto(base+'/discussions');await page.locator('#collab-list-retry').waitFor();
    await page.unroute('**/discussions/api/topics?*');await page.locator('#collab-list-retry').click();await page.locator('#collab-topics').filter({hasText:'还没有话题'}).waitFor();checks.push('local collaboration loading failure has a working retry');
    await reject('**/logout',500);await page.locator('.ws-signout').click();await page.locator('#business-feedback').filter({hasText:'退出登录失败'}).waitFor();await idle('.ws-signout');assert.equal(page.url(),base+'/discussions');
    await page.unroute('**/logout');await page.locator('.ws-signout').click();await page.waitForURL('**/login');checks.push('logout failure leaves session usable; retry succeeds');
    assert.deepEqual(errors,[]);
    const out=path.join(root,'.venv','action-failure-check');fs.mkdirSync(out,{recursive:true});fs.writeFileSync(path.join(out,'result.json'),JSON.stringify({ok:true,checks,errors},null,2));
    console.log('ACTION_FAILURES_OK '+JSON.stringify(checks));
  }catch(error){console.error(error);console.error('page errors',errors);console.error(log.slice(-2500));process.exitCode=1;}
  finally{if(browser)await browser.close();server.kill();}
})();
