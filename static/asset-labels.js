/* Print immutable identity, never a newly assigned number. Chinese text is rasterized locally. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id);let records=[];
  function label(unit){
    const code=(unit.item_id?'U:':'M:')+unit.id,canvas=document.createElement('canvas');canvas.width=900;canvas.height=500;
    const c=canvas.getContext('2d');c.fillStyle='#fff';c.fillRect(0,0,900,500);c.fillStyle='#000';c.font='bold 40px sans-serif';
    c.fillText(unit.item_name||unit.name,30,55,840);c.font='30px sans-serif';c.fillText(code,30,105);
    c.fillText(unit.serial||unit.specification||'',30,150,500);c.fillText(unit.campus_name||'',30,190,500);
    const matrix=new ZXing.QRCodeWriter().encode(code,ZXing.BarcodeFormat.QR_CODE,260,260,new Map());
    for(let y=0;y<260;y++)for(let x=0;x<260;x++)if(matrix.get(x,y))c.fillRect(610+x,80+y,1,1);
    const barcode=document.createElement('canvas');JsBarcode(barcode,code,{format:'CODE128',width:4,height:110,margin:20,displayValue:true,fontSize:30});
    c.drawImage(barcode,30,270,Math.min(530,barcode.width),190);return canvas;
  }
  async function render(){
    const form=$('asset-label-form'),w=Number(form.elements.width.value),h=Number(form.elements.height.value),a4=form.elements.paper.value==='a4';
    if(!Number.isFinite(w)||!Number.isFinite(h)||w<40||w>190||h<25||h>270)throw Error('标签宽度 40–190 mm，高度 25–270 mm');
    const preview=$('asset-label-preview');preview.replaceChildren();
    const previewLimit=12;
    for(const unit of records.slice(0,previewLimit)){const canvas=label(unit);canvas.style.width='min(100%,450px)';canvas.style.height='auto';preview.append(canvas);}
    if(records.length>previewLimit){
      const note=document.createElement('p');note.className='asset-label-preview-note';
      note.textContent=`为保持页面流畅，仅预览前 ${previewLimit} 张；下载的 PDF 仍包含全部 ${records.length} 张标签。`;
      preview.append(note);
    }
    $('asset-label-download').href='/api/assets/labels.pdf?'+new URLSearchParams({ids:records.map(u=>u.id).join(','),kind:records[0].item_id?'unit':'item',width:w,height:h,paper:a4?'a4':'thermal'});
    $('asset-label-download').hidden=false;
    $('asset-label-status').textContent=`已生成 ${records.length} 张标签。下载 PDF 后按实际尺寸打印，不缩放。补打使用原身份。`;
  }
  $('asset-label-form').onsubmit=e=>{e.preventDefault();render().catch(e=>$('asset-label-status').textContent=e.message);};
  $('asset-label-form').oninput=()=>{$('asset-label-download').hidden=true;$('asset-label-status').textContent='尺寸或纸张已修改，请先更新打印预览。';};
  $('asset-label-close').onclick=()=>$('asset-label-dialog').close();
  window.AssetLabels={open(values){records=values;$('asset-label-summary').textContent=`本次打印 ${values.length} 张标签 · ${values[0]?.item_name||values[0]?.name||''}。补打沿用原编号。`;window.AssetLedger.showDialog($('asset-label-dialog'));render().catch(e=>$('asset-label-status').textContent=e.message);},label};
})();
