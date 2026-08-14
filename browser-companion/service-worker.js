"use strict";

const PROTOCOL = 1;
const SESSION_KEY = "jobflowAssist";
const RESULT_ALARM = "jobflow-result-observer";

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

async function notifyJobFlow(result) {
  const tabs = await chrome.tabs.query({url: ["http://127.0.0.1/*", "http://localhost/*"]});
  await Promise.all(tabs.map((tab) => (
    tab.id ? chrome.tabs.sendMessage(tab.id, {type: "JOBFLOW_ASSIST_STATUS", result}).catch(() => undefined) : undefined
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

async function fillCurrentTab(tabId, tabUrl) {
  const state = await sessionState();
  if (!state || state.protocol_version !== PROTOCOL || !state.paired) {
    throw Object.assign(new Error("Pair the companion from JobFlow first."), {
      jobflow: {status: "BLOCKED", code: "COMPANION_NOT_PAIRED"}
    });
  }
  const current = new URL(tabUrl);
  if (current.protocol !== "https:" || current.origin !== state.allowed_page_origin) {
    throw Object.assign(new Error("Open the exact approved company application page."), {
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
  const applied = await chrome.tabs.sendMessage(tabId, {
    type: "JOBFLOW_APPLY_APPROVED",
    fields: prepared.fields,
    files: prepared.files.map((item) => ({
      ...item,
      download_url: `${state.base_url}${item.download_path}`
    }))
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
    submission_observed: false,
    result_final: false,
    last_result: completed
  };
  await saveSession(next);
  await notifyJobFlow(completed);
  return completed;
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
        !/^\/assist\/[A-Za-z0-9_-]{40,}$/.test(pairing.assist_path)
      ) {
        return {status: "BLOCKED", code: "COMPANION_PAIR_PAYLOAD_INVALID"};
      }
      const result = await postJSON(`${pairing.base_url}${pairing.assist_path}/pair`, {});
      const state = {
        protocol_version: PROTOCOL,
        base_url: pairing.base_url,
        assist_path: pairing.assist_path,
        assist_id: result.assist_id,
        application_id: result.application_id,
        allowed_page_origin: result.allowed_page_origin,
        expires_at: result.expires_at,
        paired: true,
        stage: result.status,
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
        application_id: state.application_id,
        allowed_page_origin: state.allowed_page_origin,
        expires_at: state.expires_at,
        last_result: state.last_result
      } : {status: "NOT_PAIRED", paired: false};
    }
    if (message.type === "JOBFLOW_FILL_CURRENT") {
      return await fillCurrentTab(Number(message.tab_id), String(message.tab_url || ""));
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

chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (changeInfo.status !== "complete") return;
  observeResult(tabId);
});

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name !== RESULT_ALARM) return;
  const state = await sessionState();
  if (state?.tab_id) await observeResult(state.tab_id);
});
