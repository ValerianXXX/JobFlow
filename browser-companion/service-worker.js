"use strict";

const PROTOCOL = 2;
const EXTENSION_VERSION = chrome.runtime.getManifest().version;
const SESSION_KEY = "jobflowAssist";
const RESULT_ALARM = "jobflow-result-observer";
const NAVIGATION_ALARM = "jobflow-navigation-observer";
const NAVIGATION_SETTLE_MS = 20000;
const BINDING_SCHEMA_VERSION = 1;
const BINDING_ALGORITHM = "HMAC-SHA256";
const PAIR_RESPONSE_FIELDS = [
  "allowed_company_domain", "allowed_page_origin", "application_id", "assist_id",
  "capture_status", "current_step", "expires_at", "intake_id", "max_steps", "mode",
  "provider", "route_kind", "status"
];
const MANUAL_CHALLENGE_FIELDS = [
  "application_id", "assist_id", "challenge_hash", "challenge_id", "client_ref",
  "control_semantics_hash", "document_instance_id", "expires_at", "issued_at", "nonce",
  "prior_page_content_hash", "stage", "tab_id"
];
const MANUAL_EVIDENCE_FIELDS = [
  "control_semantics_hash", "event_hash", "manual_navigation_application_id",
  "manual_navigation_assist_id", "manual_navigation_challenge_hash",
  "manual_navigation_challenge_id", "manual_navigation_client_ref",
  "manual_navigation_default_prevented", "manual_navigation_document_id",
  "manual_navigation_nonce", "manual_navigation_stage", "manual_navigation_tab_id",
  "prior_page_content_hash", "trusted_user_event", "url"
];
let navigationObservationInFlight = false;
let sessionMutationQueue = Promise.resolve();
let installationBindingPromise = null;

const TERMINAL_STAGES = new Set([
  "CONFIRMED", "SUBMISSION_UNKNOWN", "AWAITING_APPROVAL", "REVIEW_PACKET_READY",
  "DEFERRED", "FAILED", "REVOKED", "CANCELLED"
]);
const SAFE_REPAIR_STAGES = new Set([
  "PAIRING", "BROWSER_COMPANION_PAIRED", "GUIDED_INTAKE_PAIRED",
  "AWAITING_JOB_PAGE_CAPTURE", "AWAITING_APPLICATION_FORM_CAPTURE", "FORM_CAPTURE_FAILED"
]);

function withSessionLock(operation) {
  const run = sessionMutationQueue.then(operation, operation);
  sessionMutationQueue = run.catch(() => undefined);
  return run;
}

async function rawSessionState() {
  const value = await chrome.storage.session.get(SESSION_KEY);
  return value[SESSION_KEY] || null;
}

function sessionGeneration() {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return `${Date.now()}:${Math.random()}:${Math.random()}`;
}

function sameGeneration(left, right) {
  return Boolean(
    left?.generation && right?.generation && left.generation === right.generation &&
    Number(left.revision) === Number(right.revision)
  );
}

function staleSessionError() {
  return Object.assign(new Error("This browser action belongs to an older JobFlow session."), {
    jobflow: {status: "BLOCKED", code: "COMPANION_SESSION_GENERATION_CHANGED", automatic_retry: false}
  });
}

async function sessionState() {
  return await withSessionLock(async () => {
    const state = await rawSessionState();
    const expiresAt = Date.parse(String(state?.expires_at || ""));
    if (state && (!Number.isFinite(expiresAt) || expiresAt <= Date.now())) {
      await chrome.storage.session.remove(SESSION_KEY);
      return null;
    }
    return state;
  });
}

async function assertCurrentSession(snapshot, options = {}) {
  const current = await sessionState();
  if (!sameGeneration(current, snapshot)) throw staleSessionError();
  if (Number.isInteger(options.tabId) && current.tab_id !== options.tabId) {
    throw Object.assign(new Error("This action came from a different browser tab."), {
      jobflow: {status: "BLOCKED", code: "COMPANION_TAB_BINDING_CHANGED", automatic_retry: false}
    });
  }
  if (options.stages && !options.stages.includes(current.stage)) throw staleSessionError();
  return current;
}

async function saveSessionCAS(snapshot, value, options = {}) {
  return await withSessionLock(async () => {
    const current = await rawSessionState();
    if (!sameGeneration(current, snapshot)) throw staleSessionError();
    if (current.stage === "AWAITING_USER_SUBMIT" && value.stage !== "AWAITING_USER_SUBMIT" && !options.allowSubmitTransition) {
      throw Object.assign(new Error("The session is locked at final user review."), {
        jobflow: {status: "BLOCKED", code: "COMPANION_FINAL_REVIEW_LOCKED", automatic_retry: false}
      });
    }
    const next = {...value, generation: current.generation, revision: Number(current.revision || 0) + 1};
    await chrome.storage.session.set({[SESSION_KEY]: next});
    return next;
  });
}

async function clearSessionCAS(snapshot) {
  return await withSessionLock(async () => {
    const current = await rawSessionState();
    if (!sameGeneration(current, snapshot)) return false;
    await chrome.storage.session.remove(SESSION_KEY);
    return true;
  });
}

function endpoint(state, suffix) {
  return `${state.base_url}${state.assist_path}${suffix}`;
}

function sameAssistURL(value, state) {
  try {
    const parsed = new URL(value);
    const base = new URL(state.base_url);
    return parsed.origin === base.origin && parsed.pathname.startsWith(`${state.assist_path}/`);
  } catch (_error) {
    return false;
  }
}

function sameApprovedOrigin(value, state) {
  try {
    const parsed = new URL(value);
    return parsed.protocol === "https:" && parsed.origin === state.allowed_page_origin;
  } catch (_error) {
    return false;
  }
}

function loopbackOrigin(value) {
  try {
    const parsed = new URL(value);
    if (parsed.protocol !== "http:" || !["127.0.0.1", "localhost"].includes(parsed.hostname)) return null;
    return parsed.origin;
  } catch (_error) {
    return null;
  }
}

function bindingError(code, message) {
  return Object.assign(new Error(message), {
    jobflow: {status: "BLOCKED", code, message, automatic_retry: false}
  });
}

function exactKeys(value, expected) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const actual = Object.keys(value).sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

