"use strict";
// Requires Playwright + Chromium, and PYTHONPATH pointing at the app dependencies.
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '../..');

async function api(page, url, data, method='POST') {
  return page.evaluate(async ({url,data,method}) => {
    const response=await fetch(url,data === undefined ? {} : {
      method,headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
    return {status:response.status,body:await response.json()};
  },{url,data,method});
}
async function login(page, base, username, oldPassword, newPassword, reveal=false) {
  await page.goto(base+'/login');
  await page.locator('[name=username]').fill(username);
  await page.locator('[name=password]').fill(oldPassword);
  if(reveal) {
    await page.getByRole('button',{name:'显示密码',exact:true}).click();
    await page.locator('[name=password]').press('Enter');
  } else {
    await page.locator('#login-form button[type=submit]').click();
  }
  await page.waitForURL('**/account');
  await page.locator('#account-password-form [name=old]').fill(oldPassword);
  await page.locator('#account-password-form [name=new]').fill(newPassword);
  await page.locator('#account-password-form [name=confirm]').fill(newPassword);
  await page.locator('#account-password-form button').click();
  await page.waitForURL(base+'/');
}

async function checkPasswordVisibility(page, base) {
  await page.goto(base+'/login');
  const password=page.getByLabel('密码',{exact:true});
  const toggle=page.locator('.login-password-toggle');
  let submissions=0;
  const observe=request=>{if(request.method()==='POST' && request.url()===base+'/login')submissions++;};
  page.on('request',observe);
  await page.locator('[name=username]').fill('visibility-check');
  await password.fill('Long-password-for-visibility-check-1234567890');
  const value=await password.inputValue();
  assert.equal(await password.getAttribute('type'),'password');
  await password.focus();await page.keyboard.press('Tab');
  assert.equal(await toggle.evaluate(element=>element===document.activeElement),true);
  await page.keyboard.press('Enter');
  assert.equal(await password.getAttribute('type'),'text');
  assert.equal(await toggle.getAttribute('aria-label'),'隐藏密码');
  assert.equal(await toggle.locator('.password-hide-icon').isVisible(),true);
  assert.equal(await toggle.locator('.password-show-icon').isVisible(),false);
  await page.keyboard.press('Space');
  assert.equal(await password.getAttribute('type'),'password');
  assert.equal(await toggle.getAttribute('aria-label'),'显示密码');
  assert.equal(await toggle.locator('.password-show-icon').isVisible(),true);
  assert.equal(await toggle.locator('.password-hide-icon').isVisible(),false);
  assert.equal(await password.inputValue(),value);
  const output=path.join(root,'.venv','login-password-check');
  fs.mkdirSync(output,{recursive:true});
  for(const width of [375,1280]) {
    await page.setViewportSize({width,height:900});
    await toggle.click();
    assert.equal(await password.getAttribute('type'),'text');
    assert.equal(await password.inputValue(),value);
    const box=await toggle.boundingBox();
    assert.ok(box.width>=44 && box.height>=44);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    const spacing=await password.evaluate(input=>({padding:parseFloat(getComputedStyle(input).paddingRight),width:input.getBoundingClientRect().width}));
    assert.ok(spacing.padding>=box.width+2 && spacing.width>spacing.padding);
    await page.screenshot({path:path.join(output,`login-${width}.png`),fullPage:true});
    await toggle.click();
  }
  assert.equal(submissions,0,'visibility controls must not submit the login form');
  await toggle.click();await page.reload();
  assert.equal(await password.getAttribute('type'),'password');
  assert.equal(await toggle.getAttribute('aria-label'),'显示密码');
  page.off('request',observe);
}

(async()=>{
  const server=spawn(process.env.REVIEW_PYTHON || 'python', ['-B',path.join(__dirname,'server.py')],{
    cwd:root,env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'},stdio:['ignore','pipe','pipe']});
  let browser,serverLog='';
  try {
    const base=await new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>reject(new Error('server startup timeout\n'+serverLog)),30000);
      server.stderr.on('data',data=>{serverLog+=data});
      server.stdout.on('data',data=>{const match=String(data).match(/BROWSER_URL=(\S+)/);if(match){clearTimeout(timer);resolve(match[1])}});
      server.once('exit',code=>{clearTimeout(timer);reject(new Error('server exited '+code+'\n'+serverLog))});
    });
    browser=await chromium.launch({headless:true});
    const context=await browser.newContext();
    await context.route(/^https:\/\/(cdn\.bootcdn\.net|cdn\.jsdelivr\.net)\//,route=>route.abort());
    assert.equal((await context.request.get(base+'/vendor/bootstrap/bootstrap.bundle.min.js')).status(),200);
    const admin=await context.newPage();
    const errors=[];
    admin.on('pageerror',error=>errors.push(error.message));
    const alerts=[];
    admin.on('dialog',async dialog=>{if(dialog.type()==='alert')alerts.push(dialog.message());await dialog.accept()});
    await checkPasswordVisibility(admin,base);
    await login(admin,base,'admin','browser-test-initial','browser-test-changed',true);
    const member=(await api(admin,'/api/member',{name:'Browser Member',username:'browser-member',
      password:'browser-member-initial',role:'manager'})).body;
    assert.equal((await api(admin,'/api/admin/permissions/1',{permissions:['funds.review','invoices.manage']})).status,200);

    // Use the actual chooser + confirmation table, including a dangerous vendor.
    await admin.goto(base+'/invoices/');
    await admin.locator('#fileInput').setInputFiles({name:'receipt.pdf',mimeType:'application/pdf',
      buffer:Buffer.from('%PDF-1.4\nbrowser-unique-receipt')});
    await admin.locator('#batchUploadModal[open]').waitFor();
    await admin.locator('#buSubmitter').selectOption(String(member.id));
    await admin.locator('.bu-amount-0').fill('99.99');
    await admin.locator('.bu-date-0').fill('2026-10-14');
    await admin.locator('.bu-month-0').fill('2026.10');
    const vendor="商家'-alert(9191)-'% +";
    await admin.locator('.bu-vendor-0').fill(vendor);
    await admin.locator('#batchUploadModal .btn-save').click();
    await admin.locator('#batchUploadModal[open]').waitFor({state:'hidden'});
    await admin.waitForFunction(()=>document.querySelectorAll('#invoiceTableBody tr').length===1);
    assert.equal(await admin.locator('#invoiceTableBody [onclick],#invoiceTableBody [onchange]').count(),0);
    const invoices=(await api(admin,'/invoices/api/invoices')).body.data;
    assert.equal(invoices.length,1,'one file selection must upload only once');
    assert.equal(invoices[0].vendor,vendor); assert.equal(invoices[0].date,'2026-10-14');
    const preview=admin.waitForResponse(response=>response.url().includes('/api/open_file'));
    await admin.locator('button[data-invoice-file]').click();
    assert.equal((await preview).status(),200);
    assert.equal(alerts.length,0,'filename must never execute JavaScript');
    const memberContext=await browser.newContext();
    const memberPage=await memberContext.newPage();
    await login(memberPage,base,'browser-member','browser-member-initial','browser-member-changed');
    await memberPage.goto(base+'/invoices/');
    assert.equal((await api(memberPage,'/invoices/api/open_file?rel_path='+encodeURIComponent(invoices[0].rel_path))).status,200);

    // A displayed review must remain tied to its original version.
    const record=(await api(memberPage,'/funds/api/records',{record_type:'进',amount:'5.00',record_date:'2026-10-14'})).body;
    await admin.goto(base+'/funds/');
    await admin.waitForFunction(()=>typeof bootstrap!=='undefined');
    await admin.locator(`#tbody button[onclick^="reviewRecord(${record.id},"]`).waitFor();
    await api(memberPage,`/funds/api/records/${record.id}`,{amount:'999.00',version:record.version},'PUT');
    const conflict=admin.waitForResponse(response=>response.url().endsWith(`/records/${record.id}/review`));
    await admin.locator(`#tbody button[onclick^="reviewRecord(${record.id},"]`).click();
    assert.equal((await conflict).status(),409);
    await admin.locator('#funds-feedback').filter({hasText:'记录已变更'}).waitFor();

    // Actual DOM selections survive refresh, and removed selections block submission.
    await admin.goto(base+'/tasks');await admin.locator('#new-task-toggle').click();
    await admin.waitForFunction(()=>document.querySelector('#create-owner').options.length>1);
    const project=(await api(admin,'/api/projects')).body.projects.find(item=>item.is_active);
    const dependency=(await api(admin,'/api/task',{title:'Browser predecessor'})).body;
    await admin.evaluate(()=>refresh());
    await admin.locator('#create-form [name=title]').fill('Browser draft task');
    await admin.locator('#create-owner').selectOption(String(member.id));
    await admin.locator('#create-project').selectOption(String(project.id));
    await admin.locator(`#create-participants input[value="${member.id}"]`).check();
    await admin.locator(`#create-dependencies input[value="${dependency.id}"]`).check();
    await admin.locator('#create-parent').selectOption(String(dependency.id));
    await api(memberPage,'/api/task/'+dependency.id,{title:'Browser predecessor updated',version:dependency.version});
    await admin.evaluate(()=>refresh());
    assert.equal(await admin.locator('#create-owner').inputValue(),String(member.id));
    assert.equal(await admin.locator('#create-project').inputValue(),String(project.id));
    assert.equal(await admin.locator('#create-parent').inputValue(),String(dependency.id));
    assert.equal(await admin.locator(`#create-participants input[value="${member.id}"]`).isChecked(),true);
    assert.equal(await admin.locator(`#create-dependencies input[value="${dependency.id}"]`).isChecked(),true);
    await api(admin,'/api/member/'+member.id+'/toggle',{});
    await admin.evaluate(()=>refresh());
    assert.equal(await admin.locator('#create-form').evaluate(form=>form.checkValidity()),false);
    assert.match(await admin.locator('#create-owner').evaluate(control=>control.validationMessage),/已选项已失效/);
    // Admins are intentionally excluded from vehicle-task assignees.
    await admin.locator('#create-owner').selectOption('');
    await admin.locator(`#create-participants input[value="${member.id}"]`).uncheck();
    assert.equal(await admin.locator('#create-form').evaluate(form=>form.checkValidity()),true);
    await admin.goto(base+'/members');
    await admin.waitForFunction(()=>document.querySelector('#member-create-groups input'));
    const group=(await api(admin,'/api/groups')).body.groups.find(item=>item.is_active);
    await admin.locator(`#member-create-groups input[value="${group.id}"]`).check();
    await admin.locator(`#member-create-projects input[value="${project.id}"]`).check();
    await admin.locator(`#member-import-projects input[value="${project.id}"]`).check();
    await api(admin,'/api/task',{title:'Browser unrelated update'});
    await admin.evaluate(()=>refresh());
    for(const selector of [`#member-create-groups input[value="${group.id}"]`,
      `#member-create-projects input[value="${project.id}"]`,`#member-import-projects input[value="${project.id}"]`])
      assert.equal(await admin.locator(selector).isChecked(),true);
    await api(admin,`/api/groups/${group.id}/delete`,{confirm_name:group.name});
    await admin.evaluate(()=>refresh());
    assert.equal(await admin.locator('#member-form').evaluate(form=>form.checkValidity()),false);
    await admin.locator(`#member-create-groups input[value="${group.id}"]`).uncheck();
    await admin.locator('#member-form [name=name]').fill('New member');
    assert.equal(await admin.locator('#member-form').evaluate(form=>form.checkValidity()),true);
    assert.deepEqual(errors,[],'no browser JavaScript errors');
    console.log('BROWSER_REVIEW_FIXES_OK: login/first-password-change, upload/confirmation, XSS, preview/ownership, stale review, task/member draft preservation and invalidation');
  } catch(error) {
    console.error(error); console.error(serverLog.slice(-2500)); process.exitCode=1;
  } finally {
    if(browser)await browser.close();
    server.kill();
  }
})();
