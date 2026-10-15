"use strict";
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

function themePage(fetch, sidebarOnly=false) {
  const element = (value) => ({value, checked: value === 'light', disabled: false, textContent:'',
    events:{}, classes:new Set(), attributes:{}, setAttribute(key,value) {this.attributes[key]=value;}, addEventListener(type,fn) {this.events[type]=fn;},
    get classList() {return {add: key=>this.classes.add(key),remove:key=>this.classes.delete(key)};}});
  const inputs = [element('light'),element('dark')], result = element(), retry = element(), meta = {};
  const toggle = element(), sidebarResult = element(), sidebarRetry = element();
  const elements = {'theme-result': sidebarOnly?null:result, 'theme-retry':sidebarOnly?null:retry,
    'sidebar-theme-result':sidebarResult, 'sidebar-theme-retry':sidebarRetry};
  let ready;
  const context = {BusinessUI:{fetch}, fetch, location:{assign(){}}, document:{documentElement:{dataset:{theme:'light'}},
    querySelector:()=>meta, querySelectorAll:selector=>selector==='[data-theme-toggle]'?[toggle]:(sidebarOnly?[]:inputs),
    getElementById:id=>elements[id] || null, addEventListener:(_,fn)=>{ready=fn;}}};
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../../static/theme.js'),'utf8'),context);
  ready();
  return {context,inputs,result,retry,meta,toggle,sidebarResult,sidebarRetry};
}
test('theme applies immediately, restores on failure and retries the selected preference', async () => {
  let fail, calls=0;
  const page = themePage(async (_url,options) => {
    assert.equal(options.method,'POST'); assert.equal(JSON.parse(options.body).theme,'dark');
    if (++calls===1) return new Promise((_,reject)=>{fail=reject;});
    return {ok:true,status:200,json:async()=>({ok:true,theme:'dark'})};
  });
  const pending=page.inputs[1].events.change();
  assert.equal(page.context.document.documentElement.dataset.theme,'dark');
  assert.equal(page.inputs[0].disabled,true);
  assert.equal(page.toggle.disabled,true);
  assert.equal(page.toggle.attributes['aria-label'],'切换为银白主题');
  fail(new Error('网络连接失败'));
  await pending;
  assert.equal(page.context.document.documentElement.dataset.theme,'light');
  assert.equal(page.inputs[0].checked,true);
  assert.match(page.result.textContent,/已恢复原主题/);
  assert.equal(page.toggle.attributes['aria-label'],'切换为黑色主题');
  await page.retry.events.click();
  // Retry starts asynchronously; observe the completed save on the next turn.
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(page.context.document.documentElement.dataset.theme,'dark');
  assert.equal(page.meta.content,'#0b0b0c');
  assert.equal(page.inputs[1].checked,true);
  assert.equal(page.inputs[1].disabled,false);
  assert.match(page.result.textContent,/外观已保存/);
});

test('sidebar switches without account controls and prevents concurrent saves', async () => {
  let finish,calls=0;
  const page=themePage(async (_url,options)=>{
    calls++;
    assert.equal(JSON.parse(options.body).theme,'dark');
    return new Promise(resolve=>{finish=()=>resolve({ok:true,status:200,json:async()=>({ok:true,theme:'dark'})});});
  },true);
  const pending=page.toggle.events.click();
  await page.toggle.events.click();
  assert.equal(calls,1);
  assert.equal(page.toggle.disabled,true);
  finish();await pending;
  assert.equal(page.context.document.documentElement.dataset.theme,'dark');
  assert.equal(page.toggle.disabled,false);
  assert.match(page.sidebarResult.textContent,/外观已保存/);
});
