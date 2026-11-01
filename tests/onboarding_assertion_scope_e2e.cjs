"use strict";

const assert = require("node:assert/strict");
const path = require("node:path");
const {pathToFileURL} = require("node:url");
const {chromium} = require("playwright");

(async () => {
  const browser = await chromium.launch({channel: "msedge", headless: true});
  const page = await browser.newPage({viewport: {width: 1280, height: 900}});
  const errors = [], requests = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/*", route => {
    if (route.request().url().startsWith("file:")) return route.continue();
    requests.push(route.request().url());
    return route.abort();
  });
  try {
    await page.goto(pathToFileURL(path.join(__dirname, "../src/jobops/ui/index.html")).href);
    await page.waitForFunction(() => state.activities.length === 0);
    await page.evaluate(() => {
      state.data = {status: "IN_PROGRESS", pending_sources: [{
        source_id: "SRC-SYNTHETIC-ASSERTION-SCOPE",
        metadata: {safe_display_name: "Synthetic scope review", extension: ".docx"},
        extraction_summary: {
          analysis_mode: "AI_CORE_ENTITY_ANALYSIS", quality_contract: AI_QUALITY_CONTRACT,
          ai_chunks: 1, ai_input_characters: 180, ai_covered_characters: 180,
          ai_input_truncated: false, raw_lines: 5, reconstructed_blocks: 1,
          filtered_candidate_count: 3,
          filtered_candidate_reasons: {SOURCE_ASSERTION_CONFLICT: 2, UNSUPPORTED_ACTOR: 1},
        },
        candidates: [{candidate_id: "SYNTHETIC-C1", statement: "The applicant proposed a deployment of the synthetic service.",
          category: "project", selected: false, provenance: {line_start: 3, line_end: 3}}],
      }]};
      state.locale = "en";
      renderSourcePreviews();
    });
    const preview = page.locator(".source-preview");
    assert.equal(await preview.locator(".preview-actor-notice").count(), 1);
    assert.match(await preview.locator(".preview-actor-notice").textContent(), /Skipped 1 candidate.*does not establish the stated performer/);
    assert.match(await preview.locator(".preview-assertion-notice").textContent(), /Skipped 2 candidate.*capability status/);
    assert.equal(await preview.locator(".preview-select").isChecked(), false);
    assert.match(await preview.textContent(), /Confirm and securely keep source/);
    await page.setViewportSize({width: 390, height: 844});
    await page.evaluate(() => { state.locale = "zh"; renderSourcePreviews(); });
    assert.match(await preview.locator(".preview-actor-notice").textContent(), /有 1 条候选无法从所引原文确认执行者/);
    assert.match(await preview.locator(".preview-assertion-notice").textContent(), /有 2 条候选.*能力状态/);
    assert.equal(await preview.locator(".preview-select").isChecked(), false);
    const width = await page.evaluate(() => ({viewport: innerWidth, body: document.documentElement.scrollWidth}));
    assert.ok(width.body <= width.viewport, JSON.stringify(width));
    await page.evaluate(() => {
      state.data.pending_sources[0].extraction_summary.filtered_candidate_reasons = {};
      renderSourcePreviews();
    });
    assert.equal(await preview.locator(".preview-actor-notice").count(), 0);
    assert.equal(await preview.locator(".preview-assertion-notice").count(), 0);
    assert.deepEqual(errors, []);
    assert.deepEqual(requests, []);
    process.stdout.write(JSON.stringify({status: "PASS", locales: ["en", "zh"], widths: [1280, 390],
      protocol_reason_counts_visible: true, candidates_unselected: true,
      source_confirmation_available: true, external_requests: 0}));
  } finally { await browser.close(); }
})().catch(error => { process.stderr.write(`${error.stack || error}\n`); process.exit(1); });
