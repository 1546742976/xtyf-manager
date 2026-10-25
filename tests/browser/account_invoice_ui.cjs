'use strict';
// Real templates and an isolated Flask database; mail delivery and invoice jobs are never started.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawn, execFileSync} = require('node:child_process');
let playwright;
try { playwright = require('playwright'); }
catch (_) {
  playwright = require(execFileSync(process.env.PLAYWRIGHT_PYTHON || 'python', ['-c',
    'import pathlib,playwright; print(pathlib.Path(playwright.__file__).parent/"driver/package")'], {encoding:'utf8'}).trim());
}
const root = path.resolve(__dirname, '../..');
const output = path.join(root, '.build-tools/account-invoice-ui-check');
const widths = [390, 760, 761, 1280, 1864];
const palettes = ['silver', 'rm-light', 'mist', 'paw', 'space', 'rm-dark', 'graphite', 'xueli'];
fs.mkdirSync(output, {recursive:true});

async function checkAccount(page, width, required = false) {
  await page.setViewportSize({width, height:884});
  const layout = await page.evaluate(() => {
    const cards = [...document.querySelectorAll('.ux-account-grid > .panel')].map(el => {
      const r = el.getBoundingClientRect();
      const children = [...el.querySelectorAll('input:not([hidden]),button:not([hidden]),fieldset')]
        .filter(child => child.getClientRects().length).map(child => child.getBoundingClientRect());
      return {name:el.className.match(/ux-account-(profile|email|appearance|editor|password)\b/)[1],
        x:r.x, y:r.y, width:r.width, height:r.height, bottom:r.bottom,
        minHeight:getComputedStyle(el).minHeight,
        overflow:children.some(c => c.left < r.left - 1 || c.right > r.right + 1 || c.bottom > r.bottom + 1)};
    });
    return {cards, overflow:document.documentElement.scrollWidth > innerWidth + 1};
  });
  assert.equal(layout.overflow, false, `account body overflow at ${width}`);
  for (const card of layout.cards) assert.equal(card.overflow, false, `${card.name} overflow at ${width}`);
  const named = Object.fromEntries(layout.cards.map(card => [card.name, card]));
  assert.deepEqual(layout.cards.map(c => c.name), required
    ? ['profile', 'editor', 'appearance'] : ['profile', 'editor', 'email', 'appearance']);
  if (width > 760) {
    for (const [left, right] of required ? [['profile', 'editor']] : [['profile', 'editor'], ['appearance', 'email']]) {
      assert.ok(Math.abs(named[left].y - named[right].y) < 1, `${left}/${right} top at ${width}`);
      assert.ok(Math.abs(named[left].bottom - named[right].bottom) < 1, `${left}/${right} bottom at ${width}`);
      assert.ok(named[left].width < named[right].width, `left column narrower at ${width}`);
      assert.ok(Math.abs(named[right].x - named[left].x - named[left].width - 24) < 1, 'column gap');
    }
    assert.ok(named.profile.height >= 512, `first row minimum at ${width}`);
    if (!required) assert.ok(Math.abs(named.appearance.y - named.profile.bottom - 24) < 1, 'shared row gap');
  } else {
    for (const [index, card] of layout.cards.entries()) {
      assert.equal(card.minHeight, '0px', 'mobile removes desktop minimum');
      if (index) assert.ok(Math.abs(card.y - layout.cards[index - 1].bottom - 24) < 1, 'mobile natural order/gap');
    }
  }
  assert.equal(await page.locator('.ux-account-editor #account-password-form').count(), 1);
  assert.equal(await page.locator('.ux-account-profile #member-introductions').count(), required ? 0 : 1);
  return layout;
}

async function checkButton(page, selector, state) {
  const result = await page.locator(selector).evaluate((el, state) => {
    const style = getComputedStyle(el);
    const color = variable => {
      const probe = document.createElement('span');
      probe.style.color = `var(${variable})`; el.append(probe);
      const value = getComputedStyle(probe).color; probe.remove(); return value;
    };
    return {bg:style.backgroundColor, text:style.color, border:style.borderTopColor,
      expectedBg:color(state === 'hover' ? '--surface-hover' : '--card'),
      expectedText:color('--text'), expectedBorder:color('--border'),
      outline:parseFloat(style.outlineWidth), outlineStyle:style.outlineStyle, opacity:Number(style.opacity)};
  }, state);
  assert.equal(result.bg, result.expectedBg, `${selector} ${state} background`);
  assert.equal(result.text, result.expectedText, `${selector} ${state} text`);
  assert.equal(result.border, result.expectedBorder, `${selector} ${state} border`);
  if (state === 'focus') assert.ok(result.outline > 0 && result.outlineStyle !== 'none', 'visible keyboard focus');
  if (state === 'disabled') assert.equal(result.opacity, .5);
}

