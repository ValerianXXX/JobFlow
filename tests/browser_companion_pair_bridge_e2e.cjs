"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const project = path.resolve(__dirname, "..");
const source = fs.readFileSync(path.join(project, "browser-companion", "pair.js"), "utf8");
const pageListeners = [];
const runtimeListeners = [];
const posted = [];
const runtimeMessages = [];
let randomSequence = 0;
let deferPair = false;
let releasePair = null;

const windowObject = {
  addEventListener(type, listener) {
    if (type === "message") pageListeners.push(listener);
  },
  postMessage(message, targetOrigin) {
    posted.push({message, targetOrigin});
  }
};

const sandbox = {
  chrome: {
    runtime: {
      getManifest() { return {version: "0.7.1"}; },
      async sendMessage(message) {
        runtimeMessages.push(message);
        if (deferPair) {
          deferPair = false;
          await new Promise((resolve) => { releasePair = resolve; });
        }
        return {
          status: "GUIDED_INTAKE_PAIRED",
          intake_id: "GIN-PAIR-BRIDGE",
          protocol_version: 2,
          extension_version: "0.7.1"
        };
      },
      onMessage: {
        addListener(listener) { runtimeListeners.push(listener); }
      }
    }
  },
  Date: {now() { return 1_800_000_000_000; }},
  Math: {random() { randomSequence += 1; return randomSequence / 10; }},
  location: {hostname: "127.0.0.1", origin: "http://127.0.0.1:43123"},
  window: windowObject
};
vm.createContext(sandbox);

function executeBridge() {
  vm.runInContext(source, sandbox, {filename: "pair.js"});
  return sandbox.__jobflowPairBridgeGeneration;
}

(async () => {
  const firstGeneration = executeBridge();
  const secondGeneration = executeBridge();

  assert.notEqual(firstGeneration, secondGeneration, "reinjection must replace the bridge generation");
  assert.equal(pageListeners.length, 2, "both generations remain registered so the old one must self-disable");
  assert.equal(runtimeListeners.length, 2, "both runtime listeners remain registered so generation gating is required");

  const pairing = {
    protocol_version: 2,
    base_url: "http://127.0.0.1:43123",
    assist_path: `/intake/${"a".repeat(54)}`
  };
  const event = {
    source: windowObject,
    origin: sandbox.location.origin,
    data: {type: "JOBFLOW_PAIR_REQUEST", protocol_version: 2, pairing}
  };
  await Promise.all(pageListeners.map((listener) => listener(event)));

  assert.equal(runtimeMessages.length, 1, "only the newest generation may forward a pair request");
  assert.deepEqual(
    JSON.parse(JSON.stringify(runtimeMessages[0])),
    {type: "JOBFLOW_PAIR", pairing}
  );
  assert.equal(
    posted.filter((item) => item.message.type === "JOBFLOW_PAIR_RESULT").length,
    1,
    "only the newest generation may publish a pair result"
  );
  await pageListeners[1](event);
  assert.equal(runtimeMessages.length, 1, "each explicit popup injection may forward only one pair attempt");

  const status = {
    type: "JOBFLOW_INTAKE_STATUS",
    result: {status: "AWAITING_JOB_PAGE_CAPTURE", intake_id: "GIN-PAIR-BRIDGE"}
  };
  runtimeListeners.forEach((listener) => listener(status));
  const forwardedStatuses = posted.filter((item) => item.message.type === "JOBFLOW_INTAKE_STATUS");
  assert.equal(forwardedStatuses.length, 1, "only the newest generation may publish a status event");
  assert.deepEqual(JSON.parse(JSON.stringify(forwardedStatuses[0].message.result)), status.result);

  const pairResultsBeforeRace = posted.filter((item) => item.message.type === "JOBFLOW_PAIR_RESULT").length;
  executeBridge();
  deferPair = true;
  const staleCompletion = pageListeners[2](event);
  while (!releasePair) await new Promise((resolve) => setImmediate(resolve));
  executeBridge();
  releasePair();
  await staleCompletion;
  assert.equal(
    posted.filter((item) => item.message.type === "JOBFLOW_PAIR_RESULT").length,
    pairResultsBeforeRace,
    "a stale bridge completion must not publish after a newer popup injection"
  );

  process.stdout.write(JSON.stringify({
    status: "PASS",
    reinjected_generations: 4,
    pair_requests_forwarded: runtimeMessages.length,
    pair_results_published: 1,
    status_events_published: forwardedStatuses.length,
    stale_generation_noop: true, stale_async_completion_noop: true,
    real_external_actions: 0
  }));
})().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
