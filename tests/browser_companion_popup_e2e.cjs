"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const project = path.resolve(__dirname, "..");
const source = fs.readFileSync(path.join(project, "browser-companion", "popup.js"), "utf8");
assert.match(source, /chrome\.permissions\.request\s*\(/);
assert.match(source, /permissions:\s*\["search"\]/);
assert.match(source, /origins:\s*\["https:\/\/\*\/\*"\]/);

function element(id) {
  return {
    id,
    textContent: "",
    disabled: false,
    listeners: {},
    addEventListener(type, listener) {
      (this.listeners[type] ||= []).push(listener);
    }
  };
}

const elements = Object.fromEntries(
  ["subtitle", "version", "state", "fill", "boundary", "message", "zh", "en"]
    .map((id) => [id, element(id)])
);
const activeTab = {id: 73, url: "https://careers.example.test/jobs/role-1"};
const runtimeMessages = [];
let permissionRequests = 0;
let permissionGranted = false;

let pairedStatus = {
  status: "AWAITING_JOB_PAGE_CAPTURE",
  paired: true,
  mode: "JOB_CAPTURE",
  intake_id: "GIN-POPUP-ACTIVE-TAB",
  allowed_company_domain: "example.test"
};

const sandbox = {
  URL,
  document: {
    getElementById(id) { return elements[id]; }
  },
  chrome: {
    runtime: {
      getManifest() { return {version: "0.7.0"}; },
      async sendMessage(message) {
        runtimeMessages.push(message);
        if (message.type === "JOBFLOW_GET_STATUS") return pairedStatus;
        if (message.type === "JOBFLOW_RUN_GUIDED_AUTOPILOT") {
          const result = {
            status: pairedStatus.status === "AWAITING_APPLICATION_FORM_CAPTURE"
              ? "PREPARING_APPLICATION" : "AWAITING_APPLICATION_FORM_CAPTURE",
            intake_id: pairedStatus.intake_id
          };
          pairedStatus = {...pairedStatus, status: result.status};
          return result;
        }
        throw new Error(`Unexpected message: ${message.type}`);
      }
    },
    tabs: {
      async query(query) {
        assert.equal(query.active, true);
        assert.equal(query.currentWindow, true);
        return [activeTab];
      }
    },
    scripting: {
      async executeScript() { throw new Error("pairing injection is not expected for an already paired capture"); }
    },
    permissions: {
      async contains() { return permissionGranted; },
      async request() {
        permissionRequests += 1;
        return permissionGranted;
      }
    }
  },
  setTimeout,
  clearTimeout,
  console
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox, {filename: "popup.js"});

(async () => {
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(elements.fill.disabled, false, "the user-click activeTab path must remain available");
  assert.equal(elements.fill.listeners.click.length, 1);

  await elements.fill.listeners.click[0]();
  assert.equal(permissionRequests, 1, "the first explicit click must request search and company-site access");
  assert.match(elements.message.textContent, /未获得浏览器搜索/);
  assert.equal(runtimeMessages.filter((message) => message.type === "JOBFLOW_RUN_GUIDED_AUTOPILOT").length, 0);

  permissionGranted = true;
  await elements.fill.listeners.click[0]();

  const captureMessages = runtimeMessages.filter((message) => message.type === "JOBFLOW_RUN_GUIDED_AUTOPILOT");
  assert.equal(captureMessages.length, 1, "the popup must start the bounded guided autopilot");
  assert.deepEqual(
    JSON.parse(JSON.stringify(captureMessages[0])),
    {type: "JOBFLOW_RUN_GUIDED_AUTOPILOT"}
  );
  assert.equal(elements.message.textContent.includes("Apply"), true, "the successful automation should advance the user instruction");

  pairedStatus = {
    status: "AWAITING_APPLICATION_FORM_CAPTURE",
    paired: true,
    mode: "JOB_CAPTURE",
    intake_id: "GIN-POPUP-ACTIVE-TAB",
    allowed_company_domain: "example.test"
  };
  await vm.runInContext("refresh()", sandbox);
  await elements.fill.listeners.click[0]();
  assert.match(elements.message.textContent, /后台生成审阅包/);
  assert.equal(elements.fill.disabled, true, "background preparation must not be started twice");

  pairedStatus = {
    status: "MANUAL_NAVIGATION_RESTART_REQUIRED",
    paired: true,
    mode: "APPLICATION_ASSIST",
    assist_id: "BAS-POPUP-RESTART",
    application_id: "APP-POPUP-RESTART",
    allowed_page_origin: "https://careers.example.test",
    current_step: 1,
    max_steps: 20,
    last_result: {
      status: "MANUAL_NAVIGATION_RESTART_REQUIRED",
      code: "COMPANION_MANUAL_NAVIGATION_RESTART_REQUIRED",
      automatic_retry: false
    }
  };
  await vm.runInContext("refresh()", sandbox);
  assert.equal(elements.fill.disabled, true, "an unarmed one-use proof must disable current-page continuation");
  assert.equal(elements.fill.textContent, "返回 JobFlow 重新开始");
  assert.match(elements.message.textContent, /不会自动重试/);
  await elements.en.listeners.click[0]();
  assert.equal(elements.fill.textContent, "Return to JobFlow — restart");

  process.stdout.write(JSON.stringify({
    status: "PASS",
    user_gesture_permission_required: true,
    permission_denial_fail_closed: true,
    optional_host_permission_available: true,
    optional_permission_requests: permissionRequests,
    capture_messages: captureMessages.length,
    restart_required_bilingual: true,
    restart_required_disabled: true,
    real_external_actions: 0
  }));
})().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
