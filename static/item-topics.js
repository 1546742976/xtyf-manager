"use strict";
(() => {
  const mounts=new Set(), safe=value=>BusinessUI.escape(value);
  async function request(url,body){
    const response=await BusinessUI.fetch(url,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const data=await response.json();if(!response.ok||!data.ok)throw new Error(data.error||'读取失败');return data;
  }
  function endpoint(kind,id){return kind==='task'?`/api/task/${id}/topics`:`/api/daily-projects/${id}/topics`;}
  function mount(kind,item,host){
    const box=host?.querySelector(kind==='task'?'#task-topic-links':'#daily-topic-links');if(!box)return;
    const view={kind,item,host,box,busy:false,links:[],loaded:false};mounts.add(view);box.classList.add('item-topics');
    box.innerHTML='<h3>关联讨论与结论</h3><div data-links></div><div data-link-editor></div><p data-feedback role="status" aria-live="polite">正在读取关联…</p><button class="btn" type="button" data-reload>刷新关联</button>';
    const feedback=box.querySelector('[data-feedback]');
    function showLinks(changedId){
      const list=box.querySelector('[data-links]');
      if(changedId===undefined)list.replaceChildren();
      list.querySelectorAll('[data-link]').forEach(card=>{if(!view.links.some(link=>link.topic_id===Number(card.dataset.link)))card.remove();});
      if(!view.links.length){list.innerHTML='<p data-empty>尚未关联话题。</p>';return;}
      list.querySelector('[data-empty]')?.remove();
      for(const link of view.links){
        const old=list.querySelector(`[data-link="${link.topic_id}"]`);if(old&&link.topic_id!==changedId)continue;
        const card=document.createElement('article');card.className='item-topic-card';card.dataset.link=link.topic_id;
        card.innerHTML=`<a data-title href="${safe(link.url)}">${safe(link.title)}</a><p data-conclusion>${safe(link.conclusion_withdrawn?'结论已撤回，请重新选择':link.conclusion||'尚未指定结论')}</p>${view.editable?`<div class="item-topic-tools"><button class="btn" type="button" data-choose="${link.topic_id}" ${link.withdrawn?'disabled':''}>选择结论</button><button class="btn" type="button" data-unlink="${link.topic_id}">解除关联</button></div><div data-messages></div>`:''}`;
        if(old)old.replaceWith(card);else list.append(card);
      }
    }
    async function load(initial=false){
      if(view.busy||!box.isConnected)return;
      try{
        const data=await request(endpoint(kind,item.id));if(!box.isConnected)return;
        view.links=data.items;view.editable=data.can_edit&&!host.classList.contains('task-preview');
        if(initial||!view.loaded){
          showLinks();view.loaded=true;
          if(view.editable){
            box.querySelector('[data-link-editor]').innerHTML='<form data-search class="item-topic-tools"><label>查找已有话题<input name="query" type="search" maxlength="120" placeholder="输入标题搜索"></label><button class="btn" type="submit">搜索话题</button></form><div class="item-topic-tools"><label>搜索结果<select data-topics aria-label="选择要关联的话题"><option value="">先搜索话题</option></select></label><button type="button" class="btn" data-add>关联话题</button></div>';
          }
          feedback.textContent='';
        }else{
          // Refresh only the published text; keep search and conclusion drafts intact.
          showLinks(-1);
          for(const link of view.links){const card=box.querySelector(`[data-link="${link.topic_id}"]`);if(!card)continue;
            card.querySelector('[data-title]').textContent=link.title;
            card.querySelector('[data-conclusion]').textContent=link.conclusion_withdrawn?'结论已撤回，请重新选择':link.conclusion||'尚未指定结论';
            if(link.withdrawn){card.querySelector('[data-messages]')?.replaceChildren();const choose=card.querySelector('[data-choose]');if(choose)choose.disabled=true;}
            if(link.conclusion_withdrawn)card.querySelector(`option[value="${link.conclusion_id}"]`)?.remove();
            const choices=card.querySelector('[data-conclusion-choice]');
            if(choices&&card.dataset.cursor){
              let more=true;
              while(more&&card.isConnected){
                const changes=await request(`/discussions/api/topics/${link.topic_id}/changes?after=${card.dataset.cursor}`);
                card.dataset.cursor=changes.cursor;more=changes.has_more;
                if(changes.topic.withdrawn){card.querySelector('[data-messages]')?.replaceChildren();break;}
                changes.events.filter(e=>e.kind==='message_withdrawn').forEach(e=>{choices.querySelector(`option[value="${e.message_id}"]`)?.remove();});
              }
            }
          }
          if(data.version!==item.version)feedback.textContent='事项有更新；编辑关联前请重新打开详情，当前输入已保留。';
        }
      }catch(error){feedback.textContent=error.message+'；可点击“刷新关联”重试。';}
    }
    async function action(work){
      if(view.busy)return;view.busy=true;box.querySelectorAll('button').forEach(b=>b.disabled=true);feedback.textContent='正在处理…';
      try{await work();}catch(error){feedback.textContent=error.message+'；输入已保留。';}
      finally{view.busy=false;box.querySelectorAll('button').forEach(b=>b.disabled=false);view.links.filter(l=>l.withdrawn).forEach(l=>{const b=box.querySelector(`[data-choose="${l.topic_id}"]`);if(b)b.disabled=true;});}
    }
    async function mutate(topic_id,actionName,conclusion_id){
      const previous=item.version;
      const result=await request(endpoint(kind,item.id),{version:previous,topic_id,action:actionName,...(actionName==='conclusion'?{conclusion_id}:{})});
      if(!box.isConnected)return;
      item.version=result.version;view.links=result.items;
      host.querySelectorAll('form input[name=version]').forEach(input=>{if(Number(input.value)===previous)input.value=String(result.version);});
      showLinks(topic_id);feedback.textContent='关联已保存。';
      document.dispatchEvent(new CustomEvent('item:topics-updated',{detail:{kind,id:item.id,version:result.version}}));
    }
    async function messages(topicId,before){
      const card=box.querySelector(`[data-link="${topicId}"]`);if(!card)return;
      const data=await request(`/discussions/api/topics/${topicId}/messages`+(before?'?before='+before:''));if(!card.isConnected)return;
      const target=card.querySelector('[data-messages]');
      if(!before){card.dataset.cursor=data.cursor;target.innerHTML=`<div class="item-topic-tools"><label>选择本事项的结论<select data-conclusion-choice><option value="">清除结论</option></select></label><button type="button" class="btn" data-save-conclusion="${topicId}">保存结论</button></div>`;}
      const select=target.querySelector('select');
      data.messages.filter(m=>!m.withdrawn).forEach(m=>{if(select.querySelector(`option[value="${m.id}"]`))return;const option=document.createElement('option');option.value=m.id;option.textContent=`${m.author_name}：${m.body||'附件消息'}`;option.selected=view.links.find(l=>l.topic_id===topicId)?.conclusion_id===m.id;select.append(option);});
      target.querySelector('[data-older]')?.remove();if(data.has_more)target.insertAdjacentHTML('beforeend',`<button type="button" class="btn" data-older="${topicId}" data-before="${data.before}">加载更早消息</button>`);
      feedback.textContent='';
    }
    box.addEventListener('submit',event=>{if(!event.target.matches('[data-search]'))return;event.preventDefault();action(async()=>{
      const data=await request('/discussions/api/topics?q='+encodeURIComponent(event.target.elements.query.value));
      const select=box.querySelector('[data-topics]');select.innerHTML='<option value="">选择话题</option>';
      data.topics.filter(t=>!t.withdrawn).forEach(t=>{const option=document.createElement('option');option.value=t.id;option.textContent=t.title;select.append(option);});
      feedback.textContent=data.topics.length?'请选择话题后点击关联。':'没有匹配话题，可调整标题关键词。';
    });});
    box.addEventListener('click',event=>{
      const b=event.target.closest('button');if(!b)return;
      if(b.matches('[data-reload]')){load(true);return;}
      if(b.matches('[data-add]')){const id=Number(box.querySelector('[data-topics]').value);if(!id){feedback.textContent='请先选择话题。';return;}action(()=>mutate(id,'link'));}
      if(b.dataset.unlink)action(()=>mutate(Number(b.dataset.unlink),'unlink'));
      if(b.dataset.choose)action(()=>messages(Number(b.dataset.choose)));
      if(b.dataset.older)action(()=>messages(Number(b.dataset.older),b.dataset.before));
      if(b.dataset.saveConclusion)action(()=>mutate(Number(b.dataset.saveConclusion),'conclusion',Number(b.closest('[data-messages]').querySelector('select').value)||null));
    });
    view.refresh=()=>load();load(true);
  }
  document.addEventListener('task:rendered',event=>mount('task',event.detail.task,event.detail.host));
  document.addEventListener('daily:rendered',event=>mount('daily',event.detail.project,event.detail.host));
  let backlinkId=null,backlinkAt=0;
  document.addEventListener('discussion:rendered',async event=>{
    const box=document.getElementById('topic-backlinks'),id=event.detail.topic.id;if(!box)return;
    if(backlinkId===id&&Date.now()-backlinkAt<4000)return;backlinkId=id;backlinkAt=Date.now();box.textContent='';
    try{const data=await request(`/discussions/api/topics/${id}/items`);if(backlinkId!==id)return;
      box.innerHTML=data.items.map(item=>`<a href="${safe(item.url)}">${item.type==='task'?'任务':'项目'}：${safe(item.title)}${item.conclusion_id?' · 已选结论':''}</a>`).join('');
    }catch(_){if(backlinkId===id)box.textContent='关联事项读取失败，下次同步重试。';}
  });
  setInterval(()=>{for(const view of mounts){if(!view.box.isConnected){mounts.delete(view);continue;}if(!document.hidden&&view.box.getClientRects().length)view.refresh();}},5000);
})();
