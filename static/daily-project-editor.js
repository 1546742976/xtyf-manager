"use strict";
(() => {
  const host=document.getElementById('daily-editor');if(!host)return;
  const forms=new Map();
  const escape=value=>BusinessUI.escape(value);
  const options=(values,current)=>values.map(value=>`<option ${value===current?'selected':''}>${escape(value)}</option>`).join('');
  function build(project,catalog) {
    const members=catalog.members.filter(member=>member.is_active!==0);
    const choices=[...(project?.groups||[])];
    for(const group of catalog.groups.filter(group=>group.is_active!==0)) {
      if(!choices.some(choice=>choice.group_id===group.id))choices.push({stage:`group-${group.id}`,group_id:group.id,label:group.name,enabled:false,owners:[],progress:0});
    }
    const stages=Object.fromEntries(choices.map(group=>[group.stage,group.label]));
    const specialty=member=>(member.group_ids||[]).map(id=>catalog.groups.find(group=>group.id===id)?.name).filter(Boolean).join('／')||'未设专业';
    const unavailable=project?.groups.some(group=>group.enabled&&group.owners.some(owner=>!members.some(member=>member.id===owner.id)));
    const form=document.createElement('form');form.id='daily-form';form.className='task-create-form';
    form.innerHTML=`<div class="task-create-body"><section class="task-create-section"><h3>目标与安排</h3><div class="task-create-grid"><label class="task-create-wide">目标名称<input name="title" maxlength="200" required value="${escape(project?.title||'')}"></label><label>状态<select name="status">${options(['未开始','进行中','已完成','阻塞','已取消'],project?.status||'未开始')}</select></label><label>优先级<select name="priority">${options(['高','中','低'],project?.priority||'中')}</select></label><label>开始日期<input type="date" name="start_date" value="${escape(project?.start_date||'')}"></label><label>截止日期<input type="date" name="due_date" value="${escape(project?.due_date||'')}"></label><label class="task-create-wide">目标说明<textarea name="detail" maxlength="10000">${escape(project?.detail||'')}</textarea></label></div></section><section class="task-create-section"><h3>分组负责人</h3><p class="field-help">至少启用一个组，每组至少勾选一位负责人。可跨组负责，停用组的历史记录保留。</p>${unavailable?'<p class="daily-owner-warning" role="status">原负责人包含已停用或不可分配的成员，请为启用组重新确认有效负责人后保存。</p>':''}<div class="daily-owner-groups">${Object.entries(stages).map(([stage,label])=>{
      const group=choices.find(item=>item.stage===stage),ids=group?.owners.map(owner=>owner.id)||[];
      const sorted=[...members].sort((a,b)=>Number((b.group_ids||[]).includes(group.group_id))-Number((a.group_ids||[]).includes(group.group_id))||a.id-b.id);
      const legacy=project?.groups.some(item=>item.stage===stage)&&!group.group_id;
      return `<fieldset class="daily-owner-group"><legend><label class="daily-group-enable"><input type="checkbox" name="enabled" value="${stage}" ${group?.enabled?'checked':''} ${group.catalog_archived&&!group.enabled?'disabled':''}><span>${escape(label)}${group.catalog_archived?'（专业组已停用）':''}</span></label></legend>${legacy?`<label>关联现有专业组（历史记录保留）<select name="link-${stage}"><option value="">暂不关联</option>${catalog.groups.filter(item=>item.is_active!==0).map(item=>`<option value="${item.id}">${escape(item.name)}</option>`).join('')}</select></label>`:''}<label class="daily-owner-search">搜索负责人<input type="search" data-owner-search="${stage}" placeholder="姓名或专业" aria-label="搜索${escape(label)}负责人"></label><p class="daily-owner-count" data-owner-count="${stage}" aria-live="polite"></p><div class="daily-owner-choices" data-owner-choices="${stage}">${sorted.map(member=>`<label class="daily-owner-choice" data-owner-name="${escape(member.name+' '+specialty(member))}"><input type="checkbox" name="owners-${stage}" value="${member.id}" ${ids.includes(member.id)?'checked':''}><span>${escape(member.name)}</span><small>${escape(specialty(member))}</small></label>`).join('')||'<p class="muted">暂无可分配成员</p>'}</div><p class="daily-owner-empty" data-owner-empty="${stage}" hidden>没有匹配的成员</p></fieldset>`;
    }).join('')}</div></section></div><footer class="task-create-footer"><p class="form-message" role="status" aria-live="polite"></p><div class="task-create-actions daily-actions"><button type="button" class="btn" data-task-create-close>取消</button><button type="submit" class="btn primary">${project?'保存项目':'创建项目'}</button></div></footer>`;
    const extra=document.createElement('section');extra.className='task-create-section';
    extra.innerHTML=`<h3>阻塞跟进</h3><p class="field-help">标记阻塞时填写原因和下一步；解除时填写处理结果。</p><label>阻塞原因<textarea name="blocker_reason" maxlength="2000">${escape(project?.blocker_reason||'')}</textarea></label><label>下一步<textarea name="blocker_next_action" maxlength="2000">${escape(project?.blocker_next_action||'')}</textarea></label><label>协助人<select name="blocker_helper_id"><option value="">未指定</option>${members.map(member=>`<option value="${member.id}" ${member.id===project?.blocker_helper_id?'selected':''}>${escape(member.name)}</option>`).join('')}</select></label><label>下次跟进日期<input type="date" name="blocker_follow_up" value="${escape(project?.blocker_follow_up||'')}"></label><label>解除阻塞结果<textarea name="blocker_result" maxlength="2000"></textarea></label>${project&&['已完成','已取消'].includes(project.status)?`<h3>重新打开的实际进度</h3><p>选择进行中或未开始，并为至少一个启用组填写实际进度（0–99）；留空的组保持原值。</p>${choices.map(group=>`<label>${escape(group.label)}<input type="number" name="reopen-${group.stage}" min="0" max="99" step="1"></label>`).join('')}`:''}`;
    form.querySelector('.task-create-body').append(extra);
    if(project?.blocker_helper_id&&!members.some(member=>member.id===project.blocker_helper_id)) {
      form.elements.blocker_helper_id.insertAdjacentHTML('beforeend',`<option value="${Number(project.blocker_helper_id)}" selected>${escape(project.blocker_helper_name||`成员 #${project.blocker_helper_id}`)}（已停用）</option>`);
    }
    function update(stage) {
      const enabled=form.querySelector(`[name=enabled][value=${stage}]`).checked;
      const owners=[...form.querySelectorAll(`[name=owners-${stage}]:checked`)];
      form.querySelector(`[data-owner-count=${stage}]`).textContent=`${enabled?'已启用':'未启用'} · 已选 ${owners.length} 人`;
      form.querySelectorAll(`[data-owner-choices=${stage}] .daily-owner-choice`).forEach(row=>row.classList.toggle('is-selected',row.querySelector('input').checked));
    }
    Object.keys(stages).forEach(stage=>{
      update(stage);form.querySelector(`[name=enabled][value=${stage}]`).addEventListener('change',()=>update(stage));
      form.querySelector(`[data-owner-choices=${stage}]`).addEventListener('change',()=>update(stage));
      form.querySelector(`[data-owner-search=${stage}]`).addEventListener('input',event=>{
        let count=0;const query=event.target.value.trim().toLocaleLowerCase();
        form.querySelectorAll(`[data-owner-choices=${stage}] .daily-owner-choice`).forEach(row=>{row.hidden=!row.dataset.ownerName.toLocaleLowerCase().includes(query);if(!row.hidden)count++;});
        form.querySelector(`[data-owner-empty=${stage}]`).hidden=count!==0;
      });
    });
    let saving=false;
    form.addEventListener('submit',async event=>{
      event.preventDefault();if(saving||!form.reportValidity())return;
      const local=form.querySelector('.form-message'),body=Object.fromEntries(new FormData(form));body.groups={};body.groups_mode='replace';body.group_links={};
      body.blocker_helper_id=body.blocker_helper_id?Number(body.blocker_helper_id):null;
      if(project){body.version=project.version;body.group_versions=Object.fromEntries(project.groups.map(group=>[group.stage,group.version]));}
      form.querySelectorAll('[name=enabled]:checked').forEach(input=>{body.groups[input.value]=[...form.querySelectorAll(`[name=owners-${input.value}]:checked`)].map(owner=>Number(owner.value));});
      for(const stage of Object.keys(body.groups))if(body[`link-${stage}`])body.group_links[stage]=Number(body[`link-${stage}`]);
      if(project&&['已完成','已取消'].includes(project.status)) {
        body.reopen_progress={};for(const stage of Object.keys(body.groups))if(body[`reopen-${stage}`]!=='')body.reopen_progress[stage]=Number(body[`reopen-${stage}`]);
      }
      if(!Object.keys(body.groups).length){local.textContent='请至少启用一个组。';return;}
      const empty=Object.keys(body.groups).find(stage=>!body.groups[stage].length);
      if(empty){local.textContent=`请为${stages[empty]}组至少选择一位负责人。`;return;}
      saving=true;window.TaskCreateDialog.setBusy(true);
      const controls=[...form.querySelectorAll('button')],before=controls.map(control=>control.disabled);controls.forEach(control=>{control.disabled=true;});local.textContent='正在保存…';
      try {
        const result=await window.TaskCreateDialog.request('/api/daily-projects'+(project?'/'+project.id:''),body);
        forms.delete(project?.id||'new');document.dispatchEvent(new CustomEvent('daily:saved',{detail:{id:result.id}}));
      } catch(error){local.textContent=error.message+'；当前输入已保留。若版本冲突，请记录草稿后刷新项目。';}
      finally {saving=false;controls.forEach((control,index)=>{control.disabled=before[index];});window.TaskCreateDialog.setBusy(false);}
    });
    return form;
  }
  window.DailyProjectEditor={async prepare(project=null){
    const key=project?.id||'new';
    if(!forms.has(key))forms.set(key,build(project,await window.TaskCreateDialog.catalog()));
    host.replaceChildren(forms.get(key));
  }};
})();