function validManualChallenge(challenge, manualNavigation, state, tabId, documentInstanceId) {
  const issued = Date.parse(String(challenge?.issued_at || ""));
  const expires = Date.parse(String(challenge?.expires_at || ""));
  return Boolean(
    exactKeys(challenge, MANUAL_CHALLENGE_FIELDS) && manualNavigation &&
    /^MNC-[A-F0-9]{32}$/.test(String(challenge.challenge_id || "")) &&
    /^[A-Za-z0-9_-]{40,128}$/.test(String(challenge.nonce || "")) &&
    /^sha256:[a-f0-9]{64}$/.test(String(challenge.challenge_hash || "")) &&
    Number.isFinite(issued) && Number.isFinite(expires) && issued < expires && expires > Date.now() &&
    challenge.assist_id === state.assist_id && challenge.application_id === state.application_id &&
    challenge.tab_id === tabId && challenge.document_instance_id === documentInstanceId &&
    challenge.stage === "MANUAL_NAVIGATION_REQUIRED" &&
    challenge.client_ref === manualNavigation.client_ref &&
    challenge.prior_page_content_hash === manualNavigation.prior_page_content_hash &&
    challenge.control_semantics_hash === manualNavigation.control_semantics_hash &&
    manualNavigation.mode === "MANUAL_USER_CLICK" && manualNavigation.programmatic_allowed === false
  );
}

async function manualEventHash(challenge) {
  return await sha256(JSON.stringify([
    "MANUAL_FORWARD_CONTROL_CLICK", challenge.challenge_id, challenge.nonce,
    challenge.assist_id, challenge.application_id, challenge.tab_id,
    challenge.document_instance_id, "MANUAL_NAVIGATION_REQUIRED",
    challenge.prior_page_content_hash, challenge.control_semantics_hash,
    challenge.client_ref, false
  ]));
}

function decodeBase64Url(value) {
  if (!/^[A-Za-z0-9_-]+$/.test(String(value || ""))) throw bindingError(
    "COMPANION_BINDING_INVALID", "Reinstall the JobFlow Browser Companion on this Windows account."
  );
  const normalized = String(value).replace(/-/g, "+").replace(/_/g, "/");
  const binary = atob(normalized + "=".repeat((4 - normalized.length % 4) % 4));
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

function encodeBase64Url(bytes) {
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(offset, Math.min(offset + 0x8000, bytes.length)));
  }
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

async function installationBinding() {
  if (!installationBindingPromise) installationBindingPromise = (async () => {
    let response;
    try {
      response = await fetch(chrome.runtime.getURL("binding.json"), {
        cache: "no-store", credentials: "omit", redirect: "error"
      });
    } catch (_error) {
      throw bindingError("COMPANION_BINDING_MISSING", "Run the Browser Companion installer before pairing.");
    }
    if (!response.ok) throw bindingError("COMPANION_BINDING_MISSING", "Run the Browser Companion installer before pairing.");
    const value = await response.json().catch(() => null);
    if (
      !value || value.schema_version !== BINDING_SCHEMA_VERSION ||
      !/^[a-f0-9]{32}$/.test(String(value.installation_id || ""))
    ) {
      throw bindingError("COMPANION_BINDING_INVALID", "Reinstall the Browser Companion because its local binding is invalid.");
    }
    const secret = decodeBase64Url(value.secret_b64url);
    if (secret.length !== 32) throw bindingError(
      "COMPANION_BINDING_INVALID", "Reinstall the Browser Companion because its local binding is invalid."
    );
    const key = await crypto.subtle.importKey("raw", secret, {name: "HMAC", hash: "SHA-256"}, false, ["verify"]);
    secret.fill(0);
    return {installation_id: String(value.installation_id), key};
  })();
  try {
    return await installationBindingPromise;
  } catch (error) {
    installationBindingPromise = null;
    throw error;
  }
}

function pairBindingMessage(pairing, requestBinding, result) {
  const values = {
    assist_path: String(pairing.assist_path),
    base_url: String(pairing.base_url),
    challenge: String(requestBinding.challenge),
    extension_version: EXTENSION_VERSION,
    installation_id: String(requestBinding.installation_id),
    protocol_version: String(PROTOCOL)
  };
  for (const key of PAIR_RESPONSE_FIELDS) {
    values[`response.${key}`] = result?.[key] === null || result?.[key] === undefined ? "" : String(result[key]);
  }
  const sorted = {};
  for (const key of Object.keys(values).sort()) sorted[key] = values[key];
  return new TextEncoder().encode(JSON.stringify(sorted));
}

async function freshBindingRequest() {
  const installed = await installationBinding();
  const challengeBytes = new Uint8Array(32);
  crypto.getRandomValues(challengeBytes);
  const challenge = encodeBase64Url(challengeBytes);
  challengeBytes.fill(0);
  return {
    installed,
    request: {
      schema_version: BINDING_SCHEMA_VERSION,
      algorithm: BINDING_ALGORITHM,
      installation_id: installed.installation_id,
      challenge
    }
  };
}

async function verifyPairBinding(pairing, binding, result) {
  const proof = result?.companion_binding;
  if (
    !proof || proof.schema_version !== BINDING_SCHEMA_VERSION || proof.algorithm !== BINDING_ALGORITHM ||
    proof.installation_id !== binding.request.installation_id || proof.challenge !== binding.request.challenge
  ) {
    throw bindingError("COMPANION_BINDING_PROOF_INVALID", "The local service did not prove this Browser Companion installation.");
  }
  const proofBytes = decodeBase64Url(proof.proof);
  if (proofBytes.length !== 32) throw bindingError(
    "COMPANION_BINDING_PROOF_INVALID", "The local service did not prove this Browser Companion installation."
  );
  const valid = await crypto.subtle.verify(
    "HMAC", binding.installed.key, proofBytes, pairBindingMessage(pairing, binding.request, result)
  );
  proofBytes.fill(0);
  if (!valid) throw bindingError(
    "COMPANION_BINDING_PROOF_INVALID", "The local service did not prove this Browser Companion installation."
  );
}

function validPairing(pairing) {
  return Boolean(
    pairing && pairing.protocol_version === PROTOCOL &&
    typeof pairing.base_url === "string" && typeof pairing.assist_path === "string" &&
    /^http:\/\/(?:127\.0\.0\.1|localhost):\d+$/.test(pairing.base_url) &&
    /^\/(?:assist|intake)\/[A-Za-z0-9_-]{40,}$/.test(pairing.assist_path)
  );
}

function requestedPairingMode(pairing) {
  return String(pairing?.assist_path || "").startsWith("/intake/") ? "JOB_CAPTURE" : "APPLICATION_ASSIST";
}

function samePairingBinding(state, pairing) {
  return Boolean(state && state.base_url === pairing.base_url && state.assist_path === pairing.assist_path);
}

function pairingIdentityMatches(previous, result, requestedMode) {
  if (!previous || previous.mode !== requestedMode) return true;
  if (requestedMode === "JOB_CAPTURE" && previous.intake_id && result.intake_id !== previous.intake_id) return false;
  if (requestedMode === "APPLICATION_ASSIST" && previous.assist_id && result.assist_id !== previous.assist_id) return false;
  return true;
}

