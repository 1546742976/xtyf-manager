/* Local image preparation; original File objects stay in the form for retry. */
(function(root) {
  'use strict';
  async function prepare(file) {
    if(file.size > 10*1024*1024)throw new Error('单张图片不能超过 10 MiB');
    if(!['image/png','image/jpeg','image/webp'].includes(file.type || file.mime))throw new Error('只支持 PNG、JPEG、WebP 图片');
    // Old packaged clients only expose opaque handles, not uploadable Blob data.
    // Keep their authenticated handle upload; the server still validates content.
    if(!(file instanceof Blob))return {file, original:true};
    let bitmap, url;
    try {
      if(typeof createImageBitmap === 'function')bitmap=await createImageBitmap(file);
      else {
        url=URL.createObjectURL(file);
        bitmap=await new Promise((resolve,reject)=>{const img=new Image();img.onload=()=>resolve(img);img.onerror=()=>reject(new Error('图片解码失败'));img.src=url;});
      }
      const w=bitmap.width,h=bitmap.height;
      if(!w||!h||w>10000||h>10000||w*h>20000000)throw new Error('图片尺寸超过安全限制');
      const scale=Math.min(1,2048/Math.max(w,h)),canvas=document.createElement('canvas');
      canvas.width=Math.max(1,Math.round(w*scale));canvas.height=Math.max(1,Math.round(h*scale));
      canvas.getContext('2d').drawImage(bitmap,0,0,canvas.width,canvas.height);
      // PNG fallback preserves alpha where WebP encoding is unavailable.
      const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/webp',0.85));
      if(!blob||blob.size>10*1024*1024)throw new Error('图片处理失败或结果过大');
      return {file:new File([blob],file.name.replace(/\.[^.]*$/, '')+(blob.type==='image/webp'?'.webp':'.png'),{type:blob.type}),original:false};
    } finally {bitmap?.close?.();if(url)URL.revokeObjectURL(url);}
  }
  root.DailyImages={prepare};
  if(typeof module==='object')module.exports={prepare};
})(typeof window==='object'?window:globalThis);
