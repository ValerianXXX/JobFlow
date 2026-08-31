"use strict";

const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const {chromium} = require("playwright");

const project = path.resolve(__dirname, "..");
const fixture = fs.readFileSync(path.join(project, "tests", "fixtures", "synthetic-material-form.html"), "utf8");
const greenhouseContinueFixture = fs.readFileSync(
  path.join(project, "tests", "fixtures", "synthetic-greenhouse-continue-form.html"), "utf8"
);
const greenhouseFixture = fs.readFileSync(
  path.join(project, "tests", "fixtures", "synthetic-greenhouse-form.html"), "utf8"
);
const leverFixture = fs.readFileSync(
  path.join(project, "tests", "fixtures", "synthetic-lever-form.html"), "utf8"
);
const leverContinueFixture = fs.readFileSync(
  path.join(project, "tests", "fixtures", "synthetic-lever-continue-form.html"), "utf8"
);
const workdayReviewFixture = fs.readFileSync(
  path.join(project, "tests", "fixtures", "synthetic-workday-safe-form.html"), "utf8"
);
const ashbyFixture = fs.readFileSync(
  path.join(project, "tests", "fixtures", "synthetic-ashby-form.html"), "utf8"
);
const ashbyModernFixture = fs.readFileSync(
  path.join(project, "tests", "fixtures", "synthetic-ashby-modern-form.html"), "utf8"
);
const smartRecruitersFixture = fs.readFileSync(
  path.join(project, "tests", "fixtures", "synthetic-smartrecruiters-form.html"), "utf8"
);
const tekFixture = fs.readFileSync(path.join(project, "tests", "fixtures", "synthetic-teksystems-lwc-form.html"), "utf8");
const companion = path.join(project, "browser-companion", "dom.js");
const browserPath = process.env.JOBFLOW_TEST_BROWSER || "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const fileBytes = Buffer.from("synthetic approved resume\n", "utf8");
const fileHash = `sha256:${crypto.createHash("sha256").update(fileBytes).digest("hex")}`;

function valueHash(value) {
  return `sha256:${crypto.createHash("sha256").update(value, "utf8").digest("hex")}`;
}

async function verifyProviderApplicationRuntime(browser, {
  url, fixtureHtml, fieldValues, fieldSelector, fileIndex, fileSelector,
  navigationIndex, navigationSelector, navigationLabel, finalIndex, finalSelector
}) {
  const providerPage = await browser.newPage();
  await providerPage.route(url, (route) => route.fulfill({
    status: 200, contentType: "text/html; charset=utf-8", body: fixtureHtml
  }));
  await providerPage.goto(url, {waitUntil: "domcontentloaded"});
  await providerPage.evaluate(({encodedFile, navigationSelector}) => {
    globalThis.__jobflowMessages = [];
    globalThis.__jobflowListener = null;
    globalThis.__providerNavigationClicks = 0;
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
    document.querySelector("form").addEventListener("submit", (event) => event.preventDefault());
    document.querySelector(navigationSelector).addEventListener("click", () => {
      globalThis.__providerNavigationClicks += 1;
    });
  }, {encodedFile: fileBytes.toString("base64"), navigationSelector});
  await providerPage.addScriptTag({path: companion});
  await providerPage.evaluate(() => {
    globalThis.__jobflowCall = (message) => new Promise((resolve) => {
      globalThis.__jobflowListener(message, {}, resolve);
    });
  });
  const collected = await providerPage.evaluate(() => globalThis.__jobflowCall({type: "JOBFLOW_COLLECT_FORM"}));
  assert.equal(collected.status, "COLLECTED");
  assert.equal(collected.payload.client_refs.length, finalIndex + 1);
  const pageHash = valueHash(collected.payload.sanitized_html);
  const semanticsHash = valueHash(JSON.stringify([
    pageHash, collected.payload.client_refs[navigationIndex], "button", navigationLabel
  ]));
  const fields = fieldValues.map((value, index) => ({
    client_ref: collected.payload.client_refs[index], value, value_sha256: valueHash(value)
  }));
  const applied = await providerPage.evaluate(
    ({refs, fields, fileIndex, finalIndex, navigationIndex, pageHash, semanticsHash, navigationLabel, fileHash, controlSemantics}) =>
      globalThis.__jobflowCall({
        type: "JOBFLOW_APPLY_APPROVED",
        control_semantics_sha256: controlSemantics,
        fields,
        files: [{
          client_ref: refs[fileIndex], purpose: "resume", filename: "resume.pdf",
          sha256: fileHash, download_url: "http://127.0.0.1/assist/synthetic/file/provider-resume"
        }],
        navigation: {
          client_ref: refs[navigationIndex], mode: "PROGRAMMATIC_EXPLICIT_BUTTON", control_type: "button",
          page_content_hash: pageHash, control_semantics_hash: semanticsHash, display_label: navigationLabel
        },
        final_submit_client_refs: [refs[finalIndex]]
      }),
    {
      refs: collected.payload.client_refs, fields, fileIndex, finalIndex, navigationIndex,
      pageHash, semanticsHash, navigationLabel, fileHash,
      controlSemantics: collected.payload.control_semantics_sha256
    }
  );
  assert.equal(applied.status, "APPLIED", JSON.stringify(applied));
  assert.equal(applied.field_bindings.length, fieldValues.length);
  assert.equal(applied.material_bindings.length, 1);
  assert.equal(applied.navigation_ready, true);
  assert.equal(applied.final_submit_armed, true);
  assert.deepEqual(await providerPage.locator(fieldSelector).evaluateAll(
    (controls) => controls.map((control) => control.value)
  ), fieldValues);
  assert.equal(await providerPage.locator(fileSelector).evaluate((control) => control.files[0]?.name), "resume.pdf");
  assert.equal((await providerPage.evaluate(() => globalThis.__jobflowMessages)).filter(
    (item) => item.type === "JOBFLOW_USER_SUBMIT_OBSERVED"
  ).length, 0);
  const checked = await providerPage.evaluate(
    (clientRef) => globalThis.__jobflowCall({type: "JOBFLOW_CHECK_NAVIGATION", client_ref: clientRef}),
    collected.payload.client_refs[navigationIndex]
  );
  assert.equal(checked.status, "NAVIGATION_VALID", JSON.stringify(checked));
  const started = await providerPage.evaluate(
    ({clientRef, pageHash, semanticsHash}) => globalThis.__jobflowCall({
      type: "JOBFLOW_NAVIGATE_APPROVED", client_ref: clientRef,
      page_content_hash: pageHash, control_semantics_hash: semanticsHash
    }),
    {
      clientRef: collected.payload.client_refs[navigationIndex],
      pageHash: checked.page_content_hash,
      semanticsHash: checked.control_semantics_hash
    }
  );
  assert.equal(started.status, "NAVIGATION_STARTED", JSON.stringify(started));
  assert.equal(await providerPage.evaluate(() => globalThis.__providerNavigationClicks), 1);
  await providerPage.locator(finalSelector).click();
  await providerPage.waitForFunction(() => globalThis.__jobflowMessages.some(
    (item) => item.type === "JOBFLOW_USER_SUBMIT_OBSERVED"
  ));
  const submitSignals = (await providerPage.evaluate(() => globalThis.__jobflowMessages)).filter(
    (item) => item.type === "JOBFLOW_USER_SUBMIT_OBSERVED"
  );
  assert.equal(submitSignals.length, 1);
  assert.equal(submitSignals[0].payload.trusted_user_event, true);
  await providerPage.close();
  return {fields: applied.field_bindings.length, files: applied.material_bindings.length};
}

async function verifyProviderRebindingRuntime(browser, {
  url, fixtureHtml, targetSelector, targetIndex, value, finalIndex, finalSelector
}) {
  const providerPage = await browser.newPage();
  await providerPage.route(url, (route) => route.fulfill({
    status: 200, contentType: "text/html; charset=utf-8", body: fixtureHtml
  }));
  await providerPage.goto(url, {waitUntil: "domcontentloaded"});
  await providerPage.evaluate(() => {
    globalThis.__jobflowMessages = [];
    globalThis.__jobflowListener = null;
    globalThis.chrome = {
      runtime: {
        lastError: null,
        onMessage: {addListener(listener) { globalThis.__jobflowListener = listener; }},
        async sendMessage(message) { globalThis.__jobflowMessages.push(message); return {status: "RECORDED"}; },
        connect() { throw new Error("NO_FILE_STREAM_EXPECTED"); }
      }
    };
    document.querySelector("form").addEventListener("submit", (event) => event.preventDefault());
  });
  await providerPage.addScriptTag({path: companion});
  await providerPage.evaluate(() => {
    globalThis.__jobflowCall = (message) => new Promise((resolve) => {
      globalThis.__jobflowListener(message, {}, resolve);
    });
  });
  const collected = await providerPage.evaluate(() => globalThis.__jobflowCall({type: "JOBFLOW_COLLECT_FORM"}));
  assert.equal(collected.status, "COLLECTED");
  assert.ok(collected.payload.client_refs[targetIndex]);
  assert.ok(collected.payload.client_refs[finalIndex]);
  await providerPage.evaluate((selector) => {
    const original = document.querySelector(selector);
    const replacement = original.cloneNode(true);
    replacement.value = "";
    replacement.dataset.frameworkRender = "replacement";
    original.replaceWith(replacement);
  }, targetSelector);
  const applied = await providerPage.evaluate(
    ({clientRefs, targetIndex, finalIndex, value, hash, controlSemantics}) => globalThis.__jobflowCall({
      type: "JOBFLOW_APPLY_APPROVED",
      control_semantics_sha256: controlSemantics,
      fields: [{client_ref: clientRefs[targetIndex], value, value_sha256: hash}],
      files: [], navigation: null, final_submit_client_refs: [clientRefs[finalIndex]]
    }),
    {
      clientRefs: collected.payload.client_refs, targetIndex, finalIndex,
      value, hash: valueHash(value), controlSemantics: collected.payload.control_semantics_sha256
    }
  );
  assert.equal(applied.status, "APPLIED", JSON.stringify(applied));
  assert.equal(applied.field_bindings.length, 1);
  assert.equal(applied.final_submit_armed, true);
  assert.equal(await providerPage.locator(targetSelector).getAttribute("data-framework-render"), "replacement");
  assert.equal(await providerPage.locator(targetSelector).inputValue(), value);
  assert.equal((await providerPage.evaluate(() => globalThis.__jobflowMessages)).filter(
    (item) => item.type === "JOBFLOW_USER_SUBMIT_OBSERVED"
  ).length, 0);
  await providerPage.locator(finalSelector).click();
  await providerPage.waitForFunction(() => globalThis.__jobflowMessages.some(
    (item) => item.type === "JOBFLOW_USER_SUBMIT_OBSERVED"
  ));
  const submitSignals = (await providerPage.evaluate(() => globalThis.__jobflowMessages)).filter(
    (item) => item.type === "JOBFLOW_USER_SUBMIT_OBSERVED"
  );
  assert.equal(submitSignals.length, 1);
  assert.equal(submitSignals[0].payload.trusted_user_event, true);
  await providerPage.close();
  return true;
}

