(() => {
  "use strict";
  if (globalThis.__jobflowCompanionInstalled) return;
  globalThis.__jobflowCompanionInstalled = true;

  const MAX_TEXT = 500;
  const controlMap = new Map();
  const finalSubmitElements = new Set();
  const documentBytes = new Uint8Array(16);
  crypto.getRandomValues(documentBytes);
  const documentInstanceId = `DOC-${Array.from(documentBytes, (item) => item.toString(16).padStart(2, "0")).join("").toUpperCase()}`;
  let navigationElement = null;
  let navigationProof = null;
  let armedManualChallenge = null;
  let manualSignalSent = false;
  let pendingManualClick = null;
  let submitSignalSent = false;
  let applicationScopeHint = null;
  let domOperationStage = "IDLE";

  function compact(value, limit = MAX_TEXT) {
    return String(value || "").replace(/\s+/g, " ").trim().slice(0, limit);
  }

  function composedParent(element) {
    if (!element) return null;
    if (element.parentElement) return element.parentElement;
    const root = element.getRootNode?.();
    return root instanceof ShadowRoot ? root.host : null;
  }

  function shadowRootFor(element) {
    if (!element) return null;
    try {
      const privileged = chrome?.dom?.openOrClosedShadowRoot?.(element);
      if (privileged) return privileged;
    } catch (_error) {
      // Fall back to standards-visible open roots on browsers without chrome.dom.
    }
    return element.shadowRoot || null;
  }

  function openRoots() {
    const roots = [document];
    const seen = new Set(roots);
    for (let index = 0; index < roots.length && roots.length < 256; index += 1) {
      const root = roots[index];
      for (const element of root.querySelectorAll("*")) {
        const shadow = shadowRootFor(element);
        if (shadow && !seen.has(shadow)) {
          seen.add(shadow);
          roots.push(shadow);
          if (roots.length >= 256) break;
        }
      }
    }
    return roots;
  }

  function deepQueryAll(selector) {
    const output = [];
    const seen = new Set();
    for (const root of openRoots()) {
      for (const element of root.querySelectorAll(selector)) {
        if (!seen.has(element)) {
          seen.add(element);
          output.push(element);
        }
      }
    }
    return output;
  }

  function deepQuery(selector) {
    return deepQueryAll(selector)[0] || null;
  }

  function deepClosest(element, selector) {
    let current = element;
    for (let depth = 0; current && depth < 64; depth += 1) {
      const found = current.closest?.(selector);
      if (found) return found;
      current = composedParent(current);
    }
    return null;
  }

  function composedAncestors(element) {
    const output = [];
    let current = element;
    for (let depth = 0; current && depth < 96; depth += 1) {
      output.push(current);
      current = composedParent(current);
    }
    return output;
  }

  function rootElementById(element, id) {
    const root = element?.getRootNode?.();
    // Salesforce/LWC deliberately throws for ShadowRoot.getElementById;
    // standards-compatible querySelector remains available on open roots.
    const local = root?.querySelector?.(`[id="${CSS.escape(id)}"]`);
    return local || document.getElementById(id) || deepQueryAll(`[id="${CSS.escape(id)}"]`)[0] || null;
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

  function valueFreeRouteAttribute(value) {
    try {
      const parsed = new URL(String(value || ""), location.href);
      if (!['http:', 'https:'].includes(parsed.protocol)) return "";
      return `${parsed.origin}/__jobflow_route_redacted__`;
    } catch (_error) {
      return "";
    }
  }

  function explicitVisibleFileLabel(element) {
    const candidates = new Set(element.labels ? Array.from(element.labels) : []);
    const labelledBy = compact(element.getAttribute("aria-labelledby"));
    if (labelledBy) {
      for (const id of labelledBy.split(/\s+/)) {
        const candidate = rootElementById(element, id);
        if (candidate) candidates.add(candidate);
      }
    }
    const id = compact(element.getAttribute("id"));
    if (id) {
      const root = element.getRootNode?.();
      const candidate = root?.querySelector?.(`label[for="${CSS.escape(id)}"]`);
      if (candidate) candidates.add(candidate);
    }
    return [...candidates].some((candidate) => (
      candidate !== element && candidate?.isConnected && visible(candidate) && compact(referencedLabelText(candidate))
    ));
  }

  function visible(element) {
    const fileInput = element instanceof HTMLInputElement && element.type.toLowerCase() === "file";
    if (
      fileInput && (
        !element.isConnected || element.disabled || element.inert || element.hasAttribute("inert") ||
        element.getAttribute("aria-hidden") === "true"
      )
    ) return false;
    let current = fileInput ? composedParent(element) : element;
    for (let depth = 0; current && depth < 64; depth += 1) {
      const style = getComputedStyle(current);
      if (
        style.display === "none" || style.visibility === "hidden" || current.hidden ||
        current.inert || current.hasAttribute?.("inert") || current.getAttribute?.("aria-hidden") === "true"
      ) return false;
      current = composedParent(current);
    }
    if (fileInput) {
      const style = getComputedStyle(element);
      const visuallyHidden = (
        element.hidden || style.display === "none" || style.visibility === "hidden" ||
        style.opacity === "0" || element.getClientRects().length === 0
      );
      if (!visuallyHidden) return true;
      // ATS pages may hide the canonical upload input behind a visible
      // dropzone.  Admit it only when it has a stable identity and an explicit,
      // visible accessible label.  Anonymous parser/autofill inputs are never
      // application-material targets.
      return Boolean(
        compact(element.getAttribute("id") || element.getAttribute("name")) &&
        element.getAttribute("aria-hidden") !== "true" &&
        explicitVisibleFileLabel(element)
      );
    }
    return element.getClientRects().length > 0;
  }

  function controlType(element) {
    if (element instanceof HTMLInputElement) return (element.type || "text").toLowerCase();
    if (element instanceof HTMLSelectElement) return "select";
    if (element instanceof HTMLTextAreaElement) return "textarea";
    if (element instanceof HTMLButtonElement) return (element.type || "submit").toLowerCase();
    return "other";
  }

  function referencedLabelText(element) {
    const direct = directText(element);
    if (direct) return direct;
    const clone = element?.cloneNode?.(true);
    if (!clone?.querySelectorAll) return compact(element?.innerText || element?.textContent);
    for (const child of clone.querySelectorAll(
      "input,select,textarea,button,[role='alert'],.slds-assistive-text,.slds-form-element__help,[data-help-message]"
    )) child.remove();
    return compact(clone.innerText || clone.textContent);
  }

  function labelFor(element) {
    const labels = element.labels ? Array.from(element.labels) : [];
    const text = compact(labels.map(referencedLabelText).join(" "));
    if (text) return text;
    const labelledBy = compact(element.getAttribute("aria-labelledby"));
    if (labelledBy) {
      const labelled = labelledBy.split(/\s+/).map((id) => rootElementById(element, id))
        .filter(Boolean).map(referencedLabelText).join(" ");
      if (compact(labelled)) return compact(labelled);
    }
    const id = compact(element.getAttribute("id"));
    if (id) {
      const root = element.getRootNode?.();
      const explicit = root?.querySelector?.(`label[for="${CSS.escape(id)}"]`);
      if (compact(explicit?.innerText || explicit?.textContent)) return compact(explicit.innerText || explicit.textContent);
    }
    let current = element;
    for (let depth = 0; current && depth < 12; depth += 1) {
      for (const attribute of ["label", "data-label", "aria-label", "placeholder"]) {
        const candidate = compact(current.getAttribute?.(attribute));
        if (candidate) return candidate;
      }
      current = composedParent(current);
    }
    return compact(element.getAttribute("placeholder")) || compact(element.getAttribute("name"));
  }

  function meaningfulApplicantControl(element) {
    const type = controlType(element);
    if (element instanceof HTMLInputElement && ["file", "radio", "checkbox"].includes(type)) return true;
    if (element.getAttribute?.("role")?.toLowerCase() === "combobox") return true;
    if (element instanceof HTMLButtonElement && element.getAttribute("aria-haspopup")?.toLowerCase() === "true") return true;
    if (labelFor(element)) return true;
    if (element instanceof HTMLSelectElement && Array.from(element.options).some((option) => compact(option.textContent))) return true;
    return Boolean(compact(
      element.getAttribute?.("name") || element.getAttribute?.("placeholder") ||
      element.getAttribute?.("aria-label") || element.getAttribute?.("autocomplete") ||
      element.innerText || element.textContent || element.value
    ));
  }

  function fileSemanticKey(element) {
    if (!(element instanceof HTMLInputElement) || element.type.toLowerCase() !== "file") return "";
    const normalize = (value) => compact(value, 1000).toLowerCase()
      .replace(/[^a-z0-9\u4e00-\u9fff]+/g, " ").trim();
    const labelMaterial = normalize([labelFor(element), element.getAttribute("aria-label")].join(" "));
    const identityMaterial = normalize([element.getAttribute("name"), element.getAttribute("id")].join(" "));
    const material = `${labelMaterial} ${identityMaterial}`.trim();
    if (/(?:^| )(?:resume|curriculum vitae|cv|简历)(?: |$)/.test(material)) return "resume";
    if (/(?:^| )(?:cover letter|motivation letter|求职信|动机信)(?: |$)/.test(material)) return "cover_letter";
    if (/(?:^| )(?:portfolio|work sample|作品集|工作样本)(?: |$)/.test(material)) return "portfolio";
    return `upload:${labelMaterial || identityMaterial || "unresolved"}`;
  }

  function ambiguousFileSemanticCount(elements) {
    const counts = new Map();
    for (const element of elements) {
      const key = fileSemanticKey(element);
      if (!key) continue;
      counts.set(key, (counts.get(key) || 0) + 1);
    }
    return [...counts.values()].filter((count) => count > 1).reduce((total, count) => total + count, 0);
  }

  function choiceContainer(element) {
    return deepClosest(element, ".as-questions-container") || deepClosest(
      element,
      ".as-answers-container,fieldset,[role='radiogroup'],[role='group'],.slds-form-element,lightning-radio-group,lightning-checkbox-group"
    );
  }

  function choiceQuestion(elements) {
    const first = elements[0];
    if (!first) return "";
    const labelledBy = compact(first.getAttribute("aria-labelledby"));
    if (labelledBy) {
      const text = labelledBy.split(/\s+/).map((id) => rootElementById(first, id))
        .filter(Boolean).map((item) => item.innerText || item.textContent).join(" ");
      if (compact(text)) return compact(text);
    }
    const container = choiceContainer(first);
    const labelled = shadowRootFor(container)?.querySelector("legend,.as-question-title,.slds-form-element__label,[data-jobflow-question]") ||
      container?.querySelector("legend,.as-question-title,.slds-form-element__label,[data-jobflow-question]");
    const text = compact(labelled?.innerText || labelled?.textContent);
    if (text && !elements.some((element) => labelFor(element) === text)) return text;
    return compact(first.getAttribute("name")).replace(/[_-]+/g, " ");
  }

  function choiceDescriptor(elements, type) {
    return {
      jobflowChoiceGroup: true,
      type,
      elements,
      question: choiceQuestion(elements),
      options: elements.map((element) => labelFor(element)).filter(Boolean)
    };
  }

  function directText(element) {
    if (!element) return "";
    return compact(Array.from(element.childNodes || [])
      .filter((node) => node.nodeType === Node.TEXT_NODE)
      .map((node) => node.textContent || "")
      .join(" "));
  }

  function customSelectQuestion(element) {
    for (const ancestor of composedAncestors(element).slice(0, 12)) {
      const label = ancestor.matches?.(".labelTextDropDown,[data-jobflow-select-label]")
        ? ancestor
        : shadowRootFor(ancestor)?.querySelector(".labelTextDropDown,[data-jobflow-select-label]") ||
          ancestor.querySelector?.(".labelTextDropDown,[data-jobflow-select-label]");
      const text = directText(label) || compact(label?.getAttribute?.("data-jobflow-select-label"));
      if (text) return text.replace(/\s*\*\s*$/, "");
    }
    return labelFor(element).replace(/\s*\*\s*$/, "");
  }

  function customSelectOwner(element) {
    return deepClosest(element, "c-lwc-drop-down-menu,[data-jobflow-custom-select]") || composedParent(element);
  }

  function customSelectCurrentLabel(binding) {
    const element = binding?.element;
    const owner = binding?.owner;
    const raw = compact(
      element?.getAttribute?.("data-selected-value") ||
      owner?.getAttribute?.("data-selected-value") ||
      element?.getAttribute?.("aria-valuetext") ||
      directText(element) || element?.innerText || element?.textContent || element?.value ||
      element?.getAttribute?.("aria-label") || owner?.getAttribute?.("aria-label")
    );
    // Salesforce/LWC buttons commonly expose the current choice together with
    // an accessibility affordance (for example, "Mobile Show menu").  The
    // affordance is not part of the applicant's selected value.
    return compact(raw
      .replace(/\s+(?:show|hide|open|close)\s+(?:the\s+)?(?:menu|options?)\s*$/i, "")
      .replace(/\s*(?:显示|隐藏|展开|收起)(?:菜单|选项)\s*$/, ""));
  }

  function customSelectDescriptor(element) {
    const owner = customSelectOwner(element);
    const question = customSelectQuestion(element);
    const label = shadowRootFor(owner)?.querySelector(".labelTextDropDown,[data-jobflow-select-label]") ||
      owner?.querySelector?.(".labelTextDropDown,[data-jobflow-select-label]");
    return {
      jobflowCustomSelect: true,
      element,
      owner,
      question,
      name: compact(owner?.getAttribute?.("data-id") || element.getAttribute("name")),
      required: /\*/.test(compact(label?.textContent || question)) || element.getAttribute("aria-required") === "true"
    };
  }

  function ariaComboboxDescriptor(element) {
    return {
      jobflowAriaCombobox: true,
      element,
      question: labelFor(element).replace(/\s*\*\s*$/, ""),
      name: compact(element.getAttribute("name")),
      popupId: compact(element.getAttribute("aria-controls") || element.getAttribute("aria-owns")),
      required: element.required || element.getAttribute("aria-required") === "true"
    };
  }

  function ariaComboboxCurrentLabel(binding) {
    const element = binding?.element;
    const raw = compact(
      element?.getAttribute?.("aria-valuetext") || element?.getAttribute?.("data-selected-value") ||
      element?.value || directText(element) || element?.innerText || element?.textContent
    );
    return compact(raw
      .replace(/\s+(?:show|hide|open|close)\s+(?:the\s+)?(?:menu|options?)\s*$/i, "")
      .replace(/\s*(?:显示|隐藏|展开|收起)(?:菜单|选项)\s*$/, ""));
  }

  function sectionFor(element) {
    const fieldset = deepClosest(element, "fieldset");
    const legend = fieldset?.querySelector(":scope > legend");
    if (legend) return compact(legend.innerText || legend.textContent);
    const container = deepClosest(element, ".slds-form-element,[role='group'],section");
    const containerLabel = shadowRootFor(container)?.querySelector("legend,.slds-form-element__label,h1,h2,h3") ||
      container?.querySelector(":scope > legend,:scope > .slds-form-element__label,:scope > h1,:scope > h2,:scope > h3");
    if (compact(containerLabel?.innerText || containerLabel?.textContent)) {
      return compact(containerLabel.innerText || containerLabel.textContent);
    }
    const root = element.getRootNode?.() || document;
    const headings = Array.from(root.querySelectorAll?.("h1,h2,h3") || []);
    let latest = "";
    for (const heading of headings) {
      const relation = heading.compareDocumentPosition(element);
      if (relation & Node.DOCUMENT_POSITION_FOLLOWING) latest = compact(heading.innerText || heading.textContent);
    }
    return latest;
  }

  function relevantEmbeddedFrame(frame) {
    if (!frame || !visible(frame) || frame.getAttribute("aria-hidden") === "true") return false;
    const rect = frame.getBoundingClientRect();
    // Analytics and consent vendors commonly inject zero-sized cross-origin
    // frames. They cannot contain an applicant-visible control and must not
    // turn an otherwise first-party form into a false security stop.
    return rect.width >= 80 && rect.height >= 50 && rect.width * rect.height >= 8_000;
  }

  function blockerSignals() {
    const text = compact(document.body?.innerText || "", 250000).toLowerCase();
    const signals = [];
    if (/captcha|recaptcha|hcaptcha|verify you are human|人机验证/.test(text)) signals.push("CAPTCHA");
    if (/multi[- ]factor|two[- ]factor|one[- ]time password|verification code|验证码|动态口令/.test(text)) signals.push("MFA");
    if (/sign in|log in|login required|登录/.test(text) && deepQuery('input[type="password"]')) signals.push("LOGIN");
    if (/create account|register account|sign up|创建账号|注册账号/.test(text)) signals.push("ACCOUNT_CREATION");
    for (const frame of deepQueryAll("iframe[src]").filter(relevantEmbeddedFrame)) {
      try { if (new URL(frame.src, location.href).origin !== location.origin) signals.push("CROSS_ORIGIN_IFRAME"); }
      catch (_error) { signals.push("CROSS_ORIGIN_IFRAME"); }
    }
    for (const form of deepQueryAll("form")) {
      try { if (new URL(form.action || location.href, location.href).origin !== location.origin) signals.push("CROSS_ORIGIN_FORM"); }
      catch (_error) { signals.push("CROSS_ORIGIN_FORM"); }
    }
    return [...new Set(signals)].sort();
  }

  function serializedFormSnapshot() {
    const allControls = deepQueryAll("input,select,textarea,button,[role='combobox']")
      .filter((element) => !(element instanceof HTMLInputElement && element.type.toLowerCase() === "hidden"))
      .filter((element) => visible(element))
      .filter(meaningfulApplicantControl);
    const applicationSignals = allControls.filter((element) => (
      element instanceof HTMLInputElement && (
        element.type.toLowerCase() === "file" ||
        (element.required && ["text", "email", "tel", "url"].includes(element.type.toLowerCase()))
      )
    ));
    const signalChains = applicationSignals.map((element) => composedAncestors(element));
    const applicationScope = signalChains.length >= 2 ? signalChains[0].find((candidate) => (
      candidate !== document.body && candidate !== document.documentElement &&
      signalChains.every((chain) => chain.includes(candidate))
    )) : null;
    const formScores = new Map();
    for (const element of allControls) {
      const form = deepClosest(element, "form");
      if (!form) continue;
      const type = controlType(element);
      const score = (formScores.get(form) || 0) + (type === "file" ? 20 : ["submit", "button"].includes(type) ? 1 : 4);
      formScores.set(form, score);
    }
    const bestForm = [...formScores.entries()].sort((left, right) => right[1] - left[1])[0]?.[0] || null;
    // Once a resume upload succeeds, some ATS pages remove the file input.  If
    // the remaining required text fields live in a nested Contact section, an
    // application-signal-only scope would incorrectly hide every later
    // qualification question and the final Submit boundary.  A concrete form
    // is the stronger application container, so keep its complete control set.
    const hintedControls = applicationScopeHint?.isConnected
      ? allControls.filter((element) => composedAncestors(element).includes(applicationScopeHint))
      : [];
    const scopedControls = hintedControls.length ? hintedControls : bestForm ? allControls.filter((element) => {
      const owner = deepClosest(element, "form");
      if (owner === bestForm) return true;
      if (owner) return false;
      if (!(element instanceof HTMLButtonElement || element instanceof HTMLInputElement)) return false;
      const label = compact(element.innerText || element.textContent || element.value || element.getAttribute("aria-label"));
      return /next|continue|review|submit|apply|send|finish|complete|下一步|继续|提交|投递|发送|完成/i.test(label);
    }) : applicationScope
      ? allControls.filter((element) => composedAncestors(element).includes(applicationScope))
      : allControls;
    if (!applicationScopeHint?.isConnected) {
      applicationScopeHint = applicationScope || bestForm || null;
    }
    const rawControls = scopedControls;
    const ambiguousUploadCount = ambiguousFileSemanticCount(rawControls);
    if (ambiguousUploadCount > 0) {
      return {controls: [], clientRefs: [], sanitizedHTML: "", ambiguousUploadCount};
    }
    const controls = [];
    const consumed = new Set();
    for (const element of rawControls) {
      if (consumed.has(element)) continue;
      if (
        element instanceof HTMLButtonElement &&
        element.getAttribute("aria-haspopup")?.toLowerCase() === "true"
      ) {
        consumed.add(element);
        controls.push(customSelectDescriptor(element));
      } else if (element.getAttribute?.("role")?.toLowerCase() === "combobox") {
        consumed.add(element);
        controls.push(ariaComboboxDescriptor(element));
      } else if (element instanceof HTMLInputElement && ["radio", "checkbox"].includes(element.type.toLowerCase())) {
        const name = compact(element.getAttribute("name"));
        const container = choiceContainer(element);
        const peers = rawControls.filter((candidate) => (
          candidate instanceof HTMLInputElement && candidate.type.toLowerCase() === element.type.toLowerCase() &&
          (container ? choiceContainer(candidate) === container : name
            ? compact(candidate.getAttribute("name")) === name : candidate === element)
        ));
        peers.forEach((peer) => consumed.add(peer));
        controls.push(choiceDescriptor(peers, element.type.toLowerCase()));
      } else {
        consumed.add(element);
        controls.push(element);
      }
    }
    const clientRefs = [];
    const parts = ["<!doctype html><html><body>"];
    for (const form of deepQueryAll("form")) {
      parts.push(`<form${safeAttribute("action", valueFreeRouteAttribute(form.getAttribute("action") || location.href))}></form>`);
    }
    for (const frame of deepQueryAll("iframe[src]").filter(relevantEmbeddedFrame)) {
      parts.push(`<iframe${safeAttribute("src", valueFreeRouteAttribute(frame.getAttribute("src")))}></iframe>`);
    }
    controls.forEach((binding, index) => {
      const clientRef = `DOM-${String(index + 1).padStart(12, "0")}`;
      clientRefs.push(clientRef);
      const syntheticId = `jobflow-control-${index + 1}`;
      if (binding?.jobflowChoiceGroup) {
        const question = binding.question || binding.options.join(" / ") || "Choice";
        parts.push(`<h3>${escapeHTML(sectionFor(binding.elements[0]))}</h3>`);
        parts.push(`<label for="${syntheticId}">${escapeHTML(question)}</label>`);
        const required = binding.elements.some((element) => element.required || element.getAttribute("aria-required") === "true") ? " required" : "";
        parts.push(`<select${safeAttribute("id", syntheticId)}${safeAttribute("name", binding.elements[0]?.getAttribute("name"))}${required}>`);
        for (const option of binding.options) parts.push(`<option>${escapeHTML(option)}</option>`);
        parts.push("</select>");
        return;
      }
      if (binding?.jobflowCustomSelect) {
        const question = binding.question || binding.name || "Choice";
        parts.push(`<h3>${escapeHTML(sectionFor(binding.element))}</h3>`);
        parts.push(`<label for="${syntheticId}">${escapeHTML(question)}</label>`);
        const required = binding.required ? " required" : "";
        parts.push(`<select${safeAttribute("id", syntheticId)}${safeAttribute("name", binding.name)}${safeAttribute("aria-label", question)}${required} data-jobflow-custom-select="true"></select>`);
        return;
      }
      if (binding?.jobflowAriaCombobox) {
        const question = binding.question || binding.name || "Choice";
        parts.push(`<h3>${escapeHTML(sectionFor(binding.element))}</h3>`);
        parts.push(`<label for="${syntheticId}">${escapeHTML(question)}</label>`);
        const required = binding.required ? " required" : "";
        parts.push(`<select${safeAttribute("id", syntheticId)}${safeAttribute("name", binding.name)}${safeAttribute("aria-label", question)}${required} data-jobflow-aria-combobox="true"></select>`);
        return;
      }
      const element = binding;
      const section = sectionFor(element);
      if (section) parts.push(`<h3>${escapeHTML(section)}</h3>`);
      const label = labelFor(element);
      if (label) parts.push(`<label for="${syntheticId}">${escapeHTML(label)}</label>`);
      const required = element.required || element.getAttribute("aria-required") === "true" ? " required" : "";
      const common = `${safeAttribute("id", syntheticId)}${safeAttribute("name", element.getAttribute("name"))}` +
        `${safeAttribute("autocomplete", element.getAttribute("autocomplete"))}${safeAttribute("placeholder", element.getAttribute("placeholder"))}` +
        `${safeAttribute("aria-label", element.getAttribute("aria-label"))}${safeAttribute("maxlength", element.getAttribute("maxlength"))}` +
        `${safeAttribute("minlength", element.getAttribute("minlength"))}${safeAttribute("pattern", element.getAttribute("pattern"))}${required}`;
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
    return {controls, clientRefs, sanitizedHTML: parts.join(""), ambiguousUploadCount: 0};
  }

  function collectForm() {
    controlMap.clear();
    finalSubmitElements.clear();
    navigationElement = null;
    navigationProof = null;
    armedManualChallenge = null;
    manualSignalSent = false;
    pendingManualClick = null;
    submitSignalSent = false;
    const snapshot = serializedFormSnapshot();
    if (snapshot.ambiguousUploadCount > 0) {
      return {
        status: "BLOCKED",
        code: "COMPANION_AMBIGUOUS_FILE_CONTROLS",
        automatic_retry: false,
        ambiguous_upload_control_count: snapshot.ambiguousUploadCount
      };
    }
    snapshot.controls.forEach((element, index) => controlMap.set(snapshot.clientRefs[index], element));
    return {
      status: "COLLECTED",
      payload: {
        url: location.href,
        sanitized_html: snapshot.sanitizedHTML,
        client_refs: snapshot.clientRefs,
        document_instance_id: documentInstanceId,
        blocker_signals: blockerSignals()
      }
    };
  }

  async function collectFormWhenReady() {
    let collected = collectForm();
    if (collected.status !== "COLLECTED") return collected;
    if (collected.payload.client_refs.length > 0) return await addCollectedControlSemantics(collected);
    const deadline = Date.now() + 20000;
    while (Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 250));
      collected = collectForm();
      if (collected.status !== "COLLECTED") return collected;
      if (collected.payload.client_refs.length > 0) return await addCollectedControlSemantics(collected);
    }
    return await addCollectedControlSemantics(collected);
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
    // Component-driven career sites often leave an empty <main> shell while
    // rendering the actual posting in a sibling/custom-element subtree.  Pick
    // the richest visible candidate instead of accepting the first structural
    // landmark and falsely reporting an empty role.
    const jobRoots = [...document.querySelectorAll("main,[role=main],article"), document.body]
      .filter((element, index, values) => element && values.indexOf(element) === index);
    const jobRoot = jobRoots.reduce((best, candidate) => (
      compact(candidate?.innerText || "", 750000).length > compact(best?.innerText || "", 750000).length
        ? candidate : best
    ), document.body);
    const visibleText = readableLines(jobRoot?.innerText || "");
    const heading = compact(deepQuery("main h1,h1")?.textContent, 500);
    const siteName = compact(document.querySelector('meta[property="og:site_name"]')?.content, 300);
    const company = compact(posting?.hiringOrganization?.name, 300) || siteName;
    const title = compact(posting?.title, 500) || heading || compact(document.title, 500);
    const validThrough = compact(posting?.validThrough, 100);
    const closedSignal = /(?:job|position|posting|role|applications?)\s+(?:is\s+|are\s+|has\s+been\s+)?(?:closed|expired|filled|no\s+longer\s+(?:available|accepting))|no\s+longer\s+accepting\s+applications|not\s+accepting\s+applications|职位(?:已)?关闭|招聘(?:已)?结束|停止接受申请|职位已满/i.test(visibleText.slice(0, 120000));
    const applyCandidates = [];
    const seenApplyUrls = new Set();
    for (const anchor of deepQueryAll("a[href]")) {
      if (!visible(anchor)) continue;
      const label = compact(anchor.innerText || anchor.textContent || anchor.getAttribute("aria-label"), 200);
      if (!/(?:^|\b)(?:apply(?:\s+now)?|start\s+(?:an?\s+)?application)(?:\b|$)|申请|立即申请|开始申请/i.test(label)) continue;
      let url;
      try { url = new URL(anchor.href, location.href); }
      catch (_error) { continue; }
      if (url.protocol !== "https:" || seenApplyUrls.has(url.href)) continue;
      seenApplyUrls.add(url.href);
      applyCandidates.push({url: url.href, label});
      if (applyCandidates.length >= 20) break;
    }
    return {
      status: "COLLECTED",
      payload: {
        url: location.href,
        document_title: compact(document.title, 500),
        job_title: title,
        company_name: company,
        job_location: locationText(posting),
        visible_text: visibleText,
        availability: {closed_signal: closedSignal, valid_through: validThrough},
        blocker_signals: blockerSignals(),
        application_fields_present: Boolean(deepQuery("input:not([type=hidden]),select,textarea")),
        apply_candidates: applyCandidates
      }
    };
  }

  function collectSearchResults() {
    const results = [];
    const seen = new Set();
    for (const anchor of deepQueryAll("a[href]")) {
      if (!visible(anchor)) continue;
      let url;
      try { url = new URL(anchor.href, location.href); }
      catch (_error) { continue; }
      if (url.protocol !== "https:" || url.origin === location.origin || seen.has(url.href)) continue;
      const title = compact(anchor.innerText || anchor.textContent || anchor.getAttribute("aria-label"), 300);
      if (!title) continue;
      const container = deepClosest(anchor, "article,li,[role='listitem'],.g,.b_algo,.result") || anchor.parentElement;
      const containerText = compact(container?.innerText || container?.textContent, 900);
      const snippet = compact(containerText.replace(title, ""), 500);
      seen.add(url.href);
      results.push({url: url.href, title, snippet});
      if (results.length >= 100) break;
    }
    if (!results.length) return {status: "BLOCKED", code: "COMPANION_SEARCH_RESULTS_UNAVAILABLE"};
    return {status: "COLLECTED", payload: {search_origin: location.origin, results}};
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

  function maxLengthEvidence(element) {
    if (!(element instanceof HTMLInputElement) && !(element instanceof HTMLTextAreaElement)) {
      return {status: "ABSENT", value: null};
    }
    const raw = element.getAttribute("maxlength");
    if (raw === null) return {status: "ABSENT", value: null};
    const normalized = String(raw).trim();
    if (!/^[0-9]{1,10}$/.test(normalized)) return {status: "INVALID", value: null};
    const attributeValue = Number(normalized);
    let currentValue;
    try { currentValue = Number(element.maxLength); }
    catch (_error) { return {status: "INVALID", value: null}; }
    if (
      !Number.isInteger(attributeValue) || attributeValue < 1 || attributeValue > 2147483647 ||
      !Number.isInteger(currentValue) || currentValue !== attributeValue || currentValue < 1
    ) {
      return {status: "INVALID", value: null};
    }
    return {status: "VALID", value: currentValue};
  }

  function fieldMaxLengthFailure(binding, item) {
    const current = maxLengthEvidence(primaryBindingElement(binding));
    const hasApprovedBinding = Object.prototype.hasOwnProperty.call(item || {}, "max_length_status") ||
      Object.prototype.hasOwnProperty.call(item || {}, "max_length");
    if (hasApprovedBinding) {
      const approvedStatus = String(item?.max_length_status || "ABSENT").trim().toUpperCase();
      const approvedValue = approvedStatus === "VALID" && Number.isInteger(item?.max_length) && item.max_length > 0
        ? item.max_length : null;
      if (approvedStatus !== current.status || approvedValue !== current.value) {
        return "COMPANION_FIELD_MAX_LENGTH_CHANGED";
      }
    }
    if (current.status === "INVALID") return "COMPANION_FIELD_MAX_LENGTH_CHANGED";
    if (current.status === "VALID" && String(item?.value ?? "").length > current.value) {
      return "COMPANION_FIELD_MAX_LENGTH_CHANGED";
    }
    return null;
  }

  function normalizedChoice(value) {
    return compact(value, 500).toLocaleLowerCase().replace(/[\s_-]+/g, " ");
  }

  function choiceApplied(element) {
    return Boolean(element?.checked || element?.getAttribute?.("aria-checked") === "true");
  }

  function waitForChoiceApplied(element, maximumMilliseconds = 1200) {
    const started = Date.now();
    return new Promise((resolve) => {
      const inspect = () => {
        if (choiceApplied(element)) return resolve(true);
        if (Date.now() - started >= maximumMilliseconds) return resolve(false);
        setTimeout(inspect, 40);
      };
      inspect();
    });
  }

  async function applyChoiceValue(binding, value) {
    const desired = normalizedChoice(value);
    const target = binding.elements.find((element) => {
      const label = normalizedChoice(labelFor(element));
      const raw = normalizedChoice(element.getAttribute("value"));
      return desired === label || (raw && desired === raw);
    });
    if (!target) throw new Error("CHOICE_OPTION_NOT_FOUND");
    if (!choiceApplied(target)) {
      const label = target.labels ? Array.from(target.labels).find((item) => visible(item)) : null;
      if (label) HTMLElement.prototype.click.call(label);
      else HTMLInputElement.prototype.click.call(target);
    }
    if (!(await waitForChoiceApplied(target))) {
      // Some component frameworks do not forward a synthetic label click.
      // Retry the underlying input exactly once and then fail closed.
      HTMLInputElement.prototype.click.call(target);
    }
    if (!(await waitForChoiceApplied(target))) {
      throw new Error("CHOICE_VALUE_NOT_APPLIED");
    }
    return target;
  }

  function fieldApplyFailureCode(error) {
    const code = String(error?.message || "");
    const supported = new Set([
      "CHOICE_OPTION_NOT_FOUND", "CHOICE_VALUE_NOT_APPLIED",
      "CUSTOM_SELECT_CHANGED", "CUSTOM_SELECT_OPTION_NOT_FOUND",
      "CUSTOM_SELECT_VERIFY_FAILED", "CUSTOM_SELECT_CLOSE_FAILED",
      "ARIA_COMBOBOX_CHANGED", "ARIA_COMBOBOX_OPTION_NOT_FOUND", "ARIA_COMBOBOX_VERIFY_FAILED",
      "SELECT_OPTION_NOT_FOUND", "VALUE_SETTER_UNAVAILABLE", "UNSUPPORTED_CONTROL"
    ]);
    if (!supported.has(code)) return "COMPANION_FIELD_APPLY_FAILED";
    const aliases = {
      CHOICE_OPTION_NOT_FOUND: "COMPANION_CHOICE_OPTION_NOT_FOUND",
      CHOICE_VALUE_NOT_APPLIED: "COMPANION_CHOICE_VALUE_NOT_APPLIED",
      CUSTOM_SELECT_CHANGED: "COMPANION_CUSTOM_SELECT_CHANGED",
      CUSTOM_SELECT_OPTION_NOT_FOUND: "COMPANION_CUSTOM_SELECT_OPTION_NOT_FOUND",
      CUSTOM_SELECT_VERIFY_FAILED: "COMPANION_CUSTOM_SELECT_VERIFY_FAILED",
      CUSTOM_SELECT_CLOSE_FAILED: "COMPANION_CUSTOM_SELECT_CLOSE_FAILED",
      ARIA_COMBOBOX_CHANGED: "COMPANION_ARIA_COMBOBOX_CHANGED",
      ARIA_COMBOBOX_OPTION_NOT_FOUND: "COMPANION_ARIA_COMBOBOX_OPTION_NOT_FOUND",
      ARIA_COMBOBOX_VERIFY_FAILED: "COMPANION_ARIA_COMBOBOX_VERIFY_FAILED",
      SELECT_OPTION_NOT_FOUND: "COMPANION_SELECT_OPTION_NOT_FOUND",
      VALUE_SETTER_UNAVAILABLE: "COMPANION_VALUE_SETTER_UNAVAILABLE",
      UNSUPPORTED_CONTROL: "COMPANION_CONTROL_TYPE_UNSUPPORTED"
    };
    return aliases[code];
  }

  function safeDomDiagnosticCode(error) {
    const code = String(error?.message || "");
    return /^[A-Z][A-Z0-9_]{2,63}$/.test(code) ? code : "UNEXPECTED_DOM_EXCEPTION";
  }

  function customOptionValue(element) {
    return compact(
      element?.getAttribute?.("data-value") || element?.getAttribute?.("value") ||
      element?.getAttribute?.("label") || element?.getAttribute?.("aria-label") ||
      element?.innerText || element?.textContent
    );
  }

  function customSelectOptions(binding, visibleOnly = true) {
    const element = binding?.element;
    const owner = binding?.owner;
    const roots = [shadowRootFor(owner), owner, shadowRootFor(element)].filter(Boolean);
    const popupId = compact(element?.getAttribute?.("aria-controls") || element?.getAttribute?.("aria-owns"));
    const popup = popupId ? rootElementById(element, popupId) : null;
    if (popup) roots.push(popup);
    const candidates = [];
    const seen = new Set();
    for (const root of roots) {
      const items = root.matches?.("lightning-menu-item,[role='menuitem'],[role='option'],[data-value]")
        ? [root]
        : Array.from(root.querySelectorAll?.("lightning-menu-item,[role='menuitem'],[role='option'],[data-value]") || []);
      for (const candidate of items) {
        if (seen.has(candidate) || (visibleOnly && !visible(candidate))) continue;
        seen.add(candidate);
        candidates.push(candidate);
      }
    }
    return candidates;
  }

  function waitForExactCustomOption(binding, value, maximumMilliseconds = 7000) {
    const desired = normalizedChoice(value);
    const started = Date.now();
    return new Promise((resolve, reject) => {
      const inspect = () => {
        const option = customSelectOptions(binding, true)
          .find((candidate) => normalizedChoice(customOptionValue(candidate)) === desired);
        if (option) return resolve(option);
        if (Date.now() - started >= maximumMilliseconds) return reject(new Error("CUSTOM_SELECT_OPTION_NOT_FOUND"));
        setTimeout(inspect, 80);
      };
      inspect();
    });
  }

  async function applyCustomSelectValue(binding, value) {
    const element = binding?.element;
    if (!(element instanceof HTMLButtonElement) || !element.isConnected || element.disabled) {
      throw new Error("CUSTOM_SELECT_CHANGED");
    }
    if (normalizedChoice(customSelectCurrentLabel(binding)) === normalizedChoice(value)) return;
    HTMLElement.prototype.click.call(element);
    const option = await waitForExactCustomOption(binding, value);
    HTMLElement.prototype.click.call(option);
    await waitForDomQuiet({quietMilliseconds: 250, maximumMilliseconds: 5000});
    if (normalizedChoice(customSelectCurrentLabel(binding)) !== normalizedChoice(value)) {
      throw new Error("CUSTOM_SELECT_VERIFY_FAILED");
    }
    // Some LWC menus retain their overlay after a successful option click.
    // Close only the same non-navigation choice button that JobFlow just
    // verified; this never targets Next or final Submit.
    if (element.getAttribute("aria-expanded") === "true") {
      element.blur();
      element.dispatchEvent(new KeyboardEvent("keydown", {
        key: "Escape", code: "Escape", bubbles: true, cancelable: true
      }));
      await new Promise((resolve) => setTimeout(resolve, 120));
      if (element.getAttribute("aria-expanded") === "true") {
        HTMLElement.prototype.click.call(element);
        await waitForDomQuiet({quietMilliseconds: 180, maximumMilliseconds: 1800});
      }
      // A verified selection is the important state. Some ATS components keep
      // their menu open until focus changes; that cosmetic state must not turn
      // an already-correct value into a destructive whole-page restart.
    }
  }

  function ariaComboboxOptions(binding, visibleOnly = true) {
    const popup = binding?.popupId ? rootElementById(binding.element, binding.popupId) : null;
    if (!popup) return [];
    const candidates = deepQueryAll("[role='option'],option,[data-value]");
    return candidates.filter((candidate) => (
      (!visibleOnly || visible(candidate)) && (candidate === popup || composedAncestors(candidate).includes(popup))
    ));
  }

  function waitForExactAriaComboboxOption(binding, value, maximumMilliseconds = 7000) {
    const desired = normalizedChoice(value);
    const started = Date.now();
    return new Promise((resolve, reject) => {
      const inspect = () => {
        const option = ariaComboboxOptions(binding)
          .find((candidate) => normalizedChoice(customOptionValue(candidate)) === desired);
        if (option) return resolve(option);
        if (Date.now() - started >= maximumMilliseconds) return reject(new Error("ARIA_COMBOBOX_OPTION_NOT_FOUND"));
        setTimeout(inspect, 80);
      };
      inspect();
    });
  }

  async function applyAriaComboboxValue(binding, value) {
    const element = binding?.element;
    if (
      !(element instanceof HTMLElement) || !element.isConnected || element.disabled ||
      element.getAttribute("aria-disabled") === "true" ||
      element.getAttribute("role")?.toLowerCase() !== "combobox"
    ) {
      throw new Error("ARIA_COMBOBOX_CHANGED");
    }
    if (normalizedChoice(ariaComboboxCurrentLabel(binding)) === normalizedChoice(value)) return;
    const originalValue = element instanceof HTMLInputElement ? String(element.value || "") : "";
    const originalAriaValue = element.getAttribute("aria-valuetext");
    element.focus();
    HTMLElement.prototype.click.call(element);
    if (element instanceof HTMLInputElement && !element.readOnly) {
      setTextValue(element, String(value));
      element.dispatchEvent(new KeyboardEvent("keydown", {
        key: "ArrowDown", code: "ArrowDown", bubbles: true, cancelable: true
      }));
    }
    let option;
    try {
      option = await waitForExactAriaComboboxOption(binding, value);
    } catch (error) {
      // Searching a custom combobox may temporarily replace its text. Restore
      // the last verified display value before failing so an unavailable
      // approved option does not leave a misleading partial answer behind.
      if (element instanceof HTMLInputElement && !element.readOnly) setTextValue(element, originalValue);
      if (originalAriaValue === null) element.removeAttribute("aria-valuetext");
      else element.setAttribute("aria-valuetext", originalAriaValue);
      element.dispatchEvent(new KeyboardEvent("keydown", {
        key: "Escape", code: "Escape", bubbles: true, cancelable: true
      }));
      element.blur();
      throw error;
    }
    HTMLElement.prototype.click.call(option);
    await waitForDomQuiet({quietMilliseconds: 250, maximumMilliseconds: 5000});
    if (normalizedChoice(ariaComboboxCurrentLabel(binding)) !== normalizedChoice(value)) {
      throw new Error("ARIA_COMBOBOX_VERIFY_FAILED");
    }
  }

  function waitForDomQuiet({quietMilliseconds = 900, maximumMilliseconds = 30000} = {}) {
    return new Promise((resolve) => {
      let quietTimer = null;
      let maximumTimer = null;
      const observers = [];
      const finish = () => {
        for (const observer of observers) observer.disconnect();
        if (quietTimer) clearTimeout(quietTimer);
        if (maximumTimer) clearTimeout(maximumTimer);
        resolve();
      };
      const armQuiet = () => {
        if (quietTimer) clearTimeout(quietTimer);
        quietTimer = setTimeout(finish, quietMilliseconds);
      };
      for (const root of openRoots()) {
        const target = root === document ? document.documentElement : root;
        if (!target) continue;
        const observer = new MutationObserver(armQuiet);
        observer.observe(target, {subtree: true, childList: true, attributes: true, characterData: true});
        observers.push(observer);
      }
      maximumTimer = setTimeout(finish, maximumMilliseconds);
      armQuiet();
    });
  }

  function exactFileEvidence(scope, filename) {
    const text = compact((scope?.isConnected ? scope : document.body)?.innerText || "", 250000);
    if (!text.includes(filename)) return false;
    return /upload(?:ed)?|success|attach(?:ed)?|received|已上传|上传成功|附件/i.test(text);
  }

  function approvedFileMime(filename) {
    const normalized = String(filename || "").toLowerCase();
    if (normalized.endsWith(".pdf")) return "application/pdf";
    if (normalized.endsWith(".docx")) return "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
    if (normalized.endsWith(".txt")) return "text/plain";
    return "application/octet-stream";
  }

  async function prepareApprovedFile(item) {
    const blob = await streamFile(item.download_url);
    const bytes = new Uint8Array(await blob.arrayBuffer());
    if (await sha256(bytes) !== item.sha256) throw new Error("COMPANION_FILE_HASH_MISMATCH");
    return new File([bytes], item.filename, {type: approvedFileMime(item.filename), lastModified: Date.now()});
  }

  async function attachPreparedFile(element, item, file) {
    const verificationScope = deepClosest(element, "form") || element.parentElement || document.body;
    const transfer = new DataTransfer();
    transfer.items.add(file);
    element.files = transfer.files;
    element.dispatchEvent(new Event("input", {bubbles: true}));
    element.dispatchEvent(new Event("change", {bubbles: true}));
    // Resume parsers often rebuild the form well after the immediate change
    // event.  A longer quiet window prevents JobFlow from binding values to a
    // short-lived pre-parser tree and reporting a temporary success.
    await waitForDomQuiet({quietMilliseconds: 1800, maximumMilliseconds: 15000});
    const retained = element.isConnected && element.files && element.files.length === 1 && element.files[0].name === item.filename;
    if (!retained && !exactFileEvidence(verificationScope, item.filename)) {
      throw new Error("COMPANION_FILE_VERIFY_FAILED");
    }
  }

  function normalizedOptionValues(elements) {
    return [...new Set(elements.map((item) => normalizedChoice(customOptionValue(item))).filter(Boolean))].sort();
  }

  function bindingRebindSignature(binding) {
    if (binding?.jobflowChoiceGroup) {
      const maximum = maxLengthEvidence(binding.elements[0]);
      return JSON.stringify([
        "choice", controlType(binding.elements[0]), compact(binding.elements[0]?.getAttribute("name")),
        compact(binding.question), binding.options.map((item) => normalizedChoice(item)),
        binding.elements.some((element) => element.required || element.getAttribute("aria-required") === "true"),
        maximum.status, maximum.value
      ]);
    }
    if (binding?.jobflowCustomSelect) {
      const maximum = maxLengthEvidence(binding.element);
      return JSON.stringify([
        "custom-select", compact(binding.name), compact(binding.question),
        compact(binding.element?.getAttribute("aria-label")),
        normalizedOptionValues(customSelectOptions(binding, false)),
        Boolean(binding.required), maximum.status, maximum.value
      ]);
    }
    if (binding?.jobflowAriaCombobox) {
      const maximum = maxLengthEvidence(binding.element);
      return JSON.stringify([
        "aria-combobox", compact(binding.name), compact(binding.question), compact(binding.popupId),
        compact(binding.element?.getAttribute("aria-label")),
        normalizedOptionValues(ariaComboboxOptions(binding, false)),
        Boolean(binding.required), maximum.status, maximum.value
      ]);
    }
    const element = binding;
    const type = controlType(element);
    const maximum = maxLengthEvidence(element);
    const stableIdentity = [
      compact(element?.getAttribute?.("name")), compact(element?.getAttribute?.("autocomplete")),
      compact(element?.getAttribute?.("placeholder")), compact(element?.getAttribute?.("aria-label"))
    ];
    const options = element instanceof HTMLSelectElement
      ? Array.from(element.options).map((option) => [
        normalizedChoice(option.value), normalizedChoice(option.textContent), Boolean(option.disabled)
      ])
      : [];
    const visibleText = type === "button" || type === "submit"
      ? compact(element?.innerText || element?.textContent || element?.value)
      : "";
    return JSON.stringify([
      "control", compact(element?.tagName).toLowerCase(), type, ...stableIdentity,
      compact(labelFor(element)), visibleText, options,
      Boolean(element?.required || element?.getAttribute?.("aria-required") === "true"),
      type === "file" ? compact(element?.getAttribute?.("accept")) : "",
      type === "file" ? Boolean(element?.multiple) : false,
      maximum.status, maximum.value
    ]);
  }

  async function addCollectedControlSemantics(collected) {
    if (collected?.status !== "COLLECTED") return collected;
    const signatures = new Map();
    for (const clientRef of collected.payload.client_refs || []) {
      const binding = controlMap.get(clientRef);
      if (!binding) {
        return {status: "BLOCKED", code: "COMPANION_CONTROL_CHANGED", client_ref: clientRef};
      }
      signatures.set(clientRef, bindingRebindSignature(binding));
    }
    const semantics = Object.create(null);
    for (const clientRef of collected.payload.client_refs || []) {
      const binding = controlMap.get(clientRef);
      if (!binding || bindingRebindSignature(binding) !== signatures.get(clientRef)) {
        return {status: "BLOCKED", code: "COMPANION_CONTROL_CHANGED", client_ref: clientRef};
      }
      semantics[clientRef] = await sha256(signatures.get(clientRef));
    }
    if ([...signatures].some(([clientRef, signature]) => (
      !bindingIsConnected(controlMap.get(clientRef)) || bindingRebindSignature(controlMap.get(clientRef)) !== signature
    ))) {
      return {status: "BLOCKED", code: "COMPANION_CONTROL_CHANGED"};
    }
    collected.payload.control_semantics_sha256 = semantics;
    return collected;
  }

  function primaryBindingElement(binding) {
    if (binding?.jobflowChoiceGroup) return binding.elements[0] || null;
    if (binding?.jobflowCustomSelect || binding?.jobflowAriaCombobox) return binding.element || null;
    return binding || null;
  }

  function bindingIsConnected(binding) {
    if (binding?.jobflowChoiceGroup) {
      return binding.elements.length > 0 && binding.elements.every((element) => element.isConnected);
    }
    return Boolean(primaryBindingElement(binding)?.isConnected);
  }

  function bindingRetainsValue(binding, value) {
    const desired = normalizedChoice(value);
    if (binding?.jobflowChoiceGroup) {
      const selected = binding.elements.find((element) => {
        const label = normalizedChoice(labelFor(element));
        const raw = normalizedChoice(element.getAttribute("value"));
        return desired === label || (raw && desired === raw);
      });
      return Boolean(selected && choiceApplied(selected));
    }
    if (binding?.jobflowCustomSelect) {
      return normalizedChoice(customSelectCurrentLabel(binding)) === desired;
    }
    if (binding?.jobflowAriaCombobox) {
      return normalizedChoice(ariaComboboxCurrentLabel(binding)) === desired;
    }
    const element = binding;
    if (element instanceof HTMLSelectElement) {
      const selected = element.options[element.selectedIndex] || null;
      return Boolean(selected && (
        String(selected.value) === String(value) || normalizedChoice(selected.textContent) === desired
      ));
    }
    return (element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement) &&
      String(element.value) === String(value);
  }

  function rebindControlsAfterDomChange(requiredRefs, signatures) {
    const snapshot = serializedFormSnapshot();
    if (snapshot.ambiguousUploadCount > 0) {
      return {status: "BLOCKED", code: "COMPANION_AMBIGUOUS_FILE_CONTROLS"};
    }
    const current = snapshot.controls.map((binding, index) => ({
      binding, clientRef: snapshot.clientRefs[index], signature: bindingRebindSignature(binding)
    }));
    const rebound = new Map();
    for (const clientRef of requiredRefs) {
      const expected = signatures.get(clientRef);
      const matches = current.filter((item) => item.signature === expected);
      if (matches.length !== 1) {
        return {status: "BLOCKED", code: "COMPANION_CONTROL_REBIND_FAILED", client_ref: clientRef};
      }
      rebound.set(clientRef, matches[0].binding);
    }
    for (const [clientRef, binding] of rebound) controlMap.set(clientRef, binding);
    return {status: "REBOUND"};
  }

  function approvedSemanticHash(message, clientRef) {
    const hashes = message?.control_semantics_sha256;
    if (!hashes || typeof hashes !== "object" || Array.isArray(hashes)) return "";
    const value = Object.prototype.hasOwnProperty.call(hashes, clientRef) ? String(hashes[clientRef] || "") : "";
    return /^sha256:[0-9a-f]{64}$/.test(value) ? value : "";
  }

  async function rebindControlsFromApprovedSemantics(requiredRefs, message) {
    const snapshot = serializedFormSnapshot();
    if (snapshot.ambiguousUploadCount > 0) {
      return {status: "BLOCKED", code: "COMPANION_AMBIGUOUS_FILE_CONTROLS"};
    }
    const current = [];
    for (let index = 0; index < snapshot.controls.length; index += 1) {
      const binding = snapshot.controls[index];
      const signature = bindingRebindSignature(binding);
      current.push({
        binding, clientRef: snapshot.clientRefs[index], signature,
        hash: await sha256(signature)
      });
    }
    // Hashing yields to the page.  Reject a page that changed while its
    // semantics were being measured instead of approving a mixed snapshot.
    if (current.some((item) => bindingRebindSignature(item.binding) !== item.signature)) {
      return {status: "BLOCKED", code: "COMPANION_CONTROL_CHANGED"};
    }
    const rebound = new Map();
    const signatures = new Map();
    const usedBindings = new Set();
    for (const clientRef of requiredRefs) {
      const expected = approvedSemanticHash(message, clientRef);
      if (!expected) {
        return {status: "BLOCKED", code: "COMPANION_APPROVED_SEMANTICS_MISSING", client_ref: clientRef};
      }
      const matches = current.filter((item) => item.hash === expected);
      if (matches.length !== 1 || usedBindings.has(matches[0]?.binding)) {
        return {status: "BLOCKED", code: "COMPANION_CONTROL_REBIND_FAILED", client_ref: clientRef};
      }
      usedBindings.add(matches[0].binding);
      rebound.set(clientRef, matches[0].binding);
      signatures.set(clientRef, matches[0].signature);
    }
    for (const [clientRef, binding] of rebound) controlMap.set(clientRef, binding);
    return {status: "REBOUND", signatures};
  }

  function bindingElements(binding) {
    if (binding?.jobflowChoiceGroup) return binding.elements;
    const element = primaryBindingElement(binding);
    return element ? [element] : [];
  }

  function bindingUsable(binding) {
    const elements = bindingElements(binding);
    return elements.length > 0 && elements.every((element) => (
      element.isConnected && !element.disabled && element.getAttribute?.("aria-disabled") !== "true" && visible(element)
    ));
  }

  function preflightFieldOptionFailure(binding, value) {
    const desired = normalizedChoice(value);
    if (binding?.jobflowChoiceGroup) {
      const target = binding.elements.find((element) => (
        normalizedChoice(labelFor(element)) === desired ||
        (normalizedChoice(element.getAttribute("value")) && normalizedChoice(element.getAttribute("value")) === desired)
      ));
      return target && bindingUsable({jobflowChoiceGroup: true, elements: [target]})
        ? null : "COMPANION_CHOICE_OPTION_NOT_FOUND";
    }
    if (binding?.jobflowCustomSelect) {
      if (normalizedChoice(customSelectCurrentLabel(binding)) === desired) return null;
      return customSelectOptions(binding, false).some((candidate) => (
        normalizedChoice(customOptionValue(candidate)) === desired
      )) ? null : "COMPANION_CUSTOM_SELECT_OPTION_NOT_FOUND";
    }
    if (binding?.jobflowAriaCombobox) {
      if (normalizedChoice(ariaComboboxCurrentLabel(binding)) === desired) return null;
      return ariaComboboxOptions(binding, false).some((candidate) => (
        normalizedChoice(customOptionValue(candidate)) === desired
      )) ? null : "COMPANION_ARIA_COMBOBOX_OPTION_NOT_FOUND";
    }
    const element = binding;
    if (element instanceof HTMLSelectElement) {
      const target = Array.from(element.options).find((option) => (
        String(option.value) === String(value) || normalizedChoice(option.textContent) === desired
      ));
      return target && !target.disabled ? null : "COMPANION_SELECT_OPTION_NOT_FOUND";
    }
    if (!(element instanceof HTMLInputElement) && !(element instanceof HTMLTextAreaElement)) {
      return "COMPANION_CONTROL_TYPE_UNSUPPORTED";
    }
    return null;
  }

  function duplicateReferenceFailure(fields, files, finalSubmitRefs, navigationRef) {
    const fieldRefs = fields.map((item) => String(item.client_ref || ""));
    const fileRefs = files.map((item) => String(item.client_ref || ""));
    const finalRefs = finalSubmitRefs.map(String);
    const allRefs = [...fieldRefs, ...fileRefs, ...finalRefs, ...(navigationRef ? [navigationRef] : [])];
    if (allRefs.some((clientRef) => !clientRef)) return "COMPANION_CONTROL_CHANGED";
    if (new Set(fieldRefs).size !== fieldRefs.length || new Set(fileRefs).size !== fileRefs.length ||
        new Set(finalRefs).size !== finalRefs.length || new Set(allRefs).size !== allRefs.length) {
      return "COMPANION_DUPLICATE_CONTROL_BINDING";
    }
    return null;
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
    domOperationStage = "APPLY_INIT";
    const fieldBindings = [];
    const materialBindings = [];
    const attemptedFieldBindings = [];
    const attemptedMaterialBindings = [];
    const blockedApply = (code, clientRef = null) => ({
      status: "BLOCKED",
      code,
      ...(clientRef ? {client_ref: clientRef} : {}),
      field_bindings: fieldBindings,
      material_bindings: materialBindings,
      attempted_field_bindings: attemptedFieldBindings,
      attempted_material_bindings: attemptedMaterialBindings,
      partial_effects: attemptedFieldBindings.length > 0 || attemptedMaterialBindings.length > 0
    });
    finalSubmitElements.clear();
    navigationElement = null;
    navigationProof = null;
    armedManualChallenge = null;
    manualSignalSent = false;
    pendingManualClick = null;
    const fields = Array.isArray(message.fields) ? message.fields : [];
    const files = Array.isArray(message.files) ? message.files : [];
    const finalSubmitRefs = (message.final_submit_client_refs || []).map(String);
    const navigationRef = message.navigation?.client_ref ? String(message.navigation.client_ref) : null;
    const duplicateFailure = duplicateReferenceFailure(fields, files, finalSubmitRefs, navigationRef);
    if (duplicateFailure) return blockedApply(duplicateFailure);
    const rebindRefs = [
      ...fields.map((item) => String(item.client_ref)),
      ...files.map((item) => String(item.client_ref)),
      ...finalSubmitRefs, ...(navigationRef ? [navigationRef] : [])
    ];

    const validateEveryBinding = async () => {
      for (const item of fields) {
        const clientRef = String(item.client_ref);
        const binding = controlMap.get(clientRef);
        if (!bindingUsable(binding)) return {code: "COMPANION_CONTROL_CHANGED", clientRef};
        const maximumFailure = fieldMaxLengthFailure(binding, item);
        if (maximumFailure) return {code: maximumFailure, clientRef};
        if (await sha256(String(item.value)) !== String(item.value_sha256 || "")) {
          return {code: "COMPANION_VALUE_HASH_MISMATCH", clientRef};
        }
        const optionFailure = preflightFieldOptionFailure(binding, String(item.value));
        if (optionFailure) return {code: optionFailure, clientRef};
      }
      for (const item of files) {
        const clientRef = String(item.client_ref);
        const binding = controlMap.get(clientRef);
        const element = primaryBindingElement(binding);
        if (
          binding?.jobflowChoiceGroup || binding?.jobflowCustomSelect || binding?.jobflowAriaCombobox ||
          !(element instanceof HTMLInputElement) || element.type.toLowerCase() !== "file" || !bindingUsable(binding)
        ) return {code: "COMPANION_FILE_CONTROL_CHANGED", clientRef};
      }
      for (const clientRef of finalSubmitRefs) {
        const binding = controlMap.get(clientRef);
        const element = primaryBindingElement(binding);
        if (
          binding?.jobflowChoiceGroup || binding?.jobflowCustomSelect || binding?.jobflowAriaCombobox ||
          !bindingUsable(binding) ||
          (!(element instanceof HTMLButtonElement) && !(
            element instanceof HTMLInputElement && ["button", "submit"].includes(element.type.toLowerCase())
          ))
        ) return {code: "COMPANION_FINAL_CONTROL_CHANGED", clientRef};
      }
      if (navigationRef) {
        const binding = controlMap.get(navigationRef);
        const element = primaryBindingElement(binding);
        if (
          binding?.jobflowChoiceGroup || binding?.jobflowCustomSelect || binding?.jobflowAriaCombobox ||
          !bindingUsable(binding) ||
          (!(element instanceof HTMLButtonElement) && !(
            element instanceof HTMLInputElement && ["button", "submit"].includes(element.type.toLowerCase())
          ))
        ) return {code: "COMPANION_NAVIGATION_CONTROL_CHANGED", clientRef: navigationRef};
      }
      return null;
    };

    // Approval-time browser semantics are opaque to the host.  The host must
    // echo the hash that arrived with the reviewed snapshot; the companion
    // remeasures every protected control before the first write.
    domOperationStage = "APPROVED_SEMANTICS_PREFLIGHT";
    let approvedState = await rebindControlsFromApprovedSemantics(rebindRefs, message);
    if (approvedState.status !== "REBOUND") {
      return blockedApply(approvedState.code, approvedState.client_ref);
    }
    let rebindSignatures = approvedState.signatures;
    let validationFailure = await validateEveryBinding();
    if (validationFailure) return blockedApply(validationFailure.code, validationFailure.clientRef);

    // Fetch and hash every approved attachment before touching a file input or
    // applicant field.  A later stream/hash failure therefore has zero page
    // effects rather than leaving a half-filled form.
    domOperationStage = "FILE_PREFLIGHT";
    const preparedFiles = [];
    for (const item of files) {
      try { preparedFiles.push(await prepareApprovedFile(item)); }
      catch (error) {
        const code = error?.message === "COMPANION_FILE_HASH_MISMATCH"
          ? error.message : "COMPANION_FILE_FETCH_FAILED";
        return blockedApply(code, item.client_ref);
      }
    }

    // File streaming and cryptographic hashing yield to page scripts.  Take a
    // second complete snapshot and repeat every deterministic validation.  A
    // final synchronous signature comparison closes the last pre-write gap.
    domOperationStage = "FINAL_PREWRITE_PREFLIGHT";
    approvedState = await rebindControlsFromApprovedSemantics(rebindRefs, message);
    if (approvedState.status !== "REBOUND") {
      return blockedApply(approvedState.code, approvedState.client_ref);
    }
    rebindSignatures = approvedState.signatures;
    validationFailure = await validateEveryBinding();
    if (validationFailure) return blockedApply(validationFailure.code, validationFailure.clientRef);
    if ([...rebindSignatures].some(([clientRef, signature]) => (
      !bindingIsConnected(controlMap.get(clientRef)) || bindingRebindSignature(controlMap.get(clientRef)) !== signature
    ))) return blockedApply("COMPANION_CONTROL_CHANGED");

    if (message.navigation?.client_ref) {
      navigationProof = {
        client_ref: navigationRef,
        mode: String(message.navigation.mode || ""),
        control_type: String(message.navigation.control_type || ""),
        page_content_hash: String(message.navigation.page_content_hash || ""),
        control_semantics_hash: String(message.navigation.control_semantics_hash || ""),
        display_label: compact(message.navigation.display_label || "")
      };
    }
    const remainingBindingRefs = (fieldIndex) => [...new Set([
      ...fields.slice(fieldIndex).map((item) => String(item.client_ref)),
      ...finalSubmitRefs, ...(navigationRef ? [navigationRef] : [])
    ])];
    const ensureBindings = (refs) => {
      if (refs.every((clientRef) => {
        const binding = controlMap.get(clientRef);
        return bindingIsConnected(binding) && bindingRebindSignature(binding) === rebindSignatures.get(clientRef);
      })) {
        return {status: "CURRENT"};
      }
      return rebindControlsAfterDomChange(refs, rebindSignatures);
    };
    const refreshProtectedControls = () => {
      finalSubmitElements.clear();
      for (const clientRef of finalSubmitRefs) {
        domOperationStage = "FINAL_CONTROLS";
        const binding = controlMap.get(clientRef);
        const element = primaryBindingElement(binding);
        if (!bindingUsable(binding) || !element) {
          return {status: "BLOCKED", code: "COMPANION_FINAL_CONTROL_CHANGED", client_ref: clientRef};
        }
        finalSubmitElements.add(element);
      }
      navigationElement = null;
      if (navigationRef) {
        const binding = controlMap.get(navigationRef);
        navigationElement = primaryBindingElement(binding);
        if (!bindingUsable(binding) || !navigationElement || finalSubmitElements.has(navigationElement)) {
          return {status: "BLOCKED", code: "COMPANION_NAVIGATION_CONTROL_CHANGED", client_ref: navigationRef};
        }
      }
      return {status: "CURRENT"};
    };
    const initialProtectedControls = refreshProtectedControls();
    if (initialProtectedControls.status !== "CURRENT") {
      return blockedApply(initialProtectedControls.code, initialProtectedControls.client_ref);
    }

    for (let fileIndex = 0; fileIndex < files.length; fileIndex += 1) {
      const item = files[fileIndex];
      const clientRef = String(item.client_ref);
      const binding = controlMap.get(clientRef);
      const element = primaryBindingElement(binding);
      if (!(element instanceof HTMLInputElement) || element.type.toLowerCase() !== "file" || !bindingUsable(binding)) {
        return blockedApply("COMPANION_FILE_CONTROL_CHANGED", clientRef);
      }
      attemptedMaterialBindings.push({client_ref: clientRef, purpose: item.purpose, sha256: item.sha256});
      try { await attachPreparedFile(element, item, preparedFiles[fileIndex]); }
      catch (error) {
        const code = error?.message === "COMPANION_FILE_VERIFY_FAILED"
          ? error.message : "COMPANION_FILE_APPLY_FAILED";
        return blockedApply(code, clientRef);
      }
      materialBindings.push({client_ref: clientRef, purpose: item.purpose, sha256: item.sha256});
      const remainingRefs = [
        ...files.slice(fileIndex + 1).map((candidate) => String(candidate.client_ref)),
        ...fields.map((candidate) => String(candidate.client_ref)),
        ...finalSubmitRefs, ...(navigationRef ? [navigationRef] : [])
      ];
      if (remainingRefs.length) {
        const rebound = rebindControlsAfterDomChange(remainingRefs, rebindSignatures);
        if (rebound.status !== "REBOUND") return blockedApply(rebound.code, rebound.client_ref);
        const protectedControls = refreshProtectedControls();
        if (protectedControls.status !== "CURRENT") {
          return blockedApply(protectedControls.code, protectedControls.client_ref);
        }
      }
    }

    for (let fieldIndex = 0; fieldIndex < fields.length; fieldIndex += 1) {
      const item = fields[fieldIndex];
      const requiredRefs = remainingBindingRefs(fieldIndex);
      const current = ensureBindings(requiredRefs);
      if (!["CURRENT", "REBOUND"].includes(current.status)) {
        return blockedApply(current.code, current.client_ref);
      }
      if (current.status === "REBOUND") {
        const protectedControls = refreshProtectedControls();
        if (protectedControls.status !== "CURRENT") {
          return blockedApply(protectedControls.code, protectedControls.client_ref);
        }
      }
      domOperationStage = "FIELD_BINDING";
      const binding = controlMap.get(item.client_ref);
      const element = primaryBindingElement(binding);
      domOperationStage = "FIELD_ELEMENT_SELECTED";
      if (!element || !bindingUsable(binding)) {
        return blockedApply("COMPANION_CONTROL_CHANGED", item.client_ref);
      }
      const maxLengthFailure = fieldMaxLengthFailure(binding, item);
      if (maxLengthFailure) return blockedApply(maxLengthFailure, item.client_ref);
      const optionFailure = preflightFieldOptionFailure(binding, String(item.value));
      if (optionFailure) return blockedApply(optionFailure, item.client_ref);
      domOperationStage = "FIELD_ELEMENT_VALIDATED";
      domOperationStage = "FIELD_HASHING";
      if (await sha256(String(item.value)) !== String(item.value_sha256 || "")) {
        return blockedApply("COMPANION_VALUE_HASH_MISMATCH", item.client_ref);
      }
      domOperationStage = "FIELD_HASH_VALIDATED";
      attemptedFieldBindings.push({client_ref: item.client_ref, value_sha256: item.value_sha256});
      domOperationStage = "FIELD_APPLY";
      try {
        if (binding?.jobflowChoiceGroup) await applyChoiceValue(binding, String(item.value));
        else if (binding?.jobflowCustomSelect) await applyCustomSelectValue(binding, String(item.value));
        else if (binding?.jobflowAriaCombobox) await applyAriaComboboxValue(binding, String(item.value));
        else setTextValue(element, String(item.value));
      }
      catch (error) { return blockedApply(fieldApplyFailureCode(error), item.client_ref); }
      await waitForDomQuiet({quietMilliseconds: 300, maximumMilliseconds: 1800});
      const afterApply = ensureBindings(requiredRefs);
      if (!["CURRENT", "REBOUND"].includes(afterApply.status)) {
        return blockedApply(afterApply.code, afterApply.client_ref);
      }
      if (afterApply.status === "REBOUND") {
        const protectedControls = refreshProtectedControls();
        if (protectedControls.status !== "CURRENT") {
          return blockedApply(protectedControls.code, protectedControls.client_ref);
        }
      }
      if (!bindingRetainsValue(controlMap.get(item.client_ref), String(item.value))) {
        return blockedApply("COMPANION_FIELD_VERIFY_FAILED", item.client_ref);
      }
      fieldBindings.push({client_ref: item.client_ref, value_sha256: item.value_sha256});
    }
    domOperationStage = "POST_APPLY_VALIDATION";
    if ([...finalSubmitElements].some((element) => !element.isConnected || !visible(element) || element.disabled)) {
      return blockedApply("COMPANION_FINAL_CONTROL_CHANGED");
    }
    if (navigationElement && (!navigationElement.isConnected || !visible(navigationElement) || navigationElement.disabled)) {
      return blockedApply("COMPANION_NAVIGATION_CONTROL_CHANGED");
    }
    return {
      status: "APPLIED",
      field_bindings: fieldBindings,
      material_bindings: materialBindings,
      final_submit_armed: finalSubmitElements.size > 0,
      navigation_ready: Boolean(navigationElement && navigationProof?.mode === "PROGRAMMATIC_EXPLICIT_BUTTON"),
      manual_navigation_required: Boolean(navigationElement && navigationProof?.mode === "MANUAL_USER_CLICK")
    };
  }

  function explicitButtonControl(element) {
    if (!(element instanceof HTMLButtonElement) && !(element instanceof HTMLInputElement)) return false;
    return compact(element.getAttribute("type")).toLowerCase() === "button" && controlType(element) === "button";
  }

  async function freshNavigationEvidence(clientRef, element) {
    const snapshot = serializedFormSnapshot();
    if (snapshot.ambiguousUploadCount > 0) {
      return {status: "BLOCKED", code: "COMPANION_AMBIGUOUS_FILE_CONTROLS"};
    }
    const index = snapshot.clientRefs.indexOf(clientRef);
    if (index < 0 || snapshot.controls[index] !== element) {
      return {status: "BLOCKED", code: "COMPANION_NAVIGATION_CONTROL_CHANGED"};
    }
    const pageContentHash = await sha256(snapshot.sanitizedHTML);
    const label = compact(element.innerText || element.textContent || element.value || element.getAttribute("aria-label"));
    const semanticsHash = await sha256(JSON.stringify([pageContentHash, clientRef, controlType(element), label]));
    if (
      !navigationProof || navigationProof.client_ref !== clientRef ||
      pageContentHash !== navigationProof.page_content_hash ||
      semanticsHash !== navigationProof.control_semantics_hash ||
      label !== navigationProof.display_label
    ) {
      return {status: "BLOCKED", code: "COMPANION_NAVIGATION_PROOF_STALE"};
    }
    return {status: "FRESH", page_content_hash: pageContentHash, control_semantics_hash: semanticsHash};
  }

  async function validateNavigation(message) {
    const element = controlMap.get(String(message.client_ref || ""));
    if (
      !element || element !== navigationElement || finalSubmitElements.has(element) ||
      !element.isConnected || element.disabled || !visible(element)
    ) {
      return {status: "BLOCKED", code: "COMPANION_NAVIGATION_CONTROL_CHANGED"};
    }
    if (!navigationProof || navigationProof.mode !== "PROGRAMMATIC_EXPLICIT_BUTTON" || !explicitButtonControl(element)) {
      return {status: "BLOCKED", code: "COMPANION_MANUAL_NAVIGATION_REQUIRED", form_valid: true};
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
    const fresh = await freshNavigationEvidence(String(message.client_ref || ""), element);
    if (fresh.status !== "FRESH") return fresh;
    return {
      status: "NAVIGATION_VALID", form_valid: true, final_submit: false,
      page_content_hash: fresh.page_content_hash,
      control_semantics_hash: fresh.control_semantics_hash
    };
  }

  async function navigateApproved(message) {
    const validation = await validateNavigation(message);
    if (validation.status !== "NAVIGATION_VALID") return validation;
    if (
      String(message.page_content_hash || "") !== validation.page_content_hash ||
      String(message.control_semantics_hash || "") !== validation.control_semantics_hash
    ) {
      return {status: "BLOCKED", code: "COMPANION_NAVIGATION_AUTHORIZATION_STALE"};
    }
    const element = controlMap.get(String(message.client_ref || ""));
    element.click();
    navigationProof = null;
    return {
      status: "NAVIGATION_STARTED", form_valid: true, final_submit: false,
      page_content_hash: validation.page_content_hash,
      control_semantics_hash: validation.control_semantics_hash
    };
  }

  async function armManualNavigation(message) {
    const challenge = message?.challenge;
    if (
      !navigationProof || navigationProof.mode !== "MANUAL_USER_CLICK" ||
      !navigationElement || !navigationElement.isConnected ||
      !challenge || typeof challenge !== "object"
    ) {
      return {status: "BLOCKED", code: "COMPANION_MANUAL_CHALLENGE_CONTEXT_CHANGED"};
    }
    if (
      !/^MNC-[A-F0-9]{32}$/.test(String(challenge.challenge_id || "")) ||
      !String(challenge.nonce || "") ||
      !/^sha256:[a-f0-9]{64}$/.test(String(challenge.challenge_hash || "")) ||
      String(challenge.document_instance_id || "") !== documentInstanceId ||
      String(challenge.stage || "") !== "MANUAL_NAVIGATION_REQUIRED" ||
      String(challenge.client_ref || "") !== navigationProof.client_ref ||
      String(challenge.prior_page_content_hash || "") !== navigationProof.page_content_hash ||
      String(challenge.control_semantics_hash || "") !== navigationProof.control_semantics_hash ||
      !Number.isInteger(challenge.tab_id) || challenge.tab_id < 0 ||
      !String(challenge.assist_id || "") || !String(challenge.application_id || "") ||
      !Number.isFinite(Date.parse(String(challenge.expires_at || ""))) ||
      Date.parse(String(challenge.expires_at || "")) <= Date.now()
    ) {
      return {status: "BLOCKED", code: "COMPANION_MANUAL_CHALLENGE_INVALID"};
    }
    const eventHash = await sha256(JSON.stringify([
      "MANUAL_FORWARD_CONTROL_CLICK",
      String(challenge.challenge_id),
      String(challenge.nonce),
      String(challenge.assist_id),
      String(challenge.application_id),
      challenge.tab_id,
      documentInstanceId,
      "MANUAL_NAVIGATION_REQUIRED",
      navigationProof.page_content_hash,
      navigationProof.control_semantics_hash,
      navigationProof.client_ref,
      false
    ]));
    armedManualChallenge = {...challenge, event_hash: eventHash};
    manualSignalSent = false;
    pendingManualClick = null;
    return {
      status: "MANUAL_NAVIGATION_ARMED",
      challenge_id: String(challenge.challenge_id),
      document_instance_id: documentInstanceId,
      expires_at: String(challenge.expires_at)
    };
  }

  function dispatchManualNavigation() {
    if (
      manualSignalSent || !navigationProof || navigationProof.mode !== "MANUAL_USER_CLICK" ||
      !armedManualChallenge || Date.parse(String(armedManualChallenge.expires_at || "")) <= Date.now()
    ) return;
    manualSignalSent = true;
    // Once cancellation has been resolved, the runtime message is dispatched
    // synchronously with no digest or other awaited work in between.  The same
    // function is safe to call from the immediate-unload fallback.
    chrome.runtime.sendMessage({
      type: "JOBFLOW_MANUAL_NAVIGATION_OBSERVED",
      payload: {
        url: location.href,
        trusted_user_event: true,
        event_hash: armedManualChallenge.event_hash,
        prior_page_content_hash: navigationProof.page_content_hash,
        control_semantics_hash: navigationProof.control_semantics_hash,
        manual_navigation_challenge_id: String(armedManualChallenge.challenge_id),
        manual_navigation_nonce: String(armedManualChallenge.nonce),
        manual_navigation_challenge_hash: String(armedManualChallenge.challenge_hash),
        manual_navigation_assist_id: String(armedManualChallenge.assist_id),
        manual_navigation_application_id: String(armedManualChallenge.application_id),
        manual_navigation_tab_id: armedManualChallenge.tab_id,
        manual_navigation_document_id: documentInstanceId,
        manual_navigation_stage: "MANUAL_NAVIGATION_REQUIRED",
        manual_navigation_client_ref: navigationProof.client_ref,
        manual_navigation_default_prevented: false
      }
    }).catch(() => undefined);
  }

  function signalManualNavigationAfterDispatch(event) {
    // Event cancellation is only final after all page listeners have run.  A
    // microtask preserves immediate-navigation reliability while refusing
    // preventDefault flows, including SPA handlers that rewrite the page.
    queueMicrotask(() => {
      if (event.defaultPrevented) {
        pendingManualClick = null;
        return;
      }
      dispatchManualNavigation();
    });
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
    const formPresent = Boolean(deepQuery("form"));
    const submitPresent = Boolean(deepQuery('button[type="submit"],input[type="submit"],button:not([type])'));
    const successRoute = /\/(?:thank[-_]?you|confirmation|success|submitted|application[-_]?complete)(?:\/|$)/i.test(location.pathname);
    const invalidCount = deepQueryAll(':invalid,[aria-invalid="true"]').length;
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

  function eventControl(event, selector) {
    for (const candidate of event.composedPath?.() || [event.target]) {
      if (candidate instanceof Element && candidate.matches(selector)) return candidate;
    }
    return null;
  }

  document.addEventListener("submit", (event) => {
    if (!event.isTrusted) return;
    const submitter = event.submitter || null;
    if (submitter && submitter === navigationElement && navigationProof?.mode === "MANUAL_USER_CLICK") {
      pendingManualClick = null;
      signalManualNavigationAfterDispatch(event);
      return;
    }
    if (finalSubmitElements.size === 0) return;
    if ((submitter && finalSubmitElements.has(submitter)) || (!submitter && !navigationElement)) {
      signalUserSubmit("FORM_SUBMIT");
    }
  }, false);
  document.addEventListener("click", (event) => {
    if (!event.isTrusted) return;
    const target = eventControl(event, "button,input");
    if (target && target === navigationElement && navigationProof?.mode === "MANUAL_USER_CLICK") {
      // Capture only records the trusted candidate.  It never sends here:
      // later page listeners must still get an opportunity to preventDefault.
      pendingManualClick = {event, target};
    }
  }, true);
  document.addEventListener("click", (event) => {
    if (!event.isTrusted) return;
    const manualTarget = eventControl(event, "button,input");
    if (manualTarget && manualTarget === navigationElement && navigationProof?.mode === "MANUAL_USER_CLICK") {
      pendingManualClick = {event, target: manualTarget};
      queueMicrotask(() => {
        if (event.defaultPrevented) {
          pendingManualClick = null;
        } else if (!manualTarget.form) {
          dispatchManualNavigation();
        }
      });
    }
    const target = eventControl(event, 'button[type="submit"],input[type="submit"],button:not([type])');
    if (target && target.form && finalSubmitElements.has(target)) signalUserSubmit("SUBMIT_CONTROL_CLICK");
  }, false);
  window.addEventListener("beforeunload", () => {
    // Direct location changes can unload before a form submit event exists.
    // The trusted click object already reflects every completed click listener.
    if (pendingManualClick && !pendingManualClick.event.defaultPrevented) dispatchManualNavigation();
  }, false);

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    (async () => {
      if (message?.type === "JOBFLOW_COLLECT_JOB_PAGE") return collectJobPage();
      if (message?.type === "JOBFLOW_COLLECT_SEARCH_RESULTS") return collectSearchResults();
      if (message?.type === "JOBFLOW_COLLECT_FORM") return await collectFormWhenReady();
      if (message?.type === "JOBFLOW_APPLY_APPROVED") return await applyApproved(message);
      if (message?.type === "JOBFLOW_ARM_MANUAL_NAVIGATION") return await armManualNavigation(message);
      if (message?.type === "JOBFLOW_CHECK_NAVIGATION") return await validateNavigation(message);
      if (message?.type === "JOBFLOW_NAVIGATE_APPROVED") return await navigateApproved(message);
      if (message?.type === "JOBFLOW_COLLECT_RESULT") {
        const payload = await collectResult();
        await chrome.runtime.sendMessage({type: "JOBFLOW_RESULT_SIGNALS", payload});
        return {status: "RESULT_COLLECTED"};
      }
      return {status: "IGNORED"};
    })().then(sendResponse).catch((error) => sendResponse({
      status: "BLOCKED", code: "COMPANION_DOM_ERROR", diagnostic_code: safeDomDiagnosticCode(error),
      diagnostic_stage: domOperationStage
    }));
    return true;
  });
})();
