"use strict";
(() => {
  const form=document.getElementById('alert-filters');if(!form)return;
  const labels={blocked:'阻塞',overdue:'已逾期',unassigned:'缺少负责人',stale:'超过48小时未更新'},escape=BusinessUI.escape;
  const feedback=document.getElementById('alert-feedback'), prev=document.getElementById('alert-prev'), next=document.getElementById('alert-next');
  let page=1,busy=false;
  async function get(url){const response=await BusinessUI.fetch(url),data=await response.json();if(!response.ok||!data.ok)throw new Error(data.error||'读取失败');return data;}
  async function load(){
    if(busy)return;busy=true;form.querySelector('button').disabled=true;prev.disabled=next.disabled=true;feedback.textContent='正在读取异常…';
    try{
      const params=new URLSearchParams(new FormData(form));params.set('page',page);
      const data=await get('/api/task-alerts?'+params);
      document.getElementById('alert-counts').textContent=Object.entries(labels).map(([key,label])=>`${label} ${data.counts[key]}`).join(' · ')+'（同一事项可属于多类）';
      document.getElementById('alert-items').innerHTML=data.items.map(item=>`<article class="panel alert-card"><div class="alert-card-head"><h2><a href="${escape(item.url)}">${escape(item.title)}</a></h2><span>${item.type==='task'?'造车任务':'日常项目'}</span></div><p>${item.flags.map(flag=>`<span class="tag">${labels[flag]}</span>`).join(' ')}</p><p>${escape(item.status)} · ${Number(item.progress)}% · 截止 ${escape(item.due_date||'未设置')} · 负责人 ${escape(item.owner_names.join('、')||'未指定')}</p>${item.affected_groups.map(g=>`<p>${escape(g.label)}：${g.flags.map(f=>labels[f]).join('、')}</p>`).join('')}${item.status==='阻塞'?`<dl><dt>阻塞原因</dt><dd>${escape(item.blocker_reason||'待补充')}</dd><dt>下一步行动</dt><dd>${escape(item.blocker_next_action||'待补充')}</dd>${item.blocker_helper_name?`<dt>协助成员</dt><dd>${escape(item.blocker_helper_name)}</dd>`:''}${item.blocker_follow_up?`<dt>下次跟进</dt><dd>${escape(item.blocker_follow_up)}</dd>`:''}</dl>`:''}<a class="btn" href="${escape(item.url)}">查看并处理</a></article>`).join('')||'<p class="empty">当前筛选下暂无异常事项。</p>';
      feedback.textContent=`共 ${data.total} 项；处理后可点击“查询 / 刷新”更新列表。`;
      document.getElementById('alert-page').textContent=`第 ${page} 页`;prev.disabled=page<=1;next.disabled=page*data.per_page>=data.total;
    }catch(error){feedback.textContent=error.message+'；当前筛选已保留，可点击“查询 / 刷新”重试。';}
    finally{busy=false;form.querySelector('button').disabled=false;}
  }
  form.addEventListener('submit',event=>{event.preventDefault();page=1;load();});
  prev.addEventListener('click',()=>{page--;load();});next.addEventListener('click',()=>{page++;load();});
  async function start(){
    try{const data=await get('/api/data');for(const [name,rows] of [['project_id',data.projects],['group_id',data.groups],['owner_id',data.all_members]]){
      const select=form.elements[name];rows.forEach(row=>{const option=document.createElement('option');option.value=row.id;option.textContent=row.name+(row.is_active===0?'（已停用）':'');select.append(option);});
    }}catch(error){feedback.textContent=error.message+'；筛选目录加载失败，请刷新页面重试。';return;}
    load();
  }
  window.addEventListener('pageshow',event=>{if(event.persisted)load();});start();
})();