async function reservePairing(pairing, senderTabId) {
  return await withSessionLock(async () => {
    let current = await rawSessionState();
    const expiresAt = Date.parse(String(current?.expires_at || ""));
    if (current && (!Number.isFinite(expiresAt) || expiresAt <= Date.now())) {
      await chrome.storage.session.remove(SESSION_KEY);
      current = null;
    }
    const mode = requestedPairingMode(pairing);
    const sameBinding = samePairingBinding(current, pairing);
    if (current?.stage === "AWAITING_USER_SUBMIT") {
      throw Object.assign(new Error("Final review is already active and cannot be rebound."), {
        jobflow: {status: "BLOCKED", code: "COMPANION_FINAL_REVIEW_LOCKED", automatic_retry: false}
      });
    }
    if (current && !TERMINAL_STAGES.has(current.stage)) {
      if (!sameBinding) {
        throw Object.assign(new Error("Another JobFlow browser task is still active."), {
          jobflow: {status: "BLOCKED", code: "COMPANION_DIFFERENT_SESSION_ACTIVE", automatic_retry: false}
        });
      }
      if (current.mode && current.mode !== mode) {
        throw Object.assign(new Error("A different JobFlow browser mode is still active."), {
          jobflow: {status: "BLOCKED", code: "COMPANION_DIFFERENT_MODE_ACTIVE", automatic_retry: false}
        });
      }
      if (!SAFE_REPAIR_STAGES.has(current.stage)) {
        throw Object.assign(new Error("Finish or end the current browser step before reconnecting."), {
          jobflow: {status: "BLOCKED", code: "COMPANION_SESSION_BUSY", automatic_retry: false}
        });
      }
    }
    const generation = sessionGeneration();
    const reserved = sameBinding ? {
      ...current,
      generation,
      revision: 0,
      mode,
      jobflow_tab_id: senderTabId,
      pairing_in_progress: true
    } : {
      generation,
      revision: 0,
      protocol_version: PROTOCOL,
      base_url: pairing.base_url,
      assist_path: pairing.assist_path,
      jobflow_tab_id: senderTabId,
      mode,
      paired: false,
      stage: "PAIRING",
      expires_at: new Date(Date.now() + 5 * 60 * 1000).toISOString(),
      pairing_in_progress: true
    };
    await chrome.storage.session.set({[SESSION_KEY]: reserved});
    return {reserved, previous: sameBinding ? current : null, mode};
  });
}

async function restorePairingReservation(reservation) {
  try {
    if (!reservation.previous) {
      await clearSessionCAS(reservation.reserved);
      return;
    }
    await saveSessionCAS(reservation.reserved, {
      ...reservation.previous,
      generation: reservation.reserved.generation,
      revision: reservation.reserved.revision,
      pairing_in_progress: false
    });
  } catch (_ignored) {
    // A newer explicit pairing action owns the session now.
  }
}

function publicSessionStatus(state) {
  return state ? {
    status: state.stage,
    paired: Boolean(state.paired),
    mode: state.mode,
    assist_id: state.assist_id,
    intake_id: state.intake_id,
    application_id: state.application_id,
    allowed_page_origin: state.allowed_page_origin,
    allowed_company_domain: state.allowed_company_domain,
    provider: state.provider,
    current_step: state.current_step,
    max_steps: state.max_steps,
    handoff_kind: state.handoff_kind,
    manual_field_count: state.manual_field_count,
    expires_at: state.expires_at,
    last_result: publicResult(state.last_result),
    protocol_version: PROTOCOL,
    extension_version: EXTENSION_VERSION
  } : {
    status: "NOT_PAIRED", paired: false,
    protocol_version: PROTOCOL, extension_version: EXTENSION_VERSION
  };
}

function publicResult(result) {
  if (!result || typeof result !== "object") return null;
  const safe = {};
  for (const key of [
    "status", "code", "message", "mode", "assist_id", "intake_id", "application_id",
    "allowed_page_origin", "allowed_company_domain", "provider", "route_kind",
    "current_step", "max_steps", "capture_status", "handoff_kind", "manual_field_count",
    "expires_at", "automatic_retry", "real_external_actions", "submit_capability",
    "final_submit", "poll_count"
  ]) {
    if (["string", "number", "boolean"].includes(typeof result[key])) safe[key] = result[key];
  }
  return safe;
}

async function pairWithJobFlow(pairing, senderOrigin, senderTabId = null) {
  if (!validPairing(pairing) || !senderOrigin || senderOrigin !== pairing.base_url || !Number.isInteger(senderTabId)) {
    return {status: "BLOCKED", code: "COMPANION_PAIR_PAYLOAD_INVALID", protocol_version: PROTOCOL, extension_version: EXTENSION_VERSION};
  }
  const binding = await freshBindingRequest();
  const reservation = await reservePairing(pairing, senderTabId);
  try {
    const result = await postJSON(`${pairing.base_url}${pairing.assist_path}/pair`, {
      protocol_version: PROTOCOL, extension_version: EXTENSION_VERSION,
      companion_binding: binding.request
    }, 2500);
    await assertCurrentSession(reservation.reserved);
    await verifyPairBinding(pairing, binding, result);
    await assertCurrentSession(reservation.reserved);
    if ((result.mode && result.mode !== reservation.mode) || !pairingIdentityMatches(reservation.previous, result, reservation.mode)) {
      throw Object.assign(new Error("JobFlow returned a different browser task identity."), {
        jobflow: {status: "BLOCKED", code: "COMPANION_PAIR_IDENTITY_CHANGED", automatic_retry: false}
      });
    }
    const state = await saveSessionCAS(reservation.reserved, {
      generation: reservation.reserved.generation,
      protocol_version: PROTOCOL,
      base_url: pairing.base_url,
      assist_path: pairing.assist_path,
      jobflow_tab_id: senderTabId,
      mode: reservation.mode,
      assist_id: result.assist_id,
      intake_id: result.intake_id,
      application_id: result.application_id,
      allowed_page_origin: result.allowed_page_origin,
      allowed_company_domain: result.allowed_company_domain,
      provider: result.provider,
      route_kind: result.route_kind,
      current_step: result.current_step,
      max_steps: result.max_steps,
      expires_at: result.expires_at,
      paired: true,
      stage: result.capture_status || result.status,
      navigation: null,
      navigation_authorized: false,
      manual_field_count: 0,
      submission_observed: false,
      result_final: false,
      pairing_in_progress: false,
      last_result: result
    });
    await assertCurrentSession(state);
    return {...publicResult(result), protocol_version: PROTOCOL, extension_version: EXTENSION_VERSION};
  } catch (error) {
    await restorePairingReservation(reservation);
    throw error;
  }
}

function externalBindingMatches(message, senderOrigin, state) {
  const binding = message?.binding;
  return Boolean(
    state && binding && binding.base_url === senderOrigin &&
    binding.base_url === state.base_url && binding.assist_path === state.assist_path
  );
}

async function sha256(value) {
  const bytes = new TextEncoder().encode(String(value));
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
  return `sha256:${Array.from(digest, (item) => item.toString(16).padStart(2, "0")).join("")}`;
}

