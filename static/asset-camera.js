/* Camera/photo recognition stays on the device. No image is sent to a supplier. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id),host=$('asset-scan-camera'),scanner=window.AssetScan;
  let reader=null,stream=null,generation=0,last='',busy=null,failed='',sound=true,audio=null,flashTimer=null,decodeTimer=null;
  // Decode a capped-size frame at bounded intervals, rather than asking ZXing
  // to decode the full-resolution stream indefinitely on every video tick.
  const SCAN_INTERVAL_MS=260,MAX_DECODE_WIDTH=1024;
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
    if(detail.audible){beep(detail.kind);try{if(['accepted','success'].includes(detail.kind)&&navigator.vibrate)navigator.vibrate(40);}catch(_){/* Haptics are optional. */}}
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
    generation++;scanner.cancel();busy=null;failed='';$('asset-camera-retry').hidden=true;
    if(decodeTimer!==null){clearTimeout(decodeTimer);decodeTimer=null;}
    reader?.reset();reader=null;
    const video=$('asset-camera-video');video.pause();video.srcObject=null;
    stream?.getTracks().forEach(t=>t.stop());stream=null;
    frameCanvas.width=frameCanvas.height=0;
    $('asset-scan-dialog').classList.remove('asset-scan-camera-active');
    video.hidden=true;$('asset-camera-overlay').hidden=true;$('asset-camera-placeholder').hidden=false;
    $('asset-camera-stop').disabled=true;$('asset-camera-stop').hidden=true;$('asset-camera-start').hidden=false;
  }
  async function found(text,explicit=false,own=generation){
    if(own!==generation||busy||(!explicit&&last===text))return;
    last=text;$('asset-scan-raw').value=text;const token={};busy=token;$('asset-camera-retry').disabled=true;
    try{
      const result=await (explicit?window.AssetScan.retry(text):window.AssetScan.resolve(text));
      if(own!==generation||result?.status==='stale')return;
      if(result?.status==='duplicate'){status('同一标签已在清单中，未重复增加。');return;}
      if(result?.status!=='success'){failed=text;$('asset-camera-retry').hidden=false;status((result?.message||'识别失败')+'；可重试此标签。');return;}
      failed='';$('asset-camera-retry').hidden=true;status(window.AssetScan.task==='receive'?'标签识别完成，可连续扫描下一张。':'已识别，请查看结果。');
    }catch(error){if(own===generation){failed=text;$('asset-camera-retry').hidden=false;status(error.message+'；可重试此标签。');}}
    finally{if(busy===token){busy=null;$('asset-camera-retry').disabled=false;}}
  }
  $('asset-camera-retry').onclick=()=>failed&&found(failed,true);
  function scheduleDecode(own){
    if(own!==generation||!stream||!reader)return;
    decodeTimer=setTimeout(()=>decodeFrame(own),SCAN_INTERVAL_MS);
  }
  function decodeFrame(own){
    if(own!==generation||!reader||!stream)return;
    const video=$('asset-camera-video');
    // Skip frames while an accepted label is being resolved against the server.
    if(!busy&&video.readyState>=HTMLMediaElement.HAVE_CURRENT_DATA&&frameContext&&video.videoWidth&&video.videoHeight){
      try{
        const width=Math.min(MAX_DECODE_WIDTH,video.videoWidth);
        const height=Math.max(1,Math.round(video.videoHeight*width/video.videoWidth));
        if(frameCanvas.width!==width||frameCanvas.height!==height){frameCanvas.width=width;frameCanvas.height=height;}
        frameContext.drawImage(video,0,0,width,height);
        const pixels=frameContext.getImageData(0,0,width,height);
        const luminance=new ZXing.RGBLuminanceSource(new Int32Array(pixels.data.buffer,pixels.data.byteOffset,width*height),width,height);
        const bitmap=new ZXing.BinaryBitmap(new ZXing.HybridBinarizer(luminance));
        const match=reader.decode(bitmap);
        if(match&&own===generation)void found(match.getText(),false,own);
      }catch(_){/* Most frames contain no readable code; keep scanning. */}
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
      const videoConstraints={...cameraChoice,width:{ideal:1280},height:{ideal:720},frameRate:{ideal:15,max:24}};
      const openedStream=await navigator.mediaDevices.getUserMedia({audio:false,video:videoConstraints});
      if(own!==generation){openedStream.getTracks().forEach(t=>t.stop());return;}
      stream=openedStream;
      const video=$('asset-camera-video');video.hidden=false;$('asset-camera-overlay').hidden=false;$('asset-camera-placeholder').hidden=true;$('asset-camera-stop').disabled=false;$('asset-camera-stop').hidden=false;$('asset-camera-start').hidden=true;
      $('asset-scan-dialog').classList.add('asset-scan-camera-active');
      video.srcObject=stream;
      await video.play();
      if(own!==generation)return;
      reader=new ZXing.MultiFormatReader();
      scheduleDecode(own);
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
  $('asset-camera-rescan').onclick=()=>{last='';status('已允许重新识别，请对准当前标签。');};
  function selectInput(mode){
    stop();last='';
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
