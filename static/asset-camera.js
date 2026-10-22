/* Camera/photo recognition stays on the device. No image is sent to a supplier. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id),host=$('asset-scan-camera');
  let reader=null,stream=null,generation=0,last='',busy=false;
  host.innerHTML='<div class="asset-actions"><button type="button" class="btn" id="asset-camera-start">打开相机</button><button type="button" class="btn" id="asset-camera-stop">停止相机</button><label class="btn">从相册识别<input id="asset-camera-photo" type="file" accept="image/jpeg,image/png,image/webp" hidden></label></div><video id="asset-camera-video" playsinline muted hidden></video><p id="asset-camera-status" role="status"></p>';
  const status=t=>$('asset-camera-status').textContent=t;
  function stop(){generation++;reader?.reset();reader=null;stream?.getTracks().forEach(t=>t.stop());stream=null;$('asset-camera-video').hidden=true;}
  async function found(text){
    if(busy||last===text)return;last=text;busy=true;
    try{await window.AssetScan.resolve(text);if(navigator.vibrate)navigator.vibrate(50);status('已识别，核对后才会登记；可继续扫描其他标签。');}
    finally{busy=false;}
  }
  $('asset-camera-start').onclick=async()=>{
    stop();last='';const own=generation;
    if(!window.isSecureContext||!navigator.mediaDevices?.getUserMedia){status('当前页面不能打开相机，请使用 HTTPS、相册图片或手动输入。');return;}
    try{
      const openedStream=await navigator.mediaDevices.getUserMedia({audio:false,video:{facingMode:{ideal:'environment'}}});
      if(own!==generation){openedStream.getTracks().forEach(t=>t.stop());return;}
      stream=openedStream;
      const video=$('asset-camera-video');video.hidden=false;
      reader=new ZXing.BrowserMultiFormatReader();
      await reader.decodeFromStream(stream,video,result=>{if(result&&own===generation)found(result.getText());});
      status('相机已打开；关闭窗口或切到后台会停止相机。');
    }catch(e){if(own!==generation)return;stop();status('无法打开相机：'+e.message+'。可从相册识别或手动输入。');}
  };
  $('asset-camera-stop').onclick=()=>{stop();status('相机已停止');};
  $('asset-camera-photo').onchange=async e=>{
    const file=e.target.files[0];if(!file)return;stop();last='';
    if(file.size>10*1024*1024){status('请选择不超过 10 MiB 的图片');return;}
    let source=file;
    if(window.XTYF&&file.handle){
      try{const data=await window.XTYF.invoke('read_selected_image',{handle:file.handle});
        if(data.size>10*1024*1024||typeof data.body!=='string'||data.body.length>14*1024*1024)throw Error('图片过大');
        source=new Blob([Uint8Array.from(atob(data.body),c=>c.charCodeAt(0))],{type:data.mime});
      }catch(e){status('客户端图片读取失败：'+e.message);return;}
    }
    const url=URL.createObjectURL(source);const decoder=new ZXing.BrowserMultiFormatReader();
    try{await found((await decoder.decodeFromImageUrl(url)).getText());}
    catch(_){status('没有识别到有效条码，请重拍标签或手动输入原文。');}
    finally{decoder.reset();URL.revokeObjectURL(url);e.target.value='';}
  };
  $('asset-scan-dialog').addEventListener('close',stop);
  document.addEventListener('visibilitychange',()=>{if(document.hidden)stop();});
  window.addEventListener('pagehide',stop);
  window.addEventListener('xtyf-foreground',e=>{if(!e.detail?.active)stop();});
})();
