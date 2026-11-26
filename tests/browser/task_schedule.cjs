"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {spawn} = require("node:child_process");
const {chromium, webkit} = require("playwright");

const root = path.resolve(__dirname, "../..");
const output = path.join(root, ".venv", "task-schedule-check");
fs.mkdirSync(output, {recursive: true});
const api = (page, url, data) => page.evaluate(async ({url, data}) => {
  const r = await fetch(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(data)});
  return {status: r.status, body: await r.json()};
}, {url, data});
async function login(page, base, password, firstLogin = false) {
  await page.goto(base + "/login");
  await page.locator("[name=username]").fill("admin");
  await page.locator("[name=password]").fill(password);
  await page.locator("#login-form [type=submit]").click();
  if (firstLogin) {
    await page.waitForURL("**/account");
    await page.locator("#account-password-form [name=old]").fill(password);
    await page.locator("#account-password-form [name=new]").fill("schedule-e2e-password");
    await page.locator("#account-password-form [name=confirm]").fill("schedule-e2e-password");
    await page.locator("#account-password-form [type=submit]").click();
  }
  await page.waitForURL(base + "/");
}
async function selectProject(page, base, project) {
  await page.goto(base + "/manage");
  await page.locator("#t-filter-project option[value='" + project + "']").waitFor({state: "attached"});
  await page.locator("#t-filter-project").selectOption(String(project));
  await page.locator("#schedule-project:enabled").waitFor();
}
async function openSchedule(page) {
  await page.locator("#schedule-project").click();
  await page.locator("[data-schedule-root] #schedule-body .schedule-task").first().waitFor();
}

async function checkScheduleEntry(page, base, emptyProject) {
  await page.goto(base + "/manage");
  await page.locator("#t-filter-project option[value='" + emptyProject + "']").waitFor({state: "attached"});
  assert.equal(await page.locator("#schedule-project").isEnabled(), true,
    "default all-groups view must offer group selection instead of a silent disabled button");
  let previews = 0;
  const observe = request => { if (request.url().endsWith("/schedule/preview")) previews++; };
  page.on("request", observe);
  await page.locator("#schedule-project").click();
  await page.locator("#schedule-project-choice").waitFor();
  assert.equal(previews, 0, "opening the chooser must not schedule an arbitrary group");
  await page.keyboard.press("Escape");
  assert.equal(await page.locator("#schedule-project").evaluate(el => el === document.activeElement), true);
  await page.locator("#schedule-project").click();
  await page.locator("#schedule-project-choice [name=project]").selectOption(String(emptyProject));
  await page.locator("#schedule-project-choice [type=submit]").click();
  await page.locator("#schedule-feedback").filter({hasText:"先在任务中心创建任务"}).waitFor();
  assert.equal(await page.locator("#schedule-apply").isDisabled(), true);
  assert.equal(await page.locator("#schedule-body a").getAttribute("href"), "/tasks?type=vehicle");
  assert.equal(previews, 1);
  page.off("request", observe);
  await page.locator("[data-schedule-close]").click();
}