async function pageObservationHash(tabUrl, payload) {
  const parsed = new URL(tabUrl);
  return await sha256(JSON.stringify({
    origin: parsed.origin,
    path: parsed.pathname,
    sanitized_html: String(payload?.sanitized_html || "")
  }));
}

async function postJSON(url, body = {}, timeoutMs = 15000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let response;
  try {
    response = await fetch(url, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body),
      cache: "no-store",
      credentials: "omit",
      redirect: "error",
      signal: controller.signal
    });
  } finally {
    clearTimeout(timer);
  }
  const value = await response.json().catch(() => ({
    status: "BLOCKED", code: "COMPANION_RESPONSE_INVALID", message: "Invalid local response."
  }));
  if (!response.ok) {
    const error = new Error(value.message || value.code || "JobFlow blocked the request.");
    error.jobflow = value;
    throw error;
  }
  return value;
}

async function notifyJobFlow(result, messageType = "JOBFLOW_ASSIST_STATUS", snapshot = null) {
  let state = snapshot ? await assertCurrentSession(snapshot) : await sessionState();
  if (!state?.base_url) return;
  const tabs = await chrome.tabs.query({url: ["http://127.0.0.1/*", "http://localhost/*"]});
  state = snapshot ? await assertCurrentSession(snapshot) : await sessionState();
  if (!state?.base_url) return;
  const boundTabs = tabs.filter((tab) => {
    if (!tab.id || loopbackOrigin(tab.url || "") !== state.base_url) return false;
    return !Number.isInteger(state.jobflow_tab_id) || tab.id === state.jobflow_tab_id;
  });
  if (snapshot) await assertCurrentSession(snapshot);
  const safeResult = publicResult(result);
  await Promise.all(boundTabs.map((tab) => (
    chrome.tabs.sendMessage(tab.id, {type: messageType, result: safeResult}).catch(() => undefined)
  )));
}

function publicError(error) {
  const value = error && error.jobflow;
  if (value && typeof value === "object") return {...publicResult(value), protocol_version: PROTOCOL, extension_version: EXTENSION_VERSION};
  return {status: "BLOCKED", code: "COMPANION_LOCAL_ERROR", message: String(error?.message || error || "Unknown error"), protocol_version: PROTOCOL, extension_version: EXTENSION_VERSION};
}

async function ensureDOMScript(tabId) {
  const results = await chrome.scripting.executeScript({target: {tabId}, files: ["dom.js"]});
  const topFrame = Array.isArray(results) ? results.find((item) => item?.frameId === 0) : null;
  return typeof topFrame?.documentId === "string" && topFrame.documentId ? topFrame.documentId : null;
}

async function captureGuidedCurrentTab(tabId, tabUrl) {
  let state = await sessionState();
  if (!state || state.mode !== "JOB_CAPTURE" || !state.paired) {
    throw Object.assign(new Error("Start guided job import in JobFlow first."), {
      jobflow: {status: "BLOCKED", code: "GUIDED_INTAKE_NOT_PAIRED"}
    });
  }
  const parsed = new URL(tabUrl);
  if (parsed.protocol !== "https:") {
    throw Object.assign(new Error("Open the HTTPS company job page or its application form."), {
      jobflow: {status: "BLOCKED", code: "GUIDED_INTAKE_HTTPS_REQUIRED"}
    });
  }
  await ensureDOMScript(tabId);
  state = await assertCurrentSession(state);
  if (state.stage === "AWAITING_JOB_PAGE_CAPTURE") {
    const collected = await chrome.tabs.sendMessage(tabId, {type: "JOBFLOW_COLLECT_JOB_PAGE"});
    state = await assertCurrentSession(state);
    if (!collected || collected.status !== "COLLECTED") {
      throw Object.assign(new Error("The company job page could not be read safely."), {
        jobflow: collected || {status: "BLOCKED", code: "GUIDED_INTAKE_JOB_PAGE_UNAVAILABLE"}
      });
    }
    const result = await postJSON(endpoint(state, "/capture-job"), collected.payload);
    state = await assertCurrentSession(state);
    const next = await saveSessionCAS(state, {...state, stage: result.status, last_result: result});
    await notifyJobFlow(result, "JOBFLOW_INTAKE_STATUS", next);
    return result;
  }
  if (["AWAITING_APPLICATION_FORM_CAPTURE", "FORM_CAPTURE_FAILED"].includes(state.stage)) {
    const collected = await chrome.tabs.sendMessage(tabId, {type: "JOBFLOW_COLLECT_FORM"});
    state = await assertCurrentSession(state);
    if (!collected || collected.status !== "COLLECTED") {
      throw Object.assign(new Error("The application form could not be read safely."), {
        jobflow: collected || {status: "BLOCKED", code: "GUIDED_INTAKE_FORM_UNAVAILABLE"}
      });
    }
    await notifyJobFlow({
      status: "PREPARING_APPLICATION", intake_id: state.intake_id,
      mode: "JOB_CAPTURE", real_external_actions: 0
    }, "JOBFLOW_INTAKE_STATUS", state);
    state = await assertCurrentSession(state);
    let result;
    try {
      result = await postJSON(endpoint(state, "/capture-form"), {
        ...collected.payload, companion_tab_id: tabId
      });
      state = await assertCurrentSession(state);
    } catch (error) {
      state = await assertCurrentSession(state);
      const failure = {
        ...publicError(error), status: "FORM_CAPTURE_FAILED",
        intake_id: state.intake_id, mode: "JOB_CAPTURE", real_external_actions: 0
      };
      const failed = await saveSessionCAS(state, {...state, stage: failure.status, last_result: failure});
      await notifyJobFlow(failure, "JOBFLOW_INTAKE_STATUS", failed);
      throw error;
    }
    const next = await saveSessionCAS(state, {...state, stage: result.status, application_id: result.application_id, last_result: result});
    await notifyJobFlow(result, "JOBFLOW_INTAKE_STATUS", next);
    return result;
  }
  throw Object.assign(new Error("This guided import is not waiting for another page."), {
    jobflow: {status: "BLOCKED", code: "GUIDED_INTAKE_STAGE_INVALID"}
  });
}

async function observeResult(tabId) {
  let state = await sessionState();
  if (!state || state.tab_id !== tabId || !state.submission_observed || state.result_final) return;
  try {
    await ensureDOMScript(tabId);
    state = await assertCurrentSession(state, {tabId});
    await chrome.tabs.sendMessage(tabId, {type: "JOBFLOW_COLLECT_RESULT"});
    await assertCurrentSession(state, {tabId});
  } catch (_error) {
    try {
      state = await assertCurrentSession(state, {tabId});
      const result = await postJSON(endpoint(state, "/result-unavailable"), {reason: "PAGE_UNAVAILABLE"});
      state = await assertCurrentSession(state, {tabId});
      const next = await saveSessionCAS(state, {...state, stage: result.status, result_final: true, last_result: result});
      await notifyJobFlow(result, "JOBFLOW_ASSIST_STATUS", next);
    } catch (_ignored) {
      // A concurrent observer may already have finalized this one-use result state.
    }
  }
}

