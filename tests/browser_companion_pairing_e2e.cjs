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
const fetches = [];
const notifications = [];
const alarmCreates = [];
let tabQueryResults = [];
let failResultCollection = false;
let delayNextPair = false;
let releaseDelayedPair = null;
let invalidNextProof = false;
let tamperNextProvider = false;
let guidedPreparationPolls = 0;
const installationId = "0123456789abcdef0123456789abcdef";
const installationSecret = Buffer.alloc(32, 0x5a);

function base64url(value) {
  return Buffer.from(value).toString("base64url");
}

function event(name) {
  return {addListener(listener) { listeners[name] = listener; }};
}

function pairResult(url) {
  const isAssist = String(url).includes("/assist/");
  return isAssist ? {
    status: "BROWSER_COMPANION_PAIRED", mode: "APPLICATION_ASSIST",
    capture_status: "READY",
    assist_id: "BAS-SYNTHETIC-PAIRING", application_id: "APP-SYNTHETIC-PAIRING",
    allowed_page_origin: "https://apply.example.test", provider: "company",
    route_kind: "OFFICIAL_DIRECT", current_step: 1, max_steps: 20,
    expires_at: "2099-01-01T00:00:00Z", real_external_actions: 0
  } : {
    status: "GUIDED_INTAKE_PAIRED", capture_status: "AWAITING_JOB_PAGE_CAPTURE",
    mode: "JOB_CAPTURE", intake_id: "GIN-SYNTHETIC-PAIRING",
    official_url: "https://careers.example.test/jobs/risk-analyst",
    allowed_company_domain: "example.test", expires_at: "2099-01-01T00:00:00Z",
    real_external_actions: 0
  };
}

function signedPairResult(url, options) {
  const result = pairResult(url);
  const body = JSON.parse(String(options?.body || "{}"));
  const parsed = new URL(String(url));
  const assistPath = parsed.pathname.slice(0, -"/pair".length);
  const values = {
    assist_path: assistPath,
    base_url: parsed.origin,
    challenge: String(body.companion_binding.challenge),
    extension_version: "0.7.2",
    installation_id: String(body.companion_binding.installation_id),
    protocol_version: "2"
  };
  for (const key of [
    "allowed_company_domain", "allowed_page_origin", "application_id", "assist_id",
    "capture_status", "current_step", "discovery_mode", "expires_at", "intake_id", "max_steps", "mode",
    "official_url", "provider", "preferred_tab_id", "route_kind", "search_query", "status"
  ]) values[`response.${key}`] = result[key] === null || result[key] === undefined ? "" : String(result[key]);
  const canonical = JSON.stringify(Object.fromEntries(Object.entries(values).sort(([left], [right]) => left.localeCompare(right))));
  const proof = invalidNextProof ? Buffer.alloc(32, 0x00) : createHmac("sha256", installationSecret).update(canonical).digest();
  invalidNextProof = false;
  result.companion_binding = {
    schema_version: 1, algorithm: "HMAC-SHA256", installation_id: installationId,
    challenge: body.companion_binding.challenge, proof: base64url(proof)
  };
  if (tamperNextProvider) {
    tamperNextProvider = false;
    result.provider = result.provider === "workday" ? "company" : "workday";
  }
  return result;
}

const chrome = {
  runtime: {
    getManifest() { return {version: "0.7.2"}; },
    getURL(pathname) { return `chrome-extension://hhlliaaafegldkmcgmaoaelabipcaooj/${pathname}`; },
    onMessage: event("internal"), onMessageExternal: event("external"), onConnect: event("connect")
  },
  storage: {session: {
    async get(key) { return {[key]: session[key]}; },
    async set(value) { Object.assign(session, value); },
    async remove(key) { delete session[key]; }
  }},
  tabs: {
    async query() { return tabQueryResults; },
    async sendMessage(tabId, message) {
      if (failResultCollection && message?.type === "JOBFLOW_COLLECT_RESULT") throw new Error("SYNTHETIC_PAGE_GONE");
      if (message?.type === "JOBFLOW_COLLECT_FORM") return {
        status: "COLLECTED",
        payload: {
          url: "https://apply.example.test/application",
          sanitized_html: "<form><input name='first-name'></form>",
          blocker_signals: []
        }
      };
      notifications.push({tabId, message});
      return {status: "IGNORED"};
    },
    async get() { return null; },
    onUpdated: event("tabUpdated")
  },
  scripting: {async executeScript() {}},
  alarms: {create(name, details) { alarmCreates.push({name, details}); }, onAlarm: event("alarm")}
};

