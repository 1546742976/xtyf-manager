'use strict';
const assert=require('node:assert/strict'),{spawn}=require('node:child_process'),fs=require('node:fs'),path=require('node:path');
const {chromium,webkit,devices}=require('playwright');
const root=path.resolve(__dirname,'../..'),out=path.join(root,'.venv','palette-workspace-check');fs.mkdirSync(out,{recursive:true});
const palettes=['silver','rm-light','mist','paw','space','rm-dark','graphite','xueli'];
const widths=[375,390,767,768,850,1280];
// The vector fills its native viewBox; ensure rounded logo hosts cannot cut it off.
async function assertCompleteLogo(page, selector) {
 const logo=await page.locator(selector).evaluate(host=>{
  const img=host.querySelector('img.brand-logo');
  const style=getComputedStyle(img), outer=getComputedStyle(host);
  return {
   path:new URL(img.currentSrc||img.src).pathname,
   loaded:img.complete&&img.naturalWidth>0&&img.naturalHeight>0,
   clip:outer.overflow, fit:style.objectFit,
   padding:[style.paddingTop,style.paddingRight,style.paddingBottom,style.paddingLeft].map(parseFloat),
  };
 });
 assert.equal(logo.path,'/static/brand/xtyf-logo.svg');
 assert.equal(logo.loaded,true);
 assert.equal(logo.clip,'visible');
 assert.equal(logo.fit,'contain');
 assert.ok(logo.padding.every(value=>value>=3),JSON.stringify(logo));
}
(async()=>{
 const server=spawn(process.env.REVIEW_PYTHON||'python',['-B',path.join(__dirname,'theme_server.py')],{cwd:root,env:process.env,stdio:['ignore','pipe','pipe']});let browser,log='';const results=[];
 try{
  const base=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error(log)),30000);server.stderr.on('data',d=>log+=d);server.stdout.on('data',d=>{const m=String(d).match(/THEME_DEMO_URL=(\S+)/);if(m){clearTimeout(timer);resolve(m[1]);}});server.once('exit',code=>{clearTimeout(timer);reject(Error('server '+code+' '+log));});});
  browser=await chromium.launch({headless:true});const context=await browser.newContext({viewport:{width:1280,height:900},reducedMotion:'reduce'});const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await context.route('**/discussions/api/**',r=>r.fulfill({json:{ok:true,source:'api',items:[]}}));
  await page.goto(base+'/login');await assertCompleteLogo(page,'.login-mark');await page.locator('[name=username]').fill('admin');await page.locator('[name=password]').fill('theme-demo-password');await page.locator('#login-form button[type=submit]').click();await page.waitForURL(base+'/');
  const api=async(url,data,method='POST')=>page.evaluate(async({url,data,method})=>{const r=await fetch(url,{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});return {status:r.status,...await r.json()};},{url,data,method});
  const today=new Date(new Date().toLocaleString('en-US',{timeZone:'Asia/Shanghai'}));today.setDate(today.getDate()+24);const target=[today.getFullYear(),String(today.getMonth()+1).padStart(2,'0'),String(today.getDate()).padStart(2,'0')].join('-');
  assert.equal((await api('/api/workspace/countdown',{title:'完整形态考核',target_date:target,version:1},'PUT')).ok,true);await page.reload();
  for(const [i,palette] of palettes.entries()){
   await page.setViewportSize({width:1280,height:900});
   await page.goto(base+'/account');
   await Promise.all([page.waitForResponse(r=>r.url().endsWith('/api/account/preferences')&&r.request().method()==='POST'),page.locator(`[data-palette-choice="${palette}"]`).press("Enter")]);
   await page.waitForFunction(p=>document.documentElement.dataset.palette===p&&document.querySelector('[data-theme-toggle]').disabled===false,palette);
   assert.equal(await page.locator(`[data-palette-choice="${palette}"]`).evaluate(el=>el===document.activeElement),true);
   if(palette==='rm-dark'){
    assert.equal(await page.locator('[data-palette-choice="rm-dark"]').getAttribute('data-palette-mode'),'dark');
    assert.match(await page.locator('[data-palette-choice="rm-dark"]').innerText(),/黑白极简/);
   }
   await page.goto(base+'/');
   await assertCompleteLogo(page,'.ws-brand-mark');
   assert.equal(await page.locator('[data-palette-choice]').count(),0,'workspace shortcuts do not expose palette choices');
   const contrast=await page.evaluate(()=>{
    const s=getComputedStyle(document.documentElement);const rgb=v=>{const e=document.createElement('span');e.style.color=v;document.body.append(e);const a=getComputedStyle(e).color.match(/[\d.]+/g).slice(0,3).map(Number);e.remove();return a;};
    const lum=c=>c.map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4).reduce((s,v,i)=>s+v*[.2126,.7152,.0722][i],0);
    const ratio=(a,b)=>{a=lum(rgb(s.getPropertyValue(a)));b=lum(rgb(s.getPropertyValue(b)));return (Math.max(a,b)+.05)/(Math.min(a,b)+.05);};
    return {text:ratio('--text','--card'),muted:ratio('--muted','--card'),button:ratio('--on-primary','--primary')};
   });
   for(const [kind,value]of Object.entries(contrast)) assert.ok(value>=4.5,`${palette} ${kind} contrast ${value}`);
   for(const width of widths){await page.setViewportSize({width,height:900});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`${palette} overflow ${width}`);if([375,1280].includes(width))await page.screenshot({path:path.join(out,`${palette}-${width}.png`),fullPage:true});}
   results.push({palette,widths,contrast});
   // Independent finance shell, task center and account all consume the same selected palette.
   for(const url of ['/invoices/','/tasks','/tasks?type=daily','/account']){
    await page.goto(base+url);assert.equal(await page.locator('html').getAttribute('data-palette'),palette);
    await page.setViewportSize({width:375,height:812});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`${palette} ${url} overflow`);
   }
   await page.goto(base+'/');
  }
  await page.setViewportSize({width:1280,height:900});
  const topToggle=page.locator('.ws-top-theme-toggle[data-theme-toggle]'),sideToggle=page.locator('#workspace-sidebar [data-theme-toggle]'),notice=page.locator('[data-theme-notice]');
  const originalPalette=await page.locator('html').getAttribute('data-palette'),lightPalette=await page.locator('html').getAttribute('data-light-theme');
  assert.equal(await topToggle.getAttribute('aria-label'),await sideToggle.getAttribute('aria-label'));
  await topToggle.click();await page.waitForFunction(p=>document.documentElement.dataset.palette===p&&[...document.querySelectorAll('[data-theme-toggle]')].every(el=>!el.disabled),lightPalette);
  await notice.waitFor({state:'visible'});assert.equal(await page.locator('[data-theme-notice-text]').getAttribute('role'),'status');assert.equal(await page.locator('[data-theme-notice-text]').textContent(),'更多主题颜色请切换至账户设置页面更改');
  assert.equal(await topToggle.getAttribute('aria-label'),await sideToggle.getAttribute('aria-label'));
  await page.locator('[data-theme-notice-close]').click();await notice.waitFor({state:'hidden'});assert.equal(await topToggle.evaluate(el=>el===document.activeElement),true);
  await sideToggle.click();await page.waitForFunction(p=>document.documentElement.dataset.palette===p&&[...document.querySelectorAll('[data-theme-toggle]')].every(el=>!el.disabled),originalPalette);
  await notice.waitFor({state:'visible'});assert.equal(await page.locator('[data-theme-notice-text]').textContent(),'更多主题颜色请切换至账户设置页面更改');assert.equal(await topToggle.getAttribute('aria-label'),await sideToggle.getAttribute('aria-label'));
  await page.mouse.move(640,450);await notice.waitFor({state:'hidden',timeout:7000});
  await page.goto(base+'/account');
  await page.route('**/api/account/preferences',r=>r.fulfill({status:503,json:{ok:false,error:'模拟保存失败'}}));await page.locator('[data-palette-choice=silver]').click();await page.locator('#theme-result').filter({hasText:'已恢复原主题'}).waitFor();assert.equal(await page.locator('html').getAttribute('data-palette'),'xueli');await page.unroute('**/api/account/preferences');await page.locator('#theme-retry').click();await page.waitForFunction(()=>document.documentElement.dataset.palette==='silver'&&!document.querySelector('[data-theme-toggle]').disabled);
  await page.emulateMedia({colorScheme:'dark'});await page.locator('[data-theme-mode-choice][value=system]').check();await page.waitForFunction(()=>document.documentElement.dataset.palette==='xueli'&&!document.querySelector('[data-theme-toggle]').disabled);await page.reload();assert.equal(await page.locator('html').getAttribute('data-palette'),'xueli');await page.emulateMedia({colorScheme:'light'});await page.waitForFunction(()=>document.documentElement.dataset.palette==='silver');
  await page.setViewportSize({width:390,height:844});await page.evaluate(()=>{document.documentElement.style.fontSize='20px';});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  assert.deepEqual(errors,[]);await browser.close();browser=null;
  const webkitPath=process.env.REVIEW_WEBKIT_PATH;let webkitAvailable=fs.existsSync(webkitPath||webkit.executablePath());
  if(webkitAvailable){browser=await webkit.launch({headless:true,...(webkitPath?{executablePath:webkitPath}:{})});const p=await browser.newPage({...devices['iPhone 13']});await p.goto(base+'/login');await p.locator('[name=username]').fill('admin');await p.locator('[name=password]').fill('theme-demo-password');await p.locator('#login-form button[type=submit]').click();await p.waitForURL(base+'/');await p.goto(base+'/account');await p.locator('[data-palette-choice=xueli]').click();await p.waitForFunction(()=>document.documentElement.dataset.palette==='xueli'&&!document.querySelector('[data-theme-toggle]').disabled);await p.goto(base+'/');assert.ok(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await p.screenshot({path:path.join(out,'webkit-iphone13-xueli.png'),fullPage:true});results.push({engine:'WebKit',device:'iPhone 13 emulation',passed:true});}
  fs.writeFileSync(path.join(out,'result.json'),JSON.stringify({ok:true,results,webkitAvailable,realDevices:false},null,2));console.log('PALETTES_OK '+JSON.stringify({palettes:8,widths:6,results,realDevices:false}));
 }catch(e){console.error(e);console.error(log.slice(-2000));process.exitCode=1;}finally{if(browser)await browser.close();server.kill();}
})();