async function navigateCurrentTab(tabId, suppliedState = null) {
  let state = suppliedState || await sessionState();
  if (!state?.navigation || !state.tab_id || state.tab_id !== tabId) {
    throw Object.assign(new Error("No approved Next/Continue action is ready."), {
      jobflow: {status: "BLOCKED", code: "COMPANION_NAVIGATION_NOT_READY"}
    });
  }
  await ensureDOMScript(tabId);
  state = await assertCurrentSession(state, {tabId});
  if (state.stage !== "AWAITING_NAVIGATION") {
    const checked = await chrome.tabs.sendMessage(tabId, {
      type: "JOBFLOW_CHECK_NAVIGATION", client_ref: state.navigation.client_ref
    });
    state = await assertCurrentSession(state, {tabId});
    if (!checked || checked.status !== "NAVIGATION_VALID" || checked.form_valid !== true) {
      throw Object.assign(new Error("Complete all required fields on this page before continuing."), {
        jobflow: checked || {status: "BLOCKED", code: "COMPANION_REQUIRED_FIELDS_INCOMPLETE"}
      });
    }
    const authorized = await postJSON(endpoint(state, "/authorize-navigation"), {
      client_ref: state.navigation.client_ref,
      authorization_token: state.navigation.authorization_token,
      form_valid: true,
      submit_events: 0,
      page_content_hash: checked.page_content_hash,
      control_semantics_hash: checked.control_semantics_hash
    });
    state = await assertCurrentSession(state, {tabId});
    state = {
      ...state,
      stage: "AWAITING_NAVIGATION",
      navigation_authorized: true,
      navigation_started_at: Date.now(),
      navigation_poll_count: 0,
      authorized_navigation_proof: {
        page_content_hash: authorized.page_content_hash,
        control_semantics_hash: authorized.control_semantics_hash
      },
      prior_page_observation_hash: state.current_page_observation_hash || null,
      last_result: authorized
    };
    state = await saveSessionCAS(await assertCurrentSession(state), state);
    await notifyJobFlow(authorized, "JOBFLOW_ASSIST_STATUS", state);
  }
  const started = await chrome.tabs.sendMessage(tabId, {
    type: "JOBFLOW_NAVIGATE_APPROVED", client_ref: state.navigation.client_ref,
    page_content_hash: state.authorized_navigation_proof?.page_content_hash,
    control_semantics_hash: state.authorized_navigation_proof?.control_semantics_hash
  });
  await assertCurrentSession(state, {tabId});
  if (!started || started.status !== "NAVIGATION_STARTED" || started.final_submit !== false) {
    throw Object.assign(new Error("The approved Next/Continue control could not be activated."), {
      jobflow: started || {status: "BLOCKED", code: "COMPANION_NAVIGATION_FAILED"}
    });
  }
  chrome.alarms.create(NAVIGATION_ALARM, {delayInMinutes: 0.03});
  return started;
}

async function failManualChallengeArm(state, tabId, detailCode) {
  state = await assertCurrentSession(state);
  const failure = {
    status: "MANUAL_NAVIGATION_RESTART_REQUIRED",
    code: "COMPANION_MANUAL_NAVIGATION_RESTART_REQUIRED",
    message: "Return to JobFlow and restart this application assist; the prior one-use Next proof was not armed.",
    detail_code: detailCode,
    automatic_retry: false
  };
  const failed = await saveSessionCAS(state, {
    ...state, tab_id: tabId, stage: failure.status,
    navigation: null, manual_navigation: null, manual_navigation_evidence: null,
    manual_navigation_browser_document_id: null, manual_navigation_resume_failed: true,
    authorized_navigation_proof: null, navigation_authorized: false, last_result: failure
  });
  await notifyJobFlow(failure, "JOBFLOW_ASSIST_STATUS", failed).catch(() => undefined);
  throw Object.assign(new Error(failure.message), {jobflow: failure});
}

