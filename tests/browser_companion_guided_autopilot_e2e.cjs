"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {webcrypto} = require("node:crypto");

const project = path.resolve(__dirname, "..");
const source = fs.readFileSync(path.join(project, "browser-companion", "service-worker.js"), "utf8");
const listeners = {};
const session = {};
const tabs = new Map();
const requests = [];
const domMessages = [];
const openedUrls = [];
const searched = [];
const notifications = [];
let nextTabId = 40;
let permissionsGranted = true;
let searchMode = "selected";
let captureJobCalls = 0;

function event(name) {
  const values = new Set();
  return {
    addListener(listener) { values.add(listener); listeners[name] = listener; },
    removeListener(listener) { values.delete(listener); if (listeners[name] === listener) delete listeners[name]; },
    emit(...args) { for (const listener of values) listener(...args); }
  };
}

const tabUpdated = event("tabUpdated");
const tabRemoved = event("tabRemoved");

function seedState(stage = "AWAITING_JOB_DISCOVERY") {
  session.jobflowAssist = {
    generation: `guided-${stage}-${Date.now()}`, revision: 1, protocol_version: 2,
    base_url: "http://127.0.0.1:43123", assist_path: `/intake/${"a".repeat(54)}`,
    jobflow_tab_id: 11, mode: "JOB_CAPTURE", intake_id: "GIN-GUIDED-AUTOPILOT",
    search_query: "site:careers.example.test credit risk analyst",
    discovery_mode: "OFFICIAL_COMPANY_SEARCH", allowed_company_domain: null,
    selected_official_url: null, automation_tab_id: null,
    expires_at: "2099-01-01T00:00:00Z", paired: true, stage,
    autopilot_in_progress: false, last_result: {status: stage}
  };
}

function response(value) {
  return {ok: true, async json() { return value; }};
}

const chrome = {
  runtime: {
    lastError: null,
    getManifest() { return {version: "0.8.0"}; },
    getURL(value) { return `chrome-extension://hhlliaaafegldkmcgmaoaelabipcaooj/${value}`; },
    onMessage: event("internal"), onMessageExternal: event("external"), onConnect: event("connect")
  },
  permissions: {
    async contains() { return permissionsGranted; }
  },
  search: {
    query({text, tabId}, callback) {
      searched.push({text, tabId});
      const tab = tabs.get(tabId);
      tab.url = `https://search.example.test/?q=${encodeURIComponent(text)}`;
      tab.status = "complete";
      callback();
    }
  },
  storage: {session: {
    async get(key) { return {[key]: session[key]}; },
    async set(value) { Object.assign(session, value); },
    async remove(key) { delete session[key]; }
  }},
  tabs: {
    async create({url, active}) {
      const tab = {id: ++nextTabId, url, active: Boolean(active), status: "complete"};
      tabs.set(tab.id, tab);
      openedUrls.push(url);
      return {...tab};
    },
    async update(tabId, {url, active}) {
      const tab = tabs.get(tabId);
      assert.ok(tab, `tab ${tabId} must exist`);
      tab.url = url;
      tab.active = Boolean(active);
      tab.status = "complete";
      openedUrls.push(url);
      return {...tab};
    },
    async get(tabId) {
      const tab = tabs.get(tabId);
      if (!tab) throw new Error(`missing tab ${tabId}`);
      return {...tab};
    },
    async query() { return [{id: 11, url: "http://127.0.0.1:43123/session/local/"}]; },
    async sendMessage(tabId, message) {
      const tab = tabs.get(tabId);
      domMessages.push({tabId, url: tab?.url, message});
      if (message.type === "JOBFLOW_COLLECT_SEARCH_RESULTS") return {
        status: "COLLECTED",
        payload: {
          search_origin: "https://search.example.test",
          results: [
            {title: "Credit Risk Analyst", snippet: "Official company career role", url: "https://careers.example.test/jobs/credit-risk"},
            {title: "Aggregator copy", snippet: "Untrusted copied listing", url: "https://jobs.example.invalid/copied-role"}
          ]
        }
      };
      if (message.type === "JOBFLOW_COLLECT_JOB_PAGE") return {
        status: "COLLECTED",
        payload: {
          url: tab.url, document_title: "Credit Risk Analyst | Example Careers",
          job_title: "Credit Risk Analyst", company_name: "Example", job_location: "New York, NY",
          visible_text: "Credit Risk Analyst\nExample\nNew York, NY\nReview commercial credit risk.",
          availability: {closed_signal: false, valid_through: "2099-12-31"},
          apply_candidates: [{label: "Apply", url: "https://apply.example.test/application/credit-risk"}],
          blocker_signals: [], application_fields_present: false
        }
      };
      if (message.type === "JOBFLOW_COLLECT_FORM") return {
        status: "COLLECTED",
        payload: {
          url: tab.url, sanitized_html: "<form><input name='first-name'><button type='submit'>Submit</button></form>",
          client_refs: ["field-1", "final-submit-1"], blocker_signals: [], document_instance_id: `DOC-${"A".repeat(32)}`
        }
      };
      notifications.push({tabId, message});
      return {status: "IGNORED"};
    },
    onUpdated: tabUpdated,
    onRemoved: tabRemoved
  },
  scripting: {
    async executeScript() { return [{frameId: 0, documentId: "chrome-guided-document"}]; }
  },
  alarms: {create() {}, onAlarm: event("alarm")}
};

