"use strict";

const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const {chromium} = require("playwright");

const project = path.resolve(__dirname, "..");
const fixture = fs.readFileSync(path.join(project, "tests", "fixtures", "synthetic-material-form.html"), "utf8");
const companion = path.join(project, "browser-companion", "dom.js");
const browserPath = process.env.JOBFLOW_TEST_BROWSER || "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const fileBytes = Buffer.from("synthetic approved resume\n", "utf8");
const fileHash = `sha256:${crypto.createHash("sha256").update(fileBytes).digest("hex")}`;

function valueHash(value) {
  return `sha256:${crypto.createHash("sha256").update(value, "utf8").digest("hex")}`;
}

(async () => {
  assert.ok(fs.existsSync(browserPath), `Browser executable is missing: ${browserPath}`);
  const browser = await chromium.launch({headless: true, executablePath: browserPath});
  try {
    const page = await browser.newPage();
    await page.route("https://example.test/**", (route) => route.fulfill({
      status: 200, contentType: "text/html; charset=utf-8", body: fixture
    }));
    await page.goto("https://example.test/apply", {waitUntil: "domcontentloaded"});
    await page.evaluate(({encodedFile}) => {
      globalThis.__jobflowMessages = [];
      globalThis.__jobflowListener = null;
      const decode = () => Uint8Array.from(atob(encodedFile), (character) => character.charCodeAt(0));
      globalThis.chrome = {
        runtime: {
          lastError: null,
          onMessage: {addListener(listener) { globalThis.__jobflowListener = listener; }},
          async sendMessage(message) { globalThis.__jobflowMessages.push(message); return {status: "RECORDED"}; },
          connect() {
            const messageListeners = [];
            const disconnectListeners = [];
            return {
              onMessage: {addListener(listener) { messageListeners.push(listener); }},
              onDisconnect: {addListener(listener) { disconnectListeners.push(listener); }},
              postMessage() {
                const bytes = decode();
                let binary = "";
                for (const value of bytes) binary += String.fromCharCode(value);
                queueMicrotask(() => {
                  for (const listener of messageListeners) listener({type: "chunk", data: btoa(binary)});
                  for (const listener of messageListeners) listener({type: "end"});
                });
              },
              disconnect() { for (const listener of disconnectListeners) listener(); }
            };
          }
        }
      };
    }, {encodedFile: fileBytes.toString("base64")});
    await page.addScriptTag({path: companion});
    await page.evaluate(() => {
      document.querySelector("form").addEventListener("submit", (event) => event.preventDefault());
      globalThis.__jobflowCall = (message) => new Promise((resolve) => {
        globalThis.__jobflowListener(message, {}, resolve);
      });
    });

    const jobCollected = await page.evaluate(() => globalThis.__jobflowCall({type: "JOBFLOW_COLLECT_JOB_PAGE"}));
    assert.equal(jobCollected.status, "COLLECTED");
    assert.ok(jobCollected.payload.job_title.length > 0);
    assert.ok(jobCollected.payload.visible_text.length > 0);
    assert.ok(!jobCollected.payload.visible_text.includes("synthetic approved resume"));

    const collected = await page.evaluate(() => globalThis.__jobflowCall({type: "JOBFLOW_COLLECT_FORM"}));
    assert.equal(collected.status, "COLLECTED");
    assert.equal(collected.payload.client_refs.length, 7);
    assert.deepEqual(collected.payload.blocker_signals, []);
    assert.ok(!collected.payload.sanitized_html.includes("synthetic approved resume"));

    const values = ["Synthetic Applicant", "https://github.com/example", "https://example.com/portfolio"];
    const fields = values.map((value, index) => ({
      client_ref: collected.payload.client_refs[index],
      value,
      value_sha256: valueHash(value)
    }));
    const files = [3, 4, 5].map((index, fileIndex) => ({
      client_ref: collected.payload.client_refs[index],
      purpose: ["resume", "cover_letter", "portfolio"][fileIndex],
      filename: ["resume.pdf", "cover-letter.pdf", "portfolio.pdf"][fileIndex],
      sha256: fileHash,
      download_url: `http://127.0.0.1/assist/synthetic/file/${fileIndex}`
    }));
    const fieldApplied = await page.evaluate(
      ({fields, finalRef}) => globalThis.__jobflowCall({
        type: "JOBFLOW_APPLY_APPROVED", fields, files: [], navigation: null,
        final_submit_client_refs: [finalRef]
      }),
      {fields, finalRef: collected.payload.client_refs[6]}
    );
    assert.equal(fieldApplied.status, "APPLIED", JSON.stringify(fieldApplied));
    const fileApplied = await page.evaluate(
      ({files, finalRef}) => globalThis.__jobflowCall({
        type: "JOBFLOW_APPLY_APPROVED", fields: [], files, navigation: null,
        final_submit_client_refs: [finalRef]
      }),
      {files, finalRef: collected.payload.client_refs[6]}
    );
    assert.equal(fileApplied.status, "APPLIED", JSON.stringify(fileApplied));
    assert.equal(fieldApplied.field_bindings.length, 3);
    assert.equal(fileApplied.material_bindings.length, 3);
    const state = await page.evaluate(() => ({
      values: [document.querySelector("#full_name").value, document.querySelector("#github").value, document.querySelector("#portfolio_url").value],
      files: [document.querySelector("#resume").files[0]?.name, document.querySelector("#cover_letter").files[0]?.name, document.querySelector("#portfolio_file").files[0]?.name],
      messages: globalThis.__jobflowMessages.slice()
    }));
    assert.deepEqual(state.values, values);
    assert.deepEqual(state.files, ["resume.pdf", "cover-letter.pdf", "portfolio.pdf"]);
    assert.equal(state.messages.filter((item) => item.type === "JOBFLOW_USER_SUBMIT_OBSERVED").length, 0);

    await page.locator("#submit").click();
    await page.waitForFunction(() => globalThis.__jobflowMessages.some((item) => item.type === "JOBFLOW_USER_SUBMIT_OBSERVED"));
    const afterClick = await page.evaluate(() => globalThis.__jobflowMessages.slice());
    const userSignals = afterClick.filter((item) => item.type === "JOBFLOW_USER_SUBMIT_OBSERVED");
    assert.equal(userSignals.length, 1);
    assert.equal(userSignals[0].payload.trusted_user_event, true);

    const navigationPage = await browser.newPage();
    const navigationFixture = fs.readFileSync(
      path.join(project, "tests", "fixtures", "synthetic-v2-workday-step-1.html"), "utf8"
    );
    await navigationPage.route("https://workday.example.test/**", (route) => route.fulfill({
      status: 200, contentType: "text/html; charset=utf-8", body: navigationFixture
    }));
    await navigationPage.goto("https://workday.example.test/apply", {waitUntil: "domcontentloaded"});
    const manualBridgeMessages = [];
    await navigationPage.exposeFunction("__recordManualBridge", (message) => { manualBridgeMessages.push(message); });
    await navigationPage.evaluate(() => {
      globalThis.__jobflowMessages = [];
      globalThis.__jobflowListener = null;
      globalThis.chrome = {
        runtime: {
          lastError: null,
          onMessage: {addListener(listener) { globalThis.__jobflowListener = listener; }},
          sendMessage(message) {
            globalThis.__jobflowMessages.push(message);
            if (message?.type === "JOBFLOW_MANUAL_NAVIGATION_OBSERVED") {
              globalThis.__recordManualBridge(message);
            }
            return Promise.resolve({status: "RECORDED"});
          },
          connect() { throw new Error("NO_FILE_STREAM_EXPECTED"); }
        }
      };
    });
    await navigationPage.addScriptTag({path: companion});
    await navigationPage.evaluate(() => {
      globalThis.__programmaticNavigationClicks = 0;
      document.querySelector("form").addEventListener("submit", (event) => event.preventDefault());
      document.querySelector("#next-1").addEventListener("click", (event) => {
        if (!event.isTrusted) globalThis.__programmaticNavigationClicks += 1;
        if (event.isTrusted) location.href = "/advanced";
      });
      globalThis.__jobflowCall = (message) => new Promise((resolve) => {
        globalThis.__jobflowListener(message, {}, resolve);
      });
    });
    const navCollected = await navigationPage.evaluate(() => globalThis.__jobflowCall({type: "JOBFLOW_COLLECT_FORM"}));
    const navValue = "Synthetic Applicant";
    const manualPageHash = valueHash(navCollected.payload.sanitized_html);
    const manualSemanticsHash = valueHash(JSON.stringify([
      manualPageHash, navCollected.payload.client_refs[1], "submit", "Next"
    ]));
    const navApplied = await navigationPage.evaluate(
      ({clientRefs, value, hash, pageHash, semanticsHash}) => globalThis.__jobflowCall({
        type: "JOBFLOW_APPLY_APPROVED",
        fields: [{client_ref: clientRefs[0], value, value_sha256: hash}],
        files: [], navigation: {
          client_ref: clientRefs[1], mode: "MANUAL_USER_CLICK", control_type: "submit",
          page_content_hash: pageHash, control_semantics_hash: semanticsHash, display_label: "Next"
        }, final_submit_client_refs: []
      }),
      {
        clientRefs: navCollected.payload.client_refs, value: navValue, hash: valueHash(navValue),
        pageHash: manualPageHash, semanticsHash: manualSemanticsHash
      }
    );
    assert.equal(navApplied.navigation_ready, false);
    assert.equal(navApplied.manual_navigation_required, true);
    const manualChallenge = {
      challenge_id: `MNC-${"A".repeat(32)}`, nonce: "synthetic-one-use-nonce",
      challenge_hash: `sha256:${"b".repeat(64)}`,
      issued_at: new Date(Date.now() - 1000).toISOString(),
      expires_at: new Date(Date.now() + 120000).toISOString(),
      assist_id: "BAS-SYNTHETIC", application_id: "APP-SYNTHETIC", tab_id: 42,
      document_instance_id: navCollected.payload.document_instance_id,
      stage: "MANUAL_NAVIGATION_REQUIRED", client_ref: navCollected.payload.client_refs[1],
      prior_page_content_hash: manualPageHash, control_semantics_hash: manualSemanticsHash
    };
    const armed = await navigationPage.evaluate(
      (challenge) => globalThis.__jobflowCall({type: "JOBFLOW_ARM_MANUAL_NAVIGATION", challenge}),
      manualChallenge
    );
    assert.equal(armed.status, "MANUAL_NAVIGATION_ARMED");
    const navCheck = await navigationPage.evaluate(
      (clientRef) => globalThis.__jobflowCall({type: "JOBFLOW_CHECK_NAVIGATION", client_ref: clientRef}),
      navCollected.payload.client_refs[1]
    );
    assert.equal(navCheck.code, "COMPANION_MANUAL_NAVIGATION_REQUIRED");
    const navStarted = await navigationPage.evaluate(
      ({clientRef, pageHash, semanticsHash}) => globalThis.__jobflowCall({
        type: "JOBFLOW_NAVIGATE_APPROVED", client_ref: clientRef,
        page_content_hash: pageHash, control_semantics_hash: semanticsHash
      }),
      {clientRef: navCollected.payload.client_refs[1], pageHash: manualPageHash, semanticsHash: manualSemanticsHash}
    );
    assert.equal(navStarted.code, "COMPANION_MANUAL_NAVIGATION_REQUIRED");
    assert.equal(await navigationPage.evaluate(() => globalThis.__programmaticNavigationClicks), 0);
    const navigationMessages = await navigationPage.evaluate(() => globalThis.__jobflowMessages.slice());
    assert.equal(navigationMessages.filter((item) => item.type === "JOBFLOW_USER_SUBMIT_OBSERVED").length, 0);
    await navigationPage.locator("#next-1").click();
    await navigationPage.waitForURL("https://workday.example.test/advanced");
    assert.equal(manualBridgeMessages.filter((item) => item.type === "JOBFLOW_MANUAL_NAVIGATION_OBSERVED").length, 1);
    const observedManual = manualBridgeMessages.find((item) => item.type === "JOBFLOW_MANUAL_NAVIGATION_OBSERVED").payload;
    assert.equal(observedManual.manual_navigation_challenge_id, manualChallenge.challenge_id);
    assert.equal(observedManual.manual_navigation_document_id, navCollected.payload.document_instance_id);
    assert.equal(observedManual.manual_navigation_tab_id, 42);
    assert.match(observedManual.event_hash, /^sha256:[a-f0-9]{64}$/);

    const preventedPage = await browser.newPage();
    await preventedPage.route("https://workday.example.test/**", (route) => route.fulfill({
      status: 200, contentType: "text/html; charset=utf-8", body: navigationFixture
    }));
    await preventedPage.goto("https://workday.example.test/apply", {waitUntil: "domcontentloaded"});
    const preventedBridgeMessages = [];
    await preventedPage.exposeFunction("__recordPreventedBridge", (message) => { preventedBridgeMessages.push(message); });
    await preventedPage.evaluate(() => {
      globalThis.__jobflowListener = null;
      globalThis.chrome = {
        runtime: {
          lastError: null,
          onMessage: {addListener(listener) { globalThis.__jobflowListener = listener; }},
          sendMessage(message) {
            if (message?.type === "JOBFLOW_MANUAL_NAVIGATION_OBSERVED") globalThis.__recordPreventedBridge(message);
            return Promise.resolve({status: "RECORDED"});
          },
          connect() { throw new Error("NO_FILE_STREAM_EXPECTED"); }
        }
      };
      document.querySelector("#next-1").addEventListener("click", (event) => {
        event.preventDefault();
        document.querySelector("form").insertAdjacentHTML("beforeend", "<div id=spa-changed>changed</div>");
      });
    });
    await preventedPage.addScriptTag({path: companion});
    await preventedPage.evaluate(() => {
      globalThis.__jobflowCall = (message) => new Promise((resolve) => globalThis.__jobflowListener(message, {}, resolve));
    });
    const preventedCollected = await preventedPage.evaluate(() => globalThis.__jobflowCall({type: "JOBFLOW_COLLECT_FORM"}));
    const preventedHash = valueHash(preventedCollected.payload.sanitized_html);
    const preventedSemantics = valueHash(JSON.stringify([
      preventedHash, preventedCollected.payload.client_refs[1], "submit", "Next"
    ]));
    await preventedPage.evaluate(
      ({clientRefs, pageHash, semanticsHash}) => globalThis.__jobflowCall({
        type: "JOBFLOW_APPLY_APPROVED", fields: [], files: [], final_submit_client_refs: [],
        navigation: {
          client_ref: clientRefs[1], mode: "MANUAL_USER_CLICK", control_type: "submit",
          page_content_hash: pageHash, control_semantics_hash: semanticsHash, display_label: "Next"
        }
      }),
      {clientRefs: preventedCollected.payload.client_refs, pageHash: preventedHash, semanticsHash: preventedSemantics}
    );
    const preventedChallenge = {
      challenge_id: `MNC-${"C".repeat(32)}`, nonce: "prevented-one-use-nonce",
      challenge_hash: `sha256:${"d".repeat(64)}`,
      issued_at: new Date(Date.now() - 1000).toISOString(),
      expires_at: new Date(Date.now() + 120000).toISOString(),
      assist_id: "BAS-PREVENTED", application_id: "APP-PREVENTED", tab_id: 43,
      document_instance_id: preventedCollected.payload.document_instance_id,
      stage: "MANUAL_NAVIGATION_REQUIRED", client_ref: preventedCollected.payload.client_refs[1],
      prior_page_content_hash: preventedHash, control_semantics_hash: preventedSemantics
    };
    const preventedArmed = await preventedPage.evaluate(
      (challenge) => globalThis.__jobflowCall({type: "JOBFLOW_ARM_MANUAL_NAVIGATION", challenge}),
      preventedChallenge
    );
    assert.equal(preventedArmed.status, "MANUAL_NAVIGATION_ARMED");
    await preventedPage.locator("#next-1").click();
    await preventedPage.waitForTimeout(100);
    assert.equal(await preventedPage.locator("#spa-changed").count(), 1);
    assert.equal(preventedBridgeMessages.length, 0);

    const spaPage = await browser.newPage();
    const spaFixture = "<!doctype html><html><body><input name=city><button id=next-spa type=submit>Next</button></body></html>";
    await spaPage.route("https://workday.example.test/**", (route) => route.fulfill({
      status: 200, contentType: "text/html; charset=utf-8", body: spaFixture
    }));
    await spaPage.goto("https://workday.example.test/apply", {waitUntil: "domcontentloaded"});
    const spaBridgeMessages = [];
    await spaPage.exposeFunction("__recordSpaBridge", (message) => { spaBridgeMessages.push(message); });
    await spaPage.evaluate(() => {
      globalThis.__jobflowListener = null;
      globalThis.chrome = {
        runtime: {
          lastError: null,
          onMessage: {addListener(listener) { globalThis.__jobflowListener = listener; }},
          sendMessage(message) {
            if (message?.type === "JOBFLOW_MANUAL_NAVIGATION_OBSERVED") globalThis.__recordSpaBridge(message);
            return Promise.resolve({status: "RECORDED"});
          },
          connect() { throw new Error("NO_FILE_STREAM_EXPECTED"); }
        }
      };
      document.querySelector("#next-spa").addEventListener("click", () => {
        history.pushState({}, "", "/advanced");
        document.body.insertAdjacentHTML("beforeend", "<div id=spa-advanced>advanced</div>");
      });
    });
    await spaPage.addScriptTag({path: companion});
    await spaPage.evaluate(() => {
      globalThis.__jobflowCall = (message) => new Promise((resolve) => globalThis.__jobflowListener(message, {}, resolve));
    });
    const spaCollected = await spaPage.evaluate(() => globalThis.__jobflowCall({type: "JOBFLOW_COLLECT_FORM"}));
    const spaHash = valueHash(spaCollected.payload.sanitized_html);
    const spaSemantics = valueHash(JSON.stringify([spaHash, spaCollected.payload.client_refs[1], "submit", "Next"]));
    await spaPage.evaluate(
      ({clientRefs, pageHash, semanticsHash}) => globalThis.__jobflowCall({
        type: "JOBFLOW_APPLY_APPROVED", fields: [], files: [], final_submit_client_refs: [],
        navigation: {
          client_ref: clientRefs[1], mode: "MANUAL_USER_CLICK", control_type: "submit",
          page_content_hash: pageHash, control_semantics_hash: semanticsHash, display_label: "Next"
        }
      }),
      {clientRefs: spaCollected.payload.client_refs, pageHash: spaHash, semanticsHash: spaSemantics}
    );
    const spaChallenge = {
      challenge_id: `MNC-${"E".repeat(32)}`, nonce: "spa-one-use-nonce",
      challenge_hash: `sha256:${"f".repeat(64)}`,
      issued_at: new Date(Date.now() - 1000).toISOString(),
      expires_at: new Date(Date.now() + 120000).toISOString(),
      assist_id: "BAS-SPA", application_id: "APP-SPA", tab_id: 44,
      document_instance_id: spaCollected.payload.document_instance_id,
      stage: "MANUAL_NAVIGATION_REQUIRED", client_ref: spaCollected.payload.client_refs[1],
      prior_page_content_hash: spaHash, control_semantics_hash: spaSemantics
    };
    assert.equal((await spaPage.evaluate(
      (challenge) => globalThis.__jobflowCall({type: "JOBFLOW_ARM_MANUAL_NAVIGATION", challenge}), spaChallenge
    )).status, "MANUAL_NAVIGATION_ARMED");
    await spaPage.locator("#next-spa").click();
    await spaPage.waitForTimeout(100);
    assert.equal(await spaPage.locator("#spa-advanced").count(), 1);
    assert.equal(spaPage.url(), "https://workday.example.test/advanced");
    assert.equal(spaBridgeMessages.length, 1);

    const explicitPage = await browser.newPage();
    const explicitFixture = fs.readFileSync(
      path.join(project, "tests", "fixtures", "synthetic-v2-workday-step-1-explicit-button.html"), "utf8"
    );
    await explicitPage.route("https://workday.example.test/**", (route) => route.fulfill({
      status: 200, contentType: "text/html; charset=utf-8", body: explicitFixture
    }));
    await explicitPage.goto("https://workday.example.test/apply", {waitUntil: "domcontentloaded"});
    await explicitPage.evaluate(() => {
      globalThis.__jobflowListener = null;
      globalThis.__explicitClicks = 0;
      globalThis.chrome = {
        runtime: {
          lastError: null,
          onMessage: {addListener(listener) { globalThis.__jobflowListener = listener; }},
          async sendMessage() { return {status: "RECORDED"}; },
          connect() { throw new Error("NO_FILE_STREAM_EXPECTED"); }
        }
      };
      document.querySelector("#next-1").addEventListener("click", () => { globalThis.__explicitClicks += 1; });
    });
    await explicitPage.addScriptTag({path: companion});
    await explicitPage.evaluate(() => {
      globalThis.__jobflowCall = (message) => new Promise((resolve) => globalThis.__jobflowListener(message, {}, resolve));
    });
    const explicitCollected = await explicitPage.evaluate(() => globalThis.__jobflowCall({type: "JOBFLOW_COLLECT_FORM"}));
    const explicitPageHash = valueHash(explicitCollected.payload.sanitized_html);
    const explicitSemanticsHash = valueHash(JSON.stringify([
      explicitPageHash, explicitCollected.payload.client_refs[1], "button", "Next"
    ]));
    const explicitApplied = await explicitPage.evaluate(
      ({clientRefs, pageHash, semanticsHash, value, hash}) => globalThis.__jobflowCall({
        type: "JOBFLOW_APPLY_APPROVED",
        fields: [{client_ref: clientRefs[0], value, value_sha256: hash}],
        files: [], final_submit_client_refs: [],
        navigation: {
          client_ref: clientRefs[1], mode: "PROGRAMMATIC_EXPLICIT_BUTTON", control_type: "button",
          page_content_hash: pageHash, control_semantics_hash: semanticsHash, display_label: "Next"
        }
      }),
      {
        clientRefs: explicitCollected.payload.client_refs, pageHash: explicitPageHash,
        semanticsHash: explicitSemanticsHash, value: navValue, hash: valueHash(navValue)
      }
    );
    assert.equal(explicitApplied.navigation_ready, true);
    const explicitChecked = await explicitPage.evaluate(
      (clientRef) => globalThis.__jobflowCall({type: "JOBFLOW_CHECK_NAVIGATION", client_ref: clientRef}),
      explicitCollected.payload.client_refs[1]
    );
    assert.equal(explicitChecked.status, "NAVIGATION_VALID", JSON.stringify(explicitChecked));
    const staleAuthorization = await explicitPage.evaluate(
      ({clientRef, semanticsHash}) => globalThis.__jobflowCall({
        type: "JOBFLOW_NAVIGATE_APPROVED", client_ref: clientRef,
        page_content_hash: `sha256:${"0".repeat(64)}`, control_semantics_hash: semanticsHash
      }),
      {clientRef: explicitCollected.payload.client_refs[1], semanticsHash: explicitSemanticsHash}
    );
    assert.equal(staleAuthorization.code, "COMPANION_NAVIGATION_AUTHORIZATION_STALE");
    assert.equal(await explicitPage.evaluate(() => globalThis.__explicitClicks), 0);
    const explicitStarted = await explicitPage.evaluate(
      ({clientRef, pageHash, semanticsHash}) => globalThis.__jobflowCall({
        type: "JOBFLOW_NAVIGATE_APPROVED", client_ref: clientRef,
        page_content_hash: pageHash, control_semantics_hash: semanticsHash
      }),
      {
        clientRef: explicitCollected.payload.client_refs[1], pageHash: explicitChecked.page_content_hash,
        semanticsHash: explicitChecked.control_semantics_hash
      }
    );
    assert.equal(explicitStarted.status, "NAVIGATION_STARTED");
    assert.equal(await explicitPage.evaluate(() => globalThis.__explicitClicks), 1);

    process.stdout.write(JSON.stringify({
      status: "PASS",
      controls: collected.payload.client_refs.length,
      guided_job_capture: true,
      fields: fieldApplied.field_bindings.length,
      files: fileApplied.material_bindings.length,
      programmatic_submit_events_before_user_click: 0,
      trusted_user_submit_observed: true,
      submit_like_next_programmatic_clicks: 0,
      trusted_manual_next_observed_before_unload: true,
      scoped_explicit_button_navigation: true,
      programmatic_final_submit_events: 0
    }));
  } finally {
    await browser.close();
  }
})().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
