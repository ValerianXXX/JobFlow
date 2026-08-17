"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {createHmac, webcrypto} = require("node:crypto");

const project = path.resolve(__dirname, "..");
const source = fs.readFileSync(path.join(project, "browser-companion", "service-worker.js"), "utf8");
const listeners = {};
const session = {};
const requests = [];
const domMessages = [];
const notifications = [];
const alarms = [];
const installationId = "0123456789abcdef0123456789abcdef";
const installationSecret = Buffer.alloc(32, 0x67);
const token = "q".repeat(54);
const pairing = {
  protocol_version: 2,
  base_url: "http://127.0.0.1:43123",
  assist_path: `/assist/${token}`
};

function base64url(value) {
  return Buffer.from(value).toString("base64url");
}

function event(name) {
  const values = new Set();
  return {
    addListener(listener) { values.add(listener); listeners[name] = listener; },
    removeListener(listener) { values.delete(listener); if (listeners[name] === listener) delete listeners[name]; }
  };
}

function response(value) {
  return {ok: true, async json() { return value; }};
}

function signedPairResult(url, options) {
  const body = JSON.parse(String(options?.body || "{}"));
  const result = {
    status: "BROWSER_COMPANION_PAIRED",
    capture_status: "READY",
    mode: "APPLICATION_ASSIST",
    assist_id: "BAS-ONE-CONFIRMATION",
    application_id: "APP-ONE-CONFIRMATION",
    allowed_page_origin: "https://apply.example.test",
    provider: "company",
    route_kind: "OFFICIAL_DIRECT",
    current_step: 1,
    max_steps: 20,
    preferred_tab_id: 73,
    expires_at: "2099-01-01T00:00:00Z",
    real_external_actions: 0
  };
  const parsed = new URL(String(url));
  const assistPath = parsed.pathname.slice(0, -"/pair".length);
  const values = {
    assist_path: assistPath,
    base_url: parsed.origin,
    challenge: String(body.companion_binding.challenge),
    extension_version: "0.9.0",
    installation_id: String(body.companion_binding.installation_id),
    protocol_version: "2"
  };
  for (const key of [
    "allowed_company_domain", "allowed_page_origin", "application_id", "assist_id",
    "capture_status", "current_step", "discovery_mode", "expires_at", "intake_id", "max_steps", "mode",
    "official_url", "provider", "preferred_tab_id", "route_kind", "search_query", "status"
  ]) values[`response.${key}`] = result[key] === null || result[key] === undefined ? "" : String(result[key]);
  const canonical = JSON.stringify(Object.fromEntries(
    Object.entries(values).sort(([left], [right]) => left.localeCompare(right))
  ));
  result.companion_binding = {
    schema_version: 1,
    algorithm: "HMAC-SHA256",
    installation_id: installationId,
    challenge: body.companion_binding.challenge,
    proof: base64url(createHmac("sha256", installationSecret).update(canonical).digest())
  };
  return result;
}

session.jobflowAssist = {
  generation: "guided-terminal-generation",
  revision: 3,
  protocol_version: 2,
  base_url: "http://127.0.0.1:43123",
  assist_path: `/intake/${"g".repeat(54)}`,
  jobflow_tab_id: 11,
  mode: "JOB_CAPTURE",
  intake_id: "GIN-ONE-CONFIRMATION",
  application_id: "APP-ONE-CONFIRMATION",
  paired: true,
  stage: "REVIEW_PACKET_READY",
  expires_at: "2099-01-01T00:00:00Z",
  last_result: {status: "REVIEW_PACKET_READY", application_id: "APP-ONE-CONFIRMATION"}
};

const chrome = {
  runtime: {
    lastError: null,
    getManifest() { return {version: "0.9.0"}; },
    getURL(value) { return `chrome-extension://hhlliaaafegldkmcgmaoaelabipcaooj/${value}`; },
    onMessage: event("internal"),
    onMessageExternal: event("external"),
    onConnect: event("connect")
  },
  permissions: {async contains() { return true; }},
  storage: {session: {
    async get(key) { return {[key]: session[key]}; },
    async set(value) { Object.assign(session, value); },
    async remove(key) { delete session[key]; }
  }},
  tabs: {
    async get(tabId) {
      if (tabId !== 73) throw new Error(`unexpected tab ${tabId}`);
      return {id: 73, url: "https://apply.example.test/application/credit-risk", status: "complete", active: true};
    },
    async query() {
      return [{id: 11, url: "http://127.0.0.1:43123/session/jobflow/"}];
    },
    async sendMessage(tabId, message) {
      if (tabId === 11) {
        notifications.push(message);
        return {status: "RECEIVED"};
      }
      assert.equal(tabId, 73);
      domMessages.push(message);
      if (message.type === "JOBFLOW_COLLECT_FORM") return {
        status: "COLLECTED",
        payload: {
          url: "https://apply.example.test/application/credit-risk",
          sanitized_html: "<form><label>First name</label><input name='first-name'><button type='submit'>Submit</button></form>",
          client_refs: ["DOM-000000000001", "DOM-000000000002"],
          document_instance_id: `DOC-${"A".repeat(32)}`,
          blocker_signals: []
        }
      };
      if (message.type === "JOBFLOW_APPLY_APPROVED") {
        assert.deepEqual(message.final_submit_client_refs, ["DOM-000000000002"]);
        assert.equal(message.navigation, null);
        assert.equal(message.files.length, 0);
        assert.equal(message.fields.length, 1);
        return {
          status: "APPLIED",
          field_bindings: [{client_ref: "DOM-000000000001", value_sha256: message.fields[0].value_sha256}],
          material_bindings: [],
          submit_events: 0,
          navigation_actions: 0
        };
      }
      throw new Error(`unexpected DOM message ${message.type}`);
    },
    onUpdated: event("tabUpdated"),
    onRemoved: event("tabRemoved")
  },
  scripting: {
    async executeScript() { return [{frameId: 0, documentId: "browser-document-one-confirmation"}]; }
  },
  alarms: {
    create(name, details) { alarms.push({name, details}); },
    onAlarm: event("alarm")
  }
};

