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
    await page.goto(pathToFileURL(path.join(__dirname, "..", "src/jobops/ui/index.html")).href);
    await page.waitForFunction(() => state.activities.length === 0);
    await page.evaluate(() => {
      state.data = {status: "IN_PROGRESS", pending_sources: [{
        source_id: "SRC-SYNTHETIC-COVERAGE",
        metadata: {safe_display_name: "Synthetic source review", extension: ".txt"},
        extraction_summary: {
          analysis_mode: "AI_CORE_ENTITY_ANALYSIS", quality_contract: AI_QUALITY_CONTRACT,
          ai_chunks: 1, ai_input_characters: 90, ai_covered_characters: 90, ai_input_truncated: false,
          raw_lines: 4, reconstructed_blocks: 1,
          source_reference_coverage: {
            schema_version: 1, status: "LINE_REFERENCES_ONLY", nonempty_source_lines: 4,
            cited_source_lines: 1, uncited_source_lines: 3,
            uncited_line_ranges: [{line_start: 1, line_end: 2}, {line_start: 4, line_end: 4}],
            fact_completeness_verified: false, semantic_recall: null,
          },
        },
        candidates: [{candidate_id: "SYNTHETIC-C1", statement: "Prepared a synthetic report.",
          category: "summary", selected: false, provenance: {line_start: 3, line_end: 3}}],
      }]};
      state.locale = "en";
      renderSourcePreviews();
    });
    const preview = page.locator(".source-preview");
    assert.match(await preview.textContent(), /Input analysis returned/);
    assert.match(await preview.textContent(), /Selected text sent to AI/);
    assert.doesNotMatch(await preview.textContent(), /100%|All facts extracted/);
    assert.match(await preview.locator(".source-reference-coverage").textContent(), /cite 1\/4 lines; 3 lines are not cited/);
    assert.equal(await preview.locator(".preview-select").isChecked(), false);
    const details = preview.locator(".source-reference-coverage details");
    await details.locator("summary").focus();
    await page.keyboard.press("Enter");
    assert.equal(await details.getAttribute("open"), "");
    assert.match(await details.textContent(), /1–2, 4/);
    assert.match(await details.textContent(), /do not verify factual completeness or accuracy/);
    assert.equal(await preview.locator(".preview-select").isChecked(), false);

    await page.setViewportSize({width: 390, height: 844});
    await page.evaluate(() => { state.locale = "zh"; renderSourcePreviews(); });
    assert.match(await preview.textContent(), /输入分析已返回/);
    assert.match(await preview.textContent(), /1\/4 行；3 行尚未引用/);
    await preview.locator(".source-reference-coverage summary").click();
    assert.match(await details.textContent(), /行引用不代表事实完整或正确/);
    assert.equal(await preview.locator(".preview-select").isChecked(), false);
    const width = await page.evaluate(() => ({viewport: innerWidth, body: document.documentElement.scrollWidth}));
    assert.ok(width.body <= width.viewport, JSON.stringify(width));

    const result = await page.evaluate(() => {
      state.locale = "en";
      const summary = state.data.pending_sources[0].extraction_summary;
      const coverage = summary.source_reference_coverage;
      coverage.cited_source_lines = 4; coverage.uncited_source_lines = 0; coverage.uncited_line_ranges = [];
      renderSourcePreviews();
      const fullCitation = document.querySelector(".source-reference-coverage").textContent;
      const selectedWithAllLinesCited = document.querySelector(".preview-select").checked;
      const fallback = [];
      for (const value of [undefined, {status: "COORDINATES_UNAVAILABLE"},
        {...coverage, fact_completeness_verified: true}, {...coverage, cited_source_lines: -1},
        {...coverage, cited_source_lines: 3}, {...coverage, nonempty_source_lines: "4"}]) {
        summary.source_reference_coverage = value;
        renderSourcePreviews();
        fallback.push(document.querySelector(".source-reference-coverage").textContent);
      }
      summary.source_reference_coverage = {...coverage,
        uncited_line_ranges: [{line_start: "<img src=x onerror=alert(1)>", line_end: 10}],
        range_list_truncated: true};
      renderSourcePreviews();
      return {fullCitation, selectedWithAllLinesCited, fallback,
        injectedImages: document.querySelectorAll(".source-reference-coverage img").length,
        boundedNote: document.querySelector(".source-reference-coverage").textContent,
        metadataCompleteness: coverage.fact_completeness_verified};
    });
    assert.match(result.fullCitation, /cite 4\/4 lines; 0 lines are not cited/);
    assert.match(result.fullCitation, /do not verify factual completeness or accuracy/);
    assert.equal(result.selectedWithAllLinesCited, false);
    assert.equal(result.metadataCompleteness, false);
    for (const text of result.fallback) assert.match(text, /no reliable line-reference record/);
    assert.equal(result.injectedImages, 0);
    assert.match(result.boundedNote, /first 40 ranges/);
    assert.deepEqual(errors, []);
    assert.deepEqual(requests, []);
    process.stdout.write(JSON.stringify({status: "PASS", scenario_groups: 7,
      locales: ["en", "zh"], viewport_widths: [1280, 390], keyboard_details: true,
      candidate_selection_unchanged: true, no_fact_completeness_claim: true,
      legacy_and_invalid_metadata_fallback: true, external_requests: 0}));
  } finally {
    await browser.close();
  }
})().catch(error => { process.stderr.write(`${error.stack || error}\n`); process.exit(1); });
