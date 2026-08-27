"use strict";

const assert = require("node:assert/strict");
const {createHash, webcrypto} = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const project = path.resolve(__dirname, "..");
const source = fs.readFileSync(path.join(project, "browser-companion", "service-worker.js"), "utf8");
const listeners = {};
const session = {};
const requests = [];
const domMessages = [];
const oldDocumentInstance = `DOC-${"A".repeat(32)}`;
const nextDocumentInstance = `DOC-${"B".repeat(32)}`;
const oldBrowserDocument = "chrome-document-old";
const nextBrowserDocument = "chrome-document-next";
let currentBrowserDocument = oldBrowserDocument;
let currentCollected = collected("https://apply.example.test/step-1", oldDocumentInstance, "old-form");
let prepareMode = "manual";
let resumeFailureCode = null;
let armStateWasUncommitted = false;
let armFailureMode = null;
let dynamicReviewMode = false;
let applyFailureMode = false;

function event(name) { return {addListener(listener) { listeners[name] = listener; }}; }
function digest(value) { return `sha256:${createHash("sha256").update(value).digest("hex")}`; }
function clone(value) { return JSON.parse(JSON.stringify(value)); }

function collected(url, documentInstanceId, marker) {
  return {
    status: "COLLECTED",
    payload: {
      url, sanitized_html: `<form data-marker=${marker}><input name=city><button type=submit>Next</button></form>`,
      client_refs: ["field-1", "nav-1"], document_instance_id: documentInstanceId,
      blocker_signals: []
    }
  };
}

function baseState() {
  return {
    generation: "synthetic-generation", revision: 1, protocol_version: 2,
    base_url: "http://127.0.0.1:43123", assist_path: `/assist/${"a".repeat(54)}`,
    jobflow_tab_id: 11, mode: "APPLICATION_ASSIST", assist_id: "BAS-MANUAL",
    application_id: "APP-MANUAL", allowed_page_origin: "https://apply.example.test",
    provider: "workday", current_step: 1, max_steps: 20,
    expires_at: "2099-01-01T00:00:00Z", paired: true, stage: "READY", tab_id: 42,
    navigation: null, manual_field_count: 0, submission_observed: false, result_final: false
  };
}

function challenge() {
  const value = {
    challenge_id: `MNC-${"C".repeat(32)}`, nonce: "synthetic-one-use-manual-navigation-nonce-0001",
    issued_at: new Date(Date.now() - 1000).toISOString(),
    expires_at: new Date(Date.now() + 120000).toISOString(),
    assist_id: "BAS-MANUAL", application_id: "APP-MANUAL", tab_id: 42,
    document_instance_id: oldDocumentInstance, stage: "MANUAL_NAVIGATION_REQUIRED",
    client_ref: "nav-1", prior_page_content_hash: digest(currentCollected.payload.sanitized_html),
    control_semantics_hash: `sha256:${"2".repeat(64)}`
  };
  value.challenge_hash = digest(JSON.stringify(value));
  return value;
}

function manualNavigation() {
  const value = challenge();
  return {
    client_ref: "nav-1", mode: "MANUAL_USER_CLICK", control_type: "submit",
    prior_page_content_hash: value.prior_page_content_hash,
    control_semantics_hash: value.control_semantics_hash,
    programmatic_allowed: false, user_must_click: true, resume_after_changed_page: true,
    challenge: value
  };
}

