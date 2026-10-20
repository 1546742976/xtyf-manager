(() => {
  'use strict';
  const localBase=location.protocol+'//'+location.host;
  const isLocal=url=>url.protocol===location.protocol&&url.host===location.host;
  const routes={
    '/':['dashboard.html','dashboard'], '/login':['login.html','login'], '/tasks':['board.html','tasks'],
    '/account':['account.html','account'], '/manage':['manage.html','manage','manage_tasks'],
    '/members':['members.html','members','manage_members'], '/discussions':['discussions.html','discussions'],
    '/funds':['funds.html','funds','access_funds'], '/invoices':['invoices.html','invoices'],
    '/assets':['assets.html','assets'], '/assets/detail':['assets.html','assets'],
    '/admin/mail':['mail_admin.html','mail','manage_mail'],
    '/admin/security':['security.html','security','manage_security']
  };
  const anonymousBootstrap=()=>({authenticated:false,me:null,user:null,permissions:{},role_names:{admin:'管理员',manager:'管理人员',member:'成员'},statuses:[],priorities:[]});
  let config={serverUrl:'',version:''},bootstrap=anonymousBootstrap();
  let route='/',frame=null,sequence=0,generation=0,historyIndex=0,serverGeneration=0,bootSequence=0,bootstrapSequence=0,booting=false,compatible=false,preview=null,noticeTimer;
  let compatibilityRejected=false,nativeInitialized=false,locallyReady=false,healthySent=false,nativeForeground=true,updatesServer=null,updateState={status:'unavailable'};
  let sessionStarted=false,websiteOpening=false,websiteActive=false;
  const requests=new Map(),resourceURLs=new Set();
  const host=document.getElementById('page-host'),settings=document.getElementById('client-settings'),previewDialog=document.getElementById('resource-preview');
  function notify(text){const el=document.getElementById('client-notice');el.textContent=text;el.hidden=false;clearTimeout(noticeTimer);noticeTimer=setTimeout(()=>el.hidden=true,6000);}
  function connection(text=''){const el=document.getElementById('connection-status');el.querySelector('span').textContent=text;el.hidden=!text;}
  function supports(capability){return XTYFUpdates.supports(config,capability);}
  function updateStatus(value){
    updateState=value||{status:'unavailable'};
    const labels={current:'当前页面已是最新版本。',pending:'新页面已下载，将在下次启动生效。',unavailable:'服务器暂未发布页面更新。',incompatible:'此更新需要升级客户端。',failed:'页面更新失败，可手动重试。'};
    document.getElementById('update-status').textContent=updateState.message||labels[updateState.status]||'正在检查页面更新…';
    document.getElementById('update-version').textContent='当前页面：'+(updateState.currentVersion||'内置版本')+(updateState.pendingVersion?' · 待生效：'+updateState.pendingVersion:'');
    document.getElementById('updates-check').disabled=updateState.status==='checking';
    document.getElementById('updates-apply').hidden=updateState.status!=='pending';
    document.getElementById('updates-installer').hidden=!updateState.installer;
    if(updateState.status==='incompatible'&&!updateState.installer)document.getElementById('update-status').textContent+=' 暂无此平台的正式安装包。';
  }
  const updates=XTYFUpdates.create({invoke:(action,args)=>XTYFNative.invoke(action,args),visible:()=>!document.hidden&&nativeForeground&&!websiteActive,changed:updateStatus});
  function markHealthy(){
    if(!nativeInitialized||!locallyReady||healthySent||!supports('ui_updates'))return;
    healthySent=true;
    XTYFNative.invoke('ui_ready',{viewToken:window.__XTYF_VIEW_TOKEN__||null}).catch(error=>notify('页面已打开，但更新确认失败：'+error.message));
  }
  function pathFor(value){
    const url=new URL(value,localBase+route);
    if(!isLocal(url)&&!isServer(url))throw new Error('业务请求仅允许连接当前服务器。');
    return url.pathname+url.search;
  }
  function isServer(url){return [config.serverUrl,config.effectiveServerUrl].some(value=>value&&url.origin===new URL(value).origin);}
  function bytes(response){return Uint8Array.from(atob(response.body||''),char=>char.charCodeAt(0));}
  function asResponse(result){
    const status=Number(result.status),headers=new Headers(result.headers||{});
    return new Response([204,205,304].includes(status)?null:result.encoding==='base64'?bytes(result):result.body??'',{status,headers});
  }
  async function request(input,options={}){
    if(websiteActive)throw new Error('已切换到网站模式，请在网站中继续操作。');
    if(compatibilityRejected)throw new Error('服务器协议不兼容，请在客户端设置中检查更新或更换服务器。');
    const server=serverGeneration;
    let path=pathFor(input instanceof Request?input.url:input);
    if(path.split('?')[0]==='/login')path='/api/client/login';
    if(path.split('?')[0]==='/logout')path='/api/client/logout';
    const id='request-'+(++sequence),method=String(options.method||'GET').toUpperCase();
    const headers=Object.fromEntries(new Headers(options.headers||{}));
    // Session cookies and CSRF are always owned by the native transport.
    for(const key of Object.keys(headers))if(/^(cookie|authorization|x-csrf-token)$/i.test(key))delete headers[key];
    headers['X-XTYF-Client-Protocol']='1';
    const args={path,method,headers,requestId:id};
    if(options.body?._nativeMultipart){
      const fields=[],files=[];
      for(const [name,value] of options.body.entries()){
        if(value&&typeof value==='object'&&value.handle)files.push({field:name,handle:value.handle,name:value.name,mime:value.type||value.mime||'application/octet-stream'});
        else if(typeof value==='string')fields.push({name,value});
        else if(value?.name)throw new Error('请使用应用内文件选择器添加文件。');
      }
      args.multipart={fields,files};
    }else if(options.body!==undefined&&options.body!==null)args.body=String(options.body);
    if(path==='/api/client/logout'){delete args.multipart;args.body='{}';headers['Content-Type']='application/json';}
    const controller=new AbortController();requests.set(id,controller);
    let abort;
    const aborted=new Promise((_,reject)=>{abort=()=>{controller.abort();XTYFNative.invoke('cancel',{requestId:id}).catch(()=>{});reject(new DOMException('请求已取消','AbortError'));};});
    if(options.signal?.aborted){requests.delete(id);throw new DOMException('请求已取消','AbortError');}
    options.signal?.addEventListener('abort',abort,{once:true});controller.signal.addEventListener('abort',()=>{}, {once:true});
    requests.set(id,{abort});
    try{
      const response=asResponse(await Promise.race([XTYFNative.invoke('request',args),aborted]));
      if(server!==serverGeneration)throw new DOMException('服务器已切换','AbortError');
      if(response.status===401&&path!=='/api/client/login'&&path!=='/api/client/bootstrap'&&route.split('?')[0]!=='/login'){
        bootstrap=anonymousBootstrap();setTimeout(()=>{if(server===serverGeneration)navigate('/login?next='+encodeURIComponent(route),true);},0);
      }else if(response.status===403){const data=await response.clone().json().catch(()=>({}));if(data.reason==='password_change_required')setTimeout(()=>{if(server===serverGeneration)navigate('/account',true);},0);}
      const changesSession=path==='/api/client/login'||path==='/api/client/logout'||path==='/api/password'||path==='/api/account/preferences'||path==='/api/account/profile'||path==='/api/account/avatar'||/^\/api\/(?:admin\/permissions\/|member\/\d+\/profile)/.test(path);
      if(changesSession&&!['GET','HEAD','OPTIONS'].includes(method)&&response.ok){
        const info=await response.clone().json().catch(()=>({}));
        if(info.ok!==false){try{await refreshBootstrap(server);}catch(error){if(!error.superseded&&server===serverGeneration)connection(error.message);}}
      }
      return response;
    }finally{requests.delete(id);options.signal?.removeEventListener('abort',abort);}
  }
  function cancelRequests(){for(const item of requests.values())item.abort();requests.clear();}
  function cancelPage(){cancelRequests();for(const url of resourceURLs)URL.revokeObjectURL(url);resourceURLs.clear();}
  async function refreshBootstrap(server=serverGeneration,probe=false){
    const attempt=++bootstrapSequence;
    const reply=probe&&supports('website_fallback')
      ?await XTYFNative.invoke('probe_connection',{})
      :await XTYFNative.invoke('request',{path:'/api/client/bootstrap',method:'GET',headers:{'X-XTYF-Client-Protocol':'1'},requestId:'bootstrap-'+(++sequence)});
    if(server!==serverGeneration||attempt!==bootstrapSequence)throw Object.assign(new DOMException('初始化已被替换','AbortError'),{superseded:true});
    if(probe&&supports('website_fallback')){
      config.effectiveServerUrl=reply.effectiveServerUrl;
      document.getElementById('effective-server-url').textContent='实际连接：'+(config.effectiveServerUrl||config.serverUrl);
    }
    const result=probe&&supports('website_fallback')?reply.response:reply;
    const outcome=XTYFConnection.classify(result,XTYFUpdates.compatibleServer);
    if(outcome.mode==='website'){compatible=false;compatibilityRejected=true;throw Object.assign(new Error(outcome.message),{incompatible:true});}
    if(outcome.mode==='error')throw new Error(outcome.message);
    bootstrap=outcome.data;compatible=true;compatibilityRejected=false;connection('');return bootstrap;
  }
  function normalized(value){const u=new URL(value,localBase+route);return u.pathname+u.search+u.hash;}
  function setRoute(value,replace=false,render=true){
    route=normalized(value);const url='#'+route;
    if(!replace)historyIndex++;
    if(replace)history.replaceState({xtyf:true,index:historyIndex},'',url);else history.pushState({xtyf:true,index:historyIndex},'',url);
    if(render)renderRoute();
  }
  function navigate(value,replace=false){
    const u=new URL(value,localBase+route);
    if(!isLocal(u)&&!isServer(u))return XTYFNative.invoke('open_external',{url:u.href}).catch(error=>notify(error.message));
    setRoute(u.pathname+u.search+u.hash,replace);
  }
  function localError(title,message){
    cancelPage();if(frame){frame.remove();frame=null;}
    const section=document.createElement('section');section.className='local-error';
    const h=document.createElement('h1');h.textContent=title;
    const p=document.createElement('p');p.textContent=message;
    const retry=document.createElement('button');retry.textContent='重试';retry.onclick=()=>boot();
    const edit=document.createElement('button');edit.textContent='服务器设置';edit.onclick=openSettings;
    section.append(h,p,retry,edit);host.replaceChildren(section);
    if(supports('website_fallback')){const website=document.createElement('button');website.textContent='打开网站';website.onclick=()=>openWebsite();section.append(website);}
    locallyReady=true;markHealthy();
  }
  function renderRoute(){
    if(compatibilityRejected)return localError('服务器需要更新','服务器协议不兼容，请在客户端设置中检查更新或更换服务器。');
    const url=new URL(route,localBase),key=url.pathname.replace(/\/$/,'')||'/';
    const entry=routes[key];
    if(!entry)return localError('本地页面不存在','此链接没有对应的应用页面。可返回工作台或检查服务器地址。');
    if(!bootstrap.authenticated&&key!=='/login'){
      setRoute('/login?next='+encodeURIComponent(route),true,false);return renderRoute();
    }
    if(bootstrap.authenticated&&bootstrap.me?.must_change_password&&key!=='/account'){
      setRoute('/account',true,false);return renderRoute();
    }
    if(bootstrap.authenticated&&key==='/login'){
      setRoute(url.searchParams.get('next')||'/',true,false);return renderRoute();
    }
    if(key.startsWith('/assets')&&!bootstrap.assets_enabled)return localError('服务器暂未启用资产管理','请联系管理员更新服务器；已有任务和其他业务仍可正常使用。');
    if(entry[2]&&!bootstrap.permissions[entry[2]])return localError('没有访问权限','当前账户不能访问此页面。');
    const taskType=url.searchParams.get('type')==='daily'?'daily':'vehicle',project=url.searchParams.get('project')||'';
    const context={...bootstrap,page:entry[1],task_type:taskType,project_id:taskType==='daily'&&/^[1-9]\d{0,18}$/.test(project)?project:''};
    const html=XTYFTemplates.render(entry[0],context);
    cancelPage();generation++;locallyReady=false;
    if(frame)frame.remove();
    frame=document.createElement('iframe');frame.title='巡天御风工作空间';
    frame.setAttribute('referrerpolicy','no-referrer');
    // Both template and scripts are packaged. All server strings were escaped by the compiler.
    frame.srcdoc=html.replace('<head>','<head><base href="'+localBase+'/"><script src="/page-runtime.js"></script>');
    host.replaceChildren(frame);document.title='巡天御风管理系统';
  }
  async function openWebsite(automatic=false){
    if(websiteOpening||websiteActive||!supports('website_fallback'))return;
    if(!automatic&&!window.confirm('在客户端内打开网站？未保存的表单、头像裁剪及上传状态可能丢失。'))return;
    websiteOpening=true;
    const server=serverGeneration;
    try{
      await XTYFNative.invoke('open_website',{});
      if(server!==serverGeneration)return;
      websiteActive=true;cancelRequests();updates.visibility();if(settings.open)settings.close();
      // Manual recovery may discover an HTTPS root even when bootstrap failed.
      const current=await XTYFNative.invoke('get_config',{}).catch(()=>null);
      if(server!==serverGeneration)return;
      if(current){config={...config,...current};document.getElementById('effective-server-url').textContent='实际连接：'+(config.effectiveServerUrl||config.serverUrl);}
      connection('已在客户端内打开网站。返回后可调整地址或重新尝试本地界面。');
      locallyReady=true;markHealthy();
    }catch(error){if(server===serverGeneration)connection('网站打开失败：'+error.message);}
    finally{websiteOpening=false;}
  }
  async function boot(){
    if(booting)return;booting=true;const server=serverGeneration,attempt=++bootSequence;
    const cold=!sessionStarted;
    const identity=value=>JSON.stringify([!!value.authenticated,value.me?.id,!!value.me?.must_change_password,value.permissions||{}]);
    const previousIdentity=identity(bootstrap);
    try{
      const nextConfig=await XTYFNative.invoke('get_config',{});
      if(server!==serverGeneration||attempt!==bootSequence)return;
      config=nextConfig;document.getElementById('server-url').value=config.serverUrl;
      document.getElementById('client-version').textContent='客户端 '+config.version;
      nativeInitialized=true;document.getElementById('client-updates').hidden=!supports('ui_updates');
      document.getElementById('connection-website').hidden=!supports('website_fallback');
      document.getElementById('settings-website').hidden=!supports('website_fallback');
      document.getElementById('effective-server-url').textContent=supports('website_fallback')?'实际连接：'+(config.effectiveServerUrl||config.serverUrl):'';
      markHealthy();
      await refreshBootstrap(server,true);
      if(server!==serverGeneration||attempt!==bootSequence)return;
      // A connectivity retry must not replace the user's form/crop document.
      // Authentication or permission changes still need the authoritative page.
      if(cold||!frame||previousIdentity!==identity(bootstrap))renderRoute();
    }catch(error){
      if(server!==serverGeneration||attempt!==bootSequence||error.superseded)return;
      if(error.incompatible){
        if(cold&&supports('website_fallback'))await openWebsite(true);
        else if(!cold&&frame)connection(error.message+' 当前输入已保留，可手动打开网站。');
        else localError('服务器需要更新',error.message);
      }else{
        const message='暂时无法连接服务器，页面已在本地打开。'+(error.message?' '+error.message:'');
        connection(message);if(!frame)localError('暂时无法连接服务器',message);
      }
    }finally{if(attempt===bootSequence){
      booting=false;sessionStarted=true;
      const updateKey=config.serverUrl+'|'+(config.effectiveServerUrl||'');
      if(updatesServer!==updateKey){updatesServer=updateKey;updates.configure(config);}
    }}
  }
  function openSettings(){document.getElementById('server-url').value=config.serverUrl;document.getElementById('settings-error').textContent='';settings.showModal();}
  async function download(value,filename='download'){
    if(compatibilityRejected)throw new Error('服务器协议不兼容，请先检查更新。');
    return XTYFNative.invoke('download',{path:pathFor(value),filename});
  }
  async function readResource(value){
    if(compatibilityRejected)throw new Error('服务器协议不兼容，请先检查更新。');
    const server=serverGeneration;
    const result=await XTYFNative.invoke('read_resource',{path:pathFor(value)});
    if(server!==serverGeneration)throw new DOMException('服务器已切换','AbortError');
    if(result.status!==200)throw new Error('附件读取失败（'+result.status+'）');
    return result;
  }
  async function imageURL(value){
    const own=generation,result=await readResource(value),response=asResponse(result),blob=await response.blob();
    if(own!==generation)throw new DOMException('页面已离开','AbortError');
    if(!blob.type.startsWith('image/'))throw new Error('附件不是图片');
    const url=URL.createObjectURL(blob);resourceURLs.add(url);return url;
  }
  function closePreview(){previewDialog.close();preview=null;window.XTYFPreview=null;document.getElementById('preview-content').replaceChildren();}
  async function previewResource(value,filename='附件'){
    const result=await readResource(value),response=asResponse(result),blob=await response.blob();
    if(blob.type!=='application/pdf'&&!blob.type.startsWith('image/'))return download(value,filename);
    preview={path:value,filename};document.getElementById('preview-title').textContent=filename;
    const container=document.getElementById('preview-content');container.replaceChildren();
    if(blob.type==='application/pdf'){
      window.XTYFPreview={data:new Uint8Array(await blob.arrayBuffer()),filename};const child=document.createElement('iframe');child.title='PDF 预览';child.src='/pdf-viewer.html';container.append(child);
    }else{const img=document.createElement('img'),url=URL.createObjectURL(blob);resourceURLs.add(url);img.src=url;img.alt=filename;container.append(img);}
    previewDialog.showModal();
  }
  function back(){
    if(settings.open){settings.close();return true;}if(previewDialog.open){closePreview();return true;}
    const doc=frame?.contentDocument;if(doc){
      const dialog=[...doc.querySelectorAll('dialog[open]')].pop();if(dialog){if(dialog.dispatchEvent(new Event('cancel',{cancelable:true})))dialog.close();return true;}
      const modal=[...doc.querySelectorAll('.show,.modal-mask:not(.hidden)')].reverse().find(el=>/modal|mask/i.test(el.className));
      if(modal){const close=modal.querySelector('[data-close],.modal-close,[onclick*="close"],button[aria-label*="关闭"]');if(close){close.click();return true;}}
      if(doc.body.classList.contains('ws-menu-open')){doc.querySelector('[data-ws-menu]')?.click();return true;}
    }
    if(historyIndex>0){history.back();return true;}
    if(route!=='/'&&!route.startsWith('/login')){navigate('/',true);return true;}return false;
  }
  window.XTYF={request,download,readResource,imageURL,preview:previewResource,navigate,notify,openSettings,localBase,isLocal,isServer,
    supports,pageReady:own=>{if(own===generation){locallyReady=true;markHealthy();}},
    invoke:(action,args)=>XTYFNative.invoke(action,args),get route(){return route;},get bootstrap(){return bootstrap;},get config(){return config;},get generation(){return generation;},
    replaceRoute:value=>setRoute(value,true,false),isPage:value=>Object.hasOwn(routes,new URL(value,localBase).pathname.replace(/\/$/,'')||'/'),
    openExternal:url=>XTYFNative.invoke('open_external',{url}),get compatible(){return compatible;}};
  window.XTYFClient={back};
  document.getElementById('client-settings-open').onclick=openSettings;document.getElementById('settings-cancel').onclick=()=>settings.close();
  document.getElementById('connection-retry').onclick=()=>boot();
  document.getElementById('connection-website').onclick=()=>openWebsite();
  document.getElementById('settings-website').onclick=()=>openWebsite();
  window.addEventListener('xtyf-website-return',event=>{
    websiteActive=false;updates.visibility();
    connection(compatible?'':'已返回本地界面。可重试连接、调整地址或再次打开网站。');
    if(event.detail?.intent==='settings')openSettings();
    else if(event.detail?.intent==='retry')boot();
  });
  document.getElementById('updates-check').onclick=()=>updates.check(true);
  document.getElementById('updates-apply').onclick=async()=>{
    if(updateState.status!=='pending'||!window.confirm('立即重载页面？未保存的表单、头像裁剪及上传状态可能丢失。'))return;
    const button=document.getElementById('updates-apply');button.disabled=true;
    try{await XTYFNative.invoke('apply_update',{});}catch(error){notify('无法启用新页面：'+error.message);}finally{button.disabled=false;}
  };
  document.getElementById('updates-installer').onclick=async()=>{
    const path=updateState.installer?.url;
    if(typeof path!=='string'||!path.startsWith('/')||path.startsWith('//')||/[\\#]/.test(path))return notify('安装包链接无效，请重新检查更新。');
    const extensions={'windows-x86_64':'exe','ubuntu-aarch64':'deb','fedora-x86_64':'rpm',android:'apk'};
    const suffix=extensions[config.platform];if(!suffix)return notify('暂无此平台的正式安装包。');
    const button=document.getElementById('updates-installer');button.disabled=true;
    try{
      const result=await XTYFNative.invoke('download',{path,filename:'xtyf-manager.'+suffix,expectedSha256:updateState.installer.sha256});
      notify(result.cancelled?'已取消保存安装包。':'安装包已保存，请按系统安装步骤完成升级。');
    }catch(error){notify('安装包保存失败：'+error.message);}finally{button.disabled=false;}
  };
  document.addEventListener('visibilitychange',()=>updates.visibility());
  window.addEventListener('xtyf-foreground',event=>{nativeForeground=event.detail?.active!==false;updates.visibility();});
  window.addEventListener('focus',()=>updates.visibility());
  window.addEventListener('pagehide',()=>updates.dispose());
  document.getElementById('preview-close').onclick=closePreview;previewDialog.addEventListener('cancel',closePreview);
  document.getElementById('preview-save').onclick=()=>preview&&download(preview.path,preview.filename).catch(error=>notify(error.message));
  document.getElementById('server-form').onsubmit=async event=>{
    event.preventDefault();const button=event.target.querySelector('[type="submit"]');button.disabled=true;
    // Supersede a pending initial boot before invoking native server replacement.
    serverGeneration++;bootSequence++;bootstrapSequence++;booting=false;updates.dispose();updatesServer=null;
    try{
      const result=await XTYFNative.invoke('set_server',{serverUrl:document.getElementById('server-url').value.trim()});
      config={...config,...result};cancelPage();bootstrap=anonymousBootstrap();compatible=false;compatibilityRejected=false;historyIndex=0;websiteActive=false;
      if(previewDialog.open)closePreview();
      settings.close();navigate('/login',true);await boot();
    }catch(error){document.getElementById('settings-error').textContent=error.message;await boot();}finally{button.disabled=false;}
  };
  window.addEventListener('popstate',event=>{historyIndex=event.state?.index||0;route=location.hash.slice(1)||'/';renderRoute();});
  window.addEventListener('keydown',event=>{if(event.key==='Escape'&&back())event.preventDefault();});
  route=location.hash.slice(1)||'/';renderRoute();
  // Finish loading the initial local iframe before bootstrap can replace it.
  // Removing a loading srcdoc frame on a fast protocol rejection crashes WebKit.
  if(document.readyState==='complete')boot();
  else window.addEventListener('load',()=>boot(),{once:true});
})();