const sandbox = {
  chrome, crypto: webcrypto, URL, TextEncoder, AbortController, console, setTimeout, clearTimeout,
  btoa: (value) => Buffer.from(value, "binary").toString("base64"),
  atob: (value) => Buffer.from(value, "base64").toString("binary"),
  fetch: async (url, options) => {
    if (String(url).startsWith("chrome-extension://") && String(url).endsWith("/binding.json")) {
      return {ok: true, async json() { return {
        schema_version: 1, installation_id: installationId, secret_b64url: base64url(installationSecret)
      }; }};
    }
    fetches.push({url, options});
    if (String(url).endsWith("/result-unavailable")) {
      return {ok: true, async json() { return {
        status: "SUBMISSION_UNKNOWN", application_id: "APP-SYNTHETIC-PAIRING",
        automatic_retry: false, real_external_actions: 0,
        navigation: {authorization_token: "MUST-NOT-LEAK"},
        fields: [{value: "MUST-NOT-LEAK"}], files: [{download_path: "/private/material"}]
      }; }};
    }
    if (String(url).endsWith("/capture-form")) {
      return {ok: true, async json() { return {
        status: "PREPARING_APPLICATION", mode: "JOB_CAPTURE",
        intake_id: "GIN-SYNTHETIC-PAIRING", retry_after_ms: 3000,
        automatic_retry: false, real_external_actions: 0
      }; }};
    }
    if (String(url).endsWith("/capture-form-status")) {
      guidedPreparationPolls += 1;
      return {ok: true, async json() { return guidedPreparationPolls === 1 ? {
        status: "PREPARING_APPLICATION", mode: "JOB_CAPTURE",
        intake_id: "GIN-SYNTHETIC-PAIRING", retry_after_ms: 3000,
        automatic_retry: false, real_external_actions: 0
      } : {
        status: "REVIEW_PACKET_READY", mode: "JOB_CAPTURE",
        intake_id: "GIN-SYNTHETIC-PAIRING", application_id: "APP-SYNTHETIC-GUIDED",
        automatic_retry: false, real_external_actions: 0
      }; }};
    }
    if (delayNextPair) {
      delayNextPair = false;
      await new Promise((resolve) => { releaseDelayedPair = resolve; });
    }
    const result = signedPairResult(url, options);
    return {ok: true, async json() { return result; }};
  }
};
vm.runInNewContext(source, sandbox, {filename: "service-worker.js"});

function send(listener, message, sender) {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error("Listener response timed out")), 3000);
    const keepAlive = listener(message, sender, (result) => {
      clearTimeout(timeout);
      resolve(result);
    });
    assert.equal(keepAlive, true);
  });
}

async function waitUntil(predicate) {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  throw new Error("Condition timed out");
}