async function verifyDynamicMaxLengthFailClosed(browser) {
  const fixtureHtml = `<!doctype html><html><body><form>
    <label for="name">Name</label><input id="name" name="name">
    <label for="cover">Cover Letter</label><textarea id="cover" name="cover_letter" maxlength="1200"></textarea>
    <button id="submit" type="submit">Submit application</button>
  </form></body></html>`;
  const createPage = async (suffix) => {
    const candidatePage = await browser.newPage();
    const url = `https://boards.greenhouse.io/example/jobs/maxlength-${suffix}`;
    await candidatePage.route(url, (route) => route.fulfill({
      status: 200, contentType: "text/html; charset=utf-8", body: fixtureHtml
    }));
    await candidatePage.goto(url, {waitUntil: "domcontentloaded"});
    await candidatePage.evaluate(() => {
      globalThis.__jobflowListener = null;
      globalThis.chrome = {
        runtime: {
          lastError: null,
          onMessage: {addListener(listener) { globalThis.__jobflowListener = listener; }},
          async sendMessage() { return {status: "RECORDED"}; },
          connect() { throw new Error("NO_FILE_STREAM_EXPECTED"); }
        }
      };
      document.querySelector("form").addEventListener("submit", (event) => event.preventDefault());
    });
    await candidatePage.addScriptTag({path: companion});
    await candidatePage.evaluate(() => {
      globalThis.__jobflowCall = (message) => new Promise((resolve) => {
        globalThis.__jobflowListener(message, {}, resolve);
      });
    });
    const collected = await candidatePage.evaluate(() => globalThis.__jobflowCall({type: "JOBFLOW_COLLECT_FORM"}));
    assert.equal(collected.status, "COLLECTED");
    assert.equal(collected.payload.client_refs.length, 3);
    return {
      candidatePage, refs: collected.payload.client_refs,
      controlSemantics: collected.payload.control_semantics_sha256
    };
  };
  const fieldsFor = (refs) => {
    const narrative = "N".repeat(600);
    return [
      {
        client_ref: refs[0], value: "Synthetic Applicant", value_sha256: valueHash("Synthetic Applicant"),
        max_length: null, max_length_status: "ABSENT"
      },
      {
        client_ref: refs[1], value: narrative, value_sha256: valueHash(narrative),
        max_length: 1200, max_length_status: "VALID"
      }
    ];
  };

  const direct = await createPage("direct");
  await direct.candidatePage.locator("#cover").evaluate((element) => { element.maxLength = 500; });
  const directResult = await direct.candidatePage.evaluate(
    ({fields, finalRef, controlSemantics}) => globalThis.__jobflowCall({
      type: "JOBFLOW_APPLY_APPROVED", fields, files: [], navigation: null,
      control_semantics_sha256: controlSemantics,
      final_submit_client_refs: [finalRef]
    }),
    {fields: fieldsFor(direct.refs), finalRef: direct.refs[2], controlSemantics: direct.controlSemantics}
  );
  assert.equal(directResult.status, "BLOCKED", JSON.stringify(directResult));
  assert.equal(directResult.code, "COMPANION_CONTROL_REBIND_FAILED");
  assert.equal(directResult.partial_effects, false);
  assert.equal(directResult.attempted_field_bindings.length, 0);
  assert.deepEqual(await direct.candidatePage.locator("#name,#cover").evaluateAll(
    (controls) => controls.map((control) => control.value)
  ), ["", ""]);
  await direct.candidatePage.close();

  const rebound = await createPage("rebind");
  await rebound.candidatePage.locator("#cover").evaluate((original) => {
    const replacement = original.cloneNode(true);
    replacement.maxLength = 500;
    original.replaceWith(replacement);
  });
  const reboundResult = await rebound.candidatePage.evaluate(
    ({fields, finalRef, controlSemantics}) => globalThis.__jobflowCall({
      type: "JOBFLOW_APPLY_APPROVED", fields, files: [], navigation: null,
      control_semantics_sha256: controlSemantics,
      final_submit_client_refs: [finalRef]
    }),
    {fields: fieldsFor(rebound.refs), finalRef: rebound.refs[2], controlSemantics: rebound.controlSemantics}
  );
  assert.equal(reboundResult.status, "BLOCKED", JSON.stringify(reboundResult));
  assert.equal(reboundResult.code, "COMPANION_CONTROL_REBIND_FAILED");
  assert.equal(reboundResult.partial_effects, false);
  assert.equal(reboundResult.attempted_field_bindings.length, 0);
  assert.deepEqual(await rebound.candidatePage.locator("#name,#cover").evaluateAll(
    (controls) => controls.map((control) => control.value)
  ), ["", ""]);
  await rebound.candidatePage.close();
  return true;
}

async function verifyApprovalSemanticsAndAtomicPreflight(browser) {
  const fixtureHtml = `<!doctype html><html><body><form>
    <label for="name">Name</label><input id="name" name="name">
    <label id="cover-label" for="cover">Cover Letter</label>
    <textarea id="cover" name="cover_letter" maxlength="1200"></textarea>
    <label for="country">Country</label><select id="country" name="country">
      <option value="">Choose</option><option value="US">United States</option><option value="CA">Canada</option>
    </select>
    <label for="inert-resume">Resume</label><input id="inert-resume" name="resume" type="file" inert>
    <button id="submit" type="submit">Submit application</button>
  </form></body></html>`;
  const createPage = async (suffix) => {
    const candidatePage = await browser.newPage();
    const url = `https://boards.greenhouse.io/example/jobs/atomic-${suffix}`;
    await candidatePage.route(url, (route) => route.fulfill({
      status: 200, contentType: "text/html; charset=utf-8", body: fixtureHtml
    }));
    await candidatePage.goto(url, {waitUntil: "domcontentloaded"});
    await candidatePage.evaluate(() => {
      globalThis.__jobflowListener = null;
      globalThis.chrome = {
        runtime: {
          lastError: null,
          onMessage: {addListener(listener) { globalThis.__jobflowListener = listener; }},
          async sendMessage() { return {status: "RECORDED"}; },
          connect() { throw new Error("NO_FILE_STREAM_EXPECTED"); }
        }
      };
      document.querySelector("form").addEventListener("submit", (event) => event.preventDefault());
    });
    await candidatePage.addScriptTag({path: companion});
    await candidatePage.evaluate(() => {
      globalThis.__jobflowCall = (message) => new Promise((resolve) => {
        globalThis.__jobflowListener(message, {}, resolve);
      });
    });
    const collected = await candidatePage.evaluate(() => globalThis.__jobflowCall({type: "JOBFLOW_COLLECT_FORM"}));
    assert.equal(collected.status, "COLLECTED", JSON.stringify(collected));
    assert.equal(collected.payload.client_refs.length, 4, collected.payload.sanitized_html);
    assert.ok(!collected.payload.sanitized_html.includes("inert-resume"));
    return {candidatePage, collected};
  };
  const approvedField = (clientRef, value, valueSha256 = valueHash(value)) => ({
    client_ref: clientRef, value, value_sha256: valueSha256
  });

  const labelDrift = await createPage("label-drift");
  await labelDrift.candidatePage.locator("#cover-label").evaluate((label) => { label.textContent = "Legal Consent"; });
  const labelDriftResult = await labelDrift.candidatePage.evaluate(
    ({refs, semantics, fields}) => globalThis.__jobflowCall({
      type: "JOBFLOW_APPLY_APPROVED", control_semantics_sha256: semantics,
      fields, files: [], navigation: null, final_submit_client_refs: [refs[3]]
    }),
    {
      refs: labelDrift.collected.payload.client_refs,
      semantics: labelDrift.collected.payload.control_semantics_sha256,
      fields: [
        approvedField(labelDrift.collected.payload.client_refs[0], "Synthetic Applicant"),
        approvedField(labelDrift.collected.payload.client_refs[1], "Approved cover letter")
      ]
    }
  );
  assert.equal(labelDriftResult.status, "BLOCKED", JSON.stringify(labelDriftResult));
  assert.equal(labelDriftResult.code, "COMPANION_CONTROL_REBIND_FAILED");
  assert.equal(labelDriftResult.partial_effects, false);
  assert.deepEqual(await labelDrift.candidatePage.locator("#name,#cover").evaluateAll(
    (controls) => controls.map((control) => control.value)
  ), ["", ""]);
  await labelDrift.candidatePage.close();

  const optionMismatch = await createPage("option-mismatch");
  const optionMismatchResult = await optionMismatch.candidatePage.evaluate(
    ({refs, semantics, fields}) => globalThis.__jobflowCall({
      type: "JOBFLOW_APPLY_APPROVED", control_semantics_sha256: semantics,
      fields, files: [], navigation: null, final_submit_client_refs: [refs[3]]
    }),
    {
      refs: optionMismatch.collected.payload.client_refs,
      semantics: optionMismatch.collected.payload.control_semantics_sha256,
      fields: [
        approvedField(optionMismatch.collected.payload.client_refs[0], "Synthetic Applicant"),
        approvedField(optionMismatch.collected.payload.client_refs[2], "Mexico")
      ]
    }
  );
  assert.equal(optionMismatchResult.status, "BLOCKED", JSON.stringify(optionMismatchResult));
  assert.equal(optionMismatchResult.code, "COMPANION_SELECT_OPTION_NOT_FOUND");
  assert.equal(optionMismatchResult.partial_effects, false);
  assert.equal(await optionMismatch.candidatePage.locator("#name").inputValue(), "");
  await optionMismatch.candidatePage.close();

  const valueMismatch = await createPage("value-mismatch");
  const valueMismatchResult = await valueMismatch.candidatePage.evaluate(
    ({refs, semantics, fields}) => globalThis.__jobflowCall({
      type: "JOBFLOW_APPLY_APPROVED", control_semantics_sha256: semantics,
      fields, files: [], navigation: null, final_submit_client_refs: [refs[3]]
    }),
    {
      refs: valueMismatch.collected.payload.client_refs,
      semantics: valueMismatch.collected.payload.control_semantics_sha256,
      fields: [
        approvedField(valueMismatch.collected.payload.client_refs[0], "Synthetic Applicant"),
        approvedField(valueMismatch.collected.payload.client_refs[1], "Approved cover letter", valueHash("different"))
      ]
    }
  );
  assert.equal(valueMismatchResult.status, "BLOCKED", JSON.stringify(valueMismatchResult));
  assert.equal(valueMismatchResult.code, "COMPANION_VALUE_HASH_MISMATCH");
  assert.equal(valueMismatchResult.partial_effects, false);
  assert.equal(await valueMismatch.candidatePage.locator("#name").inputValue(), "");
  await valueMismatch.candidatePage.close();

  const finalDrift = await createPage("final-drift");
  await finalDrift.candidatePage.locator("#submit").evaluate((button) => { button.textContent = "I agree"; });
  const finalDriftResult = await finalDrift.candidatePage.evaluate(
    ({refs, semantics, field}) => globalThis.__jobflowCall({
      type: "JOBFLOW_APPLY_APPROVED", control_semantics_sha256: semantics,
      fields: [field], files: [], navigation: null, final_submit_client_refs: [refs[3]]
    }),
    {
      refs: finalDrift.collected.payload.client_refs,
      semantics: finalDrift.collected.payload.control_semantics_sha256,
      field: approvedField(finalDrift.collected.payload.client_refs[0], "Synthetic Applicant")
    }
  );
  assert.equal(finalDriftResult.status, "BLOCKED", JSON.stringify(finalDriftResult));
  assert.equal(finalDriftResult.code, "COMPANION_CONTROL_REBIND_FAILED");
  assert.equal(finalDriftResult.partial_effects, false);
  assert.equal(await finalDrift.candidatePage.locator("#name").inputValue(), "");
  await finalDrift.candidatePage.close();

  const duplicate = await createPage("duplicate-ref");
  const duplicateField = approvedField(duplicate.collected.payload.client_refs[0], "Synthetic Applicant");
  const duplicateResult = await duplicate.candidatePage.evaluate(
    ({semantics, field}) => globalThis.__jobflowCall({
      type: "JOBFLOW_APPLY_APPROVED", control_semantics_sha256: semantics,
      fields: [field, field], files: [], navigation: null, final_submit_client_refs: []
    }),
    {semantics: duplicate.collected.payload.control_semantics_sha256, field: duplicateField}
  );
  assert.equal(duplicateResult.status, "BLOCKED", JSON.stringify(duplicateResult));
  assert.equal(duplicateResult.code, "COMPANION_DUPLICATE_CONTROL_BINDING");
  assert.equal(duplicateResult.partial_effects, false);
  assert.equal(await duplicate.candidatePage.locator("#name").inputValue(), "");
  await duplicate.candidatePage.close();
  return true;
}

