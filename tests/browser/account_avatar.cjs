'use strict';
// Real account pages, an isolated database and synthetic images; no worker or mail delivery.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawn, execFileSync} = require('node:child_process');
const {chromium, webkit, devices} = require('playwright');
const root = path.resolve(__dirname, '../..');
const output = path.join(root, '.build-tools/account-avatar-check');
const python = process.env.REVIEW_PYTHON || 'python';
const password = 'avatar-browser-password';
fs.mkdirSync(output, {recursive:true});

function fixture(name, width, height) {
  const buffer = execFileSync(python, ['-c',
    'from PIL import Image,ImageDraw; import sys; w,h=map(int,sys.argv[1:]); im=Image.new("RGB",(w,h),"#f0b351"); d=ImageDraw.Draw(im); d.rectangle((0,0,w//2,h//2),fill="#3455ac"); d.rectangle((w//2,h//2,w,h),fill="#4c9978"); d.ellipse((w//4,h//4,w*3//4,h*3//4),fill="#a23d59"); im.save(sys.stdout.buffer,format="PNG")',
    String(width), String(height)]);
  return {name, mimeType:'image/png', buffer};
}

async function login(page, base, initial = false) {
  await page.goto(base + '/login');
  await page.locator('[name=username]').fill('admin');
  await page.locator('[name=password]').fill(initial ? 'browser-test-initial' : password);
  await page.locator('#login-form [type=submit]').click();
  await page.waitForURL(initial ? '**/account' : base + '/');
  if (!initial) return;
  assert.equal(await page.locator('#avatar-change').count(), 0, 'initial password must hide avatar editing');
  assert.equal(await page.locator('#avatar-crop-dialog').count(), 0, 'initial password must not render crop dialog');
  await page.locator('#account-password-form [name=old]').fill('browser-test-initial');
  for (const field of ['new', 'confirm']) await page.locator(`#account-password-form [name=${field}]`).fill(password);
  await page.locator('#account-password-form [type=submit]').click();
  await page.waitForURL(base + '/');
}

async function account(page, base) {
  await page.goto(base + '/account');
  await page.locator('#avatar-change').waitFor({state:'visible'});
  await page.waitForFunction(() => !document.querySelector('#account-profile-form [type=submit]').disabled);
  assert.equal(await page.locator('[data-account-avatar]').count(), 3);
}

async function selectImage(page, file, button = '#avatar-change') {
  const [chooser] = await Promise.all([page.waitForEvent('filechooser'), page.locator(button).click()]);
  await chooser.setFiles(file);
  await page.locator('#avatar-crop-dialog').waitFor({state:'visible'});
  await page.waitForFunction(() => !document.querySelector('#avatar-save').disabled);
}

async function imageState(page) {
  return page.locator('[data-account-avatar]').evaluateAll(elements => elements.map(element => {
    const image = element.querySelector('[data-avatar-image]');
    const initial = element.querySelector('[data-avatar-initial]');
    return {url:image?.getAttribute('src') || null, loaded:!!image?.naturalWidth,
      width:image?.naturalWidth || 0, height:image?.naturalHeight || 0,
      fallback:!!initial && !initial.hidden && getComputedStyle(initial).display !== 'none'};
  }));
}

async function loadedImages(page, count, url) {
  await page.waitForFunction(({count, url}) => {
    const images = [...document.querySelectorAll('[data-account-avatar] [data-avatar-image]')];
    return images.length === count && images.every(image => image.getAttribute('src') === url &&
      image.complete && image.naturalWidth === 512 && !image.hidden &&
      image.parentElement.querySelector('[data-avatar-initial]').hidden);
  }, {count, url});
  const state = await imageState(page);
  assert.equal(state.length, count);
  for (const image of state) {
    assert.equal(image.loaded, true); assert.equal(image.width, 512); assert.equal(image.height, 512);
    assert.equal(image.fallback, false, 'loaded image replaces initial');
  }
}

async function lastCrop(page) {
  // Browser protocols omit file-backed multipart bodies. Observe FormData before fetch serializes it.
  const crop = await page.evaluate(() => window.__avatarUploads.at(-1));
  assert.ok(crop, 'upload contains crop JSON');
  return crop;
}

function validCrop(crop, width, height) {
  assert.deepEqual(Object.keys(crop).sort(), ['size', 'x', 'y']);
  for (const value of Object.values(crop)) assert.equal(Number.isInteger(value), true);
  assert.ok(crop.size > 0 && crop.x >= 0 && crop.y >= 0);
  assert.ok(crop.x + crop.size <= width && crop.y + crop.size <= height, 'crop stays within original pixels');
}