function evidencePayload(manual, overrides = {}) {
  const item = manual.challenge;
  return {
    url: "https://apply.example.test/step-1", trusted_user_event: true,
    event_hash: digest(JSON.stringify([
      "MANUAL_FORWARD_CONTROL_CLICK", item.challenge_id, item.nonce, item.assist_id,
      item.application_id, item.tab_id, item.document_instance_id,
      "MANUAL_NAVIGATION_REQUIRED", item.prior_page_content_hash,
      item.control_semantics_hash, item.client_ref, false
    ])),
    prior_page_content_hash: item.prior_page_content_hash,
    control_semantics_hash: item.control_semantics_hash,
    manual_navigation_challenge_id: item.challenge_id,
    manual_navigation_nonce: item.nonce,
    manual_navigation_challenge_hash: item.challenge_hash,
    manual_navigation_assist_id: item.assist_id,
    manual_navigation_application_id: item.application_id,
    manual_navigation_tab_id: item.tab_id,
    manual_navigation_document_id: item.document_instance_id,
    manual_navigation_stage: item.stage,
    manual_navigation_client_ref: item.client_ref,
    manual_navigation_default_prevented: false,
    ...overrides
  };
}

const chrome = {
  runtime: {
    getManifest() { return {version: "0.9.2"}; },
    getURL(value) { return `chrome-extension://hhlliaaafegldkmcgmaoaelabipcaooj/${value}`; },
    onMessage: event("internal"), onMessageExternal: event("external"), onConnect: event("connect")
  },
  storage: {session: {
    async get(key) { return {[key]: session[key]}; },
    async set(value) { Object.assign(session, value); },
    async remove(key) { delete session[key]; }
  }},
  tabs: {
    async query() { return []; },
    async sendMessage(tabId, message) {
      domMessages.push({tabId, message});
      if (message.type === "JOBFLOW_COLLECT_FORM") return currentCollected;
      if (message.type === "JOBFLOW_APPLY_APPROVED") {
        if (applyFailureMode) return {
          status: "BLOCKED", code: "COMPANION_CONTROL_REBIND_FAILED",
          client_ref: "field-1",
          field_bindings: [], material_bindings: [],
          attempted_field_bindings: [{client_ref: "field-1", value_sha256: digest("synthetic-value")}],
          attempted_material_bindings: [], partial_effects: true
        };
        return {status: "APPLIED", field_bindings: [], material_bindings: []};
      }
      if (message.type === "JOBFLOW_ARM_MANUAL_NAVIGATION") {
        armStateWasUncommitted = session.jobflowAssist.stage !== "MANUAL_NAVIGATION_REQUIRED";
        if (armFailureMode === "page-changed") return {
          status: "BLOCKED", code: "COMPANION_MANUAL_CHALLENGE_CONTEXT_CHANGED"
        };
        return {
          status: "MANUAL_NAVIGATION_ARMED", challenge_id: message.challenge.challenge_id,
          document_instance_id: message.challenge.document_instance_id,
          expires_at: message.challenge.expires_at
        };
      }
      if (message.type === "JOBFLOW_CHECK_NAVIGATION") return {
        status: "NAVIGATION_VALID", form_valid: true,
        page_content_hash: `sha256:${"3".repeat(64)}`,
        control_semantics_hash: `sha256:${"4".repeat(64)}`
      };
      if (message.type === "JOBFLOW_NAVIGATE_APPROVED") {
        return {status: "NAVIGATION_STARTED", final_submit: false};
      }
      return {status: "IGNORED"};
    },
    async get() { return null; }, onUpdated: event("tabUpdated")
  },
  scripting: {async executeScript() { return [{frameId: 0, documentId: currentBrowserDocument}]; }},
  alarms: {create() {}, onAlarm: event("alarm")}
};

