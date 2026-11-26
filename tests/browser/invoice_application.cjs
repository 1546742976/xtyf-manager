'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const {spawn, execFileSync} = require('node:child_process');
let playwright;
try { playwright = require('playwright'); }
catch {
  playwright = require(execFileSync(process.env.PLAYWRIGHT_PYTHON || 'python', ['-c',
    'import pathlib,playwright; print(pathlib.Path(playwright.__file__).parent/"driver/package")'], {encoding:'utf8'}).trim());
}

const root = path.resolve(__dirname, '../..');

async function upload(page, amount, suffix) {
  const name = '票据-'+suffix+'.pdf';
  await page.locator('#fileInput').setInputFiles({
    name, mimeType:'application/pdf', buffer:Buffer.from('%PDF-1.4\n% '+suffix+'\n%%EOF')
  });
  const confirmation = page.locator('#batchUploadModal');
  await confirmation.waitFor();
  await confirmation.getByLabel('第 1 张发票金额').fill(amount);
  const owner = confirmation.getByLabel('统一提交人');
  if (await owner.isVisible()) await owner.selectOption('1');
  if (Number(amount) > 500) await confirmation.getByText('超出个人报销限额，可继续归档').waitFor();
  await confirmation.getByRole('button', {name:'全部确认入库'}).click();
  await confirmation.waitFor({state:'hidden'});
}

async function addExpense(page, amount) {
  await page.locator('#aAmt').fill(amount);
  await page.locator('#aSD').fill('选票浏览器回归 '+amount);
  const [result] = await Promise.all([
    page.waitForResponse(response => response.url().endsWith('/funds/api/records') && response.request().method() === 'POST'),
    page.locator('#addF button[type=submit]').click(),
  ]);
  const body = await result.json();
  assert.equal(body.code, 0, JSON.stringify(body));
  await page.locator('[data-bs-target="#t-records"]').click();
  return page.locator('#tbody tr').filter({hasText:'选票浏览器回归 '+amount});
}

(async () => {
  const server = spawn(process.env.REVIEW_PYTHON || 'python', ['-B', path.join(__dirname,'server.py')], {
    cwd:root, env:{...process.env,PYTHONUTF8:'1',FUNDS_PERSONAL_REVIEWER_ID:'1'}, stdio:['ignore','pipe','pipe'],
  });
  let browser, output = '', log = '';
  server.stderr.on('data', data => log += data);
  try {
    const base = await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(Error('fixture startup timeout')), 30000);
      server.stdout.on('data', data => {
        output += data;
        const match = output.match(/BROWSER_URL=(\S+)/);
        if (match) { clearTimeout(timeout); resolve(match[1]); }
      });
      server.once('exit', code => { clearTimeout(timeout); reject(Error('fixture exit '+code)); });
    });
    browser = await playwright.chromium.launch({headless:true});
    const page = await browser.newPage({serviceWorkers:'block', viewport:{width:390,height:844}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(base+'/login');
    await page.locator('[name=username]').fill('admin');
    await page.locator('[name=password]').fill('browser-test-initial');
    await page.locator('#login-form [type=submit]').click();
    await page.waitForURL('**/account');
    await page.locator('#account-password-form [name=old]').fill('browser-test-initial');
    await page.locator('#account-password-form [name=new]').fill('invoice-browser-regression-password');
    await page.locator('#account-password-form [name=confirm]').fill('invoice-browser-regression-password');
    await page.locator('#account-password-form [type=submit]').click();
    await page.waitForURL(base+'/');
    await page.goto(base+'/invoices/');
    await page.getByText('开票信息', {exact:true}).click();
    await page.getByText('南京航空航天大学', {exact:true}).waitFor();
    await page.evaluate(() => Object.defineProperty(navigator, 'clipboard', {
      configurable:true, value:{writeText:async text => { window.copiedInvoiceInfo = text; }}
    }));
    await page.getByRole('button', {name:'复制开票信息'}).click();
    await page.getByText('已复制开票信息').waitFor();
    assert.match(await page.evaluate(() => window.copiedInvoiceInfo), /请将发票备注留空/);

    await upload(page, '350', 'one');
    await page.getByRole('button', {name:'用这些发票申请报销'}).click();
    await page.waitForURL('**/funds/');
    await page.locator('#new-application-summary').filter({hasText:'已选 1 张'}).waitFor();
    const complete = await addExpense(page, '350');
    page.once('dialog', dialog => dialog.accept());
    await complete.getByRole('button', {name:'审核',exact:true}).click();
    await complete.getByText('待付款').waitFor();
    await complete.getByRole('button', {name:'登记报销'}).click();
    await page.getByRole('button', {name:'确认已付款'}).click();
    await complete.getByText('已个人报销').waitFor();

    await page.goto(base+'/invoices/');
    await page.getByRole('button', {name:'我的发票',exact:true}).click();
    await page.locator('tbody').getByText('已个人报销').waitFor();
    await upload(page, '680', 'over');
    await page.locator('tbody').getByText('超出个人报销限额', {exact:true}).waitFor();
    assert.equal(await page.locator('#invoice-upload-next').isVisible(), false);

    await upload(page, '500', 'partial');
    await page.getByRole('button', {name:'用这些发票申请报销'}).click();
    await page.locator('#new-application-summary').filter({hasText:'已选 1 张'}).waitFor();
    const partial = await addExpense(page, '680');
    await partial.getByText('还差 ¥180.00').waitFor();
    assert.equal(await partial.getByRole('button', {name:'审核',exact:true}).count(), 0);
    await page.goto(base+'/invoices/');
    await upload(page, '180', 'remaining');
    await page.getByRole('button', {name:'补到已有申请'}).click();
    await page.locator('#application-transfer-notice').waitFor();
    await partial.getByRole('button', {name:'补票或换票'}).click();
    await page.locator('#application-invoice-summary').filter({hasText:'已选 2 张'}).waitFor();
    assert.equal(await page.getByRole('dialog').getByRole('link', {name:'查看原件'}).count(), 2);
    await page.getByRole('button', {name:'保存发票'}).click();
    await partial.getByRole('button', {name:'审核',exact:true}).waitFor();
    assert.deepEqual(errors, []);
    console.log('INVOICE_APPLICATION_OK: invoice upload, complete payment, oversized archive and partial supplementation');
  } catch (error) { console.error(error, log.slice(-3500)); process.exitCode = 1; }
  finally { if (browser) await browser.close(); server.kill(); }
})();
