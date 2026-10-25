/* Preview and save the same authenticated PDF bytes. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id),form=$('asset-label-form'),link=$('asset-label-download');
  const Layout=window.AssetLabelLayout;
  const SETTINGS_KEY='xtyf.asset.labels.v2',defaults={paper:'thermal',width:50,height:30,layout:'auto',font:11,font_width:88};
  function settings(){
    const f=$('asset-label-form');
    const o={paper:f.elements.paper.value,width:Number(f.elements.width.value),height:Number(f.elements.height.value),
      layout:f.elements.layout.value,font:Number(f.elements.font.value),font_width:Number(f.elements.font_width.value)};
    Layout.compute(o.width,o.height,o.layout);
    if(!['thermal','a4'].includes(o.paper))throw Error('纸张类型无效');
    if(!Number.isFinite(o.font)||o.font<6||o.font>18||!Number.isFinite(o.font_width)||o.font_width<70||o.font_width>110)throw Error('字体参数无效');
    return o;
  }
  function remember(){
    try{localStorage.setItem(SETTINGS_KEY,JSON.stringify(settings()));}catch(_){/* Private mode or in-progress input. */}
  }
  function restore(){
    let saved;
    try{saved=JSON.parse(localStorage.getItem(SETTINGS_KEY)||'null');}catch(_){return;}
    if(!saved||typeof saved!=='object')return;
    const f=$('asset-label-form');
    for(const [key,value] of Object.entries(defaults)){
      const v=saved[key];
      if(v===undefined)continue;
      if(key==='paper'||key==='layout'){if([...f.elements[key].options].some(o=>o.value===v))f.elements[key].value=v;}
      else if(Number.isFinite(Number(v)))f.elements[key].value=String(v);
    }
    try{settings();}catch(_){for(const [key,value] of Object.entries(defaults))f.elements[key].value=String(value);}
  }
  let records=[],generation=0,controller=null,task=null,documentPdf=null,renderTask=null,url=null,bytes=null,page=1,pdfjs=null;
  const status=text=>$('asset-label-status').textContent=text;
  function clear(){
    generation++;controller?.abort();controller=null;renderTask?.cancel();renderTask=null;
    if(task)task.destroy().catch(()=>{});task=null;documentPdf=null;bytes=null;
    if(url)URL.revokeObjectURL(url);url=null;link.hidden=true;link.removeAttribute('href');
    $('asset-label-preview').replaceChildren();$('asset-label-pages').hidden=true;
  }
  async function showPage(own){
    renderTask?.cancel();const current=page,doc=documentPdf,pdfPage=await doc.getPage(current);
    if(own!==generation||doc!==documentPdf||current!==page)return;
    const natural=pdfPage.getViewport({scale:1}),width=Math.min(800,$('asset-label-preview').clientWidth||600);
    const scale=width/natural.width,ratio=Math.min(devicePixelRatio||1,2),viewport=pdfPage.getViewport({scale:scale*ratio});
    const canvas=document.createElement('canvas');canvas.width=Math.ceil(viewport.width);canvas.height=Math.ceil(viewport.height);
    canvas.style.width='100%';canvas.style.height='auto';canvas.setAttribute('aria-label',`PDF 第 ${current} 页`);
    const rendering=pdfPage.render({canvasContext:canvas.getContext('2d'),viewport});renderTask=rendering;
    try{await rendering.promise;}catch(e){if(e.name==='RenderingCancelledException')return;throw e;}
    if(own!==generation||doc!==documentPdf||current!==page)return;
    $('asset-label-preview').replaceChildren(canvas);$('asset-label-pages').hidden=false;
    $('asset-label-page').textContent=`第 ${current} / ${doc.numPages} 页`;
    $('asset-label-prev').disabled=current===1;$('asset-label-next').disabled=current===doc.numPages;
  }
  async function render(){
    clear();const own=generation;status('正在生成并读取 PDF…');
    try{
      const opts=settings();remember();
      const geometry=Layout.compute(opts.width,opts.height,opts.layout);
      $('asset-label-layout-info').textContent=(geometry.mode==='wide'?'左右分栏':'上文下条码')+' · '+opts.font+' pt · 字宽 '+opts.font_width+'% '+Layout.warnings(geometry).join('；');
      const path='/api/assets/labels.pdf?'+new URLSearchParams({ids:records.map(u=>u.id).join(','),kind:records[0].item_id?'unit':'item',...opts});
      let content;
      if(window.XTYF){
        const response=await window.XTYF.readResource(path);
        if(response.status!==200)throw Error('PDF 读取失败，请检查登录状态');
        content=Uint8Array.from(atob(response.body),c=>c.charCodeAt(0));
      }else{
        controller=new AbortController();const response=await fetch(path,{credentials:'same-origin',signal:controller.signal});
        if(!response.ok)throw Error('PDF 生成失败（'+response.status+'）');
        content=new Uint8Array(await response.arrayBuffer());
      }
      if(own!==generation)return;
      if(content.length>20*1024*1024||new TextDecoder().decode(content.subarray(0,5))!=='%PDF-')throw Error('服务器没有返回有效 PDF');
      pdfjs??=await import('/vendor/pdfjs/pdf.mjs');if(own!==generation)return;
      const vendor=new URL('/vendor/pdfjs/',location.href).href;
      pdfjs.GlobalWorkerOptions.workerSrc=vendor+'pdf.worker.mjs';
      // Fetch fonts in the local worker, not the business frame's native API adapter.
      const loading=pdfjs.getDocument({data:content.slice(),isEvalSupported:false,enableXfa:false,useSystemFonts:true,useWorkerFetch:true,
        cMapUrl:vendor+'cmaps/',cMapPacked:true,standardFontDataUrl:vendor+'standard_fonts/',wasmUrl:vendor+'wasm/'});
      task=loading;const doc=await loading.promise;if(own!==generation){await loading.destroy();return;}
      documentPdf=doc;page=1;await showPage(own);if(own!==generation)return;
      bytes=content;url=URL.createObjectURL(new Blob([bytes],{type:'application/pdf'}));link.href=url;link.hidden=false;
      status(`共 ${doc.numPages} 页 · ${records.length} 张标签。预览与下载为同一份 PDF，请按实际尺寸打印。`);
      if(window.XTYF&&!window.XTYF.config?.capabilities?.includes('pdf_save')){link.hidden=true;status('PDF 预览已生成；请升级客户端以保存同一份 PDF，或在浏览器下载。');}
    }catch(e){if(own!==generation)return;clear();status('PDF 预览失败：'+e.message);}
  }
  form.onsubmit=e=>{e.preventDefault();render();};
  form.oninput=()=>{clear();remember();status('配置已更新，请点击“更新打印预览”。');};
  form.onchange=()=>render();
  for(const [id,delta] of [['asset-label-prev',-1],['asset-label-next',1]])$(id).onclick=()=>{
    if(!documentPdf)return;page=Math.max(1,Math.min(documentPdf.numPages,page+delta));
    showPage(generation).catch(e=>{clear();status('PDF 预览失败：'+e.message);});
  };
  link.onclick=async e=>{
    if(!window.XTYF)return;e.preventDefault();if(!bytes)return;
    const own=generation;let binary='';for(let i=0;i<bytes.length;i+=32768)binary+=String.fromCharCode(...bytes.subarray(i,i+32768));
    try{await window.XTYF.invoke('download',{filename:'asset-labels.pdf',pdfBody:btoa(binary)});}
    catch(error){if(own===generation)status('PDF 保存失败：'+error.message);}
  };
  $('asset-label-close').onclick=()=>$('asset-label-dialog').close();
  $('asset-label-dialog').addEventListener('close',()=>{if(!$('asset-label-dialog').open)clear();});window.addEventListener('pagehide',clear);
  restore();
  window.AssetLabels={open(values){records=values;$('asset-label-summary').textContent=`本次打印 ${values.length} 张标签。补打沿用原编号。`;window.AssetLedger.showDialog($('asset-label-dialog'));return render();}};
})();
