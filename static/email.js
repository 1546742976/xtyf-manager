"use strict";
(() => {
  const esc=value=>BusinessUI.escape(value);
  async function api(url,data){
    const r=await BusinessUI.fetch(url,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}),body=await r.json();
    if(!r.ok||!body.ok)throw new Error(body.error||'请求失败');return body;
  }
  const form=document.getElementById('email-form'),result=document.getElementById('email-result');
  if(form){
    const codeForm=document.getElementById('email-code-form'),codeResult=document.getElementById('email-code-result');
    let loaded=false;
    const load=async()=>{
      const data=await api('/api/account/email'),email=data.email;
      document.getElementById('email-state').textContent=!data.available?'邮件服务尚未配置，请联系管理员':`${data.mode==='preview'?'模拟模式 · ':''}${email?.address||'尚未绑定邮箱'} · ${email?.verified?'已验证':'未验证'}${email?.paused?' · 管理员已暂停每日提醒':''}`;
      codeForm.hidden=!email?.pending;
      document.getElementById('email-code-address').textContent=email?.pending?`验证码已申请发送至 ${email.address}，30 分钟内有效；可在上方重新发送。`:'';
      if(!loaded){form.elements.email.value=email?.address||'';loaded=true;}
    };
    const refresh=()=>BusinessUI.action('email-load',[document.getElementById('email-refresh')],load,text=>{result.textContent=text;});
    document.getElementById('email-refresh').addEventListener('click',refresh);
    form.addEventListener('submit',event=>{event.preventDefault();BusinessUI.action('email-request',form.querySelectorAll('button'),async()=>{
      const data=await api('/api/account/email/request',{email:form.elements.email.value});
      result.textContent=data.message;codeResult.textContent='';codeForm.elements.code.value='';await load();
    },text=>{result.textContent=text;});});
    codeForm.addEventListener('submit',event=>{event.preventDefault();BusinessUI.action('email-verify',codeForm.querySelectorAll('button'),async()=>{
      const data=await api('/api/account/email/verify',{code:codeForm.elements.code.value.trim()});
      result.textContent=data.message;codeResult.textContent='';codeForm.elements.code.value='';await load();
    },text=>{codeResult.textContent=text;});});
    refresh();
  }
  if(document.getElementById('mail-admin')){
    const result=document.getElementById('mail-result');let page=1;
    const states={pending:'待发送',sending:'发送中',sent:'SMTP 已接受',failed:'失败',unknown:'结果未知',skipped:'跳过',simulated:'模拟完成（未投递）'};
    const load=async()=>{
      const data=await api('/api/admin/mail?page='+page);
      result.textContent=`邮件服务：${data.available?data.mode==='smtp'?'SMTP 已配置':'模拟模式':'未启用或配置不完整'}`;
      document.getElementById('mail-members').innerHTML=data.members.map(m=>`<article class="daily-card"><strong>${esc(m.name)}</strong><p>${esc(m.address||'未绑定')} · ${m.verified?'已验证':'未验证'}${!m.is_active||m.deleted_at?' · 账户停用':''}</p><button class="btn" type="button" data-mail-member="${m.id}" data-paused="${m.paused?'0':'1'}">${m.paused?'恢复每日提醒':'暂停每日提醒'}</button></article>`).join('');
      document.getElementById('mail-jobs').innerHTML=data.jobs.map(j=>`<article class="daily-update"><strong>${esc(j.name)} · ${esc({verify:'邮箱验证',morning:'个人任务早报',daily:'团队项目晚报',test:'测试邮件'}[j.kind]||j.kind)}</strong><p>${esc(j.day)} · ${esc(states[j.state])} · 尝试 ${j.attempts} 次</p><p>${esc(j.error)}</p></article>`).join('')||'<p>暂无发送记录</p>';
      document.getElementById('mail-prev').disabled=page<=1;document.getElementById('mail-next').disabled=!data.has_more;document.getElementById('mail-page').textContent='第 '+page+' 页';
    };
    const reload=()=>BusinessUI.action('mail-admin-load',[document.getElementById('mail-refresh')],load,text=>{result.textContent=text;});
    document.getElementById('mail-refresh').addEventListener('click',reload);
    document.getElementById('mail-prev').addEventListener('click',()=>{page--;reload();});document.getElementById('mail-next').addEventListener('click',()=>{page++;reload();});
    document.getElementById('mail-test').addEventListener('click',event=>BusinessUI.action('mail-test',[event.currentTarget],async()=>{const data=await api('/api/admin/mail/test',{});result.textContent=data.message;},text=>{result.textContent=text;}));
    document.getElementById('mail-members').addEventListener('click',event=>{
      const button=event.target.closest('[data-mail-member]');if(!button)return;
      BusinessUI.action('mail-pause:'+button.dataset.mailMember,[button],async()=>{await api(`/api/admin/mail/${button.dataset.mailMember}/pause`,{paused:button.dataset.paused==='1'});await load();},text=>{result.textContent=text;});
    });reload();
  }
})();