(async () => {
  const workingFetch = sandbox.fetch;
  sandbox.fetch = async (_url, options) => await new Promise((_resolve, reject) => {
    options.signal.addEventListener("abort", () => {
      const error = new Error("signal is aborted without reason");
      error.name = "AbortError";
      reject(error);
    }, {once: true});
  });
  const normalizedTimeout = await vm.runInContext(
    'postJSON("http://127.0.0.1:43123/slow", {}, 5)', sandbox
  ).then(() => null, (error) => error);
  sandbox.fetch = workingFetch;
  assert.equal(normalizedTimeout?.jobflow?.code, "COMPANION_LOCAL_REQUEST_TIMEOUT");
  assert.doesNotMatch(String(normalizedTimeout?.message || ""), /aborted without reason/i);

  const token = "a".repeat(54);
  const pairing = {protocol_version: 2, base_url: "http://127.0.0.1:43123", assist_path: `/intake/${token}`};
  const sender = {url: "http://127.0.0.1:43123/session/local/index.html", tab: {id: 11}};
  const tabSender = {tab: {id: 11, url: sender.url}};

  const ping = await send(listeners.external, {type: "JOBFLOW_PING"}, sender);
  assert.equal(ping.status, "AVAILABLE");

  invalidNextProof = true;
  const fakeLoopbackPair = await send(listeners.external, {type: "JOBFLOW_PAIR", pairing}, sender);
  assert.equal(fakeLoopbackPair.code, "COMPANION_BINDING_PROOF_INVALID");
  assert.equal(session.jobflowAssist, undefined, "an unsigned loopback service must not establish a pairing");
  const missingTabPair = await send(listeners.external, {type: "JOBFLOW_PAIR", pairing}, {url: sender.url});
  assert.equal(missingTabPair.code, "COMPANION_PAIR_PAYLOAD_INVALID");
  const wrongOriginPair = await send(listeners.external, {type: "JOBFLOW_PAIR", pairing}, {
    url: "http://127.0.0.1:43124/session/fake", tab: {id: 12}
  });
  assert.equal(wrongOriginPair.code, "COMPANION_PAIR_PAYLOAD_INVALID");

  const externalPaired = await send(listeners.external, {type: "JOBFLOW_PAIR", pairing}, sender);
  assert.equal(externalPaired.status, "GUIDED_INTAKE_PAIRED", JSON.stringify(externalPaired));
  assert.equal(session.jobflowAssist.paired, true);

  delayNextPair = true;
  const cancelledPendingPair = send(listeners.internal, {type: "JOBFLOW_PAIR", pairing}, tabSender);
  await waitUntil(() => releaseDelayedPair !== null);
  const cancelledWhilePairing = await send(listeners.external, {
    type: "JOBFLOW_CANCEL_GUIDED", binding: pairing, intake_id: "GIN-SYNTHETIC-PAIRING"
  }, sender);
  assert.equal(cancelledWhilePairing.status, "GUIDED_INTAKE_COMPANION_CLEARED");
  releaseDelayedPair();
  releaseDelayedPair = null;
  const cancelledPairResult = await cancelledPendingPair;
  assert.equal(cancelledPairResult.code, "COMPANION_SESSION_GENERATION_CHANGED");
  assert.equal(session.jobflowAssist, undefined, "a cancelled pairing must not restore its session");

  const paired = await send(listeners.internal, {type: "JOBFLOW_PAIR", pairing}, tabSender);
  assert.equal(paired.status, "GUIDED_INTAKE_PAIRED", JSON.stringify(paired));
  assert.equal(fetches.length, 4);
  assert.ok(session.jobflowAssist.generation);

  invalidNextProof = true;
  const fakeService = await send(listeners.internal, {type: "JOBFLOW_PAIR", pairing}, tabSender);
  assert.equal(fakeService.code, "COMPANION_BINDING_PROOF_INVALID");
  assert.equal(session.jobflowAssist.paired, true, "a fake loopback service must not replace the valid pairing");

  tamperNextProvider = true;
  const tamperedRouting = await send(listeners.internal, {type: "JOBFLOW_PAIR", pairing}, tabSender);
  assert.equal(tamperedRouting.code, "COMPANION_BINDING_PROOF_INVALID");
  assert.equal(session.jobflowAssist.provider, paired.provider, "an unsigned provider change must not replace routing state");

  const status = await send(listeners.external, {type: "JOBFLOW_GET_STATUS", binding: pairing}, sender);
  assert.equal(status.paired, true);
  assert.equal(status.intake_id, "GIN-SYNTHETIC-PAIRING");
  session.jobflowAssist = {
    ...session.jobflowAssist,
    last_result: {
      status: "AWAITING_JOB_PAGE_CAPTURE",
      navigation: {authorization_token: "MUST-NOT-LEAK"},
      fields: [{value: "MUST-NOT-LEAK"}], files: [{download_path: "/private/material"}]
    }
  };
  const sanitizedStatus = await send(listeners.external, {type: "JOBFLOW_GET_STATUS", binding: pairing}, sender);
  assert.deepEqual(JSON.parse(JSON.stringify(sanitizedStatus.last_result)), {status: "AWAITING_JOB_PAGE_CAPTURE"});
  const wrongStatus = await send(listeners.external, {
    type: "JOBFLOW_GET_STATUS", binding: {...pairing, assist_path: `/intake/${"b".repeat(54)}`}
  }, sender);
  assert.equal(wrongStatus.code, "COMPANION_STATUS_BINDING_INVALID");

  session.jobflowAssist = {
    ...session.jobflowAssist,
    stage: "AWAITING_APPLICATION_FORM_CAPTURE",
    last_result: {status: "AWAITING_APPLICATION_FORM_CAPTURE"}
  };
  const preparationStarted = await send(listeners.internal, {
    type: "JOBFLOW_CAPTURE_CURRENT", tab_id: 77,
    tab_url: "https://apply.example.test/application"
  }, {tab: {id: 77, url: "https://apply.example.test/application"}});
  assert.equal(preparationStarted.status, "PREPARING_APPLICATION");
  assert.equal(session.jobflowAssist.stage, "PREPARING_APPLICATION");
  assert.ok(alarmCreates.some((item) => item.name === "jobflow-guided-preparation-observer"));
  await listeners.alarm({name: "jobflow-guided-preparation-observer"});
  assert.equal(session.jobflowAssist.stage, "PREPARING_APPLICATION", "a pending poll must not duplicate preparation");
  await listeners.alarm({name: "jobflow-guided-preparation-observer"});
  assert.equal(session.jobflowAssist.stage, "REVIEW_PACKET_READY");
  assert.equal(session.jobflowAssist.application_id, "APP-SYNTHETIC-GUIDED");
  assert.equal(fetches.filter((item) => String(item.url).endsWith("/capture-form")).length, 1);

  const wrongCancel = await send(listeners.external, {
    type: "JOBFLOW_CANCEL_GUIDED", binding: pairing, intake_id: "GIN-DIFFERENT-INTAKE"
  }, sender);
  assert.equal(wrongCancel.code, "COMPANION_CANCEL_BINDING_INVALID");
  assert.equal(session.jobflowAssist.intake_id, "GIN-SYNTHETIC-PAIRING");
  const wrongTabCancel = await send(listeners.external, {
    type: "JOBFLOW_CANCEL_GUIDED", binding: pairing, intake_id: "GIN-SYNTHETIC-PAIRING"
  }, {...sender, tab: {id: 12}});
  assert.equal(wrongTabCancel.code, "COMPANION_CANCEL_BINDING_INVALID");
  const cancelled = await send(listeners.external, {
    type: "JOBFLOW_CANCEL_GUIDED", binding: pairing, intake_id: "GIN-SYNTHETIC-PAIRING"
  }, sender);
  assert.equal(cancelled.status, "GUIDED_INTAKE_COMPANION_CLEARED");
  assert.equal(session.jobflowAssist, undefined);
  const cancelledAgain = await send(listeners.external, {
    type: "JOBFLOW_CANCEL_GUIDED", binding: pairing, intake_id: "GIN-SYNTHETIC-PAIRING"
  }, sender);
  assert.equal(cancelledAgain.status, "GUIDED_INTAKE_COMPANION_CLEARED");
  const pairedAfterCancel = await send(listeners.internal, {type: "JOBFLOW_PAIR", pairing}, tabSender);
  assert.equal(pairedAfterCancel.status, "GUIDED_INTAKE_PAIRED");

  const fetchCountBeforeModeAttack = fetches.length;
  session.jobflowAssist = {...session.jobflowAssist, mode: "APPLICATION_ASSIST"};
  const differentMode = await send(listeners.internal, {type: "JOBFLOW_PAIR", pairing}, tabSender);
  assert.equal(differentMode.code, "COMPANION_DIFFERENT_MODE_ACTIVE");
  assert.equal(fetches.length, fetchCountBeforeModeAttack);
  session.jobflowAssist = {...session.jobflowAssist, mode: "JOB_CAPTURE"};

  delayNextPair = true;
  const stalePair = send(listeners.internal, {type: "JOBFLOW_PAIR", pairing}, tabSender);
  await waitUntil(() => releaseDelayedPair !== null);
  const winningPair = await send(listeners.internal, {type: "JOBFLOW_PAIR", pairing}, tabSender);
  assert.equal(winningPair.status, "GUIDED_INTAKE_PAIRED");
  const winningGeneration = session.jobflowAssist.generation;
  releaseDelayedPair();
  const staleResult = await stalePair;
  assert.equal(staleResult.code, "COMPANION_SESSION_GENERATION_CHANGED");
  assert.equal(session.jobflowAssist.generation, winningGeneration, "stale async completion must not restore an old generation");
  assert.equal(session.jobflowAssist.paired, true);

  session.jobflowAssist = {
    ...session.jobflowAssist, stage: "AWAITING_USER_SUBMIT", submission_observed: true,
    tab_id: 99, application_id: "APP-SYNTHETIC-PAIRING"
  };
  const fetchCountBeforeTabAttack = fetches.length;
  const notificationCountBeforeTabAttack = notifications.length;
  listeners.tabUpdated(100, {status: "complete"}, {url: "https://apply.example.test/unrelated"});
  await new Promise((resolve) => setTimeout(resolve, 30));
  assert.equal(fetches.length, fetchCountBeforeTabAttack, "an unrelated tab update must not make a request");
  assert.equal(notifications.length, notificationCountBeforeTabAttack, "an unrelated tab update must not notify JobFlow");

  const lockedPair = await send(listeners.internal, {type: "JOBFLOW_PAIR", pairing}, tabSender);
  assert.equal(lockedPair.code, "COMPANION_FINAL_REVIEW_LOCKED");
  assert.equal(session.jobflowAssist.stage, "AWAITING_USER_SUBMIT");
  session.jobflowAssist = {...session.jobflowAssist, stage: "OBSERVING_RESULT_PAGE"};

  tabQueryResults = [
    {id: 11, url: "http://127.0.0.1:43123/session/local/"},
    {id: 12, url: "http://127.0.0.1:43124/session/other/"}
  ];
  failResultCollection = true;
  listeners.tabUpdated(99, {status: "complete"}, {url: "https://apply.example.test/result"});
  await waitUntil(() => fetches.some((item) => String(item.url).endsWith("/result-unavailable")));
  assert.equal(session.jobflowAssist.stage, "SUBMISSION_UNKNOWN");
  const statusNotifications = notifications.filter((item) => item.message?.type === "JOBFLOW_ASSIST_STATUS");
  assert.deepEqual(statusNotifications.map((item) => item.tabId), [11]);
  assert.equal(JSON.stringify(statusNotifications).includes("MUST-NOT-LEAK"), false);
  assert.equal(JSON.stringify(statusNotifications).includes("download_path"), false);

  process.stdout.write(JSON.stringify({
    status: "PASS", signed_external_pairing: true, popup_pair_fallback: true,
    installation_hmac_required: true, fake_loopback_rejected: true, unsigned_routing_change_rejected: true,
    different_mode_blocked: true, stale_async_completion_blocked: true,
    final_review_immutable: true, unrelated_tab_update_ignored: true,
    same_bound_tab_result_observed: true, binding_scoped_status: true, scoped_cancel_releases_session: true,
    guided_preparation_background_polled: true, guided_preparation_start_count: 1,
    raw_abort_error_normalized: true,
    capability_values_redacted: true,
    network_calls: fetches.length, real_external_actions: 0
  }));
})().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
