"use strict";

const PROTOCOL = 2;
const SESSION_KEY = "jobflowAssist";
const RESULT_ALARM = "jobflow-result-observer";
const NAVIGATION_ALARM = "jobflow-navigation-observer";
const NAVIGATION_SETTLE_MS = 20000;
let navigationObservationInFlight = false;

async function sessionState() {
  const value = await chrome.storage.session.get(SESSION_KEY);
  return value[SESSION_KEY] || null;
}

async function saveSession(value) {
  await chrome.storage.session.set({[SESSION_KEY]: value});
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

async function postJSON(url, body = {}) {
  const response = await fetch(url, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body),
    cache: "no-store",
    credentials: "omit",
    redirect: "error"
  });
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

async function notifyJobFlow(result, messageType = "JOBFLOW_ASSIST_STATUS") {
  const tabs = await chrome.tabs.query({url: ["http://127.0.0.1/*", "http://localhost/*"]});
  await Promise.all(tabs.map((tab) => (
    tab.id ? chrome.tabs.sendMessage(tab.id, {type: messageType, result}).catch(() => undefined) : undefined
  )));
}

function publicError(error) {
  const value = error && error.jobflow;
  if (value && typeof value === "object") return value;
  return {status: "BLOCKED", code: "COMPANION_LOCAL_ERROR", message: String(error?.message || error || "Unknown error")};
}

async function ensureDOMScript(tabId) {
  await chrome.scripting.executeScript({target: {tabId}, files: ["dom.js"]});
}

async function captureGuidedCurrentTab(tabId, tabUrl) {
  const state = await sessionState();
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
  if (state.stage === "AWAITING_JOB_PAGE_CAPTURE") {
    const collected = await chrome.tabs.sendMessage(tabId, {type: "JOBFLOW_COLLECT_JOB_PAGE"});
    if (!collected || collected.status !== "COLLECTED") {
      throw Object.assign(new Error("The company job page could not be read safely."), {
        jobflow: collected || {status: "BLOCKED", code: "GUIDED_INTAKE_JOB_PAGE_UNAVAILABLE"}
      });
    }
    const result = await postJSON(endpoint(state, "/capture-job"), collected.payload);
    await saveSession({...state, stage: result.status, last_result: result});
    await notifyJobFlow(result, "JOBFLOW_INTAKE_STATUS");
    return result;
  }
  if (["AWAITING_APPLICATION_FORM_CAPTURE", "FORM_CAPTURE_FAILED"].includes(state.stage)) {
    const collected = await chrome.tabs.sendMessage(tabId, {type: "JOBFLOW_COLLECT_FORM"});
    if (!collected || collected.status !== "COLLECTED") {
      throw Object.assign(new Error("The application form could not be read safely."), {
        jobflow: collected || {status: "BLOCKED", code: "GUIDED_INTAKE_FORM_UNAVAILABLE"}
      });
    }
    await notifyJobFlow({
      status: "PREPARING_APPLICATION", intake_id: state.intake_id,
      mode: "JOB_CAPTURE", real_external_actions: 0
    }, "JOBFLOW_INTAKE_STATUS");
    let result;
    try {
      result = await postJSON(endpoint(state, "/capture-form"), collected.payload);
    } catch (error) {
      const failure = {
        ...publicError(error), status: "FORM_CAPTURE_FAILED",
        intake_id: state.intake_id, mode: "JOB_CAPTURE", real_external_actions: 0
      };
      await saveSession({...state, stage: failure.status, last_result: failure});
      await notifyJobFlow(failure, "JOBFLOW_INTAKE_STATUS");
      throw error;
    }
    await saveSession({...state, stage: result.status, application_id: result.application_id, last_result: result});
    await notifyJobFlow(result, "JOBFLOW_INTAKE_STATUS");
    return result;
  }
  throw Object.assign(new Error("This guided import is not waiting for another page."), {
    jobflow: {status: "BLOCKED", code: "GUIDED_INTAKE_STAGE_INVALID"}
  });
}

