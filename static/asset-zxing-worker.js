/* ZXing is intentionally loaded and executed outside the camera UI thread.
 * This worker receives only transferable RGBA pixel buffers, never network data.
 */
'use strict';
importScripts('./vendor/asset-tools/zxing.js');
const reader=new self.ZXing.MultiFormatReader();
self.onmessage=event=>{
  const {generation,width,height,rgba}=event.data||{};
  let text='';
  try{
    if(!(rgba instanceof ArrayBuffer)||!Number.isInteger(width)||!Number.isInteger(height)||
       width<1||height<1||width*height>1024*1024||rgba.byteLength!==width*height*4)throw Error('Invalid frame');
    const source=new self.ZXing.RGBLuminanceSource(new Int32Array(rgba),width,height);
    const bitmap=new self.ZXing.BinaryBitmap(new self.ZXing.HybridBinarizer(source));
    text=reader.decode(bitmap).getText();
  }catch(_){/* An unreadable frame is the normal case. */}
  finally{reader.reset();}
  self.postMessage({generation,text});
};
