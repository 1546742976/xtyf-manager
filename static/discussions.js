"use strict";
(() => {
  const MiB=1024*1024;
  function filesError(files) {
    if(files.length>5)return '一次最多添加 5 个文件。';
    if(files.some(file=>file.size>100*MiB))return '单个文件不能超过 100 MiB。';
    if(files.reduce((sum,file)=>sum+file.size,0)>180*MiB)return '附件合计不能超过 180 MiB。';
    return '';
  }
  function readThrough(messages,seen,readId,hasEarlier,memberId,loadedBefore) {
    const ordered=[...messages].sort((a,b)=>a.id-b.id);
    if(hasEarlier&&(!Number.isInteger(loadedBefore)||loadedBefore>readId))return readId;
    let result=readId;
    for(const message of ordered) {
      if(message.id<=readId)continue;
      if(message.withdrawn)continue;
      if(message.author_id===memberId&&!seen.has(message.id))continue;
      if(!seen.has(message.id))break;
      result=message.id;
    }
    return result;
  }
  function makeAttempt(draft,uuid) {
    if(!draft.attempt)draft.attempt={id:uuid(),body:draft.body,title:draft.title,files:[...draft.files],uncertain:false};
    return draft.attempt;
  }
  const helpers={filesError,readThrough,makeAttempt};
  if(typeof module!=='undefined'&&module.exports)module.exports=helpers;
  if(typeof document==='undefined')return;
  const hub=document.getElementById('collab-hub');if(!hub)return;
  const $=id=>document.getElementById(id),memberId=Number(hub.dataset.memberId),cache=new Map();
  const state={page:1,q:'',selected:null,listGeneration:0,detailGeneration:0,polling:false};
  const list=$('collab-topics'),scroll=$('collab-messages-scroll'),messagesBox=$('collab-messages'),dialog=$('collab-topic-dialog');
  const reply=$('collab-reply'),topicForm=$('collab-topic-form');
  const newDraft=draft();let listBusy=false,pollTimer,readTimer,modalReturn=null,observer;
  const visibleTimers=new Map(),withdrawPending=new Set();
  function draft(){return {title:'',body:'',files:[],attempt:null,busy:false,status:'',progress:null};}
  function topicState(id){if(!cache.has(id))cache.set(id,{id,topic:null,messages:new Map(),withdrawn:new Map(),seen:new Set(),before:null,hasMore:false,cursor:0,readId:0,readBusy:false,loaded:false,loading:false,newCount:0,draft:draft()});return cache.get(id);}
  function current(){return state.selected?topicState(state.selected):null;}
  const safe=value=>BusinessUI.escape(value);
  const size=value=>value>=MiB?`${(value/MiB).toFixed(1)} MiB`:`${Math.max(1,Math.ceil(value/1024))} KiB`;
  const date=value=>{const result=new Date(value);return Number.isFinite(result.getTime())?result.toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}):'';};
  function attachmentURL(value){try{const url=new URL(value,location.origin);return url.origin===location.origin&&url.pathname.startsWith('/discussions/')?url.href:'';}catch(_){return '';}}
  function uuid(){return crypto.randomUUID?crypto.randomUUID():Array.from(crypto.getRandomValues(new Uint8Array(24)),value=>value.toString(16).padStart(2,'0')).join('');}
  function unreadChanged(){document.dispatchEvent(new Event('collaboration:unread'));}
  function issue(message,uncertain=false,status=0){return Object.assign(new Error(message),{uncertain,status});}
  async function request(path,body) {
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),15000),write=body!==undefined;
    try {
      const response=await fetch('/discussions/api'+path,{signal:controller.signal,...(write?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});
      let data;try{data=await response.json();}catch(_){throw issue('服务器响应异常，请核对操作结果。',write,response.status);}
      if(!response.ok||!data.ok)throw issue(data.error||`请求失败（${response.status}）`,write&&response.status>=500,response.status);
      return data;
    } catch(error) {
      if(error.uncertain!==undefined)throw error;
      throw issue(error.name==='AbortError'?'请求超时，请检查网络。':'网络连接中断，请稍后重试。',write);
    } finally {clearTimeout(timer);}
  }
  function upload(path,attempt,progress) {
    return new Promise((resolve,reject)=>{
      const xhr=new XMLHttpRequest(),body=new FormData();body.set('submission_id',attempt.id);body.set('body',attempt.body);if(path==='/topics')body.set('title',attempt.title);
      attempt.files.forEach(file=>body.append('files',file,file.name));
      xhr.open('POST','/discussions/api'+path);xhr.timeout=180000;
      xhr.setRequestHeader('X-CSRF-Token',document.querySelector('meta[name="csrf-token"]')?.content||'');
      xhr.upload.onprogress=event=>progress(event.lengthComputable?Math.round(event.loaded/event.total*100):null);
      xhr.onerror=()=>reject(issue('上传连接中断，发送结果尚未确认。',true));
      xhr.ontimeout=()=>reject(issue('上传超过 180 秒，发送结果尚未确认。',true));
      xhr.onabort=()=>reject(issue('上传中断，发送结果尚未确认。',true));
      xhr.onload=()=>{
        let data;try{data=JSON.parse(xhr.responseText);}catch(_){reject(issue('服务器响应异常，发送结果尚未确认。',true,xhr.status));return;}
        if(xhr.status<200||xhr.status>=300||!data.ok){reject(issue(data.error||`上传失败（${xhr.status}）`,xhr.status>=500,xhr.status));return;}
        resolve(data);
      };xhr.send(body);
    });
  }
  const downloads=new Set();
  async function downloadFile(link) {
    if(downloads.has(link.href))return;
    downloads.add(link.href);link.setAttribute('aria-busy','true');
    const article=link.closest('[data-message-id]');let feedback=article.querySelector('[data-download-status]');
    if(!feedback){feedback=document.createElement('p');feedback.dataset.downloadStatus='';feedback.className='collab-status';feedback.setAttribute('role','status');article.append(feedback);}
    feedback.textContent='正在检查附件…';const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);
    try {
      const response=await fetch(link.href,{method:'HEAD',signal:controller.signal});
      if(!response.ok||!/attachment/i.test(response.headers.get('Content-Disposition')||''))throw new Error(response.status===401?'登录已失效，请重新登录后下载。':response.status===404?'附件已撤回或不存在。':'附件暂时无法下载，请核对权限后重试。');
      const native=document.createElement('a');native.href=link.href;native.download=link.download;document.body.append(native);native.click();native.remove();feedback.textContent='下载已开始，请查看浏览器下载列表。';
    } catch(error){feedback.textContent=error.name==='AbortError'?'附件检查超时，请重试。':error.message;}
    finally {clearTimeout(timer);downloads.delete(link.href);link.removeAttribute('aria-busy');}
  }
  function status(node,message){node.textContent=message||'';}
  function renderTopics(topics) {
    const focusId=document.activeElement?.dataset.topicId,fragment=document.createDocumentFragment();
    for(const topic of topics) {
      const button=list.querySelector(`[data-topic-id="${Number(topic.id)}"]`)||document.createElement('button');
      button.type='button';button.className='collab-topic';button.dataset.topicId=topic.id;button.setAttribute('aria-current',String(topic.id===state.selected));
      button.innerHTML=`<strong>${safe(topic.withdrawn?'话题已撤回':topic.title)}${topic.unread_count?`<span class="collab-topic-count" aria-label="${Number(topic.unread_count)} 条未读">${Number(topic.unread_count)>99?'99+':Number(topic.unread_count)}</span>`:''}</strong><small>${safe(topic.author_name)} · ${safe(date(topic.last_activity_at||topic.created_at))}</small>`;fragment.append(button);
    }
    if(!topics.length){const empty=document.createElement('p');empty.className='collab-empty';empty.textContent=state.q?'没有匹配的话题，试试其他关键词。':'还没有话题，点击“新建话题”开始交流。';fragment.append(empty);}
    list.replaceChildren(fragment);if(focusId)list.querySelector(`[data-topic-id="${Number(focusId)}"]`)?.focus({preventScroll:true});
  }
  async function loadTopics(quiet=false) {
    const generation=++state.listGeneration;listBusy=true;if(!quiet)status($('collab-list-status'),'正在读取话题…');
    try {
      const data=await request(`/topics?page=${state.page}&q=${encodeURIComponent(state.q)}`);if(generation!==state.listGeneration)return;
      renderTopics(data.topics);$('collab-page').textContent=`第 ${data.page} 页 · 共 ${data.total} 项`;$('collab-prev').disabled=state.page<=1;$('collab-next').disabled=!data.has_more;
      status($('collab-list-status'),'');$('collab-list-retry').hidden=true;
    } catch(error){if(generation===state.listGeneration){status($('collab-list-status'),error.message+' 已显示的内容保留。');$('collab-list-retry').hidden=false;}}
    finally {if(generation===state.listGeneration)listBusy=false;}
  }
  function updateHeader(item) {
    const topic=item.topic;if(!topic)return;
    document.dispatchEvent(new CustomEvent('discussion:rendered',{detail:{topic}}));
    $('collab-title').textContent=topic.withdrawn?'话题已撤回':topic.title;
    $('collab-topic-meta').textContent=`${topic.author_name} · ${date(topic.created_at)}`;
    $('collab-withdraw-topic').hidden=!topic.can_withdraw||topic.withdrawn;
    reply.hidden=!!topic.withdrawn;
    if(topic.withdrawn)status($('collab-thread-status'),'话题已撤回，不能继续回复。');
    const gap=item.hasMore&&(!Number.isInteger(item.before)||item.before>item.readId);$('collab-read-gap').hidden=!gap;
  }
  function messageNode(message) {
    const article=document.createElement('article');article.tabIndex=-1;article._signature=JSON.stringify(message);article.className='collab-message'+(message.author_id===memberId?' is-own':'')+(message.withdrawn?' is-withdrawn':'');article.dataset.messageId=message.id;
    const attachments=message.withdrawn?[]:message.attachments||[];
    article.innerHTML=`<div class="collab-message-head" data-read-marker="${message.id}"><strong>${safe(message.author_name)}</strong><time datetime="${safe(message.created_at)}">${safe(date(message.created_at))}</time>${message.can_withdraw&&!message.withdrawn?`<button class="btn" type="button" data-withdraw-message="${message.id}">撤回</button>`:''}</div><p class="collab-message-body">${message.withdrawn?'这条消息已撤回':safe(message.body||'')}</p>${attachments.length?'<div class="collab-attachments">'+attachments.map(file=>{
      const url=attachmentURL(file.download_url),thumbnail=file.thumbnail_url?attachmentURL(file.thumbnail_url):'';
      return url?`<a class="collab-attachment" href="${safe(url)}" download="${safe(file.name)}">${thumbnail?`<img src="${safe(thumbnail)}" alt="${safe(file.name)}的缩略图" loading="lazy">`:''}<span>${safe(file.name)}<small>${size(Number(file.size))} · 下载附件</small></span></a>`:'';
    }).join('')+'</div>':''}`;
    return article;
  }
  function renderMessages(item,{prepend=false,scrollToStart=false}={}) {
    const oldTop=scroll.scrollTop,oldHeight=scroll.scrollHeight,focused=document.activeElement;
    const focusedMessage=focused?.closest('[data-message-id]')?.dataset.messageId;
    const ordered=[...item.messages.values()].sort((a,b)=>a.id-b.id),ids=new Set(ordered.map(message=>String(message.id)));
    let previous=null;
    for(const message of ordered) {
      let node=messagesBox.querySelector(`[data-message-id="${message.id}"]`);
      if(!node||node._signature!==JSON.stringify(message)) {
        const replacement=messageNode(message);if(node)node.replaceWith(replacement);node=replacement;
      }
      const next=previous?previous.nextSibling:messagesBox.firstChild;
      if(node!==next)messagesBox.insertBefore(node,next);
      previous=node;
    }
    [...messagesBox.children].forEach(node=>{if(!ids.has(node.dataset.messageId))node.remove();});
    $('collab-older').hidden=!item.hasMore;$('collab-new-messages').hidden=!item.newCount;$('collab-new-messages').textContent=`查看 ${item.newCount} 条新消息`;
    updateHeader(item);observeMessages(item);
    if(prepend)scroll.scrollTop=oldTop+scroll.scrollHeight-oldHeight;
    else if(scrollToStart) {
      const unread=[...item.messages.values()].sort((a,b)=>a.id-b.id).find(message=>message.id>item.readId&&message.author_id!==memberId&&!message.withdrawn);
      const node=unread?messagesBox.querySelector(`[data-message-id="${unread.id}"]`):null;
      scroll.scrollTop=node?node.offsetTop-scroll.offsetTop:scroll.scrollHeight;
    } else scroll.scrollTop=oldTop;
    if(focusedMessage&&!focused.isConnected){const replacement=messagesBox.querySelector(`[data-message-id="${Number(focusedMessage)}"]`);(replacement?.querySelector('button,a')||replacement)?.focus({preventScroll:true});}
  }
  function visible(element) {
    if(document.hidden||dialog.open||!element.isConnected||!element.getClientRects().length)return false;
    const box=element.getBoundingClientRect(),viewport=scroll.getBoundingClientRect();
    return box.bottom>Math.max(viewport.top,0)&&box.top<Math.min(viewport.bottom,innerHeight)&&Math.min(box.bottom,viewport.bottom,innerHeight)-Math.max(box.top,viewport.top,0)>=Math.min(24,box.height*.75);
  }
  function observeMessages(item) {
    observer?.disconnect();visibleTimers.forEach(clearTimeout);visibleTimers.clear();
    observer=new IntersectionObserver(entries=>entries.forEach(entry=>{
      const marker=entry.target,id=Number(marker.dataset.readMarker);clearTimeout(visibleTimers.get(id));visibleTimers.delete(id);
      if(entry.isIntersecting&&visible(marker))visibleTimers.set(id,setTimeout(()=>{
        visibleTimers.delete(id);if(state.selected!==item.id||!visible(marker))return;
        item.seen.add(id);clearTimeout(readTimer);readTimer=setTimeout(()=>advanceRead(item),250);
      },500));
    }),{root:scroll,threshold:[0,.5,.75,1]});
    messagesBox.querySelectorAll('[data-read-marker]').forEach(marker=>observer.observe(marker));
  }
  async function advanceRead(item) {
    if(document.hidden||dialog.open||state.selected!==item.id||item.readBusy||!scroll.getClientRects().length)return;
    const through=readThrough(item.messages.values(),item.seen,item.readId,item.hasMore,memberId,item.before);if(through<=item.readId)return;
    item.readBusy=true;
    try {await request(`/topics/${item.id}/read`,{message_id:through});item.readId=Math.max(item.readId,through);updateHeader(item);unreadChanged();loadTopics(true);}
    catch(_){/* Read state will retry on the next visible poll, never on a write loop. */}
    finally {item.readBusy=false;}
  }
  function mounted(form,item){return form===topicForm||current()?.draft===item;}
  function mountDraft(form,draftState) {
    if(!mounted(form,draftState))return;
    form.elements.body.value=draftState.body;window.FormControls?.refresh(form.elements.body);if(form.elements.title)form.elements.title.value=draftState.title;
    renderDraft(form,draftState);
  }
  function renderDraft(form,item) {
    if(!mounted(form,item))return;
    const locked=item.busy||!!item.attempt?.uncertain;
    form.elements.body.readOnly=locked;if(form.elements.title)form.elements.title.readOnly=locked;
    form.querySelector('input[type=file]').disabled=locked;form.querySelector('[type=submit]').disabled=locked;
    form.querySelector('[data-selected-files]').innerHTML=item.files.map((file,index)=>`<li><span>${safe(file.name)} · ${size(file.size)}</span><button type="button" class="btn" data-remove-file="${index}" ${locked?'disabled':''} aria-label="移除${safe(file.name)}">移除</button></li>`).join('');
    const progress=form.querySelector('[data-upload-progress]');progress.hidden=item.progress===null;if(item.progress!==null)progress.value=item.progress;
    status(form.querySelector('[data-send-status]'),item.status);const retry=form.querySelector('[data-send-retry]');retry.hidden=!item.attempt?.uncertain;retry.disabled=item.busy;
    if(form===topicForm)dialog.querySelectorAll('[data-topic-close]').forEach(button=>{button.disabled=item.busy;});
  }
  async function selectTopic(id,{updateURL=true,focus=true}={}) {
    const generation=++state.detailGeneration;state.selected=id;const item=topicState(id);
    hub.classList.add('is-thread');$('collab-empty').hidden=true;$('collab-thread').hidden=false;$('collab-thread-retry').hidden=true;
    mountDraft(reply,item.draft);list.querySelectorAll('[data-topic-id]').forEach(button=>button.setAttribute('aria-current',String(Number(button.dataset.topicId)===id)));
    if(updateURL)history.replaceState(null,'',location.pathname+'?topic='+id);
    if(item.loaded){updateHeader(item);renderMessages(item);if(focus)$('collab-title').focus({preventScroll:true});return;}
    item.loading=true;messagesBox.replaceChildren();status($('collab-thread-status'),'正在读取消息…');$('collab-title').textContent='读取话题中…';$('collab-topic-meta').textContent='';$('collab-withdraw-topic').hidden=true;reply.hidden=true;
    try {
      const data=await request(`/topics/${id}/messages`);if(generation!==state.detailGeneration)return;
      item.topic=data.topic;item.messages=new Map(data.messages.map(message=>[message.id,message]));item.before=data.before;item.hasMore=data.has_more;item.cursor=data.cursor;item.readId=Number(data.topic.read_message_id)||0;item.loaded=true;
      status($('collab-thread-status'),'');renderMessages(item,{scrollToStart:true});if(focus)$('collab-title').focus({preventScroll:true});
    } catch(error){if(generation===state.detailGeneration){status($('collab-thread-status'),error.message);$('collab-thread-retry').hidden=false;}}
    finally {item.loading=false;}
  }
  async function older() {
    const item=current();if(!item||item.loading||!item.hasMore)return;item.loading=true;$('collab-older').disabled=true;
    try {
      const data=await request(`/topics/${item.id}/messages?before=${item.before}`);
      data.messages.forEach(message=>{if(!item.messages.has(message.id)){const latest=item.withdrawn.get(message.id)||message;if(item.topic.withdrawn)Object.assign(latest,{body:'',attachments:[],withdrawn:true,can_withdraw:false});item.messages.set(message.id,latest);}});item.before=data.before;item.hasMore=data.has_more;
      if(state.selected===item.id){status($('collab-thread-status'),'');renderMessages(item,{prepend:true});}
    } catch(error){if(state.selected===item.id)status($('collab-thread-status'),error.message+' 请再次加载更早消息。');}
    finally {item.loading=false;$('collab-older').disabled=false;}
  }
  function applyEvents(item,data) {
    let added=0;
    for(const event of data.events) {
      if(event.message&&event.kind==='message_created'){if(!item.messages.has(event.message.id)&&!event.message.withdrawn)added++;item.messages.set(event.message.id,event.message);}
      if(event.message&&event.kind==='message_withdrawn'){item.withdrawn.set(event.message.id,event.message);if(item.messages.has(event.message.id))item.messages.set(event.message.id,event.message);}
      if(event.kind==='topic_withdrawn')item.messages.forEach(message=>Object.assign(message,{body:'',attachments:[],withdrawn:true,can_withdraw:false}));
    }
    item.cursor=data.cursor;item.topic=data.topic;item.readId=Math.max(item.readId,Number(data.topic.read_message_id)||0);item.newCount+=added;
    if(state.selected===item.id){if(!item.topic.withdrawn)status($('collab-thread-status'),'');if(data.events.length)renderMessages(item);else updateHeader(item);}
  }
  async function poll() {
    clearTimeout(pollTimer);if(document.hidden||state.polling)return;state.polling=true;
    try {
      const item=current();if(item?.loaded&&!item.loading){
        let more=true;
        while(more&&!document.hidden&&state.selected===item.id){const data=await request(`/topics/${item.id}/changes?after=${item.cursor}`);if(state.selected!==item.id)break;applyEvents(item,data);more=data.has_more;}
        await advanceRead(item);
      }
      if(!listBusy)await loadTopics(true);
    } catch(error){if(current()?.loaded)status($('collab-thread-status'),'同步暂时中断：'+error.message+' 将在页面可见时继续检查。');}
    finally {state.polling=false;if(!document.hidden)pollTimer=setTimeout(poll,5000);}
  }
  async function complete(form,item,result,topicId) {
    item.title='';item.body='';item.files=[];item.attempt=null;item.status='已发送';item.progress=null;mountDraft(form,item);unreadChanged();
    if(form===topicForm){closeTopic(true);await selectTopic(Number(result.topic_id));}
    else {
      const thread=topicState(topicId);if(result.message)thread.messages.set(result.message.id,result.message);
      if(state.selected===topicId){if(result.message){renderMessages(thread);scroll.scrollTop=scroll.scrollHeight;}else {thread.loaded=false;await selectTopic(topicId);}}
    }
    loadTopics(true);
  }
  async function checkSubmission(attempt) {return request('/submissions/'+encodeURIComponent(attempt.id));}
  async function send(form,item,topicId,manualRetry=false) {
    if(item.busy||(!manualRetry&&item.attempt?.uncertain))return;
    if(!item.attempt){
      if(!form.reportValidity())return;item.body=form.elements.body.value;if(form.elements.title)item.title=form.elements.title.value.trim();
      const error=filesError(item.files)||(!item.body.trim()&&!item.files.length?'请填写内容或添加附件。':'');
      if(error){item.status=error;renderDraft(form,item);return;}
    }
    const attempt=makeAttempt(item,uuid);item.busy=true;item.status=manualRetry?'正在核对上次发送结果…':'正在发送…';renderDraft(form,item);
    try {
      if(manualRetry){const found=await checkSubmission(attempt);if(found.found){await complete(form,item,found,topicId);return;}}
      attempt.uncertain=false;item.status=attempt.files.length?'正在上传附件…':'正在发送…';renderDraft(form,item);
      const path=topicId?`/topics/${topicId}/messages`:'/topics';
      const result=attempt.files.length?await upload(path,attempt,value=>{item.progress=value;item.status=value===100?'附件已上传，正在保存…':`正在上传附件${value===null?'':' '+value+'%'}…`;renderDraft(form,item);}):await request(path,{submission_id:attempt.id,body:attempt.body,...(!topicId?{title:attempt.title}:{})});
      await complete(form,item,result,topicId);
    } catch(error) {
      if(error.uncertain||attempt.uncertain){
        attempt.uncertain=true;item.status='发送结果尚未确认，正在核对…';renderDraft(form,item);
        try {const found=await checkSubmission(attempt);if(found.found){await complete(form,item,found,topicId);return;}item.status=error.message+' 暂未查到发送记录。内容和附件已保留，请点击“核对结果并重试”；重试沿用同一次提交，不重复创建。';}
        catch(_){item.status=error.message+' 暂时无法核对发送结果。内容和附件已保留，连接恢复后点击“核对结果并重试”。';}
      } else {item.attempt=null;item.status=error.message+' 内容和附件已保留。';}
    } finally {item.busy=false;item.progress=null;renderDraft(form,item);}
  }
  function bindComposer(form,getDraft,getTopic) {
    let composing=false;
    form.addEventListener('compositionstart',()=>{composing=true;});form.addEventListener('compositionend',()=>{composing=false;});
    form.addEventListener('keydown',event=>{if(event.key==='Enter'&&(event.ctrlKey||event.metaKey)&&!event.isComposing&&!composing&&event.keyCode!==229){event.preventDefault();form.requestSubmit();}});
    form.elements.body.addEventListener('input',()=>{getDraft().body=form.elements.body.value;});form.elements.title?.addEventListener('input',()=>{getDraft().title=form.elements.title.value;});
    form.querySelector('input[type=file]').addEventListener('change',event=>{
      const item=getDraft(),combined=[...item.files,...event.target.files],error=filesError(combined);event.target.value='';
      if(error)item.status=error;else {item.files=combined;item.status='';}renderDraft(form,item);
    });
    form.addEventListener('click',event=>{const remove=event.target.closest('[data-remove-file]');if(remove){const item=getDraft();if(item.busy||item.attempt?.uncertain)return;item.files.splice(Number(remove.dataset.removeFile),1);item.status='';renderDraft(form,item);}});
    form.addEventListener('submit',event=>{event.preventDefault();const item=getDraft();if(item)send(form,item,getTopic());});
    form.querySelector('[data-send-retry]').addEventListener('click',()=>send(form,getDraft(),getTopic(),true));
  }
  function closeTopic(force=false){if(newDraft.busy&&!force)return;dialog.close();document.body.classList.remove('collab-dialog-open');modalReturn?.focus();if(current()?.loaded)observeMessages(current());}
  $('collab-new-topic').addEventListener('click',()=>{modalReturn=document.activeElement;mountDraft(topicForm,newDraft);dialog.showModal();document.body.classList.add('collab-dialog-open');topicForm.elements.title.focus();});
  dialog.addEventListener('cancel',event=>{event.preventDefault();closeTopic();});dialog.addEventListener('click',event=>{if(event.target.closest('[data-topic-close]'))closeTopic();});
  dialog.addEventListener('keydown',event=>{if(event.key!=='Tab')return;const controls=[...dialog.querySelectorAll('button:not(:disabled),input:not(:disabled),textarea:not(:disabled)')].filter(control=>control.getClientRects().length);const first=controls[0],last=controls.at(-1);if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}});
  bindComposer(topicForm,()=>newDraft,()=>null);bindComposer(reply,()=>current()?.draft,()=>state.selected);
  async function withdraw(kind,id) {
    const key=kind+id,item=current();if(!item||withdrawPending.has(key))return;
    if(!confirm(kind==='topics'?'撤回整个话题？正文和附件将不可再查看，不能继续回复。':'撤回这条消息？正文和附件将不可再查看。'))return;
    withdrawPending.add(key);const button=kind==='topics'?$('collab-withdraw-topic'):messagesBox.querySelector(`[data-withdraw-message="${id}"]`);if(button)button.disabled=true;
    try {await request(`/${kind}/${id}/withdraw`,{});status($('collab-thread-status'),'撤回已受理，正在同步…');await poll();unreadChanged();}
    catch(error){if(state.selected===item.id)status($('collab-thread-status'),error.message+' 当前内容未替换，请刷新核对后重试。');}
    finally {withdrawPending.delete(key);if(button?.isConnected)button.disabled=false;}
  }
  $('collab-withdraw-topic').addEventListener('click',()=>withdraw('topics',state.selected));messagesBox.addEventListener('click',event=>{const link=event.target.closest('.collab-attachment');if(link&&!event.ctrlKey&&!event.metaKey){event.preventDefault();downloadFile(link);return;}const button=event.target.closest('[data-withdraw-message]');if(button)withdraw('messages',Number(button.dataset.withdrawMessage));});
  list.addEventListener('click',event=>{const button=event.target.closest('[data-topic-id]');if(button)selectTopic(Number(button.dataset.topicId));});
  $('collab-search').addEventListener('submit',event=>{event.preventDefault();state.q=$('collab-query').value.trim();state.page=1;loadTopics();});
  $('collab-prev').addEventListener('click',()=>{if(state.page>1){state.page--;loadTopics();}});$('collab-next').addEventListener('click',()=>{state.page++;loadTopics();});
  $('collab-list-retry').addEventListener('click',()=>loadTopics());$('collab-thread-retry').addEventListener('click',()=>selectTopic(state.selected));$('collab-older').addEventListener('click',older);
  $('collab-back').addEventListener('click',()=>{hub.classList.remove('is-thread');observer?.disconnect();history.replaceState(null,'',location.pathname);list.querySelector(`[data-topic-id="${state.selected}"]`)?.focus();});
  $('collab-new-messages').addEventListener('click',()=>{const item=current();if(!item)return;item.newCount=0;$('collab-new-messages').hidden=true;scroll.scrollTop=scroll.scrollHeight;observeMessages(item);});
  document.addEventListener('visibilitychange',()=>{clearTimeout(pollTimer);visibleTimers.forEach(clearTimeout);visibleTimers.clear();if(!document.hidden){if(current()?.loaded)observeMessages(current());poll();}});
  window.addEventListener('resize',()=>{if(current()?.loaded)observeMessages(current());});
  loadTopics();const initial=Number(new URLSearchParams(location.search).get('topic'));if(Number.isSafeInteger(initial)&&initial>0)selectTopic(initial,{updateURL:false,focus:false});pollTimer=setTimeout(poll,5000);
})();
