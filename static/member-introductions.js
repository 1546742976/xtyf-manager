"use strict";
(() => {
  const root=document.getElementById('member-introductions');if(!root)return;
  const $=id=>document.getElementById(id),esc=value=>BusinessUI.escape(value),dialog=$('intro-dialog'),form=$('intro-form');
  let version=0,loaded=false,busy=false,latest=null,page=1,sequence=0,returnFocus=null,profiles=[],canModerate=false,moderating=null;
  const reasons=new Map();
  async function api(url,body){
    const response=await BusinessUI.fetch(url,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    if(response.status===404){const e=new Error('当前服务器尚不支持成员简介，原协作功能仍可使用');e.status=404;throw e;}
    const data=await response.json();if(!response.ok||!data.ok){const e=new Error(data.error||'请求失败，草稿已保留');e.status=response.status;throw e;}return data;
  }
  function close(){if(busy)return;dialog.close();returnFocus?.focus();}
  function requireHomepage(profile){
    if(!Object.hasOwn(profile,'github_url'))throw new Error('当前服务器尚不支持 GitHub 个人主页，请更新服务器后再编辑；原协作功能仍可使用');
  }
  function legacyInfo(profile){
    const repos=profile.repositories||[];$('intro-legacy').hidden=!repos.length;
    $('intro-legacy-content').textContent=repos.map(r=>[r.name,r.url,r.description].filter(Boolean).join(' · ')).join('\n');
  }
  function moderationState(profile){$('intro-moderation-state').textContent=profile.hidden?`此资料已被隐藏：${profile.moderation_reason}。可以修改，但保存不会解除隐藏。`:'';}
  $('intro-edit').onclick=async event=>{
    returnFocus=event.currentTarget;
    try{if(!loaded){const data=await api('/api/account/introduction');requireHomepage(data.profile);version=data.profile.version;form.elements.bio.value=data.profile.bio;form.elements.github_url.value=data.profile.github_url?.replace('https://github.com/','www.github.com/')||'';legacyInfo(data.profile);moderationState(data.profile);loaded=true;}
      dialog.showModal();form.elements.bio.focus();
    }catch(error){$('intro-status').textContent=error.message;}
  };
  $('intro-close').onclick=close;dialog.addEventListener('cancel',event=>{event.preventDefault();close();});
  dialog.addEventListener('click',event=>{const r=dialog.getBoundingClientRect();if(event.target===dialog&&(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom))close();});
  form.onsubmit=async event=>{
    event.preventDefault();if(busy)return;
    busy=true;$('intro-save').disabled=true;$('intro-save-result').textContent='正在保存…';
    try{const data=await api('/api/account/introduction',{version,bio:form.elements.bio.value,github_url:form.elements.github_url.value});version=data.profile.version;moderationState(data.profile);$('intro-save-result').textContent='简介已保存';$('intro-conflict').hidden=true;latest=null;if(root.dataset.list==='1')await load();}
    catch(error){$('intro-save-result').textContent=error.message;if(error.status===409){$('intro-conflict').hidden=false;$('intro-accept-version').hidden=true;}}
    finally{busy=false;$('intro-save').disabled=false;}
  };
  $('intro-check-latest').onclick=async()=>{try{latest=(await api('/api/account/introduction')).profile;requireHomepage(latest);$('intro-latest').textContent=`最新版本 ${latest.version} · ${latest.hidden?'已隐藏：'+latest.moderation_reason:'公开'}\n${latest.bio}\n${latest.github_url||'未填写个人主页'}`;$('intro-accept-version').hidden=false;}catch(e){$('intro-save-result').textContent=e.message;}};
  $('intro-accept-version').onclick=()=>{if(!latest)return;version=latest.version;moderationState(latest);$('intro-conflict').hidden=true;$('intro-save-result').textContent='已核对最新状态，当前草稿未更改，请再次保存';};
  async function load(){
    const ticket=++sequence,search=$('intro-search'),query=new URLSearchParams({page:String(page),q:search.elements.q.value,include_hidden:search.elements.include_hidden.checked?'1':'0'});
    try{const data=await api('/api/collaboration/profiles?'+query);if(ticket!==sequence)return;profiles=data.profiles;canModerate=data.can_moderate;$('intro-hidden-filter').hidden=!canModerate;
      $('intro-list').innerHTML=profiles.map(p=>`<article class="intro-card"><div class="intro-identity"><span class="intro-initial">${esc(p.name.slice(0,1))}</span>${p.avatar_url?`<img data-profile-avatar="${esc(p.avatar_url)}" hidden alt="${esc(p.name)}的头像">`:''}<strong>${esc(p.name)}</strong></div><p>${esc(p.groups||'未分组')}</p><p class="intro-bio">${esc(p.bio)}</p>${p.github_url?`<p><a href="${esc(p.github_url)}" target="_blank" rel="noopener noreferrer">${esc(p.github_url.replace('https://github.com/','www.github.com/'))}</a></p>`:''}${p.hidden?`<p>已隐藏：${esc(p.moderation_reason)}</p>`:''}${canModerate?`<button class="btn" type="button" data-moderate="${p.member_id}">${p.hidden?'恢复展示':'隐藏简介'}</button>`:''}</article>`).join('')||'<p>暂无符合条件的成员简介。</p>';
      for(const img of $('intro-list').querySelectorAll('[data-profile-avatar]')){img.onload=()=>{img.hidden=false;img.previousElementSibling.hidden=true;};img.onerror=()=>img.remove();const url=img.dataset.profileAvatar;
        if(window.XTYF?.invoke){if(window.XTYF.imageURL)window.XTYF.imageURL(url).then(src=>{if(img.isConnected)img.src=src;}).catch(()=>img.remove());}else img.src=url;
      }
      $('intro-page').textContent=`第 ${page} 页 · 共 ${data.total} 人`;$('intro-prev').disabled=page<=1;$('intro-next').disabled=!data.has_more;$('intro-status').textContent='成员简介已更新';
    }catch(error){if(ticket===sequence)$('intro-status').textContent=error.message;}
  }
  const modDialog=$('intro-moderation-dialog'),modForm=$('intro-moderation-form');
  function closeModeration(){if(busy)return;if(moderating)reasons.set(moderating.member_id,modForm.elements.reason.value);modDialog.close();returnFocus?.focus();}
  $('intro-moderation-close').onclick=closeModeration;modDialog.addEventListener('cancel',event=>{event.preventDefault();closeModeration();});
  modForm.onsubmit=async event=>{event.preventDefault();if(busy||!moderating)return;busy=true;modForm.querySelector('[type=submit]').disabled=true;
    try{await api(`/api/collaboration/profiles/${moderating.member_id}/moderation`,{version:moderating.version,hidden:!moderating.hidden,reason:modForm.elements.reason.value});reasons.delete(moderating.member_id);modDialog.close();await load();$('intro-status').textContent='展示状态已保存';returnFocus?.focus();}catch(e){$('intro-moderation-result').textContent=e.message;}finally{busy=false;modForm.querySelector('[type=submit]').disabled=false;}};
  if(root.dataset.list==='1'){
    $('intro-search').onsubmit=e=>{e.preventDefault();page=1;load();};$('intro-search').elements.include_hidden.onchange=()=>{page=1;load();};
    $('intro-prev').onclick=()=>{page--;load();};$('intro-next').onclick=()=>{page++;load();};$('intro-refresh').onclick=load;
    $('intro-list').onclick=event=>{const button=event.target.closest('[data-moderate]');if(!button||!canModerate)return;moderating=profiles.find(p=>p.member_id===Number(button.dataset.moderate));returnFocus=button;modForm.elements.reason.value=reasons.get(moderating.member_id)||'';$('intro-moderation-result').textContent='';$('intro-moderation-target').textContent=`${moderating.hidden?'恢复':'隐藏'} ${moderating.name} 的整份简介与个人主页` ;modDialog.showModal();modForm.elements.reason.focus();};load();
  }
})();
