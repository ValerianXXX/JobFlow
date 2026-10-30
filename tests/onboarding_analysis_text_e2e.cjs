"use strict";
const assert = require("node:assert/strict");
const path = require("node:path");
const {pathToFileURL} = require("node:url");
const {chromium} = require("playwright");

(async () => {
  const browser = await chromium.launch({channel:"msedge", headless:true});
  const page = await browser.newPage({viewport:{width:1280, height:900}});
  const errors = [], external = [];
  page.on("pageerror", e => errors.push(e.message));
  await page.route("**/*", route => {
    if (route.request().url().startsWith("file:")) return route.continue();
    external.push(route.request().url()); return route.abort();
  });
  try {
    await page.goto(pathToFileURL(path.join(__dirname, "../src/jobops/ui/index.html")).href);
    await page.waitForFunction(() => state.activities.length === 0 && window.JobFlowAnalysisText);
    await page.evaluate(() => {
      const descriptor = {status:"AVAILABLE", fact_completeness_verified:false, source_sha256:"sha256:"+"a".repeat(64), text_sha256:"sha256:"+"b".repeat(64), total_lines:245};
      window.syntheticLines = Array.from({length:245}, (_, i) => i === 22 ? "仅远程工作，入职时间待确认。" : i === 49 ? '<img src="https://untrusted.example.test/pixel" onerror="alert(1)">' : i % 3 === 2 ? "" : `Synthetic line ${i+1}.`);
      state.locale = "en";
      state.data = {status:"IN_PROGRESS", sources:[], suggestions:[], ai_engine:{}, ai_connection:{}, catalog:{fields:[]}};
      state.data.pending_sources = [{source_id:"SRC-TRANSCRIPT", metadata:{source_id:"SRC-TRANSCRIPT", safe_display_name:"Synthetic complete source", extension:".txt", analysis_text_review:descriptor},
        extraction_summary:{analysis_mode:"AI_CORE_ENTITY_ANALYSIS", quality_contract:AI_QUALITY_CONTRACT, ai_chunks:1, ai_input_characters:4000, ai_covered_characters:4000, ai_input_truncated:false, source_context_review:{status:"PROVIDED"},
          source_reference_coverage:{status:"LINE_REFERENCES_ONLY", fact_completeness_verified:false, nonempty_source_lines:160, cited_source_lines:100, uncited_source_lines:60, uncited_line_ranges:[{line_start:103,line_end:105}]}},
        candidates:[{candidate_id:"C1", statement:"Prepared a synthetic report.", category:"summary", selected:false, provenance:{line_start:11,line_end:12}}],
        context_notes:[{kind:"profile_constraint", field_id:"remote_preference", quote:syntheticLines[22], line_start:23, line_end:23, status:"UNCONFIRMED_SOURCE_CONTEXT", approved_for_external:false}]}];
      window.readRequests = [];
      api = async (route, options) => {
        if (route !== "source-review-text") throw Error("Only a private text read is allowed");
        const request = JSON.parse(options.body); readRequests.push(request);
        if (window.failStale) throw {code:"SOURCE_TEXT_REVIEW_STALE"};
        if (window.delayRead) await new Promise(resolve => {window.finishDelayed = resolve;});
        const first = request.line_start, last = Math.min(245,first+99);
        return {source_id:"SRC-TRANSCRIPT", source_sha256:descriptor.source_sha256, text_sha256:descriptor.text_sha256, first_line:first, last_line:last, total_lines:245, next_line:last<245?last+1:null, fact_completeness_verified:false,
          lines:syntheticLines.slice(first-1,last).map((text,i)=>({number:first+i,text}))};
      };
      renderSourcePreviews();
    });
    const root = page.locator(".source-preview"), dialog = page.locator(".analysis-text-dialog");
    await root.locator(".source-reference-coverage summary").click();
    await root.locator(".analysis-gap-links button").focus();
    await page.keyboard.press("Enter");
    await page.waitForFunction(() => document.querySelectorAll(".analysis-line").length === 100);
    assert.equal(await dialog.locator(".analysis-line").first().getAttribute("data-line"), "100");
    assert.equal(await dialog.locator(".requested-line").count(), 3);
    assert.equal(await dialog.locator(".analysis-prev").isEnabled(), true);
    assert.equal(await root.locator(".preview-select").isChecked(), false);
    await dialog.locator(".analysis-next").click();
    await page.waitForFunction(() => document.querySelector(".analysis-line")?.dataset.line === "200");
    assert.equal(await dialog.locator(".analysis-line").count(), 46);
    await dialog.locator(".analysis-prev").click();
    await page.waitForFunction(() => document.querySelector(".analysis-line")?.dataset.line === "100");
    await dialog.locator("input").fill("23");
    await dialog.locator(".analysis-jump button").click();
    await page.waitForFunction(() => document.querySelector(".analysis-line")?.dataset.line === "20");
    assert.match(await dialog.locator(".requested-line").textContent(), /仅远程工作/);
    assert.equal(await dialog.locator("img").count(), 0);
    assert.match(await dialog.textContent(), /onerror/);
    await dialog.locator(".analysis-prev").click();
    await page.waitForFunction(() => document.querySelector(".analysis-line")?.dataset.line === "1");
    assert.ok(await dialog.getByLabel("Blank line", {exact:true}).count() > 0);
    await page.keyboard.press("Escape");
    await page.waitForFunction(() => document.querySelector(".analysis-text-dialog").childElementCount === 0);
    assert.equal(await root.locator(".preview-select").isChecked(), false);

    await root.locator(".preview-meta .analysis-text-open").click();
    await page.waitForFunction(() => document.querySelectorAll(".analysis-line").length === 100);
    assert.equal(await dialog.locator(".requested-line").count(), 2);
    assert.equal(await root.locator(".preview-select").isChecked(), false);
    await dialog.locator(".analysis-close").click();
    await page.waitForFunction(() => document.querySelector(".analysis-text-dialog").childElementCount === 0);
    await page.setViewportSize({width:390,height:844});
    await page.evaluate(() => {state.locale="zh"; renderSourcePreviews();});
    await root.locator(".source-context .analysis-text-open").click();
    await page.waitForFunction(() => document.querySelectorAll(".analysis-line").length === 100);
    assert.match(await dialog.textContent(), /本次分析使用的已保存文本/);
    assert.match(await dialog.locator(".requested-line").textContent(), /仅远程工作/);
    const width = await dialog.evaluate(el => ({width:el.clientWidth,scroll:el.scrollWidth,viewport:innerWidth,body:document.documentElement.scrollWidth}));
    assert.ok(width.scroll <= width.width && width.body <= width.viewport, JSON.stringify(width));
    await dialog.locator(".analysis-close").click();
    await page.waitForFunction(() => document.querySelector(".analysis-text-dialog").childElementCount === 0);

    await page.evaluate(() => {window.delayRead=true;});
    await root.locator(".analysis-text-controls button").click();
    await page.waitForFunction(() => window.finishDelayed);
    await dialog.locator(".analysis-close").click();
    await page.waitForFunction(() => document.querySelector(".analysis-text-dialog").childElementCount === 0);
    await page.evaluate(() => {window.delayRead=false; window.finishDelayed();});
    await page.waitForFunction(() => document.querySelector(".analysis-text-dialog").childElementCount === 0);
    await page.evaluate(() => {window.failStale=true; state.locale="en"; renderSourcePreviews();});
    await root.locator(".analysis-text-controls button").click();
    await page.waitForFunction(() => document.querySelector(".analysis-text-status")?.textContent.includes("analysis changed"));
    assert.equal(await dialog.locator(".analysis-line").count(), 0);
    await dialog.locator(".analysis-close").click();
    await page.waitForFunction(() => document.querySelector(".analysis-text-dialog").childElementCount === 0);
    await page.evaluate(() => {
      window.failStale=false;
      const preview=state.data.pending_sources[0];
      state.data.sources=[{...preview.metadata,raw_retained:true,category:"supporting_material",fact_count:0}];
      state.data.context_notes=preview.context_notes.map(note=>({...note,source_id:preview.source_id}));
      state.data.pending_sources=[]; renderSources();
    });
    await page.locator(".source-entry .analysis-text-controls button").click();
    await page.waitForFunction(() => document.querySelectorAll(".analysis-line").length === 100);
    assert.equal(await dialog.locator("img").count(), 0);
    assert.deepEqual(errors, []); assert.deepEqual(external, []);
    process.stdout.write(JSON.stringify({status:"PASS",scenario_groups:8,locales:["en","zh"],widths:[1280,390],keyboard:true,late_response_cleared:true,selection_unchanged:true,external_requests:0,backend:"contract double"}));
  } finally {await browser.close();}
})().catch(e => {process.stderr.write(`${e.stack||e}\n`);process.exit(1);});
