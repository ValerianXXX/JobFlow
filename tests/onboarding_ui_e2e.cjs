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
    assert.match(initial.scriptVersion, /20260906-jobflow-v44-accessible-controls/);
    assert.match(initial.styleVersion, /20260906-jobflow-v44-accessible-controls/);
    assert.equal(initial.skipHref, "#mainContent");
    assert.equal(initial.mainTabIndex, "-1");
    assert.equal(initial.supportButtonType, "button");
    assert.equal(initial.supportLiveRegion, "polite");
    assert.equal(initial.updateButtonType, "button");
    assert.equal(initial.updateLiveRegion, "polite");
    assert.equal(initial.aiHeadingTabIndex, "-1");

    const companionVersions = await page.evaluate(async () => {
      const originalMessage = companionExternalMessage;
      const originalAvailability = state.companionAvailability;
      const originalLocale = state.locale;
      const rows = [];
      const messages = [];
      try {
        for (const [version, protocol] of [
          ["0.9.2", 2], ["0.9.1", 2], ["0.9.3", 2], ["0.10.0", 2],
          ["1.0.0", 2], ["0.9.2.0", 2], ["0.9.2", 3], ["private-version", 2],
        ]) {
          const response = {status: "AVAILABLE", protocol_version: protocol, extension_version: version};
          companionExternalMessage = async message => { messages.push(message.type); return response; };
          state.locale = "en";
          let error = null;
          try { await requireCurrentCompanion(); } catch (caught) { error = caught; }
          const info = {...state.companionAvailability};
          const en = error ? localizedErrorMessage(error) : "";
          const title = t(companionAvailabilityKey());
          state.companionAvailability = {status: "AVAILABLE", extension_version: COMPANION_VERSION};
          const pairedError = companionVersionCurrent(response) ? null : pairingError(response);
          const pairingStatus = state.companionAvailability.status;
          state.companionAvailability = info;
          state.locale = "zh";
          rows.push({
            version, protocol, ...info, code: error?.code || null, en, title,
            zh: error ? localizedErrorMessage(error) : "",
            pairingTarget: pairedError?.details?.update_target || null,
            pairingStatus,
          });
        }
        return {rows, messages};
      } finally {
        companionExternalMessage = originalMessage;
        state.companionAvailability = originalAvailability;
        state.locale = originalLocale;
      }
    });
    assert.equal(companionVersions.rows[0].status, "AVAILABLE");
    assert.equal(companionVersions.rows[0].code, null);
    assert.equal(companionVersions.rows[1].update_target, "EXTENSION");
    assert.match(companionVersions.rows[1].en, /Browser Companion is older/);
    for (const row of companionVersions.rows.slice(2, 5)) {
      assert.equal(row.update_target, "DESKTOP");
      assert.equal(row.pairingTarget, "DESKTOP");
      assert.match(row.en, /newer than this JobFlow desktop/);
      assert.match(row.zh, /比此 JobFlow 桌面版更新/);
      assert.match(row.title, /Update JobFlow desktop/);
    }
    for (const row of companionVersions.rows.slice(5)) {
      assert.equal(row.update_target, "BOTH");
      assert.match(row.en, /incompatible versions or protocols/);
    }
    for (const row of companionVersions.rows.slice(1)) {
      assert.equal(row.status, "UPDATE_REQUIRED");
      assert.equal(row.code, "BROWSER_COMPANION_UPDATE_REQUIRED");
      assert.equal(row.pairingStatus, "UPDATE_REQUIRED");
    }
    assert.equal(companionVersions.rows.at(-1).extension_version, null);
    assert.equal(companionVersions.messages.every(type => type === "JOBFLOW_PING"), true);

    const authorizedDiscovery = await page.evaluate(() => {
      state.locale = "en";
      state.guidedIntakeSession = null;
      state.browserAssistSession = null;
      state.data = {
        demo_mode: false,
        browser_assist: {},
        guided_intake: {active: false},
        ai_engine: {status: "READY", available: true},
        authorized_discovery: {
          control: {
            status: "READY", configured: true, enabled: true, paused: false,
            generation: 3, interval_minutes: 360, max_new_per_run: 20, inbox_limit: 250,
            source_count: 2, task_registration_state: "REGISTERED",
            next_run_at: "2026-08-20T00:00:00Z", authorized_until: "2026-08-21T00:00:00Z",
          },
          inbox: {
            counts: {NEW: 1, QUEUED: 0, IGNORED: 0},
            candidates: [{
              candidate_id: "ADC-SYNTHETIC01", status: "NEW", title: "Risk Analyst",
              location: "Remote", company_domain: "example.test", provider: "company",
              official_url: "https://careers.example.test/jobs/risk-analyst", discovered_at: "2026-08-19T00:00:00Z",
            }],
          },
        },
        dashboard: {
          queue: {}, safety: {knowledge_write_operations: 0, submit_capability: false, automatic_retry: false},
          pending_applications: [], deferred_intake: [], recent_applications: [], execution_runs: [],
          intake_control: {status: "NOT_CONFIGURED", configured: false, manual_run_allowed: false, paused: false},
        },
      };
      renderDashboard();
      return {
        hidden: document.querySelector("#authorizedDiscoveryPanel").classList.contains("hidden"),
        status: document.querySelector("#authorizedDiscoveryStatus").textContent,
        count: document.querySelector("#discoveryInboxCount").textContent,
        title: document.querySelector(".discovery-candidate strong")?.textContent,
        href: document.querySelector(".discovery-candidate a")?.getAttribute("href"),
        processDisabled: document.querySelector(".discovery-candidate-start")?.disabled,
        pauseHidden: document.querySelector("#pauseAuthorizedDiscovery").classList.contains("hidden"),
        resumeHidden: document.querySelector("#resumeAuthorizedDiscovery").classList.contains("hidden"),
      };
    });
    assert.equal(authorizedDiscovery.hidden, false);
    assert.equal(authorizedDiscovery.status, "Background discovery enabled");
    assert.equal(authorizedDiscovery.count, "1");
    assert.equal(authorizedDiscovery.title, "Risk Analyst");
    assert.equal(authorizedDiscovery.href, "https://careers.example.test/jobs/risk-analyst");
    assert.equal(authorizedDiscovery.processDisabled, false);
    assert.equal(authorizedDiscovery.pauseHidden, false);
    assert.equal(authorizedDiscovery.resumeHidden, true);

    await page.keyboard.press("Tab");
    assert.equal(await page.evaluate(() => document.activeElement?.classList.contains("skip-link")), true);

    const discoveryInteractions = await page.evaluate(async () => {
      state.locale = "en";
      state.serviceCompatible = true;
      state.guidedIntakeSession = null;
      state.browserAssistSession = null;
      state.data.application_readiness = {status: "READY_FOR_OFFLINE_APPLICATION_PREPARATION"};
      const calls = [];
      api = async (requestPath, options = {}) => {
        const parsedBody = options.body ? JSON.parse(options.body) : null;
        calls.push({requestPath, method: options.method || "GET", body: parsedBody});
        if (requestPath === "authorized-discovery-candidate" && parsedBody?.action === "START_GUIDED_INTAKE") {
          return {guided_intake: {
            intake_id: "GDI-SYNTHETIC01", intake_path: "/api/guided-intake/GDI-SYNTHETIC01",
            protocol_version: 1, expires_at: new Date(Date.now() + 600000).toISOString(), status: "PAIRING",
          }};
        }
        return {status: "OK"};
      };
      refreshLatest = async () => {};
      requireCurrentCompanion = async () => ({available: true});
      beginCompanionPairing = async () => {};
      window.confirm = () => true;

      document.querySelector("#discoveryCareerUrls").value = "https://careers.example.test/jobs";
      document.querySelector("#discoveryRoleTerms").value = "risk analyst";
      document.querySelector("#discoveryLocationTerms").value = "remote";
      document.querySelector("#discoveryExcludeTerms").value = "director";
      document.querySelector("#discoveryIntervalMinutes").value = "360";
      document.querySelector("#discoveryAuthorizationHours").value = "24";
      document.querySelector("#discoveryMaxNewPerRun").value = "20";
      document.querySelector("#discoveryInboxLimit").value = "250";
      document.querySelector("#authorizedDiscoveryConfirm").checked = true;
      document.querySelector("#configureAuthorizedDiscovery").click();
      await new Promise(resolve => setTimeout(resolve, 0));

      document.querySelector("#pauseAuthorizedDiscovery").click();
      await new Promise(resolve => setTimeout(resolve, 0));

      state.data.authorized_discovery.control.status = "PAUSED";
      renderAuthorizedDiscovery();
      document.querySelector("#resumeAuthorizedDiscovery").click();
      await new Promise(resolve => setTimeout(resolve, 0));

      state.data.authorized_discovery.control.status = "READY";
      state.data.authorized_discovery.control.task_registration_state = "REGISTRATION_REQUIRED";
      renderAuthorizedDiscovery();
      document.querySelector("#retryDiscoveryRegistration").click();
      await new Promise(resolve => setTimeout(resolve, 0));

      state.data.authorized_discovery.control.task_registration_state = "REGISTERED";
      renderAuthorizedDiscovery();
      document.querySelector(".discovery-candidate-ignore").click();
      await new Promise(resolve => setTimeout(resolve, 0));

      state.data.authorized_discovery.inbox.candidates[0].status = "IGNORED";
      renderAuthorizedDiscovery();
      document.querySelector(".discovery-candidate-restore").click();
      await new Promise(resolve => setTimeout(resolve, 0));

      state.data.authorized_discovery.inbox.candidates[0].status = "NEW";
      renderAuthorizedDiscovery();
      document.querySelector(".discovery-candidate-start").click();
      await new Promise(resolve => setTimeout(resolve, 0));

      state.guidedIntakeSession = null;
      state.data.authorized_discovery.control.status = "READY";
      renderAuthorizedDiscovery();
      document.querySelector("#killAuthorizedDiscovery").click();
      await new Promise(resolve => setTimeout(resolve, 0));

      return {
        calls,
        localized: {
          network: localizedErrorMessage({code: "DISCOVERY_NETWORK_HOST_BLOCKED"}),
          changed: localizedErrorMessage({code: "DISCOVERY_CANDIDATE_TRANSITION_INVALID"}),
          repeated: localizedErrorMessage({code: "DISCOVERY_REPEATED_FAILURES"}),
        },
      };
    });
    assert.deepEqual(discoveryInteractions.calls.map(item => [item.requestPath, item.body?.action || null]), [
      ["authorized-discovery-configure", null],
      ["authorized-discovery-control", "PAUSE"],
      ["authorized-discovery-control", "RESUME"],
      ["authorized-discovery-control", "RETRY_REGISTRATION"],
      ["authorized-discovery-candidate", "IGNORE"],
      ["authorized-discovery-candidate", "RESTORE"],
      ["authorized-discovery-candidate", "START_GUIDED_INTAKE"],
      ["authorized-discovery-control", "KILL"],
    ]);
    assert.match(discoveryInteractions.localized.network, /public HTTPS careers page/);
    assert.match(discoveryInteractions.localized.changed, /changed state/);
    assert.match(discoveryInteractions.localized.repeated, /paused after repeated read failures/);

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

    // Exercise real keyboard and pointer dispatch, not only CSS assertions.
    // All API effects stay in memory; no applicant data or network is used.
    const accessPage = await browser.newPage({reducedMotion:"reduce"});
    await accessPage.route("**/*", route => {
      if (route.request().url().startsWith("file:")) return route.continue();
      nonLocalRequests += 1;
      return route.abort();
    });
    await accessPage.goto(pathToFileURL(html).href, {waitUntil:"load"});
    await accessPage.waitForFunction(() => state.activities.length === 0);
    const motionImmediate = await accessPage.evaluate(() => {
      navigate("review");
      return {reduce:matchMedia("(prefers-reduced-motion: reduce)").matches, scrollY};
    });
    await accessPage.waitForTimeout(150);
    assert.equal(motionImmediate.reduce, true);
    assert.ok(motionImmediate.scrollY > 0);
    assert.equal(await accessPage.evaluate(() => scrollY), motionImmediate.scrollY,
      "reduced-motion navigation must finish immediately, not animate after the call");

    await accessPage.evaluate(() => {
      window.accessAudit = {saveCalls:0, stopCalls:0, dynamicClicks:0};
      state.data = {completion:{remaining:1, remaining_fields:[]}};
      api = async requestPath => {
        if (requestPath === "save") {
          window.accessAudit.saveCalls += 1;
          await new Promise(resolve => {window.accessAudit.releaseSave = resolve;});
          return {status:"OK"};
        }
        if (requestPath === "external-action-kill-switch") {
          window.accessAudit.stopCalls += 1;
          await new Promise(resolve => {window.accessAudit.releaseStop = resolve;});
          return {status:"OK"};
        }
        throw new Error("Unexpected request in isolated accessibility fixture");
      };
      refresh = async () => {};
      refreshLatest = async () => {};
      window.confirm = () => true;
      document.querySelector("#pipelineDashboard").classList.remove("hidden");
      for (const id of Object.keys(BUSY_INTERRUPTION_ACTIONS)) {
        document.getElementById(id).disabled = false;
      }
      const preexisting = document.createElement("button");
      preexisting.id = "preexistingInertFixture";
      preexisting.inert = true;
      document.body.append(preexisting);
      navigate("questionnaire");
      document.querySelector("#saveAnswers").focus();
    });
    await accessPage.keyboard.press("Enter");
    await accessPage.waitForFunction(() => window.accessAudit.saveCalls === 1);
    assert.equal(await accessPage.locator("#saveAnswers").evaluate(el => el.inert), true);
    await accessPage.keyboard.press("Enter");
    await accessPage.locator("#saveAnswers").evaluate(el => el.click());
    assert.equal(await accessPage.evaluate(() => window.accessAudit.saveCalls), 1,
      "busy keyboard and programmatic activation must not duplicate a save request");

    await accessPage.evaluate(() => {
      const dynamic = document.createElement("button");
      dynamic.id = "dynamicBusyFixture";
      dynamic.textContent = "Synthetic dynamic action";
      dynamic.addEventListener("click", () => {window.accessAudit.dynamicClicks += 1;});
      document.body.append(dynamic);
    });
    await accessPage.waitForFunction(() => document.querySelector("#dynamicBusyFixture").inert);
    await accessPage.locator("#dynamicBusyFixture").evaluate(el => el.click());
    assert.equal(await accessPage.evaluate(() => window.accessAudit.dynamicClicks), 0);
    const stopControls = await accessPage.evaluate(() => Object.keys(BUSY_INTERRUPTION_ACTIONS).map(id => {
      const control = document.getElementById(id);
      return {id, inert:control.inert, pointer:getComputedStyle(control).pointerEvents};
    }));
    for (const control of stopControls) {
      assert.equal(control.inert, false, `${control.id} must remain a reachable interruption control`);
      assert.equal(control.pointer, "auto", `${control.id} must receive pointer input while busy`);
    }
    await accessPage.locator("#emergencyStop").click();
    await accessPage.waitForFunction(() => window.accessAudit.stopCalls === 1);
    assert.equal(await accessPage.locator("#emergencyStop").evaluate(el => el.inert), true,
      "a pending stop request must not be sent twice");
    await accessPage.keyboard.press("Enter");
    await accessPage.locator("#emergencyStop").evaluate(el => el.click());
    assert.equal(await accessPage.evaluate(() => window.accessAudit.stopCalls), 1);
    await accessPage.evaluate(() => window.accessAudit.releaseStop());
    await accessPage.waitForFunction(() => !state.activities.some(item => item.key === "stoppingExternalActions"));
    assert.equal(await accessPage.locator("#emergencyStop").evaluate(el => el.inert), false);
    assert.equal(await accessPage.locator("#saveAnswers").evaluate(el => el.inert), true);
    await accessPage.evaluate(() => window.accessAudit.releaseSave());
    await accessPage.waitForFunction(() => state.activities.length === 0);
    const restored = await accessPage.evaluate(() => ({
      saveInert:document.querySelector("#saveAnswers").inert,
      dynamicInert:document.querySelector("#dynamicBusyFixture").inert,
      originalInert:document.querySelector("#preexistingInertFixture").inert,
      disabledPreserved:document.querySelector("#startGuidedIntake").disabled,
      focus:document.activeElement?.id,
    }));
    assert.equal(restored.saveInert, false);
    assert.equal(restored.dynamicInert, false);
    assert.equal(restored.originalInert, true);
    assert.equal(restored.disabledPreserved, true);
    assert.equal(restored.focus, "saveAnswers");
    await accessPage.locator("#dynamicBusyFixture").evaluate(el => el.click());
    assert.equal(await accessPage.evaluate(() => window.accessAudit.dynamicClicks), 1);
    await accessPage.emulateMedia({reducedMotion:"no-preference"});
    assert.equal(await accessPage.evaluate(() => {
      let behavior = null;
      scrollToElement({scrollIntoView(options) {behavior = options.behavior;}}, {block:"center"});
      return behavior;
    }), "smooth");
    await accessPage.close();
    const visualReview = await page.evaluate(() => {
      state.locale = "en";
      state.data.pending_sources = [{source_id:"SRC-VISUAL-SYNTHETIC", metadata:{safe_display_name:"Synthetic image"},
        extraction_summary:{visual_verification_required:true, visual_regions:[{page:1,line_start:1,bbox:[0,0,1,1]}]},
        candidates:[{candidate_id:"VISUAL-C1",statement:"Built a synthetic dashboard for 12 stores.",category:"project",selected:false}]}];
      renderSourcePreviews();
      const initial = previewSelections("SRC-VISUAL-SYNTHETIC", true)[0];
      document.querySelector(".visual-source-confirm").checked = true;
      const confirmed = previewSelections("SRC-VISUAL-SYNTHETIC")[0];
      return {initial, confirmed, visible:document.querySelector("#sourcePreviewList").textContent.includes("page 1, line 1")};
    });
    assert.equal(visualReview.initial.selected, true);
    assert.equal(visualReview.initial.visual_source_verified, false);
    assert.equal(visualReview.confirmed.visual_source_verified, true);
    assert.equal(visualReview.visible, true);
    assert.equal(nonLocalRequests, 0);

    process.stdout.write(JSON.stringify({
      status: "PASS",
      materials_before_application_console: true,
      persistent_failure_indicator: true,
      persistent_primary_workflow: true,
      authorized_discovery_inbox: true,
      companion_version_update_direction: true,
      incompatible_companion_pairing_blocked: true,
      busy_keyboard_reentrancy_blocked: true,
      busy_stop_controls_reachable: true,
      busy_control_state_restored: true,
      reduced_motion_navigation: true,
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
