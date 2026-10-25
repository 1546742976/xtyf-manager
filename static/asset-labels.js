/* In-browser preview; PDF output uses the same mm geometry and original identities. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id),Layout=window.AssetLabelLayout;
  const SETTINGS_KEY='xtyf.asset.labels.v2',defaults={paper:'thermal',width:50,height:30,layout:'auto',font:11,font_width:88};
  let records=[];
  const ptmm=n=>n*25.4/72;
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
  function drawText(ctx,lines,rect,basePt,stretch,px){
    const maxPt=Math.max(4,Math.min(basePt,rect.h/ptmm(3.35)));
    const titlePx=ptmm(maxPt)*px,secondaryPx=ptmm(Math.max(4,maxPt*.77))*px;
    const widthPx=rect.w*px,scale=stretch/100;
    let y=rect.y*px+titlePx*.88,used=0;
    ctx.fillStyle='#000';ctx.textBaseline='alphabetic';
    ctx.font='bold '+titlePx+'px sans-serif';
    const title=String(lines[0]||''),parts=[];let line='';
    for(const ch of title){
      if(line&&ctx.measureText(line+ch).width*scale>widthPx){parts.push(line);line='';}
      line+=ch;
    }
    if(line)parts.push(line);
    let display=parts.slice(0,2);
    if(parts.length>2&&display.length)display[1]=display[1].slice(0,-1)+'…';
    for(const part of display){
      ctx.save();ctx.translate(rect.x*px,0);ctx.scale(scale,1);ctx.fillText(part,0,y,widthPx/scale);ctx.restore();
      y+=titlePx*1.12;used++;
    }
    ctx.font=secondaryPx+'px sans-serif';
    for(const part of lines.slice(1)){
      if(!part||y+secondaryPx*.1>(rect.y+rect.h)*px)break;
      ctx.save();ctx.translate(rect.x*px,0);ctx.scale(scale,1);ctx.fillText(part,0,y,widthPx/scale);ctx.restore();
      y+=secondaryPx*1.16;
    }
    return maxPt;
  }
  function label(unit,opts=settings()){
    const code=(unit.item_id?'U:':'M:')+unit.id,g=Layout.compute(opts.width,opts.height,opts.layout);
    const px=6,canvas=document.createElement('canvas');canvas.width=Math.round(opts.width*px);canvas.height=Math.round(opts.height*px);
    const c=canvas.getContext('2d');c.fillStyle='#fff';c.fillRect(0,0,canvas.width,canvas.height);
    const name=unit.item_name||unit.name||'',serial=unit.serial||unit.specification||'';
    drawText(c,[name,code,serial],g.text,opts.font,opts.font_width,px);
    const side=Math.min(g.qr.w,g.qr.h),matrix=new ZXing.QRCodeWriter().encode(code,ZXing.BarcodeFormat.QR_CODE,0,0,new Map());
    const modules=matrix.getWidth(),step=side*px/modules;
    c.fillStyle='#000';
    for(let y=0;y<modules;y++)for(let x=0;x<modules;x++)if(matrix.get(x,y))c.fillRect(Math.round((g.qr.x*px)+x*step),Math.round((g.qr.y*px)+y*step),Math.ceil(step),Math.ceil(step));
    const source=document.createElement('canvas');
    JsBarcode(source,code,{format:'CODE128',width:2,height:65,margin:8,displayValue:false});
    const caption=Math.max(5,Math.min(8,opts.font*.65)),capHeight=ptmm(caption);
    const box=g.barcode,barHeight=Math.max(1,box.h-capHeight-0.3);
    c.drawImage(source,box.x*px,box.y*px,box.w*px,barHeight*px);
    c.font=(ptmm(caption)*px)+'px sans-serif';c.fillStyle='#000';c.textAlign='center';
    c.fillText(code,(box.x+box.w/2)*px,(box.y+box.h)*px,box.w*px);
    return canvas;
  }
  async function render(){
    const opts=settings(),preview=$('asset-label-preview');preview.replaceChildren();
    const count=12;
    for(const unit of records.slice(0,count)){
      const canvas=label(unit,opts);canvas.style.width='min(100%,450px)';canvas.style.height='auto';
      canvas.style.aspectRatio=opts.width+' / '+opts.height;preview.append(canvas);
    }
    if(records.length>count){
      const note=document.createElement('p');note.className='asset-label-preview-note';
      note.textContent='为保持页面流畅，仅预览前 '+count+' 张；下载 PDF 包含全部 '+records.length+' 张标签。';preview.append(note);
    }
    const g=Layout.compute(opts.width,opts.height,opts.layout),warnings=Layout.warnings(g);
    $('asset-label-layout-info').textContent='当前：'+(g.mode==='wide'?'左右分栏':'上文下条码')+' · '+opts.font+' pt · 字宽 '+opts.font_width+'%'+(warnings.length?'。注意：'+warnings.join('；'):'');
    $('asset-label-download').href='/api/assets/labels.pdf?'+new URLSearchParams({
      ids:records.map(u=>u.id).join(','),kind:records[0].item_id?'unit':'item',width:opts.width,height:opts.height,
      paper:opts.paper,layout:opts.layout,font:opts.font,font_width:opts.font_width
    });
    $('asset-label-download').hidden=false;
    $('asset-label-status').textContent='已生成 '+records.length+' 张标签；PDF 使用实际毫米尺寸打印，禁用打印机缩放。';
    remember();
  }
  const form=$('asset-label-form');
  form.onsubmit=e=>{e.preventDefault();render().catch(e=>$('asset-label-status').textContent=e.message);};
  form.oninput=()=>{remember();$('asset-label-download').hidden=true;$('asset-label-status').textContent='配置已更新，请点击“更新打印预览”。';};
  $('asset-label-close').onclick=()=>$('asset-label-dialog').close();
  restore();
  window.AssetLabels={open(values){
    records=values||[];if(!records.length)return;
    $('asset-label-summary').textContent='本次打印 '+records.length+' 张标签 · '+(records[0].item_name||records[0].name||'')+'。补打沿用原编号。';
    window.AssetLedger.showDialog($('asset-label-dialog'));render().catch(e=>$('asset-label-status').textContent=e.message);
  },label};
})();