async function verifyProtectedAccountAndCredentialGates(browser) {
  const createPage = async (suffix, fixtureHtml) => {
    const candidatePage = await browser.newPage();
    const url = `https://tenant.wd1.myworkdayjobs.com/en-US/Careers/account-gate-${suffix}`;
    await candidatePage.route(url, (route) => route.fulfill({
      status: 200, contentType: "text/html; charset=utf-8", body: fixtureHtml
    }));
    await candidatePage.goto(url, {waitUntil: "domcontentloaded"});
    await candidatePage.evaluate(() => {
      globalThis.__jobflowListener = null;
      globalThis.chrome = {
        runtime: {
          lastError: null,
          onMessage: {addListener(listener) { globalThis.__jobflowListener = listener; }},
          async sendMessage() { return {status: "RECORDED"}; },
          connect() { throw new Error("NO_FILE_STREAM_EXPECTED"); }
        }
      };
      document.querySelector("form")?.addEventListener("submit", (event) => event.preventDefault());
    });
    await candidatePage.addScriptTag({path: companion});
    await candidatePage.evaluate(() => {
      globalThis.__jobflowCall = (message) => new Promise((resolve) => {
        globalThis.__jobflowListener(message, {}, resolve);
      });
    });
    return candidatePage;
  };

  const guestPage = await createPage("guest", `<!doctype html><html><body><main>
    <h1>Apply for this role</h1><form>
      <label for="name">Full name</label><input id="name" name="name" required>
      <label for="email">Email</label><input id="email" name="email" type="email" required>
      <label for="resume">Resume</label><input id="resume" name="resume" type="file">
      <button id="submit" type="submit">Submit application</button>
    </form><a href="/create-account">Create account</a>
  </main></body></html>`);
  const guestCollected = await guestPage.evaluate(() => globalThis.__jobflowCall({type: "JOBFLOW_COLLECT_FORM"}));
  assert.equal(guestCollected.status, "COLLECTED", JSON.stringify(guestCollected));
  assert.ok(!guestCollected.payload.blocker_signals.includes("ACCOUNT_CREATION"));
  assert.ok(!guestCollected.payload.blocker_signals.includes("LOGIN"));
  await guestPage.close();

  const accountPage = await createPage("actual", `<!doctype html><html><body><main>
    <h1>Create Account</h1><form>
      <label for="account-email">Email</label><input id="account-email" name="email" type="email">
      <label for="password">Password</label><input id="password" name="password" type="password" autocomplete="new-password">
      <label for="verify">Verify New Password</label><input id="verify" name="verify_password" type="password" autocomplete="new-password">
      <button type="submit">Create Account</button>
    </form><a href="/sign-in">Sign In</a>
  </main></body></html>`);
  const accountCollected = await accountPage.evaluate(() => globalThis.__jobflowCall({type: "JOBFLOW_COLLECT_FORM"}));
  assert.equal(accountCollected.status, "COLLECTED", JSON.stringify(accountCollected));
  assert.ok(accountCollected.payload.blocker_signals.includes("ACCOUNT_CREATION"));
  assert.ok(accountCollected.payload.blocker_signals.includes("LOGIN"));
  const accountApply = await accountPage.evaluate(
    ({clientRef, semantics, value, hash}) => globalThis.__jobflowCall({
      type: "JOBFLOW_APPLY_APPROVED", control_semantics_sha256: semantics,
      fields: [{client_ref: clientRef, value, value_sha256: hash}],
      files: [], navigation: null, final_submit_client_refs: []
    }),
    {
      clientRef: accountCollected.payload.client_refs[1],
      semantics: accountCollected.payload.control_semantics_sha256,
      value: "never-write-this-password", hash: valueHash("never-write-this-password")
    }
  );
  assert.equal(accountApply.status, "BLOCKED", JSON.stringify(accountApply));
  assert.equal(accountApply.code, "COMPANION_LOGIN_STOP");
  assert.equal(accountApply.partial_effects, false);
  assert.equal(await accountPage.locator("#password").inputValue(), "");
  await accountPage.close();

  const credentialPage = await createPage("credential-control", `<!doctype html><html><body><main>
    <h1>Candidate details</h1><form>
      <label for="name">Full name</label><input id="name" name="name">
      <label for="secret">Access credential</label><input id="secret" name="access_credential" type="password" autocomplete="new-password">
      <button type="submit">Continue</button>
    </form>
  </main></body></html>`);
  const credentialCollected = await credentialPage.evaluate(() => globalThis.__jobflowCall({type: "JOBFLOW_COLLECT_FORM"}));
  assert.equal(credentialCollected.status, "COLLECTED", JSON.stringify(credentialCollected));
  assert.deepEqual(credentialCollected.payload.blocker_signals, []);
  const credentialApply = await credentialPage.evaluate(
    ({clientRef, semantics, value, hash}) => globalThis.__jobflowCall({
      type: "JOBFLOW_APPLY_APPROVED", control_semantics_sha256: semantics,
      fields: [{client_ref: clientRef, value, value_sha256: hash}],
      files: [], navigation: null, final_submit_client_refs: []
    }),
    {
      clientRef: credentialCollected.payload.client_refs[1],
      semantics: credentialCollected.payload.control_semantics_sha256,
      value: "never-write-this-password", hash: valueHash("never-write-this-password")
    }
  );
  assert.equal(credentialApply.status, "BLOCKED", JSON.stringify(credentialApply));
  assert.equal(credentialApply.code, "COMPANION_PROTECTED_CREDENTIAL_CONTROL");
  assert.equal(credentialApply.partial_effects, false);
  assert.equal(await credentialPage.locator("#secret").inputValue(), "");

  await credentialPage.locator("#secret").evaluate((control) => {
    control.type = "text";
    control.name = "candidate_code";
    control.autocomplete = "one-time-code";
    document.querySelector('label[for="secret"]').textContent = "Candidate code";
  });
  const oneTimeCodeCollected = await credentialPage.evaluate(
    () => globalThis.__jobflowCall({type: "JOBFLOW_COLLECT_FORM"})
  );
  assert.equal(oneTimeCodeCollected.status, "COLLECTED", JSON.stringify(oneTimeCodeCollected));
  assert.deepEqual(oneTimeCodeCollected.payload.blocker_signals, []);
  const oneTimeCodeApply = await credentialPage.evaluate(
    ({clientRef, semantics, value, hash}) => globalThis.__jobflowCall({
      type: "JOBFLOW_APPLY_APPROVED", control_semantics_sha256: semantics,
      fields: [{client_ref: clientRef, value, value_sha256: hash}],
      files: [], navigation: null, final_submit_client_refs: []
    }),
    {
      clientRef: oneTimeCodeCollected.payload.client_refs[1],
      semantics: oneTimeCodeCollected.payload.control_semantics_sha256,
      value: "123456", hash: valueHash("123456")
    }
  );
  assert.equal(oneTimeCodeApply.status, "BLOCKED", JSON.stringify(oneTimeCodeApply));
  assert.equal(oneTimeCodeApply.code, "COMPANION_PROTECTED_CREDENTIAL_CONTROL");
  assert.equal(oneTimeCodeApply.partial_effects, false);
  assert.equal(await credentialPage.locator("#secret").inputValue(), "");
  await credentialPage.close();
  return true;
}