(async () => {
  const server = spawn(process.env.REVIEW_PYTHON || "python", ["-B", path.join(__dirname, "server.py")],
    {cwd: root, env: {...process.env, MAIL_MODE: "off"}, stdio: ["ignore","pipe","pipe"]});
  let browser, webkitBrowser, outputBuffer = "", errors = "", base;
  try {
    base = await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error("Schedule server timed out: " + errors)), 30000);
      server.stderr.on("data", chunk => {errors += chunk;});
      server.stdout.on("data", chunk => {
        outputBuffer += chunk;
        const match = outputBuffer.match(/BROWSER_URL=(\S+)/);
        if (match) {clearTimeout(timeout); resolve(match[1]);}
      });
      server.once("exit", code => {clearTimeout(timeout); reject(new Error("Schedule server exited " + code + ": " + errors));});
    });
    browser = await chromium.launch({headless: true});
    const page = await browser.newPage({viewport: {width: 1280, height: 900}});
    page.setDefaultTimeout(15000);
    const pageErrors = [];
    page.on("pageerror", error => pageErrors.push(error.message));
    await login(page, base, "browser-test-initial", true);
    const emptyProject = (await api(page, "/api/projects", {name: "空组排期入口"})).body.id;
    await checkScheduleEntry(page, base, emptyProject);
    const project = (await api(page, "/api/projects", {name: "依赖排期集成测试"})).body.id;
    assert.ok(project);
    const parent = (await api(page, "/api/task", {title: "前置 A", project_id: project,
      start_date: "2027-01-15", due_date: "2027-01-30"})).body.id;
    const reserved = (await api(page, "/api/task", {title: "提前预留 B", project_id: project,
      parent_id: parent, start_date: "2027-01-20", due_date: "2027-01-22", schedule_locked: true})).body.id;
    const reschedule = (await api(page, "/api/task", {title: "待顺延 C", project_id: project,
      start_date: "2020-01-01", due_date: "2020-01-02"})).body.id;
    assert.ok(parent && reserved && reschedule);
    const preview = (await api(page, "/api/projects/" + project + "/schedule/preview",
      {algorithm_version: 2, base_date: "2027-01-14"})).body;
    assert.equal(preview.can_apply, true);
    assert.equal(preview.rows.find(row => row.id === reserved).after.start_date, "2027-01-20");
    assert.ok(preview.warnings.some(w => w.kind === "dependency_early_reservation" && w.task_id === reserved));
    assert.ok(preview.changed_count > 0);
    const outdated = (await api(page, "/api/task/" + parent, {title: "前置 A 已修改", version: 1})).body;
    assert.equal(outdated.ok, true);
    const conflict = await api(page, "/api/projects/" + project + "/schedule/apply", {snapshot: preview.snapshot});
    assert.equal(conflict.status, 409);
    await selectProject(page, base, project);
    await openSchedule(page);
    assert.equal(await page.locator("#modal-mask").evaluate(el => !el.classList.contains("hidden")), true);
    await page.keyboard.press("Escape");
    assert.equal(await page.locator("#modal-mask").evaluate(el => el.classList.contains("hidden")), true);
    // After closing, the same vehicle group must reopen with a fresh session.
    await openSchedule(page);
    await page.locator("#schedule-base-date").fill("2027-01-14");
    await page.locator("#schedule-repreview").click();
    await page.locator("#schedule-apply:enabled").waitFor();
    let arrived;
    const arrivedPromise = new Promise(resolve => arrived = resolve);
    let release;
    const gate = new Promise(resolve => release = resolve);
    await page.route("**/schedule/apply", async route => {arrived(); await gate; await route.continue();});
    await page.locator("#schedule-apply").click();
    await arrivedPromise;
    assert.equal(await page.locator("#schedule-repreview").isDisabled(), true);
    await page.keyboard.press("Escape");
    assert.equal(await page.locator("#modal-mask").evaluate(el => !el.classList.contains("hidden")), true);
    await page.locator("#modal-mask").click({position: {x: 2, y: 2}});
    assert.equal(await page.locator("#modal-mask").evaluate(el => !el.classList.contains("hidden")), true);
    release();
    await page.locator("#schedule-feedback").filter({hasText: "排期已保存"}).waitFor();
    await page.unroute("**/schedule/apply");
    await page.locator("[data-schedule-close]").click();
    assert.equal(await page.locator("#modal-mask").evaluate(el => el.classList.contains("hidden")), true);
    await openSchedule(page);
    await page.locator("[data-schedule-close]").click();
    // Enter a task from the member lane dialog, then return to that dialog.
    await page.goto(base + "/tasks");
    await page.locator("[data-lane-tasks]").first().waitFor();
    await page.locator("[data-lane-tasks]").first().click();
    await page.locator("#modal [data-member-task-list]").waitFor();
    await page.locator("#modal [data-member-task-list] [data-task-id]").first().click();
    await page.locator("#modal #task-form").waitFor();
    await page.locator("#modal [data-close]").first().click();
    await page.locator("#modal [data-member-task-list]").waitFor();
    await page.locator("#modal [data-close]").first().click();
    assert.deepEqual(pageErrors, []);
    await page.screenshot({path: path.join(output, "schedule-chromium.png")});
    await page.close();

    webkitBrowser = await webkit.launch({headless: true});
    const mobile = await webkitBrowser.newPage({viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true});
    mobile.setDefaultTimeout(15000);
    await login(mobile, base, "schedule-e2e-password");
    await checkScheduleEntry(mobile, base, emptyProject);
    await selectProject(mobile, base, project);
    await openSchedule(mobile);
    await mobile.locator("[data-schedule-close]").click();
    await openSchedule(mobile);
    await mobile.keyboard.press("Escape");
    assert.equal(await mobile.locator("#modal-mask").evaluate(el => el.classList.contains("hidden")), true);
    await mobile.screenshot({path: path.join(output, "schedule-webkit.png")});
    await mobile.close();
    console.log("Task schedule integration passed: signed 409, early reservations, pending-save guard, reopening, member list return, Chromium + WebKit");
  } finally {
    await browser?.close();
    await webkitBrowser?.close();
    server.kill();
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
