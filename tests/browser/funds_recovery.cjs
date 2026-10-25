'use strict';
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const path = require('node:path');
const playwright = require('playwright');
const root = path.resolve(__dirname, '../..');

(async()=>{
  const server = spawn(process.env.REVIEW_PYTHON || 'python', ['-B', 'tests/browser/server.py'],
    {cwd:root,env:{...process.env,PYTHONUTF8:'1'},stdio:['ignore','pipe','pipe']});
  let log='', stdout='', browser;
  server.stderr.on('data',data=>{log+=data;});
  try {
    const base = await new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>reject(Error('server startup timeout '+log)),30000);
      server.stdout.on('data',data=>{stdout+=data;const match=stdout.match(/BROWSER_URL=(\S+)/);if(match){clearTimeout(timer);resolve(match[1]);}});
      server.once('exit',code=>{clearTimeout(timer);reject(Error('server exit '+code+' '+log));});
    });
    let password='browser-test-initial';
    for(const engine of ['chromium','webkit']){
      browser=await playwright[engine].launch({headless:true});
      const page=await browser.newPage(), errors=[];
      page.on('pageerror',error=>errors.push(error.message));
      await page.goto(base+'/login');
      const api=(url,body)=>page.evaluate(async({url,body})=>{
        const token=(await (await fetch('/api/csrf')).json()).token;
        const response=await fetch(url,body?{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':token},body:JSON.stringify(body)}:{});
        return {status:response.status,body:await response.json()};
      },{url,body});
      assert.equal((await api('/login',{username:'admin',password})).body.ok,true);
      if(engine==='chromium'){
        assert.equal((await api('/api/password',{old:password,new:'Funds-recovery-password-2026'})).body.ok,true);
        password='Funds-recovery-password-2026';
      }
      await page.goto(base+'/funds/');
      await page.locator('#funds-ledger-state').filter({hasText:'TXT 台账已同步'}).waitFor();
      const ledgerBefore=(await api('/funds/api/records')).body.data;
      await page.locator('#funds-ledger-repair').click();
      await page.locator('#funds-feedback').filter({hasText:'资金记录未改变'}).waitFor();
      assert.deepEqual((await api('/funds/api/records')).body.data,ledgerBefore);
      for(const mode of ['html','truncated','structure','mismatch','server-error']){
        await page.locator('[data-bs-target="#t-add"]').click();
        await page.locator('#aT').selectOption('进');
        await page.locator('#aAmt').fill('123.45');
        const note=engine+'-'+mode;
        await page.locator('#aNotes').fill(note);
        const requests=[];let first=true;
        await page.route('**/funds/api/records',async route=>{
          if(route.request().method()!=='POST')return route.continue();
          requests.push(route.request().postDataJSON());
          const response=await route.fetch();
          assert.equal(response.status(),200);
          if(!first)return route.fulfill({response});
          first=false;
          const receipt=await response.json();
          const bodies={html:'<html>login</html>',truncated:'{"code":0',structure:'{"code":0}',
            mismatch:JSON.stringify({...receipt,request_id:'another-request-id'}),'server-error':JSON.stringify(receipt)};
          return route.fulfill({status:mode==='server-error'?503:200,contentType:mode==='html'?'text/html':'application/json',body:bodies[mode]});
        });
        await page.locator('#addF [type=submit]').click();
        await page.locator('#funds-create-status').filter({hasText:'结果待确认'}).waitFor();
        assert.ok(await page.locator('#addF [type=submit]').isDisabled());
        assert.equal(await page.locator('#aNotes').inputValue(),note);
        await page.reload();
        await page.locator('[data-bs-target="#t-add"]').click();
        await page.locator('#funds-create-retry').waitFor();
        assert.equal(await page.locator('#aNotes').inputValue(),note);
        await page.locator('#funds-create-retry').click();
        await page.waitForFunction(()=>!document.querySelector('#funds-create-fields').disabled);
        assert.deepEqual(requests[0],requests[1]);
        const records=(await api('/funds/api/records')).body.data;
        assert.equal(records.filter(record=>record.notes===note).length,1);
        await page.unroute('**/funds/api/records');
      }
      // A browser-storage failure must happen before any write request leaves the page.
      let writes=0;
      await page.route('**/funds/api/records',route=>{if(route.request().method()==='POST')writes++;return route.continue();});
      await page.locator('#aT').selectOption('进');await page.locator('#aAmt').fill('10');
      await page.evaluate(()=>{Storage.prototype.setItem=()=>{throw Error('synthetic quota');};});
      await page.locator('#addF [type=submit]').click();
      await page.locator('#funds-feedback').filter({hasText:'未发送新增请求'}).waitFor();
      assert.equal(writes,0);
      await page.unroute('**/funds/api/records');
      // An old server can still list funds, while creation remains unavailable.
      await page.route('**/funds/api/capabilities',route=>route.fulfill({status:404,contentType:'application/json',body:'{"code":1}'}));
      await page.reload();
      await page.locator('[data-bs-target="#t-add"]').click();
      await page.locator('#funds-create-status').filter({hasText:'已暂停新增'}).waitFor();
      assert.ok(await page.locator('#addF [type=submit]').isDisabled());
      assert.equal((await api('/funds/api/records')).status,200);
      assert.deepEqual(errors,[]);
      console.log(engine+': response recovery, immutable retries, refresh drafts, storage and legacy capability PASS');
      await browser.close();browser=null;
    }
  } catch(error) {console.error(error,log.slice(-4000));process.exitCode=1;}
  finally {if(browser)await browser.close();server.kill();}
})();
