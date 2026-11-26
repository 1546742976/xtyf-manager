'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {spawn}=require('node:child_process');
const {chromium,webkit,devices}=require('playwright');
const root=path.resolve(__dirname,'../..'),output=path.join(root,'.build-tools/seven-repairs-20261018/browser');
fs.mkdirSync(output,{recursive:true});
async function api(page,url,data){return page.evaluate(async({url,data})=>{
  const response=await BusinessUI.fetch(url,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  return {status:response.status,body:await response.json()};
},{url,data});}
async function post(page,url,data){const result=await api(page,url,data);assert.equal(result.status,200,JSON.stringify(result));return result.body;}
async function login(page,base,first){
  await page.goto(base+'/login');await page.locator('[name=username]').fill('admin');await page.locator('[name=password]').fill(first?'browser-test-initial':'seven-repairs-password');await page.locator('#login-form [type=submit]').click();
  if(first){await page.waitForURL('**/account');await page.locator('#account-password-form [name=old]').fill('browser-test-initial');await page.locator('#account-password-form [name=new]').fill('seven-repairs-password');await page.locator('#account-password-form [name=confirm]').fill('seven-repairs-password');await page.locator('#account-password-form [type=submit]').click();}
  await page.waitForURL(base+'/');
}
(async()=>{
  const server=spawn(process.env.REVIEW_PYTHON||'python',['-B',path.join(__dirname,'server.py')],{cwd:root,env:{...process.env,MAIL_MODE:'off'},stdio:['ignore','pipe','pipe']});
  const checks=[],errors=[];let stream='',log='',browser;
  try{
    const base=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error('Server timeout '+log)),30000);server.stderr.on('data',d=>log+=d);server.stdout.on('data',d=>{stream+=d;const m=stream.match(/BROWSER_URL=(\S+)/);if(m){clearTimeout(timer);resolve(m[1]);}});server.once('exit',code=>{clearTimeout(timer);reject(new Error('Server exit '+code+' '+log));});});
    for(const [engine,options,name] of [[chromium,{viewport:{width:1280,height:900}},'chromium'],[webkit,{...devices['iPhone 13']},'webkit']]){
      browser=await engine.launch({headless:true,...(name==='webkit'&&process.env.REVIEW_WEBKIT_PATH?{executablePath:process.env.REVIEW_WEBKIT_PATH}:{})});
      const context=await browser.newContext(options),page=await context.newPage();page.setDefaultTimeout(12000);page.on('pageerror',e=>errors.push(name+': '+e.message));
      await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
      await login(page,base,name==='chromium');
      const owner=await post(page,'/api/member',{name:name+'负责人',username:name+'_repairs',password:'repair-owner-password'});
      const task=await post(page,'/api/task',{title:name+' 阻塞任务',owner_id:owner.id,status:'阻塞',blocker_reason:'等待零件 <script>',blocker_next_action:'联系供应商',due_date:'2020-01-01'});
      const topic=(await api(page,'/discussions/api/topics',{submission_id:name+'-seven-repairs-topic',title:name+' 关联讨论',body:'结论 <img src=x onerror=alert(1)>'})).body;
      assert.ok(topic.topic_id,JSON.stringify(topic));
      await page.goto(base+'/tasks?task='+task.id);
      await page.locator('#task-topic-links [data-search]').waitFor();
      await page.locator('#task-topic-links [name=query]').fill(name+' 关联讨论');await page.locator('#task-topic-links [data-search] button').click();
      await page.locator(`#task-topic-links [data-topics] option[value="${topic.topic_id}"]`).waitFor({state:'attached'});
      await page.locator('#task-topic-links [data-topics]').selectOption(String(topic.topic_id));await page.locator('#task-topic-links [data-add]').click();
      await page.locator(`#task-topic-links [data-choose="${topic.topic_id}"]`).click();
      await page.locator('#task-topic-links [data-conclusion-choice]').selectOption(String(topic.message_id));await page.locator('#task-topic-links [data-save-conclusion]').click();
      await page.locator('#task-topic-links [data-conclusion]').filter({hasText:'结论 <img'}).waitFor();
      assert.equal(await page.locator('#task-topic-links img').count(),0);
      const linkedVersion=(await api(page,'/api/task/'+task.id+'/topics')).body.version;
      assert.equal(Number(await page.locator('#progress-form [name=version]').inputValue()),linkedVersion);
      assert.equal(Number(await page.locator('#task-form [name=version]').inputValue()),linkedVersion);
      await post(page,`/discussions/api/messages/${topic.message_id}/withdraw`,{});
      await page.locator('#task-topic-links [data-conclusion]').filter({hasText:'结论已撤回'}).waitFor();
      await page.goto(base+'/discussions?topic='+topic.topic_id);await page.locator('#topic-backlinks a').filter({hasText:name+' 阻塞任务'}).waitFor();
      checks.push(name+': topic association, conclusion, withdrawal masking and backlink');
      await page.goto(base+'/task-alerts');await page.locator('#alert-items a').filter({hasText:name+' 阻塞任务'}).first().waitFor();
      assert.ok((await page.locator('#alert-items').textContent()).includes('等待零件 <script>'));
      assert.equal(await page.locator('#alert-items script').count(),0);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
      await page.screenshot({path:path.join(output,name+'-alerts.png'),fullPage:true});
      await page.goto(base+'/tasks?task='+task.id);await page.locator('#progress-form [name=progress]').fill('100');await page.locator('#progress-form [type=submit]').click();
      await page.locator('#modal-mask').waitFor({state:'hidden'});await page.goto(base+'/tasks?task='+task.id);await page.locator('#modal .modal-info').filter({hasText:'已完成'}).waitFor();
      await page.locator('#task-form [name=status]').selectOption('进行中');await page.locator('#task-form [name=progress]').fill('45');await page.locator('#task-form [name=note]').fill('实测仍需调试');await page.locator('#task-form [type=submit]').click();await page.locator('#modal-mask').waitFor({state:'hidden'});await page.goto(base+'/tasks?task='+task.id);await page.locator('#modal .modal-info').filter({hasText:'进行中'}).waitFor();
      const updated=(await api(page,'/api/data')).body.tasks.find(t=>t.id===task.id);assert.equal(updated.progress,45);assert.equal(updated.completed_at,null);
      checks.push(name+': alerts, blocked completion and explicit actual-progress reopen');
      await page.goto(base+'/tasks?type=daily#create');await page.locator('#task-create-panel[aria-busy="false"] #daily-form').waitFor();
      const catalog=(await api(page,'/api/data')).body,hardware=catalog.groups.find(g=>g.name==='硬件'),mechanical=catalog.groups.find(g=>g.name==='机械');
      const stages=[`group-${hardware.id}`,`group-${mechanical.id}`];
      await page.locator('#daily-form [name=title]').fill(name+' 动态项目');await page.locator('#daily-form [name=status]').selectOption('进行中');
      for(const stage of stages){await page.locator(`[name=enabled][value="${stage}"]`).check();await page.locator(`[name=owners-${stage}][value="${owner.id}"]`).check();}
      await page.locator('#daily-form [type=submit]').click();await page.waitForURL(/project=\d+/);
      const pid=Number(new URL(page.url()).searchParams.get('project'));
      const a=page.locator(`[data-progress-form="${stages[0]}"]`),b=page.locator(`[data-progress-form="${stages[1]}"]`);
      await b.locator('[name=note]').fill('保留另一个组的草稿');await a.locator('[name=progress]').fill('30');await a.locator('[type=submit]').click();await a.locator('.form-message').filter({hasText:'已保存'}).waitFor();
      assert.equal(await b.locator('[name=note]').inputValue(),'保留另一个组的草稿');
      await a.locator('[name=progress]').fill('100');await a.locator('[type=submit]').click();await a.locator('.form-message').filter({hasText:'已保存'}).waitFor();
      await b.locator('[name=progress]').fill('100');await b.locator('[type=submit]').click();await b.locator('[type=submit]').waitFor();
      await page.waitForFunction(()=>[...document.querySelectorAll('[data-progress-form] button[type=submit]')].every(b=>b.disabled));
      let project=(await api(page,'/api/daily-projects/'+pid)).body.project;assert.equal(project.status,'已完成');assert.equal(project.progress,100);
      assert.equal(await b.locator('[name=note]').inputValue(),'');
      await page.locator('#daily-edit').click();await page.locator('#task-create-panel[aria-busy="false"] #daily-form').waitFor();
      await page.locator('#daily-form [name=status]').selectOption('进行中');await page.locator(`[name=reopen-${stages[0]}]`).fill('60');await page.locator('#daily-form [name=note]').fill('继续联调');await page.locator('#daily-form [type=submit]').click();await page.locator('#task-create-panel').waitFor({state:'hidden'});
      await page.waitForFunction(stage=>document.querySelector(`[data-progress-form="${stage}"] [name=progress]`)?.value==='60',stages[0]);
      project=(await api(page,'/api/daily-projects/'+pid)).body.project;assert.equal(project.status,'进行中');assert.equal(project.groups.find(g=>g.stage===stages[0]).progress,60);
      await page.locator('#daily-blocker-form [name=blocker_reason]').fill('需要联调');await page.locator('#daily-blocker-form [name=blocker_next_action]').fill('安排时间');await page.locator('#daily-blocker-form [type=submit]').click();await page.locator('#daily-blocker-form [data-resolve-blocker]').waitFor();
      await page.locator('#daily-blocker-form [name=blocker_result]').fill('已排定联调');await page.locator('#daily-blocker-form [data-resolve-blocker]').click();await page.locator('#daily-blocker-form [data-resolve-blocker]').waitFor({state:'detached'});
      await page.screenshot({path:path.join(output,name+'-daily.png'),fullPage:true});
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
      assert.equal(await page.evaluate(()=>[...document.querySelectorAll('#daily-blocker-form label')].every(label=>{const control=label.querySelector('textarea,input,select'),outer=label.getBoundingClientRect(),inner=control.getBoundingClientRect();return getComputedStyle(label).display==='grid'&&inner.width>0&&inner.height>=44&&inner.left>=outer.left-1&&inner.right<=outer.right+1;})),true);
      checks.push(name+': dynamic group creation, independent drafts, final completion, specified group reopen, blocker cycle');
      await browser.close();browser=null;
    }
    assert.deepEqual(errors,[]);fs.writeFileSync(path.join(output,'results.json'),JSON.stringify({checks,errors},null,2));console.log(JSON.stringify({checks,errors},null,2));
  }catch(error){fs.writeFileSync(path.join(output,'server.log'),log);throw error;}
  finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exitCode=1;});
