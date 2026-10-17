"use strict";
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {spawn,spawnSync}=require('node:child_process');
const {chromium,webkit,devices}=require('playwright');
const root=path.resolve(__dirname,'../..'),output=path.join(root,'.venv','daily-mail-check');fs.mkdirSync(output,{recursive:true});
async function api(page,url,data){return page.evaluate(async({url,data})=>{const r=await fetch(url,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});return {status:r.status,body:await r.json()};},{url,data});}
(async()=>{
  const python=process.env.REVIEW_PYTHON||'python';
  const server=spawn(python,['-B',path.join(__dirname,'server.py')],{cwd:root,env:{...process.env,MAIL_MODE:'preview',PUBLIC_BASE_URL:'https://team.example'},stdio:['ignore','pipe','pipe']});
  let browser,phoneBrowser,log='',testRoot,buffer='';const errors=[],checks=[];
  try{
    const base=await new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>reject(new Error('server timeout '+log)),30000);
      server.stderr.on('data',d=>log+=d);server.stdout.on('data',d=>{buffer+=d;const url=buffer.match(/BROWSER_URL=(\S+)/),dir=buffer.match(/BROWSER_ROOT=([^\r\n]+)/);if(url&&dir){clearTimeout(timer);testRoot=dir[1];resolve(url[1]);}});server.once('exit',code=>{clearTimeout(timer);reject(new Error('server exit '+code));});
    });
    browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:1280,height:900}});page.setDefaultTimeout(15000);
    page.on('pageerror',error=>errors.push(error.message));page.on('dialog',d=>d.accept());
    await page.goto(base+'/login');await page.locator('[name=username]').fill('admin');await page.locator('[name=password]').fill('123456');await page.locator('#login-form [type=submit]').click();await page.waitForURL('**/account');
    await page.locator('#account-password-form [name=old]').fill('123456');await page.locator('#account-password-form [name=new]').fill('daily-browser-password');await page.locator('#account-password-form [name=confirm]').fill('daily-browser-password');await page.locator('#account-password-form button').click();await page.waitForURL(base+'/');
    const owner=(await api(page,'/api/member',{name:'浏览器负责人',username:'daily_browser_owner',password:'owner-password'})).body;
    await page.goto(base+'/daily-projects');await page.locator('#new-task-toggle').click();
    await page.locator('#daily-form [name=title]').fill('共同大目标：研发长期可读的机械电控视觉协作方案');
    await page.locator('#daily-form [name=detail]').fill('一个大目标，不拆分小任务。');await page.locator('#daily-form [name=status]').selectOption('阻塞');
    for(const stage of ['mechanical','vision']){await page.locator(`[name=enabled][value=${stage}]`).check();await page.locator(`[name=owners-${stage}][value="${owner.id}"]`).check();}
    await page.locator('#daily-form [type=submit]').click();await page.waitForURL(/\/tasks\?type=daily&project=\d+$/);
    const id=Number(new URL(page.url()).searchParams.get('project'));await page.locator('[data-progress-form=mechanical]').waitFor();
    await page.locator('[data-progress-form=vision] [name=note]').fill('尚未提交的视觉草稿');
    await page.locator('[data-progress-form=mechanical] [name=progress]').fill('70');await page.locator('[data-progress-form=mechanical] [name=note]').fill('机械结构完成');await page.locator('[data-progress-form=mechanical] button').click();
    await page.locator('[data-progress-form=mechanical] .form-message').filter({hasText:'进度已保存'}).waitFor();assert.equal(await page.locator('[data-progress-form=vision] [name=note]').inputValue(),'尚未提交的视觉草稿');
    assert.equal((await api(page,`/api/daily-projects/${id}`)).body.project.progress,35);
    await page.route(`**/api/daily-projects/${id}/groups/vision/progress`,r=>r.request().method()==='POST'?r.fulfill({status:409,json:{ok:false,error:'分配或进度已变化'}}):r.continue());
    await page.locator('[data-progress-form=vision] button').click();await page.locator('[data-progress-form=vision] .form-message').filter({hasText:'输入已保留'}).waitFor();assert.equal(await page.locator('[data-progress-form=vision] [name=note]').inputValue(),'尚未提交的视觉草稿');
    await page.unroute(`**/api/daily-projects/${id}/groups/vision/progress`);checks.push('goal creation, group progress, cross-group draft retention and conflict recovery');
    for(const theme of ['light','dark']){
      await api(page,'/api/account/preferences',{theme});
      for(const width of [375,390,767,768,1280]){
        await page.setViewportSize({width,height:900});await page.goto(base+`/daily-projects/${id}`);await page.locator('[data-progress-form=mechanical]').waitFor();
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true,theme+' '+width);
        await page.screenshot({path:path.join(output,`daily-${theme}-${width}.png`),fullPage:true});
      }
    }
    checks.push('both themes at 375/390/767/768/1280 CSS pixels without body overflow');
    await page.setViewportSize({width:1280,height:900});await page.goto(base+'/account');
    const mailPanel=page.locator('section').filter({has:page.locator('#email-form')});
    assert.match(await mailPanel.textContent(),/08:00.*个人未完成任务早报.*22:00/);
    // Initial account loading populates the input; wait before entering a new address.
    await page.locator('#email-state').filter({hasText:'模拟模式'}).waitFor();
    await page.locator('#email-address').fill('browser@example.com');await page.locator('#email-form button').click();await page.locator('#email-result').filter({hasText:'模拟模式'}).waitFor();
    const query=spawnSync(python,['-c',"import sqlite3,sys; db=sqlite3.connect(sys.argv[1]); print(db.execute('SELECT generation FROM member_email WHERE member_id=1').fetchone()[0])",path.join(testRoot,'data.db')],{encoding:'utf8'});assert.equal(query.status,0,query.stderr);
    const digest=crypto.createHmac('sha256',fs.readFileSync(path.join(testRoot,'secret.key'),'utf8').trim()).update('email-code:'+query.stdout.trim()).digest();
    const code=String(Number(digest.readBigUInt64BE(0)%1000000n)).padStart(6,'0');
    await page.locator('#email-code-form').waitFor({state:'visible'});
    await page.locator('#email-code').fill(code);
    await page.locator('#email-code-form button').click();
    await page.locator('#email-result').filter({hasText:'邮箱已验证'}).waitFor();
    assert.equal(await page.locator('#email-code-form').isVisible(),false);
    await page.goto(base+'/admin/mail');await page.locator('[data-mail-member="1"]').click();await page.locator('[data-mail-member="1"]').filter({hasText:'恢复'}).waitFor();
    await page.goto(base+'/account');await page.locator('#email-state').filter({hasText:'管理员已暂停'}).waitFor();
    await page.goto(base+'/admin/mail');await page.locator('[data-mail-member="1"]').click();await page.locator('[data-mail-member="1"]').filter({hasText:'暂停'}).waitFor();await page.locator('#mail-test').click();await page.locator('#mail-result').filter({hasText:'加入队列'}).waitFor();checks.push('email request, six-digit inline verification, admin pause/resume and test queue');
    const mailState=(await api(page,'/api/admin/mail?page=1')).body;
    const sample=mailState.jobs[0];assert.ok(sample);
    await page.route('**/api/admin/mail?*',r=>r.fulfill({json:{...mailState,jobs:[{...sample,kind:'morning'},{...sample,kind:'daily'}]}}));
    await page.locator('#mail-refresh').click();
    await page.locator('#mail-jobs strong').filter({hasText:'个人任务早报'}).waitFor();
    await page.locator('#mail-jobs strong').filter({hasText:'团队项目晚报'}).waitFor();
    assert.match(await page.locator('#mail-admin').textContent(),/08:00.*22:00/);
    await page.screenshot({path:path.join(output,'morning-evening-mail-admin.png'),fullPage:true});
    await page.unroute('**/api/admin/mail?*');checks.push('08:00 personal and 22:00 team notices and separate admin record labels');
    await page.goto(base+'/');await page.locator('#daily-dashboard a').filter({hasText:'共同大目标'}).waitFor();
    const task=(await api(page,'/api/task',{title:'邮件链接目标'})).body;
    await page.goto(base+'/tasks?task='+task.id);await page.locator('#modal').filter({hasText:'邮件链接目标'}).waitFor();
    // Use a fresh unauthenticated context for email-link login; background task polling
    // in the original page must not race a fetch-based logout and restore its cookie.
    const returnPage=await browser.newPage();returnPage.on('pageerror',e=>errors.push(e.message));
    await returnPage.goto(base+`/daily-projects/${id}`);await returnPage.waitForURL(/\/login\?/);await returnPage.locator('[name=username]').fill('admin');await returnPage.locator('[name=password]').fill('daily-browser-password');await returnPage.locator('#login-form [type=submit]').click();await returnPage.waitForURL(base+`/tasks?type=daily&project=${id}`);await returnPage.context().close();checks.push('separate dashboard and login returns to linked goal');
    if(process.env.REVIEW_WEBKIT_PATH){
      phoneBrowser=await webkit.launch({headless:true,executablePath:process.env.REVIEW_WEBKIT_PATH});
      const context=await phoneBrowser.newContext({...devices['iPhone 13']});await context.addCookies(await page.context().cookies());const phone=await context.newPage();phone.on('pageerror',e=>errors.push(e.message));
      await phone.goto(base+`/daily-projects/${id}`);await phone.locator('[data-progress-form=mechanical]').waitFor();assert.equal(await phone.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
      await phone.locator('[data-progress-form=mechanical] [name=note]').fill('WebKit 手机更新');await phone.locator('[data-progress-form=mechanical] button').click();await phone.locator('[data-progress-form=mechanical] .form-message').filter({hasText:'进度已保存'}).waitFor();await phone.screenshot({path:path.join(output,'daily-webkit-iphone.png'),fullPage:true});checks.push('iPhone WebKit simulated group update');
    }
    assert.deepEqual(errors,[]);fs.writeFileSync(path.join(output,'result.json'),JSON.stringify({ok:true,checks,errors},null,2));console.log('DAILY_MAIL_BROWSER_OK '+JSON.stringify(checks));
  }catch(error){console.error(error);console.error(errors);console.error(log.slice(-2000));process.exitCode=1;}
  finally{if(phoneBrowser)await phoneBrowser.close();if(browser)await browser.close();server.kill();}
})();
