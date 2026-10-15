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
  let browser, log='', failGithub=false;
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
    await context.route('**/discussions/api/**', async route=>{
      const url=new URL(route.request().url());
      const kind=url.searchParams.get('kind') || url.pathname.split('/').at(-2);
      const item={number:1,title:'模拟主题 <script>',body:'只读正文 <script>unsafe</script>',author:'测试作者',url:'https://github.com/team/repo/issues/1',updated_at:'2026-10-15T00:00:00Z'};
      if(failGithub){await route.fulfill({status:503,json:{ok:false,error:'模拟 GitHub 暂不可用'}});return;}
      const payload=url.pathname.includes('/item/')
        ? {ok:true,item,comments_available:kind==='issues',comments:[{author:'回复人',body:'测试回复',created_at:item.updated_at}]}
        : {ok:true,source:kind==='discussions'?'atom':'api',items:Array.from({length:kind==='issues' && url.searchParams.get('page')==='1'?30:1},(_,i)=>({...item,number:i+1}))};
      await route.fulfill({status:200,json:payload});
    });
    const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
    await login(page,base,'admin','browser-test-initial');
    const groups=(await api(page,'/api/groups')).body.groups;
    const projects=(await api(page,'/api/projects')).body.projects;
    const member=(await api(page,'/api/member',{name:'合并验收项管',username:'merge-manager',password:'merge-test-password',role:'manager',group_ids:[groups.find(g=>g.name==='机械').id]})).body;
    const date=offset=>{
      const day=new Date();day.setDate(day.getDate()+offset);
      return [day.getFullYear(),String(day.getMonth()+1).padStart(2,'0'),String(day.getDate()).padStart(2,'0')].join('-');
    };
    const project=projects.find(p=>p.is_active).id;
    const blocked=(await api(page,'/api/task',{title:'阻塞且今天截止',owner_id:member.id,project_id:project,status:'阻塞',start_date:date(-2),due_date:date(0)})).body;
    const overdue=(await api(page,'/api/task',{title:'已逾期任务',owner_id:member.id,status:'进行中',due_date:date(-1)})).body;
    assert.equal(blocked.ok,true);assert.equal(overdue.ok,true);
    for(const theme of ['dark','light']) {
      await page.goto(base+'/account');
      const [saved]=await Promise.all([
        page.waitForResponse(r=>r.url().endsWith('/api/account/preferences') && r.request().method()==='POST'),
        page.locator(`input[name=workspace-theme][value=${theme}]`).check(),
      ]);
      assert.equal(saved.status(),200);
      await page.reload();assert.equal(await page.locator('html').getAttribute('data-theme'),theme);
      await page.goto(base+'/tasks');await page.locator(`.task[data-task-id="${blocked.id}"]`).waitFor();
      assert.match(await page.locator(`.task[data-task-id="${blocked.id}"]`).getAttribute('class'),/is-blocked.*is-due-soon/);
      assert.match(await page.locator(`.task[data-task-id="${overdue.id}"]`).getAttribute('class'),/overdue/);
      await page.locator('#f-project').selectOption(String(project));await page.locator('.timeline-row').waitFor();
      assert.equal(await page.locator('.timeline-row.is-blocked').count(),1);
      await page.screenshot({path:path.join(output,theme+'-timeline.png'),fullPage:true});
      await page.goto(base+'/discussions');await page.locator('.github-topic').first().waitFor();
      assert.equal(await page.locator('.github-topic').count(),30);
      await page.locator('#github-next').click();await page.waitForFunction(()=>document.querySelectorAll('.github-topic').length===1);
      assert.equal(await page.locator('#github-page').innerText(),'第 2 页');
      await page.locator('.github-topic').first().click();await page.locator('.github-thread').waitFor();
      assert.match(await page.locator('.github-body').innerText(),/<script>unsafe<\/script>/);
      assert.equal(await page.locator('#github-topic-detail script').count(),0);
      await page.locator('[data-github-kind=discussions]').click();await page.locator('#github-source-note').filter({hasText:'订阅源'}).waitFor();
      await page.locator('.github-topic').first().click();await page.locator('.github-thread').waitFor();
      assert.match(await page.locator('.github-replies').innerText(),/不提供回复内容/);
      await page.screenshot({path:path.join(output,theme+'-github.png'),fullPage:true});
      failGithub=true;await page.locator('[data-github-kind=issues]').click();await page.locator('#github-topic-list [role=alert]').waitFor();failGithub=false;
    }
    await page.goto(base+'/tasks');await page.locator(`.task[data-task-id="${blocked.id}"]`).click();
    await page.locator('#progress-form [name=progress]').fill('45');
    await page.locator('#progress-form [name=note]').fill('合并后无截图进度记录');
    await page.locator('#progress-form button[type=submit]').click();await page.locator('#modal-mask').waitFor({state:'hidden'});
    assert.equal((await api(page,`/api/task/${blocked.id}/progress`)).body.updates[0].progress,45);
    const managerContext=await browser.newContext(), manager=await managerContext.newPage();
    manager.on('pageerror',error=>errors.push(error.message));await login(manager,base,'merge-manager','merge-test-password');
    await manager.goto(base+'/manage');await manager.locator('#create-parent').waitFor();
    assert.equal(await manager.locator('.task-create-dependencies').count(),1);
    assert.equal(await manager.locator('.task-create-details').count(),1);
    const child=await api(manager,'/api/task',{title:'项管创建主前置任务',owner_id:member.id,parent_id:blocked.id});assert.equal(child.status,200);
    // A fresh browser session reads the saved preference, independent of browser storage.
    const freshContext=await browser.newContext(), fresh=await freshContext.newPage();
    await login(fresh,base,'admin','browser-test-initial-changed',false);
    assert.equal(await fresh.locator('html').getAttribute('data-theme'),'light');
    assert.deepEqual(errors,[]);
    fs.writeFileSync(path.join(output,'browser-result.json'),JSON.stringify({ok:true,errors,chromium:browser.version(),node:process.version},null,2));
    console.log('PR22_MERGE_BROWSER_OK: persisted themes, timeline, overlapping alerts, progress, manager dependency form and mocked GitHub listing/detail/pagination/errors');
  } catch(error) {console.error(error);console.error(log.slice(-1600));process.exitCode=1;}
  finally {if(browser)await browser.close();server.kill();}
})();