async function fillCurrentTab(tabId, tabUrl) {
  let state = await sessionState();
  if (!state || state.protocol_version !== PROTOCOL || !state.paired) {
    throw Object.assign(new Error("Pair the companion from JobFlow first."), {
      jobflow: {status: "BLOCKED", code: "COMPANION_NOT_PAIRED"}
    });
  }
  if (!sameApprovedOrigin(tabUrl, state)) {
    throw Object.assign(new Error("Return to the approved company/ATS application tab."), {
      jobflow: {status: "BLOCKED", code: "COMPANION_WRONG_TAB"}
    });
  }
  if (state.stage === "AWAITING_USER_SUBMIT") {
    throw Object.assign(new Error("Final review is locked for the user's submit decision."), {
      jobflow: {status: "BLOCKED", code: "COMPANION_FINAL_REVIEW_LOCKED", automatic_retry: false}
    });
  }
  if (state.stage === "MANUAL_NAVIGATION_RESTART_REQUIRED") {
    throw Object.assign(new Error("Return to JobFlow and restart this application assist."), {
      jobflow: {
        status: "BLOCKED", code: "COMPANION_MANUAL_NAVIGATION_RESTART_REQUIRED",
        message: "Return to JobFlow and restart this application assist.", automatic_retry: false
      }
    });
  }
  const browserDocumentId = await ensureDOMScript(tabId);
  state = await assertCurrentSession(state);
  const collected = await chrome.tabs.sendMessage(tabId, {type: "JOBFLOW_COLLECT_FORM"});
  state = await assertCurrentSession(state);
  if (!collected || collected.status !== "COLLECTED") {
    throw Object.assign(new Error("The current form could not be read safely."), {
      jobflow: collected || {status: "BLOCKED", code: "COMPANION_FORM_UNAVAILABLE"}
    });
  }
  if (!/^DOC-[A-F0-9]{32}$/.test(String(collected.payload?.document_instance_id || ""))) {
    throw Object.assign(new Error("The current document instance could not be bound safely."), {
      jobflow: {status: "BLOCKED", code: "COMPANION_DOCUMENT_BINDING_INVALID", automatic_retry: false}
    });
  }
  const formPayload = {...collected.payload, companion_tab_id: tabId};
  if (state.stage === "MANUAL_NAVIGATION_REQUIRED") {
    const evidence = state.manual_navigation_evidence;
    if (state.manual_navigation_resume_failed === true) {
      throw Object.assign(new Error("Restart this browser assist before trying manual navigation again."), {
        jobflow: {status: "BLOCKED", code: "COMPANION_MANUAL_NAVIGATION_RESTART_REQUIRED", automatic_retry: false}
      });
    }
    if (!evidence || evidence.trusted_user_event !== true) {
      throw Object.assign(new Error("Click the highlighted Next/Continue yourself, then open JobFlow J on the changed page."), {
        jobflow: {status: "BLOCKED", code: "COMPANION_MANUAL_NAVIGATION_NOT_OBSERVED", automatic_retry: false}
      });
    }
    let resumed;
    try {
      resumed = await postJSON(endpoint(state, "/resume-manual-navigation"), {
        ...formPayload, ...evidence
      });
    } catch (error) {
      state = await assertCurrentSession(state, {tabId});
      await saveSessionCAS(state, {
        ...state, manual_navigation_resume_failed: true, last_result: publicError(error)
      });
      throw error;
    }
    state = await assertCurrentSession(state, {tabId});
    if (resumed.status !== "NEXT_PAGE_READY") {
      await saveSessionCAS(state, {
        ...state, manual_navigation_resume_failed: true,
        last_result: {status: "BLOCKED", code: "COMPANION_MANUAL_NAVIGATION_RESUME_FAILED", automatic_retry: false}
      });
      throw Object.assign(new Error("The manually opened next page could not be rebound safely."), {
        jobflow: resumed || {status: "BLOCKED", code: "COMPANION_MANUAL_NAVIGATION_RESUME_FAILED"}
      });
    }
    state = await saveSessionCAS(state, {
      ...state,
      stage: "READY",
      current_step: resumed.current_step,
      navigation: null,
      manual_navigation: null,
      manual_navigation_evidence: null,
      manual_navigation_browser_document_id: null,
      manual_navigation_resume_failed: false,
      authorized_navigation_proof: null,
      current_page_observation_hash: resumed.next_page_content_hash,
      last_result: resumed
    });
    await notifyJobFlow(resumed, "JOBFLOW_ASSIST_STATUS", state);
  }
  const prepared = await postJSON(endpoint(state, "/prepare"), formPayload);
  state = await assertCurrentSession(state);
  if (prepared.status === "HANDOFF_REQUIRED") {
    const handoffState = {
      ...state, tab_id: tabId, stage: prepared.status, handoff_kind: prepared.handoff_kind,
      navigation: null, last_result: prepared
    };
    const next = await saveSessionCAS(state, handoffState);
    await notifyJobFlow(prepared, "JOBFLOW_ASSIST_STATUS", next);
    return prepared;
  }
  const applied = await chrome.tabs.sendMessage(tabId, {
    type: "JOBFLOW_APPLY_APPROVED",
    fields: prepared.fields,
    files: prepared.files.map((item) => ({
      ...item,
      download_url: `${state.base_url}${item.download_path}`
    })),
    navigation: prepared.navigation,
    final_submit_client_refs: prepared.final_submit_client_refs
  });
  state = await assertCurrentSession(state);
  if (!applied || applied.status !== "APPLIED") {
    throw Object.assign(new Error("The browser could not apply every approved field and material."), {
      jobflow: applied || {status: "BLOCKED", code: "COMPANION_APPLY_INCOMPLETE"}
    });
  }
  const completed = await postJSON(endpoint(state, "/complete"), {
    field_bindings: applied.field_bindings,
    material_bindings: applied.material_bindings,
    submit_events: 0,
    navigation_actions: 0
  });
  state = await assertCurrentSession(state);
  let manualBrowserDocumentId = null;
  if (completed.status === "MANUAL_NAVIGATION_REQUIRED") {
    const manualNavigation = completed.manual_navigation;
    const challenge = manualNavigation?.challenge;
    if (!browserDocumentId || !validManualChallenge(
      challenge, manualNavigation, state, tabId, String(collected.payload.document_instance_id)
    )) {
      return await failManualChallengeArm(state, tabId, "COMPANION_MANUAL_CHALLENGE_INVALID");
    }
    let armed;
    try {
      armed = await chrome.tabs.sendMessage(tabId, {
        type: "JOBFLOW_ARM_MANUAL_NAVIGATION", challenge
      });
    } catch (_error) {
      return await failManualChallengeArm(state, tabId, "COMPANION_MANUAL_CHALLENGE_ARM_FAILED");
    }
    state = await assertCurrentSession(state);
    if (
      !armed || armed.status !== "MANUAL_NAVIGATION_ARMED" ||
      armed.challenge_id !== challenge.challenge_id ||
      armed.document_instance_id !== challenge.document_instance_id ||
      armed.expires_at !== challenge.expires_at
    ) {
      return await failManualChallengeArm(state, tabId, "COMPANION_MANUAL_CHALLENGE_ARM_FAILED");
    }
    manualBrowserDocumentId = browserDocumentId;
  }
  const observationHash = await pageObservationHash(tabUrl, collected.payload);
  state = await assertCurrentSession(state);
  const next = {
    ...state,
    tab_id: tabId,
    stage: completed.status,
    current_step: completed.current_step,
    handoff_kind: null,
    navigation: completed.navigation || null,
    manual_navigation: completed.manual_navigation || null,
    manual_navigation_evidence: null,
    manual_navigation_browser_document_id: manualBrowserDocumentId,
    manual_navigation_resume_failed: false,
    authorized_navigation_proof: null,
    navigation_authorized: false,
    manual_field_count: Number(completed.manual_field_count || 0),
    current_page_observation_hash: observationHash,
    submission_observed: false,
    result_final: false,
    last_result: completed
  };
  const saved = await saveSessionCAS(state, next);
  await notifyJobFlow(completed, "JOBFLOW_ASSIST_STATUS", saved);
  if (completed.status === "PAGE_REVIEW_REQUIRED" && saved.manual_field_count === 0) {
    return await navigateCurrentTab(tabId, saved);
  }
  return completed;
}

