"use strict";
(() => {
  const esc=value=>BusinessUI.escape(value);
  async function api(url,data){
    const r=await BusinessUI.fetch(url,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}),body=await r.json();
    if(!r.ok||!body.ok)throw new Error(body.error||'请求失败');return body;
  }
  const prefForm=document.getElementById('email-preferences-form');
  function prefsFromForm(form){
    const field=name=>form.elements.namedItem(name);
    return {
      morning_interval_hours:Math.round(Number(field('morning_interval_days').value)*24),
      daily_interval_days:Number(field('daily_interval_days').value),
      countdown_interval_days:Number(field('countdown_interval_days').value),
      morning_enabled:field('morning_enabled').checked,
      daily_enabled:field('daily_enabled').checked,
      countdown_enabled:field('countdown_enabled').checked,
      ...(field('assignment_enabled')&&!field('assignment_enabled').disabled?{assignment_enabled:field('assignment_enabled').checked}:{})
    };
  }
  function fillPrefs(form,p){
    const field=name=>form.elements.namedItem(name);
    field('morning_interval_days').value=p.morning_interval_hours/24;
    field('daily_interval_days').value=p.daily_interval_days;
    field('countdown_interval_days').value=p.countdown_interval_days;
    for(const key of ['morning_enabled','daily_enabled','countdown_enabled'])field(key).checked=!!p[key];
    if(field('assignment_enabled')){field('assignment_enabled').checked=p.assignment_enabled===true;field('assignment_enabled').disabled=p.assignment_enabled===undefined;}
  }
  const form=document.getElementById('email-form'),result=document.getElementById('email-result');
  if(form){
    const codeForm=document.getElementById('email-code-form'),codeResult=document.getElementById('email-code-result');
    let loaded=false,prefsLoaded=false;
    const load=async()=>{
      const data=await api('/api/account/email'),email=data.email;
      document.getElementById('email-state').textContent=!data.available?'邮件服务尚未配置，请联系管理员':`${data.mode==='preview'?'模拟模式 · ':''}${email?.address||'尚未绑定邮箱'} · ${email?.verified?'已验证':'未验证'}${email?.paused?' · 管理员已暂停每日提醒':''}`;
      codeForm.hidden=!email?.pending;
      document.getElementById('email-code-address').textContent=email?.pending?`验证码已申请发送至 ${email.address}，30 分钟内有效；可在上方重新发送。`:'';
      if(!loaded){form.elements.email.value=email?.address||'';loaded=true;}
      if(prefForm&&!prefsLoaded){fillPrefs(prefForm,data.preferences);prefsLoaded=true;}
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
    if(prefForm)prefForm.addEventListener('submit',event=>{event.preventDefault();BusinessUI.action(
      'email-preferences',prefForm.querySelectorAll('button'),async()=>{
        await api('/api/account/email/preferences',prefsFromForm(prefForm));
        document.getElementById('email-preferences-result').textContent='偏好已保存，指派邮件由后台尽快处理，汇总邮件按所设周期发送';
        prefsLoaded=false;await load();
      },text=>{document.getElementById('email-preferences-result').textContent=text;}
    );});
    refresh();
  }
  if(document.getElementById('mail-admin')){
    const result=document.getElementById('mail-result');let page=1;
    const states={pending:'待发送',sending:'发送中',sent:'SMTP 已接受',failed:'失败',unknown:'结果未知',skipped:'跳过',simulated:'模拟完成（未投递）'};
    const load=async()=>{
      const data=await api('/api/admin/mail?page='+page);
      result.textContent=`邮件服务：${data.available?data.mode==='smtp'?'SMTP 已配置':'模拟模式':'未启用或配置不完整'}`;
      document.getElementById('mail-members').innerHTML=data.members.map(m=>{
        const p=m.preferences;
        return `<article class="daily-card">
          <strong>${esc(m.name)}</strong><p>${esc(m.address||'未绑定')} · ${m.verified?'已验证':'未验证'}${!m.is_active||m.deleted_at?' · 账户停用':''}</p>
          <button class="btn" type="button" data-mail-member="${m.id}" data-paused="${m.paused?'0':'1'}">${m.paused?'恢复提醒':'暂停提醒'}</button>
          <form data-mail-prefs="${m.id}" class="ux-account-form"><label><input name="assignment_enabled" type="checkbox" ${p.assignment_enabled?'checked':''} ${p.assignment_enabled===undefined?'disabled':''}> 即时任务指派邮件</label>
            <label><input name="morning_enabled" type="checkbox" ${p.morning_enabled?'checked':''}> 个人详细汇总</label>
            <label>间隔（0.5–7 天）<input name="morning_interval_days" type="number" min="0.5" max="7" step="0.5" value="${p.morning_interval_hours/24}" required></label>
            <label><input name="daily_enabled" type="checkbox" ${p.daily_enabled?'checked':''}> 团队详细汇总</label>
            <label>间隔（1–14 天）<input name="daily_interval_days" type="number" min="1" max="14" step="1" value="${p.daily_interval_days}" required></label>
            <label><input name="countdown_enabled" type="checkbox" ${p.countdown_enabled?'checked':''}> 首页倒计时邮件</label>
            <label>间隔（1–14 天）<input name="countdown_interval_days" type="number" min="1" max="14" step="1" value="${p.countdown_interval_days}" required></label>
            <button class="btn primary" type="submit">保存该成员设置</button>
          </form>
        </article>`;
      }).join('');
      document.getElementById('mail-jobs').innerHTML=data.jobs.map(j=>`<article class="daily-update"><strong>${esc(j.name)} · ${esc({verify:'邮箱验证',morning:'个人任务早报',morning_pm:'个人任务晚间汇总',daily:'团队项目晚报',countdown:'目标倒计时',test:'测试邮件',assignment:'任务指派'}[j.kind]||j.kind)}</strong><p>${esc(j.day)} · ${esc(states[j.state])} · 尝试 ${j.attempts} 次</p><p>${esc(j.error)}</p></article>`).join('')||'<p>暂无发送记录</p>';
      document.getElementById('mail-prev').disabled=page<=1;document.getElementById('mail-next').disabled=!data.has_more;document.getElementById('mail-page').textContent='第 '+page+' 页';
    };
    const reload=()=>BusinessUI.action('mail-admin-load',[document.getElementById('mail-refresh')],load,text=>{result.textContent=text;});
    document.getElementById('mail-refresh').addEventListener('click',reload);
    document.getElementById('mail-prev').addEventListener('click',()=>{page--;reload();});document.getElementById('mail-next').addEventListener('click',()=>{page++;reload();});
    document.getElementById('mail-test').addEventListener('click',event=>BusinessUI.action('mail-test',[event.currentTarget],async()=>{const data=await api('/api/admin/mail/test',{});result.textContent=data.message;},text=>{result.textContent=text;}));
    document.getElementById('mail-members').addEventListener('submit',event=>{
      const form=event.target.closest('[data-mail-prefs]');if(!form)return;
      event.preventDefault();
      BusinessUI.action('admin-mail-prefs:'+form.dataset.mailPrefs,form.querySelectorAll('button'),async()=>{
        await api('/api/admin/mail/'+form.dataset.mailPrefs+'/preferences',prefsFromForm(form));
        await load();result.textContent='成员设置已保存';
      },text=>{result.textContent=text;});
    });
    document.getElementById('mail-members').addEventListener('click',event=>{
      const button=event.target.closest('[data-mail-member]');if(!button)return;
      BusinessUI.action('mail-pause:'+button.dataset.mailMember,[button],async()=>{await api(`/api/admin/mail/${button.dataset.mailMember}/pause`,{paused:button.dataset.paused==='1'});await load();},text=>{result.textContent=text;});
    });reload();
  }
})();
