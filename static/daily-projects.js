"use strict";
(() => {
  const escape = value => BusinessUI.escape(value);
  async function request(url, data) {
    const options = data === undefined ? {} : data instanceof FormData ? {method:'POST',body:data} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)};
    const response = await BusinessUI.fetch(url,options), body = await response.json();
    if(!response.ok || !body.ok) throw new Error(body.error || '请求失败');
    return body;
  }
  function reasons(project) {
    if(['已完成','已取消'].includes(project.status))return '';
    const days=project.days_left, labels=[];
    if(days!==null && days<0)labels.push(`已逾期 ${-days} 天`);
    if(project.status==='阻塞')labels.push('阻塞');
    if(days!==null && days>=0 && days<=3)labels.push(days===0?'今天截止':`剩余 ${days} 天`);
    return labels.join(' · ');
  }
  function meter(value,label) {
    return `<div class="daily-meter"><span>${escape(label)} <strong>${Number(value)}%</strong></span><progress max="100" value="${Number(value)}" aria-label="${escape(label)}"></progress></div>`;
  }
  function card(project) {
    const enabled=project.groups.filter(g=>g.enabled), owners=[...new Set(enabled.flatMap(g=>g.owners.map(o=>o.name)))];
    return `<article class="daily-card${reasons(project)?' daily-alert':''}"><h2><a href="/tasks?type=daily&project=${project.id}">${escape(project.title)}</a></h2><p>${escape(project.status)} · ${escape(project.priority)}优先级</p><p class="muted">${escape(owners.join('、'))}</p>${meter(project.progress,'各组平均进度')}<div>${enabled.map(g=>meter(g.progress,g.label)).join('')}</div><p>${escape(reasons(project)|| (project.due_date?'截止：'+project.due_date:'未设置截止日期'))}</p><a class="btn" href="/tasks?type=daily&project=${project.id}">查看项目</a></article>`;
  }
  const dashboard=document.getElementById('daily-dashboard');
  if(dashboard) {
    const load=async()=>{
      const data=await request('/api/daily-projects?dashboard=1');
      dashboard.innerHTML=`<p>已展示 ${data.items.length}／共 ${data.total} 项</p><div class="daily-grid">${data.items.map(card).join('') || '<p>暂无自己的待办或需要提醒的日常项目</p>'}</div>`;
    };
    const refresh=document.getElementById('daily-dashboard-refresh');
    const reload=()=>BusinessUI.action('daily-dashboard',[refresh],load,error=>{dashboard.textContent=error+'；可点击刷新重试。';});
    refresh.addEventListener('click',reload); reload();
  }
  const root=document.getElementById('daily-app');
  if(!root)return;
  const isManager=root.dataset.manager==='1', memberId=Number(root.dataset.memberId);
  const host=document.getElementById('daily-detail'), message=document.getElementById('daily-message');
  let page=1, current=null;
  const feedback=text=>{message.textContent=text;};
  const action=(key, controls, work)=>BusinessUI.action(key,controls,work,feedback);
  async function list() {
    const form=document.getElementById('daily-filters'), params=new URLSearchParams(new FormData(form));
    params.set('mine',form.elements.mine.checked?'1':'0'); params.set('page',page);
    const data=await request('/api/daily-projects?'+params);
    document.getElementById('daily-list').innerHTML=data.items.map(card).join('') || '<p class="empty">暂无符合条件的项目</p>';
    const pages=document.getElementById('daily-pages');
    pages.innerHTML=`<button class="btn" type="button" data-page="${page-1}" ${page<=1?'disabled':''}>上一页</button><span>第 ${page} 页 · 共 ${data.total} 项</span><button class="btn" type="button" data-page="${page+1}" ${page*20>=data.total?'disabled':''}>下一页</button>`;
  }
  function own(group) {return isManager || group.owners.some(o=>o.id===memberId);}
  function renderLifecycle(project){
    host.querySelector('.daily-lifecycle')?.remove();
    const id=project.id;
    const closed=['已完成','已取消'].includes(project.status);
    const executionGroups=project.groups.filter(group=>group.enabled&&own(group));
    if(executionGroups.length) {
      host.insertAdjacentHTML('beforeend',`<details class="daily-lifecycle"><summary>${closed?'重新打开任务':'取消任务'}</summary><form id="daily-lifecycle">${closed?`<label>重开的专业组<select name="stage">${executionGroups.map(group=>`<option value="${escape(group.stage)}">${escape(group.label)}</option>`).join('')}</select></label><label>实际进度（0–99）<input name="progress" type="number" min="0" max="99" required value="0"></label>`:''}<label>原因（必填）<textarea name="note" maxlength="2000" required></textarea></label><button type="submit" class="btn">${closed?'确认重开':'确认取消整个任务'}</button><p class="form-message" role="alert"></p></form></details>`);
      const lifecycle=host.querySelector('#daily-lifecycle');
      lifecycle.addEventListener('submit',event=>{
        event.preventDefault();BusinessUI.action('daily-lifecycle:'+id,lifecycle.querySelectorAll('button'),async()=>{
          const body={action:closed?'reopen':'cancel',version:current.version,note:lifecycle.elements.note.value};
          if(closed){body.group_versions=Object.fromEntries(current.groups.map(group=>[group.stage,group.version]));body.reopen_progress={[lifecycle.elements.stage.value]:Number(lifecycle.elements.progress.value)};}
          await request('/api/daily-projects/'+id,body);await show(id);
        },text=>{lifecycle.querySelector('.form-message').textContent=text+'；输入已保留。';});
      });
    }
  }
  function updateSummary(project) {
    const statusChanged=current.status!==project.status;
    current.version=project.version;current.status=project.status;current.progress=project.progress;
    for(const key of ['completed_at','blocker_reason','blocker_next_action','blocker_helper_id','blocker_follow_up','blocker_helper_name','blocker_events'])current[key]=project[key];
    const status=host.querySelector('[data-daily-status]');if(status)status.textContent=project.status;
    host.querySelector(':scope > .daily-meter strong').textContent=project.progress+'%';
    host.querySelector(':scope > .daily-meter progress').value=project.progress;
    const closed=['已完成','已取消'].includes(project.status);
    host.querySelectorAll('[data-progress-form] button,[data-progress-form] input,[data-progress-form] textarea').forEach(control=>{control.disabled=closed;});
    const edit=document.getElementById('daily-edit');if(edit)edit.textContent=closed?'重新打开／调整项目':'编辑项目';
    if(statusChanged)renderLifecycle(current);
    const hint=host.querySelector('[data-daily-reminder]');if(hint)hint.textContent=reasons(project);
  }
  async function showBlocker(project) {
    const target=host.querySelector('#daily-blocker');if(!target)return;
    const blocked=project.status==='阻塞',canEdit=!['已完成','已取消'].includes(project.status)&&(isManager||project.groups.some(group=>group.enabled&&own(group)));
    target.innerHTML=`<h3>阻塞跟进</h3>${blocked?`<p>${escape(project.blocker_reason||'历史阻塞尚未补充原因')}</p><p>下一步：${escape(project.blocker_next_action||'待补充')}</p><p>协助人：${escape(project.blocker_helper_name||'未指定')}</p><p>下次跟进：${escape(project.blocker_follow_up||'未设置')}</p>`:'<p>当前无阻塞</p>'}${canEdit?`<form id="daily-blocker-form"><label>阻塞原因<textarea name="blocker_reason" maxlength="2000">${escape(project.blocker_reason||'')}</textarea></label><label>下一步<textarea name="blocker_next_action" maxlength="2000">${escape(project.blocker_next_action||'')}</textarea></label><label>协助人<select name="blocker_helper_id"><option value="">未指定</option></select></label><label>下次跟进日期<input name="blocker_follow_up" type="date" value="${escape(project.blocker_follow_up||'')}"></label>${blocked?'<label>解除阻塞结果<textarea name="blocker_result" maxlength="2000"></textarea></label>':''}<button class="btn" type="submit">${blocked?'保存阻塞跟进':'标记阻塞'}</button>${blocked?'<button class="btn" type="button" data-resolve-blocker>解除阻塞</button>':''}<p class="form-message" role="status"></p></form>`:''}<details><summary>阻塞历史（${(project.blocker_events||[]).length}）</summary>${(project.blocker_events||[]).map(event=>`<article class="daily-update"><strong>${escape(event.actor_name)} · ${escape({open:'建立阻塞',update:'更新跟进',resolve:'结束阻塞',cancel:'取消项目，结束跟进'}[event.action]||event.action)}</strong><time>${escape(event.created_at)}</time><p>${escape(event.reason)}</p><p>${escape(event.next_action)}</p><p>协助人：${escape(event.helper_name||'未指定')} · 下次跟进：${escape(event.follow_up||'未设置')}</p><p>${escape(event.resolution||'')}</p></article>`).join('')}</details>`;
    const form=target.querySelector('form');if(!form)return;
    const catalog=await request('/api/data');if(!form.isConnected)return;
    const members=catalog.members.filter(member=>member.is_active!==0);
    form.elements.blocker_helper_id.insertAdjacentHTML('beforeend',members.map(member=>`<option value="${member.id}" ${member.id===project.blocker_helper_id?'selected':''}>${escape(member.name)}</option>`).join(''));
    if(project.blocker_helper_id&&!members.some(member=>member.id===project.blocker_helper_id)) {
      form.elements.blocker_helper_id.insertAdjacentHTML('beforeend',`<option value="${Number(project.blocker_helper_id)}" selected>${escape(project.blocker_helper_name||`成员 #${project.blocker_helper_id}`)}（已停用）</option>`);
    }
    const save=resolve=>BusinessUI.action('daily-blocker:'+project.id,form.querySelectorAll('button'),async()=>{
      const body=Object.fromEntries(new FormData(form));body.version=current.version;body.blocker_action=resolve?'resolve':blocked?'update':'open';body.blocker_helper_id=body.blocker_helper_id?Number(body.blocker_helper_id):null;
      const data=await request(`/api/daily-projects/${project.id}/blocker`,body);updateSummary(data.project);await showBlocker(data.project);
    },text=>{form.querySelector('.form-message').textContent=text+'；输入已保留。';});
    form.addEventListener('submit',event=>{event.preventDefault();save(false);});
    form.querySelector('[data-resolve-blocker]')?.addEventListener('click',()=>save(true));
  }
  async function history(project, stage, before) {
    const box=host.querySelector(`[data-history="${stage}"]`);
    if(!box)return;
    const data=await request(`/api/daily-projects/${project.id}/groups/${stage}/progress`+(before?'?before='+before:''));
    if(current?.id!==project.id || !box.isConnected)return;
    const rows=data.updates.map(row=>`<article class="daily-update"><strong>${row.progress}% · ${escape(row.actor_name)}</strong><time>${escape(row.created_at)}</time><p class="daily-note">${escape(row.note||'无备注')}</p><div class="daily-images">${row.images.map(image=>`<a href="${escape(image.url)}" target="_blank" rel="noopener"><img src="${escape(image.url)}" alt="${escape(image.original_name)}" loading="lazy"></a>`).join('')}</div></article>`).join('');
    if(before)box.querySelector('[data-more]')?.remove();else box.innerHTML='';
    box.insertAdjacentHTML('beforeend',rows || (!before?'<p class="muted">还没有进度记录</p>':''));
    if(data.next_before)box.insertAdjacentHTML('beforeend',`<button class="btn" type="button" data-more="${data.next_before}" data-stage="${stage}">加载更早记录</button>`);
  }
  async function show(id) {
    const data=await request('/api/daily-projects/'+id); current=data.project;
    const project=current, closed=['已完成','已取消'].includes(project.status);
    host.hidden=false; document.getElementById('daily-list-panel').hidden=true;
    host.innerHTML=`<a class="btn" href="/tasks?type=daily">返回项目列表</a><h2>${escape(project.title)}</h2><p class="daily-note">${escape(project.detail||'暂无目标说明')}</p><p>${escape(project.status)} · ${escape(project.priority)}优先级 · ${escape(project.start_date||'未设开始日期')} → ${escape(project.due_date||'未设截止日期')}</p><p>${escape(reasons(project))}</p>${meter(project.progress,'各组平均进度')}${isManager?`<div class="daily-actions"><button type="button" class="btn" id="daily-edit">${closed?'重新打开／调整项目':'编辑项目'}</button><button type="button" class="btn danger" id="daily-delete">删除项目</button></div>`:''}<div class="daily-group-grid">${project.groups.map(g=>`<section class="daily-group"><h3>${escape(g.label)}${g.enabled?'':'（已停用）'}${g.catalog_archived?'（专业组已停用）':''}</h3><p>负责人：${g.owners.map(o=>escape(o.name)+(!o.is_active||o.deleted_at?'（已停用）':'')).join('、')}</p>${meter(g.progress,g.label+'进度')}${g.enabled&&!closed&&own(g)?`<form data-progress-form="${g.stage}"><label>进度百分比<input name="progress" type="number" min="0" max="100" step="1" value="${g.progress}" required></label><label>进度备注<textarea name="note" rows="3" maxlength="2000"></textarea></label><label>图片（最多 5 张）<input name="files" type="file" accept="image/png,image/jpeg,image/webp" multiple></label><button class="btn primary" type="submit">提交${escape(g.label)}进度</button><p class="form-message" role="status" aria-live="polite"></p></form>`:'<p class="muted">只读记录</p>'}<h4>进度记录</h4><div data-history="${g.stage}">加载中…</div><button class="btn" type="button" data-history-retry="${g.stage}">刷新记录</button></section>`).join('')}</div>`;
    if(!isManager && Number(project.created_by)===memberId && !closed)host.insertAdjacentHTML('afterbegin','<button type="button" class="btn" id="daily-edit">编辑计划</button>');
    renderLifecycle(project);
    if(project.changes?.length)host.insertAdjacentHTML('beforeend',`<details><summary>任务修改记录</summary>${project.changes.map(change=>`<article><strong>${escape(change.actor_name)} · ${escape(change.created_at)}</strong><p>${escape({create:'创建',update:'管理调整',plan:'计划修改',cancel:'取消',reopen:'重新打开'}[change.action]||change.action)} · ${escape(change.note||'无备注')}</p></article>`).join('')}</details>`);
    for(const group of project.groups) history(project,group.stage).catch(error=>{const box=host.querySelector(`[data-history="${group.stage}"]`);if(box)box.textContent=error.message;});
    const header=host.querySelector(':scope > p:nth-of-type(2)');
    if(header)header.innerHTML=`<span data-daily-status>${escape(project.status)}</span> · ${escape(project.priority)}优先级 · ${escape(project.start_date||'未设开始日期')} → ${escape(project.due_date||'未设截止日期')}`;
    const reminder=host.querySelector(':scope > p:nth-of-type(3)');if(reminder)reminder.dataset.dailyReminder='';
    host.insertAdjacentHTML('beforeend',`<section id="daily-blocker"></section><section id="daily-topic-links" data-item-type="daily" data-item-id="${project.id}"></section>`);
    showBlocker(project).catch(error=>{host.querySelector('#daily-blocker').textContent=error.message+'；请刷新后重试。';});
    document.dispatchEvent(new CustomEvent('daily:rendered',{detail:{project,host}}));
    document.getElementById('daily-edit')?.addEventListener('click',event=>window.TaskCreateDialog.open({type:'daily',project:current,trigger:event.currentTarget}));
    document.getElementById('daily-delete')?.addEventListener('click',()=>{
      if(!confirm('删除该项目？项目将从列表移除，历史记录仍保留。'))return;
      action('daily-delete',host.querySelectorAll('button'),async()=>{await request(`/api/daily-projects/${id}/delete`,{version:current.version});location.assign('/tasks?type=daily');});
    });
    const imagePreparation=new WeakMap();
    const prepareSelected=file=>{
      if(!imagePreparation.has(file))imagePreparation.set(file,DailyImages.prepare(file).then(value=>({value}),error=>({error})));
      return imagePreparation.get(file);
    };
    host.querySelectorAll('[data-progress-form] [name=files]').forEach(input=>input.addEventListener('change',()=>{
      if(input.files.length<=5)Array.from(input.files).forEach(prepareSelected);
    }));
    host.querySelectorAll('[data-progress-form]').forEach(form=>form.addEventListener('submit',event=>{
      event.preventDefault();
      const stage=form.dataset.progressForm, group=current.groups.find(g=>g.stage===stage), local=form.querySelector('.form-message');
      BusinessUI.action('daily-progress:'+id+stage,form.querySelectorAll('button'),async()=>{
        if(form.elements.files.files.length>5)throw new Error('一次最多上传 5 张图片');
        const fields={progress:Number(form.elements.progress.value),note:form.elements.note.value,version:group.version,project_version:current.version};
        let body=fields,uploadNotice='';
        if(form.elements.files.files.length){
          local.textContent='正在本地处理图片…';
          const prepared=[];
          for(const file of Array.from(form.elements.files.files)){
            const result=await prepareSelected(file);
            if(result.error){imagePreparation.delete(file);throw result.error;}
            prepared.push(result.value);
          }
          local.textContent='正在核对权限与版本…';
          await request(`/api/daily-projects/${id}/groups/${stage}/preflight?version=${fields.version}&project_version=${fields.project_version}`);
          body=new FormData();
          for(const [key,value] of Object.entries(fields))body.set(key,value);
          for(const entry of prepared)body.append('files',entry.file);
          if(prepared.some(entry=>entry.original))uploadNotice='；当前客户端使用原图安全上传';
        }
        local.textContent='正在保存…'+uploadNotice;
        const result=await request(`/api/daily-projects/${id}/groups/${stage}/progress`,body);
        group.version=result.version;group.progress=fields.progress;
        updateSummary(result.project);
        form.elements.note.value='';form.elements.files.value='';local.textContent='进度已保存'+uploadNotice;
        const section=form.closest('.daily-group');section.querySelector('progress').value=fields.progress;section.querySelector('.daily-meter strong').textContent=fields.progress+'%';
        // Refresh only this history and total; preserve drafts in the other groups.
        try {
          await history(current,stage);
          if(['已完成','已取消'].includes(current.status))await showBlocker(current);
        } catch(error) {local.textContent='进度已保存，但最新摘要加载失败，请刷新记录核对：'+error.message;}
      },text=>{local.textContent=text+'；输入已保留。若版本冲突，请记录草稿后重新打开项目。';}).finally(()=>{
        if(['已完成','已取消'].includes(current.status))form.querySelectorAll('button,input,textarea').forEach(control=>{control.disabled=true;});
      });
    }));
  }
  host.addEventListener('click',event=>{
    const retry=event.target.closest('[data-history-retry]'),more=event.target.closest('[data-more]');
    if(retry||more)action('history:'+(retry?.dataset.historyRetry||more.dataset.stage),[retry||more],()=>history(current,retry?.dataset.historyRetry||more.dataset.stage,more?.dataset.more));
  });
  const reload=()=>action('daily-load',[document.getElementById('daily-reload')],()=>root.dataset.projectId?show(Number(root.dataset.projectId)):list());
  document.getElementById('daily-reload').addEventListener('click',reload);
  document.getElementById('daily-filters').addEventListener('submit',event=>{event.preventDefault();page=1;reload();});
  document.getElementById('daily-pages').addEventListener('click',event=>{if(event.target.dataset.page){page=Number(event.target.dataset.page);reload();}});
  reload();
})();
