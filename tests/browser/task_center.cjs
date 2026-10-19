"use strict";
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {chromium, webkit, devices} = require('playwright');
const root = path.resolve(__dirname, '../..');
const output = path.join(root, '.venv', 'task-center-check');
fs.mkdirSync(output, {recursive:true});
async function api(page, url, data) {
  return page.evaluate(async ({url, data}) => {
    const response = await fetch(url, data === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
    return {status:response.status, body:await response.json()};
  }, {url, data});
}
async function ready(page) { await page.locator('#task-create-panel[open][aria-busy="false"]').waitFor(); }
(async () => {
  const server = spawn(process.env.REVIEW_PYTHON || 'python', ['-B', path.join(__dirname,'server.py')], {cwd:root, env:{...process.env,MAIL_MODE:'preview'},stdio:['ignore','pipe','pipe']});
  let browser, phoneBrowser, buffer='', log=''; const errors=[], checks=[];
  try {
    const base = await new Promise((resolve,reject) => {
      const timeout=setTimeout(()=>reject(new Error('Server timeout '+log)),30000);
      server.stderr.on('data',d=>log+=d);
      server.stdout.on('data',d=>{buffer+=d;const match=buffer.match(/BROWSER_URL=(\S+)/);if(match){clearTimeout(timeout);resolve(match[1]);}});
      server.once('exit',code=>{clearTimeout(timeout);reject(new Error('Server exit '+code+' '+log));});
    });
    browser=await chromium.launch({headless:true});
    const page=await browser.newPage({viewport:{width:1280,height:900}});page.setDefaultTimeout(15000);
    page.on('pageerror',error=>errors.push(error.message));
    await page.goto(base+'/login');
    await page.locator('[name=username]').fill('admin');await page.locator('[name=password]').fill('browser-test-initial');await page.locator('#login-form [type=submit]').click();await page.waitForURL('**/account');
    await page.locator('#account-password-form [name=old]').fill('browser-test-initial');await page.locator('#account-password-form [name=new]').fill('task-center-password');await page.locator('#account-password-form [name=confirm]').fill('task-center-password');await page.locator('#account-password-form [type=submit]').click();await page.waitForURL(base+'/');
    const owner=(await api(page,'/api/member',{name:'机械负责人',username:'task_center_owner',password:'owner-password'})).body;
    await api(page,'/api/member',{name:'视觉负责人',username:'task_center_vision',password:'owner-password'});
    const groups=(await api(page,'/api/data')).body.groups;
    const mechanicalGroup=groups.find(g=>g.name==='机械'&&g.is_active!==0);
    const visionGroup=groups.find(g=>g.name==='视觉'&&g.is_active!==0);
    assert.ok(mechanicalGroup&&visionGroup,'specialty groups must be present');
    const mechanical='group-'+mechanicalGroup.id,vision='group-'+visionGroup.id;
    await page.goto(base+'/tasks#create');await ready(page);
    assert.equal(await page.locator('#create-form [name=title]').evaluate(node=>node===document.activeElement),true);
    await page.locator('#create-form [name=title]').fill('保持草稿');await page.keyboard.press('Escape');
    assert.equal(await page.locator('#new-task-toggle').evaluate(node=>node===document.activeElement),true);
    await page.locator('#new-task-toggle').click();await ready(page);assert.equal(await page.locator('#create-form [name=title]').inputValue(),'保持草稿');
    await page.locator('#task-create-type').selectOption('daily');await ready(page);
    await page.locator('#daily-form [name=title]').fill('保留日常草稿');
    await page.locator('#daily-form [name=detail]').fill('保留分组与说明');
    await page.locator(`[name=enabled][value=${mechanical}]`).check();await page.locator(`[name=owners-${mechanical}][value="${owner.id}"]`).check();
    assert.equal(await page.locator(`[name=owners-${mechanical}][value="1"]`).count(),1);
    const row=page.locator(`[name=owners-${mechanical}][value="${owner.id}"]`).locator('..');assert.ok((await row.boundingBox()).height>=44);
    assert.equal(await row.getAttribute('class'),'daily-owner-choice is-selected');
    await page.locator(`[data-owner-search=${mechanical}]`).fill('不存在');assert.equal(await row.isVisible(),false);
    await page.locator(`[data-owner-search=${mechanical}]`).fill('机械');assert.equal(await row.isVisible(),true);
    await page.locator('#task-create-type').selectOption('vehicle');await ready(page);assert.equal(await page.locator('#create-form [name=title]').inputValue(),'保持草稿');
    await page.locator('#task-create-type').selectOption('daily');await ready(page);assert.equal(await page.locator('#daily-form [name=title]').inputValue(),'保留日常草稿');assert.equal(await page.locator(`[name=owners-${mechanical}][value="${owner.id}"]`).isChecked(),true);
    await page.locator('#task-create-type').selectOption('vehicle');await ready(page);
    assert.equal(await page.locator('#create-owner option[value="1"]').count(),1);
    assert.equal(await page.locator('#create-participants input[value="1"]').count(),1);
    await page.locator(`[name=participant_ids][value="${owner.id}"]`).check();await page.locator('#create-form [type=submit]').click();
    assert.equal(await page.locator('#create-owner').evaluate(node=>node.validity.valid),false);
    await page.locator(`[name=participant_ids][value="${owner.id}"]`).uncheck();assert.equal(await page.locator('#create-owner').evaluate(node=>node.validity.valid),true);
    await page.locator('#create-owner').selectOption(String(owner.id));await page.locator(`[name=participant_ids][value="${owner.id}"]`).check();
    await page.locator('#create-form [type=submit]').focus();await page.keyboard.press('Tab');
    assert.equal(await page.locator('#task-create-panel').evaluate(node=>node.contains(document.activeElement)),true);
    await page.route('**/api/task',route=>route.fulfill({status:500,json:{ok:false,error:'模拟保存失败'}}));
    await page.locator('#create-form [type=submit]').click();await page.locator('#create-form .form-message').filter({hasText:'模拟保存失败'}).waitFor();
    assert.equal(await page.locator('#create-form [name=title]').inputValue(),'保持草稿');assert.equal(await page.locator('#task-create-panel').evaluate(node=>node.open),true);
    await page.unroute('**/api/task');
    await page.locator('#create-form [type=submit]').click();await page.locator('#task-create-feedback').filter({hasText:'任务已创建'}).waitFor();
    assert.equal(await page.locator('#task-create-panel').evaluate(node=>node.open),false);
    await page.locator('#new-task-toggle').click();await ready(page);assert.equal(await page.locator('#create-form input[type=checkbox]:checked').count(),0);await page.keyboard.press('Escape');
    checks.push('shared type switching preserves independent drafts; admin assignable; owner search, selection and validation; clean new draft');
    checks.push('desktop create: deep link, focus, Escape, retained draft, failed save, successful save');
    const parentA=(await api(page,'/api/task',{title:'前置 A',due_date:'2026-12-10'})).body;
    const parentB=(await api(page,'/api/task',{title:'前置 B',due_date:'2026-12-20'})).body;
    await page.evaluate(()=>refresh());await page.locator('#new-task-toggle').click();await ready(page);
    await page.locator('#create-form [name=title]').fill('日期建议测试');
    await page.locator('#create-parent').selectOption(String(parentA.id));
    assert.equal(await page.locator('#create-form [name=start_date]').inputValue(),'2026-12-08');
    await page.locator('#create-form [name=start_date]').fill('2026-12-03');
    await page.locator('#create-parent').selectOption(String(parentB.id));
    assert.equal(await page.locator('#create-form [name=start_date]').inputValue(),'2026-12-03');
    await page.locator('[data-task-date-reset]').click();
    assert.equal(await page.locator('#create-form [name=start_date]').inputValue(),'2026-12-18');
    await page.locator('#create-form [type=submit]').click();await page.locator('#task-create-panel').waitFor({state:'hidden'});
    let tasks=(await api(page,'/api/data')).body.tasks;const child=tasks.find(task=>task.title==='日期建议测试'), parent=tasks.find(task=>task.id===parentB.id);
    assert.equal(child.start_date,'2026-12-18');
    assert.equal((await api(page,'/api/task/'+parent.id,{version:parent.version,due_date:'2026-12-25'})).body.ok,true);
    tasks=(await api(page,'/api/data')).body.tasks;assert.equal(tasks.find(task=>task.id===child.id).start_date,'2026-12-18');
    checks.push('main predecessor due minus two, manual preservation, explicit reset, saved date does not drift');
    for (const width of [375,390,767,768,850,1280]) {
      await page.setViewportSize({width,height:820});await page.goto(base+'/tasks#create');await ready(page);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true,'body overflow '+width);
      const bounds=await page.locator('#task-create-panel').boundingBox();assert.ok(bounds.width<=width+1);
      await page.locator('#create-form [name=detail]').fill('长文本'.repeat(60));
      const submit=page.locator('#create-form [type=submit]');await submit.scrollIntoViewIfNeeded();const button=await submit.boundingBox();assert.ok(button.y+button.height<=820+1,'footer reachable '+width);
      assert.ok(button.height>=44);
      await page.screenshot({path:path.join(output,'create-'+width+'.png')});
      await page.locator('#task-create-type').selectOption('daily');await ready(page);
      const dailySubmit=page.locator('#daily-form [type=submit]');await dailySubmit.scrollIntoViewIfNeeded();const dailyBounds=await dailySubmit.boundingBox();assert.ok(dailyBounds.y+dailyBounds.height<=821);
      assert.equal(await page.locator('#daily-form').evaluate(node=>node.scrollWidth<=node.clientWidth+1),true,'daily form overflow '+width);
      const textarea=await page.locator('#daily-form [name=detail]').evaluate(node=>({resize:getComputedStyle(node).resize,width:node.getBoundingClientRect().width,parent:node.parentElement.getBoundingClientRect().width}));assert.equal(textarea.resize,'none');assert.ok(Math.abs(textarea.width-textarea.parent)<2);
      await page.screenshot({path:path.join(output,'daily-create-'+width+'.png')});
      await page.locator('.daily-owner-groups').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(output,'daily-owners-'+width+'.png')});await page.keyboard.press('Escape');
    }
    checks.push('375/390/767/768/850/1280 create dialog: no overflow and submit reachable');
    await page.goto(base+'/tasks?type=daily');assert.equal(await page.locator('#board').count(),0);await page.locator('#new-task-toggle').click();await ready(page);assert.equal(await page.locator('#task-create-type').inputValue(),'daily');await page.locator('#daily-form [name=title]').fill('统一入口日常目标');
    for(const stage of [mechanical,vision]){await page.locator(`[name=enabled][value=${stage}]`).check();await page.locator(`[name=owners-${stage}][value="${owner.id}"]`).check();}
    await page.locator('#daily-form [name=status]').selectOption('阻塞');
    await page.locator('#daily-form [name=blocker_reason]').fill('联调设备未到位');
    await page.locator('#daily-form [name=blocker_next_action]').fill('联系成员领取设备');
    let pending,posts=0;await page.route('**/api/daily-projects',route=>{posts++;pending=route;});
    await page.locator('#daily-form [type=submit]').click();await page.waitForFunction(()=>document.querySelector('#daily-form [type=submit]').disabled);
    await page.keyboard.press('Escape');assert.equal(await page.locator('#task-create-panel').evaluate(node=>node.open),true);assert.equal(await page.locator('#task-create-type').isDisabled(),true);
    await page.locator('#daily-form').evaluate(form=>form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));assert.equal(posts,1);
    await pending.fulfill({status:500,json:{ok:false,error:'模拟项目保存失败'}});await page.locator('#daily-form .form-message').filter({hasText:'当前输入已保留'}).waitFor();
    assert.equal(await page.locator('#daily-form [name=title]').inputValue(),'统一入口日常目标');assert.equal(await page.locator('#daily-form [type=submit]').isEnabled(),true);await page.unroute('**/api/daily-projects');
    await page.locator('#daily-form [type=submit]').click();await page.waitForURL(/\/tasks\?type=daily&project=\d+$/);const id=new URL(page.url()).searchParams.get('project');
    await page.locator(`[data-progress-form=${mechanical}]`).waitFor();assert.equal(await page.locator('[data-progress-form]').count(),2);
    await page.route('**/api/data',route=>route.fulfill({status:503,json:{ok:false,error:'模拟候选加载失败'}}));
    await page.locator('#daily-edit').click();await ready(page);await page.locator('#task-create-load').filter({hasText:'模拟候选加载失败'}).waitFor();assert.equal(await page.locator('#task-create-type').isDisabled(),true);
    await page.unroute('**/api/data');await page.locator('#task-create-load button').click();await ready(page);
    assert.equal(await page.locator('#task-create-type').isDisabled(),true);assert.equal(await page.locator('#task-create-type').inputValue(),'daily');
    await page.locator('#daily-form [name=title]').fill('编辑草稿保留');await page.keyboard.press('Escape');await page.locator('#daily-edit').click();await ready(page);assert.equal(await page.locator('#daily-form [name=title]').inputValue(),'编辑草稿保留');await page.keyboard.press('Escape');
    checks.push('daily create defaults type, failure keeps input, duplicate save blocked, editing locks type and keeps draft');
    await page.locator('#new-task-toggle').click();await ready(page);await page.locator('#daily-form [name=title]').fill('新项目独立草稿');await page.keyboard.press('Escape');
    await page.locator('#daily-edit').click();await ready(page);assert.equal(await page.locator('#daily-form [name=title]').inputValue(),'编辑草稿保留');await page.keyboard.press('Escape');
    await page.locator('#new-task-toggle').click();await ready(page);assert.equal(await page.locator('#daily-form [name=title]').inputValue(),'新项目独立草稿');
    await page.locator('#task-create-type').selectOption('vehicle');await ready(page);await page.locator('#create-form [name=title]').fill('日常页发起造车任务');await page.locator('#create-form [type=submit]').click();await page.waitForURL(/\/tasks\?type=vehicle&task=\d+$/);await page.locator('#modal').filter({hasText:'日常页发起造车任务'}).waitFor();
    checks.push('daily edit catalog failure has retry; new and edit drafts isolated; daily page can create vehicle without app.js');
    await page.goto(base+'/tasks?type=daily');
    let candidate,releaseCandidate;const candidateReady=new Promise(resolve=>releaseCandidate=resolve);await page.route('**/api/data',route=>{candidate=route;releaseCandidate();});await page.locator('#new-task-toggle').click();await page.waitForFunction(()=>document.querySelector('#task-create-panel').getAttribute('aria-busy')==='true');
    await candidateReady;await page.locator('.task-create-heading [data-task-create-close]').click();assert.equal(await page.locator('#task-create-panel').evaluate(node=>node.open),false);await candidate.fulfill({status:503,json:{ok:false,error:'模拟迟到的失败'}});await page.unroute('**/api/data');
    await page.locator('#new-task-toggle').click();await ready(page);await page.keyboard.press('Escape');checks.push('candidate loading can be closed; late failure does not reopen dialog');
    await page.goto(base+'/daily-projects/'+id);await page.waitForURL(base+'/tasks?type=daily&project='+id);
    await page.locator('.task-type-tabs a').filter({hasText:'造车任务'}).click();await page.locator('#board').waitFor();assert.equal(await page.locator('#daily-app').count(),0);
    checks.push('daily tab retains grouped ownership; legacy URL redirects; switching restores vehicle board');
    // Assert the actual default card, disclosure and archive behavior in a mixed dataset.
    await page.waitForFunction(() => state.members.some(member => member.role !== 'admin'));
    const urgencyUI=await page.evaluate(() => {
      const memberId=Number(state.members.find(m=>m.role!=='admin').id);
      state.filters={group:'',project:'',status:'',q:'',mine:false};
      state.view='person';
      state.tasks=[
        {id:901001,title:'无截止主责',owner_id:memberId,status:'阻塞',priority:'高',days_left:null},
        {id:901002,title:'明天紧急参与',owner_id:901200,participant_ids:[memberId],status:'阻塞',priority:'高',days_left:1},
        {id:901003,title:'一般总负责',owner_id:memberId,status:'进行中',priority:'低',days_left:7},
        {id:901004,title:'历史已完成',owner_id:memberId,status:'已完成',priority:'高',days_left:-100},
      ];
      renderBoard();
      const lane=document.querySelector('#lane-m-'+memberId).closest('.person-lane');
      const initial=Array.from(lane.querySelectorAll('#lane-m-'+memberId+' .task-head h3'),n=>n.textContent);
      const firstScore=taskUrgencyScore(state.tasks[1],memberId);
      lane.querySelector('[data-lane-tasks]').click();
      const list=document.querySelector('#modal [data-member-task-list]');
      const expanded=Array.from(list.querySelectorAll('[data-task-group="active"] .task-head h3'),n=>n.textContent);
      const archived=Array.from(list.querySelectorAll('[data-task-group="finished"] .task-head h3'),n=>n.textContent);
      const hasSeparateGroups=list.querySelectorAll('[data-task-group]').length===2;
      return {initial,expanded,archived,hasSeparateGroups,firstScore};
    });
    assert.deepEqual(urgencyUI.initial,['明天紧急参与']);
    assert.deepEqual(urgencyUI.expanded,['明天紧急参与','无截止主责','一般总负责']);
    assert.deepEqual(urgencyUI.archived,['历史已完成']);
    assert.equal(urgencyUI.hasSeparateGroups,true);
    assert.equal(urgencyUI.firstScore,68);
    checks.push('mixed member default top, full-list modal urgency order, and separate finished section in DOM');

    await page.locator('#modal [data-close]').click();
    for(const width of [375,390,767,768,850,1280,1606]) {
      await page.setViewportSize({width,height:900});await page.goto(base+'/');await page.locator('#daily-dashboard .daily-card').waitFor();
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true,'dashboard overflow '+width);
      if(width>=1280){const recent=await page.locator('[aria-label="近期任务"]').boundingBox(),daily=await page.locator('.ws-dashboard-daily').boundingBox(),tip=await page.locator('.ws-dashboard-aside > section').filter({hasText:'工作提示'}).boundingBox();assert.ok(daily.x>recent.x);assert.ok(daily.y>=tip.y+tip.height);}
    }
    checks.push('dashboard daily block below work hints on right at 1280/1606; no overflow at seven widths');

    // The homepage must sort team-wide work (not just "my tasks" first), using the
    // same deadline/status/priority weights but a +4 team responsibility baseline.
    const homeRanking = await page.evaluate(() => {
      const self = Number(state.me.id);
      state.tasks = [
        {id:8101,title:'我的无期限主责',owner_id:self,priority:'高',status:'阻塞',days_left:null},
        {id:8102,title:'我参与明天截止',owner_id:98765,participant_ids:[self],priority:'高',status:'阻塞',days_left:1},
        {id:8103,title:'团队明天截止',owner_id:98765,priority:'高',status:'阻塞',days_left:1},
        {id:8104,title:'团队今天截止',owner_id:98765,priority:'高',status:'阻塞',days_left:0},
        {id:8105,title:'我参与今天截止',owner_id:98765,participant_ids:[self],priority:'高',status:'阻塞',days_left:0},
        {id:8106,title:'我主责今天截止',owner_id:self,priority:'低',status:'进行中',days_left:0},
        {id:8107,title:'团队逾期高优先',owner_id:98765,priority:'高',status:'阻塞',days_left:-6},
        {id:8108,title:'已完成旧任务',owner_id:self,priority:'高',status:'已完成',days_left:-900},
      ];
      renderWorkspaceDashboard();
      return {
        ids: Array.from(document.querySelectorAll('#dashboard-task-list [data-dashboard-task]'),
          row => Number(row.dataset.dashboardTask)),
        summary: document.querySelector('#dashboard-task-list [data-dashboard-task="8107"] .ws-task-row-sub')?.textContent,
        teamScore: taskUrgencyScore(state.tasks[2],self,DASHBOARD_TEAM_TASK_WEIGHT),
        participantScore: taskUrgencyScore(state.tasks[1],self,DASHBOARD_TEAM_TASK_WEIGHT),
        ownScore: taskUrgencyScore(state.tasks[0],self,DASHBOARD_TEAM_TASK_WEIGHT),
      };
    });
    assert.deepEqual(homeRanking.ids,[8107,8105,8106,8102,8104,8103,8101]);
    assert.match(homeRanking.summary,/团队任务/);
    assert.equal(homeRanking.teamScore,64);
    assert.equal(homeRanking.participantScore,68);
    assert.equal(homeRanking.ownScore,56);
    checks.push('homepage ranks team tasks +4 vs participant +8, owner +28; excludes completed work');

    if(process.env.REVIEW_WEBKIT_PATH) {
      phoneBrowser=await webkit.launch({headless:true,executablePath:process.env.REVIEW_WEBKIT_PATH});
      const context=await phoneBrowser.newContext({...devices['iPhone 13']});await context.addCookies(await page.context().cookies());const phone=await context.newPage();phone.on('pageerror',error=>errors.push(error.message));
      await phone.goto(base+'/tasks#create');await ready(phone);await phone.locator('#create-form [name=title]').fill('iPhone WebKit 新任务');await phone.locator('#create-form [type=submit]').click();await phone.locator('#task-create-feedback').filter({hasText:'任务已创建'}).waitFor();
      assert.equal(await phone.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
      await phone.goto(base+'/tasks?type=daily#create');await ready(phone);assert.equal(await phone.locator('#task-create-type').inputValue(),'daily');await phone.locator('#daily-form [name=title]').fill('WebKit 日常草稿');
      await phone.locator('#task-create-type').selectOption('vehicle');await ready(phone);await phone.locator('#task-create-type').selectOption('daily');await ready(phone);assert.equal(await phone.locator('#daily-form [name=title]').inputValue(),'WebKit 日常草稿');
      assert.equal(await phone.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);checks.push('iPhone WebKit simulated create, close and shared type switching');
    }
    assert.deepEqual(errors,[]);fs.writeFileSync(path.join(output,'result.json'),JSON.stringify({ok:true,checks,errors},null,2));console.log('TASK_CENTER_BROWSER_OK '+JSON.stringify(checks));
  } catch(error) { console.error(error);console.error(errors);console.error(log.slice(-2000));process.exitCode=1; }
  finally {if(phoneBrowser)await phoneBrowser.close();if(browser)await browser.close();server.kill();}
})();