const sandbox = {
  chrome, crypto: webcrypto, URL, TextEncoder, AbortController, console, setTimeout, clearTimeout,
  btoa: (value) => Buffer.from(value, "binary").toString("base64"),
  atob: (value) => Buffer.from(value, "base64").toString("binary"),
  fetch: async (url, options) => {
    const body = JSON.parse(String(options?.body || "{}"));
    requests.push({url: String(url), body});
    if (String(url).endsWith("/resume-manual-navigation")) {
      if (resumeFailureCode) return {ok: false, async json() { return {
        status: "BLOCKED", code: resumeFailureCode, automatic_retry: false
      }; }};
      prepareMode = "final";
      return {ok: true, async json() { return {status: "NEXT_PAGE_READY", current_step: 2, max_steps: 20}; }};
    }
    if (String(url).endsWith("/prepare")) return {ok: true, async json() { return {
      status: "LIVE_PAGE_APPROVED_FOR_ASSIST", fields: [], files: [], manual_field_count: 0,
      navigation: prepareMode === "manual" ? {
        client_ref: "nav-1", mode: "MANUAL_USER_CLICK", control_type: "submit",
        page_content_hash: challenge().prior_page_content_hash,
        control_semantics_hash: challenge().control_semantics_hash, display_label: "Next"
      } : null,
      final_submit_client_refs: prepareMode === "manual" ? [] : ["nav-1"]
    }; }};
    if (String(url).endsWith("/discover-dynamic-fields")) return {ok: true, async json() { return dynamicReviewMode ? {
      status: "SUPPLEMENTAL_REVIEW_REQUIRED", dynamic_field_count: 4,
      packet_version: 3, real_external_actions: 2, automatic_retry: false
    } : {
      status: "NO_DYNAMIC_FIELDS", dynamic_field_count: 0, real_external_actions: 0
    }; }};
    if (String(url).endsWith("/complete")) return {ok: true, async json() { return prepareMode === "manual" ? {
      status: "MANUAL_NAVIGATION_REQUIRED", current_step: 1, manual_field_count: 0,
      manual_navigation: manualNavigation(), automatic_retry: false
    } : {
      status: "AWAITING_USER_SUBMIT", current_step: 2, manual_field_count: 0,
      final_submit: false, submit_capability: "USER_ONLY"
    }; }};
    if (String(url).endsWith("/abort-page-apply")) return {ok: true, async json() { return {
      status: "APPLY_RESTART_REQUIRED", code: "COMPANION_APPLY_RESTART_REQUIRED",
      failure_code: body.cause_code,
      failure_control_type: "combobox",
      failure_page_position: 7,
      failure_field_label: "Phone Type",
      field_attempt_count: body.attempted_field_bindings.length,
      file_attempt_count: body.attempted_material_bindings.length,
      real_external_actions: body.attempted_field_bindings.length ? 1 : 0,
      submit_capability: false, final_submit: false, automatic_retry: false
    }; }};
    if (String(url).endsWith("/authorize-navigation")) return {ok: true, async json() { return {
      status: "NAVIGATION_AUTHORIZED", page_content_hash: body.page_content_hash,
      control_semantics_hash: body.control_semantics_hash
    }; }};
    throw new Error(`Unexpected URL: ${url}`);
  }
};
vm.runInNewContext(source, sandbox, {filename: "service-worker.js"});

function send(listener, message, sender = {}) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("Listener response timed out")), 3000);
    const keepAlive = listener(message, sender, (result) => { clearTimeout(timer); resolve(result); });
    assert.equal(keepAlive, true);
  });
}
function internal(message, sender = {}) { return send(listeners.internal, message, sender); }
function manualSender(overrides = {}) {
  return {tab: {id: 42, url: "https://apply.example.test/step-1"}, documentId: oldBrowserDocument, ...overrides};
}
async function eventually(predicate, timeoutMs = 5000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  throw new Error("Timed out waiting for the browser companion state transition.");
}

