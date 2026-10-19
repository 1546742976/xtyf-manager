"use strict";
(() => {
  const form=document.getElementById('create-form');if(!form)return;
  const escape=value=>BusinessUI.escape(value);
  const selected=name=>[...form.querySelectorAll(`[name="${name}"]:checked`)].map(input=>Number(input.value));
  let data={members:[],projects:[],tasks:[]},loaded=false,saving=false;
  const validateOwner=()=>form.elements.owner_id.setCustomValidity(!form.elements.owner_id.value&&selected('participant_ids').length?'选择参与成员后必须指定负责人':'');
  const syncBlocker=()=>{
    const host=form.querySelector('[data-create-blocker]');if(!host)return;
    host.hidden=form.elements.status.value!=='阻塞';
    host.querySelectorAll('input,select,textarea').forEach(control=>{control.disabled=host.hidden;control.required=!host.hidden&&['blocker_reason','blocker_next_action'].includes(control.name);});
  };
  form.elements.status.addEventListener('change',syncBlocker);form.addEventListener('reset',()=>queueMicrotask(syncBlocker));
  function selectOptions(id,items,empty) {
    const control=document.getElementById(id),value=control.value;
    control.innerHTML=`<option value="">${empty}</option>`+items.map(item=>`<option value="${item.id}">${escape(item.name)}</option>`).join('');
    if(value&&!items.some(item=>String(item.id)===value))control.insertAdjacentHTML('beforeend',`<option value="${escape(value)}">已失效，请重新选择</option>`);
    control.value=value;
    control.setCustomValidity(value&&!items.some(item=>String(item.id)===value)?'已选项已失效，请重新选择':'');
    control.onchange=()=>{control.setCustomValidity('');if(control.name==='owner_id')validateOwner();};
  }
  function choices(id,name,items) {
    const ids=selected(name),host=document.getElementById(id);
    const missing=ids.filter(id=>!items.some(item=>item.id===id)).map(id=>({id,name:'已失效，请取消选择',invalid:true}));
    host.innerHTML='<div class="choice-grid">'+[...items,...missing].map(item=>`<label class="choice-item"><input type="checkbox" name="${name}" value="${item.id}" ${ids.includes(item.id)?'checked':''} ${item.invalid?'data-invalid="1"':''}><span>${escape(item.name)}</span></label>`).join('')+'</div>';
    const validate=()=>host.querySelector('input')?.setCustomValidity(host.querySelector('[data-invalid]:checked')?'已选项已失效，请取消选择':'');
    host.onchange=()=>{validate();if(name==='participant_ids')validateOwner();};validate();
  }
  function render(catalog) {
    data=catalog;
    const members=data.members.filter(member=>member.is_active!==0),tasks=data.tasks.map(task=>({...task,name:`#${task.id} ${task.title}`}));
    selectOptions('create-owner',members,'未指定');selectOptions('create-project',data.projects.filter(project=>project.is_active!==0),'未选择');
    if(document.getElementById('create-blocker-helper'))selectOptions('create-blocker-helper',members,'未指定');
    selectOptions('create-parent',tasks,'无主前置任务');choices('create-participants','participant_ids',members);choices('create-dependencies','predecessor_ids',tasks);
    form._taskDates?.refresh();
    syncBlocker();
  }
  window.TaskDateDefaults.bind(form,{saved:false,getTasks:()=>data.tasks});
  window.VehicleTaskCreator={async prepare(){render(await window.TaskCreateDialog.catalog());loaded=true;}};
  if(typeof addDataListener==='function')addDataListener(()=>{if(loaded&&!saving)render(state);});
  form.addEventListener('submit',async event=>{
    event.preventDefault();if(saving||!loaded||!form.reportValidity())return;
    const payload=Object.fromEntries(new FormData(form));payload.participant_ids=selected('participant_ids');payload.predecessor_ids=selected('predecessor_ids');
    if('blocker_helper_id' in payload)payload.blocker_helper_id=payload.blocker_helper_id?Number(payload.blocker_helper_id):null;
    payload.schedule_locked=!!form.querySelector('[name="schedule_locked"]')?.checked;
    if(!payload.owner_id&&payload.participant_ids.length){form.elements.owner_id.setCustomValidity('选择参与成员后必须指定负责人');form.elements.owner_id.reportValidity();return;}
    window.TaskDateDefaults.payload(form,payload);
    saving=true;window.TaskCreateDialog.setBusy(true);
    const message=form.querySelector('.form-message'),controls=[...form.querySelectorAll('button')],before=controls.map(control=>control.disabled);controls.forEach(control=>{control.disabled=true;});message.textContent='正在创建…';
    try {
      const result=await window.TaskCreateDialog.request('/api/task',payload);form.reset();
      form.querySelectorAll('input[type=checkbox]').forEach(control=>{control.checked=false;control.defaultChecked=false;});
      form.querySelectorAll('input,select,textarea').forEach(control=>control.setCustomValidity(''));message.textContent='';
      document.dispatchEvent(new CustomEvent('task:created',{detail:{id:result.id}}));
    } catch(error){message.textContent=error.message+'；当前输入已保留。';}
    finally {saving=false;controls.forEach((control,index)=>{control.disabled=before[index];});window.TaskCreateDialog.setBusy(false);form._taskDates?.refresh();}
  });
})();
