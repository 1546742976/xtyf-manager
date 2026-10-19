// PDF content is fetched by the native authenticated transport, never by a remote viewer.
import * as pdfjsLib from '/vendor/pdfjs/pdf.mjs';
pdfjsLib.GlobalWorkerOptions.workerSrc='/vendor/pdfjs/pdf.worker.mjs';
let pdf,index=1,rendering=null,sequence=0;
const status=document.getElementById('status');
async function show(){
  const generation=++sequence;rendering?.cancel();
  const page=await pdf.getPage(index);if(generation!==sequence)return;
  const width=Math.min(1400,Math.max(300,document.documentElement.clientWidth-24)),base=page.getViewport({scale:1});
  const viewport=page.getViewport({scale:width/base.width}),ratio=Math.min(devicePixelRatio||1,2),canvas=document.createElement('canvas');
  canvas.width=Math.floor(viewport.width*ratio);canvas.height=Math.floor(viewport.height*ratio);canvas.style.width=viewport.width+'px';
  canvas.setAttribute('aria-label','PDF 第 '+index+' 页');document.getElementById('pages').replaceChildren(canvas);
  document.getElementById('page-number').textContent=index+' / '+pdf.numPages;
  document.getElementById('previous').disabled=index===1;document.getElementById('next').disabled=index===pdf.numPages;
  rendering=page.render({canvasContext:canvas.getContext('2d'),viewport,transform:ratio!==1?[ratio,0,0,ratio,0,0]:null});
  try{await rendering.promise;status.hidden=true;}catch(error){if(error.name!=='RenderingCancelledException')throw error;}
}
document.getElementById('previous').onclick=()=>{if(index>1){index--;show().catch(error=>status.textContent=error.message);}};
document.getElementById('next').onclick=()=>{if(index<pdf.numPages){index++;show().catch(error=>status.textContent=error.message);}};
try{
  const task=pdfjsLib.getDocument({data:parent.XTYFPreview.data.slice(),isEvalSupported:false,enableXfa:false,useSystemFonts:true,cMapUrl:'/vendor/pdfjs/cmaps/',cMapPacked:true,standardFontDataUrl:'/vendor/pdfjs/standard_fonts/',wasmUrl:'/vendor/pdfjs/wasm/'});
  task.onPassword=(updatePassword,reason)=>{const password=prompt(reason===1?'此 PDF 需要密码':'密码不正确，请重试');if(password===null)task.destroy();else updatePassword(password);};
  pdf=await task.promise;await show();window.addEventListener('pagehide',()=>pdf.destroy());
}catch(error){status.hidden=false;status.textContent='PDF 预览失败：'+error.message+'。可使用“另存为”保存文件。';}
