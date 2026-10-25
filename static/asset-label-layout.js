/* Millimetre-based layout shared by browser preview and PDF's equivalent geometry. */
(function(root,factory){
  const api=factory();
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  if(root)root.AssetLabelLayout=api;
})(typeof window!=='undefined'?window:undefined,function(){
  'use strict';
  const clamp=(n,a,b)=>Math.min(b,Math.max(a,n));
  function compute(w,h,preferred='auto'){
    if(!Number.isFinite(w)||!Number.isFinite(h)||w<15||w>190||h<10||h>270)throw Error('标签宽度 15–190 mm，高度 10–270 mm');
    if(!['auto','wide','compact'].includes(preferred))throw Error('不支持的标签排版');
    const mode=preferred==='auto'?(w/h>=1.5&&w>=36&&h>=20?'wide':'compact'):preferred;
    const pad=clamp(Math.min(w,h)*.045,.75,1.6),gap=clamp(Math.min(w,h)*.035,.55,1.3);
    const iw=w-2*pad,ih=h-2*pad;
    let text,qr,barcode;
    if(mode==='wide'){
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
    if(g.qr.w<12)result.push('二维码小于 12 mm，建议实测识别距离');
    if(g.barcode.w<22)result.push('条码空间较窄，长编号可能难以识别');
    if(g.barcode.h<6)result.push('条码高度偏小');
    if(g.text.w<12)result.push('文字区域较窄，名称可能省略');
    return result;
  }
  return {compute,warnings};
});