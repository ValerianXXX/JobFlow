"use strict";
const assert=require("node:assert/strict");
const path=require("node:path");
const {pathToFileURL}=require("node:url");
const {chromium}=require("playwright");

(async()=>{
  const browser=await chromium.launch({channel:"msedge",headless:true});
  const page=await browser.newPage({viewport:{width:1280,height:900},reducedMotion:"reduce"});
  const errors=[],requests=[];
  page.on("pageerror",error=>errors.push(error.message));
  await page.route("**/*",route=>{
    if(route.request().url().startsWith("file:"))return route.continue();
    requests.push(route.request().url());return route.abort();
  });
  try{
    await page.goto(pathToFileURL(path.join(__dirname,"..","src/jobops/ui/index.html")).href);
    await page.waitForFunction(()=>state.activities.length===0);
    await page.evaluate(()=>{
      state.locale="en";
      const field={id:"remote_preference",group:"work",input_type:"text",options:[],default_policy:"reuse",
        label:{en:"Work arrangement",zh:"办公方式"},help:{en:"Synthetic Profile question",zh:"合成资料问题"}};
      state.data={status:"IN_PROGRESS",answers:{},sources:[],suggestions:[],ai_engine:{},
        catalog:{groups:[{id:"work",label:{en:"Work",zh:"工作"}}],fields:[field]},pending_sources:[{
          source_id:"SRC-SYNTHETIC",metadata:{safe_display_name:"Synthetic constraints",extension:".txt"},
          extraction_summary:{analysis_mode:"AI_CORE_ENTITY_ANALYSIS",quality_contract:AI_QUALITY_CONTRACT,
            ai_chunks:1,ai_input_characters:80,ai_covered_characters:80,ai_input_truncated:false,
            source_context_review:{status:"PROVIDED",retained:2,filtered:0}},candidates:[],
          context_notes:[{source_id:"SRC-SYNTHETIC",kind:"profile_constraint",field_id:"remote_preference",
            quote:"Remote work only. <img src=x onerror=alert(1)>",line_start:1,line_end:1,
            status:"UNCONFIRMED_SOURCE_CONTEXT",approved_for_external:false},
          {source_id:"SRC-SYNTHETIC",kind:"evidence_gap",field_id:"",quote:"References need verification.",
            line_start:2,line_end:2,status:"UNCONFIRMED_SOURCE_CONTEXT",approved_for_external:false}]}]};
      renderSources();renderQuestions();navigate("sources");
    });
    const context=page.locator("#sourcePreviewList .source-context");
    assert.equal(await context.locator("li").count(),2);
    assert.equal(await context.locator("img").count(),0);
    assert.equal(await context.locator("input,textarea,select").count(),0);
    assert.match(await context.textContent(),/do not automatically fill Profile/);
    await context.locator("button").focus();await page.keyboard.press("Enter");
    assert.equal(await page.locator("#answer-remote_preference").isVisible(),true);
    assert.equal(await page.locator("#answer-remote_preference").inputValue(),"");
    assert.equal(await page.locator("#status-remote_preference").inputValue(),"UNKNOWN");
    assert.equal(await page.evaluate(()=>document.activeElement.id),"answer-remote_preference");

    await page.setViewportSize({width:390,height:844});
    await page.evaluate(()=>{state.locale="zh";renderSources();renderQuestions();navigate("sources");});
    assert.match(await context.textContent(),/原文中的约束与待核实事项/);
    assert.match(await context.textContent(),/不会自动填写资料/);
    let width=await page.evaluate(()=>({viewport:innerWidth,body:document.documentElement.scrollWidth}));
    assert.ok(width.body<=width.viewport,JSON.stringify(width));
    await context.locator("button").click();
    assert.equal(await page.locator("#answer-remote_preference").inputValue(),"");
    assert.equal(await page.locator("#status-remote_preference").inputValue(),"UNKNOWN");

    await page.evaluate(()=>{
      const preview=state.data.pending_sources[0];
      state.data.context_notes=preview.context_notes;
      state.data.sources=[{source_id:preview.source_id,safe_display_name:"Retained synthetic source",category:"project_case",
        fact_count:0,source_context_review:preview.extraction_summary.source_context_review}];
      state.data.pending_sources=[];state.locale="en";renderSources();navigate("sources");
    });
    assert.equal(await page.locator("#sourceList .source-context li").count(),2);
    assert.equal(await page.locator("#sourcePreviewBox").isVisible(),false);
    const fallback=await page.evaluate(()=>{
      const invalid={kind:"profile_constraint",field_id:"email",quote:"Forbidden destination",line_start:1,line_end:1,
        status:"UNCONFIRMED_SOURCE_CONTEXT",approved_for_external:false};
      return {legacy:sourceContextMarkup(undefined,undefined),
        empty:sourceContextMarkup([],{status:"PROVIDED"}),
        invalid:sourceContextMarkup([invalid],{status:"PROVIDED",filtered:1})};
    });
    assert.match(fallback.legacy,/No constraint review record/);
    assert.match(fallback.empty,/does not mean the source has none/);
    assert.doesNotMatch(fallback.invalid,/Forbidden destination|data-context-field="email"/);
    assert.match(fallback.invalid,/1 model annotations failed/);
    assert.deepEqual(await page.evaluate(()=>state.data.answers),{});
    assert.deepEqual(errors,[]);assert.deepEqual(requests,[]);
    process.stdout.write(JSON.stringify({status:"PASS",locales:["en","zh"],viewport_widths:[1280,390],
      keyboard_and_collapsed_field_navigation:true,answers_unchanged:true,untrusted_html_escaped:true,
      retained_context_visible:true,external_requests:0}));
  }finally{await browser.close();}
})().catch(error=>{process.stderr.write(`${error.stack||error}\n`);process.exit(1);});
