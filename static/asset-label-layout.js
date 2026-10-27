/* Millimetre-based layout shared by browser preview and PDF's equivalent geometry. */
(function(root,factory){
  const api=factory();
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  if(root)root.AssetLabelLayout=api;
})(typeof window!=='undefined'?window:undefined,function(){
  'use strict';
  const clamp=(n,a,b)=>Math.min(b,Math.max(a,n));
  function compute(w,h,preferred='auto',options={}){
    if(!Number.isFinite(w)||!Number.isFinite(h)||w<6||w>190||h<6||h>270)throw Error('标签宽度 6–190 mm，高度 6–270 mm');
    if(!['auto','wide','compact'].includes(preferred))throw Error('不支持的标签排版');
    const textOn=options.show_text??true,qrOn=options.show_qr??true,barOn=options.show_barcode??true;
    if([textOn,qrOn,barOn].some(v=>typeof v!=='boolean'))throw Error('标签显示设置无效');
    if(!textOn&&!qrOn&&!barOn)throw Error('文字、二维码和条形码至少保留一项');
    const mode=preferred==='auto'?(w/h>=1.5&&w>=36&&h>=20?'wide':'compact'):preferred;
    const pad=clamp(Math.min(w,h)*.045,.75,1.6),gap=clamp(Math.min(w,h)*.035,.55,1.3);
    const iw=w-2*pad,ih=h-2*pad;
    let text=null,qr=null,barcode=null;
    if(Number(textOn)+Number(qrOn)+Number(barOn)===1){
      if(qrOn){const side=Math.min(iw,ih);qr={x:(w-side)/2,y:(h-side)/2,w:side,h:side};}
      else if(textOn)text={x:pad,y:pad,w:iw,h:ih};
      else barcode={x:pad,y:pad,w:iw,h:ih};
    }else if(!barOn){
      const side=Math.min(ih,iw*.52);
      qr={x:w-pad-side,y:(h-side)/2,w:side,h:side};
      text={x:pad,y:pad,w:iw-side-gap,h:ih};
    }else if(!qrOn||!textOn){
      const topH=ih*(textOn?.60:.68);
      barcode={x:pad,y:pad+topH+gap,w:iw,h:ih-topH-gap};
      if(textOn)text={x:pad,y:pad,w:iw,h:topH};
      else {const side=Math.min(iw,topH);qr={x:(w-side)/2,y:pad+(topH-side)/2,w:side,h:side};}
    }else if(mode==='wide'){
      const side=Math.min(ih*.86,iw*.49);
      qr={x:w-pad-side,y:(h-side)/2,w:side,h:side};
      const leftW=qr.x-gap-pad,topH=ih*.58;
      text={x:pad,y:pad,w:leftW,h:topH};
      barcode={x:pad,y:pad+topH+gap,w:leftW,h:ih-topH-gap};
    }else{
      const topH=ih*.63,side=Math.min(topH,iw*.44);
      qr={x:w-pad-side,y:pad,w:side,h:side};
      text={x:pad,y:pad,w:iw-side-gap,h:topH};
      barcode={x:pad,y:pad+topH+gap,w:iw,h:ih-topH-gap};
    }
    return {mode,pad,gap,text,qr,barcode};
  }
  function warnings(g){
    const result=[];
    if(g.qr&&g.qr.w<12)result.push('二维码小于 12 mm，建议实测识别距离');
    if(g.barcode&&g.barcode.w<22)result.push('条码空间较窄，长编号可能难以识别');
    if(g.barcode&&g.barcode.h<6)result.push('条码高度偏小');
    if(g.text&&g.text.w<12)result.push('文字区域较窄，可关闭文字或改用换行');
    return result;
  }
  function recommend(w,h,preferred='auto',options={}){
    const g=compute(w,h,preferred,options),pt=g.text?clamp(Math.min(g.text.h/1.8,g.text.w/.9),4,18):11;
    return {font:Math.round(clamp(pt,6,18)*2)/2,name_font:Math.round(pt*2)/2,
      spec_font:Math.round(Math.max(4,pt*.8)*2)/2,serial_font:Math.round(Math.max(4,pt*.7)*2)/2,
      code_font:Math.round(Math.max(4,pt*.7)*2)/2};
  }
  return {compute,warnings,recommend};
});
