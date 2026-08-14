(() => {
  "use strict";
  const PROTOCOL = 2;
  const allowedPage = location.hostname === "127.0.0.1" || location.hostname === "localhost";
  if (!allowedPage) return;

  window.addEventListener("message", async (event) => {
    if (event.source !== window || event.origin !== location.origin) return;
    const message = event.data;
    if (!message || message.type !== "JOBFLOW_PAIR_REQUEST" || message.protocol_version !== PROTOCOL) return;
    try {
      const result = await chrome.runtime.sendMessage({type: "JOBFLOW_PAIR", pairing: message.pairing});
      window.postMessage({type: "JOBFLOW_PAIR_RESULT", protocol_version: PROTOCOL, result}, location.origin);
    } catch (_error) {
      window.postMessage({
        type: "JOBFLOW_PAIR_RESULT",
        protocol_version: PROTOCOL,
        result: {status: "BLOCKED", code: "COMPANION_PAIR_FAILED"}
      }, location.origin);
    }
  });

  chrome.runtime.onMessage.addListener((message) => {
    if (!message || message.type !== "JOBFLOW_ASSIST_STATUS") return;
    window.postMessage({
      type: "JOBFLOW_ASSIST_STATUS",
      protocol_version: PROTOCOL,
      result: message.result
    }, location.origin);
  });
})();