(async () => {
  session.jobflowAssist = {...baseState(), tab_id: null};
  const armedResult = await internal({
    type: "JOBFLOW_FILL_CURRENT", tab_id: 42, tab_url: "https://apply.example.test/step-1"
  });
  assert.equal(armedResult.status, "MANUAL_NAVIGATION_REQUIRED");
  assert.equal(session.jobflowAssist.tab_id, 42, "the first approved application tab must be bound before writes");
  assert.equal(armStateWasUncommitted, true, "the manual stage must be saved only after the DOM arms it");
  assert.equal(session.jobflowAssist.stage, "MANUAL_NAVIGATION_REQUIRED");
  assert.equal(session.jobflowAssist.manual_navigation_browser_document_id, oldBrowserDocument);
  const prepareRequest = requests.find((item) => item.url.endsWith("/prepare"));
  assert.equal(prepareRequest.body.companion_tab_id, 42);
  assert.equal(prepareRequest.body.document_instance_id, oldDocumentInstance);
  const armedState = clone(session.jobflowAssist);
  const manual = armedState.manual_navigation;
  const validEvidence = evidencePayload(manual);

  const forgedNonce = await internal(
    {type: "JOBFLOW_MANUAL_NAVIGATION_OBSERVED", payload: {...validEvidence, manual_navigation_nonce: "forged-nonce"}},
    manualSender()
  );
  assert.equal(forgedNonce.code, "COMPANION_MANUAL_NAVIGATION_EVIDENCE_INVALID");
  assert.equal(session.jobflowAssist.manual_navigation_evidence, null);

  const wrongTab = await internal(
    {type: "JOBFLOW_MANUAL_NAVIGATION_OBSERVED", payload: validEvidence},
    {tab: {id: 43, url: "https://apply.example.test/step-1"}, documentId: oldBrowserDocument}
  );
  assert.equal(wrongTab.code, "COMPANION_MANUAL_NAVIGATION_BINDING_INVALID");
  const wrongDocument = await internal(
    {type: "JOBFLOW_MANUAL_NAVIGATION_OBSERVED", payload: validEvidence},
    manualSender({documentId: "chrome-document-wrong"})
  );
  assert.equal(wrongDocument.code, "COMPANION_MANUAL_NAVIGATION_EVIDENCE_INVALID");
  const prevented = await internal(
    {type: "JOBFLOW_MANUAL_NAVIGATION_OBSERVED", payload: {...validEvidence, manual_navigation_default_prevented: true}},
    manualSender()
  );
  assert.equal(prevented.code, "COMPANION_MANUAL_NAVIGATION_EVIDENCE_INVALID");

  const immediateUnload = await internal(
    {type: "JOBFLOW_MANUAL_NAVIGATION_OBSERVED", payload: validEvidence},
    {tab: {id: 42, url: "https://apply.example.test/step-2"}, documentId: oldBrowserDocument}
  );
  assert.equal(immediateUnload.status, "MANUAL_NAVIGATION_RECORDED");
  const replay = await internal(
    {type: "JOBFLOW_MANUAL_NAVIGATION_OBSERVED", payload: validEvidence}, manualSender()
  );
  assert.equal(replay.code, "COMPANION_MANUAL_NAVIGATION_REPLAYED");
  const publicStatus = await send(listeners.external, {
    type: "JOBFLOW_GET_STATUS",
    binding: {base_url: armedState.base_url, assist_path: armedState.assist_path}
  }, {url: `${armedState.base_url}/session/synthetic/`});
  assert.equal(JSON.stringify(publicStatus).includes(manual.challenge.nonce), false);
  assert.equal(JSON.stringify(publicStatus).includes("challenge"), false);

  currentCollected = collected("https://apply.example.test/advanced", oldDocumentInstance, "spa-new-form");
  currentBrowserDocument = oldBrowserDocument;
  listeners.tabUpdated(42, {status: "complete"}, {
    id: 42, status: "complete", url: "https://apply.example.test/advanced"
  });
  await eventually(() => session.jobflowAssist?.stage === "AWAITING_USER_SUBMIT");
  assert.equal(session.jobflowAssist.stage, "AWAITING_USER_SUBMIT");
  const resumedBody = requests.filter((item) => item.url.endsWith("/resume-manual-navigation")).at(-1).body;
  assert.equal(resumedBody.url, "https://apply.example.test/advanced", "the new page URL must not be overwritten by old evidence");
  assert.equal(resumedBody.companion_tab_id, 42);
  assert.equal(resumedBody.manual_navigation_nonce, manual.challenge.nonce);
  assert.equal(Object.values(resumedBody).includes("https://apply.example.test/step-1"), false);

  session.jobflowAssist = clone(armedState);
  currentCollected = collected("https://apply.example.test/unrelated", nextDocumentInstance, "unrelated-role");
  currentBrowserDocument = nextBrowserDocument;
  await internal({type: "JOBFLOW_MANUAL_NAVIGATION_OBSERVED", payload: validEvidence}, manualSender());
  resumeFailureCode = "FORM_ROUTE_IDENTITY_CHANGED";
  const unrelated = await internal({
    type: "JOBFLOW_FILL_CURRENT", tab_id: 42, tab_url: "https://apply.example.test/unrelated"
  });
  assert.equal(unrelated.code, "FORM_ROUTE_IDENTITY_CHANGED");
  assert.equal(session.jobflowAssist.manual_navigation_resume_failed, true);
  const resumeRequestsAfterFailure = requests.filter((item) => item.url.endsWith("/resume-manual-navigation")).length;
  const noRetry = await internal({
    type: "JOBFLOW_FILL_CURRENT", tab_id: 42, tab_url: "https://apply.example.test/unrelated"
  });
  assert.equal(noRetry.code, "COMPANION_MANUAL_NAVIGATION_RESTART_REQUIRED");
  assert.equal(requests.filter((item) => item.url.endsWith("/resume-manual-navigation")).length, resumeRequestsAfterFailure);
  resumeFailureCode = null;

  session.jobflowAssist = clone(armedState);
  session.jobflowAssist.manual_navigation.challenge.expires_at = new Date(Date.now() - 1000).toISOString();
  const expired = await internal(
    {type: "JOBFLOW_MANUAL_NAVIGATION_OBSERVED", payload: evidencePayload(session.jobflowAssist.manual_navigation)},
    manualSender()
  );
  assert.equal(expired.code, "COMPANION_MANUAL_NAVIGATION_EVIDENCE_INVALID");

  session.jobflowAssist = {...baseState(), revision: 30};
  prepareMode = "manual";
  currentCollected = collected("https://apply.example.test/step-1", oldDocumentInstance, "old-form");
  currentBrowserDocument = oldBrowserDocument;
  armFailureMode = "page-changed";
  const completeCountBeforeArmFailure = requests.filter((item) => item.url.endsWith("/complete")).length;
  const armFailed = await internal({
    type: "JOBFLOW_FILL_CURRENT", tab_id: 42, tab_url: "https://apply.example.test/step-1"
  });
  assert.equal(armFailed.code, "COMPANION_MANUAL_NAVIGATION_RESTART_REQUIRED");
  assert.equal(armFailed.automatic_retry, false);
  assert.equal(session.jobflowAssist.stage, "MANUAL_NAVIGATION_RESTART_REQUIRED");
  assert.equal(session.jobflowAssist.manual_navigation, null);
  assert.equal(session.jobflowAssist.manual_navigation_evidence, null);
  const armFailureStatus = await send(listeners.external, {
    type: "JOBFLOW_GET_STATUS",
    binding: {base_url: session.jobflowAssist.base_url, assist_path: session.jobflowAssist.assist_path}
  }, {url: `${session.jobflowAssist.base_url}/session/synthetic/`});
  assert.equal(armFailureStatus.status, "MANUAL_NAVIGATION_RESTART_REQUIRED");
  assert.equal(armFailureStatus.last_result.code, "COMPANION_MANUAL_NAVIGATION_RESTART_REQUIRED");
  assert.match(armFailureStatus.last_result.message, /Return to JobFlow/);
  assert.equal(JSON.stringify(armFailureStatus).includes("nonce"), false);
  assert.equal(JSON.stringify(armFailureStatus).includes("challenge"), false);
  const armMessagesAfterFailure = domMessages.filter((item) => item.message.type === "JOBFLOW_ARM_MANUAL_NAVIGATION").length;
  const armRetry = await internal({
    type: "JOBFLOW_FILL_CURRENT", tab_id: 42, tab_url: "https://apply.example.test/step-1"
  });
  assert.equal(armRetry.code, "COMPANION_MANUAL_NAVIGATION_RESTART_REQUIRED");
  assert.equal(requests.filter((item) => item.url.endsWith("/complete")).length, completeCountBeforeArmFailure + 1);
  assert.equal(domMessages.filter((item) => item.message.type === "JOBFLOW_ARM_MANUAL_NAVIGATION").length, armMessagesAfterFailure);
  armFailureMode = null;

  session.jobflowAssist = {
    ...baseState(), revision: 40, stage: "PAGE_REVIEW_REQUIRED",
    navigation: {client_ref: "explicit-next", authorization_token: "one-use-token"}
  };
  const automatic = await internal({type: "JOBFLOW_CONTINUE_CURRENT", tab_id: 42});
  assert.equal(automatic.status, "NAVIGATION_STARTED");
  const authorization = requests.filter((item) => item.url.endsWith("/authorize-navigation")).at(-1);
  const navigation = domMessages.filter((item) => item.message.type === "JOBFLOW_NAVIGATE_APPROVED").at(-1).message;
  assert.equal(navigation.page_content_hash, authorization.body.page_content_hash);
  assert.equal(navigation.control_semantics_hash, authorization.body.control_semantics_hash);

  session.jobflowAssist = {...baseState(), revision: 50};
  prepareMode = "final";
  dynamicReviewMode = true;
  currentCollected = collected("https://apply.example.test/step-1", oldDocumentInstance, "conditional-form");
  currentBrowserDocument = oldBrowserDocument;
  const completeCountBeforeDynamicReview = requests.filter((item) => item.url.endsWith("/complete")).length;
  const supplemental = await internal({
    type: "JOBFLOW_FILL_CURRENT", tab_id: 42, tab_url: "https://apply.example.test/step-1"
  });
  assert.equal(supplemental.status, "SUPPLEMENTAL_REVIEW_REQUIRED");
  assert.equal(supplemental.dynamic_field_count, 4);
  assert.equal(session.jobflowAssist.stage, "SUPPLEMENTAL_REVIEW_REQUIRED");
  assert.equal(
    requests.filter((item) => item.url.endsWith("/complete")).length,
    completeCountBeforeDynamicReview,
    "a newly revealed question must return to review before completion"
  );
  assert.equal(session.jobflowAssist.last_result.automatic_retry, false);
  dynamicReviewMode = false;

  session.jobflowAssist = {...baseState(), revision: 60};
  prepareMode = "final";
  applyFailureMode = true;
  currentCollected = collected("https://apply.example.test/step-1", oldDocumentInstance, "partial-apply");
  const partialApply = await internal({
    type: "JOBFLOW_FILL_CURRENT", tab_id: 42, tab_url: "https://apply.example.test/step-1"
  });
  assert.equal(partialApply.status, "APPLY_RESTART_REQUIRED");
  assert.equal(partialApply.code, "COMPANION_APPLY_RESTART_REQUIRED");
  assert.equal(partialApply.field_attempt_count, 1);
  assert.equal(partialApply.file_attempt_count, 0);
  assert.equal(partialApply.automatic_retry, false);
  assert.equal(session.jobflowAssist.stage, "APPLY_RESTART_REQUIRED");
  const abortRequest = requests.filter((item) => item.url.endsWith("/abort-page-apply")).at(-1);
  assert.equal(abortRequest.body.cause_code, "COMPANION_CONTROL_REBIND_FAILED");
  assert.equal(abortRequest.body.failed_client_ref, "field-1");
  assert.equal(abortRequest.body.attempted_field_bindings.length, 1);
  assert.equal(abortRequest.body.submit_events, 0);
  assert.equal(abortRequest.body.navigation_actions, 0);
  const abortCount = requests.filter((item) => item.url.endsWith("/abort-page-apply")).length;
  const partialRetry = await internal({
    type: "JOBFLOW_FILL_CURRENT", tab_id: 42, tab_url: "https://apply.example.test/step-1"
  });
  assert.equal(partialRetry.code, "COMPANION_APPLY_RESTART_REQUIRED");
  assert.equal(requests.filter((item) => item.url.endsWith("/abort-page-apply")).length, abortCount);
  const partialPublic = await send(listeners.external, {
    type: "JOBFLOW_GET_STATUS",
    binding: {base_url: session.jobflowAssist.base_url, assist_path: session.jobflowAssist.assist_path}
  }, {url: `${session.jobflowAssist.base_url}/session/synthetic/`});
  assert.equal(partialPublic.status, "APPLY_RESTART_REQUIRED");
  assert.equal(partialPublic.last_result.field_attempt_count, 1);
  assert.equal(partialPublic.last_result.failure_code, "COMPANION_CONTROL_REBIND_FAILED");
  assert.equal(partialPublic.last_result.failure_control_type, "combobox");
  assert.equal(partialPublic.last_result.failure_page_position, 7);
  assert.equal(partialPublic.last_result.failure_field_label, "Phone Type");
  assert.equal(JSON.stringify(partialPublic).includes("attempted_field_bindings"), false);
  applyFailureMode = false;

  session.jobflowAssist = {...baseState(), revision: 70, tab_id: 41, preferred_tab_id: 41};
  prepareMode = "final";
  currentCollected = collected("https://apply.example.test/step-1", oldDocumentInstance, "initial-handoff");
  currentBrowserDocument = oldBrowserDocument;
  const handedOff = await internal({
    type: "JOBFLOW_FILL_CURRENT", tab_id: 42, tab_url: "https://apply.example.test/step-1"
  });
  assert.equal(handedOff.status, "AWAITING_USER_SUBMIT");
  assert.equal(session.jobflowAssist.tab_id, 42);
  assert.equal(session.jobflowAssist.preferred_tab_id, 42);
  assert.equal(session.jobflowAssist.initial_tab_handoff_count, 1);

  session.jobflowAssist = {
    ...baseState(), revision: 80, tab_id: 41, preferred_tab_id: 41,
    stage: "PAGE_REVIEW_REQUIRED", navigation: {client_ref: "next-1"}
  };
  const lateHandoff = await internal({
    type: "JOBFLOW_FILL_CURRENT", tab_id: 42, tab_url: "https://apply.example.test/step-1"
  });
  assert.equal(lateHandoff.code, "COMPANION_TAB_BINDING_CHANGED");
  assert.equal(session.jobflowAssist.tab_id, 41);

  session.jobflowAssist = {...baseState(), revision: 90, tab_id: 41, preferred_tab_id: 41};
  const wrongOriginHandoff = await internal({
    type: "JOBFLOW_FILL_CURRENT", tab_id: 42, tab_url: "https://other.example.test/step-1"
  });
  assert.equal(wrongOriginHandoff.code, "COMPANION_WRONG_TAB");
  assert.equal(session.jobflowAssist.tab_id, 41);

  process.stdout.write(JSON.stringify({
    status: "PASS", arm_before_stage_commit: true, companion_tab_injected: true,
    forged_nonce_rejected: true, wrong_tab_rejected: true, wrong_document_rejected: true,
    prevented_default_rejected: true, replay_rejected: true, immediate_unload_recorded: true,
    automatic_resume_after_trusted_next: true, spa_resume_supported: true,
    unrelated_same_origin_rejected: true, no_automatic_retry: true,
    expired_challenge_rejected: true, public_nonce_redacted: true,
    arm_failure_restart_required: true, arm_failure_no_retry: true,
    explicit_button_fresh_proof_required: true, dynamic_review_before_complete: true,
    partial_apply_audited_once: true, partial_apply_no_retry: true,
    initial_tab_handoff: true, post_write_tab_handoff_blocked: true, wrong_origin_handoff_blocked: true,
    final_submit_user_only: true,
    real_external_actions: 0
  }));
})().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
