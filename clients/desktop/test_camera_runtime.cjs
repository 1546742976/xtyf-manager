'use strict';
// Actual Windows WebView2, synthetic camera only; does not access hardware or accounts.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const {spawn, execFileSync} = require('node:child_process');
const root = path.resolve(__dirname, '../..');
const output = path.join(root, '.build-tools/camera-native-check');
fs.mkdirSync(output, {recursive:true});
const profile = fs.mkdtempSync(path.join(output, 'profile-'));
let playwright;
try { playwright = require('playwright'); }
catch (_) {
  const driver = execFileSync(process.env.PLAYWRIGHT_PYTHON || 'python', ['-c', 'import pathlib,playwright; print(pathlib.Path(playwright.__file__).parent/"driver/package")'], {encoding:'utf8'}).trim();
  playwright = require(driver);
}
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const denyTest = process.env.XTYF_CAMERA_TEST_MODE === 'deny';
let application, browser, server;
const result = {platform:'Windows x64 WebView2', realCameraVerified:false, checks:[]};
(async () => {
  server = http.createServer((request, response) => { response.writeHead(503); response.end('isolated offline fixture'); });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  fs.writeFileSync(path.join(profile, 'server.json'), JSON.stringify({serverUrl:`http://127.0.0.1:${server.address().port}`}));
  application = spawn(process.env.XTYF_TEST_EXE, [], {cwd:root, windowsHide:true, stdio:'ignore', env:{...process.env,
    XTYF_SMOKE_DIR:profile, WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS:'--remote-debugging-port=9243 --use-fake-device-for-media-stream' + (denyTest ? '' : ' --use-fake-ui-for-media-stream')}});
  for (let attempt=0; attempt<100; attempt++) {
    try { browser = await playwright.chromium.connectOverCDP('http://127.0.0.1:9243', {timeout:500}); break; }
    catch (_) { await sleep(200); }
  }
  assert.ok(browser, 'private native WebView2 started');
  let page;
  for (let attempt=0; attempt<100; attempt++) {
    page=browser.contexts().flatMap(context=>context.pages()).find(value=>value.url().startsWith('http://tauri.localhost'));
    if (page) break;
    await sleep(100);
  }
  assert.ok(page, 'bundled local page loaded');
  if (!denyTest) {
  const camera = await page.evaluate(async () => {
    const stream = await navigator.mediaDevices.getUserMedia({video:true,audio:false});
    const track = stream.getVideoTracks()[0];
    const result={kind:track.kind,state:track.readyState,label:track.label};
    stream.getTracks().forEach(value=>value.stop());
    return result;
  });
  assert.equal(camera.kind,'video'); assert.equal(camera.state,'live');
  result.checks.push({name:'local video capture with simulated consent',camera});
  } else {
  // Fake-UI bypasses permission callbacks, so test denial in a fresh process
  // without that switch, never in the simulated-consent capture process.
  const microphone = await page.evaluate(async () => {
    try { const stream=await navigator.mediaDevices.getUserMedia({audio:true}); stream.getTracks().forEach(track=>track.stop()); return 'allowed'; }
    catch(error) { return error.name; }
  });
  assert.equal(microphone, 'NotAllowedError');
  result.checks.push({name:'microphone denied',error:microphone});
  }
  result.passed=true;
})().catch(error=>{result.passed=false;result.error=error.stack;process.exitCode=1;}).finally(async()=>{
  if(browser) await browser.close().catch(()=>{});
  if(application && application.exitCode===null) application.kill();
  if(server) server.close();
  fs.writeFileSync(path.join(output,denyTest ? 'deny-result.json' : 'capture-result.json'),JSON.stringify(result,null,2));
  console.log(JSON.stringify(result,null,2));
});
