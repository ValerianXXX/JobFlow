"use strict";
const assert=require("node:assert/strict");
const {spawn}=require("node:child_process");
const {mkdirSync,writeFileSync}=require("node:fs");
const path=require("node:path");
const {chromium}=require("playwright");
const root=path.resolve(__dirname,"..");

(async()=>{
  const python=process.env.JOBFLOW_TEST_PYTHON||path.join(root,".venv","Scripts","python.exe");
  const child=spawn(python,[path.join(__dirname,"claim_wording_ui_server.py"),"--stdio-control"],
    {cwd:root,windowsHide:true,stdio:["pipe","pipe","pipe"]});
  let output="",stderr="",browser,page;
  const exited=new Promise(resolve=>child.once("exit",resolve));
  child.stdout.on("data",data=>{output+=data;});child.stderr.on("data",data=>{stderr+=data;});
  const artifacts=path.join(root,"reports","claim-wording-ui");mkdirSync(artifacts,{recursive:true});
  const errors=[],external=[];
  try{
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
    await page.route("**/*",route=>{if(route.request().url().startsWith(url))return route.continue();
      external.push(route.request().url());return route.abort();});
    const headers={"X-JobOps-Session":"synthetic-claim-wording-session"};
    const bootstrap=async()=>{
      const response=await page.request.get(url+"api/bootstrap",{headers});
      assert.equal(response.status(),200);return response.json();
    };
    const action=async(selector,route)=>{
      const refreshed=page.waitForResponse(response=>response.url().endsWith("/api/bootstrap"));
      const pending=page.waitForResponse(response=>response.url().endsWith("/api/"+route));
      await page.locator(selector).click();const result=await pending;
      assert.equal(result.status(),200,await result.text());
      await refreshed;await page.waitForFunction(()=>state.activities.length===0);
      return result.json();
    };
    const row=id=>page.locator(`[data-claim-row="${id}"]`);
    await page.goto(url);
    await page.locator('.step[data-target="review"]').click();
    await row("CLM-SYNTHETIC-3").waitFor();
    assert.equal(await page.locator("#profileReview").isChecked(),true);
    assert.match(await row("CLM-SYNTHETIC-3").innerText(),/原始 AI 候选/);
    await row("CLM-SYNTHETIC-3").locator(".claim-statement").fill("The applicant revised this synthetic evidence statement for a new review.");
    assert.equal(await page.locator("#profileReview").isChecked(),false);
    assert.equal(await row("CLM-SYNTHETIC-3").locator(".claim-decision").inputValue(),"PENDING");
    assert.match(await row("CLM-SYNTHETIC-3").innerText(),/用户改写/);
    await action("#saveReview","review");
    let saved=await bootstrap();
    assert.equal(saved.claims.find(c=>c.claim_id==="CLM-SYNTHETIC-3").wording_provenance.origin,"USER_EDITED");
    assert.equal(saved.profile_review,"PENDING");
    assert.equal(saved.external_claim_approval.current,false);
    await page.reload();await page.locator('.step[data-target="review"]').click();
    await row("CLM-SYNTHETIC-3").waitFor();
    assert.match(await row("CLM-SYNTHETIC-3").innerText(),/用户改写/);
    await row("CLM-SYNTHETIC-1").locator(".claim-merge-select").check();
    await row("CLM-SYNTHETIC-2").locator(".claim-merge-select").check();
    page.once("dialog",dialog=>dialog.accept("The applicant combined the synthetic project records and team contribution for review."));
    const merged=await action("#mergeClaims","claim-transform");const mergedId=merged.created_claim_ids[0];
    await row(mergedId).waitFor();assert.match(await row(mergedId).innerText(),/用户合并/);
    await row(mergedId).locator(".toggle-split").click();
    await row(mergedId).locator(".split-statements").fill("The applicant reviewed the synthetic project records with the team.\nThe applicant recorded the synthetic team contribution for later review.");
    const split=await action(`[data-claim-row="${mergedId}"] .apply-split`,"claim-transform");
    const children=split.created_claim_ids;
    await row(children[0]).waitFor();assert.match(await row(children[0]).innerText(),/用户拆分/);
    await page.locator('[data-batch="CONFIRMED"]').first().click();await page.locator("#profileReview").check();
    await action("#saveReview","review");
    saved=await bootstrap();assert.ok(children.every(id=>saved.claims.find(c=>c.claim_id===id).decision==="CONFIRMED"));
    // Restore an ancestor, change it, and save. The children retain their words but need a fresh review.
    await page.locator('.step[data-target="review"]').click();
    await row("CLM-SYNTHETIC-1").locator(".toggle-delete").click();
    assert.equal(await page.locator("#profileReview").isChecked(),false);
    assert.equal(await row(children[0]).locator(".claim-decision").inputValue(),"PENDING");
    await row("CLM-SYNTHETIC-1").locator(".claim-statement").fill("The applicant corrected the synthetic project record after reviewing the original.");
    await action("#saveReview","review");
    saved=await bootstrap();assert.ok(children.every(id=>{
      const item=saved.claims.find(c=>c.claim_id===id);return item.decision==="PENDING"&&item.source_review_required;
    }));
    await page.locator('[data-locale="en"]').click();
    assert.match(await row(children[0]).innerText(),/User split; no new AI analysis/);
    assert.match(await row(children[0]).innerText(),/Upstream content changed/);
    await row(children[0]).locator(".claim-decision").selectOption("CONFIRMED");
    await action("#saveReview","review");
    saved=await bootstrap();
    assert.equal(saved.claims.find(c=>c.claim_id===children[0]).source_review_required,false);
    assert.equal(saved.claims.find(c=>c.claim_id===children[1]).decision,"PENDING");
    await row(children[1]).scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(artifacts,"desktop-en.png")});
    await page.setViewportSize({width:390,height:844});
    await page.locator('[data-locale="zh"]').click();
    await row(children[1]).scrollIntoViewIfNeeded();
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
    assert.match(await row(children[1]).innerText(),/上游内容已改变/);
    const widths=await row(children[1]).evaluate(element=>({row:element.clientWidth,text:element.querySelector(".claim-statement").clientWidth}));
    assert.ok(widths.text/widths.row>0.8,"Mobile Claim wording must use the card width");
    await page.screenshot({path:path.join(artifacts,"mobile-zh.png")});
    await row(children[1]).screenshot({path:path.join(artifacts,"mobile-zh-claim.png")});
    await page.reload();await page.locator('.step[data-target="review"]').click();
    await row(children[1]).waitFor();
    assert.equal(await row(children[1]).locator(".claim-decision").inputValue(),"PENDING");
    saved=await bootstrap();assert.equal(saved.external_claim_approval.current,false);
    assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
    writeFileSync(path.join(artifacts,"result.json"),JSON.stringify({status:"PASS",synthetic_only:true,
      actual_loopback_http:true,actual_windows_dpapi:true,live_provider_calls:0,
      original_ai_words:"SEEDED_FIXTURE_NOT_MODEL_OBSERVATION",edit_merge_split:true,
      ancestor_review_invalidation:true,subsequent_individual_review:true,reload_preserves:true,
      external_approval_granted:false,external_requests:external,browser_errors:errors,
      locales:["zh","en"],viewport_widths:[1280,390],independent_human_acceptance:false},null,2)+"\n");
    console.log("Claim wording UI: PASS (actual HTTP/DPAPI; synthetic edit, merge, split, ancestor review, reload, bilingual)");
  }catch(error){if(page)await page.screenshot({path:path.join(artifacts,"failure.png")}).catch(()=>{});throw error;
  }finally{
    if(browser)await browser.close();if(child.exitCode===null)child.stdin.end("STOP\n");
    const code=await exited;
    if(code!==0||!output.includes('"temporary_state_removed": true'))throw new Error(`Synthetic cleanup not confirmed (${code}): ${stderr}`);
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