const sandbox = {
  chrome, crypto: webcrypto, URL, TextEncoder, AbortController, console, setTimeout, clearTimeout,
  btoa: (value) => Buffer.from(value, "binary").toString("base64"),
  atob: (value) => Buffer.from(value, "base64").toString("binary"),
  fetch: async (url, options) => {
    const value = String(url);
    const body = JSON.parse(String(options?.body || "{}"));
    requests.push({url: value, body});
    if (value.endsWith("/capture-search")) {
      if (searchMode === "ambiguous") return response({
        status: "SEARCH_SELECTION_REQUIRED", mode: "JOB_CAPTURE", intake_id: "GIN-GUIDED-AUTOPILOT",
        candidate_options: [
          {candidate_ref: "JDC-ONE", title: "Credit Risk Analyst", company_label: "Example", host_label: "careers.example.test"},
          {candidate_ref: "JDC-TWO", title: "Senior Credit Risk Analyst", company_label: "Example", host_label: "careers.example.test"}
        ], automatic_retry: false, real_external_actions: 0
      });
      return response({
        status: "AWAITING_JOB_PAGE_CAPTURE", mode: "JOB_CAPTURE", intake_id: "GIN-GUIDED-AUTOPILOT",
        official_url: "https://careers.example.test/jobs/credit-risk",
        allowed_company_domain: "example.test", automatic_retry: false, real_external_actions: 0
      });
    }
    if (value.endsWith("/capture-job")) {
      captureJobCalls += 1;
      if (searchMode === "retry" && captureJobCalls === 1) return response({
        status: "AWAITING_JOB_PAGE_CAPTURE", mode: "JOB_CAPTURE", intake_id: "GIN-GUIDED-AUTOPILOT",
        official_url: "https://careers.second-example.test/jobs/credit-risk",
        allowed_company_domain: "second-example.test", prior_candidate_status: "AI_NO_MATCH",
        automatic_retry: false, real_external_actions: 0
      });
      return response({
        status: "AWAITING_APPLICATION_FORM_CAPTURE", mode: "JOB_CAPTURE", intake_id: "GIN-GUIDED-AUTOPILOT",
        application_url: "https://apply.example.test/application/credit-risk",
        apply_route_status: "SELECTED", automatic_retry: false, real_external_actions: 0
      });
    }
    if (value.endsWith("/capture-form")) return response({
      status: "PREPARING_APPLICATION", mode: "JOB_CAPTURE", intake_id: "GIN-GUIDED-AUTOPILOT",
      application_id: "APP-GUIDED-AUTOPILOT", retry_after_ms: 3000,
      automatic_retry: false, real_external_actions: 0
    });
    throw new Error(`Unexpected URL: ${url}`);
  }
};

