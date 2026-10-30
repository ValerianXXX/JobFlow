"use strict";
const assert = require("node:assert/strict");
const {spawn} = require("node:child_process");
const {mkdirSync,writeFileSync} = require("node:fs");
const path = require("node:path");
const {chromium} = require("playwright");
const root = path.resolve(__dirname,"..");

(async()=>{
  const python = process.env.JOBFLOW_TEST_PYTHON || path.join(root,".venv","Scripts","python.exe");
  const child = spawn(python,[path.join(__dirname,"source_context_ui_server.py"),"--stdio-control"],
    {cwd:root,windowsHide:true,stdio:["pipe","pipe","pipe"]});
  let output="",stderr="",browser,page;
  const exited = new Promise(resolve=>child.once("exit",resolve));
  child.stdout.on("data",data=>{output+=data;});
  child.stderr.on("data",data=>{stderr+=data;});
  const artifacts=path.join(root,"reports","context-review-ui");mkdirSync(artifacts,{recursive:true});
  const errors=[],external=[];
  try {
    const url=await new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>reject(new Error("Fixture startup timed out")),120000);
      child.once("error",error=>{clearTimeout(timer);reject(error);});
      child.once("exit",code=>{clearTimeout(timer);reject(new Error(`Fixture exit ${code}: ${stderr}`));});
      child.stdout.on("data",()=>{const line=output.split(/\r?\n/).find(item=>item.startsWith('{"url"'));
        if(line){clearTimeout(timer);resolve(JSON.parse(line).url);}});
    });
    browser=await chromium.launch({channel:"msedge",headless:true});
    page=await browser.newPage({viewport:{width:1280,height:900},reducedMotion:"reduce"});
    page.on("pageerror",error=>errors.push(error.message));
    await page.route("**/*",route=>{
      if(route.request().url().startsWith(url))return route.continue();
      external.push(route.request().url());return route.abort();
    });
    const headers={"X-JobOps-Session":"synthetic-source-context-session"};
    const bootstrap=async()=>{
      const response=await page.request.get(url+"api/bootstrap",{headers});
      assert.equal(response.status(),200);return response.json();
    };
    const action=async(selector,route)=>{
      const pending=page.waitForResponse(response=>response.url().endsWith("/api/"+route));
      await page.locator(selector).click();const result=await pending;
      assert.equal(result.status(),200,await result.text());return result.json();
    };
    await page.goto(url);
    await page.locator(".commit-preview").waitFor();
    assert.equal(await page.locator("#sourcePreviewList .context-decision-panel").count(),0);
    const asset=await page.request.get(url+"context-review.js");
    assert.equal(asset.status(),200);assert.match(asset.headers()["content-type"],/javascript/);
    await action(".commit-preview","commit-source");
    await page.locator("#sourceList .context-decision-panel").first().waitFor();
    const before=await bootstrap();
    const card=index=>page.locator("#sourceList [data-context-note]").nth(index);
    const status=async(index,value)=>{
      await card(index).locator(`[data-review-status="${value}"]`).waitFor();
    };
    const decision=async(index,choice,reason="")=>{
      const row=card(index);await row.locator("summary").click();
      const english=await page.locator("html").getAttribute("lang")==="en";
      await row.getByLabel(english?"Choose a decision":"选择核对决定",{exact:true}).selectOption(choice);
      assert.equal(await row.locator(".context-save").isDisabled(),true);
      if(reason)await row.locator(".context-reason").selectOption(reason);
      await row.locator(".context-confirm").check();
      const result=await action(`#sourceList [data-context-note]:nth-child(${index+1}) .context-save`,"source-context-review");
      assert.equal(result.answers_changed,false);assert.equal(result.approved_for_external,false);
      return result;
    };
    await decision(1,"PROFILE_REVIEWED");await status(1,"PROFILE_UNKNOWN_REVIEWED");
    const reviewed=await bootstrap();
    for(const key of ["answers","claims","profile_review","external_claim_approval"])assert.deepEqual(reviewed[key],before[key],key);
    await card(0).locator(".source-context-field").click();
    await page.locator("#answer-remote_preference").selectOption("remote");
    // Locale switching preserves drafts in data.answers; the review must still
    // display the real saved snapshot and leave the draft intact.
    await page.locator('[data-locale="en"]').click();
    await page.locator('[data-target="sources"]').first().click();
    assert.match(await card(0).locator(".context-saved-answer").textContent(),/Saved Profile answer: Unanswered/);
    await decision(0,"PROFILE_REVIEWED");await status(0,"PROFILE_UNKNOWN_REVIEWED");
    await card(0).locator(".source-context-field").click();
    assert.equal(await page.locator("#answer-remote_preference").inputValue(),"remote");
    await action("#saveAnswers","save");
    await page.locator('[data-target="sources"]').first().click();
    await status(0,"ANSWER_CHANGED");
    await decision(0,"PROFILE_REVIEWED");await status(0,"PROFILE_REVIEWED");
    await card(2).locator("summary").click();
    assert.equal(await card(2).locator('option[value="PROFILE_REVIEWED"]').count(),0);
    await card(2).locator(".context-decision").selectOption("FOLLOW_UP");
    await card(2).locator(".context-confirm").check();
    assert.equal(await card(2).locator(".context-save").isDisabled(),true);
    await card(2).locator(".context-reason").selectOption("NEEDS_EVIDENCE");
    await action("#sourceList [data-context-note]:nth-child(3) .context-save","source-context-review");
    await status(2,"FOLLOW_UP");
    await decision(2,"REOPEN");await status(2,"PENDING");
    const saved=await bootstrap();
    assert.equal(saved.context_review.event_count,5);
    const denied=await page.request.post(new URL(url).origin+"/api/source-context-review",{data:{}});
    assert.equal(denied.status(),403);
    const origin=await page.request.post(url+"api/source-context-review",{headers:{...headers,Origin:"https://synthetic.invalid"},data:{}});
    assert.equal(origin.status(),403);
    assert.deepEqual((await bootstrap()).context_review,saved.context_review);
    await action(".reprocess-source","reprocess-source");
    await page.locator(".discard-preview").waitFor();
    assert.equal(await card(0).locator(".context-decision").isDisabled(),true);
    assert.equal(await page.locator("#sourcePreviewList .context-decision-panel").count(),0);
    await action(".discard-preview","discard-source");
    await status(0,"PROFILE_REVIEWED");
    await page.reload();await status(0,"PROFILE_REVIEWED");await status(1,"PROFILE_UNKNOWN_REVIEWED");
    assert.deepEqual((await bootstrap()).context_review,saved.context_review);
    await action('[data-locale="zh"]',"save");
    await page.locator("#activityIndicator").waitFor({state:"hidden"});
    await card(0).locator("summary").click();
    await card(0).locator("summary").scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(artifacts,"desktop-zh.png")});
    await page.setViewportSize({width:390,height:844});
    await action('[data-locale="en"]',"save");
    await page.locator("#activityIndicator").waitFor({state:"hidden"});
    await card(1).locator("summary").click();
    await card(1).locator(".context-saved-answer").scrollIntoViewIfNeeded();
    const widths=await page.evaluate(()=>({body:document.documentElement.scrollWidth,viewport:innerWidth}));
    assert.ok(widths.body<=widths.viewport,JSON.stringify(widths));
    assert.match(await card(1).innerText(),/Reviewed; answer still unknown/);
    await page.screenshot({path:path.join(artifacts,"mobile-en.png")});
    // Capture the whole review card below the sticky header without changing
    // page content or concealing any controls in a screenshot-only DOM edit.
    await page.locator("body").press("Control+Home");
    await page.waitForFunction(()=>scrollY===0);
    const cardBox=await card(1).boundingBox();
    assert.ok(cardBox && cardBox.y>100);
    await page.screenshot({fullPage:true,clip:cardBox,path:path.join(artifacts,"mobile-en-review-card.png")});
    assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
    const final=await bootstrap();
    assert.equal(final.claims.length,0);assert.equal(final.external_claim_approval.current,false);
    writeFileSync(path.join(artifacts,"result.json"),JSON.stringify({status:"PASS",synthetic_only:true,
      actual_loopback_http:true,actual_windows_dpapi:true,live_provider_calls:0,source_analysis:"FIXED_SYNTHETIC_RESPONSE",
      decisions_persisted:5,unknown_remains_unknown:true,unsaved_profile_draft_preserved:true,
      locale_switch_uses_saved_answer:true,changed_answer_invalidates:true,reason_required:true,
      reanalysis_blocks_review:true,discard_restores_review:true,reload_preserves:true,
      session_origin_checks:true,external_requests:external,browser_errors:errors,
      locales:["zh","en"],viewport_widths:[1280,390],independent_human_acceptance:false},null,2)+"\n");
    console.log("Context review UI: PASS (actual HTTP/DPAPI, synthetic source, saved-answer binding, drafts, reasons, reanalysis, reload, bilingual)");
  } catch(error) {
    if(page)await page.screenshot({path:path.join(artifacts,"failure.png")}).catch(()=>{});
    throw error;
  } finally {
    if(browser)await browser.close();
    if(child.exitCode===null)child.stdin.end("STOP\n");
    const code=await exited;
    if(code!==0||!output.includes('"temporary_state_removed": true'))throw new Error(`Synthetic cleanup not confirmed (${code}): ${stderr}`);
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
