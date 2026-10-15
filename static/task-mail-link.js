"use strict";
(() => {
  const value=new URLSearchParams(location.search).get('task');
  if(!/^\d+$/.test(value||''))return;
  let opened=false;
  addDataListener(()=>{
    if(opened)return;
    opened=true;
    if(state.tasks.some(task=>task.id===Number(value)))openTaskModal(Number(value));
    else BusinessUI.notify('该任务已删除或不可查看');
  });
})();
