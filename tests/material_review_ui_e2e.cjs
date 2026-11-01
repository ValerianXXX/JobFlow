"use strict";
// Real loopback HTTP and PDF pages; deliberately seeded synthetic memory store.
const assert = require("node:assert/strict");
const {spawn} = require("node:child_process");
const {createHash} = require("node:crypto");
const {mkdirSync, readFileSync, writeFileSync} = require("node:fs");
const path = require("node:path");
const {chromium} = require("playwright");
const root = path.resolve(__dirname,"..");
const sha = bytes => "sha256:"+createHash("sha256").update(bytes).digest("hex");

(async()=>{
  const child=spawn(process.env.JOBFLOW_TEST_PYTHON||path.join(root,".venv","Scripts","python.exe"),
    [path.join(__dirname,"material_review_ui_server.py")],{cwd:root,windowsHide:true,stdio:["pipe","pipe","pipe"]});
  let browser,output="",stderr="";
  child.stdout.on("data",part=>{output+=part;});child.stderr.on("data",part=>{stderr+=part;});
  const exited=new Promise(resolve=>child.once("exit",resolve));
  const artifacts=path.join(root,"reports","material-review-ui");mkdirSync(artifacts,{recursive:true});
  try {
    const ready=await new Promise((resolve,reject)=>{
      const timeout=setTimeout(()=>reject(new Error("Material fixture startup timed out")),120000);
      child.once("error",error=>{clearTimeout(timeout);reject(error);});
      child.once("exit",code=>{clearTimeout(timeout);reject(new Error(`Fixture exited ${code}: ${stderr}`));});
      const check=()=>{if(output.includes("\n")){clearTimeout(timeout);resolve(JSON.parse(output.split("\n")[0]));}};
      child.stdout.on("data",check);check();
    });
    browser=await chromium.launch({channel:"msedge",headless:true});
    const page=await browser.newPage({viewport:{width:1365,height:900},reducedMotion:"reduce",acceptDownloads:true});
    page.setDefaultTimeout(30000);
    const errors=[],external=[],origin=new URL(ready.url).origin;
    page.on("pageerror",error=>errors.push(error.message));
    await page.route("**/*",route=>{
      if(route.request().url().startsWith(origin+"/"))return route.continue();
      external.push(route.request().url());return route.abort();
    });
    const open=async()=>{
      await page.locator(`.open-review-packet[data-id="${ready.application_id}"]`).first().click();
      await page.locator('.material-file-card[data-material-id="resume.pdf"] .material-preview').waitFor();
    };
    const card=id=>page.locator(`.material-file-card[data-material-id="${id}"]`);
    const dialog=page.locator(".material-review-dialog");
    await page.goto(ready.url);await open();
    assert.match(await page.locator(".application-material-review").innerText(),/检查最终申请材料/);
    assert.doesNotMatch(await page.locator("#reviewPacketBody").innerText(),/file_upload_stop|private_fixed|AI: material_upload/);
    await page.locator(".application-material-review").scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(artifacts,"desktop-files-zh.png")});
    await card("resume.pdf").locator(".material-preview").click();
    await dialog.locator("img").waitFor({state:"visible"});
    await dialog.getByRole("button",{name:"下一页",exact:true}).click();
    await page.waitForFunction(()=>document.querySelector(".material-page-status")?.textContent.includes("第 2 页，共 2 页"));
    assert.equal(await dialog.getByRole("button",{name:"下一页",exact:true}).isDisabled(),true);
    await page.screenshot({path:path.join(artifacts,"desktop-page2-zh.png")});
    await page.keyboard.press("Escape");
    assert.equal(await dialog.locator("img").count(),0);
    assert.equal(await card("resume.pdf").locator(".material-preview").evaluate(element=>element===document.activeElement),true);

    const downloads=[];
    for(const id of ["resume.pdf","resume.docx","cover_letter.pdf","cover_letter.docx"]){
      const waiting=page.waitForEvent("download");
      await card(id).locator(".material-download").click();
      const file=await waiting;const raw=readFileSync(await file.path());
      assert.equal(sha(raw),ready.materials.find(item=>item.material_id===id).sha256);
      assert.equal(file.suggestedFilename(),ready.materials.find(item=>item.material_id===id).filename);
      downloads.push({material_id:id,sha256:sha(raw)});
    }

    // A slow but genuine local response must not repopulate a closed dialog.
    let release,arrived;
    const responseArrived=new Promise(resolve=>{arrived=resolve;});
    const delayed=new Promise(resolve=>{release=resolve;});
    await page.route("**/api/application-material-page",async route=>{
      const response=await route.fetch();arrived();await delayed;
      await route.fulfill({response}).catch(()=>{});
    });
    await card("resume.pdf").locator(".material-preview").click();await responseArrived;
    await page.keyboard.press("Escape");release();
    await page.unroute("**/api/application-material-page",null,{behavior:"wait"});
    assert.equal(await dialog.locator("img").count(),0);

    await page.getByText("EN",{exact:true}).click();
    await page.waitForFunction(()=>document.querySelector(".application-material-review")?.textContent.includes("Review final application files"));
    await page.setViewportSize({width:390,height:844});
    await card("cover_letter.pdf").locator(".material-preview").click();
    await dialog.locator("img").waitFor({state:"visible"});
    await dialog.locator("select").selectOption("200");
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth),true);
    assert.equal(await dialog.evaluate(element=>element.scrollWidth<=element.clientWidth),true);
    await page.screenshot({path:path.join(artifacts,"mobile-cover-zoom-en.png")});
    await dialog.getByRole("button",{name:"Close and clear",exact:true}).click();
    await page.locator("#closeReviewPacket").click();
    assert.equal(await page.locator(".material-file-card").count(),0);
    await page.reload();await open();
    assert.equal(await dialog.evaluate(element=>element.open),false);

    // Corrupt only the requested expected hash; the actual server must reject it.
    await page.route("**/api/application-material-page",route=>{
      const data=route.request().postDataJSON();data.expected_packet_hash="sha256:"+"0".repeat(64);
      return route.continue({postData:JSON.stringify(data)});
    });
    await card("resume.pdf").locator(".material-preview").click();
    await page.waitForFunction(()=>document.querySelector(".material-page-status")?.textContent.includes("packet changed"));
    assert.equal(await dialog.locator("img:not([hidden])").count(),0);
    await page.screenshot({path:path.join(artifacts,"stale-packet-en.png")});
    await page.keyboard.press("Escape");
    assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
    writeFileSync(path.join(artifacts,"verification.json"),JSON.stringify({status:"PASS",syntheticOnly:true,
      actualHttpAndPdf:true,store:"MEMORY_CONTRACT_DOUBLE",newModelCalls:0,independentHuman:false,
      downloads,secondPage:true,escapeClearsAndRestoresFocus:true,lateResponseDiscarded:true,
      mobile200PercentZoom:true,reloadAndCloseClear:true,staleHashRejectedByRealService:true,
      externalRequests:external.length,pageErrors:errors.length},null,2)+"\n");
  } finally {
    if(browser)await browser.close();
    if(child.exitCode===null)child.stdin.end("stop\n");
    const code=await exited;
    writeFileSync(path.join(artifacts,"fixture-stdout.txt"),output);
    writeFileSync(path.join(artifacts,"fixture-stderr.txt"),stderr);
    assert.equal(code,0,stderr);
    const receipt=JSON.parse(output.trim().split("\n").at(-1));
    assert.equal(receipt.application_state_unchanged,true);
  }
  process.stdout.write("Material review UI PASS: bound files, two PDF pages, downloads, stale/late responses, bilingual desktop/mobile; synthetic memory store.\n");
})().catch(error=>{process.stderr.write(error.stack+"\n");process.exitCode=1;});
