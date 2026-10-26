/* Camera/photo recognition stays on the device. No image is sent to a supplier. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id),host=$('asset-scan-camera'),scanner=window.AssetScan;
  let reader=null,stream=null,generation=0,last='',failed='',sound=true,audio=null,flashTimer=null,decodeTimer=null;
  let nativeDetector=null,decodeWorker=null,workerBusy=false,workerWatchdog=null,nativeMisses=0;
  const cameraScriptURL=document.currentScript?.src||'';
  let scanSerial=0,hadLiveCamera=false;
  const recentLabels=new Map();
  // The camera only decodes; AssetScan owns asynchronous lookups and queue deduplication.
  // Bound memory and latch a stationary code until another label or an explicit rescan.
  const RECENT_LABEL_MS=2500,MAX_RECENT_LABELS=64;
  // Decode a capped-size frame at bounded intervals, rather than asking ZXing
  // to decode the full-resolution stream indefinitely on every video tick.
  const SCAN_INTERVAL_MS=260,NATIVE_INTERVAL_MS=120,MAX_DECODE_WIDTH=800,WORKER_TIMEOUT_MS=2500;
  // Decode full video only with the browser's native engine; copy a smaller
  // centred region for ZXing. The video element itself stays full resolution.
  const frameCanvas=document.createElement('canvas');
  const frameContext=frameCanvas.getContext('2d',{willReadFrequently:true});
  host.innerHTML=`<div class="asset-camera-frame" id="asset-camera-frame">
    <video id="asset-camera-video" playsinline muted hidden></video>
    <p id="asset-camera-placeholder">将标签放在镜头中央<br>识别后会显示商品资料与本地记录</p>
    <div id="asset-camera-overlay" class="asset-camera-overlay" hidden>
      <div class="asset-camera-overlay-top"><strong id="asset-camera-task">扫一扫</strong><span id="asset-camera-count">等待扫描</span></div>
      <div class="asset-camera-target" aria-hidden="true"></div>
      <div class="asset-camera-feedback" role="status" aria-live="polite"><strong id="asset-camera-feedback-title">对准包装二维码</strong><span id="asset-camera-feedback-message">识别后会自动提示</span></div>
    </div>
  </div>
  <div class="asset-actions asset-camera-controls">
    <button type="button" class="btn primary" id="asset-camera-start">打开摄像头</button>
    <button type="button" class="btn" id="asset-camera-stop" hidden disabled>停止</button>
    <button type="button" class="btn" id="asset-camera-sound" aria-pressed="true">提示音：开</button>
    <button type="button" class="btn" id="asset-camera-list" data-scan-action="open-list" hidden>清单 0</button>
  </div>
  <p id="asset-camera-status" role="status" aria-live="polite"></p><button type="button" class="btn" id="asset-camera-retry" hidden>重试此标签</button>
  <details class="asset-form-section"><summary>切换镜头 / 识别设置 / 图片</summary>
    <label id="asset-camera-device-field" hidden>切换摄像头<select id="asset-camera-device"><option value="">系统默认摄像头</option></select></label>
    <div class="asset-actions"><button type="button" class="btn" id="asset-camera-rescan">重新识别当前标签</button><label class="btn">使用已有标签图片<input id="asset-camera-photo" type="file" accept="image/jpeg,image/png,image/webp" hidden></label></div>
  </details>`;
  const status=t=>$('asset-camera-status').textContent=t;
  function prepareAudio(){
    if(!sound)return;
    try{const Audio=window.AudioContext||window.webkitAudioContext;if(Audio){audio||=new Audio();if(audio.state==='suspended')audio.resume().catch(()=>{});}}catch(_){/* Visual feedback works without audio support. */}
  }
  function beep(kind){
    if(!sound||audio?.state!=='running')return;
    try{
      const tone=audio.createOscillator(),gain=audio.createGain(),now=audio.currentTime;
      tone.frequency.value=kind==='error'||kind==='attention'?260:kind==='duplicate'?440:880;
      gain.gain.setValueAtTime(0,now);gain.gain.linearRampToValueAtTime(.07,now+.01);gain.gain.exponentialRampToValueAtTime(.001,now+.14);
      tone.connect(gain);gain.connect(audio.destination);tone.start(now);tone.stop(now+.16);
      tone.onended=()=>{tone.disconnect();gain.disconnect();};
    }catch(_){/* Sound must never block scanning. */}
  }
  function updateCount(detail){
    const receive=detail.task==='receive';
    $('asset-camera-task').textContent=receive?'连续扫码入库':$('asset-scan-title').textContent;
    $('asset-camera-count').textContent=receive?`已扫 ${detail.count} / 20${detail.waiting?' · 查询中 '+detail.waiting:''}${detail.issues?' · 待处理 '+detail.issues:''}`:detail.count?`已选 ${detail.count} 件`:'等待扫描';
    $('asset-camera-list').hidden=!receive;$('asset-camera-list').textContent=`清单 ${detail.count}`;
  }
  document.addEventListener('asset-scan-queue-updated',event=>updateCount(event.detail));
  document.addEventListener('asset-scan-feedback',event=>{
    const detail=event.detail;
    if(!$('asset-scan-dialog').open&&detail.kind!=='idle')return;
    if(detail.kind==='idle')updateCount(detail);
    $('asset-camera-feedback-title').textContent=detail.title;
    $('asset-camera-feedback-message').textContent=detail.message;
    const frame=$('asset-camera-frame');clearTimeout(flashTimer);frame.dataset.feedback=detail.kind;
    flashTimer=setTimeout(()=>delete frame.dataset.feedback,750);
    if(detail.audible&&(!hadLiveCamera||stream)){beep(detail.kind);try{if(['accepted','success'].includes(detail.kind)&&navigator.vibrate)navigator.vibrate(40);}catch(_){/* Haptics are optional. */}}
  });
  $('asset-camera-sound').onclick=()=>{sound=!sound;$('asset-camera-sound').textContent='提示音：'+(sound?'开':'关');$('asset-camera-sound').setAttribute('aria-pressed',String(sound));if(sound){prepareAudio();beep('success');}};
  function cameraUnavailable(){
    if(!window.isSecureContext)return `当前地址 ${location.origin} 不是浏览器认可的安全连接，无法使用实时摄像头。手机访问电脑的 HTTP 局域网地址不属于 localhost 例外；请通过证书受信任的 HTTPS 地址打开。也可展开下方“使用已有标签图片”识别照片。`;
    if(!navigator.mediaDevices?.getUserMedia)return '当前浏览器没有提供实时摄像头接口。请用系统浏览器打开本页，并检查浏览器的相机权限；也可使用下方图片识别。';
    return '';
  }
  function cameraFailure(error){
    const messages={
      NotAllowedError:'未获得相机权限。请检查浏览器的网站相机权限，以及手机系统给浏览器／客户端的相机权限，再点击打开摄像头。',
      PermissionDeniedError:'相机权限被拒绝。请在网站设置和手机系统设置中允许相机，再重新打开。',
      SecurityError:'当前页面的相机访问被浏览器策略禁止，请使用系统浏览器直接打开此 HTTPS 页面。',
      NotFoundError:'没有找到可用摄像头，请检查设备是否有相机或是否已连接。',
      DevicesNotFoundError:'没有找到可用摄像头，请检查设备是否有相机或是否已连接。',
      NotReadableError:'摄像头无法启动，可能被其他应用占用或被系统禁用。请关闭其他使用相机的应用后重试。',
      TrackStartError:'摄像头无法启动，请关闭其他使用相机的应用并检查系统相机权限。',
      OverconstrainedError:'所选摄像头已不可用，请在“切换摄像头”中选择系统默认摄像头后重试。',
      AbortError:'摄像头启动被中断，请保持页面在前台并重新打开。'
    };
    return messages[error.name]||'无法打开摄像头：'+(error.message||error.name||'未知错误')+'。可以使用下方图片识别。';
  }
  function stop(){
    generation++;scanSerial++;last='';recentLabels.clear();failed='';
    // Stopping video must not cancel background receipt lookups. The scan
    // dialog's own close handler cancels those jobs when the session ends.
    $('asset-camera-retry').hidden=true;$('asset-camera-retry').disabled=false;
    if(decodeTimer!==null){clearTimeout(decodeTimer);decodeTimer=null;}
    if(workerWatchdog!==null){clearTimeout(workerWatchdog);workerWatchdog=null;}
    decodeWorker?.terminate();decodeWorker=null;workerBusy=false;nativeDetector=null;nativeMisses=0;
    reader?.reset();reader=null;
    const video=$('asset-camera-video');video.pause();video.srcObject=null;
    stream?.getTracks().forEach(t=>t.stop());stream=null;
    frameCanvas.width=frameCanvas.height=0;
    $('asset-scan-dialog').classList.remove('asset-scan-camera-active');
    video.hidden=true;$('asset-camera-overlay').hidden=true;$('asset-camera-placeholder').hidden=false;
    $('asset-camera-stop').disabled=true;$('asset-camera-stop').hidden=true;$('asset-camera-start').hidden=false;
  }
  function found(text,explicit=false,own=generation){
    if(own!==generation||typeof text!=='string')return false;
    text=text.trim();
    if(!text)return false;
    const now=Date.now();
    for(const [code,expires] of recentLabels)if(expires<=now)recentLabels.delete(code);
    if(!explicit&&(text===last||recentLabels.has(text)))return false;
    if(recentLabels.size>=MAX_RECENT_LABELS)recentLabels.delete(recentLabels.keys().next().value);
    last=text;recentLabels.set(text,now+RECENT_LABEL_MS);
    $('asset-scan-raw').value=text;
    failed='';$('asset-camera-retry').hidden=true;
    const serial=++scanSerial;
    // Dispatch immediately: decoding must not wait on the 15-second lookup limit.
    try{
      const pending=explicit?scanner.retry(text):scanner.resolve(text);
      status('标签已读取，可继续扫描下一张；查询结果会更新到清单。');
      Promise.resolve(pending).then(result=>{
        if(own!==generation||serial!==scanSerial||result?.status==='stale')return;
        if(result?.status==='duplicate'){
          status('同一标签已在清单中，未重复增加。');return;
        }
        if(result?.status!=='success'){
          failed=text;$('asset-camera-retry').hidden=false;
          status((result?.message||'识别失败')+'；可重试此标签。');
          return;
        }
        failed='';$('asset-camera-retry').hidden=true;
        status(scanner.task==='receive'?'标签识别完成，可连续扫描下一张。':'已识别，请查看结果。');
      }).catch(error=>{
        if(own!==generation||serial!==scanSerial)return;
        failed=text;$('asset-camera-retry').hidden=false;
        status((error?.message||'识别失败')+'；可重试此标签。');
      });
    }catch(error){
      if(own!==generation||serial!==scanSerial)return false;
      failed=text;$('asset-camera-retry').hidden=false;
      status((error?.message||'识别失败')+'；可重试此标签。');
    }
    return true;
  }
  $('asset-camera-retry').onclick=()=>{if(failed)found(failed,true);};
  function scheduleDecode(own,delay=SCAN_INTERVAL_MS){
    if(own!==generation||!stream||(!reader&&!decodeWorker&&!nativeDetector))return;
    if(decodeTimer!==null)clearTimeout(decodeTimer);
    decodeTimer=setTimeout(()=>{decodeTimer=null;void decodeFrame(own);},delay);
  }
  function videoReady(){
    const video=$('asset-camera-video');
    return video.readyState>=HTMLMediaElement.HAVE_CURRENT_DATA&&video.videoWidth>0&&video.videoHeight>0;
  }
  function grabFrame(){
    if(!frameContext||!videoReady())return null;
    const video=$('asset-camera-video'),vw=video.videoWidth,vh=video.videoHeight;
    // The target guide is centred; do not expend CPU on the outer margins.
    const cropWidth=Math.round(vw*.9),cropHeight=Math.round(vh*.8);
    const width=Math.min(MAX_DECODE_WIDTH,cropWidth);
    const height=Math.max(1,Math.round(cropHeight*width/cropWidth));
    if(frameCanvas.width!==width||frameCanvas.height!==height){frameCanvas.width=width;frameCanvas.height=height;}
    frameContext.drawImage(video,Math.round((vw-cropWidth)/2),Math.round((vh-cropHeight)/2),cropWidth,cropHeight,0,0,width,height);
    return {width,height,pixels:frameContext.getImageData(0,0,width,height).data};
  }
  function disableWorker(own){
    if(workerWatchdog!==null){clearTimeout(workerWatchdog);workerWatchdog=null;}
    decodeWorker?.terminate();decodeWorker=null;workerBusy=false;
    if(!nativeDetector&&own===generation&&stream){reader??=new ZXing.MultiFormatReader();scheduleDecode(own);}
  }
  function submitWorkerFrame(own){
    if(workerBusy||!decodeWorker||own!==generation||!videoReady())return false;
    try{
      const frame=grabFrame();if(!frame)return false;
      workerBusy=true;
      // Transfer ownership rather than cloning a new multi-megabyte RGBA buffer.
      decodeWorker.postMessage({generation:own,width:frame.width,height:frame.height,rgba:frame.pixels.buffer},[frame.pixels.buffer]);
      workerWatchdog=setTimeout(()=>{if(own===generation&&workerBusy)disableWorker(own);},WORKER_TIMEOUT_MS);
      return true;
    }catch(_){disableWorker(own);return false;}
  }
  async function prepareDecoder(own){
    if(typeof window.BarcodeDetector==='function'){
      try{
        const formats=await window.BarcodeDetector.getSupportedFormats();
        // Code128 is used by printed labels; QR is used by supplier packages.
        if(own===generation&&['qr_code','code_128'].every(f=>formats.includes(f))){
          nativeDetector=new window.BarcodeDetector({formats});
        }
      }catch(_){/* Browser API is optional. */}
    }
    if(own!==generation)return;
    if(typeof Worker==='function'){
      try{
        const workerURL=new URL('asset-zxing-worker.js',cameraScriptURL||new URL('/static/asset-camera.js',location.href).href);
        const worker=new Worker(workerURL.href);
        decodeWorker=worker;
        worker.onmessage=event=>{
          if(own!==generation||decodeWorker!==worker||!workerBusy||event.data?.generation!==own)return;
          workerBusy=false;
          if(workerWatchdog!==null){clearTimeout(workerWatchdog);workerWatchdog=null;}
          if(typeof event.data.text==='string'&&event.data.text)void found(event.data.text,false,own);
          if(!nativeDetector)scheduleDecode(own);
        };
        worker.onerror=()=>{if(own===generation&&decodeWorker===worker)disableWorker(own);};
      }catch(_){/* Some embedded WebViews do not support workers. */}
    }
    if(!nativeDetector&&!decodeWorker)reader=new ZXing.MultiFormatReader();
  }
  async function decodeFrame(own){
    if(own!==generation||!stream)return;
    if(!videoReady()){scheduleDecode(own);return;}
    if(nativeDetector){
      try{
        const results=await nativeDetector.detect($('asset-camera-video'));
        if(own!==generation||!stream)return;
        if(results?.length){
          nativeMisses=0;
          const text=results.find(result=>typeof result.rawValue==='string'&&result.rawValue.trim())?.rawValue;
          if(text)void found(text,false,own);
        }else if(++nativeMisses>=7){
          // Native implementations cover different symbologies. Periodically
          // ask ZXing for uncommon vendor labels without blocking preview.
          nativeMisses=0;submitWorkerFrame(own);
        }
      }catch(_){if(own===generation){nativeDetector=null;if(!decodeWorker&&!reader)reader=new ZXing.MultiFormatReader();}}
      if(own===generation&&stream)scheduleDecode(own,nativeDetector?NATIVE_INTERVAL_MS:SCAN_INTERVAL_MS);
      return;
    }
    if(decodeWorker){
      if(!submitWorkerFrame(own))scheduleDecode(own);
      return; // The worker response schedules the next decode; no backlog.
    }
    if(reader&&frameContext){
      try{
        const frame=grabFrame();
        if(frame){
          const luminance=new ZXing.RGBLuminanceSource(
            new Int32Array(frame.pixels.buffer,frame.pixels.byteOffset,frame.width*frame.height),
            frame.width,frame.height);
          const match=reader.decode(new ZXing.BinaryBitmap(new ZXing.HybridBinarizer(luminance)));
          if(match&&own===generation)void found(match.getText(),false,own);
        }
      }catch(_){/* No readable label in this frame. */}
      finally{reader?.reset();}
    }
    scheduleDecode(own);
  }
  $('asset-camera-start').onclick=async()=>{
    prepareAudio();
    stop();last='';const own=generation;
    const unavailable=cameraUnavailable();
    if(unavailable){status(unavailable);return;}
    status('正在请求摄像头，请在授权提示中选择允许…');
    try{
      const device=$('asset-camera-device').value;
      const cameraChoice=device?{deviceId:{exact:device}}:{facingMode:{ideal:'environment'}};
      const videoConstraints={...cameraChoice,width:{ideal:1280},height:{ideal:720},frameRate:{ideal:30,max:30}};
      const openedStream=await navigator.mediaDevices.getUserMedia({audio:false,video:videoConstraints});
      if(own!==generation){openedStream.getTracks().forEach(t=>t.stop());return;}
      stream=openedStream;hadLiveCamera=true;
      const video=$('asset-camera-video');video.hidden=false;$('asset-camera-overlay').hidden=false;$('asset-camera-placeholder').hidden=true;$('asset-camera-stop').disabled=false;$('asset-camera-stop').hidden=false;$('asset-camera-start').hidden=true;
      $('asset-scan-dialog').classList.add('asset-scan-camera-active');
      video.srcObject=stream;
      await video.play();
      if(own!==generation)return;
      await prepareDecoder(own);
      if(own!==generation)return;
      scheduleDecode(own,nativeDetector?NATIVE_INTERVAL_MS:SCAN_INTERVAL_MS);
      try{
        const devices=(await navigator.mediaDevices.enumerateDevices()).filter(d=>d.kind==='videoinput');
        if(own!==generation)return;
        const select=$('asset-camera-device'),current=stream.getVideoTracks()[0]?.getSettings().deviceId;
        select.replaceChildren(new Option('系统默认摄像头',''),...devices.map((d,i)=>new Option(d.label||'摄像头 '+(i+1),d.deviceId)));
        select.value=current||'';
        $('asset-camera-device-field').hidden=devices.length<2;
      }catch(_){/* Camera enumeration is optional; decoding remains available. */}
      status('对准标签连续扫描；识别反馈显示在取景器内。');
    }catch(e){if(own!==generation)return;stop();status(cameraFailure(e));}
  };
  $('asset-camera-stop').onclick=()=>{stop();status('相机已停止');};
  $('asset-camera-device').onchange=()=>$('asset-camera-start').click();
  $('asset-camera-rescan').onclick=()=>{last='';recentLabels.clear();scanSerial++;status('已允许重新识别，请对准当前标签。');};
  function selectInput(mode){
    stop();if(mode==='gun')hadLiveCamera=false;
    for(const button of document.querySelectorAll('[data-scan-input]'))button.setAttribute('aria-pressed',String(button.dataset.scanInput===mode));
    host.hidden=mode==='gun';$('asset-scan-gun-settings').hidden=mode!=='gun';
    $('asset-camera-device-field').hidden=$('asset-camera-device').options.length<3;
    status(mode==='camera'?(cameraUnavailable()||'点击打开摄像头；允许访问后，可通过“切换摄像头”选择可用镜头。'):'');
    if(mode==='gun'&&$('asset-scan-dialog').open)$('asset-scan-raw').focus();
  }
  for(const button of document.querySelectorAll('[data-scan-input]'))button.onclick=()=>selectInput(button.dataset.scanInput);
  selectInput(matchMedia('(pointer:coarse)').matches?'camera':'gun');
  document.addEventListener('click',event=>{if(event.target.closest('#asset-scan-form'))prepareAudio();});
  $('asset-scan-raw').addEventListener('keydown',prepareAudio);
  $('asset-camera-photo').onchange=async e=>{
    const file=e.target.files[0];if(!file)return;stop();last='';const own=generation;
    if(file.size>10*1024*1024){status('请选择不超过 10 MiB 的图片');return;}
    let source=file;
    if(window.XTYF&&file.handle){
      try{const data=await window.XTYF.invoke('read_selected_image',{handle:file.handle});
        if(data.size>10*1024*1024||typeof data.body!=='string'||data.body.length>14*1024*1024)throw Error('图片过大');
        source=new Blob([Uint8Array.from(atob(data.body),c=>c.charCodeAt(0))],{type:data.mime});
      }catch(e){if(own===generation)status('客户端图片读取失败：'+e.message);return;}
    }
    if(own!==generation)return;
    const url=URL.createObjectURL(source);const decoder=new ZXing.BrowserMultiFormatReader();
    try{const result=await decoder.decodeFromImageUrl(url);if(own===generation)await found(result.getText(),false,own);}
    catch(_){if(own===generation)status('没有识别到有效条码，请重拍标签或手动输入原文。');}
    finally{decoder.reset();URL.revokeObjectURL(url);e.target.value='';}
  };
  $('asset-scan-dialog').addEventListener('close',stop);
  document.addEventListener('visibilitychange',()=>{if(document.hidden)stop();});
  window.addEventListener('pagehide',stop);
  window.addEventListener('xtyf-foreground',e=>{if(!e.detail?.active)stop();});
})();