(async () => {
  const fixturePython = process.env.REVIEW_PYTHON || path.join(root, '.venv/Scripts/python.exe');
  const server = spawn(fixturePython, ['-B', path.join(__dirname, 'server.py')], {
    cwd:root, env:{...process.env, MAIL_MODE:'preview', PUBLIC_BASE_URL:'https://team.example'}, stdio:['ignore','pipe','pipe'],
  });
  let browser, log = '', buffer = '';
  const errors = [], checks = [], accountLayouts = [];
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
    browser = await playwright.chromium.launch({headless:true});
    const page = await browser.newPage({viewport:{width:1864, height:884}, reducedMotion:'reduce'});
    page.setDefaultTimeout(15000); page.on('pageerror', error => errors.push(error.message));
    await page.goto(base + '/login');
    await page.locator('[name=username]').fill('admin'); await page.locator('[name=password]').fill('browser-test-initial');
    await page.locator('#login-form [type=submit]').click(); await page.waitForURL('**/account');
    for (const width of widths) accountLayouts.push({required:true, width, ...await checkAccount(page, width, true)});
    checks.push('forced password layout: paired profile/password row; no profile form or empty mobile rows');
    await page.locator('#account-password-form [name=old]').fill('browser-test-initial');
    for (const name of ['new', 'confirm']) await page.locator(`#account-password-form [name=${name}]`).fill('account-ui-test-password');
    await page.locator('#account-password-form button').click(); await page.waitForURL(base + '/');
    await page.goto(base + '/account');
    await page.waitForFunction(() => !document.querySelector('#account-profile-form [type=submit]').disabled);
    for (const width of widths) {
      accountLayouts.push({required:false, width, ...await checkAccount(page, width)});
      if ([390, 1864].includes(width)) {
        await page.evaluate(() => scrollTo(0, 0));
        await page.screenshot({path:path.join(output, `account-normal-${width}.png`), fullPage:true});
      }
    }
    checks.push('normal account rows align and all four panels stay ordered at 390/760/761/1280/1864 px');
    await page.route('**/api/account/email', route => route.fulfill({json:{ok:true, available:true, mode:'preview',
      email:{address:'layout@example.com', verified:false, pending:true, paused:false}}}));
    const longChoices = Array.from({length:18}, (_, i) => ({id:i + 1, is_active:1, name:`长选项${i + 1}：机械电控视觉联合研发组`}));
    await page.route('**/api/groups', route => route.fulfill({json:{ok:true, groups:longChoices}}));
    await page.route('**/api/projects', route => route.fulfill({json:{ok:true, projects:longChoices}}));
    await page.reload(); await page.locator('#email-code-form').waitFor({state:'visible'});
    await page.waitForFunction(() => !document.querySelector('#account-profile-form [type=submit]').disabled);
    for (const width of widths) accountLayouts.push({expanded:true, width, ...await checkAccount(page, width)});
    assert.ok(accountLayouts.at(-1).cards.find(c => c.name === 'email').height > 512, 'OTP form grows email row');
    checks.push('pending email code form and long group/project choices grow both rows without clipping');
    await page.route('**/invoices/api/jobs', route => route.fulfill({json:{jobs:[
      {id:'ui-done', type:'refresh', status:'done', progress:100, message:'完成', actor_name:'UI fixture', created_at:1},
      {id:'ui-running', type:'refresh', status:'running', progress:40, message:'处理中', actor_name:'UI fixture', created_at:1},
    ]}}));
    for (const [index, palette] of palettes.entries()) {
      const saved = await page.evaluate(async ({palette, dark}) => {
        const r = await BusinessUI.fetch('/api/account/preferences', {method:'POST', headers:{'Content-Type':'application/json'},
          body:JSON.stringify({mode:dark?'dark':'light', light_theme:dark?'silver':palette, dark_theme:dark?palette:'space'})});
        return r.ok;
      }, {palette, dark:index >= 4});
      assert.equal(saved, true);
      await page.goto(base + '/invoices/'); await page.locator('[data-job-id="ui-done"]').waitFor();
      assert.equal(await page.locator('html').getAttribute('data-palette'), palette);
      for (const selector of ['#invoiceSortDirection', '[data-job-id="ui-done"]', '[data-job-id="ui-running"]']) {
        await page.mouse.move(0, 0); await checkButton(page, selector, 'normal');
        await page.locator(selector).hover(); await checkButton(page, selector, 'hover');
        await page.mouse.move(0, 0); await page.keyboard.press('Tab'); await page.locator(selector).focus();
        await checkButton(page, selector, 'focus');
        await page.locator(selector).evaluate(el => { el.disabled = true; }); await checkButton(page, selector, 'disabled');
        await page.locator(selector).evaluate(el => { el.disabled = false; });
      }
      await page.locator('#invoiceSortDirection').click(); assert.match(await page.locator('#invoiceSortDirection').textContent(), /升序/);
      for (const width of widths) {
        await page.setViewportSize({width, height:884});
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `invoice ${palette} overflow ${width}`);
      }
      if (palette === 'graphite') {
        await page.evaluate(() => scrollTo(0, 0));
        await page.screenshot({path:path.join(output, 'invoices-graphite-1864.png'), fullPage:true});
        await page.goto(base + '/account'); await page.locator('#email-code-form').waitFor({state:'visible'});
        await page.screenshot({path:path.join(output, 'account-graphite-expanded-1864.png'), fullPage:true});
      }
    }
    checks.push('eight palettes: both job labels and sorting normal/hover/focus/disabled colors, sorting behavior and five viewport widths');
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify({ok:true, checks, widths, palettes, accountLayouts, errors}, null, 2));
    console.log('ACCOUNT_INVOICE_UI_OK ' + JSON.stringify(checks));
  } catch (error) { console.error(error); console.error(errors); console.error(log.slice(-2000)); process.exitCode = 1; }
  finally { if (browser) await browser.close(); server.kill(); }
})();
