"use strict";
const WORKSPACE_PALETTES = {
  silver:{mode:'light',name:'Mistral 暖橙',bg:'#f6f4f1'}, 'rm-light':{mode:'light',name:'RM 机甲红蓝',bg:'#f6f7fb'},
  mist:{mode:'light',name:'云雾绿',bg:'#f2f7f4'}, paw:{mode:'light',name:'奶霜猫爪粉',bg:'#fff3f2'},
  space:{mode:'dark',name:'GitHub 暗黑',bg:'#000000'}, 'rm-dark':{mode:'dark',name:'黑白极简',bg:'#090909'},
  graphite:{mode:'dark',name:'石墨灰金',bg:'#191a1d'}, xueli:{mode:'dark',name:'雪梨星语',bg:'#231b20'},
  'custom-light':{mode:'light',name:'自定义浅色',bg:'#f5f7fa'},
  'custom-dark':{mode:'dark',name:'自定义深色',bg:'#15191f'},
};
const paletteFor = (p,theme) => p[theme + '_custom'] ? 'custom-' + theme : p[theme + '_theme'];
function resolvedTheme(p, systemDark) {return p.mode === 'system' ? (systemDark ? 'dark' : 'light') : p.mode;}
function applyWorkspaceTheme(p, systemDark) {
  const theme = resolvedTheme(p, systemDark), palette = paletteFor(p, theme), root = document.documentElement;
  Object.assign(root.dataset,{theme,palette,themeMode:p.mode,lightTheme:p.light_theme,darkTheme:p.dark_theme,
    lightCustom:String(p.light_custom),darkCustom:String(p.dark_custom),lightAccent:p.light_accent,darkAccent:p.dark_accent});
  if (palette.startsWith('custom-')) root.style?.setProperty?.('--custom-accent',p[theme + '_accent']);
  else root.style?.removeProperty?.('--custom-accent');
  document.querySelectorAll('[data-custom-accent]').forEach(el => {el.value=p[el.dataset.customAccent + '_accent'];});
  for(const mode of ['light','dark'])document.querySelectorAll('[data-preview="custom-' + mode + '"]').forEach(el =>
    el.style?.setProperty?.('--pv-accent',p[mode + '_accent']));
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = document.documentElement.dataset.page === 'login' ? '#f5f5f5' : WORKSPACE_PALETTES[palette].bg;
  document.querySelectorAll('[data-theme-mode-choice]').forEach(el => {el.checked=el.value===p.mode;});
  document.querySelectorAll('[data-palette-choice]').forEach(el => el.setAttribute('aria-pressed',String(el.dataset.paletteChoice===paletteFor(p,el.dataset.paletteMode))));
  document.querySelectorAll('[data-theme-toggle]').forEach(el => {
    const next=theme==='dark'?'light':'dark';
    const label=`切换为${WORKSPACE_PALETTES[paletteFor(p,next)].name}${p.mode==='system'?'，退出跟随系统':''}`;
    el.setAttribute('aria-label',label); el.title=label;
  });
  document.querySelectorAll('[data-current-palette]').forEach(el => {el.textContent=WORKSPACE_PALETTES[palette].name;});
  document.dispatchEvent(new CustomEvent('workspace-theme-change',{detail:{theme,palette}}));
}
async function saveThemePreference(p) {
  const response=await BusinessUI.fetch('/api/account/preferences',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});
  const data=await response.json().catch(()=>({}));
  if (!response.ok || !data.ok || !['light','dark','system'].includes(data.mode) || WORKSPACE_PALETTES[data.light_theme]?.mode!=='light' || WORKSPACE_PALETTES[data.dark_theme]?.mode!=='dark'
    || typeof data.light_custom!=='boolean' || typeof data.dark_custom!=='boolean'
    || !/^#[0-9a-f]{6}$/i.test(data.light_accent) || !/^#[0-9a-f]{6}$/i.test(data.dark_accent)) throw new Error(data.error||'主题保存失败，请重试');
  return {mode:data.mode,light_theme:data.light_theme,dark_theme:data.dark_theme,
    light_custom:data.light_custom,dark_custom:data.dark_custom,light_accent:data.light_accent,dark_accent:data.dark_accent};
}
document.addEventListener('DOMContentLoaded',()=>{
  const root=document.documentElement, media=window.matchMedia?.('(prefers-color-scheme: dark)');
  let preferences={mode:root.dataset.themeMode||root.dataset.theme||'light',light_theme:root.dataset.lightTheme||'silver',dark_theme:root.dataset.darkTheme||'space',
    light_custom:root.dataset.lightCustom==='true',dark_custom:root.dataset.darkCustom==='true',
    light_accent:root.dataset.lightAccent||'#3266c7',dark_accent:root.dataset.darkAccent||'#80aaff'};
  const modes=[...document.querySelectorAll('[data-theme-mode-choice]')], palettes=[...document.querySelectorAll('[data-palette-choice]')], toggles=[...document.querySelectorAll('[data-theme-toggle]')];
  const colors=[...document.querySelectorAll('[data-custom-accent]')];
  const controls=[...modes,...palettes,...colors,...toggles],results=[...document.querySelectorAll('[data-theme-result]')],retries=[...document.querySelectorAll('[data-theme-retry]')];
  const notice=document.querySelector('[data-theme-notice]'),noticeText=document.querySelector('[data-theme-notice-text]');
  let noticeTimer,noticeTrigger;
  const hideNotice=()=>{clearTimeout(noticeTimer);if(notice)notice.hidden=true;};
  const dismissLater=()=>{clearTimeout(noticeTimer);noticeTimer=setTimeout(hideNotice,5000);};
  function showNotice(trigger) {
    if(!notice||!noticeText)return;
    noticeTrigger=trigger;notice.hidden=false;
    noticeText.textContent='更多主题颜色请切换至账户设置页面更改';
    dismissLater();
  }
  notice?.addEventListener('mouseenter',()=>clearTimeout(noticeTimer));
  notice?.addEventListener('mouseleave',()=>{if(!notice.contains(document.activeElement))dismissLater();});
  notice?.addEventListener('focusin',()=>clearTimeout(noticeTimer));
  notice?.addEventListener('focusout',dismissLater);
  document.querySelector('[data-theme-notice-close]')?.addEventListener('click',()=>{hideNotice();noticeTrigger?.focus({preventScroll:true});});
  let saving=false,pending=null;
  const apply=()=>applyWorkspaceTheme(preferences,Boolean(media?.matches));
  apply();
  if(media?.addEventListener) media.addEventListener('change',apply); else media?.addListener?.(apply);
  async function save(next) {
    if(saving)return;
    const returnFocus=document.activeElement;
    saving=true; pending={...next}; const previous={...preferences}; preferences={...next}; apply();
    const disabled=controls.map(el=>el.disabled); controls.forEach(el=>{el.disabled=true;}); retries.forEach(el=>{el.hidden=true;});
    results.forEach(el=>{el.classList.remove('error');el.textContent='正在保存外观…';});
    try {
      preferences=await saveThemePreference(next);apply();pending=null;
      results.forEach(el=>{el.textContent='外观已保存，其他设备下次打开或刷新时同步。';});
    } catch(error) {
      preferences=previous;apply();
      results.forEach(el=>{el.classList.add('error');el.textContent=`${error.message}，已恢复原主题。`;});retries.forEach(el=>{el.hidden=false;});
    } finally {
      saving=false;controls.forEach((el,i)=>{el.disabled=disabled[i];});
      if(returnFocus?.isConnected && returnFocus.getClientRects().length
          && [document.body,document.documentElement].includes(document.activeElement)) {
        returnFocus.focus({preventScroll:true});
      }
    }
  }
  modes.forEach(el=>el.addEventListener('change',()=>save({...preferences,mode:el.value})));
  palettes.forEach(el=>el.addEventListener('click',()=>{
    const mode=el.dataset.paletteMode,custom=el.dataset.paletteChoice==='custom-'+mode;
    return save({...preferences,mode,[mode+'_custom']:custom,
      ...(custom?{}:{[mode+'_theme']:el.dataset.paletteChoice})});
  }));
  colors.forEach(el=>el.addEventListener('change',()=>{
    const mode=el.dataset.customAccent;
    return save({...preferences,mode,[mode+'_custom']:true,[mode+'_accent']:el.value});
  }));
  toggles.forEach(el=>el.addEventListener('click',()=>{
    if(saving)return;
    showNotice(el);
    return save({...preferences,mode:root.dataset.theme==='dark'?'light':'dark'});
  }));
  retries.forEach(el=>el.addEventListener('click',()=>pending&&save(pending)));
  document.addEventListener('keydown',event=>{if(event.key==='Escape'&&notice&&!notice.hidden){
    const focused=notice.contains(document.activeElement);hideNotice();
    if(focused)noticeTrigger?.focus({preventScroll:true});
  }});
});