async function observeNavigation(tabId, tabUrl) {
  if (navigationObservationInFlight) return;
  navigationObservationInFlight = true;
  try {
    let state = await sessionState();
    if (!state || state.stage !== "AWAITING_NAVIGATION" || state.tab_id !== tabId) return;
    if (!sameApprovedOrigin(tabUrl, state)) {
      throw Object.assign(new Error("Next/Continue left the approved application origin."), {
        jobflow: {status: "BLOCKED", code: "COMPANION_NAVIGATION_ORIGIN_CHANGED"}
      });
    }
    await ensureDOMScript(tabId);
    state = await assertCurrentSession(state, {tabId});
    const collected = await chrome.tabs.sendMessage(tabId, {type: "JOBFLOW_COLLECT_FORM"});
    state = await assertCurrentSession(state, {tabId});
    if (!collected || collected.status !== "COLLECTED") {
      throw Object.assign(new Error("The next application page is not ready."), {
        jobflow: {status: "BLOCKED", code: "COMPANION_NEXT_PAGE_NOT_READY"}
      });
    }
    const observedPageHash = await pageObservationHash(tabUrl, collected.payload);
    state = await assertCurrentSession(state, {tabId});
    if (state.prior_page_observation_hash && observedPageHash === state.prior_page_observation_hash) {
      const elapsed = Date.now() - Number(state.navigation_started_at || Date.now());
      const pollCount = Number(state.navigation_poll_count || 0) + 1;
      if (elapsed < NAVIGATION_SETTLE_MS) {
        await saveSessionCAS(state, {...state, navigation_poll_count: pollCount});
        chrome.alarms.create(NAVIGATION_ALARM, {delayInMinutes: 0.05});
        return {status: "NAVIGATION_PENDING", poll_count: pollCount};
      }
      const stalled = {
        status: "NAVIGATION_STALLED",
        code: "COMPANION_NAVIGATION_STALLED",
        application_id: state.application_id,
        current_step: state.current_step,
        automatic_retry: false
      };
      const next = await saveSessionCAS(state, {...state, last_result: stalled, navigation_poll_count: pollCount});
      await notifyJobFlow(stalled, "JOBFLOW_ASSIST_STATUS", next);
      return stalled;
    }
    const eventHash = await sha256(JSON.stringify({
      origin: new URL(tabUrl).origin,
      path: new URL(tabUrl).pathname,
      prior_step: state.current_step,
      page_observation_hash: observedPageHash,
      time_bucket: Math.floor(Date.now() / 1000)
    }));
    const observed = await postJSON(endpoint(state, "/navigation-observed"), {
      url: tabUrl, event_hash: eventHash
    });
    state = await assertCurrentSession(state, {tabId});
    const next = {
      ...state, stage: observed.status, current_step: observed.current_step,
      navigation: null, navigation_authorized: false, manual_field_count: 0,
      manual_navigation: null, manual_navigation_evidence: null, authorized_navigation_proof: null,
      current_page_observation_hash: observedPageHash,
      last_result: observed
    };
    const saved = await saveSessionCAS(state, next);
    await notifyJobFlow(observed, "JOBFLOW_ASSIST_STATUS", saved);
    return await fillCurrentTab(tabId, tabUrl);
  } finally {
    navigationObservationInFlight = false;
  }
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  (async () => {
    if (!message || typeof message.type !== "string") return {status: "IGNORED"};
    if (message.type === "JOBFLOW_PAIR") {
      const senderOrigin = loopbackOrigin(sender?.tab?.url || sender?.url || "");
      return await pairWithJobFlow(message.pairing, senderOrigin, sender?.tab?.id);
    }
    if (message.type === "JOBFLOW_GET_STATUS") {
      return publicSessionStatus(await sessionState());
    }
    if (message.type === "JOBFLOW_FILL_CURRENT") {
      const state = await sessionState();
      if (state?.mode === "JOB_CAPTURE") return {status: "BLOCKED", code: "GUIDED_INTAKE_CAPTURE_REQUIRED"};
      return await fillCurrentTab(Number(message.tab_id), String(message.tab_url || ""));
    }
    if (message.type === "JOBFLOW_CAPTURE_CURRENT") {
      return await captureGuidedCurrentTab(Number(message.tab_id), String(message.tab_url || ""));
    }
    if (message.type === "JOBFLOW_CONTINUE_CURRENT") {
      return await navigateCurrentTab(Number(message.tab_id));
    }
    if (message.type === "JOBFLOW_MANUAL_NAVIGATION_OBSERVED") {
      let state = await sessionState();
      const payload = message.payload;
      const manualNavigation = state?.manual_navigation;
      const challenge = manualNavigation?.challenge;
      if (
        !state || state.mode !== "APPLICATION_ASSIST" || state.stage !== "MANUAL_NAVIGATION_REQUIRED" ||
        !manualNavigation || !challenge || !sender.tab?.id || sender.tab.id !== state.tab_id ||
        state.manual_navigation_resume_failed === true
      ) {
        return {status: "BLOCKED", code: "COMPANION_MANUAL_NAVIGATION_BINDING_INVALID", automatic_retry: false};
      }
      if (state.manual_navigation_evidence) {
        return {status: "BLOCKED", code: "COMPANION_MANUAL_NAVIGATION_REPLAYED", automatic_retry: false};
      }
      if (
        !exactKeys(payload, MANUAL_EVIDENCE_FIELDS) || payload.trusted_user_event !== true ||
        payload.manual_navigation_default_prevented !== false ||
        !sameApprovedOrigin(String(payload.url || ""), state) ||
        !sameApprovedOrigin(String(sender.tab.url || ""), state) ||
        typeof sender.documentId !== "string" || !sender.documentId ||
        sender.documentId !== state.manual_navigation_browser_document_id ||
        Date.parse(String(challenge.expires_at || "")) <= Date.now() ||
        payload.manual_navigation_challenge_id !== challenge.challenge_id ||
        payload.manual_navigation_nonce !== challenge.nonce ||
        payload.manual_navigation_challenge_hash !== challenge.challenge_hash ||
        payload.manual_navigation_assist_id !== state.assist_id ||
        payload.manual_navigation_assist_id !== challenge.assist_id ||
        payload.manual_navigation_application_id !== state.application_id ||
        payload.manual_navigation_application_id !== challenge.application_id ||
        payload.manual_navigation_tab_id !== sender.tab.id ||
        payload.manual_navigation_tab_id !== challenge.tab_id ||
        payload.manual_navigation_document_id !== challenge.document_instance_id ||
        payload.manual_navigation_stage !== "MANUAL_NAVIGATION_REQUIRED" ||
        payload.manual_navigation_stage !== challenge.stage ||
        payload.manual_navigation_client_ref !== manualNavigation.client_ref ||
        payload.manual_navigation_client_ref !== challenge.client_ref ||
        payload.prior_page_content_hash !== manualNavigation.prior_page_content_hash ||
        payload.prior_page_content_hash !== challenge.prior_page_content_hash ||
        payload.control_semantics_hash !== manualNavigation.control_semantics_hash ||
        payload.control_semantics_hash !== challenge.control_semantics_hash ||
        !/^sha256:[a-f0-9]{64}$/.test(String(payload.event_hash || "")) ||
        payload.event_hash !== await manualEventHash(challenge)
      ) {
        return {status: "BLOCKED", code: "COMPANION_MANUAL_NAVIGATION_EVIDENCE_INVALID", automatic_retry: false};
      }
      state = await assertCurrentSession(state, {tabId: sender.tab.id, stages: ["MANUAL_NAVIGATION_REQUIRED"]});
      const next = await saveSessionCAS(state, {
        ...state,
        manual_navigation_evidence: {
          trusted_user_event: true,
          event_hash: String(payload.event_hash),
          prior_page_content_hash: String(payload.prior_page_content_hash),
          control_semantics_hash: String(payload.control_semantics_hash),
          manual_navigation_challenge_id: String(payload.manual_navigation_challenge_id),
          manual_navigation_nonce: String(payload.manual_navigation_nonce),
          manual_navigation_challenge_hash: String(payload.manual_navigation_challenge_hash),
          manual_navigation_assist_id: String(payload.manual_navigation_assist_id),
          manual_navigation_application_id: String(payload.manual_navigation_application_id),
          manual_navigation_tab_id: payload.manual_navigation_tab_id,
          manual_navigation_document_id: String(payload.manual_navigation_document_id),
          manual_navigation_stage: String(payload.manual_navigation_stage),
          manual_navigation_client_ref: String(payload.manual_navigation_client_ref),
          manual_navigation_default_prevented: false
        }
      });
      return {
        status: "MANUAL_NAVIGATION_RECORDED",
        assist_id: next.assist_id,
        application_id: next.application_id,
        automatic_retry: false
      };
    }
    if (message.type === "JOBFLOW_USER_SUBMIT_OBSERVED") {
      let state = await sessionState();
      if (!state || state.result_final || state.stage !== "AWAITING_USER_SUBMIT") {
        return {status: "IGNORED"};
      }
      if (!sender.tab?.id || sender.tab.id !== state.tab_id) {
        return {status: "BLOCKED", code: "COMPANION_TAB_BINDING_CHANGED"};
      }
      const observed = await postJSON(endpoint(state, "/submit-observed"), message.payload);
      state = await assertCurrentSession(state, {tabId: sender.tab.id, stages: ["AWAITING_USER_SUBMIT"]});
      const next = await saveSessionCAS(
        state,
        {...state, submission_observed: true, stage: observed.status, last_result: observed},
        {allowSubmitTransition: true}
      );
      chrome.alarms.create(RESULT_ALARM, {delayInMinutes: 0.05});
      await notifyJobFlow(observed, "JOBFLOW_ASSIST_STATUS", next);
      return observed;
    }
    if (message.type === "JOBFLOW_RESULT_SIGNALS") {
      let state = await sessionState();
      if (!state || !state.submission_observed || state.result_final) return {status: "IGNORED"};
      if (!sender.tab?.id || sender.tab.id !== state.tab_id) {
        return {status: "BLOCKED", code: "COMPANION_TAB_BINDING_CHANGED"};
      }
      const result = await postJSON(endpoint(state, "/observe-result"), message.payload);
      state = await assertCurrentSession(state, {tabId: sender.tab.id});
      const final = ["CONFIRMED", "SUBMISSION_UNKNOWN", "AWAITING_APPROVAL"].includes(result.status);
      const next = await saveSessionCAS(state, {...state, stage: result.status, result_final: final, last_result: result});
      await notifyJobFlow(result, "JOBFLOW_ASSIST_STATUS", next);
      return result;
    }
    return {status: "IGNORED"};
  })().then(sendResponse).catch((error) => sendResponse(publicError(error)));
  return true;
});

