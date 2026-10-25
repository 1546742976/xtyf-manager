'use strict';
const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const path=require('node:path');
const playwright=require('playwright');
const root=path.resolve(__dirname,'../..');
(async()=>{
  const server=spawn(process.env.REVIEW_PYTHON||'python',['-B','tests/browser/server.py'],
    {cwd:root,env:{...process.env,PYTHONUTF8:'1'},stdio:['ignore','pipe','pipe']});
  let log='',stdout='',browser;
  server.stderr.on('data',data=>log+=data);
  try{
    const base=await new Promise((resolve,reject)=>{
      const timeout=setTimeout(()=>reject(Error('startup timeout '+log)),30000);
      server.stdout.on('data',data=>{stdout+=data;const match=stdout.match(/BROWSER_URL=(\S+)/);if(match){clearTimeout(timeout);resolve(match[1]);}});
      server.once('exit',code=>{clearTimeout(timeout);reject(Error('server exit '+code+' '+log));});
    });
    let password='browser-test-initial';
    for(const engine of ['chromium','webkit']){
      browser=await playwright[engine].launch({headless:true});
      const context=await browser.newContext(),page=await context.newPage(),errors=[];
      page.on('pageerror',error=>errors.push(error.message));
      await page.goto(base+'/login');
      const post=(url,body)=>page.evaluate(async({url,body})=>{
        const token=(await(await fetch('/api/csrf')).json()).token;
        const response=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':token},body:JSON.stringify(body)});
        return {status:response.status,body:await response.json()};
      },{url,body});
      assert.equal((await post('/login',{username:'admin',password})).body.ok,true);
      if(engine==='chromium'){
        assert.equal((await post('/api/password',{old:password,new:'Synthetic-polling-password'})).body.ok,true);
        password='Synthetic-polling-password';
      }
      let state='running',requests=0,streams=0,deny=false;
      page.on('request',req=>{if(req.url().includes('/job/stream/'))streams++;});
      await page.route('**/invoices/api/jobs',route=>{
        requests++;return route.fulfill({status:deny?403:200,contentType:'application/json',body:JSON.stringify(deny?{error:'权限已变化'}:{jobs:[]})});
      });
      await page.route('**/invoices/api/job/synthetic-progress',route=>route.fulfill({contentType:'application/json',body:JSON.stringify({
        id:'synthetic-progress',type:'refresh',status:state,progress:state==='done'?100:10,message:'合成任务',result:{total:1}})}));
      await page.goto(base+'/invoices/');
      await page.waitForFunction(()=>!!window.InvoiceJobs?.monitor);
      await page.evaluate(()=>{
        window.resultCount=0;const show=InvoiceJobs.showResult;
        InvoiceJobs.showResult=data=>{window.resultCount++;show(data);};
        return InvoiceJobs.watch('synthetic-progress');
      });
      state='done';await page.evaluate(()=>InvoiceJobs.monitor.refresh());
      await page.locator('#invoiceJobModal[open]').waitFor();
      await page.evaluate(()=>InvoiceJobs.monitor.refresh());
      assert.equal(await page.evaluate(()=>window.resultCount),1);
      assert.equal(streams,0);
      await context.setOffline(true);await page.waitForFunction(()=>!navigator.onLine);
      const before=requests;await page.evaluate(()=>InvoiceJobs.monitor.refresh());assert.equal(requests,before);
      await context.setOffline(false);await page.waitForFunction(()=>navigator.onLine);
      await page.waitForFunction(()=>!!document.querySelector('#invoiceJobsList'));
      deny=true;await page.evaluate(()=>InvoiceJobs.monitor.refresh());
      await page.locator('#invoiceJobsList').filter({hasText:'重新登录'}).waitFor();
      const stopped=requests;await page.evaluate(()=>InvoiceJobs.monitor.refresh());assert.equal(requests,stopped);
      assert.deepEqual(errors,[]);
      console.log(engine+': polling, omitted job, result once, offline and permission stop PASS');
      await browser.close();browser=null;
    }
  }catch(error){console.error(error,log.slice(-3000));process.exitCode=1;}
  finally{if(browser)await browser.close();server.kill();}
})();
