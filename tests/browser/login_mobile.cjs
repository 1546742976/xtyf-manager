"use strict";
// Install Playwright's WebKit and Chromium engines before running.
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const {chromium, webkit, devices} = require('playwright');
const root = path.resolve(__dirname, '../..');
const output = path.join(root, '.venv', 'login-password-check');
fs.mkdirSync(output, {recursive:true});
(async () => {
  const server = spawn(process.env.REVIEW_PYTHON || 'python', ['-B',path.join(__dirname,'server.py')], {
    cwd:root, env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'}, stdio:['ignore','pipe','pipe']});
  let browser, log='';
  const results=[];
  try {
    const base=await new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>reject(new Error('server timeout '+log)),30000);
      server.stderr.on('data',data=>log+=data);
      server.stdout.on('data',data=>{const match=String(data).match(/BROWSER_URL=(\S+)/);if(match){clearTimeout(timer);resolve(match[1]);}});
      server.once('exit',code=>{clearTimeout(timer);reject(new Error('server exited '+code+' '+log));});
    });
    for(const [engine, device] of [[webkit,'iPhone SE'],[webkit,'iPhone 13'],[chromium,'Pixel 7']]) {
      const executablePath=engine===webkit ? process.env.REVIEW_WEBKIT_PATH : undefined;
      browser=await engine.launch({headless:true,...(executablePath?{executablePath}:{})});
      const context=await browser.newContext({...devices[device]});
      const page=await context.newPage(), errors=[];
      page.on('pageerror',error=>errors.push(error.message));
      await page.goto(base+'/login');
      const password=page.getByLabel('密码',{exact:true});
      const toggle=page.locator('.login-password-toggle');
      await page.locator('[name=username]').fill('admin');
      const longPassword='Long-mobile-password-1234567890-1234567890';
      await password.fill(longPassword);
      const portrait=devices[device].viewport;
      for(const [orientation,viewport] of [
        ['portrait',portrait],['landscape',{width:portrait.height,height:portrait.width}],
        ['short-viewport',{width:portrait.width,height:320}],
      ]) {
        await page.setViewportSize(viewport);
        assert.equal(await password.getAttribute('type'),'password');
        await toggle.tap();
        assert.equal(await password.getAttribute('type'),'text');
        assert.equal(await password.inputValue(),longPassword);
        assert.equal(await toggle.getAttribute('aria-label'),'隐藏密码');
        assert.equal(await toggle.locator('.password-hide-icon').isVisible(),true);
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
        for(const input of [page.locator('[name=username]'),password]) {
          assert.ok(await input.evaluate(element=>parseFloat(getComputedStyle(element).fontSize)>=16));
        }
        const box=await toggle.boundingBox();
        assert.ok(box.width>=44 && box.height>=44);
        assert.ok(await password.evaluate(element=>parseFloat(getComputedStyle(element).paddingRight)>=46));
        await page.screenshot({path:path.join(output,`${device.replaceAll(' ','-')}-${orientation}.png`),fullPage:true});
        await toggle.tap();
        assert.equal(await password.inputValue(),longPassword);
        await page.locator('button[type=submit]').scrollIntoViewIfNeeded();
        assert.ok(await page.locator('button[type=submit]').isVisible());
      }
      await toggle.tap();await page.reload();
      assert.equal(await password.getAttribute('type'),'password');
      await page.locator('[name=username]').fill('admin');
      await password.fill('browser-test-initial');
      await toggle.tap();
      await page.locator('button[type=submit]').tap();
      await page.waitForURL('**/account');
      assert.deepEqual(errors,[]);
      results.push({device,engine:engine.name(),version:browser.version(),orientations:3,login:true});
      await browser.close();browser=undefined;
    }
    fs.writeFileSync(path.join(output,'mobile-result.json'),JSON.stringify({ok:true,results},null,2));
    console.log('MOBILE_LOGIN_OK '+JSON.stringify(results));
  } catch(error) {console.error(error);console.error(log.slice(-1500));process.exitCode=1;}
  finally {if(browser)await browser.close();server.kill();}
})();
