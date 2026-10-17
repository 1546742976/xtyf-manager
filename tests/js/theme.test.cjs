'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
function page(fetch,initial={}) {
 const element=(props={})=>Object.assign({disabled:false,checked:false,hidden:false,textContent:'',dataset:{},events:{},attributes:{},classes:new Set(),setAttribute(k,v){this.attributes[k]=v;},addEventListener(k,v){this.events[k]=v;},get classList(){return {add:k=>this.classes.add(k),remove:k=>this.classes.delete(k)};}},props);
 const modes=['light','dark','system'].map(value=>element({value})), palettes=['silver','rm-light','mist','paw','space','rm-dark','graphite','xueli'].map((id,i)=>element({dataset:{paletteChoice:id,paletteMode:i<4?'light':'dark'}}));
 const toggle=element(),topToggle=element(),result=element(),retry=element(),meta={},label=element(),notice=element({hidden:true}),noticeText=element(),noticeClose=element();let ready;const changes=[];
 const media={matches:false,addEventListener(t,fn){this.update=fn;}};
 const selectors={'[data-theme-mode-choice]':modes,'[data-palette-choice]':palettes,'[data-theme-toggle]':[toggle,topToggle],'[data-theme-result]':[result],'[data-theme-retry]':[retry],'[data-current-palette]':[label]};
 const single={'meta[name="theme-color"]':meta,'[data-theme-notice]':notice,'[data-theme-notice-text]':noticeText,'[data-theme-notice-close]':noticeClose};
 const context={setTimeout(){},clearTimeout(){},BusinessUI:{fetch},window:{matchMedia:()=>media},CustomEvent:class {constructor(type,options){this.type=type;this.detail=options.detail;}},document:{documentElement:{dataset:{theme:'light',themeMode:'light',lightTheme:'silver',darkTheme:'space',...initial}},querySelector:s=>single[s]||null,querySelectorAll:s=>selectors[s]||[],addEventListener(t,fn){if(t==='DOMContentLoaded')ready=fn;},dispatchEvent:event=>changes.push(event.detail)}};
 vm.createContext(context);vm.runInContext(fs.readFileSync(path.join(__dirname,'../../static/theme.js'),'utf8'),context);ready();
 return {context,modes,palettes,toggle,topToggle,notice,noticeText,result,retry,meta,media,changes,label,root:context.document.documentElement.dataset};
}
const success=async(_url,options)=>({ok:true,json:async()=>({ok:true,...JSON.parse(options.body)})});
test('eight palettes select and preserve opposite mode preference',async()=>{
 const p=page(success);
 for(const button of p.palettes) {
  await button.events.click();assert.equal(p.root.palette,button.dataset.paletteChoice);assert.equal(button.attributes['aria-pressed'],'true');assert.equal(p.toggle.disabled,false);
 }
 assert.equal(p.root.lightTheme,'paw');assert.equal(p.root.darkTheme,'xueli');
 await p.toggle.events.click();assert.equal(p.root.palette,'paw');await p.topToggle.events.click();assert.equal(p.root.palette,'xueli');
 assert.equal(p.topToggle.attributes['aria-label'],p.toggle.attributes['aria-label']);
 assert.equal(p.notice.hidden,false);assert.equal(p.noticeText.textContent,'更多主题颜色请切换至账户设置页面更改');
});
test('failed optimistic save restores full preference and explicit retry succeeds',async()=>{
 let reject,calls=0;const p=page(async(u,o)=>++calls===1?new Promise((_,r)=>{reject=r;}):success(u,o));
 const saving=p.palettes[7].events.click();assert.equal(p.root.palette,'xueli');assert.equal(p.toggle.disabled,true);
 await p.toggle.events.click();assert.equal(calls,1);reject(new Error('网络失败'));await saving;
 assert.equal(p.root.palette,'silver');assert.equal(p.root.darkTheme,'space');assert.match(p.result.textContent,/已恢复原主题/);assert.equal(p.retry.hidden,false);
 await p.retry.events.click();assert.equal(p.root.palette,'xueli');assert.equal(p.retry.hidden,true);assert.equal(p.meta.content,'#231b20');
});
test('system changes apply both remembered themes without writes; toggle leaves system',async()=>{
 let calls=0;const p=page(async(u,o)=>{calls++;return success(u,o);},{themeMode:'system',lightTheme:'paw',darkTheme:'graphite'});
 assert.equal(p.root.palette,'paw');p.media.matches=true;p.media.update();assert.equal(p.root.palette,'graphite');assert.equal(calls,0);
 assert.match(p.toggle.attributes['aria-label'],/退出跟随系统/);await p.toggle.events.click();assert.equal(p.root.themeMode,'light');assert.equal(p.root.palette,'paw');
 p.media.matches=false;p.media.update();assert.equal(calls,1);assert.equal(p.root.palette,'paw');
});
test('invalid successful response is treated as failed save and restored',async()=>{
 const p=page(async()=>({ok:true,json:async()=>({ok:true,mode:'dark',light_theme:'xueli',dark_theme:'bad'})}));
 await p.toggle.events.click();assert.equal(p.root.palette,'silver');assert.match(p.result.textContent,/保存失败/);
});
test('system resolution occurs before DOM ready and never uses browser storage',()=>{
 const context={window:{matchMedia:()=>({matches:true})},document:{documentElement:{dataset:{themeMode:'system',theme:'light',lightTheme:'mist',darkTheme:'graphite'}}}};
 vm.createContext(context);vm.runInContext(fs.readFileSync(path.join(__dirname,'../../static/theme-init.js'),'utf8'),context);
 assert.equal(context.document.documentElement.dataset.theme,'dark');assert.equal(context.document.documentElement.dataset.palette,'graphite');
});
