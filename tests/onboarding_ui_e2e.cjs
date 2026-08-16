"use strict";

const assert = require("node:assert/strict");
const path = require("node:path");
const {pathToFileURL} = require("node:url");
const {chromium} = require("playwright");

(async () => {
  const browser = await chromium.launch({channel: "msedge", headless: true});
  const page = await browser.newPage();
  const pageErrors = [];
  page.on("pageerror", error => pageErrors.push(error.message));
  let nonLocalRequests = 0;
  await page.route("**/*", async route => {
    const url = route.request().url();
    if (url.startsWith("file:")) return route.continue();
    nonLocalRequests += 1;
    return route.abort();
  });
  try {
    const html = path.join(__dirname, "..", "src", "jobops", "ui", "index.html");
    await page.goto(pathToFileURL(html).href, {waitUntil: "load"});
    await page.waitForTimeout(250);

    const initial = await page.evaluate(() => {
      const sources = document.querySelector("#sources");
      const finish = document.querySelector("#finish");
      const dashboard = document.querySelector("#pipelineDashboard");
      return {
        sourcesBeforeDashboard: Boolean(sources.compareDocumentPosition(dashboard) & Node.DOCUMENT_POSITION_FOLLOWING),
        dashboardImmediatelyAfterFinish: finish.nextElementSibling === dashboard,
        scriptVersion: document.querySelector('script[src*="app.js"]')?.getAttribute("src"),
        styleVersion: document.querySelector('link[href*="styles.css"]')?.getAttribute("href"),
      };
    });
    if (!initial.sourcesBeforeDashboard || !initial.dashboardImmediatelyAfterFinish) {
      process.stderr.write(`${JSON.stringify({initial, pageErrors})}\n`);
    }
    assert.equal(initial.sourcesBeforeDashboard, true);
    assert.equal(initial.dashboardImmediatelyAfterFinish, true);
    assert.match(initial.scriptVersion, /20260816-jobflow-v29-form-diagnostics/);
    assert.match(initial.styleVersion, /20260816-jobflow-v29-form-diagnostics/);
    assert.deepEqual(pageErrors, []);

    const failureZh = await page.evaluate(() => {
      state.locale = "zh";
      state.activities = [];
      state.guidedIntakeSession = {
        status: "FORM_CAPTURE_FAILED",
        code: "INELIGIBLE",
        hard_gap_codes: ["level"],
        unknown_condition_codes: [],
      };
      renderActivity();
      const indicator = document.querySelector("#activityIndicator");
      return {
        display: getComputedStyle(indicator).display,
        failed: indicator.classList.contains("failed"),
        title: document.querySelector("#activityTitle").textContent,
        detail: document.querySelector("#activityStage").textContent,
        busy: document.querySelector("main").hasAttribute("aria-busy"),
      };
    });
    assert.notEqual(failureZh.display, "none");
    assert.equal(failureZh.failed, true);
    assert.equal(failureZh.title, "本次处理已停止");
    assert.match(failureZh.detail, /职位级别/);
    assert.equal(failureZh.busy, false);

    const failureEn = await page.evaluate(() => {
      state.locale = "en";
      renderActivity();
      return {
        title: document.querySelector("#activityTitle").textContent,
        detail: document.querySelector("#activityStage").textContent,
        display: getComputedStyle(document.querySelector("#activityIndicator")).display,
      };
    });
    assert.equal(failureEn.title, "This run stopped");
    assert.match(failureEn.detail, /Role level/);
    assert.notEqual(failureEn.display, "none");
    assert.equal(nonLocalRequests, 0);

    process.stdout.write(JSON.stringify({
      status: "PASS",
      materials_before_application_console: true,
      persistent_failure_indicator: true,
      supported_locales: ["zh", "en"],
      real_external_actions: 0,
    }));
  } finally {
    await browser.close();
  }
})().catch(error => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exit(1);
});