vm.runInNewContext(source, sandbox, {filename: "service-worker.js"});

function send(message, sender = {}) {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error("Listener response timed out")), 3000);
    const keepAlive = listeners.internal(message, sender, (result) => {
      clearTimeout(timeout);
      resolve(result);
    });
    assert.equal(keepAlive, true);
  });
}

(async () => {
  seedState();
  const completed = await send({type: "JOBFLOW_RUN_GUIDED_AUTOPILOT"});
  assert.equal(completed.status, "PREPARING_APPLICATION");
  assert.equal(searched.length, 1, "the browser search must be launched once");
  assert.deepEqual(openedUrls, [
    "about:blank",
    "https://careers.example.test/jobs/credit-risk",
    "https://apply.example.test/application/credit-risk"
  ]);
  assert.equal(requests.filter((item) => item.url.endsWith("/capture-search")).length, 1);
  assert.equal(requests.filter((item) => item.url.endsWith("/capture-job")).length, 1);
  assert.equal(requests.filter((item) => item.url.endsWith("/capture-form")).length, 1);
  assert.equal(requests.find((item) => item.url.endsWith("/capture-form")).body.companion_tab_id, 41);
  assert.equal(session.jobflowAssist.stage, "PREPARING_APPLICATION");
  assert.equal(session.jobflowAssist.preferred_tab_id, 41);
  assert.equal(session.jobflowAssist.autopilot_in_progress, false);
  assert.equal(domMessages.some((item) => item.message.type === "JOBFLOW_APPLY_APPROVED"), false);
  assert.equal(domMessages.some((item) => item.message.type === "JOBFLOW_NAVIGATE_APPROVED"), false);

  const openedBeforeRetry = openedUrls.length;
  const requestsBeforeRetry = requests.length;
  searchMode = "retry";
  captureJobCalls = 0;
  seedState();
  const retriedCandidate = await send({type: "JOBFLOW_RUN_GUIDED_AUTOPILOT"});
  assert.equal(retriedCandidate.status, "PREPARING_APPLICATION");
  assert.deepEqual(openedUrls.slice(openedBeforeRetry), [
    "about:blank",
    "https://careers.example.test/jobs/credit-risk",
    "https://careers.second-example.test/jobs/credit-risk",
    "https://apply.example.test/application/credit-risk"
  ]);
  assert.equal(requests.slice(requestsBeforeRetry).filter((item) => item.url.endsWith("/capture-job")).length, 2);

  const openedBeforeAmbiguity = openedUrls.length;
  const requestsBeforeAmbiguity = requests.length;
  searchMode = "ambiguous";
  seedState();
  const ambiguous = await send({type: "JOBFLOW_RUN_GUIDED_AUTOPILOT"});
  assert.equal(ambiguous.status, "SEARCH_SELECTION_REQUIRED");
  assert.equal(openedUrls.length, openedBeforeAmbiguity + 1, "ambiguity may open only the visible search tab");
  assert.equal(requests.slice(requestsBeforeAmbiguity).some((item) => item.url.endsWith("/capture-job")), false);
  assert.equal(session.jobflowAssist.autopilot_in_progress, false);

  const openedBeforeDenial = openedUrls.length;
  const requestsBeforeDenial = requests.length;
  permissionsGranted = false;
  searchMode = "selected";
  seedState();
  const denied = await send({type: "JOBFLOW_RUN_GUIDED_AUTOPILOT"});
  assert.equal(denied.code, "COMPANION_AUTOMATION_PERMISSION_REQUIRED");
  assert.equal(openedUrls.length, openedBeforeDenial);
  assert.equal(requests.length, requestsBeforeDenial);

  process.stdout.write(JSON.stringify({
    status: "PASS", visible_search_started: 1, official_job_pages_opened: 1,
    rejected_candidate_advanced_to_next: 1,
    verified_apply_routes_followed: 1, application_forms_captured: 1,
    ambiguous_search_navigations: 0, permission_denial_external_actions: 0,
    programmatic_apply_clicks: 0, programmatic_submit_clicks: 0,
    real_external_actions: 0
  }));
})().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
