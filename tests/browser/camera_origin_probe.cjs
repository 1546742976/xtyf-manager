'use strict';
// Browser permissions-policy evidence only, not a native Tauri permission test.
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
(async () => {
  const browser = await chromium.launch({headless:true, args:['--use-fake-device-for-media-stream','--use-fake-ui-for-media-stream']});
  try {
    const page = await browser.newPage();
    for (const delegated of [false,true]) {
      await page.route('http://127.0.0.1:48261/**', route => route.fulfill({contentType:'text/html',body:
        `<iframe src="http://localhost:48262/child" ${delegated ? 'allow="camera http://localhost:48262"' : ''}></iframe>`}));
      await page.route('http://localhost:48262/**', route => route.fulfill({contentType:'text/html',body:'<!doctype html><title>synthetic child</title>'}));
      await page.goto('http://127.0.0.1:48261/');
      const child = page.frames().find(frame => frame.url().startsWith('http://localhost:48262/'));
      assert.ok(child);
      const outcome = await child.evaluate(async () => {
        try { const stream = await navigator.mediaDevices.getUserMedia({video:true}); stream.getTracks().forEach(track=>track.stop()); return 'granted'; }
        catch(error) { return error.name; }
      });
      assert.equal(outcome, delegated ? 'granted' : 'NotAllowedError');
      console.log(`cross-origin child delegated=${delegated}: ${outcome}`);
      await page.unrouteAll();
    }
  } finally { await browser.close(); }
})().catch(error => {console.error(error);process.exitCode=1;});
