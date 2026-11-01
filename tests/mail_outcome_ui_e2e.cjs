"use strict";
// Actual local HTTP/DPAPI; synthetic user review, no provider or mailbox.
const assert = require("node:assert/strict");
const {spawn} = require("node:child_process");
const {mkdirSync, writeFileSync} = require("node:fs");
const path = require("node:path");
const {chromium} = require("playwright");
const root = path.resolve(__dirname, "..");

(async () => {
  const artifacts = path.join(root, "reports", "mail-outcome-ui", String(Date.now()));
  mkdirSync(artifacts, {recursive: true});
  const python = process.env.JOBFLOW_TEST_PYTHON || path.join(root, ".venv", "Scripts", "python.exe");
  const child = spawn(python, [path.join(__dirname, "workbench_ui_server.py"), "--stdio-control", "--mail-outcome-fixture"],
    {cwd: root, windowsHide: true, stdio: ["pipe", "pipe", "pipe"]});
  let browser, output = "", stderr = "";
  const exited = new Promise(resolve => child.once("exit", resolve));
  child.stderr.on("data", value => { stderr += value; });
  try {
    const url = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("Synthetic mail-outcome startup timed out")), 120000);
      child.once("error", error => { clearTimeout(timer); reject(error); });
      child.once("exit", code => { clearTimeout(timer); reject(new Error(`Fixture exited ${code}: ${stderr}`)); });
      child.stdout.on("data", value => {
        output += value;
        if (output.includes("\n")) { clearTimeout(timer); resolve(JSON.parse(output.trim().split("\n")[0]).url); }
      });
    });
    const origin = new URL(url).origin;
    browser = await chromium.launch({channel: "msedge", headless: true});
    const page = await browser.newPage({viewport: {width: 1365, height: 900}, reducedMotion: "reduce"});
    page.setDefaultTimeout(30000);
    const errors = [], external = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.route("**/*", route => {
      if (route.request().url().startsWith(origin + "/")) return route.continue();
      external.push(route.request().url()); return route.abort();
    });
    const api = async (name, data, expected = 200) => {
      const response = await page.request.post(origin + "/api/" + name,
        {headers: {"X-JobOps-Session": "synthetic-workbench-ui", "Origin": origin}, data});
      assert.equal(response.status(), expected, await response.text());
      return response.json();
    };
    const capture = async (card, name) => {
      // Capture actual viewports: a tall element screenshot can composite the
      // sticky page header over card content that was never covered on screen.
      await card.locator("h4").evaluate(element => {
        const header = document.querySelector(".topbar").getBoundingClientRect();
        window.scrollBy(0, element.getBoundingClientRect().top - header.height - 16);
      });
      await page.waitForFunction(() => getComputedStyle(document.querySelector("#toast")).opacity === "0");
      const heading = await card.locator("h4").boundingBox();
      const topbar = await page.locator(".topbar").boundingBox();
      assert.ok(heading.y >= topbar.y + topbar.height, "Card title must be below the sticky header");
      await page.screenshot({path: path.join(artifacts, name + "-result.png")});
      await card.locator(".mail-actions").evaluate(element => {
        window.scrollBy(0, element.getBoundingClientRect().bottom - window.innerHeight + 20);
      });
      for (const control of await card.locator(".mail-actions button").all()) {
        const box = await control.boundingBox();
        assert.ok(box.y >= topbar.height && box.y + box.height <= page.viewportSize().height);
      }
      await page.screenshot({path: path.join(artifacts, name + "-controls.png")});
    };
    await page.goto(url);
    await page.getByRole("button", {name: "查看状态收件箱", exact: true}).click();
    const panel = page.locator("#statusInboxPanel");
    await panel.locator(".mail-event").first().waitFor();
    const initial = await api("status-inbox", {});
    assert.equal(initial.events.length, 3);
    assert.ok(initial.events.every(event => event.user_outcome === null && event.status_proposal === "UNKNOWN"));
    const english = panel.locator(".mail-event").filter({hasText: "Please choose a time"});
    const quote = "Please choose a time to speak with our hiring panel.";
    await english.locator("summary").click();
    await english.getByRole("button", {name: "保存消息结果", exact: true}).click();
    assert.match(await page.locator("#workbenchNotice").innerText(), /核对/);
    await english.locator(".mail-application").selectOption("APP-DEFACED00001");
    await english.locator(".mail-confirm").check();
    await english.getByRole("button", {name: "保存消息结果", exact: true}).click();
    assert.match(await page.locator("#workbenchNotice").innerText(), /原文/);
    await english.locator(".mail-outcome-status").focus();
    await page.keyboard.press("Home");
    for (let step = 0; step < 3; step++) await page.keyboard.press("ArrowDown");
    assert.equal(await english.locator(".mail-outcome-status").inputValue(), "INTERVIEW_INVITED");
    await english.locator(".mail-outcome-quote").fill("This invented quote does not occur in the message.");
    await english.getByRole("button", {name: "保存消息结果", exact: true}).click();
    await page.waitForFunction(() => document.querySelector("#workbenchNotice").textContent.includes("不要改写"));
    assert.equal((await api("status-inbox", {})).review_ref, initial.review_ref);
    await english.locator(".mail-outcome-quote").fill(quote);
    await english.getByRole("button", {name: "保存消息结果", exact: true}).focus();
    await page.keyboard.press("Enter");
    await english.locator(".mail-user-outcome").waitFor();
    assert.match(await english.locator(".mail-user-outcome").innerText(), /面试邀请/);
    assert.match(await english.locator("p").first().innerText(), /无法确定/);
    const recorded = await api("status-inbox", {});
    const saved = recorded.events.find(event => event.body.includes(quote));
    assert.equal(saved.user_outcome.quote, quote);
    assert.equal(saved.status_proposal, "UNKNOWN");
    assert.equal(saved.authenticated_sender, false);
    assert.equal(saved.review_history.length, 1);
    const stale = await api("review-status-message", {event_id: saved.event_id, application_id: saved.application_id,
      decision: "RECORD_OUTCOME", reported_status: "REJECTED", evidence_quote: quote,
      user_confirmed: true, expected_review_ref: initial.review_ref}, 400);
    assert.equal(stale.code, "MAIL_REVIEW_STALE");
    await page.waitForFunction(() => getComputedStyle(document.querySelector("#toast")).opacity === "0");
    await capture(english, "desktop-zh");

    await page.reload();
    await page.getByRole("button", {name: "查看状态收件箱", exact: true}).click();
    await english.locator(".mail-user-outcome").waitFor();
    assert.equal(await english.locator(".mail-confirm").isChecked(), false);
    const localeResponse = page.waitForResponse(response => response.url().endsWith("/api/save"));
    await page.getByText("EN", {exact: true}).click();
    assert.equal((await localeResponse).status(), 200);
    await page.locator("#activityIndicator").waitFor({state: "hidden"});
    await page.setViewportSize({width: 390, height: 844});
    await english.scrollIntoViewIfNeeded();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), true);
    assert.match(await english.locator(".mail-user-outcome").innerText(), /Your recorded message outcome: Interview invitation/);
    await capture(english, "mobile-en");
    const chinese = panel.locator(".mail-event").filter({hasText: "请选一个时间与招聘小组交谈。"});
    await chinese.locator(".mail-application").selectOption("APP-DEFACED00001");
    await chinese.locator(".mail-outcome-status").selectOption("INTERVIEW_INVITED");
    await chinese.locator(".mail-outcome-quote").fill("请选一个时间与招聘小组交谈。");
    await chinese.locator(".mail-confirm").focus();
    await page.keyboard.press("Space");
    await chinese.getByRole("button", {name: "Save message outcome", exact: true}).click();
    await chinese.locator(".mail-user-outcome").waitFor();

    const ambiguous = panel.locator(".mail-event").filter({hasText: "Repeated phrase. First section."});
    await ambiguous.locator(".mail-application").selectOption("APP-DEFACED00001");
    await ambiguous.locator(".mail-outcome-status").selectOption("UNKNOWN");
    await ambiguous.locator(".mail-outcome-quote").fill("Repeated phrase.");
    await ambiguous.locator(".mail-confirm").check();
    await ambiguous.getByRole("button", {name: "Save message outcome", exact: true}).click();
    await page.waitForFunction(() => document.querySelector("#workbenchNotice").textContent.includes("more than once"));
    await ambiguous.locator(".mail-outcome-quote").fill("Repeated phrase. First section.");
    await ambiguous.getByRole("button", {name: "Save message outcome", exact: true}).click();
    await ambiguous.locator(".mail-user-outcome").waitFor();
    assert.match(await ambiguous.locator(".mail-user-outcome").innerText(), /Uncertain/);

    await english.locator(".mail-confirm").check();
    await english.getByRole("button", {name: "Undo review", exact: true}).click();
    await english.locator(".mail-user-outcome").waitFor({state: "detached"});
    const final = await api("status-inbox", {});
    const undone = final.events.find(event => event.event_id === saved.event_id);
    assert.equal(undone.user_outcome, null);
    assert.equal(undone.application_id, null);
    assert.equal(undone.status_proposal, "UNKNOWN");
    assert.equal(undone.review_history.length, 2);
    assert.equal(undone.review_history[0].outcome.status, "INTERVIEW_INVITED");
    assert.equal((await api("application-workspace", {application_id: "APP-DEFACED00001"})).status, "AWAITING_APPROVAL");
    await panel.getByRole("button", {name: "Close and clear", exact: true}).click();
    assert.equal(await panel.locator(".mail-event").count(), 0);
    assert.deepEqual(errors, []); assert.deepEqual(external, []);
    writeFileSync(path.join(artifacts, "verification.json"), JSON.stringify({status: "PASS", syntheticOnly: true,
      store: "REAL_WINDOWS_DPAPI", mailboxAccess: false, externalRequests: 0, modelRequests: 0,
      locales: ["zh", "en"], viewports: ["1365x900", "390x844"], outcomesRecorded: 3,
      originalProposalRetained: true, exactQuoteRequired: true, ambiguousQuoteRejected: true,
      staleReviewRejected: true, keyboardControls: true, reloadAndUndo: true,
      applicationStateChanged: false, independentHumanAcceptance: false}, null, 2) + "\n");
    process.stdout.write(`Mail-outcome HTTP/DPAPI browser PASS: ${artifacts}\n`);
  } finally {
    if (browser) await browser.close();
    child.stdin.end("stop\n");
    const code = await Promise.race([exited, new Promise(resolve => setTimeout(() => resolve("timeout"), 15000))]);
    if (code === "timeout") { child.kill(); throw new Error("Synthetic mail-outcome fixture did not cleanly stop"); }
    if (code !== 0) throw new Error(`Synthetic mail-outcome fixture exited ${code}: ${stderr}`);
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
