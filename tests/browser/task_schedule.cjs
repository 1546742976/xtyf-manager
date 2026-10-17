"use strict";
// Real loopback Flask + Chromium; never connects to the deployed site or database.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '../..');
const output = path.join(root, '.build-tools', 'schedule-browser-check');
fs.mkdirSync(output, {recursive:true});
async function api(page, url, data) {
  return page.evaluate(async ({url, data}) => {
    const response = await BusinessUI.fetch(url, data === undefined ? {} : {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
    return {status:response.status, body:await response.json()};
  }, {url, data});
}
async function post(page, url, data) {
  const result = await api(page, url, data);
  assert.equal(result.status, 200, JSON.stringify(result));
  assert.equal(result.body.ok, true, JSON.stringify(result));
  return result.body;
}
async function ready(page) {
  await page.locator('[data-schedule-root][aria-busy="false"] .schedule-task').first().waitFor();
}
(async () => {
  const server = spawn(process.env.REVIEW_PYTHON || 'python', ['-B', path.join(__dirname,'server.py')], {
    cwd:root, env:{...process.env, MAIL_MODE:'preview'}, stdio:['ignore','pipe','pipe']});
  let browser, buffer='', log='';
  const errors=[], checks=[];
  try {
    const base = await new Promise((resolve,reject) => {
      const timeout = setTimeout(()=>reject(new Error('Server timeout '+log)),30000);
      server.stderr.on('data', d=>log+=d);
      server.stdout.on('data', d=>{buffer+=d; const match=buffer.match(/BROWSER_URL=(\S+)/);
        if(match){clearTimeout(timeout);resolve(match[1]);}});
      server.once('exit', code=>{clearTimeout(timeout);reject(new Error('Server exit '+code+' '+log));});
    });
    browser = await chromium.launch({headless:true});
    const page = await browser.newPage({viewport:{width:1280,height:900}});
    page.setDefaultTimeout(15000);
    page.on('pageerror', error=>errors.push(error.message));
    await page.goto(base+'/login');
    await page.locator('[name=username]').fill('admin');
    await page.locator('[name=password]').fill('123456');
    await page.locator('#login-form [type=submit]').click(); await page.waitForURL('**/account');
    await page.locator('#account-password-form [name=old]').fill('123456');
    await page.locator('#account-password-form [name=new]').fill('schedule-browser-password');
    await page.locator('#account-password-form [name=confirm]').fill('schedule-browser-password');
    await page.locator('#account-password-form [type=submit]').click(); await page.waitForURL(base+'/');
    const owner = await post(page, '/api/member', {name:'排期负责人', username:'schedule_browser_owner', password:'owner-password'});
    const project = await post(page, '/api/projects', {name:'浏览器排期验收'});
    const task = (data) => post(page, '/api/task', {project_id:project.id, owner_id:owner.id, ...data});
    const fixed = await task({title:'固定前置任务', start_date:'2026-10-10', due_date:'2026-10-20', schedule_locked:true});
    const child = await task({title:'依赖任务', start_date:'2026-10-12', due_date:'2026-10-14', parent_id:fixed.id});
    const missing = await task({title:'待补工期任务', predecessor_ids:[child.id]});
    const running = await task({title:'进行中任务', status:'进行中', start_date:'2026-10-22', due_date:'2026-10-25'});
    await page.goto(base+'/manage');
    await page.locator(`#t-filter-project option[value="${project.id}"]`).waitFor({state:'attached'});
    await page.locator('#t-filter-project').selectOption(String(project.id));
    await page.route('**/schedule/preview', route=>route.fulfill({status:503,json:{ok:false,error:'模拟首次读取失败'}}));
    await page.locator('#schedule-project').click();
    await page.locator('#schedule-feedback').filter({hasText:'模拟首次读取失败'}).waitFor();
    await page.unroute('**/schedule/preview');
    await page.locator('#schedule-repreview').click(); await ready(page);
    checks.push('initial network failure retries with server default base date without requiring manual input');
    const selection = id=>page.locator(`[data-schedule-task="${id}"]`);
    assert.equal(await selection(fixed.id).isChecked(), false);
    assert.equal(await selection(running.id).isChecked(), false);
    assert.equal(await selection(child.id).isChecked(), true);
    assert.equal(await page.locator('#schedule-apply').isDisabled(), true);
    await page.locator('#schedule-base-date').fill('2026-10-16');
    await page.locator(`[data-schedule-duration="${missing.id}"]`).fill('3');
    await page.locator('#schedule-repreview').click(); await ready(page);
    assert.match(await page.locator(`#schedule-task-${child.id} .schedule-dates`).innerText(), /新：2026-10-18/);
    assert.match(await page.locator(`#schedule-task-${child.id} .schedule-dates`).innerText(), /新：2026-10-20/);
    assert.match(await page.locator(`#schedule-task-${missing.id} .schedule-dates`).innerText(), /新：2026-10-18/);
    assert.equal(await page.locator('#schedule-apply').isEnabled(), true);
    await page.locator('.schedule-warnings').filter({hasText:'同一负责人'}).waitFor();
    checks.push('default fixed/in-progress protection; all-dependency two-day overlap; inline inclusive duration; workload warning permits save');

    await selection(fixed.id).check();
    assert.equal(await page.locator('#schedule-apply').isDisabled(), true);
    await selection(fixed.id).uncheck();
    await page.route('**/schedule/preview', route=>route.fulfill({status:503,json:{ok:false,error:'模拟预览失败'}}));
    await page.locator('#schedule-repreview').click();
    await page.locator('#schedule-feedback').filter({hasText:'模拟预览失败'}).waitFor();
    assert.equal(await page.locator(`[data-schedule-duration="${missing.id}"]`).inputValue(), '3');
    assert.equal(await selection(fixed.id).isChecked(), false);
    await page.unroute('**/schedule/preview');
    await page.locator('#schedule-repreview').click(); await ready(page);
    // Change a signed input after preview to force an actual optimistic-conflict response.
    const before = (await api(page,'/api/data')).body.tasks;
    const fixedBefore = before.find(t=>t.id===fixed.id);
    await post(page, '/api/task/'+fixed.id, {version:fixedBefore.version, due_date:'2026-10-21'});
    await page.locator('#schedule-apply').click();
    await page.locator('#schedule-feedback').filter({hasText:'本次未保存任何调整'}).waitFor();
    assert.equal(await page.locator(`[data-schedule-duration="${missing.id}"]`).inputValue(), '3');
    assert.equal(await page.locator('#schedule-apply').isDisabled(), true);
    await page.locator('#schedule-repreview').click(); await ready(page);
    assert.match(await page.locator(`#schedule-task-${child.id} .schedule-dates`).innerText(), /新：2026-10-19/);
    checks.push('editing invalidates snapshot; failed preview and real 409 preserve draft; repreview uses updated predecessor');

    for (const width of [375,390,767,1280]) {
      await page.setViewportSize({width,height:820});
      assert.equal(await page.locator('#modal').evaluate(node=>node.scrollWidth<=node.clientWidth+1), true, 'modal overflow '+width);
      const button = await page.locator('#schedule-apply').boundingBox();
      assert.ok(button.y>=0 && button.y+button.height<=821, 'confirmation visible '+width);
      assert.ok(button.height>=44, 'touch target '+width);
      await page.locator('#schedule-apply').focus(); await page.keyboard.press('Tab');
      assert.equal(await page.locator('#modal').evaluate(node=>node.contains(document.activeElement)), true);
      await page.locator('.schedule-scroll').evaluate(node=>{node.scrollTop=0;});
      await page.screenshot({path:path.join(output, 'schedule-'+width+'.png')});
    }
    await page.evaluate(()=>applyWorkspaceTheme({mode:'dark', light_theme:'silver', dark_theme:'space'},true));
    await page.screenshot({path:path.join(output,'schedule-dark.png')});
    checks.push('375/390/767/1280 layout has no horizontal overflow, persistent reachable confirmation, 44px touch target and keyboard focus trap; dark screenshot');

    let pending, posts=0;
    const routeReady = new Promise(resolve=>page.route('**/schedule/apply', route=>{posts++;pending=route;resolve();}));
    await page.locator('#schedule-apply').click(); await routeReady;
    await page.keyboard.press('Escape');
    await page.locator('#modal-mask').dispatchEvent('click');
    await page.evaluate(id=>openSchedulePreview(id), project.id);
    await page.evaluate(()=>showModal('<h2>不应替换</h2>'));
    assert.equal(await page.locator('[data-schedule-root]').count(), 1);
    assert.equal(await page.locator('[data-schedule-close]').isDisabled(), true);
    await page.locator('#schedule-apply').dispatchEvent('click');
    assert.equal(posts, 1);
    await pending.continue();
    await page.locator('#schedule-feedback').filter({hasText:'排期已保存，共更新 2 项任务'}).waitFor();
    await page.unroute('**/schedule/apply');
    const after = (await api(page,'/api/data')).body.tasks;
    assert.equal(after.find(t=>t.id===child.id).start_date, '2026-10-19');
    assert.equal(after.find(t=>t.id===missing.id).due_date, '2026-10-21');
    assert.equal(after.find(t=>t.id===fixed.id).schedule_locked, true);
    assert.equal(after.find(t=>t.id===running.id).start_date, '2026-10-22');
    await page.locator('[data-schedule-close]').click();
    await page.evaluate(id=>openSchedulePreview(id), project.id); await ready(page);
    assert.equal(await page.locator('#schedule-apply').isDisabled(), true);
    assert.doesNotMatch(await page.locator('#schedule-feedback').innerText(), /排期已保存/);
    checks.push('one real atomic apply; pending save blocks Escape/mask/replacement/duplicate; reopened session never inherits success');
    await page.locator('[data-schedule-close]').click();
    await page.locator(`[data-edit-task="${fixed.id}"]`).click();
    assert.equal(await page.locator('#task-form [name=schedule_locked]').isChecked(), true);
    await page.locator('#task-form [name=schedule_locked]').uncheck();
    await page.locator('#task-form [type=submit]').click();
    await page.locator('#task-form').waitFor({state:'hidden'});
    assert.equal((await api(page,'/api/data')).body.tasks.find(t=>t.id===fixed.id).schedule_locked, false);
    await page.goto(base+'/tasks#create');
    await page.locator('#task-create-panel[open][aria-busy="false"]').waitFor();
    await page.locator('#create-form [name=title]').fill('固定日期界面创建');
    await page.locator('#create-form [name=schedule_locked]').check();
    await page.locator('#create-form [type=submit]').click();
    await page.locator('#task-create-feedback').filter({hasText:'任务已创建'}).waitFor();
    assert.equal((await api(page,'/api/data')).body.tasks.find(t=>t.title==='固定日期界面创建').schedule_locked, true);
    checks.push('persistent date lock checkbox round-trips through real create and edit forms');
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(output,'result.json'), JSON.stringify({ok:true, checks, errors},null,2));
    console.log(JSON.stringify({ok:true,checks,output},null,2));
  } catch(error) {
    fs.writeFileSync(path.join(output,'server.log'), log);
    if(browser) await browser.contexts()[0]?.pages()[0]?.screenshot({path:path.join(output,'failure.png')}).catch(()=>{});
    throw error;
  } finally {
    if(browser) await browser.close();
    server.kill();
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