async function saveAvatar(page) {
  const [response] = await Promise.all([
    page.waitForResponse(response => response.url().includes('/api/account/avatar') && response.request().method() === 'POST'),
    page.locator('#avatar-save').click(),
  ]);
  assert.equal(response.status(), 200);
  const body = await response.json();
  assert.equal(body.ok, true); assert.ok(body.avatar_url);
  await page.locator('#avatar-crop-dialog').waitFor({state:'hidden'});
  await loadedImages(page, 3, body.avatar_url);
  return {url:body.avatar_url, crop:await lastCrop(page)};
}

async function canvasState(page) {
  return page.locator('#avatar-crop-canvas').evaluate(canvas => canvas.toDataURL());
}

async function adjustCrop(page, mobile) {
  const before = await canvasState(page);
  const slider = page.locator('#avatar-zoom'), sliderBox = await slider.boundingBox();
  const sliderPoint = {position:{x:sliderBox.width / 2, y:sliderBox.height / 2}};
  if (mobile) await slider.tap(sliderPoint); else await slider.click(sliderPoint);
  assert.ok(Number(await slider.inputValue()) > 1 && Number(await slider.inputValue()) < 4);
  assert.notEqual(await canvasState(page), before, 'zoom changes the crop preview');
  const canvas = page.locator('#avatar-crop-canvas');
  await canvas.focus();
  const beforeKeys = await canvasState(page);
  await page.keyboard.press('Shift+ArrowLeft');
  await page.keyboard.press('ArrowUp');
  assert.notEqual(await canvasState(page), beforeKeys, 'keyboard changes crop position');
  const box = await canvas.boundingBox();
  const beforeDrag = await canvasState(page);
  if (mobile) {
    // Playwright has tap but no public touch-drag API: exercise the real touch pointer handlers.
    const pointer = {pointerId:7, pointerType:'touch', isPrimary:true, button:0, buttons:1};
    await canvas.dispatchEvent('pointerdown', {...pointer, clientX:box.x + box.width / 2, clientY:box.y + box.height / 2});
    await canvas.dispatchEvent('pointermove', {...pointer, clientX:box.x + box.width / 2 - 36, clientY:box.y + box.height / 2 - 28});
    await canvas.dispatchEvent('pointerup', {...pointer, buttons:0});
  } else {
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down(); await page.mouse.move(box.x + box.width / 2 - 36, box.y + box.height / 2 - 28, {steps:4});
    await page.mouse.up();
  }
  assert.notEqual(await canvasState(page), beforeDrag, 'drag changes crop position');
  await page.locator('[data-avatar-move=down]').click();
  await canvas.focus();
  for (let i = 0; i < 30; i++) await page.keyboard.press('Shift+ArrowRight');
  for (let i = 0; i < 30; i++) await page.keyboard.press('Shift+ArrowDown');
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true, 'crop dialog fits viewport');
}

