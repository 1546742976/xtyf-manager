"use strict";
const WORKSPACE_PALETTES = {
  silver:{mode:'light',name:'银白蓝',bg:'#f5f5f7'}, 'rm-light':{mode:'light',name:'RM 机甲红蓝',bg:'#f6f7fb'},
  mist:{mode:'light',name:'云雾绿',bg:'#f2f7f4'}, paw:{mode:'light',name:'奶霜猫爪粉',bg:'#fff3f2'},
  space:{mode:'dark',name:'深空蓝',bg:'#111726'}, 'rm-dark':{mode:'dark',name:'RM 机甲红蓝',bg:'#101b2b'},
  graphite:{mode:'dark',name:'石墨灰金',bg:'#191a1d'}, xueli:{mode:'dark',name:'雪梨星语',bg:'#231b20'},
};
function resolvedTheme(p, systemDark) {return p.mode === 'system' ? (systemDark ? 'dark' : 'light') : p.mode;}
function applyWorkspaceTheme(p, systemDark) {
  const theme = resolvedTheme(p, systemDark), palette = p[`${theme}_theme`];
  Object.assign(document.documentElement.dataset,{theme,palette,themeMode:p.mode,lightTheme:p.light_theme,darkTheme:p.dark_theme});
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = WORKSPACE_PALETTES[palette].bg;
  document.querySelectorAll('[data-theme-mode-choice]').forEach(el => {el.checked=el.value===p.mode;});
  document.querySelectorAll('[data-palette-choice]').forEach(el => el.setAttribute('aria-pressed',String(el.dataset.paletteChoice===p[`${el.dataset.paletteMode}_theme`])));
  document.querySelectorAll('[data-theme-toggle]').forEach(el => {
    const next=theme==='dark'?'light':'dark';
    const label=`切换为${WORKSPACE_PALETTES[p[`${next}_theme`]].name}${p.mode==='system'?'，退出跟随系统':''}`;
    el.setAttribute('aria-label',label); el.title=label;
  });
  document.querySelectorAll('[data-current-palette]').forEach(el => {el.textContent=WORKSPACE_PALETTES[palette].name;});
  document.dispatchEvent(new CustomEvent('workspace-theme-change',{detail:{theme,palette}}));
}
async function saveThemePreference(p) {
  const response=await BusinessUI.fetch('/api/account/preferences',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});
  const data=await response.json().catch(()=>({}));
  if (!response.ok || !data.ok || !['light','dark','system'].includes(data.mode) || WORKSPACE_PALETTES[data.light_theme]?.mode!=='light' || WORKSPACE_PALETTES[data.dark_theme]?.mode!=='dark') throw new Error(data.error||'主题保存失败，请重试');
  return {mode:data.mode,light_theme:data.light_theme,dark_theme:data.dark_theme};
}
document.addEventListener('DOMContentLoaded',()=>{
  const root=document.documentElement, media=window.matchMedia?.('(prefers-color-scheme: dark)');
  let preferences={mode:root.dataset.themeMode||root.dataset.theme||'light',light_theme:root.dataset.lightTheme||'silver',dark_theme:root.dataset.darkTheme||'space'};
  const modes=[...document.querySelectorAll('[data-theme-mode-choice]')], palettes=[...document.querySelectorAll('[data-palette-choice]')], toggles=[...document.querySelectorAll('[data-theme-toggle]')];
  const controls=[...modes,...palettes,...toggles],results=[...document.querySelectorAll('[data-theme-result]')],retries=[...document.querySelectorAll('[data-theme-retry]')];
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
  palettes.forEach(el=>el.addEventListener('click',()=>save({...preferences,mode:el.dataset.paletteMode,[`${el.dataset.paletteMode}_theme`]:el.dataset.paletteChoice})));
  toggles.forEach(el=>el.addEventListener('click',()=>save({...preferences,mode:root.dataset.theme==='dark'?'light':'dark'})));
  retries.forEach(el=>el.addEventListener('click',()=>pending&&save(pending)));
  document.querySelectorAll('[data-theme-menu]').forEach(menu=>{
    document.addEventListener('keydown',event=>{if(menu.open&&event.key==='Escape'){menu.open=false;menu.querySelector('summary').focus();}});
    document.addEventListener('click',event=>{if(menu.open&&!menu.contains(event.target))menu.open=false;});
  });
});
