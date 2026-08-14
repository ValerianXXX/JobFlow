(() => {
  "use strict";
  if (globalThis.__jobflowCompanionInstalled) return;
  globalThis.__jobflowCompanionInstalled = true;

  const MAX_TEXT = 500;
  const controlMap = new Map();
  const finalSubmitElements = new Set();
  let navigationElement = null;
  let submitSignalSent = false;

  function compact(value, limit = MAX_TEXT) {
    return String(value || "").replace(/\s+/g, " ").trim().slice(0, limit);
  }

  function escapeHTML(value) {
    return String(value).replace(/[&<>"']/g, (char) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;"
    })[char]);
  }

  function safeAttribute(name, value) {
    const text = compact(value, 256);
    return text ? ` ${name}="${escapeHTML(text)}"` : "";
  }

  function visible(element) {
    const style = getComputedStyle(element);
    return style.display !== "none" && style.visibility !== "hidden" && !element.hidden;
  }

  function controlType(element) {
    if (element instanceof HTMLInputElement) return (element.type || "text").toLowerCase();
    if (element instanceof HTMLSelectElement) return "select";
    if (element instanceof HTMLTextAreaElement) return "textarea";
    if (element instanceof HTMLButtonElement) return (element.type || "submit").toLowerCase();
    return "other";
  }

  function labelFor(element) {
    const labels = element.labels ? Array.from(element.labels) : [];
    const text = compact(labels.map((item) => item.innerText || item.textContent).join(" "));
    return text || compact(element.getAttribute("aria-label")) || compact(element.getAttribute("placeholder"));
  }

  function sectionFor(element) {
    const fieldset = element.closest("fieldset");
    const legend = fieldset?.querySelector(":scope > legend");
    if (legend) return compact(legend.innerText || legend.textContent);
    const headings = Array.from(document.querySelectorAll("h1,h2,h3"));
    let latest = "";
    for (const heading of headings) {
      const relation = heading.compareDocumentPosition(element);
      if (relation & Node.DOCUMENT_POSITION_FOLLOWING) latest = compact(heading.innerText || heading.textContent);
    }
    return latest;
  }

  function blockerSignals() {
    const text = compact(document.body?.innerText || "", 250000).toLowerCase();
    const signals = [];
    if (/captcha|recaptcha|hcaptcha|verify you are human|人机验证/.test(text)) signals.push("CAPTCHA");
    if (/multi[- ]factor|two[- ]factor|one[- ]time password|verification code|验证码|动态口令/.test(text)) signals.push("MFA");
    if (/sign in|log in|login required|登录/.test(text) && document.querySelector('input[type="password"]')) signals.push("LOGIN");
    if (/create account|register account|sign up|创建账号|注册账号/.test(text)) signals.push("ACCOUNT_CREATION");
    for (const frame of document.querySelectorAll("iframe[src]")) {
      try { if (new URL(frame.src, location.href).origin !== location.origin) signals.push("CROSS_ORIGIN_IFRAME"); }
      catch (_error) { signals.push("CROSS_ORIGIN_IFRAME"); }
    }
    for (const form of document.forms) {
      try { if (new URL(form.action || location.href, location.href).origin !== location.origin) signals.push("CROSS_ORIGIN_FORM"); }
      catch (_error) { signals.push("CROSS_ORIGIN_FORM"); }
    }
    return [...new Set(signals)].sort();
  }

  function collectForm() {
    controlMap.clear();
    finalSubmitElements.clear();
    navigationElement = null;
    submitSignalSent = false;
    const controls = Array.from(document.querySelectorAll("input,select,textarea,button"))
      .filter((element) => !(element instanceof HTMLInputElement && element.type.toLowerCase() === "hidden"));
    const clientRefs = [];
    const parts = ["<!doctype html><html><body>"];
    for (const form of document.forms) {
      parts.push(`<form${safeAttribute("action", form.getAttribute("action") || "")}></form>`);
    }
    for (const frame of document.querySelectorAll("iframe[src]")) {
      parts.push(`<iframe${safeAttribute("src", frame.getAttribute("src"))}></iframe>`);
    }
    controls.forEach((element, index) => {
      const clientRef = `DOM-${String(index + 1).padStart(12, "0")}`;
      clientRefs.push(clientRef);
      controlMap.set(clientRef, element);
      const syntheticId = `jobflow-control-${index + 1}`;
      const section = sectionFor(element);
      if (section) parts.push(`<h3>${escapeHTML(section)}</h3>`);
      const label = labelFor(element);
      if (label) parts.push(`<label for="${syntheticId}">${escapeHTML(label)}</label>`);
      const required = element.required || element.getAttribute("aria-required") === "true" ? " required" : "";
      const common = `${safeAttribute("id", syntheticId)}${safeAttribute("name", element.getAttribute("name"))}` +
        `${safeAttribute("autocomplete", element.getAttribute("autocomplete"))}${safeAttribute("placeholder", element.getAttribute("placeholder"))}` +
        `${safeAttribute("aria-label", element.getAttribute("aria-label"))}${required}`;
      if (element instanceof HTMLSelectElement) {
        parts.push(`<select${common}>`);
        for (const option of element.options) parts.push(`<option>${escapeHTML(compact(option.textContent, 200))}</option>`);
        parts.push("</select>");
      } else if (element instanceof HTMLTextAreaElement) {
        parts.push(`<textarea${common}></textarea>`);
      } else if (element instanceof HTMLButtonElement) {
        parts.push(`<button${common}${safeAttribute("type", controlType(element))}>${escapeHTML(compact(element.innerText || element.textContent))}</button>`);
      } else {
        parts.push(`<input${common}${safeAttribute("type", controlType(element))}>`);
      }
    });
    parts.push("</body></html>");
    return {
      status: "COLLECTED",
      payload: {
        url: location.href,
        sanitized_html: parts.join(""),
        client_refs: clientRefs,
        blocker_signals: blockerSignals()
      }
    };
  }

  function readableLines(value, limit = 750000) {
    const lines = String(value || "").split(/\r?\n/)
      .map((line) => compact(line, 4000))
      .filter(Boolean);
    return lines.join("\n").slice(0, limit);
  }

  function findJobPosting(value, depth = 0) {
    if (depth > 12 || value === null || value === undefined) return null;
    if (Array.isArray(value)) {
      for (const item of value.slice(0, 200)) {
        const found = findJobPosting(item, depth + 1);
        if (found) return found;
      }
      return null;
    }
    if (typeof value !== "object") return null;
    const type = value["@type"];
    if (type === "JobPosting" || (Array.isArray(type) && type.includes("JobPosting"))) return value;
    for (const item of Object.values(value).slice(0, 200)) {
      const found = findJobPosting(item, depth + 1);
      if (found) return found;
    }
    return null;
  }

  function structuredJobPosting() {
    const scripts = Array.from(document.querySelectorAll('script[type="application/ld+json"]')).slice(0, 20);
    for (const script of scripts) {
      const source = String(script.textContent || "");
      if (!source || source.length > 500000) continue;
      try {
        const found = findJobPosting(JSON.parse(source));
        if (found) return found;
      } catch (_error) {
        // Untrusted page metadata is optional; malformed JSON never weakens the capture gate.
      }
    }
    return null;
  }

  function locationText(posting) {
    const locations = Array.isArray(posting?.jobLocation) ? posting.jobLocation : [posting?.jobLocation];
    const values = [];
    for (const item of locations) {
      const address = item?.address || item;
      for (const key of ["streetAddress", "addressLocality", "addressRegion", "postalCode", "addressCountry", "name"]) {
        const value = typeof address?.[key] === "object" ? address[key]?.name : address?.[key];
        if (value) values.push(compact(value, 160));
      }
    }
    return [...new Set(values.filter(Boolean))].join(", ").slice(0, 500);
  }

  function collectJobPage() {
    const posting = structuredJobPosting();
    const jobRoot = document.querySelector("main,[role=main],article") || document.body;
    const visibleText = readableLines(jobRoot?.innerText || "");
    const heading = compact(document.querySelector("main h1,h1")?.textContent, 500);
    const siteName = compact(document.querySelector('meta[property="og:site_name"]')?.content, 300);
    const company = compact(posting?.hiringOrganization?.name, 300) || siteName;
    const title = compact(posting?.title, 500) || heading || compact(document.title, 500);
    return {
      status: "COLLECTED",
      payload: {
        url: location.href,
        document_title: compact(document.title, 500),
        job_title: title,
        company_name: company,
        job_location: locationText(posting),
        visible_text: visibleText,
        blocker_signals: blockerSignals(),
        application_fields_present: Boolean(document.querySelector("input:not([type=hidden]),select,textarea"))
      }
    };
  }

  async function sha256(bytesOrText) {
    const bytes = typeof bytesOrText === "string" ? new TextEncoder().encode(bytesOrText) : bytesOrText;
    const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
    return `sha256:${Array.from(digest, (item) => item.toString(16).padStart(2, "0")).join("")}`;
  }

  function setTextValue(element, value) {
    let prototype;
    if (element instanceof HTMLInputElement) prototype = HTMLInputElement.prototype;
    else if (element instanceof HTMLTextAreaElement) prototype = HTMLTextAreaElement.prototype;
    else if (element instanceof HTMLSelectElement) prototype = HTMLSelectElement.prototype;
    else throw new Error("UNSUPPORTED_CONTROL");
    const setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
    if (!setter) throw new Error("VALUE_SETTER_UNAVAILABLE");
    if (element instanceof HTMLSelectElement) {
      const target = Array.from(element.options).find((option) => (
        option.value === value || compact(option.textContent).toLowerCase() === compact(value).toLowerCase()
      ));
      if (!target) throw new Error("SELECT_OPTION_NOT_FOUND");
      setter.call(element, target.value);
    } else {
      setter.call(element, value);
    }
    element.dispatchEvent(new Event("input", {bubbles: true}));
    element.dispatchEvent(new Event("change", {bubbles: true}));
  }

  function streamFile(url) {
    return new Promise((resolve, reject) => {
      const port = chrome.runtime.connect({name: "jobflow-file-stream"});
      const chunks = [];
      port.onMessage.addListener((message) => {
        if (message.type === "chunk") {
          const binary = atob(message.data);
          const bytes = new Uint8Array(binary.length);
          for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
          chunks.push(bytes);
        } else if (message.type === "end") {
          resolve(new Blob(chunks));
          port.disconnect();
        } else if (message.type === "error") {
          reject(new Error(message.code || "FILE_STREAM_FAILED"));
          port.disconnect();
        }
      });
      port.onDisconnect.addListener(() => {
        if (chrome.runtime.lastError) reject(new Error("FILE_STREAM_DISCONNECTED"));
      });
      port.postMessage({url});
    });
  }

  async function applyApproved(message) {
    const fieldBindings = [];
    const materialBindings = [];
    finalSubmitElements.clear();
    navigationElement = null;
    for (const clientRef of message.final_submit_client_refs || []) {
      const element = controlMap.get(clientRef);
      if (!element || !document.contains(element)) {
        return {status: "BLOCKED", code: "COMPANION_FINAL_CONTROL_CHANGED", client_ref: clientRef};
      }
      finalSubmitElements.add(element);
    }
    if (message.navigation?.client_ref) {
      navigationElement = controlMap.get(message.navigation.client_ref) || null;
      if (!navigationElement || !document.contains(navigationElement) || finalSubmitElements.has(navigationElement)) {
        return {status: "BLOCKED", code: "COMPANION_NAVIGATION_CONTROL_CHANGED"};
      }
    }
    for (const item of message.fields || []) {
      const element = controlMap.get(item.client_ref);
      if (!element || !document.contains(element) || element.disabled || !visible(element)) {
        return {status: "BLOCKED", code: "COMPANION_CONTROL_CHANGED"};
      }
      if (await sha256(String(item.value)) !== item.value_sha256) {
        return {status: "BLOCKED", code: "COMPANION_VALUE_HASH_MISMATCH"};
      }
      try { setTextValue(element, String(item.value)); }
      catch (_error) { return {status: "BLOCKED", code: "COMPANION_FIELD_APPLY_FAILED", client_ref: item.client_ref}; }
      if (String(element.value) !== String(item.value) && !(element instanceof HTMLSelectElement)) {
        return {status: "BLOCKED", code: "COMPANION_FIELD_VERIFY_FAILED", client_ref: item.client_ref};
      }
      fieldBindings.push({client_ref: item.client_ref, value_sha256: item.value_sha256});
    }
    for (const item of message.files || []) {
      const element = controlMap.get(item.client_ref);
      if (!(element instanceof HTMLInputElement) || element.type !== "file" || !document.contains(element) || element.disabled) {
        return {status: "BLOCKED", code: "COMPANION_FILE_CONTROL_CHANGED", client_ref: item.client_ref};
      }
      let blob;
      try { blob = await streamFile(item.download_url); }
      catch (_error) { return {status: "BLOCKED", code: "COMPANION_FILE_FETCH_FAILED", client_ref: item.client_ref}; }
      const bytes = new Uint8Array(await blob.arrayBuffer());
      if (await sha256(bytes) !== item.sha256) {
        return {status: "BLOCKED", code: "COMPANION_FILE_HASH_MISMATCH", client_ref: item.client_ref};
      }
      const transfer = new DataTransfer();
      transfer.items.add(new File([bytes], item.filename, {type: "application/octet-stream", lastModified: Date.now()}));
      element.files = transfer.files;
      element.dispatchEvent(new Event("input", {bubbles: true}));
      element.dispatchEvent(new Event("change", {bubbles: true}));
      if (!element.files || element.files.length !== 1 || element.files[0].name !== item.filename) {
        return {status: "BLOCKED", code: "COMPANION_FILE_VERIFY_FAILED", client_ref: item.client_ref};
      }
      materialBindings.push({client_ref: item.client_ref, purpose: item.purpose, sha256: item.sha256});
    }
    return {
      status: "APPLIED",
      field_bindings: fieldBindings,
      material_bindings: materialBindings,
      final_submit_armed: finalSubmitElements.size > 0,
      navigation_ready: Boolean(navigationElement)
    };
  }

  function validateNavigation(message) {
    const element = controlMap.get(String(message.client_ref || ""));
    if (
      !element || element !== navigationElement || finalSubmitElements.has(element) ||
      !document.contains(element) || element.disabled || !visible(element)
    ) {
      return {status: "BLOCKED", code: "COMPANION_NAVIGATION_CONTROL_CHANGED"};
    }
    const label = compact(element.innerText || element.textContent || element.value || element.getAttribute("aria-label"));
    if (
      !/(?:^|\b)(?:next|continue|save\s*(?:and|&)\s*continue|review(?:\s+application)?)(?:\b|$)|下一步|继续|保存并继续/i.test(label) ||
      /(?:^|\b)(?:back|previous|cancel)(?:\b|$)|返回|上一步|取消/i.test(label)
    ) {
      return {status: "BLOCKED", code: "COMPANION_NAVIGATION_LABEL_CHANGED"};
    }
    const form = element.form;
    if (form && !form.checkValidity()) {
      form.reportValidity();
      return {status: "BLOCKED", code: "COMPANION_REQUIRED_FIELDS_INCOMPLETE", form_valid: false};
    }
    return {status: "NAVIGATION_VALID", form_valid: true, final_submit: false};
  }

  function navigateApproved(message) {
    const validation = validateNavigation(message);
    if (validation.status !== "NAVIGATION_VALID") return validation;
    const element = controlMap.get(String(message.client_ref || ""));
    element.click();
    return {status: "NAVIGATION_STARTED", form_valid: true, final_submit: false};
  }

  function resultMarkers() {
    const text = compact(document.body?.innerText || "", 1500000).toLowerCase();
    const success = [];
    const failure = [];
    if (/application (?:has been )?submitted|申请(?:已)?提交/.test(text)) success.push("APPLICATION_SUBMITTED");
    if (/application (?:has been )?received|we have received your application|已收到.{0,20}申请/.test(text)) success.push("APPLICATION_RECEIVED");
    if (/thank you for applying|感谢.{0,20}(?:申请|投递)/.test(text)) success.push("THANK_YOU_FOR_APPLYING");
    if (/submission complete|投递完成|提交完成/.test(text)) success.push("SUBMISSION_COMPLETE");
    if (/there was an error|submission error|提交.{0,10}错误/.test(text)) failure.push("SUBMISSION_ERROR");
    if (/please correct.{0,50}(?:error|field)|请.{0,20}(?:更正|修正)/.test(text)) failure.push("VALIDATION_ERROR");
    if (/unable to submit|failed to submit|无法提交|提交失败/.test(text)) failure.push("UNABLE_TO_SUBMIT");
    if (/application (?:was )?not (?:sent|submitted)|申请未提交/.test(text)) failure.push("APPLICATION_NOT_SENT");
    return {success: [...new Set(success)], failure: [...new Set(failure)]};
  }

  async function collectResult() {
    const markers = resultMarkers();
    const formPresent = Boolean(document.querySelector("form"));
    const submitPresent = Boolean(document.querySelector('button[type="submit"],input[type="submit"],button:not([type])'));
    const successRoute = /\/(?:thank[-_]?you|confirmation|success|submitted|application[-_]?complete)(?:\/|$)/i.test(location.pathname);
    const invalidCount = document.querySelectorAll(':invalid,[aria-invalid="true"]').length;
    const fingerprint = await sha256(JSON.stringify({
      origin: location.origin,
      path: location.pathname,
      success: markers.success,
      failure: markers.failure,
      formPresent,
      submitPresent,
      successRoute,
      invalidCount
    }));
    return {
      url: location.href,
      success_markers: markers.success,
      failure_markers: markers.failure,
      form_present: formPresent,
      submit_control_present: submitPresent,
      success_route: successRoute,
      invalid_control_count: invalidCount,
      page_fingerprint: fingerprint
    };
  }

  async function signalUserSubmit(eventKind) {
    if (submitSignalSent) return;
    submitSignalSent = true;
    const eventHash = await sha256(JSON.stringify({
      eventKind,
      origin: location.origin,
      path: location.pathname,
      timeBucket: Math.floor(Date.now() / 1000)
    }));
    await chrome.runtime.sendMessage({
      type: "JOBFLOW_USER_SUBMIT_OBSERVED",
      payload: {url: location.href, trusted_user_event: true, event_hash: eventHash}
    }).catch(() => undefined);
    setTimeout(async () => {
      const payload = await collectResult();
      await chrome.runtime.sendMessage({type: "JOBFLOW_RESULT_SIGNALS", payload}).catch(() => undefined);
    }, 3500);
  }

  document.addEventListener("submit", (event) => {
    if (!event.isTrusted || finalSubmitElements.size === 0) return;
    const submitter = event.submitter || null;
    if ((submitter && finalSubmitElements.has(submitter)) || (!submitter && !navigationElement)) {
      signalUserSubmit("FORM_SUBMIT");
    }
  }, true);
  document.addEventListener("click", (event) => {
    if (!event.isTrusted) return;
    const target = event.target instanceof Element ? event.target.closest('button[type="submit"],input[type="submit"],button:not([type])') : null;
    if (target && target.form && finalSubmitElements.has(target)) signalUserSubmit("SUBMIT_CONTROL_CLICK");
  }, true);

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    (async () => {
      if (message?.type === "JOBFLOW_COLLECT_JOB_PAGE") return collectJobPage();
      if (message?.type === "JOBFLOW_COLLECT_FORM") return collectForm();
      if (message?.type === "JOBFLOW_APPLY_APPROVED") return await applyApproved(message);
      if (message?.type === "JOBFLOW_CHECK_NAVIGATION") return validateNavigation(message);
      if (message?.type === "JOBFLOW_NAVIGATE_APPROVED") return navigateApproved(message);
      if (message?.type === "JOBFLOW_COLLECT_RESULT") {
        const payload = await collectResult();
        await chrome.runtime.sendMessage({type: "JOBFLOW_RESULT_SIGNALS", payload});
        return {status: "RESULT_COLLECTED"};
      }
      return {status: "IGNORED"};
    })().then(sendResponse).catch(() => sendResponse({status: "BLOCKED", code: "COMPANION_DOM_ERROR"}));
    return true;
  });
})();