(async () => {
  const server = spawn(python, ['-B', path.join(__dirname, 'server.py')], {
    cwd:root, env:{...process.env, PYTHONDONTWRITEBYTECODE:'1', MAIL_MODE:'preview'}, stdio:['ignore', 'pipe', 'pipe'],
  });
  let browser, currentPage, log = '', buffer = '';
  const results = [], errors = [];
  try {
    const base = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(Error('server timeout ' + log)), 30000);
      server.stderr.on('data', data => log += data);
      server.stdout.on('data', data => {
        buffer += data; const match = buffer.match(/BROWSER_URL=(\S+)/);
        if (match) { clearTimeout(timer); resolve(match[1]); }
      });
      server.once('exit', code => { clearTimeout(timer); reject(Error('server exit ' + code + ' ' + log)); });
    });
    const landscape = fixture('landscape.png', 960, 640);
    const portrait = fixture('portrait.png', 800, 1000);
    for (const [engine, mobile] of [[chromium, false], [webkit, true]]) {
      const executablePath = engine === webkit ? process.env.REVIEW_WEBKIT_PATH : undefined;
      browser = await engine.launch({headless:true, ...(executablePath ? {executablePath} : {})});
      const context = await browser.newContext(mobile ? {...devices['iPhone 13'], reducedMotion:'reduce'} :
        {viewport:{width:1280, height:900}, reducedMotion:'reduce'});
      // Keep the real Bootstrap resources, but remove CDN availability from this
      // avatar regression. Versions match the URLs in the funds page.
      const bootstrap = path.dirname(require.resolve('bootstrap/package.json'));
      const icons = path.dirname(require.resolve('bootstrap-icons/package.json'));
      const resources = {
        'bootstrap.min.css': path.join(bootstrap, 'dist/css/bootstrap.min.css'),
        'bootstrap.bundle.min.js': path.join(bootstrap, 'dist/js/bootstrap.bundle.min.js'),
        'bootstrap-icons.min.css': path.join(icons, 'font/bootstrap-icons.min.css'),
        'bootstrap-icons.css': path.join(icons, 'font/bootstrap-icons.css'),
        'bootstrap-icons.woff': path.join(icons, 'font/fonts/bootstrap-icons.woff'),
        'bootstrap-icons.woff2': path.join(icons, 'font/fonts/bootstrap-icons.woff2'),
      };
      await context.route(/^https:\/\/(cdn\.bootcdn\.net|cdn\.jsdelivr\.net)\//, route => {
        const resource = resources[path.posix.basename(new URL(route.request().url()).pathname)];
        return resource ? route.fulfill({path:resource}) : route.continue();
      });
      const page = await context.newPage(); currentPage = page;
      await page.addInitScript(() => {
        const original = window.fetch;
        window.__avatarUploads = [];
        window.fetch = function (input, options) {
          const url = typeof input === 'string' ? input : input.url;
          if (url.includes('/api/account/avatar') && options?.method === 'POST' && options.body instanceof FormData) {
            window.__avatarUploads.push(JSON.parse(options.body.get('crop')));
          }
          return original.apply(this, arguments);
        };
      });
      page.setDefaultTimeout(15000); page.on('pageerror', error => errors.push(`${engine.name()}: ${error.message}`));
      let posts = 0;
      page.on('request', request => { if (request.url().includes('/api/account/avatar') && request.method() === 'POST') posts++; });
      await login(page, base, !mobile); await account(page, base);
      assert.ok((await imageState(page)).every(image => image.fallback && !image.url));
      assert.equal(await page.locator('#avatar-remove').isDisabled(), true);
      const draft = page.locator('#account-profile-form [name=username]');
      await draft.fill(`unsaved-${engine.name()}`);
      await selectImage(page, landscape);
      await page.screenshot({path:path.join(output, `${engine.name()}-crop-open-viewport.png`)});
      await adjustCrop(page, mobile);
      const cancelledCrop = await canvasState(page);
      await page.locator('#avatar-cancel').click();
      await page.locator('#avatar-crop-dialog').waitFor({state:'hidden'});
      assert.equal(posts, 0, 'cancel never uploads');
      assert.ok((await imageState(page)).every(image => image.fallback && !image.url));
      assert.equal(await page.locator('#avatar-change').evaluate(element => element === document.activeElement), true, 'cancel returns focus');
      await page.locator('#avatar-resume').click();
      assert.equal(await canvasState(page), cancelledCrop, 'cancel preserves an editable crop draft');
      const first = await saveAvatar(page); validCrop(first.crop, 960, 640);
      assert.equal(await draft.inputValue(), `unsaved-${engine.name()}`, 'avatar save preserves unrelated profile draft');
      assert.equal(posts, 1);

      await selectImage(page, landscape);
      await selectImage(page, portrait, '#avatar-reselect');
      assert.equal(await page.locator('#avatar-zoom').inputValue(), '1', 'reselect starts with a centered crop');
      await adjustCrop(page, mobile);
      const retryCrop = await canvasState(page), retryZoom = await page.locator('#avatar-zoom').inputValue();
      let failedCrop, releaseFailure;
      const failureGate = new Promise(resolve => { releaseFailure = resolve; });
      await page.route('**/api/account/avatar', async route => {
        if (route.request().method() !== 'POST') return route.continue();
        await failureGate;
        await route.fulfill({status:503, json:{ok:false, error:'模拟头像保存失败，请重试'}});
      });
      await Promise.all([
        page.waitForRequest(request => request.url().includes('/api/account/avatar') && request.method() === 'POST'),
        page.locator('#avatar-save').click(),
      ]);
      failedCrop = await lastCrop(page);
      assert.equal(await page.locator('#avatar-save').isDisabled(), true, 'pending save blocks duplicate submission');
      assert.equal(await page.locator('#avatar-zoom').isDisabled(), true);
      await page.locator('#avatar-save').evaluate(button => button.click());
      assert.equal(posts, 2, 'disabled button cannot submit a second pending upload');
      releaseFailure();
      await page.locator('#avatar-crop-result').filter({hasText:'模拟头像保存失败'}).waitFor();
      assert.equal(await page.locator('#avatar-crop-dialog').isVisible(), true);
      assert.equal(await canvasState(page), retryCrop); assert.equal(await page.locator('#avatar-zoom').inputValue(), retryZoom);
      assert.equal(await draft.inputValue(), `unsaved-${engine.name()}`);
      await loadedImages(page, 3, first.url);
      assert.equal(await page.locator('#avatar-save').isDisabled(), false);
      await page.screenshot({path:path.join(output, `${engine.name()}-crop-retry.png`), fullPage:true});
      await page.screenshot({path:path.join(output, `${engine.name()}-crop-retry-viewport.png`)});
      await page.unroute('**/api/account/avatar');
      const second = await saveAvatar(page); validCrop(second.crop, 800, 1000);
      assert.deepEqual(second.crop, failedCrop, 'retry submits the same crop');
      assert.notEqual(second.url, first.url, 'successful replacement changes the image version');
      assert.equal(posts, 3);
      await page.reload(); await loadedImages(page, 3, second.url);
      assert.equal(await page.locator('#account-profile-form [name=username]').inputValue(), 'admin', 'avatar action never saved unrelated username');
      await page.screenshot({path:path.join(output, `${engine.name()}-saved.png`), fullPage:true});
      await page.screenshot({path:path.join(output, `${engine.name()}-saved-viewport.png`)});
      for (const route of ['/invoices/', '/funds/']) {
        await page.goto(base + route); await loadedImages(page, 2, second.url);
        if (route === '/invoices/') await page.locator('#emptyMsg').waitFor({state:'visible'});
        else await page.locator('#rcnt').filter({hasText:'0 条记录'}).waitFor();
      }
      await account(page, base); await loadedImages(page, 3, second.url);
      await page.route('**/api/account/avatar?**', route => route.fulfill({status:404, body:''}));
      await page.reload();
      await page.waitForFunction(() => [...document.querySelectorAll('[data-avatar-initial]')].every(element =>
        !element.hidden && getComputedStyle(element).display !== 'none'));
      assert.ok((await imageState(page)).every(image => image.fallback), 'failed image loads use initials');
      await page.unroute('**/api/account/avatar?**'); await page.reload(); await loadedImages(page, 3, second.url);

      const native = await browser.newContext({storageState:await context.storageState()});
      await native.addInitScript(() => { window.XTYF = {invoke:async () => ({})}; });
      const nativePage = await native.newPage(); let nativeImages = 0;
      nativePage.on('pageerror', error => errors.push(`${engine.name()} native: ${error.message}`));
      nativePage.on('request', request => { if (request.url().includes('/api/account/avatar')) nativeImages++; });
      await nativePage.goto(base + '/account');
      await nativePage.waitForFunction(() => !document.querySelector('#account-profile-form [type=submit]').disabled);
      assert.equal(await nativePage.locator('#avatar-editor').isVisible(), false, 'unadapted client hides edit actions');
      assert.equal(nativeImages, 0, 'unadapted client keeps initials without requesting avatar');
      await native.close();

      const [removed] = await Promise.all([
        page.waitForResponse(response => response.url().includes('/api/account/avatar') && response.request().method() === 'DELETE'),
        page.locator('#avatar-remove').click(),
      ]);
      assert.equal(removed.status(), 200); assert.equal((await removed.json()).avatar_url, null);
      await page.waitForFunction(() => document.querySelector('#avatar-remove').disabled &&
        document.querySelector('#avatar-editor').getAttribute('aria-busy') === 'false' &&
        !document.querySelector('[data-avatar-image]'));
      assert.ok((await imageState(page)).every(image => image.fallback && !image.url));
      await page.reload(); assert.ok((await imageState(page)).every(image => image.fallback && !image.url));
      await page.screenshot({path:path.join(output, `${engine.name()}-restored.png`), fullPage:true});
      results.push({engine:engine.name(), version:browser.version(), mobile, uploads:posts,
        dragInput:mobile ? 'synthetic-touch-pointer' : 'mouse', fileSelection:'filechooser',
        checks:['cancel-no-upload', 'drag-keyboard-zoom-bounds', 'failure-and-retry', 'profile-draft-preserved',
          'three-avatars', 'refresh', 'invoices-and-funds', 'image-fallback', 'native-hidden', 'restore-default']});
      await browser.close(); browser = undefined; currentPage = undefined;
    }
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify({ok:true, results, errors}, null, 2));
    console.log('ACCOUNT_AVATAR_OK ' + JSON.stringify(results));
  } catch (error) {
    if (currentPage) await currentPage.screenshot({path:path.join(output, 'failure.png'), fullPage:true}).catch(() => {});
    fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify({ok:false, results, errors, error:String(error)}, null, 2));
    console.error(error); console.error(errors); console.error(log.slice(-2000)); process.exitCode = 1;
  } finally { if (browser) await browser.close(); server.kill(); }
})();
