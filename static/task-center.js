"use strict";
(() => {
  const dialog=document.getElementById('task-create-panel'), opener=document.getElementById('new-task-toggle');
  if(!dialog || !opener)return;
  const picker=document.getElementById('task-create-type'), load=document.getElementById('task-create-load');
  const panes=[...dialog.querySelectorAll('[data-create-pane]')];
  let returnFocus=null, busy=false, loading=false, editing=false, preparing=0, catalogRequest=null;
  async function request(url,data) {
    const response=await BusinessUI.fetch(url,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
    const body=await response.json();
    if(!response.ok || !body.ok)throw new Error(body.error||'操作失败，请重试');
    return body;
  }
  async function catalog() {
    if(!catalogRequest)catalogRequest=request('/api/data').finally(()=>{catalogRequest=null;});
    return catalogRequest;
  }
  function updateControls() {
    picker.disabled=busy||loading||editing;
    dialog.querySelectorAll('[data-task-create-close]').forEach(button=>{button.disabled=busy;});
    dialog.setAttribute('aria-busy',String(busy||loading));
  }
  function setBusy(value) {busy=value;updateControls();}
  function restoreFocus() {
    if(dialog.open)return;
    document.body.classList.remove('task-create-open');returnFocus?.focus();
  }
  function close() {if(busy)return;preparing++;loading=false;updateControls();dialog.close();restoreFocus();}
  async function select(type,project=null) {
    const token=++preparing;picker.value=type;
    panes.forEach(pane=>{pane.hidden=pane.dataset.createPane!==type;});
    load.textContent='正在读取可选成员与任务…';loading=true;updateControls();
    try {
      const provider=type==='daily'?window.DailyProjectEditor:window.VehicleTaskCreator;
      await provider.prepare(project);
      if(token!==preparing)return;
      load.textContent='';
      panes.find(pane=>!pane.hidden)?.querySelector('[name=title]')?.focus();
    } catch(error) {
      if(token!==preparing)return;
      load.textContent=error.message+'；已填内容保留。';
      const retry=document.createElement('button');retry.type='button';retry.className='btn';retry.textContent='重新读取';
      retry.addEventListener('click',()=>select(type,project));load.append(retry);
    } finally {if(token===preparing){loading=false;updateControls();}}
  }
  async function open({type=dialog.dataset.defaultType,project=null,trigger=opener}={}) {
    if(busy)return;
    editing=!!project;returnFocus=trigger;
    document.getElementById('task-create-title').textContent=editing?'编辑日常项目':'新建任务';
    document.getElementById('task-create-type-help').textContent=editing?'已有日常项目保留原类型和历史记录。':'切换类型会保留尚未提交的内容。';
    if(!dialog.open)dialog.showModal();
    document.body.classList.add('task-create-open');
    await select(project?'daily':type==='daily'?'daily':'vehicle',project);
  }
  window.TaskCreateDialog={open,close,setBusy,request,catalog};
  opener.addEventListener('click',()=>open());
  picker.addEventListener('change',()=>{if(!editing)select(picker.value);});
  dialog.addEventListener('click',event=>{if(event.target.closest('[data-task-create-close]'))close();});
  dialog.addEventListener('cancel',event=>{event.preventDefault();close();});
  dialog.addEventListener('close',restoreFocus);
  dialog.addEventListener('keydown',event=>{
    if(event.key!=='Tab')return;
    const controls=[...dialog.querySelectorAll('button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href]')].filter(control=>control.getClientRects().length&&control.type!=='hidden');
    const first=controls[0],last=controls.at(-1);
    if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}
    else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}
  });
  document.addEventListener('task:created',event=>{
    setBusy(false);close();
    document.getElementById('task-create-feedback').textContent='任务已创建，可在造车任务中查看。';
    if(typeof refresh==='function')refresh();
    else location.assign('/tasks?type=vehicle&task='+event.detail.id);
  });
  document.addEventListener('daily:saved',event=>{
    setBusy(false);close();location.assign('/tasks?type=daily&project='+event.detail.id);
  });
  function openHash(){if(location.hash==='#create'){history.replaceState(null,'',location.pathname+location.search);open();}}
  window.addEventListener('hashchange',openHash);openHash();
})();
