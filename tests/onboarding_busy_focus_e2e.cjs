"use strict";

const assert = require("node:assert/strict");
const path = require("node:path");
const {pathToFileURL} = require("node:url");
const {test, before, after} = require("node:test");
const {chromium} = require("playwright");

let browser;
before(async () => {browser = await chromium.launch({channel:"msedge", headless:true});});
after(async () => {await browser?.close();});

async function fixture(t) {
  const page = await browser.newPage({reducedMotion:"reduce"});
  t.after(() => page.close());
  await page.route("**/*", route => route.request().url().startsWith("file:") ? route.continue() : route.abort());
  await page.goto(pathToFileURL(path.join(__dirname,"..","src","jobops","ui","index.html")).href);
  await page.waitForFunction(() => state.activities.length === 0);
  await page.evaluate(() => {
    navigate("questionnaire");
    const target = document.getElementById("saveAnswers");
    target.disabled = false;
    target.focus();
    window.focusFixtureActivity = beginActivity("savingAnswers");
  });
  assert.equal(await page.locator("#saveAnswers").evaluate(element => element.inert), true);
  return page;
}

for (const id of ["emergencyStop","cancelGuidedIntake","pauseAuthorizedDiscovery","killAuthorizedDiscovery","pauseIntakeControl"]) {
  test(`return to the initiating control after ${id} retains focus`, async t => {
    const page = await fixture(t);
    const result = await page.evaluate(interruptionId => {
      const control = document.getElementById(interruptionId);
      // Make the actual interruption control visible in this isolated fixture.
      // No click, API request, applicant data or external action is involved.
      document.body.append(control);
      control.classList.remove("hidden");
      control.hidden = false;
      control.disabled = false;
      const interruption = beginActivity(BUSY_INTERRUPTION_ACTIONS[interruptionId]);
      const blockedWhilePending = control.inert;
      endActivity(interruption, false);
      const saveStillBlocked = document.getElementById("saveAnswers").inert;
      // Reproduce the observed hosted-browser state deterministically: Chromium
      // may retain focus after a brief inert interval instead of blurring to body.
      control.focus();
      const before = document.activeElement?.id;
      endActivity(window.focusFixtureActivity, false);
      return {blockedWhilePending,saveStillBlocked,before,after:document.activeElement?.id};
    },id);
    assert.equal(result.blockedWhilePending,true);
    assert.equal(result.saveStillBlocked,true,"nested completion must not unblock the original operation");
    assert.equal(result.before,id,"fixture must establish the actual hosted focus condition");
    assert.equal(result.after,"saveAnswers");
  });
}

test("restore from the document body when no control retained focus", async t => {
  const page = await fixture(t);
  const result = await page.evaluate(() => {
    document.activeElement?.blur();
    endActivity(window.focusFixtureActivity,false);
    return document.activeElement?.id;
  });
  assert.equal(result,"saveAnswers");
});

test("do not steal focus deliberately moved to another accessible element", async t => {
  const page = await fixture(t);
  const result = await page.evaluate(() => {
    const heading = document.createElement("h2");
    heading.id = "independentFocusFixture";
    heading.tabIndex = -1;
    heading.textContent = "Synthetic focus destination";
    document.body.append(heading);
    heading.focus();
    endActivity(window.focusFixtureActivity,false);
    return document.activeElement?.id;
  });
  assert.equal(result,"independentFocusFixture");
});

for (const condition of ["removed","disabled","hidden"]) test(`do not restore an initiating control that is ${condition}`,async t => {
  const page = await fixture(t);
  const result = await page.evaluate(mode => {
    const target = document.getElementById("saveAnswers");
    if (mode === "removed") target.remove();
    if (mode === "disabled") target.disabled = true;
    if (mode === "hidden") target.style.display = "none";
    endActivity(window.focusFixtureActivity,false);
    return document.activeElement?.id;
  },condition);
  assert.notEqual(result,"saveAnswers");
});