(async () => {
  assert.ok(fs.existsSync(browserPath), `Browser executable is missing: ${browserPath}`);
  const browser = await chromium.launch({headless: true, executablePath: browserPath});
  try {
    const page = await browser.newPage();
    await page.route("https://boards.greenhouse.io/**", (route) => route.fulfill({
      status: 200, contentType: "text/html; charset=utf-8", body: fixture
    }));
    await page.goto("https://boards.greenhouse.io/example/jobs/987654", {waitUntil: "domcontentloaded"});
    await page.evaluate(({encodedFile}) => {
      globalThis.__jobflowMessages = [];
      globalThis.__jobflowListener = null;
      const decode = () => Uint8Array.from(atob(encodedFile), (character) => character.charCodeAt(0));
      globalThis.chrome = {
        dom: {openOrClosedShadowRoot(element) {
          return globalThis.__jobflowClosedRoots?.get(element) || element.shadowRoot || null;
        }},
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
      // Real component-driven career sites may expose an empty first <main>
      // landmark while the populated application lives in another subtree.
      // The job reader must choose actual readable content, not the first shell.
      const emptyShell = document.createElement("main");
      emptyShell.id = "empty-component-shell";
      document.body.prepend(emptyShell);
      document.querySelector("form").addEventListener("submit", (event) => event.preventDefault());
      globalThis.__jobflowCall = (message) => new Promise((resolve) => {
        globalThis.__jobflowListener(message, {}, resolve);
      });
    });

    const jobCollected = await page.evaluate(() => globalThis.__jobflowCall({type: "JOBFLOW_COLLECT_JOB_PAGE"}));
    assert.equal(jobCollected.status, "COLLECTED");
    assert.ok(jobCollected.payload.job_title.length > 0);
    assert.ok(jobCollected.payload.visible_text.length > 0);
    assert.equal(typeof jobCollected.payload.availability.closed_signal, "boolean");
    assert.equal(typeof jobCollected.payload.availability.valid_through, "string");
    assert.ok(!jobCollected.payload.visible_text.includes("synthetic approved resume"));

    const collected = await page.evaluate(() => globalThis.__jobflowCall({type: "JOBFLOW_COLLECT_FORM"}));
    assert.equal(collected.status, "COLLECTED");
    assert.equal(collected.payload.client_refs.length, 7);
    assert.deepEqual(collected.payload.blocker_signals, []);
    assert.equal(new URL(page.url()).hostname, "boards.greenhouse.io");
    assert.equal(await page.locator("main[data-provider]").getAttribute("data-provider"), "greenhouse");
    assert.ok(!collected.payload.sanitized_html.includes("synthetic approved resume"));

    await page.evaluate(() => {
      const tracker = document.createElement("iframe");
      tracker.id = "hidden-analytics-frame";
      tracker.src = "https://tracker.example.invalid/pixel";
      tracker.style.display = "none";
      document.body.append(tracker);
    });
    const hiddenTracker = await page.evaluate(() => globalThis.__jobflowCall({type: "JOBFLOW_COLLECT_FORM"}));
    assert.deepEqual(hiddenTracker.payload.blocker_signals, []);
    assert.ok(!hiddenTracker.payload.sanitized_html.includes("tracker.example.invalid"));
    await page.evaluate(() => document.querySelector("#hidden-analytics-frame").remove());

    await page.evaluate(() => {
      const externalForm = document.createElement("iframe");
      externalForm.id = "visible-external-form";
      externalForm.src = "https://forms.example.invalid/apply";
      externalForm.style.width = "500px";
      externalForm.style.height = "300px";
      document.body.append(externalForm);
    });
    const visibleExternal = await page.evaluate(() => globalThis.__jobflowCall({type: "JOBFLOW_COLLECT_FORM"}));
    assert.ok(visibleExternal.payload.blocker_signals.includes("CROSS_ORIGIN_IFRAME"));
    await page.evaluate(() => document.querySelector("#visible-external-form").remove());

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
      ({fields, finalRef, controlSemantics}) => globalThis.__jobflowCall({
        type: "JOBFLOW_APPLY_APPROVED", fields, files: [], navigation: null,
        control_semantics_sha256: controlSemantics,
        final_submit_client_refs: [finalRef]
      }),
      {fields, finalRef: collected.payload.client_refs[6], controlSemantics: collected.payload.control_semantics_sha256}
    );
    assert.equal(fieldApplied.status, "APPLIED", JSON.stringify(fieldApplied));
    const fileApplied = await page.evaluate(
      ({files, finalRef, controlSemantics}) => globalThis.__jobflowCall({
        type: "JOBFLOW_APPLY_APPROVED", fields: [], files, navigation: null,
        control_semantics_sha256: controlSemantics,
        final_submit_client_refs: [finalRef]
      }),
      {files, finalRef: collected.payload.client_refs[6], controlSemantics: collected.payload.control_semantics_sha256}
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

    const leverPage = await browser.newPage();
    await leverPage.route("https://jobs.lever.co/**", (route) => route.fulfill({
      status: 200, contentType: "text/html; charset=utf-8", body: leverFixture
    }));
    await leverPage.goto("https://jobs.lever.co/example/abc-123/apply", {waitUntil: "domcontentloaded"});
    await leverPage.evaluate(({encodedFile}) => {
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
      document.querySelector("form").addEventListener("submit", (event) => event.preventDefault());
    }, {encodedFile: fileBytes.toString("base64")});
    await leverPage.addScriptTag({path: companion});
    await leverPage.evaluate(() => {
      globalThis.__jobflowCall = (message) => new Promise((resolve) => {
        globalThis.__jobflowListener(message, {}, resolve);
      });
    });
    const leverCollected = await leverPage.evaluate(() => globalThis.__jobflowCall({type: "JOBFLOW_COLLECT_FORM"}));
    assert.equal(leverCollected.status, "COLLECTED");
    assert.equal(new URL(leverPage.url()).hostname, "jobs.lever.co");
    assert.equal(leverCollected.payload.client_refs.length, 5);
    assert.deepEqual(leverCollected.payload.blocker_signals, []);
    const leverValues = ["Synthetic Applicant", "https://example.com/portfolio", "https://linkedin.com/in/example"];
    const leverFields = leverValues.map((value, index) => ({
      client_ref: leverCollected.payload.client_refs[index], value, value_sha256: valueHash(value)
    }));
    const leverApplied = await leverPage.evaluate(
      ({refs, fields, fileHash, controlSemantics}) => globalThis.__jobflowCall({
        type: "JOBFLOW_APPLY_APPROVED",
        control_semantics_sha256: controlSemantics,
        fields,
        files: [{
          client_ref: refs[3], purpose: "resume", filename: "resume.pdf",
          sha256: fileHash, download_url: "http://127.0.0.1/assist/synthetic/file/lever-resume"
        }],
        navigation: null,
        final_submit_client_refs: [refs[4]]
      }),
      {
        refs: leverCollected.payload.client_refs, fields: leverFields, fileHash,
        controlSemantics: leverCollected.payload.control_semantics_sha256
      }
    );
    assert.equal(leverApplied.status, "APPLIED", JSON.stringify(leverApplied));
    assert.equal(leverApplied.field_bindings.length, 3);
    assert.equal(leverApplied.material_bindings.length, 1);
    assert.equal(leverApplied.final_submit_armed, true);
    assert.deepEqual(await leverPage.locator("#name, #portfolio, #linkedin").evaluateAll(
      (controls) => controls.map((control) => control.value)
    ), leverValues);
    assert.equal(await leverPage.locator("#resume").evaluate((control) => control.files[0]?.name), "resume.pdf");
    assert.equal((await leverPage.evaluate(() => globalThis.__jobflowMessages)).filter(
      (item) => item.type === "JOBFLOW_USER_SUBMIT_OBSERVED"
    ).length, 0);
    await leverPage.locator("button[type=submit]").click();
    await leverPage.waitForFunction(() => globalThis.__jobflowMessages.some(
      (item) => item.type === "JOBFLOW_USER_SUBMIT_OBSERVED"
    ));
    const leverSubmitSignals = (await leverPage.evaluate(() => globalThis.__jobflowMessages)).filter(
      (item) => item.type === "JOBFLOW_USER_SUBMIT_OBSERVED"
    );
    assert.equal(leverSubmitSignals.length, 1);
    assert.equal(leverSubmitSignals[0].payload.trusted_user_event, true);
    await leverPage.close();

    const leverNavigationRuntime = await verifyProviderApplicationRuntime(browser, {
      url: "https://jobs.lever.co/example/abc-123/apply/staged",
      fixtureHtml: leverContinueFixture,
      fieldValues: ["Synthetic Applicant", "https://example.com/portfolio", "https://linkedin.com/in/example"],
      fieldSelector: "#name, #portfolio, #linkedin",
      fileIndex: 3,
      fileSelector: "#resume",
      navigationIndex: 4,
      navigationSelector: "#continue",
      navigationLabel: "Continue",
      finalIndex: 5,
      finalSelector: "#submit"
    });

    const workdayPage = await browser.newPage();
    await workdayPage.route("https://example.wd5.myworkdayjobs.com/**", (route) => route.fulfill({
      status: 200, contentType: "text/html; charset=utf-8", body: workdayReviewFixture
    }));
    await workdayPage.goto(
      "https://example.wd5.myworkdayjobs.com/en-US/Careers/job/123/apply/review",
      {waitUntil: "domcontentloaded"}
    );
    await workdayPage.evaluate(({encodedFile}) => {
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
      document.querySelector("form").addEventListener("submit", (event) => event.preventDefault());
    }, {encodedFile: fileBytes.toString("base64")});
    await workdayPage.addScriptTag({path: companion});
    await workdayPage.evaluate(() => {
      globalThis.__jobflowCall = (message) => new Promise((resolve) => {
        globalThis.__jobflowListener(message, {}, resolve);
      });
    });
    const workdayCollected = await workdayPage.evaluate(() => globalThis.__jobflowCall({type: "JOBFLOW_COLLECT_FORM"}));
    assert.equal(workdayCollected.status, "COLLECTED");
    assert.equal(new URL(workdayPage.url()).hostname, "example.wd5.myworkdayjobs.com");
    assert.equal(workdayCollected.payload.client_refs.length, 8);
    assert.equal(await workdayPage.locator("main").getAttribute("data-provider"), "workday");
    const workdayValues = [
      "Synthetic Applicant", "synthetic@example.test", "https://example.com/portfolio", "Yes", "75000"
    ];
    const workdayFields = workdayValues.map((value, index) => ({
      client_ref: workdayCollected.payload.client_refs[index], value, value_sha256: valueHash(value)
    }));
    const workdayFiles = [{
      client_ref: workdayCollected.payload.client_refs[6],
      purpose: "resume", filename: "resume.pdf", sha256: fileHash,
      download_url: "http://127.0.0.1/assist/synthetic/file/workday-resume"
    }];
    const workdayApplied = await workdayPage.evaluate(
      ({refs, fields, files, controlSemantics}) => globalThis.__jobflowCall({
        type: "JOBFLOW_APPLY_APPROVED",
        control_semantics_sha256: controlSemantics,
        fields, files, navigation: null, final_submit_client_refs: [refs[7]]
      }),
      {
        refs: workdayCollected.payload.client_refs,
        fields: workdayFields,
        files: workdayFiles,
        controlSemantics: workdayCollected.payload.control_semantics_sha256
      }
    );
    assert.equal(workdayApplied.status, "APPLIED", JSON.stringify(workdayApplied));
    assert.equal(workdayApplied.field_bindings.length, 5);
    assert.equal(workdayApplied.material_bindings.length, 1);
    assert.equal(workdayApplied.final_submit_armed, true);
    assert.deepEqual(await workdayPage.locator("#wd-name, #wd-email, #wd-portfolio, #wd-auth, #wd-salary").evaluateAll(
      (controls) => controls.map((control) => control.value)
    ), workdayValues);
    assert.equal(await workdayPage.locator("#wd-gender").inputValue(), "Decline to answer");
    assert.equal(await workdayPage.locator("#wd-resume").evaluate((control) => control.files[0]?.name), "resume.pdf");
    assert.equal((await workdayPage.evaluate(() => globalThis.__jobflowMessages)).filter(
      (item) => item.type === "JOBFLOW_USER_SUBMIT_OBSERVED"
    ).length, 0);
    await workdayPage.locator("button[type=submit]").click();
    await workdayPage.waitForFunction(() => globalThis.__jobflowMessages.some(
      (item) => item.type === "JOBFLOW_USER_SUBMIT_OBSERVED"
    ));
    const workdaySubmitSignals = (await workdayPage.evaluate(() => globalThis.__jobflowMessages)).filter(
      (item) => item.type === "JOBFLOW_USER_SUBMIT_OBSERVED"
    );
    assert.equal(workdaySubmitSignals.length, 1);
    assert.equal(workdaySubmitSignals[0].payload.trusted_user_event, true);
    await workdayPage.close();

    const ashbyRuntime = await verifyProviderApplicationRuntime(browser, {
      url: "https://jobs.ashbyhq.com/example/11111111-1111-4111-8111-111111111111/application",
      fixtureHtml: ashbyFixture,
      fieldValues: ["Synthetic Applicant", "synthetic@example.test"],
      fieldSelector: "#full-name, #email",
      fileIndex: 2,
      fileSelector: "#resume",
      navigationIndex: 3,
      navigationSelector: "button[type=button]",
      navigationLabel: "Continue",
      finalIndex: 4,
      finalSelector: "button[type=submit]"
    });

    const modernAshbyPage = await browser.newPage();
    const modernAshbyUrl = "https://jobs.ashbyhq.com/example/22222222-2222-4222-8222-222222222222/application";
    await modernAshbyPage.route(modernAshbyUrl, (route) => route.fulfill({
      status: 200, contentType: "text/html; charset=utf-8", body: ashbyModernFixture
    }));
    await modernAshbyPage.goto(modernAshbyUrl, {waitUntil: "domcontentloaded"});
    await modernAshbyPage.evaluate(({encodedFile}) => {
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
      document.querySelector("form").addEventListener("submit", (event) => event.preventDefault());
    }, {encodedFile: fileBytes.toString("base64")});
    await modernAshbyPage.addScriptTag({path: companion});
    await modernAshbyPage.evaluate(() => {
      globalThis.__jobflowCall = (message) => new Promise((resolve) => {
        globalThis.__jobflowListener(message, {}, resolve);
      });
    });
    const modernAshbyCollected = await modernAshbyPage.evaluate(
      () => globalThis.__jobflowCall({type: "JOBFLOW_COLLECT_FORM"})
    );
    assert.equal(modernAshbyCollected.status, "COLLECTED");
    assert.equal(modernAshbyCollected.payload.client_refs.length, 9, modernAshbyCollected.payload.sanitized_html);
    assert.equal(new Set(modernAshbyCollected.payload.client_refs).size, 9);
    assert.ok(!modernAshbyCollected.payload.sanitized_html.includes("Search jobs"));
    assert.ok(!modernAshbyCollected.payload.sanitized_html.includes("Autofill from resume"));
    assert.ok(!modernAshbyCollected.payload.sanitized_html.includes("candidate_token"));
    assert.ok(!modernAshbyCollected.payload.sanitized_html.includes("must-not-leave-page"));
    assert.ok(!modernAshbyCollected.payload.sanitized_html.includes("OPAQUE-PATH-MUST-NOT-LEAVE"));
    assert.ok(!modernAshbyCollected.payload.sanitized_html.includes("private-fragment"));
    assert.ok(!modernAshbyCollected.payload.sanitized_html.includes("22222222-2222-4222-8222-222222222222"));
    assert.match(
      modernAshbyCollected.payload.sanitized_html,
      /action="https:\/\/jobs\.ashbyhq\.com\/__jobflow_route_redacted__"/
    );
    assert.match(modernAshbyCollected.payload.sanitized_html, /<label[^>]*>Name<\/label><input[^>]*name="name"/);
    assert.match(modernAshbyCollected.payload.sanitized_html, /<input[^>]*name="_systemfield_resume"[^>]*type="file"/);
    assert.match(modernAshbyCollected.payload.sanitized_html, /Cover Letter[\s\S]*<textarea/);
    const modernAshbyValues = [
      [0, "Synthetic Applicant"],
      [2, "+1"],
      [6, "Synthetic cover letter text grounded only in approved test evidence."],
      [7, "Yes"]
    ];
    const modernAshbyFields = modernAshbyValues.map(([index, value]) => ({
      client_ref: modernAshbyCollected.payload.client_refs[index], value, value_sha256: valueHash(value)
    }));
    const modernAshbyApplied = await modernAshbyPage.evaluate(
      ({refs, fields, fileHash, controlSemantics}) => globalThis.__jobflowCall({
        type: "JOBFLOW_APPLY_APPROVED",
        control_semantics_sha256: controlSemantics,
        fields,
        files: [{
          client_ref: refs[5], purpose: "resume", filename: "resume.pdf", sha256: fileHash,
          download_url: "http://127.0.0.1/assist/synthetic/file/modern-ashby-resume"
        }],
        navigation: null,
        final_submit_client_refs: [refs[8]]
      }),
      {
        refs: modernAshbyCollected.payload.client_refs,
        fields: modernAshbyFields,
        fileHash,
        controlSemantics: modernAshbyCollected.payload.control_semantics_sha256
      }
    );
    assert.equal(modernAshbyApplied.status, "APPLIED", JSON.stringify(modernAshbyApplied));
    assert.equal(modernAshbyApplied.field_bindings.length, 4);
    assert.equal(modernAshbyApplied.material_bindings.length, 1);
    assert.equal(modernAshbyApplied.navigation_ready, false);
    assert.equal(modernAshbyApplied.final_submit_armed, true);
    assert.equal(await modernAshbyPage.locator("#candidate-name").inputValue(), "Synthetic Applicant");
    assert.equal(await modernAshbyPage.locator("#phone-country-code").inputValue(), "+1");
    assert.equal(
      await modernAshbyPage.locator("#cover-letter").inputValue(),
      "Synthetic cover letter text grounded only in approved test evidence."
    );
    assert.equal(await modernAshbyPage.locator('input[name="relocation"]:checked').inputValue(), "Yes");
    assert.equal(await modernAshbyPage.locator('input[type="file"]:not([id])').evaluate((control) => control.files.length), 0);
    assert.equal(await modernAshbyPage.locator("#_systemfield_resume").evaluate((control) => control.files[0]?.name), "resume.pdf");
    assert.equal(await modernAshbyPage.locator("#_systemfield_resume").evaluate((control) => control.files[0]?.type), "application/pdf");
    assert.equal((await modernAshbyPage.evaluate(() => globalThis.__jobflowMessages)).filter(
      (item) => item.type === "JOBFLOW_USER_SUBMIT_OBSERVED"
    ).length, 0);
    await modernAshbyPage.evaluate(() => {
      const form = document.querySelector("form");
      const label = document.createElement("label");
      label.htmlFor = "duplicate-resume";
      label.textContent = "Resume";
      const input = document.createElement("input");
      input.id = "duplicate-resume";
      input.name = "duplicate_resume";
      input.type = "file";
      input.hidden = true;
      form.append(label, input);
    });
    const ambiguousAshbyCollected = await modernAshbyPage.evaluate(
      () => globalThis.__jobflowCall({type: "JOBFLOW_COLLECT_FORM"})
    );
    assert.equal(ambiguousAshbyCollected.status, "BLOCKED");
    assert.equal(ambiguousAshbyCollected.code, "COMPANION_AMBIGUOUS_FILE_CONTROLS");
    assert.equal(ambiguousAshbyCollected.automatic_retry, false);
    assert.equal(ambiguousAshbyCollected.ambiguous_upload_control_count, 2);
    assert.equal((await modernAshbyPage.evaluate(() => globalThis.__jobflowMessages)).filter(
      (item) => item.type === "JOBFLOW_USER_SUBMIT_OBSERVED"
    ).length, 0);
    await modernAshbyPage.close();
    const smartRecruitersRuntime = await verifyProviderApplicationRuntime(browser, {
      url: "https://jobs.smartrecruiters.com/example/12345-synthetic-credit-analyst/apply",
      fixtureHtml: smartRecruitersFixture,
      fieldValues: ["Synthetic", "Applicant", "synthetic@example.test"],
      fieldSelector: "#first-name, #last-name, #email",
      fileIndex: 3,
      fileSelector: "#resume",
      navigationIndex: 4,
      navigationSelector: "button[type=button]",
      navigationLabel: "Next",
      finalIndex: 5,
      finalSelector: "button[type=submit]"
    });
    const greenhouseRebinding = await verifyProviderRebindingRuntime(browser, {
      url: "https://boards.greenhouse.io/example/jobs/987654/rebinding",
      fixtureHtml: greenhouseFixture,
      targetSelector: "#portfolio", targetIndex: 1,
      value: "https://example.com/greenhouse-portfolio", finalIndex: 4, finalSelector: "#submit"
    });
    const leverRebinding = await verifyProviderRebindingRuntime(browser, {
      url: "https://jobs.lever.co/example/abc-123/apply/rebinding",
      fixtureHtml: leverFixture,
      targetSelector: "#linkedin", targetIndex: 2,
      value: "https://linkedin.com/in/rebound-example", finalIndex: 4, finalSelector: "button[type=submit]"
    });
    const workdayRebinding = await verifyProviderRebindingRuntime(browser, {
      url: "https://example.wd5.myworkdayjobs.com/en-US/Careers/job/123/apply/rebinding",
      fixtureHtml: workdayReviewFixture,
      targetSelector: "#wd-email", targetIndex: 1,
      value: "rebound@example.test", finalIndex: 7, finalSelector: "button[type=submit]"
    });
    const ashbyRebinding = await verifyProviderRebindingRuntime(browser, {
      url: "https://jobs.ashbyhq.com/example/11111111-1111-4111-8111-111111111111/application/rebinding",
      fixtureHtml: ashbyFixture,
      targetSelector: "#email", targetIndex: 1,
      value: "rebound@example.test", finalIndex: 4, finalSelector: "button[type=submit]"
    });
    const smartRecruitersRebinding = await verifyProviderRebindingRuntime(browser, {
      url: "https://jobs.smartrecruiters.com/example/12345-synthetic-credit-analyst/apply/rebinding",
      fixtureHtml: smartRecruitersFixture,
      targetSelector: "#last-name", targetIndex: 1,
      value: "Rebound", finalIndex: 5, finalSelector: "button[type=submit]"
    });

    const tekPage = await browser.newPage();
    await tekPage.route("https://apply.teksystems.test/**", (route) => route.fulfill({
      status: 200, contentType: "text/html; charset=utf-8", body: tekFixture
    }));
    await tekPage.goto("https://apply.teksystems.test/v1/s/", {waitUntil: "domcontentloaded"});
    await tekPage.evaluate(() => {
      globalThis.__jobflowListener = null;
      globalThis.chrome = {
        dom: {openOrClosedShadowRoot(element) {
          return globalThis.__jobflowClosedRoots?.get(element) || element.shadowRoot || null;
        }},
        runtime: {
          lastError: null,
          onMessage: {addListener(listener) { globalThis.__jobflowListener = listener; }},
          sendMessage() { return Promise.resolve({status: "RECORDED"}); },
          connect() { throw new Error("NO_FILE_STREAM_EXPECTED"); }
        }
      };
    });
    await tekPage.addScriptTag({path: companion});
    await tekPage.evaluate(() => {
      globalThis.__jobflowCall = (message) => new Promise((resolve) => globalThis.__jobflowListener(message, {}, resolve));
    });
    const tekCollected = await tekPage.evaluate(() => globalThis.__jobflowCall({type: "JOBFLOW_COLLECT_FORM"}));
    assert.equal(tekCollected.status, "COLLECTED");
    assert.equal(tekCollected.payload.client_refs.length, 8);
    assert.match(tekCollected.payload.sanitized_html, /<label[^>]*>Phone Type<\/label><select[^>]*data-jobflow-custom-select="true"/);
    assert.match(tekCollected.payload.sanitized_html, /two professional references/);
    const tekApplied = await tekPage.evaluate(
      ({clientRef, value, hash, controlSemantics}) => globalThis.__jobflowCall({
        type: "JOBFLOW_APPLY_APPROVED",
        control_semantics_sha256: controlSemantics,
        fields: [{client_ref: clientRef, value, value_sha256: hash}],
        files: [], navigation: null,
        final_submit_client_refs: []
      }),
      {
        clientRef: tekCollected.payload.client_refs[6],
        value: "Mobile",
        hash: valueHash("Mobile"),
        controlSemantics: tekCollected.payload.control_semantics_sha256
      }
    );
    assert.equal(tekApplied.status, "APPLIED", JSON.stringify(tekApplied));
    assert.match(await tekPage.locator("#phone-type").evaluate(
      (host) => host.shadowRoot.querySelector("lightning-button-menu > button").innerText
    ), /^Mobile\s+Show menu$/);
    await tekPage.close();

    const ariaComboboxPage = await browser.newPage();
    const ariaComboboxFixture = `<!doctype html><html><body><form>
      <label for="country">Country *</label>
      <input id="country" name="country" role="combobox" aria-controls="country-options"
        aria-expanded="false" aria-required="true" required>
      <div id="country-options" role="listbox" hidden>
        <button type="button" role="option" data-value="United States">United States</button>
        <button type="button" role="option" data-value="Canada">Canada</button>
      </div>
      <button id="aria-submit" type="submit">Submit</button>
    </form></body></html>`;
    await ariaComboboxPage.route("https://aria.example.test/**", (route) => route.fulfill({
      status: 200, contentType: "text/html; charset=utf-8", body: ariaComboboxFixture
    }));
    await ariaComboboxPage.goto("https://aria.example.test/apply", {waitUntil: "domcontentloaded"});
    await ariaComboboxPage.evaluate(() => {
      globalThis.__jobflowListener = null;
      globalThis.chrome = {
        runtime: {
          lastError: null,
          onMessage: {addListener(listener) { globalThis.__jobflowListener = listener; }},
          sendMessage() { return Promise.resolve({status: "RECORDED"}); },
          connect() { throw new Error("NO_FILE_STREAM_EXPECTED"); }
        }
      };
      const input = document.querySelector("#country");
      const popup = document.querySelector("#country-options");
      const open = () => { popup.hidden = false; input.setAttribute("aria-expanded", "true"); };
      input.addEventListener("click", open);
      input.addEventListener("input", open);
      input.addEventListener("keydown", (event) => { if (event.key === "ArrowDown") open(); });
      for (const option of popup.querySelectorAll("[role=option]")) {
        option.addEventListener("click", () => {
          input.value = option.dataset.value;
          input.setAttribute("aria-valuetext", option.dataset.value);
          input.setAttribute("aria-expanded", "false");
          popup.hidden = true;
          input.dispatchEvent(new Event("change", {bubbles: true}));
        });
      }
      document.querySelector("form").addEventListener("submit", (event) => event.preventDefault());
    });
    await ariaComboboxPage.addScriptTag({path: companion});
    await ariaComboboxPage.evaluate(() => {
      globalThis.__jobflowCall = (message) => new Promise((resolve) => globalThis.__jobflowListener(message, {}, resolve));
    });
    const ariaCollected = await ariaComboboxPage.evaluate(() => globalThis.__jobflowCall({type: "JOBFLOW_COLLECT_FORM"}));
    assert.equal(ariaCollected.status, "COLLECTED");
    assert.equal(ariaCollected.payload.client_refs.length, 2);
    assert.match(ariaCollected.payload.sanitized_html, /data-jobflow-aria-combobox="true"/);
    const ariaApplied = await ariaComboboxPage.evaluate(
      ({clientRefs, value, hash, controlSemantics}) => globalThis.__jobflowCall({
        type: "JOBFLOW_APPLY_APPROVED",
        control_semantics_sha256: controlSemantics,
        fields: [{client_ref: clientRefs[0], value, value_sha256: hash}],
        files: [], navigation: null, final_submit_client_refs: [clientRefs[1]]
      }),
      {
        clientRefs: ariaCollected.payload.client_refs,
        value: "United States",
        hash: valueHash("United States"),
        controlSemantics: ariaCollected.payload.control_semantics_sha256
      }
    );
    assert.equal(ariaApplied.status, "APPLIED", JSON.stringify(ariaApplied));
    assert.equal(await ariaComboboxPage.locator("#country").inputValue(), "United States");
    assert.equal(ariaApplied.final_submit_armed, true);
    const ariaMissing = await ariaComboboxPage.evaluate(
      ({clientRef, value, hash, controlSemantics}) => globalThis.__jobflowCall({
        type: "JOBFLOW_APPLY_APPROVED",
        control_semantics_sha256: controlSemantics,
        fields: [{client_ref: clientRef, value, value_sha256: hash}],
        files: [], navigation: null, final_submit_client_refs: []
      }),
      {
        clientRef: ariaCollected.payload.client_refs[0],
        value: "Mexico",
        hash: valueHash("Mexico"),
        controlSemantics: ariaCollected.payload.control_semantics_sha256
      }
    );
    assert.equal(ariaMissing.status, "BLOCKED", JSON.stringify(ariaMissing));
    assert.equal(ariaMissing.code, "COMPANION_ARIA_COMBOBOX_OPTION_NOT_FOUND");
    assert.equal(await ariaComboboxPage.locator("#country").inputValue(), "United States");
    await ariaComboboxPage.close();

    const buttonComboboxPage = await browser.newPage();
    const buttonComboboxFixture = `<!doctype html><html><body><form>
      <label id="work-setting-label">Work setting *</label>
      <button id="work-setting" name="work_setting" type="button" role="combobox"
        aria-labelledby="work-setting-label" aria-controls="work-setting-options"
        aria-expanded="false" aria-required="true">Choose a setting</button>
      <div id="work-setting-options" role="listbox" hidden>
        <button type="button" role="option" data-value="Hybrid">Hybrid</button>
        <button type="button" role="option" data-value="Remote">Remote</button>
      </div>
      <button id="button-combobox-submit" type="submit">Submit</button>
    </form></body></html>`;
    await buttonComboboxPage.route("https://button-combobox.example.test/**", (route) => route.fulfill({
      status: 200, contentType: "text/html; charset=utf-8", body: buttonComboboxFixture
    }));
    await buttonComboboxPage.goto("https://button-combobox.example.test/apply", {waitUntil: "domcontentloaded"});
    await buttonComboboxPage.evaluate(() => {
      globalThis.__jobflowListener = null;
      globalThis.chrome = {
        runtime: {
          lastError: null,
          onMessage: {addListener(listener) { globalThis.__jobflowListener = listener; }},
          sendMessage() { return Promise.resolve({status: "RECORDED"}); },
          connect() { throw new Error("NO_FILE_STREAM_EXPECTED"); }
        }
      };
      const control = document.querySelector("#work-setting");
      const popup = document.querySelector("#work-setting-options");
      control.addEventListener("click", () => {
        popup.hidden = false;
        control.setAttribute("aria-expanded", "true");
      });
      for (const option of popup.querySelectorAll("[role=option]")) {
        option.addEventListener("click", () => {
          control.textContent = `${option.dataset.value} Show menu`;
          control.setAttribute("data-selected-value", option.dataset.value);
          control.setAttribute("aria-valuetext", option.dataset.value);
          control.setAttribute("aria-expanded", "false");
          popup.hidden = true;
          control.dispatchEvent(new Event("change", {bubbles: true}));
        });
      }
      document.querySelector("form").addEventListener("submit", (event) => event.preventDefault());
    });
    await buttonComboboxPage.addScriptTag({path: companion});
    await buttonComboboxPage.evaluate(() => {
      globalThis.__jobflowCall = (message) => new Promise((resolve) => globalThis.__jobflowListener(message, {}, resolve));
    });
    const buttonComboboxCollected = await buttonComboboxPage.evaluate(
      () => globalThis.__jobflowCall({type: "JOBFLOW_COLLECT_FORM"})
    );
    assert.equal(buttonComboboxCollected.status, "COLLECTED");
    assert.equal(buttonComboboxCollected.payload.client_refs.length, 2);
    assert.match(buttonComboboxCollected.payload.sanitized_html, /data-jobflow-aria-combobox="true"/);
    const buttonComboboxApplied = await buttonComboboxPage.evaluate(
      ({refs, value, hash, controlSemantics}) => globalThis.__jobflowCall({
        type: "JOBFLOW_APPLY_APPROVED",
        control_semantics_sha256: controlSemantics,
        fields: [{client_ref: refs[0], value, value_sha256: hash}],
        files: [], navigation: null, final_submit_client_refs: [refs[1]]
      }),
      {
        refs: buttonComboboxCollected.payload.client_refs,
        value: "Hybrid",
        hash: valueHash("Hybrid"),
        controlSemantics: buttonComboboxCollected.payload.control_semantics_sha256
      }
    );
    assert.equal(buttonComboboxApplied.status, "APPLIED", JSON.stringify(buttonComboboxApplied));
    assert.equal(await buttonComboboxPage.locator("#work-setting").getAttribute("aria-valuetext"), "Hybrid");
    assert.equal(buttonComboboxApplied.final_submit_armed, true);
    await buttonComboboxPage.close();

    const repaintPage = await browser.newPage();
    await repaintPage.route("https://repaint.example.test/**", (route) => route.fulfill({
      status: 200, contentType: "text/html; charset=utf-8",
      body: "<!doctype html><html><body><div id='component-host'></div></body></html>"
    }));
    await repaintPage.goto("https://repaint.example.test/apply", {waitUntil: "domcontentloaded"});
    await repaintPage.evaluate(() => {
      globalThis.__jobflowListener = null;
      globalThis.__componentRedraws = 0;
      globalThis.chrome = {
        dom: {openOrClosedShadowRoot(element) { return element.shadowRoot || null; }},
        runtime: {
          lastError: null,
          onMessage: {addListener(listener) { globalThis.__jobflowListener = listener; }},
          sendMessage() { return Promise.resolve({status: "RECORDED"}); },
          connect() { throw new Error("NO_FILE_STREAM_EXPECTED"); }
        }
      };
      const root = document.querySelector("#component-host").attachShadow({mode: "open"});
      const render = (firstValue = "") => {
        root.innerHTML = `<form id="component-form">
          <label for="component-first">First Name</label>
          <input id="component-first" name="first_name" autocomplete="given-name" value="${firstValue}">
          <label for="component-last">Last Name</label>
          <input id="component-last" name="last_name" autocomplete="family-name">
          <button id="component-submit" type="submit">Submit</button>
        </form>`;
        root.querySelector("form").addEventListener("submit", (event) => event.preventDefault());
      };
      render();
      root.querySelector("#component-first").addEventListener("change", (event) => {
        const retained = event.target.value;
        setTimeout(() => {
          globalThis.__componentRedraws += 1;
          render(retained);
        }, 60);
      }, {once: true});
    });
    await repaintPage.addScriptTag({path: companion});
    await repaintPage.evaluate(() => {
      globalThis.__jobflowCall = (message) => new Promise((resolve) => globalThis.__jobflowListener(message, {}, resolve));
    });
    const repaintCollected = await repaintPage.evaluate(() => globalThis.__jobflowCall({type: "JOBFLOW_COLLECT_FORM"}));
    assert.equal(repaintCollected.status, "COLLECTED");
    assert.equal(repaintCollected.payload.client_refs.length, 3);
    const repaintApplied = await repaintPage.evaluate(
      ({refs, first, last, firstHash, lastHash, controlSemantics}) => globalThis.__jobflowCall({
        type: "JOBFLOW_APPLY_APPROVED",
        control_semantics_sha256: controlSemantics,
        fields: [
          {client_ref: refs[0], value: first, value_sha256: firstHash},
          {client_ref: refs[1], value: last, value_sha256: lastHash}
        ],
        files: [], navigation: null, final_submit_client_refs: [refs[2]]
      }),
      {
        refs: repaintCollected.payload.client_refs,
        first: "Synthetic", last: "Applicant",
        firstHash: valueHash("Synthetic"), lastHash: valueHash("Applicant"),
        controlSemantics: repaintCollected.payload.control_semantics_sha256
      }
    );
    assert.equal(repaintApplied.status, "APPLIED", JSON.stringify(repaintApplied));
    assert.equal(await repaintPage.evaluate(() => globalThis.__componentRedraws), 1);
    assert.equal(await repaintPage.evaluate(() => document.querySelector("#component-host").shadowRoot.querySelector("#component-first").value), "Synthetic");
    assert.equal(await repaintPage.evaluate(() => document.querySelector("#component-host").shadowRoot.querySelector("#component-last").value), "Applicant");
    assert.equal(repaintApplied.final_submit_armed, true);
    await repaintPage.close();

    const delayedPage = await browser.newPage();
    await delayedPage.route("https://delayed.example.test/**", (route) => route.fulfill({
      status: 200, contentType: "text/html; charset=utf-8",
      body: "<!doctype html><html><body><main id='application-root'></main></body></html>"
    }));
    await delayedPage.goto("https://delayed.example.test/apply", {waitUntil: "domcontentloaded"});
    await delayedPage.evaluate(() => {
      globalThis.__jobflowListener = null;
      globalThis.chrome = {
        runtime: {
          lastError: null,
          onMessage: {addListener(listener) { globalThis.__jobflowListener = listener; }},
          sendMessage() { return Promise.resolve({status: "RECORDED"}); },
          connect() { throw new Error("NO_FILE_STREAM_EXPECTED"); }
        }
      };
    });
    await delayedPage.addScriptTag({path: companion});
    await delayedPage.evaluate(() => {
      setTimeout(() => {
        document.querySelector("#application-root").innerHTML = `
          <form><label for="late-name">First Name</label>
          <input id="late-name" type="text" required placeholder="First Name">
          <button type="submit">Submit</button></form>`;
      }, 300);
      globalThis.__jobflowCall = (message) => new Promise((resolve) => globalThis.__jobflowListener(message, {}, resolve));
    });
    const delayedCollected = await delayedPage.evaluate(() => globalThis.__jobflowCall({type: "JOBFLOW_COLLECT_FORM"}));
    assert.equal(delayedCollected.status, "COLLECTED", JSON.stringify(delayedCollected));
    assert.equal(delayedCollected.payload.client_refs.length, 2);
    assert.match(delayedCollected.payload.sanitized_html, /First Name/);
    await delayedPage.close();

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
      ({clientRefs, value, hash, pageHash, semanticsHash, controlSemantics}) => globalThis.__jobflowCall({
        type: "JOBFLOW_APPLY_APPROVED",
        control_semantics_sha256: controlSemantics,
        fields: [{client_ref: clientRefs[0], value, value_sha256: hash}],
        files: [], navigation: {
          client_ref: clientRefs[1], mode: "MANUAL_USER_CLICK", control_type: "submit",
          page_content_hash: pageHash, control_semantics_hash: semanticsHash, display_label: "Next"
        }, final_submit_client_refs: []
      }),
      {
        clientRefs: navCollected.payload.client_refs, value: navValue, hash: valueHash(navValue),
        pageHash: manualPageHash, semanticsHash: manualSemanticsHash,
        controlSemantics: navCollected.payload.control_semantics_sha256
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
      ({clientRefs, pageHash, semanticsHash, controlSemantics}) => globalThis.__jobflowCall({
        type: "JOBFLOW_APPLY_APPROVED", fields: [], files: [], final_submit_client_refs: [],
        control_semantics_sha256: controlSemantics,
        navigation: {
          client_ref: clientRefs[1], mode: "MANUAL_USER_CLICK", control_type: "submit",
          page_content_hash: pageHash, control_semantics_hash: semanticsHash, display_label: "Next"
        }
      }),
      {
        clientRefs: preventedCollected.payload.client_refs,
        pageHash: preventedHash,
        semanticsHash: preventedSemantics,
        controlSemantics: preventedCollected.payload.control_semantics_sha256
      }
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
      ({clientRefs, pageHash, semanticsHash, controlSemantics}) => globalThis.__jobflowCall({
        type: "JOBFLOW_APPLY_APPROVED", fields: [], files: [], final_submit_client_refs: [],
        control_semantics_sha256: controlSemantics,
        navigation: {
          client_ref: clientRefs[1], mode: "MANUAL_USER_CLICK", control_type: "submit",
          page_content_hash: pageHash, control_semantics_hash: semanticsHash, display_label: "Next"
        }
      }),
      {
        clientRefs: spaCollected.payload.client_refs,
        pageHash: spaHash,
        semanticsHash: spaSemantics,
        controlSemantics: spaCollected.payload.control_semantics_sha256
      }
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
    await explicitPage.route("https://boards.greenhouse.io/**", (route) => route.fulfill({
      status: 200, contentType: "text/html; charset=utf-8", body: greenhouseContinueFixture
    }));
    await explicitPage.goto(
      "https://boards.greenhouse.io/example/jobs/987654/application/step-1",
      {waitUntil: "domcontentloaded"}
    );
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
    assert.equal(new URL(explicitPage.url()).hostname, "boards.greenhouse.io");
    assert.equal(await explicitPage.locator("main").getAttribute("data-provider"), "greenhouse");
    assert.equal(explicitCollected.payload.client_refs.length, 3);
    const explicitPageHash = valueHash(explicitCollected.payload.sanitized_html);
    const explicitSemanticsHash = valueHash(JSON.stringify([
      explicitPageHash, explicitCollected.payload.client_refs[2], "button", "Continue"
    ]));
    const explicitValues = [navValue, "synthetic@example.test"];
    const explicitApplied = await explicitPage.evaluate(
      ({clientRefs, pageHash, semanticsHash, values, hashes, controlSemantics}) => globalThis.__jobflowCall({
        type: "JOBFLOW_APPLY_APPROVED",
        control_semantics_sha256: controlSemantics,
        fields: [
          {client_ref: clientRefs[0], value: values[0], value_sha256: hashes[0]},
          {client_ref: clientRefs[1], value: values[1], value_sha256: hashes[1]}
        ],
        files: [], final_submit_client_refs: [],
        navigation: {
          client_ref: clientRefs[2], mode: "PROGRAMMATIC_EXPLICIT_BUTTON", control_type: "button",
          page_content_hash: pageHash, control_semantics_hash: semanticsHash, display_label: "Continue"
        }
      }),
      {
        clientRefs: explicitCollected.payload.client_refs, pageHash: explicitPageHash,
        semanticsHash: explicitSemanticsHash, values: explicitValues,
        hashes: explicitValues.map((value) => valueHash(value)),
        controlSemantics: explicitCollected.payload.control_semantics_sha256
      }
    );
    assert.equal(explicitApplied.navigation_ready, true);
    const explicitChecked = await explicitPage.evaluate(
      (clientRef) => globalThis.__jobflowCall({type: "JOBFLOW_CHECK_NAVIGATION", client_ref: clientRef}),
      explicitCollected.payload.client_refs[2]
    );
    assert.equal(explicitChecked.status, "NAVIGATION_VALID", JSON.stringify(explicitChecked));
    const staleAuthorization = await explicitPage.evaluate(
      ({clientRef, semanticsHash}) => globalThis.__jobflowCall({
        type: "JOBFLOW_NAVIGATE_APPROVED", client_ref: clientRef,
        page_content_hash: `sha256:${"0".repeat(64)}`, control_semantics_hash: semanticsHash
      }),
      {clientRef: explicitCollected.payload.client_refs[2], semanticsHash: explicitSemanticsHash}
    );
    assert.equal(staleAuthorization.code, "COMPANION_NAVIGATION_AUTHORIZATION_STALE");
    assert.equal(await explicitPage.evaluate(() => globalThis.__explicitClicks), 0);
    const explicitStarted = await explicitPage.evaluate(
      ({clientRef, pageHash, semanticsHash}) => globalThis.__jobflowCall({
        type: "JOBFLOW_NAVIGATE_APPROVED", client_ref: clientRef,
        page_content_hash: pageHash, control_semantics_hash: semanticsHash
      }),
      {
        clientRef: explicitCollected.payload.client_refs[2], pageHash: explicitChecked.page_content_hash,
        semanticsHash: explicitChecked.control_semantics_hash
      }
    );
    assert.equal(explicitStarted.status, "NAVIGATION_STARTED");
    assert.equal(await explicitPage.evaluate(() => globalThis.__explicitClicks), 1);

    const lwcFixture = fs.readFileSync(
      path.join(project, "tests", "fixtures", "synthetic-teksystems-lwc-form.html"), "utf8"
    );
    const lwcPage = await browser.newPage();
    await lwcPage.route("https://apply.teksystems.example/**", (route) => route.fulfill({
      status: 200, contentType: "text/html; charset=utf-8", body: lwcFixture
    }));
    await lwcPage.goto("https://apply.teksystems.example/v1/s/", {waitUntil: "domcontentloaded"});
    await lwcPage.evaluate(({encodedFile}) => {
      globalThis.__jobflowMessages = [];
      globalThis.__jobflowListener = null;
      const decode = () => Uint8Array.from(atob(encodedFile), (character) => character.charCodeAt(0));
      globalThis.chrome = {
        dom: {openOrClosedShadowRoot(element) {
          return globalThis.__jobflowClosedRoots?.get(element) || element.shadowRoot || null;
        }},
        runtime: {
          lastError: null,
          onMessage: {addListener(listener) { globalThis.__jobflowListener = listener; }},
          async sendMessage(message) { globalThis.__jobflowMessages.push(message); return {status: "RECORDED"}; },
          connect() {
            const messageListeners = [];
            return {
              onMessage: {addListener(listener) { messageListeners.push(listener); }},
              onDisconnect: {addListener() {}},
              postMessage() {
                const bytes = decode();
                let binary = "";
                for (const value of bytes) binary += String.fromCharCode(value);
                queueMicrotask(() => {
                  for (const listener of messageListeners) listener({type: "chunk", data: btoa(binary)});
                  for (const listener of messageListeners) listener({type: "end"});
                });
              },
              disconnect() {}
            };
          }
        }
      };
    }, {encodedFile: fileBytes.toString("base64")});
    await lwcPage.addScriptTag({path: companion});
    await lwcPage.evaluate(() => {
      globalThis.__jobflowCall = (message) => new Promise((resolve) => globalThis.__jobflowListener(message, {}, resolve));
    });
    const lwcCollected = await lwcPage.evaluate(() => globalThis.__jobflowCall({type: "JOBFLOW_COLLECT_FORM"}));
    assert.equal(lwcCollected.status, "COLLECTED");
    assert.equal(lwcCollected.payload.client_refs.length, 8, lwcCollected.payload.sanitized_html);
    assert.match(lwcCollected.payload.sanitized_html, /authorized to work[\s\S]*<option>Yes<\/option><option>No<\/option>/);
    assert.match(lwcCollected.payload.sanitized_html, /work settings[\s\S]*<option>Hybrid<\/option><option>On-site<\/option><option>Remote<\/option>/);
    const lwcValues = ["Jordan", "Lee", "Yes", "On-site", "Mobile", "Yes"];
    const lwcFields = [1, 2, 3, 4, 6, 7].map((index, valueIndex) => ({
      client_ref: lwcCollected.payload.client_refs[index], value: lwcValues[valueIndex],
      value_sha256: valueHash(lwcValues[valueIndex])
    }));
    const lwcApplied = await lwcPage.evaluate(
      ({refs, fields, fileHash, controlSemantics}) => globalThis.__jobflowCall({
        type: "JOBFLOW_APPLY_APPROVED",
        control_semantics_sha256: controlSemantics,
        fields,
        files: [{
          client_ref: refs[0], purpose: "resume", filename: "resume-approved.docx",
          sha256: fileHash, download_url: "http://127.0.0.1/assist/synthetic/file/resume"
        }],
        navigation: null,
        final_submit_client_refs: [refs[5]]
      }),
      {
        refs: lwcCollected.payload.client_refs,
        fields: lwcFields,
        fileHash,
        controlSemantics: lwcCollected.payload.control_semantics_sha256
      }
    );
    assert.equal(lwcApplied.status, "APPLIED", JSON.stringify(lwcApplied));
    assert.equal(lwcApplied.field_bindings.length, 6);
    assert.equal(lwcApplied.material_bindings.length, 1);
    assert.equal(lwcApplied.final_submit_armed, true);
    const lwcState = await lwcPage.evaluate(() => ({
      resumeInputGone: !document.querySelector("#resume-lwc"),
      resumeSuccess: document.querySelector("#resume-zone")?.innerText,
      parserComplete: document.querySelector("#first-name")?.dataset.resumeParserComplete,
      first: document.querySelector("#first-name")?.value,
      last: document.querySelector("#last-name")?.value,
      authorized: document.querySelector("#auth-yes")?.checked,
      onsite: document.querySelector("#onsite")?.checked,
      references: globalThis.__jobflowClosedRoots.get(document.querySelector("#reference-group"))
        ?.querySelector("#reference-yes")?.checked,
      finalClickMessages: globalThis.__jobflowMessages.filter((item) => item.type === "JOBFLOW_USER_SUBMIT_OBSERVED").length
    }));
    assert.equal(lwcState.resumeInputGone, true);
    assert.match(lwcState.resumeSuccess, /resume-approved\.docx/);
    assert.equal(lwcState.parserComplete, "true");
    assert.deepEqual([lwcState.first, lwcState.last], ["Jordan", "Lee"]);
    assert.equal(lwcState.authorized, true);
    assert.equal(lwcState.onsite, true);
    assert.equal(lwcState.references, true);
    assert.equal(lwcState.finalClickMessages, 0);
    assert.equal(await lwcPage.locator("#phone-type").evaluate(
      (host) => host.shadowRoot.querySelector("lightning-button-menu > button").getAttribute("aria-expanded")
    ), "false");
    const lwcAfterUpload = await lwcPage.evaluate(() => globalThis.__jobflowCall({type: "JOBFLOW_COLLECT_FORM"}));
    assert.equal(lwcAfterUpload.status, "COLLECTED");
    assert.equal(lwcAfterUpload.payload.client_refs.length, 7, lwcAfterUpload.payload.sanitized_html);
    assert.match(lwcAfterUpload.payload.sanitized_html, /authorized to work[\s\S]*two professional references/);
    assert.match(lwcAfterUpload.payload.sanitized_html, /SUBMIT/);

    const choiceFailure = await lwcPage.evaluate(
      ({clientRef, value, hash, controlSemantics}) => globalThis.__jobflowCall({
        type: "JOBFLOW_APPLY_APPROVED",
        control_semantics_sha256: controlSemantics,
        fields: [{client_ref: clientRef, value, value_sha256: hash}],
        files: [], navigation: null, final_submit_client_refs: []
      }),
      {
        clientRef: lwcAfterUpload.payload.client_refs[2],
        value: "Not an offered choice",
        hash: valueHash("Not an offered choice"),
        controlSemantics: lwcAfterUpload.payload.control_semantics_sha256
      }
    );
    assert.equal(choiceFailure.status, "BLOCKED", JSON.stringify(choiceFailure));
    assert.equal(choiceFailure.code, "COMPANION_CHOICE_OPTION_NOT_FOUND");
    assert.equal(choiceFailure.client_ref, lwcAfterUpload.payload.client_refs[2]);

    const partialPage = await browser.newPage();
    await partialPage.route("https://apply.partial.example/**", (route) => route.fulfill({
      status: 200, contentType: "text/html; charset=utf-8", body: lwcFixture
    }));
    await partialPage.goto("https://apply.partial.example/v1/s/", {waitUntil: "domcontentloaded"});
    await partialPage.evaluate(({encodedFile}) => {
      globalThis.__jobflowListener = null;
      const decode = () => Uint8Array.from(atob(encodedFile), (character) => character.charCodeAt(0));
      globalThis.chrome = {
        dom: {openOrClosedShadowRoot(element) {
          return globalThis.__jobflowClosedRoots?.get(element) || element.shadowRoot || null;
        }},
        runtime: {
          lastError: null,
          onMessage: {addListener(listener) { globalThis.__jobflowListener = listener; }},
          sendMessage() { return Promise.resolve({status: "RECORDED"}); },
          connect() {
            const messageListeners = [];
            return {
              onMessage: {addListener(listener) { messageListeners.push(listener); }},
              onDisconnect: {addListener() {}},
              postMessage() {
                const bytes = decode();
                let binary = "";
                for (const value of bytes) binary += String.fromCharCode(value);
                queueMicrotask(() => {
                  for (const listener of messageListeners) listener({type: "chunk", data: btoa(binary)});
                  for (const listener of messageListeners) listener({type: "end"});
                });
              },
              disconnect() {}
            };
          }
        }
      };
      document.querySelector("#resume-lwc").addEventListener("change", () => {
        setTimeout(() => document.querySelector("#last-name")?.remove(), 140);
      });
    }, {encodedFile: fileBytes.toString("base64")});
    await partialPage.addScriptTag({path: companion});
    await partialPage.evaluate(() => {
      globalThis.__jobflowCall = (message) => new Promise((resolve) => globalThis.__jobflowListener(message, {}, resolve));
    });
    const partialCollected = await partialPage.evaluate(() => globalThis.__jobflowCall({type: "JOBFLOW_COLLECT_FORM"}));
    const partialValue = "Lee";
    const partialApplied = await partialPage.evaluate(
      ({refs, value, hash, fileHash, controlSemantics}) => globalThis.__jobflowCall({
        type: "JOBFLOW_APPLY_APPROVED",
        control_semantics_sha256: controlSemantics,
        fields: [{client_ref: refs[2], value, value_sha256: hash}],
        files: [{
          client_ref: refs[0], purpose: "resume", filename: "resume-approved.docx",
          sha256: fileHash, download_url: "http://127.0.0.1/assist/synthetic/file/resume"
        }],
        navigation: null,
        final_submit_client_refs: [refs[5]]
      }),
      {
        refs: partialCollected.payload.client_refs,
        value: partialValue,
        hash: valueHash(partialValue),
        fileHash,
        controlSemantics: partialCollected.payload.control_semantics_sha256
      }
    );
    assert.equal(partialApplied.status, "BLOCKED", JSON.stringify(partialApplied));
    assert.equal(partialApplied.code, "COMPANION_CONTROL_REBIND_FAILED");
    assert.equal(partialApplied.partial_effects, true);
    assert.equal(partialApplied.attempted_material_bindings.length, 1);
    assert.equal(partialApplied.material_bindings.length, 1);
    assert.equal(partialApplied.attempted_field_bindings.length, 0);
    await partialPage.close();

    const dynamicMaxLengthFailClosed = await verifyDynamicMaxLengthFailClosed(browser);
    const approvalSemanticsAndAtomicPreflight = await verifyApprovalSemanticsAndAtomicPreflight(browser);
    const protectedAccountAndCredentialGates = await verifyProtectedAccountAndCredentialGates(browser);

    process.stdout.write(JSON.stringify({
      status: "PASS",
      controls: collected.payload.client_refs.length,
      guided_job_capture: true,
      fields: fieldApplied.field_bindings.length,
      files: fileApplied.material_bindings.length,
      programmatic_submit_events_before_user_click: 0,
      trusted_user_submit_observed: true,
      lever_provider_runtime: true,
      lever_fields: leverApplied.field_bindings.length,
      lever_files: leverApplied.material_bindings.length,
      lever_explicit_nonfinal_navigation: true,
      lever_navigation_fields: leverNavigationRuntime.fields,
      lever_navigation_files: leverNavigationRuntime.files,
      lever_programmatic_final_submit_events: 0,
      workday_provider_file_runtime: true,
      workday_fields: workdayApplied.field_bindings.length,
      workday_files: workdayApplied.material_bindings.length,
      workday_programmatic_final_submit_events: 0,
      ashby_provider_runtime: true,
      ashby_fields: ashbyRuntime.fields,
      ashby_files: ashbyRuntime.files,
      ashby_programmatic_final_submit_events: 0,
      smartrecruiters_provider_runtime: true,
      smartrecruiters_fields: smartRecruitersRuntime.fields,
      smartrecruiters_files: smartRecruitersRuntime.files,
      smartrecruiters_programmatic_final_submit_events: 0,
      greenhouse_component_rerender_rebinding: greenhouseRebinding,
      lever_component_rerender_rebinding: leverRebinding,
      workday_component_rerender_rebinding: workdayRebinding,
      ashby_component_rerender_rebinding: ashbyRebinding,
      smartrecruiters_component_rerender_rebinding: smartRecruitersRebinding,
      submit_like_next_programmatic_clicks: 0,
      trusted_manual_next_observed_before_unload: true,
      scoped_explicit_button_navigation: true,
      lwc_choice_controls: true,
      non_input_aria_combobox: true,
      component_rerender_rebinding: true,
      shadow_root_mutation_settling: true,
      async_upload_replacement: true,
      partial_apply_evidence: true,
      dynamic_maxlength_fail_closed: dynamicMaxLengthFailClosed,
      approval_semantics_and_atomic_preflight: approvalSemanticsAndAtomicPreflight,
      protected_account_and_credential_gates: protectedAccountAndCredentialGates,
      programmatic_final_submit_events: 0
    }));
  } finally {
    await browser.close();
  }
})().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
