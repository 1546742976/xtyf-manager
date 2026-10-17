'use strict';
// Isolated end-to-end checks: real local APIs/files, no external services.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {chromium, webkit, devices} = require('playwright');
const root = path.resolve(__dirname, '../..');
const output = path.join(root, '.venv', 'local-collaboration-check');
fs.mkdirSync(output, {recursive: true});
async function api(page, url, data) {
  return page.evaluate(async ({url, data}) => {
    const r = await fetch(url, data === undefined ? {} : {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});
    return {status: r.status, body: await r.json()};
  }, {url, data});
}
async function login(page, base, user, password, changeTo) {
  await page.goto(base + '/login');
  await page.locator('[name=username]').fill(user);
  await page.locator('[name=password]').fill(password);
  await page.locator('#login-form [type=submit]').click();
  if (changeTo) {
    await page.waitForURL('**/account');
    await page.locator('#account-password-form [name=old]').fill(password);
    await page.locator('#account-password-form [name=new]').fill(changeTo);
    await page.locator('#account-password-form [name=confirm]').fill(changeTo);
    await page.locator('#account-password-form [type=submit]').click();
  }
  await page.waitForURL(base + '/');
}
async function openTopic(page, base, topic) {
  await page.goto(base + '/discussions?topic=' + topic);
  await page.locator('#collab-messages [data-message-id]').first().waitFor();
}
(async () => {
  const server = spawn(process.env.REVIEW_PYTHON || 'python', ['-B', path.join(__dirname, 'server.py')], {
    cwd: root, env: {...process.env, MAIL_MODE: 'off'}, stdio: ['ignore', 'pipe', 'pipe']});
  let browser, phoneBrowser, log = '', stream = '';
  const checks = [], errors = [], external = [], layouts = [];
  try {
    const base = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('server timeout ' + log)), 30000);
      server.stderr.on('data', b => log += b);
      server.stdout.on('data', b => {stream += b; const m = stream.match(/BROWSER_URL=(\S+)/); if (m) {clearTimeout(timer); resolve(m[1]);}});
      server.once('exit', code => {clearTimeout(timer); reject(new Error('server exit ' + code + log));});
    });
    browser = await chromium.launch({headless: true});
    const context = await browser.newContext({viewport: {width: 1280, height: 900}, reducedMotion: 'reduce'});
    await context.route('**/*', route => {
      const url = new URL(route.request().url());
      if (!['127.0.0.1', 'localhost'].includes(url.hostname)) {external.push(url.origin); return route.abort();}
      return route.continue();
    });
    const page = await context.newPage(); page.setDefaultTimeout(12000);
    page.on('pageerror', e => errors.push(e.message));
    page.on('dialog', d => d.accept());
    await login(page, base, 'admin', '123456', 'collaboration-admin-password');
    const member = (await api(page, '/api/member', {name: '协作成员', username: 'collab_member', password: 'collab-member-password'})).body;
    assert.ok(member.id);
    await page.goto(base + '/discussions');
    await page.locator('#collab-new-topic').click();
    await page.locator('#collab-new-title').fill('装配讨论 <script>');
    await page.locator('#collab-new-body').fill('第一行\n<img src=x onerror=alert(1)>');
    const fileBytes = Buffer.from([0, 1, 255, 10, 13, 60, 62]);
    await page.locator('#collab-new-files').setInputFiles([
      {name: '试验文件-无扩展名', mimeType: 'application/octet-stream', buffer: fileBytes},
      {name: '预览.png', mimeType: 'image/png', buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a7XcAAAAASUVORK5CYII=', 'base64')},
    ]);
    await page.locator('#collab-topic-form [type=submit]').click();
    await page.locator('#collab-messages [data-message-id]').first().waitFor();
    const list = (await api(page, '/discussions/api/topics')).body.topics;
    const topic = list.find(t => t.title === '装配讨论 <script>').id;
    assert.equal(await page.locator('#collab-messages script').count(), 0);
    assert.match(await page.locator('#collab-messages').innerText(), /<img src=x onerror=alert\(1\)>/);
    const data = (await api(page, `/discussions/api/topics/${topic}/messages`)).body;
    const attachment = data.messages[0].attachments.find(a => a.name === '试验文件-无扩展名');
    const downloadResponse = await context.request.get(base + attachment.download_url);
    assert.deepEqual(await downloadResponse.body(), fileBytes);
    assert.equal(downloadResponse.headers()['x-content-type-options'], 'nosniff');
    const fileLink = page.locator(`.collab-attachment[href$="${attachment.download_url}"]`);
    await page.route('**' + attachment.download_url, r => r.request().method() === 'HEAD'
      ? r.fulfill({status: 404}) : r.continue());
    await fileLink.click();
    await page.locator('[data-download-status]').filter({hasText: '附件已撤回或不存在'}).waitFor();
    await page.unroute('**' + attachment.download_url);
    const [nativeDownload] = await Promise.all([page.waitForEvent('download'), fileLink.click()]);
    assert.equal(nativeDownload.suggestedFilename(), '试验文件-无扩展名');
    assert.deepEqual(fs.readFileSync(await nativeDownload.path()), fileBytes);
    checks.push('real topic creation, multiline text, XSS escaping, multi-file upload and byte-perfect download');
    checks.push('native streaming download with failed permission preflight and successful retry');

    // Keyboard line breaks and composition must not accidentally send.
    await page.locator('#collab-body').fill('保留草稿');
    await page.locator('#collab-body').press('Enter');
    assert.equal(await page.locator('#collab-body').inputValue(), '保留草稿\n');
    await page.locator('#collab-body').evaluate(node => {
      node.dispatchEvent(new CompositionEvent('compositionstart', {bubbles: true}));
      node.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', ctrlKey: true, isComposing: true, bubbles: true}));
      node.dispatchEvent(new CompositionEvent('compositionend', {bubbles: true}));
    });
    assert.equal((await api(page, `/discussions/api/topics/${topic}/messages`)).body.messages.length, 1);
    await page.locator('#collab-body').press('Control+Enter');
    await page.waitForFunction(() => document.querySelectorAll('#collab-messages [data-message-id]').length === 2);
    checks.push('Enter newline, IME guard and Ctrl+Enter send');

    // Explicit failure retains selected File objects and text, repeat clicks disabled.
    let held;
    await page.route(`**/discussions/api/topics/${topic}/messages`, r => r.request().method() === 'POST' ? (held = r) : r.continue());
    await page.locator('#collab-body').fill('上传失败仍保留');
    await page.locator('#collab-files').setInputFiles({name: '模型.step', mimeType: 'application/octet-stream', buffer: fileBytes});
    await page.locator('#collab-reply [type=submit]').click();
    await page.waitForFunction(() => document.querySelector('#collab-reply [type=submit]').disabled);
    assert.ok(held);
    await held.fulfill({status: 500, json: {ok: false, error: '模拟磁盘保存失败'}});
    await page.locator('#collab-reply [data-send-status]').filter({hasText: '核对结果并重试'}).waitFor();
    assert.equal(await page.locator('#collab-body').inputValue(), '上传失败仍保留');
    assert.match(await page.locator('#collab-reply').innerText(), /模型.step/);
    await page.unroute(`**/discussions/api/topics/${topic}/messages`);
    await page.locator('#collab-reply [data-send-retry]').click();
    await page.waitForFunction(() => document.querySelector('#collab-body').value === '');
    checks.push('upload failure preserves text/files, disables repeat sends and manual retry succeeds');

    // Server commits but response is lost: reconciliation must not create a duplicate.
    let lostId, posts = 0;
    await page.route(`**/discussions/api/topics/${topic}/messages`, async r => {
      if (r.request().method() !== 'POST') return r.continue();
      posts++;
      const response = await r.fetch();
      lostId = (await response.json()).message_id;
      await r.abort('connectionreset');
    });
    await page.locator('#collab-body').fill('响应丢失但已发布');
    await page.locator('#collab-reply [type=submit]').click();
    await page.locator('#collab-messages').filter({hasText: '响应丢失但已发布'}).waitFor();
    assert.equal(posts, 1); assert.ok(lostId);
    assert.equal((await api(page, `/discussions/api/topics/${topic}/messages`)).body.messages.filter(m => m.body === '响应丢失但已发布').length, 1);
    await page.unroute(`**/discussions/api/topics/${topic}/messages`);
    checks.push('lost successful response reconciles by submission ID without reposting');

    const memberContext = await browser.newContext({viewport: {width: 390, height: 844}});
    const peer = await memberContext.newPage(); peer.setDefaultTimeout(12000);
    peer.on('pageerror', e => errors.push(e.message));
    await login(peer, base, 'collab_member', 'collab-member-password', 'collab-member-password-changed');
    const unreadBefore = (await api(peer, '/discussions/api/unread')).body.total;
    assert.ok(unreadBefore >= 4);
    await openTopic(peer, base, topic);
    for (const message of await peer.locator('#collab-messages [data-message-id]').all()) {
      await message.scrollIntoViewIfNeeded(); await peer.waitForTimeout(850);
    }
    await peer.waitForFunction(async () => (await (await fetch('/discussions/api/unread')).json()).total === 0);
    const secondPeer = await memberContext.newPage();
    await secondPeer.goto(base + '/');
    assert.equal((await api(secondPeer, '/discussions/api/unread')).body.total, 0);
    checks.push('member access and actual visible read position shared across sessions');

    // Long history: old-message reader keeps position and focus when new reply arrives.
    for (let i = 0; i < 54; i++) {
      const r = await api(page, `/discussions/api/topics/${topic}/messages`, {submission_id: 'history-' + i + '-browser', body: '历史记录 ' + i});
      assert.equal(r.status < 300, true);
    }
    await openTopic(page, base, topic);
    await page.locator('#collab-older').click();
    await page.waitForFunction(() => document.querySelectorAll('#collab-messages [data-message-id]').length > 50);
    await page.locator('#collab-messages-scroll').evaluate(el => {el.scrollTop = 0;});
    await page.locator('#collab-body').focus();
    const beforeScroll = await page.locator('#collab-messages-scroll').evaluate(el => el.scrollTop);
    await api(peer, `/discussions/api/topics/${topic}/messages`, {submission_id: 'new-browser-peer', body: '来自另一位成员的新消息'});
    await page.locator('#collab-new-messages').waitFor({state: 'visible', timeout: 15000});
    assert.ok(Math.abs((await page.locator('#collab-messages-scroll').evaluate(el => el.scrollTop)) - beforeScroll) < 3);
    assert.equal(await page.locator('#collab-body').evaluate(el => el === document.activeElement), true);
    await page.locator('#collab-new-messages').click();
    checks.push('50-message history pagination and polling preserves reading position and input focus');

    // Topic changes preserve independent drafts and selected files.
    const other = (await api(page, '/discussions/api/topics', {submission_id: 'other-draft-topic', title: '另一个讨论话题', body: '这里是另一个话题'})).body;
    await page.locator('#collab-body').fill('第一个话题的草稿');
    await page.locator('#collab-files').setInputFiles({name: '草稿资料.zip', mimeType: 'application/zip', buffer: fileBytes});
    await page.locator('#collab-query').fill('另一个讨论话题');
    await page.locator('#collab-search [type=submit]').click();
    await page.locator(`[data-topic-id="${other.topic_id}"]`).click();
    await page.locator('#collab-title').filter({hasText: '另一个讨论话题'}).waitFor();
    assert.equal(await page.locator('#collab-body').inputValue(), '');
    await page.locator('#collab-body').fill('第二个话题的草稿');
    await page.locator('#collab-query').fill('装配讨论');
    await page.locator('#collab-search [type=submit]').click();
    await page.locator(`[data-topic-id="${topic}"]`).click();
    assert.equal(await page.locator('#collab-body').inputValue(), '第一个话题的草稿');
    assert.match(await page.locator('#collab-reply [data-selected-files]').innerText(), /草稿资料.zip/);
    await page.locator('#collab-reply [data-remove-file]').click();
    await page.locator('#collab-body').fill('');
    checks.push('switching topics preserves separate text and File drafts without reposting');

    // Existing attachment keyboard focus survives a polling update.
    const focusedAttachment = page.locator(`.collab-attachment[href$="${attachment.download_url}"]`);
    await focusedAttachment.focus();
    const additional = (await api(peer, `/discussions/api/topics/${topic}/messages`, {submission_id: 'focus-peer-reply', body: '更新后保持附件键盘焦点'})).body;
    await page.locator(`[data-message-id="${additional.message_id}"]`).waitFor({state: 'attached', timeout: 15000});
    assert.equal(await focusedAttachment.evaluate(el => el === document.activeElement), true);
    await page.locator(`[data-message-id="${additional.message_id}"] [data-withdraw-message]`).click();
    await page.locator(`[data-message-id="${additional.message_id}"]`).filter({hasText: '这条消息已撤回'}).waitFor({timeout: 15000});
    checks.push('attachment focus survives new replies, management withdrawal arrives through change cursor');

    for (let i = 0; i < 20; i++) await api(page, '/discussions/api/topics', {
      submission_id: 'pagination-browser-' + i, title: '分页话题 ' + i, body: '分页测试消息'});
    await page.locator('#collab-query').fill('');
    await page.locator('#collab-search [type=submit]').click();
    await page.waitForFunction(() => document.querySelectorAll('#collab-topics [data-topic-id]').length === 20);
    await page.locator('#collab-next').click();
    await page.locator('#collab-page').filter({hasText: '第 2 页'}).waitFor();
    assert.equal(await page.locator('#collab-topics [data-topic-id]').count(), 2);
    checks.push('real 20-topic UI pagination and title search');

    const palettes = ['silver', 'rm-light', 'mist', 'paw', 'space', 'rm-dark', 'graphite', 'xueli'];
    for (const palette of palettes) {
      const light = ['silver', 'rm-light', 'mist', 'paw'].includes(palette);
      const savedTheme = await api(page, '/api/account/preferences', {mode: light ? 'light' : 'dark', light_theme: light ? palette : 'silver', dark_theme: light ? 'space' : palette});
      assert.equal(savedTheme.status, 200);
      await page.reload(); await page.locator('#collab-messages [data-message-id]').first().waitFor();
      assert.equal(await page.locator('html').getAttribute('data-palette'), palette);
      const contrasts = await page.locator('.collab-message:not(.is-withdrawn)').first().evaluate(el => {
        const luminance = value => {
          const v = value.match(/[\d.]+/g).slice(0, 3).map(Number).map(x => {
            x /= 255; return x <= .04045 ? x / 12.92 : ((x + .055) / 1.055) ** 2.4;
          });
          return v[0] * .2126 + v[1] * .7152 + v[2] * .0722;
        };
        const bg = luminance(getComputedStyle(el).backgroundColor);
        return ['.collab-message-body', 'time'].map(selector => {
          const fg = luminance(getComputedStyle(el.querySelector(selector)).color);
          return (Math.max(bg, fg) + .05) / (Math.min(bg, fg) + .05);
        });
      });
      assert.ok(contrasts.every(ratio => ratio >= 4.5), palette + ' text contrast ' + contrasts);
      for (const width of [375, 390, 767, 768, 850, 1280]) {
        await page.setViewportSize({width, height: 844});
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
        assert.equal(overflow, false, palette + ' overflow ' + width);
        const box = await page.locator('#collab-reply [type=submit]').boundingBox();
        assert.ok(box && box.height >= 44 && box.width >= 44, 'send target ' + palette + width);
        layouts.push({palette, width, overflow, contrasts});
      }
      await page.screenshot({path: path.join(output, palette + '-desktop.png')});
    }
    await page.setViewportSize({width: 390, height: 844});
    await page.screenshot({path: path.join(output, 'mobile-detail.png')});
    await page.locator('#collab-back').click();
    assert.equal(await page.locator('#collab-topics').isVisible(), true);
    assert.equal(await page.locator('#collab-reply').isVisible(), false);
    await page.locator('#collab-new-topic').click();
    const dialog = await page.locator('#collab-topic-dialog').boundingBox();
    assert.ok(dialog.width <= 390 && dialog.x >= 0);
    await page.locator('#collab-topic-form [type=submit]').scrollIntoViewIfNeeded();
    await page.screenshot({path: path.join(output, 'mobile-create.png')});
    await page.keyboard.press('Escape');
    checks.push('8 palettes × 6 widths, 44px send target, mobile list/back and reachable dialog actions');

    await openTopic(page, base, topic);
    await page.locator('#collab-body').fill('手机上传失败保留全部文件');
    await page.locator('#collab-files').setInputFiles(Array.from({length: 5}, (_, i) => ({
      name: `文件${i}-` + '很长的中文文件名'.repeat(10) + '.bin', mimeType: 'application/octet-stream', buffer: fileBytes})));
    await page.route(`**/discussions/api/topics/${topic}/messages`, r => r.request().method() === 'POST'
      ? r.fulfill({status: 503, json: {ok: false, error: '模拟手机上传失败'}}) : r.continue());
    await page.locator('#collab-reply [type=submit]').click();
    const phoneRetry = page.locator('#collab-reply [data-send-retry]');
    await phoneRetry.waitFor(); await phoneRetry.scrollIntoViewIfNeeded();
    const reachable = await phoneRetry.evaluate(el => {
      const r = el.getBoundingClientRect();
      return r.top >= 0 && r.bottom <= innerHeight && el === document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
    });
    assert.equal(reachable, true, 'mobile five-file failure retry must be reachable');
    const backVisible = await page.locator('#collab-back').evaluate(el => {
      const r = el.getBoundingClientRect();
      return el === document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
    });
    assert.equal(backVisible, true, 'long failed upload must not push topic navigation outside its panel');
    assert.equal(await page.locator('#collab-reply [data-selected-files] li').count(), 5);
    await page.screenshot({path: path.join(output, 'mobile-upload-failure.png')});
    checks.push('mobile five long filenames and uncertain upload retain files with reachable retry');

    // Optional local WebKit executable provides simulated iPhone coverage, never real-device signoff.
    phoneBrowser = await webkit.launch({headless: true, ...(process.env.REVIEW_WEBKIT_PATH ? {executablePath: process.env.REVIEW_WEBKIT_PATH} : {})});
    const phoneContext = await phoneBrowser.newContext({...devices['iPhone 13']});
    const phone = await phoneContext.newPage(); phone.on('pageerror', e => errors.push(e.message));
    await login(phone, base, 'admin', 'collaboration-admin-password');
    await openTopic(phone, base, topic);
    await phone.locator('#collab-body').fill('模拟 iPhone 文件回复');
    await phone.locator('#collab-files').setInputFiles({name: '手机数据.bin', mimeType: 'application/octet-stream', buffer: fileBytes});
    await phone.locator('#collab-reply [type=submit]').click();
    await phone.waitForFunction(() => document.querySelector('#collab-body').value === '');
    assert.equal(await phone.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    await phone.screenshot({path: path.join(output, 'webkit-phone.png')});
    await phone.setViewportSize({width: 844, height: 390});
    assert.equal(await phone.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    checks.push('simulated iPhone WebKit file upload, portrait/landscape (not real hardware)');

    assert.deepEqual(external, []); assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify({ok: true, checks, layouts, errors, external, real_devices: false, production: false}, null, 2));
    console.log('LOCAL_COLLABORATION_OK ' + JSON.stringify(checks));
  } catch (error) {console.error(error); console.error(log.slice(-2500)); process.exitCode = 1;}
  finally {if (phoneBrowser) await phoneBrowser.close(); if (browser) await browser.close(); server.kill();}
})();