const sandbox = {
  chrome,
  crypto: webcrypto,
  URL,
  TextEncoder,
  AbortController,
  console,
  setTimeout,
  clearTimeout,
  btoa: (value) => Buffer.from(value, "binary").toString("base64"),
  atob: (value) => Buffer.from(value, "base64").toString("binary"),
  fetch: async (url, options) => {
    const value = String(url);
    if (value.startsWith("chrome-extension://") && value.endsWith("/binding.json")) {
      return response({
        schema_version: 1,
        installation_id: installationId,
        secret_b64url: base64url(installationSecret)
      });
    }
    requests.push({url: value, body: JSON.parse(String(options?.body || "{}"))});
    if (value.endsWith("/pair")) return response(signedPairResult(value, options));
    if (value.endsWith("/prepare")) return response({
      status: "PAGE_PREPARED",
      application_id: "APP-ONE-CONFIRMATION",
      current_step: 1,
      fields: [{
        client_ref: "DOM-000000000001",
        value: "Synthetic",
        value_sha256: `sha256:${"1".repeat(64)}`
      }],
      files: [],
      navigation: null,
      final_submit_client_refs: ["DOM-000000000002"],
      automatic_retry: false
    });
    if (value.endsWith("/discover-dynamic-fields")) return response({
      status: "NO_DYNAMIC_FIELDS",
      application_id: "APP-ONE-CONFIRMATION",
      automatic_retry: false
    });
    if (value.endsWith("/complete")) return response({
      status: "AWAITING_USER_SUBMIT",
      application_id: "APP-ONE-CONFIRMATION",
      current_step: 1,
      manual_field_count: 0,
      navigation: null,
      final_submit: false,
      automatic_retry: false
    });
    throw new Error(`unexpected URL ${value}`);
  }
};

vm.runInNewContext(source, sandbox, {filename: "service-worker.js"});

function send(listener, message, sender) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("listener timed out")), 15000);
    const keepAlive = listener(message, sender, (result) => {
      clearTimeout(timer);
      resolve(result);
    });
    assert.equal(keepAlive, true);
  });
}

(async () => {
  const sender = {
    url: "http://127.0.0.1:43123/session/jobflow/index.html",
    tab: {id: 11, url: "http://127.0.0.1:43123/session/jobflow/index.html"}
  };
  const paired = await send(listeners.external, {type: "JOBFLOW_PAIR", pairing}, sender);
  assert.equal(paired.status, "BROWSER_COMPANION_PAIRED");
  assert.equal(session.jobflowAssist.mode, "APPLICATION_ASSIST");
  assert.equal(session.jobflowAssist.preferred_tab_id, 73);
  assert.ok(alarms.some((item) => item.name === "jobflow-application-autopilot"));

  await listeners.alarm({name: "jobflow-application-autopilot"});
  assert.equal(session.jobflowAssist.stage, "AWAITING_USER_SUBMIT");
  assert.equal(session.jobflowAssist.autopilot_in_progress, false);
  assert.equal(requests.filter((item) => item.url.endsWith("/prepare")).length, 1);
  assert.equal(requests.filter((item) => item.url.endsWith("/complete")).length, 1);
  assert.equal(domMessages.filter((item) => item.type === "JOBFLOW_APPLY_APPROVED").length, 1);
  assert.equal(domMessages.some((item) => item.type === "JOBFLOW_NAVIGATE_APPROVED"), false);
  assert.equal(domMessages.some((item) => item.type === "JOBFLOW_USER_SUBMIT_OBSERVED"), false);

  const writesBeforeSecondAlarm = domMessages.filter((item) => item.type === "JOBFLOW_APPLY_APPROVED").length;
  await listeners.alarm({name: "jobflow-application-autopilot"});
  assert.equal(
    domMessages.filter((item) => item.type === "JOBFLOW_APPLY_APPROVED").length,
    writesBeforeSecondAlarm,
    "the final-review lock must prevent duplicate fill or upload"
  );

  assert.ok(notifications.some((item) => (
    item.type === "JOBFLOW_ASSIST_STATUS" && item.result?.status === "AWAITING_USER_SUBMIT"
  )));
  process.stdout.write(JSON.stringify({
    status: "PASS",
    one_confirmation_transition: true,
    approved_application_tab_reused: true,
    automatic_prefill_runs: 1,
    duplicate_prefill_runs: 0,
    programmatic_navigation_clicks: 0,
    programmatic_submit_clicks: 0,
    final_submit: "USER_ONLY"
  }));
})().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
