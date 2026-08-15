(() => {
  "use strict";
  const PROTOCOL = 2;
  const EXTENSION_VERSION = chrome.runtime.getManifest().version;
  const GENERATION = `${EXTENSION_VERSION}:${Date.now()}:${Math.random()}`;
  let pairAttempted = false;
  const allowedPage = location.hostname === "127.0.0.1" || location.hostname === "localhost";
  if (!allowedPage) return;

  const announceReady = () => window.postMessage({
    type: "JOBFLOW_COMPANION_READY", protocol_version: PROTOCOL
  }, location.origin);
  globalThis.__jobflowPairBridgeGeneration = GENERATION;

  window.addEventListener("message", async (event) => {
    if (globalThis.__jobflowPairBridgeGeneration !== GENERATION) return;
    if (event.source !== window || event.origin !== location.origin) return;
    const message = event.data;
    if (!message || message.type !== "JOBFLOW_PAIR_REQUEST" || message.protocol_version !== PROTOCOL) return;
    if (pairAttempted) return;
    pairAttempted = true;
    try {
      const result = await chrome.runtime.sendMessage({type: "JOBFLOW_PAIR", pairing: message.pairing});
      if (globalThis.__jobflowPairBridgeGeneration !== GENERATION) return;
      window.postMessage({type: "JOBFLOW_PAIR_RESULT", protocol_version: PROTOCOL, result}, location.origin);
    } catch (_error) {
      if (globalThis.__jobflowPairBridgeGeneration !== GENERATION) return;
      window.postMessage({
        type: "JOBFLOW_PAIR_RESULT",
        protocol_version: PROTOCOL,
        result: {
          status: "BLOCKED", code: "COMPANION_PAIR_FAILED",
          protocol_version: PROTOCOL, extension_version: EXTENSION_VERSION
        }
      }, location.origin);
    }
  });

  chrome.runtime.onMessage.addListener((message) => {
    if (globalThis.__jobflowPairBridgeGeneration !== GENERATION) return;
    if (!message || !["JOBFLOW_ASSIST_STATUS", "JOBFLOW_INTAKE_STATUS"].includes(message.type)) return;
    window.postMessage({
      type: message.type,
      protocol_version: PROTOCOL,
      result: message.result
    }, location.origin);
  });

  announceReady();
})();
