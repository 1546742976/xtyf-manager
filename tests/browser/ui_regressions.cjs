'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawn} = require('node:child_process');
const playwright = require('playwright');
const root = path.resolve(__dirname, '../..');
const output = path.join(root, '.build-tools/ui-regressions');
fs.mkdirSync(output, {recursive:true});
(async () => {
  const server = spawn(process.env.REVIEW_PYTHON || 'python', ['-B', path.join(__dirname, 'server.py')], {cwd:root, env:{...process.env, PYTHONUTF8:'1'}, stdio:['ignore','pipe','pipe']});
  let log='', buffer='', browser;
  const passed=[];
  try {
    const base = await new Promise((resolve,reject) => {
      const timer=setTimeout(()=>reject(Error('startup timeout '+log)),30000);
      server.stderr.on('data', data=>log+=data);
      server.stdout.on('data',data=>{buffer+=data;const match=buffer.match(/BROWSER_URL=(\S+)/);if(match){clearTimeout(timer);resolve(match[1]);}});
      server.once('exit',code=>{clearTimeout(timer);reject(Error('server exited '+code+' '+log));});
    });
    for (const engine of ['chromium','webkit']) {
      browser=await playwright[engine].launch({headless:true});
      const page=await browser.newPage({viewport:{width:1280,height:900}});
      const external=[], errors=[];
      page.on('pageerror',error=>errors.push(error.message));
      await page.route('**/*',route=>{const url=route.request().url();if(/^https?:/.test(url)&&!url.startsWith(base+'/')){external.push(url);return route.abort();}return route.continue();});
      await page.goto(base+'/login');
      assert.equal((await page.request.get(base+'/vendor/bootstrap/bootstrap.min.css')).status(),200);
      await page.locator('[name=username]').fill('admin');
      await page.locator('[name=password]').fill('wrong-password');
      await page.locator('#login-form [type=submit]').click();
      await page.waitForFunction(()=>document.querySelector('#login-error').textContent);
      assert.equal(await page.locator('#login-error').getAttribute('role'),'alert');
      assert.equal(await page.locator('[name=username]').getAttribute('aria-invalid'),'true');
      await page.locator('[name=password]').fill(engine==='chromium'?'browser-test-initial':'Ui-regression-password-2026');
      await page.locator('#login-form [type=submit]').click();
      if(engine==='chromium'){
      await page.waitForURL('**/account');
      await page.locator('#account-password-form [name=old]').fill('browser-test-initial');
      for(const name of ['new','confirm'])await page.locator(`#account-password-form [name=${name}]`).fill('Ui-regression-password-2026');
      await page.locator('#account-password-form button').click();await page.waitForURL(base+'/');
      }else await page.waitForURL(base+'/');
      await page.goto(base+'/funds/');
      await page.getByRole('tab',{name:'添加记录',exact:true}).click();
      assert.equal(await page.locator('#t-add').isVisible(),true);
      await page.locator('#aNotes').fill('验收备注');
      assert.equal(await page.locator('#aNotes').getAttribute('maxlength'),'2000');
      assert.equal(await page.locator('#chartImg').getAttribute('src'),null);
      await page.locator('#aT').selectOption('进');await page.locator('#aAmt').fill('12.34');
      await page.locator('#addF [type=submit]').click();
      await page.waitForFunction(()=>document.querySelector('#funds-feedback').textContent.includes('记录已'));
      await page.getByRole('tab',{name:'记录管理',exact:true}).click();
      await page.locator('#tbody button[title="编辑记录"]').first().click();
      await page.locator('#eAmt').waitFor({state:'visible'});
      assert.equal(await page.locator('#eAmt').inputValue(),'12.34');
      await page.locator('#editMd [aria-label="关闭编辑记录"]').click();
      await page.goto(base+'/members');
      await page.locator('#import-form [name=text]').fill('验收成员,,ui-'+engine+'\n验收重名,,ui-'+engine);
      await page.locator('#import-form [type=submit]').click();
      await page.locator('#member-import-result').waitFor();
      assert.match(await page.locator('#member-import-result').innerText(),/成功 1 人，失败 1 行/);
      assert.match(await page.locator('#member-import-result').innerText(),/2/);
      const task=await page.evaluate(async()=>{
        const me=await (await fetch('/api/client/bootstrap')).json();
        return (await fetch('/api/task',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:'弹窗隐藏字段验收',owner_id:me.me.id,status:'进行中'})})).json();
      });
      assert.equal(task.ok,true);
      await page.goto(base+'/tasks#task='+task.id);
      await page.locator('#task-form').waitFor();
      assert.equal(await page.locator('[data-task-reopen]').isVisible(),false);
      assert.equal(await page.locator('[data-task-blocker-result]').isVisible(),false);
      await page.goto(base+'/invoices/');
      await page.waitForFunction(()=>window.App?.stats);
      const trigger=page.getByRole('button',{name:'统计',exact:true});
      await trigger.click();
      const dialog=page.getByRole('dialog',{name:'发票统计'});
      assert.equal(await dialog.isVisible(),true);
      assert.equal(await dialog.evaluate(el=>el.contains(document.activeElement)),true);
      await page.screenshot({path:path.join(output,`${engine}-dialog.png`)});
      await page.keyboard.press('Shift+Tab');
      assert.equal(await dialog.evaluate(el=>el.contains(document.activeElement)),true);
      await page.keyboard.press('Escape');
      await dialog.waitFor({state:'hidden'});
      assert.equal(await dialog.isVisible(),false);
      assert.equal(await trigger.evaluate(el=>document.activeElement===el),true);
      await page.evaluate(async()=>{showToast('旧错误','error');await BUModal.open([{success:true,tmp_path:'synthetic-no-write',original_name:'合成.pdf',info:{amount:'0.004'}}]);});
      assert.equal(await page.locator('#toast.show').count(),0);
      assert.equal(await page.locator('.bu-amount-0').getAttribute('aria-invalid'),'true');
      await page.locator('.bu-amount-0').fill('0.005');
      assert.equal(await page.locator('.bu-amount-0').getAttribute('aria-invalid'),'false');
      await page.evaluate(()=>{BUModal._data=[];BUModal.close();});
      await page.route('**/invoices/api/invoices?*',async route=>{
        const response=await route.fetch();
        await route.fulfill({response,headers:{...response.headers(),'X-Invoice-Snapshot-State':'failed','X-Invoice-Snapshot-Updated-At':'2026-10-22T12:00:00+08:00'}});
      });
      await page.evaluate(()=>loadInvoices(1));
      assert.match(await page.locator('#invoice-snapshot-status').innerText(),/更新失败.*上次完成.*2026-10-22/);
      await page.unroute('**/invoices/api/invoices?*');
      for(const width of [390,760,1280]) {
        await page.setViewportSize({width,height:900});
        const size=await page.locator('#invoiceSortDirection').boundingBox();
        if(width<768)assert.ok(size.height>=44&&size.width>=44);
        await page.screenshot({path:path.join(output,`${engine}-${width}.png`)});
      }
      assert.equal((await page.goto(base+'/missing-ui-regression-page')).status(),404);
      assert.match(await page.locator('body').innerText(),/未找到|不存在/);
      assert.deepEqual(external,[], 'public pages must not request external CDN assets');
      assert.deepEqual(errors,[]);
      passed.push(engine);await browser.close();browser=null;

    }
    fs.writeFileSync(path.join(output,'result.json'),JSON.stringify({passed, nativeRuntime:false},null,2));
  } finally {if(browser)await browser.close();server.kill();fs.writeFileSync(path.join(output,'server.log'),log);}
})().catch(error=>{console.error(error);process.exitCode=1;});


