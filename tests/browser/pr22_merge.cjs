"use strict";
// Isolated regression for the theme PR merged with the existing upstream features.
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '../..');
const output = path.join(root, '.venv', 'pr22-merge-check');
fs.mkdirSync(output, {recursive:true});
async function api(page, url, data) {
  return page.evaluate(async ({url,data}) => {
    const response = await fetch(url, data === undefined ? {} : {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
    return {status:response.status, body:await response.json()};
  }, {url,data});
}
async function login(page, base, username, password, firstLogin=true) {
  await page.goto(base+'/login');
  await page.locator('[name=username]').fill(username);
  await page.locator('[name=password]').fill(password);
  await page.locator('#login-form button[type=submit]').click();
  if (!firstLogin) { await page.waitForURL(base+'/'); return; }
  await page.waitForURL('**/account');
  await page.locator('#account-password-form [name=old]').fill(password);
  await page.locator('#account-password-form [name=new]').fill(password+'-changed');
  await page.locator('#account-password-form [name=confirm]').fill(password+'-changed');
  await page.locator('#account-password-form button').click();
  await page.waitForURL(base+'/');
}
(async () => {
  const server = spawn(process.env.REVIEW_PYTHON || 'python', ['-B',path.join(__dirname,'server.py')], {
    cwd:root, env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'}, stdio:['ignore','pipe','pipe']});
  let browser, log='';
  const errors=[];
  try {
    const base=await new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>reject(new Error('server timeout '+log)),30000);
      server.stderr.on('data',data=>log+=data);
      server.stdout.on('data',data=>{const match=String(data).match(/BROWSER_URL=(\S+)/);if(match){clearTimeout(timer);resolve(match[1]);}});
      server.once('exit',code=>{clearTimeout(timer);reject(new Error('server exited '+code+' '+log));});
    });
    browser=await chromium.launch({headless:true});
    const context=await browser.newContext({viewport:{width:1280,height:900},reducedMotion:'reduce'});
    const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
    await login(page,base,'admin','123456');
    const groups=(await api(page,'/api/groups')).body.groups;
    const projects=(await api(page,'/api/projects')).body.projects;
    const member=(await api(page,'/api/member',{name:'合并验收项管',username:'merge-manager',password:'merge-test-password',role:'manager',group_ids:[groups.find(g=>g.name==='机械').id]})).body;
    // Match the server's Beijing calendar even when the CI runner uses UTC.
    const calendar=await api(page,'/api/workspace/countdown');assert.equal(calendar.status,200);
    const today=calendar.body.countdown.today;
    const date=offset=>{
      const day=new Date(`${today}T12:00:00Z`);day.setUTCDate(day.getUTCDate()+offset);
      return day.toISOString().slice(0,10);
    };
    const project=projects.find(p=>p.is_active).id;
    const blocked=(await api(page,'/api/task',{title:'阻塞且今天截止',owner_id:member.id,project_id:project,status:'阻塞',blocker_reason:'等待联调',blocker_next_action:'负责人协调测试时间',start_date:date(-2),due_date:date(0)})).body;
    const overdue=(await api(page,'/api/task',{title:'已逾期任务',owner_id:member.id,status:'进行中',due_date:date(-1)})).body;
    assert.equal(blocked.ok,true);assert.equal(overdue.ok,true);
    const taskDates=(await api(page,'/api/data')).body.tasks;
    assert.equal(taskDates.find(task=>task.id===blocked.id).days_left,0,'今天截止夹具必须与服务端北京时间一致');
    assert.equal(taskDates.find(task=>task.id===overdue.id).days_left,-1,'昨天截止夹具必须已逾期');
    // The sidebar shortcut works away from account settings and rolls back failed saves.
    await page.goto(base+'/manage');
    const shortcut=page.locator('#workspace-sidebar [data-theme-toggle]');
    await page.route('**/api/account/preferences',route=>route.fulfill({status:503,json:{ok:false,error:'模拟保存失败'}}));
    await shortcut.click();
    await page.locator('#sidebar-theme-result').filter({hasText:'已恢复原主题'}).waitFor();
    assert.equal(await page.locator('html').getAttribute('data-theme'),'light');
    await page.unroute('**/api/account/preferences');
    await page.locator('#sidebar-theme-retry').click();
    await page.waitForFunction(()=>document.querySelector('#sidebar-theme-result').textContent.includes('外观已保存'));
    await page.reload();assert.equal(await page.locator('html').getAttribute('data-theme'),'dark');
    await page.screenshot({path:path.join(output,'sidebar-dark.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    await page.locator('[data-ws-menu]').click();
    await shortcut.scrollIntoViewIfNeeded();
    const toggleBox=await shortcut.boundingBox();assert.ok(toggleBox.width>=44 && toggleBox.height>=44);
    await shortcut.click();
    await page.waitForFunction(()=>document.querySelector('#sidebar-theme-result').textContent.includes('外观已保存'));
    assert.equal(await page.locator('html').getAttribute('data-theme'),'light');
    await page.screenshot({path:path.join(output,'sidebar-mobile-light.png'),fullPage:true});
    await page.setViewportSize({width:1280,height:900});
    for(const theme of ['dark','light']) {
      await page.goto(base+'/account');
      const [saved]=await Promise.all([
        page.waitForResponse(r=>r.url().endsWith('/api/account/preferences') && r.request().method()==='POST'),
        page.locator(`input[name=account-mode][value=${theme}]`).check(),
      ]);
      assert.equal(saved.status(),200);
      assert.equal(await shortcut.getAttribute('aria-label'),theme==='dark'?'切换为Mistral 暖橙':'切换为GitHub 暗黑');
      await page.reload();assert.equal(await page.locator('html').getAttribute('data-theme'),theme);
      await page.goto(base+'/tasks');await page.locator('[data-view="status"]').click();await page.locator(`.task[data-task-id="${blocked.id}"]`).waitFor();
      assert.match(await page.locator(`.task[data-task-id="${blocked.id}"]`).getAttribute('class'),/is-blocked.*is-due-soon/);
      assert.match(await page.locator(`.task[data-task-id="${overdue.id}"]`).getAttribute('class'),/overdue/);
      await page.locator('#f-project').selectOption(String(project));await page.locator('.timeline-row').waitFor();
      assert.equal(await page.locator('.timeline-row.is-blocked').count(),1);
      await page.screenshot({path:path.join(output,theme+'-timeline.png'),fullPage:true});
      await page.goto(base+'/discussions');await page.locator('#collab-new-topic').waitFor();
      assert.equal(await page.locator('#collab-topics').count(),1);
      assert.equal((await api(page,'/discussions/api/topics')).status,200);
      assert.equal(await page.locator('a[href*="github.com"]').count(),0);
      await page.screenshot({path:path.join(output,theme+'-collaboration.png'),fullPage:true});
    }
    await page.goto(base+'/tasks');await page.locator('[data-view="status"]').click();await page.locator(`.task[data-task-id="${blocked.id}"]`).click();
    await page.locator('#progress-form [name=progress]').fill('45');
    await page.locator('#progress-form [name=note]').fill('合并后无截图进度记录');
    await page.locator('#progress-form button[type=submit]').click();await page.locator('#modal-mask').waitFor({state:'hidden'});
    assert.equal((await api(page,`/api/task/${blocked.id}/progress`)).body.updates[0].progress,45);
    const managerContext=await browser.newContext(), manager=await managerContext.newPage();
    manager.on('pageerror',error=>errors.push(error.message));await login(manager,base,'merge-manager','merge-test-password');
    await manager.goto(base+'/tasks');await manager.locator('#new-task-toggle').click();await manager.locator('#create-parent').waitFor();
    assert.equal(await manager.locator('.task-create-dependencies').count(),1);
    assert.equal(await manager.locator('#create-form textarea[name="detail"]').isVisible(),true);
    const child=await api(manager,'/api/task',{title:'项管创建主前置任务',owner_id:member.id,parent_id:blocked.id});assert.equal(child.status,200);
    // A fresh browser session reads the saved preference, independent of browser storage.
    const freshContext=await browser.newContext(), fresh=await freshContext.newPage();
    await login(fresh,base,'admin','123456-changed',false);
    assert.equal(await fresh.locator('html').getAttribute('data-theme'),'light');
    assert.deepEqual(errors,[]);
    fs.writeFileSync(path.join(output,'browser-result.json'),JSON.stringify({ok:true,errors,chromium:browser.version(),node:process.version},null,2));
    console.log('PR22_MERGE_BROWSER_OK: persisted themes, timeline, overlapping alerts, progress, manager dependency form and local collaboration shell');
  } catch(error) {console.error(error);console.error(log.slice(-1600));process.exitCode=1;}
  finally {if(browser)await browser.close();server.kill();}
})();
