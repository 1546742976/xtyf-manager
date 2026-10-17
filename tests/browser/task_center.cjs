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
    await page.locator('[name=username]').fill('admin');await page.locator('[name=password]').fill('123456');await page.locator('#login-form [type=submit]').click();await page.waitForURL('**/account');
    await page.locator('#account-password-form [name=old]').fill('123456');await page.locator('#account-password-form [name=new]').fill('task-center-password');await page.locator('#account-password-form [name=confirm]').fill('task-center-password');await page.locator('#account-password-form [type=submit]').click();await page.waitForURL(base+'/');
    const owner=(await api(page,'/api/member',{name:'机械负责人',username:'task_center_owner',password:'owner-password'})).body;
    await api(page,'/api/member',{name:'视觉负责人',username:'task_center_vision',password:'owner-password'});
    await page.goto(base+'/tasks#create');await ready(page);
    assert.equal(await page.locator('#create-form [name=title]').evaluate(node=>node===document.activeElement),true);
    await page.locator('#create-form [name=title]').fill('保持草稿');await page.keyboard.press('Escape');
    assert.equal(await page.locator('#new-task-toggle').evaluate(node=>node===document.activeElement),true);
    await page.locator('#new-task-toggle').click();await ready(page);assert.equal(await page.locator('#create-form [name=title]').inputValue(),'保持草稿');
    await page.locator('#task-create-type').selectOption('daily');await ready(page);
    await page.locator('#daily-form [name=title]').fill('保留日常草稿');
    await page.locator('#daily-form [name=detail]').fill('保留分组与说明');
    await page.locator('[name=enabled][value=mechanical]').check();await page.locator(`[name=owners-mechanical][value="${owner.id}"]`).check();
    assert.equal(await page.locator('[name=owners-mechanical][value="1"]').count(),1);
    const row=page.locator(`[name=owners-mechanical][value="${owner.id}"]`).locator('..');assert.ok((await row.boundingBox()).height>=44);
    assert.equal(await row.getAttribute('class'),'daily-owner-choice is-selected');
    await page.locator('[data-owner-search=mechanical]').fill('不存在');assert.equal(await row.isVisible(),false);
    await page.locator('[data-owner-search=mechanical]').fill('机械');assert.equal(await row.isVisible(),true);
    await page.locator('#task-create-type').selectOption('vehicle');await ready(page);assert.equal(await page.locator('#create-form [name=title]').inputValue(),'保持草稿');
    await page.locator('#task-create-type').selectOption('daily');await ready(page);assert.equal(await page.locator('#daily-form [name=title]').inputValue(),'保留日常草稿');assert.equal(await page.locator(`[name=owners-mechanical][value="${owner.id}"]`).isChecked(),true);
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
    for(const stage of ['mechanical','vision']){await page.locator(`[name=enabled][value=${stage}]`).check();await page.locator(`[name=owners-${stage}][value="${owner.id}"]`).check();}
    await page.locator('#daily-form [name=status]').selectOption('阻塞');
    let pending,posts=0;await page.route('**/api/daily-projects',route=>{posts++;pending=route;});
    await page.locator('#daily-form [type=submit]').click();await page.waitForFunction(()=>document.querySelector('#daily-form [type=submit]').disabled);
    await page.keyboard.press('Escape');assert.equal(await page.locator('#task-create-panel').evaluate(node=>node.open),true);assert.equal(await page.locator('#task-create-type').isDisabled(),true);
    await page.locator('#daily-form').evaluate(form=>form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));assert.equal(posts,1);
    await pending.fulfill({status:500,json:{ok:false,error:'模拟项目保存失败'}});await page.locator('#daily-form .form-message').filter({hasText:'当前输入已保留'}).waitFor();
    assert.equal(await page.locator('#daily-form [name=title]').inputValue(),'统一入口日常目标');assert.equal(await page.locator('#daily-form [type=submit]').isEnabled(),true);await page.unroute('**/api/daily-projects');
    await page.locator('#daily-form [type=submit]').click();await page.waitForURL(/\/tasks\?type=daily&project=\d+$/);const id=new URL(page.url()).searchParams.get('project');
    await page.locator('[data-progress-form=mechanical]').waitFor();assert.equal(await page.locator('[data-progress-form]').count(),2);
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
    // Assert the actual default card and grouped member dialog in a mixed dataset.
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
      const active=Array.from(document.querySelectorAll('#modal [data-task-group="active"] .task-head h3'),n=>n.textContent);
      const archived=Array.from(document.querySelectorAll('#modal [data-task-group="finished"] .task-head h3'),n=>n.textContent);
      const boardCards=lane.querySelectorAll('[data-task-id]').length;
      hideModal();
      return {initial,active,archived,boardCards,firstScore};
    });
    assert.deepEqual(urgencyUI.initial,['明天紧急参与']);
    assert.deepEqual(urgencyUI.active,['明天紧急参与','无截止主责','一般总负责']);
    assert.deepEqual(urgencyUI.archived,['历史已完成']);
    assert.equal(urgencyUI.boardCards,1);
    assert.equal(urgencyUI.firstScore,68);
    checks.push('mixed member lane keeps one card; all filtered work appears in active and finished dialog groups');

    const laneFocus=await page.evaluate(()=>{
      const [first,second]=state.members.filter(member=>member.role!=='admin');
      const shared={id:902001,title:'共同紧急任务',owner_id:first.id,participant_ids:[second.id],status:'阻塞',priority:'高',days_left:0};
      const own=id=>({id,title:'成员独立任务 '+id,owner_id:second.id,status:'未开始',priority:'低',days_left:null});
      state.filters={group:'',project:'',status:'',q:'',mine:false};state.view='person';
      state.tasks=[shared,own(902002),own(902003)];renderBoard();
      const lane=()=>document.getElementById('lane-m-'+second.id);
      const sharedCard=()=>lane().querySelector('[data-task-id="902001"]');
      sharedCard().focus();renderBoard();
      const sameMember=sharedCard()===document.activeElement;
      state.tasks=[own(902002),own(902003)];renderBoard();
      const remainingEntry=document.activeElement?.dataset.laneTasks==='m-'+second.id;
      state.tasks=[shared,own(902002)];renderBoard();sharedCard().focus();
      state.tasks=[own(902002)];renderBoard();
      const remainingCard=lane().querySelector('[data-task-id="902002"]')===document.activeElement;
      state.tasks=[shared];renderBoard();sharedCard().focus();state.tasks=[];renderBoard();
      const emptyFallback=document.activeElement===document.querySelector('#view-seg [data-view="person"]');
      return {sameMember,remainingEntry,remainingCard,emptyFallback};
    });
    assert.deepEqual(laneFocus,{sameMember:true,remainingEntry:true,remainingCard:true,emptyFallback:true});
    checks.push('background lane redraw retains shared-task focus within the same member and falls back to that member entry/card or person view');

    // Exercise list/detail navigation and writes against this isolated server's actual tasks.
    const listTasks=[];
    for(let index=0;index<9;index++) {
      const result=await api(page,'/api/task',{title:'成员弹窗任务 '+index,owner_id:owner.id,status:index>=7?'已完成':'进行中',priority:'中'});
      assert.equal(result.body.ok,true);listTasks.push(result.body.id);
    }
    await page.goto(base+'/tasks');
    await page.waitForFunction(id=>state.tasks.some(task=>task.id===id),listTasks[8]);
    await page.locator('#f-q').fill('成员弹窗');
    const listOpener=page.locator(`[data-lane-tasks="m-${owner.id}"]`);
    await listOpener.click();
    const list=page.locator('#modal.member-task-list-dialog');
    await list.waitFor();assert.equal(await list.locator('[data-task-id]').count(),9);
    assert.equal(await list.locator('[data-task-group="active"] [data-task-id]').count(),7);
    assert.equal(await list.locator('[data-task-group="finished"] [data-task-id]').count(),2);
    const lastCard=list.locator('[data-task-id]').last();
    await lastCard.focus();await page.keyboard.press('Tab');
    assert.equal(await list.locator('[data-close]').evaluate(node=>node===document.activeElement),true);
    await page.keyboard.press('Shift+Tab');
    assert.equal(await lastCard.evaluate(node=>node===document.activeElement),true);
    const lastId=Number(await lastCard.getAttribute('data-task-id'));
    await lastCard.scrollIntoViewIfNeeded();
    const listScroll=await list.locator('[data-member-task-scroll]').evaluate(node=>node.scrollTop);
    await lastCard.press('Enter');
    await page.locator('#task-form').waitFor();
    assert.equal(await page.locator('#modal-mask:not(.hidden)').count(),1);
    assert.equal(await page.locator('#modal [data-close]').innerText(),'返回任务列表');
    await page.keyboard.press('Escape');await list.waitFor();
    assert.equal(await list.locator(`[data-task-id="${lastId}"]`).evaluate(node=>node===document.activeElement),true);
    assert.ok(Math.abs(await list.locator('[data-member-task-scroll]').evaluate(node=>node.scrollTop)-listScroll)<2);
    await page.keyboard.press('Escape');await list.waitFor({state:'hidden'});
    assert.equal(await listOpener.evaluate(node=>node===document.activeElement),true);
    checks.push('member list traps Tab on task cards; Enter opens a single detail; Escape restores list scroll/card then current lane opener');

    await listOpener.click();
    await list.locator(`[data-task-id="${listTasks[0]}"]`).click();await page.locator('#task-form').waitFor();
    await page.locator('#modal-mask').click({position:{x:3,y:3}});await list.waitFor();
    await list.locator(`[data-task-id="${listTasks[0]}"]`).click();await page.locator('#task-form').waitFor();
    await page.locator('#modal [data-close]').click();await list.waitFor();
    await list.locator(`[data-task-id="${listTasks[0]}"]`).click();await page.locator('#task-form').waitFor();
    await page.locator('#task-form [name=title]').fill('成员弹窗任务 已更新');await page.locator('#task-form [type=submit]').click();
    await list.waitFor();await list.locator('.task-head h3').filter({hasText:'成员弹窗任务 已更新'}).waitFor();
    assert.equal(await page.locator('#f-q').inputValue(),'成员弹窗');
    await list.locator(`[data-task-id="${listTasks[0]}"]`).click();await page.locator('#progress-form').waitFor();
    await page.locator('#progress-form [name=progress]').fill('42');await page.locator('#progress-form [name=note]').fill('列表返回验证');
    await page.locator('#progress-form [type=submit]').click();await list.waitFor();
    assert.match(await list.locator(`[data-task-id="${listTasks[0]}"] .task-progress-heading`).innerText(),/42%/);
    await list.locator(`[data-task-id="${listTasks[1]}"]`).click();await page.locator('#task-form').waitFor();
    page.once('dialog',dialog=>dialog.accept());await page.locator('#modal [data-delete-task]').click();await list.waitFor();
    assert.equal(await list.locator(`[data-task-id="${listTasks[1]}"]`).count(),0);
    assert.equal(await list.locator('[data-task-id]').count(),8);
    checks.push('detail close/backdrop, settings save, progress save and deletion all return to refreshed member list without changing filter');

    const focusId=listTasks[8];
    await list.locator(`[data-task-id="${focusId}"]`).focus();
    const beforeRefreshScroll=await list.locator('[data-member-task-scroll]').evaluate(node=>node.scrollTop);
    const fresh=(await api(page,'/api/data')).body.tasks.find(task=>task.id===focusId);
    assert.equal((await api(page,'/api/task/'+focusId,{version:fresh.version,title:'成员弹窗任务 后台更新'})).body.ok,true);
    await page.evaluate(()=>refresh());
    await list.locator('.task-head h3').filter({hasText:'成员弹窗任务 后台更新'}).waitFor();
    assert.equal(await list.locator(`[data-task-id="${focusId}"]`).evaluate(node=>node===document.activeElement),true);
    assert.ok(Math.abs(await list.locator('[data-member-task-scroll]').evaluate(node=>node.scrollTop)-beforeRefreshScroll)<2);
    await list.locator('[data-close]').click();
    assert.equal(await listOpener.evaluate(node=>node===document.activeElement),true);
    await page.locator('#f-status').selectOption('已完成');await listOpener.click();
    assert.equal(await list.locator('[data-task-group="active"] [data-task-id]').count(),0);
    assert.equal(await list.locator('[data-task-group="finished"] [data-task-id]').count(),2);
    await list.locator('[data-close]').click();await page.locator('#f-status').selectOption('');
    checks.push('background refresh preserves list scroll/focus and restores rebuilt opener; completed filter stays scoped');

    for(const width of [375,390,760,761,767,768,1280,1864]) {
      await page.setViewportSize({width,height:820});await listOpener.click();
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true,'member list page overflow '+width);
      const rect=await list.boundingBox();assert.ok(rect.x>=0&&rect.x+rect.width<=width+1,'member list dialog fits '+width);
      assert.equal(await list.locator('[data-member-task-scroll]').evaluate(node=>node.scrollHeight>node.clientHeight),true);
      if([390,760,761,1280,1864].includes(width))await page.screenshot({path:path.join(output,'member-task-list-'+width+'.png')});
      await list.locator('[data-close]').click();
    }
    checks.push('member task list scrolls internally and fits 375/390/760/761/767/768/1280/1864 widths');

    for(const width of [375,390,767,768,850,1280,1606]) {
      await page.setViewportSize({width,height:900});await page.goto(base+'/');await page.locator('#daily-dashboard .daily-card').waitFor();
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true,'dashboard overflow '+width);
      if(width>=1280){const recent=await page.locator('[aria-label="近期任务"]').boundingBox(),daily=await page.locator('.ws-dashboard-daily').boundingBox(),tip=await page.locator('.ws-dashboard-aside > section').filter({hasText:'工作提示'}).boundingBox();assert.ok(daily.x>recent.x);assert.ok(daily.y>=tip.y+tip.height);}
    }
    checks.push('dashboard daily block below work hints on right at 1280/1606; no overflow at seven widths');

    // The homepage puts owned and participating tasks first, then sorts each group
    // by urgency with the same weights and a +4 team responsibility baseline.
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
    assert.deepEqual(homeRanking.ids,[8105,8106,8102,8101,8107,8104,8103]);
    assert.match(homeRanking.summary,/团队任务/);
    assert.equal(homeRanking.teamScore,64);
    assert.equal(homeRanking.participantScore,68);
    assert.equal(homeRanking.ownScore,56);
    checks.push('homepage puts personal tasks first, ranks each group by urgency (+4 team, +8 participant, +28 owner); excludes completed work');

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
