'use strict';
const assert=require('node:assert/strict'),{spawn}=require('node:child_process'),path=require('node:path'),fs=require('node:fs');
const {chromium,webkit,request}=require('playwright');
const {recordFailure,cleanupResources}=require('./diagnostics.cjs');
const root=path.resolve(__dirname,'../..'),output=path.join(root,'.build-tools/responsive-inputs-check');
fs.mkdirSync(output,{recursive:true});
const server=spawn(process.env.REVIEW_PYTHON||'python',['-B','tests/browser/server.py'],{cwd:root,env:{...process.env,PYTHONUTF8:'1'}});
let browser,page,api,engineName,phase='startup';const passed=[],errors=[];
async function settle(){await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));}
async function main(){
  const base=await new Promise((resolve,reject)=>{
    let out='',err='';const timer=setTimeout(()=>reject(Error('server timeout '+err)),30000);
    server.stderr.on('data',d=>err+=d);server.stdout.on('data',d=>{out+=d;const m=out.match(/BROWSER_URL=(\S+)/);if(m){clearTimeout(timer);resolve(m[1]);}});
    server.once('exit',code=>{clearTimeout(timer);reject(Error('server exited '+code+' '+err));});
  });
  api=await request.newContext({baseURL:base});
  async function post(url,data){const csrf=(await (await api.get('/api/csrf')).json()).csrf_token;const r=await api.post(url,{headers:{'X-CSRF-Token':csrf},data});assert.equal(r.status(),200,await r.text());}
  await post('/api/client/login',{username:'admin',password:'browser-test-initial'});
  await post('/api/password',{old:'browser-test-initial',new:'Synthetic-responsive-2026',confirm:'Synthetic-responsive-2026'});
  for(const [name,engine] of [['chromium',chromium],['webkit',webkit]]){
    engineName=name;browser=await engine.launch({headless:true});
    const context=await browser.newContext({storageState:await api.storageState(),serviceWorkers:'block'});
    page=await context.newPage();page.setDefaultTimeout(15000);page.on('pageerror',e=>errors.push(e.message));
    for(const url of ['/account','/tasks','/discussions','/assets','/funds/','/invoices/']){
      phase=name+' shared script '+url;await page.goto(base+url);await page.waitForLoadState('networkidle');
      assert.equal(await page.evaluate(()=>typeof window.FormControls?.refresh),'function');
      assert.deepEqual(await page.locator('input,select,textarea').evaluateAll(fields=>fields.filter(f=>getComputedStyle(f).resize!=='none').map(f=>f.id)),[]);
    }
    for(const width of [320,768,1440]){
      await page.setViewportSize({width,height:900});
      for(const url of ['/invoices/','/account','/tasks','/discussions']){
        await page.goto(base+url);await page.waitForLoadState('networkidle');await settle();
        assert.deepEqual(await page.locator('input,select,textarea').evaluateAll(fields=>fields.filter(f=>f.getClientRects().length&&f.getBoundingClientRect().right>innerWidth+1).map(f=>f.id)),[],url+' controls overflow');
        if(url==='/invoices/'&&width===1440){
          assert.ok(await page.locator('#filterStatus').evaluate(f=>f.offsetWidth<f.parentElement.clientWidth/2),'inline invoice filter stretched');
          await page.evaluate(()=>document.getElementById('batchBar').style.display='flex');
          assert.ok(await page.locator('#batchStatus').evaluate(f=>f.offsetWidth<f.parentElement.clientWidth/2),'batch select stretched');
        }
      }
    }
    phase=name+' dynamic dialog and responsive text';await page.goto(base+'/account');await page.waitForLoadState('networkidle');
    await page.evaluate(()=>{
      const box=document.createElement('dialog');box.id='responsive-probe';box.style.cssText='width:min(620px,calc(100vw - 32px));box-sizing:border-box;';
      box.innerHTML='<form><label>合成说明<textarea rows="2" style="padding:12px;border:1px solid"></textarea></label><input type="text" aria-label="合成输入"><input type="checkbox" aria-label="合成选择"></form>';
      document.body.append(box);box.showModal();
    });
    const text=page.locator('#responsive-probe textarea');
    for(const width of [320,768,1440]){
      await page.setViewportSize({width,height:900});await text.fill('短文');await settle();
      const short=await text.evaluate(f=>f.getBoundingClientRect().height);
      await text.fill(Array(8).fill('中文说明与 long-text').join('\n'));await settle();
      const medium=await text.evaluate(f=>f.getBoundingClientRect().height);assert.ok(medium>short);
      await text.fill(Array(80).fill('中文内容 long text').join('\n'));await settle();
      const large=await text.evaluate(f=>({height:f.offsetHeight,scroll:f.scrollHeight,client:f.clientHeight,overflow:getComputedStyle(f).overflowY,width:f.offsetWidth,parent:f.parentElement.clientWidth}));
      assert.ok(large.height<=362);assert.ok(large.scroll>large.client);assert.equal(large.overflow,'auto');
      await text.fill('');await settle();assert.ok(await text.evaluate(f=>f.offsetHeight)<medium);
      assert.ok(await page.locator('#responsive-probe input[type=text]').evaluate(f=>f.offsetWidth<=f.form.clientWidth));
      assert.ok(await page.locator('#responsive-probe input[type=checkbox]').evaluate(f=>f.offsetWidth<50));
    }
    // Visible prefill must work without an input event or reopening the dialog.
    await page.evaluate(()=>{const f=document.querySelector('#responsive-probe textarea');f.value='程序赋值\n'.repeat(70);FormControls.refresh(f);});await settle();
    assert.ok(await text.evaluate(f=>f.scrollHeight>f.clientHeight));
    await page.evaluate(()=>{const f=document.querySelector('#responsive-probe textarea');f.value='';FormControls.refresh(f);});await settle();
    assert.ok(await text.evaluate(f=>f.offsetHeight)<150);
    // Reopening, programmatic prefill, reset, parent-only resize and font zoom.
    await page.evaluate(()=>{const d=document.getElementById('responsive-probe');d.close();d.querySelector('textarea').value='预填\n'.repeat(60);d.showModal();});await settle();
    assert.ok(await text.evaluate(f=>f.scrollHeight>f.clientHeight));
    await page.evaluate(()=>document.querySelector('#responsive-probe form').reset());await settle();
    assert.ok(await text.evaluate(f=>f.offsetHeight)<150);
    await text.fill('W '.repeat(80));await settle();const wide=await text.evaluate(f=>f.offsetHeight);
    await page.evaluate(()=>document.getElementById('responsive-probe').style.width='260px');await settle();await settle();
    assert.ok(await text.evaluate(f=>f.offsetHeight)>wide);
    await page.evaluate(()=>document.documentElement.style.fontSize='200%');await settle();
    assert.ok(await text.evaluate(f=>f.offsetWidth<=f.form.clientWidth));
    const scans=await page.evaluate(async()=>{
      const query=document.querySelectorAll.bind(document);let scans=0;
      document.querySelectorAll=selector=>{if(selector==='textarea')scans++;return query(selector);};
      const tip=document.createElement('span');document.body.append(tip);
      for(let i=0;i<5;i++){tip.style.left=i+'px';await new Promise(requestAnimationFrame);}
      tip.remove();delete document.querySelectorAll;return scans;
    });assert.equal(scans,0,'unrelated mutations must not scan all textareas');
    await page.screenshot({path:path.join(output,name+'.png')});
    assert.deepEqual(errors,[]);passed.push(name+': six page shells, 320/768/1440 widths, grow/shrink/cap, reset, prefill, container resize, font zoom');
    await browser.close();browser=null;
  }
  fs.writeFileSync(path.join(output,'result.json'),JSON.stringify({ok:true,passed,nativeRuntime:false},null,2));console.log(JSON.stringify({ok:true,passed}));
}
main().catch(async error=>{await recordFailure(error,{output,page,engine:engineName,phase,passed,pageErrors:errors});console.error(error);process.exitCode=1;})
  .finally(()=>cleanupResources({browser:()=>browser?.close(),api:()=>api?.dispose(),server:()=>server.kill()}));