async function observeResult(tabId) {
  const state = await sessionState();
  if (!state || !state.submission_observed || state.result_final) return;
  try {
    await ensureDOMScript(tabId);
    await chrome.tabs.sendMessage(tabId, {type: "JOBFLOW_COLLECT_RESULT"});
  } catch (_error) {
    try {
      const result = await postJSON(endpoint(state, "/result-unavailable"), {reason: "PAGE_UNAVAILABLE"});
      await saveSession({...state, stage: result.status, result_final: true, last_result: result});
      await notifyJobFlow(result);
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
  if (state.stage !== "AWAITING_NAVIGATION") {
    const checked = await chrome.tabs.sendMessage(tabId, {
      type: "JOBFLOW_CHECK_NAVIGATION", client_ref: state.navigation.client_ref
    });
    if (!checked || checked.status !== "NAVIGATION_VALID" || checked.form_valid !== true) {
      throw Object.assign(new Error("Complete all required fields on this page before continuing."), {
        jobflow: checked || {status: "BLOCKED", code: "COMPANION_REQUIRED_FIELDS_INCOMPLETE"}
      });
    }
    const authorized = await postJSON(endpoint(state, "/authorize-navigation"), {
      client_ref: state.navigation.client_ref,
      authorization_token: state.navigation.authorization_token,
      form_valid: true,
      submit_events: 0
    });
    state = {
      ...state,
      stage: "AWAITING_NAVIGATION",
      navigation_authorized: true,
      navigation_started_at: Date.now(),
      navigation_poll_count: 0,
      prior_page_observation_hash: state.current_page_observation_hash || null,
      last_result: authorized
    };
    await saveSession(state);
    await notifyJobFlow(authorized);
  }
  const started = await chrome.tabs.sendMessage(tabId, {
    type: "JOBFLOW_NAVIGATE_APPROVED", client_ref: state.navigation.client_ref
  });
  if (!started || started.status !== "NAVIGATION_STARTED" || started.final_submit !== false) {
    throw Object.assign(new Error("The approved Next/Continue control could not be activated."), {
      jobflow: started || {status: "BLOCKED", code: "COMPANION_NAVIGATION_FAILED"}
    });
  }
  chrome.alarms.create(NAVIGATION_ALARM, {delayInMinutes: 0.03});
  return started;
}

async function fillCurrentTab(tabId, tabUrl) {
  const state = await sessionState();
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
  await ensureDOMScript(tabId);
  const collected = await chrome.tabs.sendMessage(tabId, {type: "JOBFLOW_COLLECT_FORM"});
  if (!collected || collected.status !== "COLLECTED") {
    throw Object.assign(new Error("The current form could not be read safely."), {
      jobflow: collected || {status: "BLOCKED", code: "COMPANION_FORM_UNAVAILABLE"}
    });
  }
  const prepared = await postJSON(endpoint(state, "/prepare"), collected.payload);
  if (prepared.status === "HANDOFF_REQUIRED") {
    const handoffState = {
      ...state, tab_id: tabId, stage: prepared.status, handoff_kind: prepared.handoff_kind,
      navigation: null, last_result: prepared
    };
    await saveSession(handoffState);
    await notifyJobFlow(prepared);
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
  const next = {
    ...state,
    tab_id: tabId,
    stage: completed.status,
    current_step: completed.current_step,
    handoff_kind: null,
    navigation: completed.navigation || null,
    navigation_authorized: false,
    manual_field_count: Number(completed.manual_field_count || 0),
    current_page_observation_hash: await pageObservationHash(tabUrl, collected.payload),
    submission_observed: false,
    result_final: false,
    last_result: completed
  };
  await saveSession(next);
  await notifyJobFlow(completed);
  if (completed.status === "PAGE_REVIEW_REQUIRED" && next.manual_field_count === 0) {
    return await navigateCurrentTab(tabId, next);
  }
  return completed;
}

async function observeNavigation(tabId, tabUrl) {
  if (navigationObservationInFlight) return;
  navigationObservationInFlight = true;
  try {
    const state = await sessionState();
    if (!state || state.stage !== "AWAITING_NAVIGATION" || state.tab_id !== tabId) return;
    if (!sameApprovedOrigin(tabUrl, state)) {
      throw Object.assign(new Error("Next/Continue left the approved application origin."), {
        jobflow: {status: "BLOCKED", code: "COMPANION_NAVIGATION_ORIGIN_CHANGED"}
      });
    }
    await ensureDOMScript(tabId);
    const collected = await chrome.tabs.sendMessage(tabId, {type: "JOBFLOW_COLLECT_FORM"});
    if (!collected || collected.status !== "COLLECTED") {
      throw Object.assign(new Error("The next application page is not ready."), {
        jobflow: {status: "BLOCKED", code: "COMPANION_NEXT_PAGE_NOT_READY"}
      });
    }
    const observedPageHash = await pageObservationHash(tabUrl, collected.payload);
    if (state.prior_page_observation_hash && observedPageHash === state.prior_page_observation_hash) {
      const elapsed = Date.now() - Number(state.navigation_started_at || Date.now());
      const pollCount = Number(state.navigation_poll_count || 0) + 1;
      if (elapsed < NAVIGATION_SETTLE_MS) {
        await saveSession({...state, navigation_poll_count: pollCount});
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
      await saveSession({...state, last_result: stalled, navigation_poll_count: pollCount});
      await notifyJobFlow(stalled);
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
    const next = {
      ...state, stage: observed.status, current_step: observed.current_step,
      navigation: null, navigation_authorized: false, manual_field_count: 0,
      current_page_observation_hash: observedPageHash,
      last_result: observed
    };
    await saveSession(next);
    await notifyJobFlow(observed);
    return await fillCurrentTab(tabId, tabUrl);
  } finally {
    navigationObservationInFlight = false;
  }
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  (async () => {
    if (!message || typeof message.type !== "string") return {status: "IGNORED"};
    if (message.type === "JOBFLOW_PAIR") {
      const pairing = message.pairing;
      if (
        !pairing || pairing.protocol_version !== PROTOCOL ||
        typeof pairing.base_url !== "string" || typeof pairing.assist_path !== "string" ||
        !/^http:\/\/(?:127\.0\.0\.1|localhost):\d+$/.test(pairing.base_url) ||
        !/^\/(?:assist|intake)\/[A-Za-z0-9_-]{40,}$/.test(pairing.assist_path)
      ) {
        return {status: "BLOCKED", code: "COMPANION_PAIR_PAYLOAD_INVALID"};
      }
      const result = await postJSON(`${pairing.base_url}${pairing.assist_path}/pair`, {});
      const state = {
        protocol_version: PROTOCOL,
        base_url: pairing.base_url,
        assist_path: pairing.assist_path,
        mode: result.mode || "APPLICATION_ASSIST",
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
        last_result: result
      };
      await saveSession(state);
      return result;
    }
    if (message.type === "JOBFLOW_GET_STATUS") {
      const state = await sessionState();
      return state ? {
        status: state.stage,
        paired: Boolean(state.paired),
        mode: state.mode,
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
        last_result: state.last_result
      } : {status: "NOT_PAIRED", paired: false};
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
    if (message.type === "JOBFLOW_USER_SUBMIT_OBSERVED") {
      const state = await sessionState();
      if (!state || state.result_final || state.stage !== "AWAITING_USER_SUBMIT") {
        return {status: "IGNORED"};
      }
      if (!sender.tab?.id || sender.tab.id !== state.tab_id) {
        return {status: "BLOCKED", code: "COMPANION_TAB_BINDING_CHANGED"};
      }
      const observed = await postJSON(endpoint(state, "/submit-observed"), message.payload);
      await saveSession({...state, submission_observed: true, stage: observed.status, last_result: observed});
      chrome.alarms.create(RESULT_ALARM, {delayInMinutes: 0.05});
      await notifyJobFlow(observed);
      return observed;
    }
    if (message.type === "JOBFLOW_RESULT_SIGNALS") {
      const state = await sessionState();
      if (!state || !state.submission_observed || state.result_final) return {status: "IGNORED"};
      if (!sender.tab?.id || sender.tab.id !== state.tab_id) {
        return {status: "BLOCKED", code: "COMPANION_TAB_BINDING_CHANGED"};
      }
      const result = await postJSON(endpoint(state, "/observe-result"), message.payload);
      const final = ["CONFIRMED", "SUBMISSION_UNKNOWN", "AWAITING_APPROVAL"].includes(result.status);
      await saveSession({...state, stage: result.status, result_final: final, last_result: result});
      await notifyJobFlow(result);
      return result;
    }
    return {status: "IGNORED"};
  })().then(sendResponse).catch((error) => sendResponse(publicError(error)));
  return true;
});

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== "jobflow-file-stream") return;
  port.onMessage.addListener(async (message) => {
    const state = await sessionState();
    if (!state || !sameAssistURL(String(message?.url || ""), state)) {
      port.postMessage({type: "error", code: "COMPANION_FILE_URL_FORBIDDEN"});
      port.disconnect();
      return;
    }
    try {
      const response = await fetch(message.url, {cache: "no-store", credentials: "omit", redirect: "error"});
      if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);
      const reader = response.body.getReader();
      while (true) {
        const {done, value} = await reader.read();
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
    if (state?.submission_observed) return observeResult(tabId);
    if (state?.stage === "AWAITING_NAVIGATION" && tab.url) return observeNavigation(tabId, tab.url);
  })().catch(async (error) => notifyJobFlow(publicError(error)));
});

chrome.alarms.onAlarm.addListener(async (alarm) => {
  const state = await sessionState();
  if (alarm.name === RESULT_ALARM && state?.tab_id) await observeResult(state.tab_id);
  if (alarm.name === NAVIGATION_ALARM && state?.tab_id) {
    const tab = await chrome.tabs.get(state.tab_id).catch(() => null);
    if (tab?.url && tab.status === "complete") {
      await observeNavigation(state.tab_id, tab.url).catch(async (error) => notifyJobFlow(publicError(error)));
    } else if (tab) {
      chrome.alarms.create(NAVIGATION_ALARM, {delayInMinutes: 0.05});
    }
  }
});
