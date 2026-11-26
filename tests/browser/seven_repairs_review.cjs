'use strict';
// Focused regressions from review: live withdrawal masking, retained drafts and owner blockers.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {spawn} = require('node:child_process');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '../..');
const output = path.join(root, '.venv', 'seven-repairs-review-check');
fs.mkdirSync(output, {recursive: true});

async function api(page, url, data) {
  const result = await page.evaluate(async ({url, data}) => {
    const response = await fetch(url, data === undefined ? {} : {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});
    return {status: response.status, body: await response.json()};
  }, {url, data});
  assert.ok(result.status < 300, `${url}: ${result.status} ${JSON.stringify(result.body)}`);
  return result.body;
}

async function login(page, base, username, password, newPassword) {
  await page.goto(base + '/login');
  await page.locator('[name=username]').fill(username);
  await page.locator('[name=password]').fill(password);
  await page.locator('#login-form [type=submit]').click();
  await page.waitForURL('**/account');
  await page.locator('#account-password-form [name=old]').fill(password);
  await page.locator('#account-password-form [name=new]').fill(newPassword);
  await page.locator('#account-password-form [name=confirm]').fill(newPassword);
  await page.locator('#account-password-form [type=submit]').click();
  await page.waitForURL(base + '/');
}

(async () => {
  const server = spawn(process.env.REVIEW_PYTHON || 'python', ['-B', path.join(__dirname, 'server.py')], {
    cwd: root, env: {...process.env, MAIL_MODE: 'off'}, stdio: ['ignore', 'pipe', 'pipe']});
  const checks = [], errors = [], external = [];
  let browser, log = '', stream = '';
  try {
    const base = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('server timeout ' + log)), 30000);
      server.stderr.on('data', data => {log += data;});
      server.stdout.on('data', data => {
        stream += data;
        const match = stream.match(/BROWSER_URL=(\S+)/);
        if (match) {clearTimeout(timer); resolve(match[1]);}
      });
      server.once('exit', code => {clearTimeout(timer); reject(new Error('server exit ' + code + log));});
    });
    browser = await chromium.launch({headless: true});
    async function newPage(context) {
      const owned = context || await browser.newContext({viewport: {width: 1280, height: 900}});
      if (!context) await owned.route('**/*', route => {
        const hostname = new URL(route.request().url()).hostname;
        if (!['localhost', '127.0.0.1'].includes(hostname)) {external.push(hostname); return route.abort();}
        return route.continue();
      });
      const page = await owned.newPage();
      page.setDefaultTimeout(12000);
      page.on('pageerror', error => errors.push(error.message));
      page.on('dialog', dialog => dialog.accept());
      return page;
    }
    const admin = await newPage();
    await login(admin, base, 'admin', 'browser-test-initial', 'review-admin-password');
    const owner = await api(admin, '/api/member', {name: '日常负责人', username: 'repair_owner', password: 'owner-password'});
    const reader = await api(admin, '/api/member', {name: '只读成员', username: 'repair_reader', password: 'reader-password'});
    assert.ok(owner.id && reader.id);
    const task = await api(admin, '/api/task', {title: '只读撤回回归', owner_id: owner.id});
    const readonlyTopics = [];
    let version = 1;
    for (const suffix of ['A', 'B']) {
      const topic = await api(admin, '/discussions/api/topics', {submission_id: crypto.randomUUID(), title: '只读话题' + suffix, body: '应遮蔽正文-' + suffix});
      const linked = await api(admin, `/api/task/${task.id}/topics`, {version, topic_id: topic.topic_id});
      const concluded = await api(admin, `/api/task/${task.id}/topics`, {version: linked.version, topic_id: topic.topic_id, action: 'conclusion', conclusion_id: topic.message_id});
      version = concluded.version;
      readonlyTopics.push(topic);
    }
    const viewer = await newPage();
    await login(viewer, base, 'repair_reader', 'reader-password', 'reader-new-password');
    await viewer.goto(base + `/tasks?task=${task.id}`);
    await viewer.locator('#task-topic-links [data-conclusion]').filter({hasText: '应遮蔽正文-A'}).waitFor();
    await viewer.locator('#task-topic-links [data-conclusion]').filter({hasText: '应遮蔽正文-B'}).waitFor();
    assert.equal(await viewer.locator('#task-topic-links [data-choose]').count(), 0);
    for (const topic of readonlyTopics) await api(admin, `/discussions/api/topics/${topic.topic_id}/withdraw`, {});
    await viewer.waitForFunction(() => {
      const box = document.querySelector('#task-topic-links');
      return box && !box.textContent.includes('应遮蔽正文') &&
        [...box.querySelectorAll('[data-title]')].length === 2 &&
        [...box.querySelectorAll('[data-title]')].every(item => item.textContent === '话题已撤回');
    });
    checks.push('read-only view masks both withdrawn topics and conclusions on live refresh');

    const editable = await api(admin, '/api/task', {title: '结论草稿回归', owner_id: owner.id});
    const draftTopics = [];
    version = 1;
    for (const suffix of ['C', 'D']) {
      const topic = await api(admin, '/discussions/api/topics', {submission_id: crypto.randomUUID(), title: '草稿话题' + suffix, body: '候选结论-' + suffix});
      const linked = await api(admin, `/api/task/${editable.id}/topics`, {version, topic_id: topic.topic_id});
      version = linked.version;
      draftTopics.push(topic);
    }
    const pending = await api(admin, `/discussions/api/topics/${draftTopics[1].topic_id}/messages`, {submission_id: crypto.randomUUID(), body: '未保存候选撤回正文'});
    const control = await newPage(admin.context());
    await control.goto(base + '/');
    await admin.goto(base + `/tasks?task=${editable.id}`);
    const [first, second] = draftTopics;
    const cardA = admin.locator(`#task-topic-links [data-link="${first.topic_id}"]`);
    const cardB = admin.locator(`#task-topic-links [data-link="${second.topic_id}"]`);
    await cardA.locator('[data-choose]').click();
    await cardA.locator('[data-conclusion-choice]').selectOption(String(first.message_id));
    await cardB.locator('[data-choose]').click();
    await cardB.locator('[data-conclusion-choice]').selectOption(String(second.message_id));
    const selectorB = cardB.locator('[data-conclusion-choice]');
    assert.match(await selectorB.textContent(), /未保存候选撤回正文/);
    await cardA.locator('[data-save-conclusion]').click();
    await admin.locator('#task-topic-links [data-feedback]').filter({hasText: '关联已保存'}).waitFor();
    assert.equal(await selectorB.inputValue(), String(second.message_id));
    assert.match(await selectorB.textContent(), /未保存候选撤回正文/);
    checks.push('saving a conclusion keeps another topic card selection and loaded message drafts');
    await api(control, `/discussions/api/messages/${pending.message_id}/withdraw`, {});
    await admin.waitForFunction(({topic}) => {
      const select = document.querySelector(`[data-link="${topic}"] [data-conclusion-choice]`);
      return select && !select.textContent.includes('未保存候选撤回正文');
    }, {topic: second.topic_id});
    assert.equal(await selectorB.inputValue(), String(second.message_id));
    checks.push('withdrawing an unselected candidate masks its body while retaining another selected draft');

    const project = await api(control, '/api/daily-projects', {title: '成员阻塞维护', status: '进行中', groups: {mechanical: [owner.id]}});
    const member = await newPage();
    await login(member, base, 'repair_owner', 'owner-password', 'owner-new-password');
    await member.goto(base + `/tasks?type=daily&project=${project.id}`);
    const progress = member.locator('[data-progress-form=mechanical] [name=note]');
    await progress.fill('保留组进度草稿');
    await member.locator('#daily-blocker-form [name=blocker_reason]').fill('等待零件');
    await member.locator('#daily-blocker-form [name=blocker_next_action]').fill('联系采购确认时间');
    await member.locator(`#daily-blocker-form [name=blocker_helper_id] option[value="${reader.id}"]`).waitFor({state: 'attached'});
    await member.locator('#daily-blocker-form [name=blocker_helper_id]').selectOption(String(reader.id));
    await member.locator('#daily-blocker-form [type=submit]').click();
    await member.locator('[data-daily-status]').filter({hasText: '阻塞'}).waitFor();
    assert.equal(await progress.inputValue(), '保留组进度草稿');
    await member.locator('#daily-blocker-form [name=blocker_result]').fill('零件到货，恢复执行');
    await member.locator('#daily-blocker-form [data-resolve-blocker]').click();
    await member.locator('[data-daily-status]').filter({hasText: '进行中'}).waitFor();
    assert.equal(await progress.inputValue(), '保留组进度草稿');
    assert.equal(await member.locator('#daily-edit').count(), 0);
    const saved = (await api(member, `/api/daily-projects/${project.id}`)).project;
    assert.equal(saved.blocker_events[0].resolution, '零件到货，恢复执行');
    assert.equal(saved.groups[0].progress, 0);
    checks.push('ordinary daily owner can select helper, block and resolve without manager editor or losing group drafts');

    assert.deepEqual(errors, []);
    assert.deepEqual(external, []);
    await member.screenshot({path: path.join(output, 'owner-blocker-and-draft.png'), fullPage: true});
    fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify({ok: true, checks, errors, external}, null, 2));
    console.log('SEVEN_REPAIRS_REVIEW_BROWSER_OK ' + JSON.stringify(checks));
  } catch (error) {
    console.error(error);
    console.error(errors);
    console.error(log.slice(-3000));
    process.exitCode = 1;
  } finally {
    if (browser) await browser.close();
    server.kill();
  }
})();
