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
        skipHref: document.querySelector(".skip-link")?.getAttribute("href"),
        mainTabIndex: document.querySelector("#mainContent")?.getAttribute("tabindex"),
        supportButtonType: document.querySelector("#downloadSupportDiagnostics")?.getAttribute("type"),
        supportLiveRegion: document.querySelector("#supportDiagnosticsStatus")?.getAttribute("aria-live"),
        updateButtonType: document.querySelector("#launchDesktopUpdate")?.getAttribute("type"),
        updateLiveRegion: document.querySelector("#desktopUpdateStatus")?.getAttribute("aria-live"),
        aiHeadingTabIndex: document.querySelector("#aiConnectionTitle")?.getAttribute("tabindex"),
      };
    });
    if (!initial.sourcesBeforeDashboard || !initial.dashboardImmediatelyAfterFinish) {
      process.stderr.write(`${JSON.stringify({initial, pageErrors})}\n`);
    }
    assert.equal(initial.sourcesBeforeDashboard, true);
    assert.equal(initial.dashboardImmediatelyAfterFinish, true);
    assert.match(initial.scriptVersion, /20260819-jobflow-v38-signed-update/);
    assert.match(initial.styleVersion, /20260819-jobflow-v38-signed-update/);
    assert.equal(initial.skipHref, "#mainContent");
    assert.equal(initial.mainTabIndex, "-1");
    assert.equal(initial.supportButtonType, "button");
    assert.equal(initial.supportLiveRegion, "polite");
    assert.equal(initial.updateButtonType, "button");
    assert.equal(initial.updateLiveRegion, "polite");
    assert.equal(initial.aiHeadingTabIndex, "-1");
    await page.keyboard.press("Tab");
    assert.equal(await page.evaluate(() => document.activeElement?.classList.contains("skip-link")), true);
    const toastSemantics = await page.evaluate(() => {
      showToast("Synthetic error", true, 20);
      const toast = document.querySelector("#toast");
      return {role: toast?.getAttribute("role"), live: toast?.getAttribute("aria-live")};
    });
    assert.deepEqual(toastSemantics, {role: "alert", live: "assertive"});
    assert.deepEqual(pageErrors, []);

    const updateStates = await page.evaluate(() => {
      state.locale = "en";
      state.data = {desktop_update: {available: false}, demo_mode: false, browser_assist: {}, guided_intake: {active: false}};
      renderDesktopUpdate();
      const unavailable = {
        disabled: document.querySelector("#launchDesktopUpdate")?.disabled,
        text: document.querySelector("#launchDesktopUpdate")?.textContent,
        status: document.querySelector("#desktopUpdateStatus")?.textContent,
      };
      state.data.desktop_update.available = true;
      renderDesktopUpdate();
      const available = {
        disabled: document.querySelector("#launchDesktopUpdate")?.disabled,
        text: document.querySelector("#launchDesktopUpdate")?.textContent,
        status: document.querySelector("#desktopUpdateStatus")?.textContent,
      };
      state.data.browser_assist.active_assist_id = "AST-SYNTHETIC";
      renderDesktopUpdate();
      const active = {
        disabled: document.querySelector("#launchDesktopUpdate")?.disabled,
        status: document.querySelector("#desktopUpdateStatus")?.textContent,
      };
      return {unavailable, available, active};
    });
    assert.equal(updateStates.unavailable.disabled, true);
    assert.match(updateStates.unavailable.text, /Install the fixed version first/);
    assert.match(updateStates.unavailable.status, /source folder/);
    assert.equal(updateStates.available.disabled, false);
    assert.match(updateStates.available.text, /Check for updates/);
    assert.match(updateStates.available.status, /never checks for or installs updates silently/);
    assert.equal(updateStates.active.disabled, true);
    assert.match(updateStates.active.status, /Finish or cancel/);

    const adaptiveProfile = await page.evaluate(() => {
      state.locale = "zh";
      state.data = {
        status: "IN_PROGRESS",
        catalog: {
          groups: [
            {id: "identity_and_contact", label: {zh: "身份与联系方式", en: "Identity & contact"}},
            {id: "public_links", label: {zh: "公开链接与作品集", en: "Public links & portfolio"}},
          ],
          fields: [
            {id: "first_name", group: "identity_and_contact", label: {zh: "名字", en: "First name"}, help: {zh: "", en: ""}, input_type: "text", options: [], sensitive: false, default_policy: "reuse", required_resolution: false},
            {id: "email", group: "identity_and_contact", label: {zh: "邮箱", en: "Email"}, help: {zh: "", en: ""}, input_type: "text", options: [], sensitive: false, default_policy: "reuse", required_resolution: false},
            {id: "github_url", group: "public_links", label: {zh: "GitHub", en: "GitHub"}, help: {zh: "", en: ""}, input_type: "text", options: [], sensitive: false, default_policy: "reuse", required_resolution: false},
          ],
        },
        answers: {
          first_name: {value: "Jordan", status: "CONFIRMED", source: "APPLICANT_PROVIDED_UNCONFIRMED", use_policy: "reuse"},
          email: {value: null, status: "UNKNOWN", source: "UNKNOWN", use_policy: "reuse"},
          github_url: {value: null, status: "UNKNOWN", source: "UNKNOWN", use_policy: "reuse"},
        },
      };
      renderQuestions();
      return {
        visibleMissingIdentity: document.querySelectorAll('[data-question-group="identity_and_contact"] > .question-row').length,
        resolvedCount: document.querySelectorAll(".resolved-question-details .question-row").length,
        optionalCount: document.querySelectorAll(".optional-question-details .question-row").length,
        resolvedOpen: document.querySelector(".resolved-question-details")?.open,
        resolvedSummary: document.querySelector(".resolved-question-details summary")?.textContent,
      };
    });
    assert.equal(adaptiveProfile.visibleMissingIdentity, 1);
    assert.equal(adaptiveProfile.resolvedCount, 1);
    assert.equal(adaptiveProfile.optionalCount, 1);
    assert.equal(adaptiveProfile.resolvedOpen, false);
    assert.match(adaptiveProfile.resolvedSummary, /已从资料填好 1 项/);

    const operatorActivity = await page.evaluate(() => {
      state.locale = "en";
      state.data = {
        ai_operator: {activity: {recent_turns: [
          {application_id: "APP-ABCDEF123456", decision_point: "JOB_AND_MATERIAL_DECISION", selected_tool: "jobflow.plan_resume_changes", status: "HOST_PIPELINE_VERIFIED"},
          {application_id: "APP-ABCDEF123456", decision_point: "CURRENT_FORM_SEMANTIC_REVIEW", selected_tool: "jobflow.inspect_application_form", status: "HOST_PIPELINE_VERIFIED"},
        ]}},
      };
      renderAiOperatorActivity();
      return {
        hidden: document.querySelector("#aiOperatorActivity").classList.contains("hidden"),
        badge: document.querySelector("#aiOperatorActivityBadge").textContent,
        items: [...document.querySelectorAll(".ai-operator-activity-item")].map(item => item.textContent),
      };
    });
    assert.equal(operatorActivity.hidden, false);
    assert.equal(operatorActivity.badge, "2 verified decisions");
    assert.match(operatorActivity.items[0], /Understand the current form/);
    assert.match(operatorActivity.items[0], /Verified by JobFlow/);
    assert.match(operatorActivity.items[1], /Select approved resume evidence/);

    const linearWorkflow = await page.evaluate(() => {
      state.locale = "zh";
      state.activities = [];
      state.guidedIntakeSession = null;
      state.reviewPacket = null;
      state.browserAssistSession = null;
      state.data = {
        status: "IN_PROGRESS",
        demo_mode: false,
        ai_engine: {status: "READY", available: true},
        sources: [{analysis_mode: "AI_CORE_ENTITY_ANALYSIS"}],
        pending_sources: [],
        completion: {remaining: 2},
        claims: [], conflicts: [], profile_review: "PENDING",
      };
      renderWorkflowNow();
      return {
        title: document.querySelector("#workflowNowTitle").textContent,
        detail: document.querySelector("#workflowNowDetail").textContent,
        action: document.querySelector("#workflowNowAction").textContent,
        target: document.querySelector("#workflowNowAction").dataset.target,
        hidden: document.querySelector("#workflowNowAction").classList.contains("hidden"),
      };
    });
    assert.equal(linearWorkflow.title, "只补充仍然缺失的资料");
    assert.match(linearWorkflow.detail, /仍未解决的必填项/);
    assert.equal(linearWorkflow.action, "继续补充资料");
    assert.equal(linearWorkflow.target, "questionnaire");
    assert.equal(linearWorkflow.hidden, false);
    const workflowNavigation = await page.evaluate(() => {
      document.querySelector("#workflowNowAction").click();
      return {
        questionnaireActive: document.querySelector("#questionnaire").classList.contains("active-panel"),
        activeStep: document.querySelector('.step[aria-current="step"]')?.dataset.target,
      };
    });
    assert.equal(workflowNavigation.questionnaireActive, true);
    assert.equal(workflowNavigation.activeStep, "questionnaire");

    const persistentWork = await page.evaluate(() => {
      state.activities = [{id: 99, key: "preparingGuidedApplication", started: Date.now()-31000, estimatedSeconds: 300}];
      renderActivity();
      return {
        title: document.querySelector("#workflowNowTitle").textContent,
        detail: document.querySelector("#workflowNowDetail").textContent,
        tone: document.querySelector("#workflowNow").dataset.tone,
        target: document.querySelector("#workflowNowAction").dataset.target,
      };
    });
    assert.equal(persistentWork.title, "正在根据岗位生成材料与审阅包…");
    assert.match(persistentWork.detail, /31 秒/);
    assert.equal(persistentWork.tone, "working");
    assert.equal(persistentWork.target, "guidedIntakePanel");

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
        workflowTitle: document.querySelector("#workflowNowTitle").textContent,
        workflowTarget: document.querySelector("#workflowNowAction").dataset.target,
      };
    });
    assert.notEqual(failureZh.display, "none");
    assert.equal(failureZh.failed, true);
    assert.equal(failureZh.title, "本次处理已停止");
    assert.match(failureZh.detail, /职位级别/);
    assert.equal(failureZh.busy, false);
    assert.equal(failureZh.workflowTitle, "本次处理已停止");
    assert.equal(failureZh.workflowTarget, "guidedIntakePanel");

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
      persistent_primary_workflow: true,
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
