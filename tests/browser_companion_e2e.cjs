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
      (fields) => globalThis.__jobflowCall({type: "JOBFLOW_APPLY_APPROVED", fields, files: []}),
      fields
    );
    assert.equal(fieldApplied.status, "APPLIED", JSON.stringify(fieldApplied));
    const fileApplied = await page.evaluate(
      (files) => globalThis.__jobflowCall({type: "JOBFLOW_APPLY_APPROVED", fields: [], files}),
      files
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

    process.stdout.write(JSON.stringify({
      status: "PASS",
      controls: collected.payload.client_refs.length,
      fields: fieldApplied.field_bindings.length,
      files: fileApplied.material_bindings.length,
      programmatic_submit_events_before_user_click: 0,
      trusted_user_submit_observed: true
    }));
  } finally {
    await browser.close();
  }
})().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