chrome.runtime.onMessageExternal.addListener((message, sender, sendResponse) => {
  (async () => {
    const senderOrigin = loopbackOrigin(sender?.url || sender?.origin || "");
    if (!senderOrigin || !message || typeof message.type !== "string") {
      return {status: "BLOCKED", code: "COMPANION_EXTERNAL_SENDER_FORBIDDEN", protocol_version: PROTOCOL, extension_version: EXTENSION_VERSION};
    }
    if (message.type === "JOBFLOW_PING") {
      return {status: "AVAILABLE", protocol_version: PROTOCOL, extension_version: EXTENSION_VERSION};
    }
    if (message.type === "JOBFLOW_PAIR") {
      return {
        status: "BLOCKED", code: "COMPANION_EXTERNAL_PAIR_FORBIDDEN",
        message: "Open the JobFlow companion and connect from its popup.",
        protocol_version: PROTOCOL, extension_version: EXTENSION_VERSION
      };
    }
    if (message.type === "JOBFLOW_GET_STATUS") {
      const state = await sessionState();
      if (!externalBindingMatches(message, senderOrigin, state)) {
        return {status: "BLOCKED", code: "COMPANION_STATUS_BINDING_INVALID", protocol_version: PROTOCOL, extension_version: EXTENSION_VERSION};
      }
      return publicSessionStatus(state);
    }
    return {status: "BLOCKED", code: "COMPANION_EXTERNAL_ACTION_FORBIDDEN", protocol_version: PROTOCOL, extension_version: EXTENSION_VERSION};
  })().then(sendResponse).catch((error) => sendResponse(publicError(error)));
  return true;
});

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== "jobflow-file-stream") return;
  port.onMessage.addListener(async (message) => {
    let state = await sessionState();
    if (!state || !sameAssistURL(String(message?.url || ""), state)) {
      port.postMessage({type: "error", code: "COMPANION_FILE_URL_FORBIDDEN"});
      port.disconnect();
      return;
    }
    try {
      const response = await fetch(message.url, {cache: "no-store", credentials: "omit", redirect: "error"});
      state = await assertCurrentSession(state);
      if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);
      const reader = response.body.getReader();
      while (true) {
        const {done, value} = await reader.read();
        state = await assertCurrentSession(state);
        if (done) break;
        let binary = "";
        for (let offset = 0; offset < value.length; offset += 0x8000) {
          binary += String.fromCharCode(...value.subarray(offset, Math.min(offset + 0x8000, value.length)));
        }
        port.postMessage({type: "chunk", data: btoa(binary)});
      }
      port.postMessage({type: "end"});
    } catch (_error) {
      port.postMessage({type: "error", code: "COMPANION_FILE_STREAM_FAILED"});
    }
  });
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.status !== "complete") return;
  (async () => {
    const state = await sessionState();
    if (!state || state.tab_id !== tabId) return;
    if (state?.submission_observed) return observeResult(tabId);
    if (state?.stage === "AWAITING_NAVIGATION" && tab.url) return observeNavigation(tabId, tab.url);
  })().catch(async (error) => {
    const state = await sessionState();
    if (state?.tab_id === tabId) await notifyJobFlow(publicError(error), "JOBFLOW_ASSIST_STATUS", state).catch(() => undefined);
  });
});

chrome.alarms.onAlarm.addListener(async (alarm) => {
  const state = await sessionState();
  if (alarm.name === RESULT_ALARM && state?.tab_id) await observeResult(state.tab_id);
  if (alarm.name === NAVIGATION_ALARM && state?.tab_id) {
    const tab = await chrome.tabs.get(state.tab_id).catch(() => null);
    const current = await assertCurrentSession(state, {tabId: state.tab_id}).catch(() => null);
    if (!current) return;
    if (tab?.url && tab.status === "complete") {
      await observeNavigation(state.tab_id, tab.url).catch(async (error) => {
        const current = await sessionState();
        if (sameGeneration(current, state)) {
          await notifyJobFlow(publicError(error), "JOBFLOW_ASSIST_STATUS", current).catch(() => undefined);
        }
      });
    } else if (tab) {
      chrome.alarms.create(NAVIGATION_ALARM, {delayInMinutes: 0.05});
    }
  }
});
