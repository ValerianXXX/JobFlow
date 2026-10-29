"use strict";

// Actual upload/parser/validator/render/DPAPI; fixed synthetic model replay.
const assert = require("node:assert/strict");
const {spawn} = require("node:child_process");
const {mkdirSync, writeFileSync} = require("node:fs");
const path = require("node:path");
const {chromium} = require("playwright");
const root = path.resolve(__dirname, "..");

(async () => {
  const python = process.env.JOBFLOW_TEST_PYTHON || path.join(root,".venv","Scripts","python.exe");
  const child = spawn(python,[path.join(__dirname,"pdf_regions_ui_server.py"),"--stdio-control"],
    {cwd:root,windowsHide:true,stdio:["pipe","pipe","pipe"]});
  let browser, page, output="", error="";
  child.stderr.on("data", chunk=>{error+=chunk;});
  const exited = new Promise(resolve=>child.once("exit",resolve));
  const artifacts=path.join(root,"reports","pdf-regions-ui",String(Date.now()));
  mkdirSync(artifacts,{recursive:true});
  try {
    const url = await new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>reject(new Error("Synthetic fixture startup timed out")),60000);
      child.once("error",err=>{clearTimeout(timer);reject(err);});
      child.once("exit",code=>{clearTimeout(timer);reject(new Error(`Fixture exited ${code}: ${error}`));});
      child.stdout.on("data",chunk=>{output+=chunk;if(output.includes("\n")){clearTimeout(timer);resolve(JSON.parse(output.split("\n")[0]).url);}});
    });
    const origin=new URL(url).origin;
    browser=await chromium.launch({channel:"msedge",headless:true});
    page=await browser.newPage({viewport:{width:1365,height:900}});
    page.setDefaultTimeout(30000);
    await page.addInitScript(()=>{
      window.syntheticImportReceipts=[];
      const open=XMLHttpRequest.prototype.open;
      XMLHttpRequest.prototype.open=function(method,url,...rest){
        if(method==="POST"&&String(url).includes("/api/import?")){
          this.addEventListener("loadend",()=>window.syntheticImportReceipts.push({status:this.status,body:this.responseText}));
        }
        return open.call(this,method,url,...rest);
      };
    });
    const pageErrors=[], externalRequests=[];
    page.on("pageerror",err=>pageErrors.push(err.message));
    await page.route("**/*",route=>{
      if(route.request().url().startsWith(origin+"/"))return route.continue();
      externalRequests.push(route.request().url());return route.abort();
    });
    await page.goto(url);
    await page.waitForFunction(()=>state.data && !document.querySelector("#documentFile").disabled);
    await page.locator("#documentFile").setInputFiles(path.join(root,"tests","fixtures","complete-materials-v1","challenge-en-missing_middle.pdf"));
    await page.waitForFunction(()=>window.syntheticImportReceipts.length===1);
    const incomplete=await page.evaluate(()=>window.syntheticImportReceipts[0]);
    assert.equal(incomplete.status,400,JSON.stringify(incomplete));
    const incompleteBody=JSON.parse(incomplete.body);
    assert.equal(incompleteBody.code,"LOCAL_PARSER_INCOMPLETE_PAGINATION");
    await page.waitForFunction(()=>document.querySelector("#toast").textContent.includes("页码不连续"));
    await page.waitForFunction(()=>Number(getComputedStyle(document.querySelector("#toast")).opacity)>=.99);
    assert.equal(await page.locator(".source-preview").count(),0);
    await page.screenshot({path:path.join(artifacts,"incomplete-zh.png")});
    await page.getByText("EN",{exact:true}).click();
    await page.locator("#documentFile").setInputFiles([]);
    await page.locator("#documentFile").setInputFiles(path.join(root,"tests","fixtures","complete-materials-v1","challenge-en-missing_middle.pdf"));
    await page.waitForFunction(()=>window.syntheticImportReceipts.length===2);
    const incompleteEn=await page.evaluate(()=>window.syntheticImportReceipts[1]);
    assert.equal(incompleteEn.status,400,JSON.stringify(incompleteEn));
    assert.equal(JSON.parse(incompleteEn.body).code,"LOCAL_PARSER_INCOMPLETE_PAGINATION");
    await page.waitForFunction(()=>document.querySelector("#toast").textContent.includes("pagination is incomplete"));
    await page.waitForFunction(()=>Number(getComputedStyle(document.querySelector("#toast")).opacity)>=.99);
    await page.screenshot({path:path.join(artifacts,"incomplete-en.png")});
    await page.getByText("中文",{exact:true}).click();
    const uploaded=page.waitForResponse(response=>response.request().method()==="POST" && response.url().includes("/api/import?"));
    await page.locator("#documentFile").setInputFiles(path.join(root,"tests","fixtures","material-layout-probes-v1","zh-columns.pdf"));
    const uploadResponse=await uploaded;
    assert.equal(uploadResponse.status(),200);
    await page.locator(".source-preview").waitFor();
    assert.equal(await page.locator(".preview-select").count(),5);
    assert.equal(await page.locator(".preview-select:checked").count(),0);
    await page.locator(".preview-select").first().check();
    const blocked=page.waitForResponse(response=>response.url().endsWith("/api/commit-source"));
    await page.locator(".commit-preview").click();
    assert.equal((await (await blocked).json()).code,"VISUAL_SOURCE_VERIFICATION_REQUIRED");
    assert.equal(await page.locator(".source-preview").count(),1);
    await page.locator(".source-review-open").click();
    await page.locator(".source-original img").waitFor();
    await page.waitForFunction(()=>document.querySelector(".source-original img")?.naturalWidth>0);
    assert.equal(await page.locator(".source-original svg rect").count(),10);
    assert.equal(await page.locator(".source-region-details").getAttribute("open"),null);
    await page.locator(".source-region-details summary").click();
    assert.match(await page.locator(".source-region-details p").innerText(),/page 1, line 1/);
    await page.locator(".source-region-details summary").click();
    await page.getByRole("button",{name:"放大原图",exact:true}).click();
    assert.equal(await page.getByRole("button",{name:"适应宽度",exact:true}).getAttribute("aria-pressed"),"true");
    await page.waitForFunction(()=>getComputedStyle(document.querySelector("#toast")).opacity==="0");
    await page.screenshot({path:path.join(artifacts,"desktop-source.png")});
    await page.getByRole("button",{name:"关闭原图",exact:true}).click();
    assert.equal(await page.locator(".source-original img").count(),0);
    await page.getByText("EN",{exact:true}).click();
    await page.setViewportSize({width:390,height:844});
    await page.locator(".source-review-open").click();
    await page.locator(".source-original img").waitFor();
    await page.getByRole("button",{name:"Zoom original",exact:true}).click();
    const viewport=page.locator(".source-image-viewport");
    await viewport.focus();await page.keyboard.press("ArrowRight");
    await page.waitForFunction(()=>document.querySelector(".source-image-viewport").scrollLeft>0);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth),true);
    await page.screenshot({path:path.join(artifacts,"mobile-source.png")});
    await page.getByRole("button",{name:"Fit width",exact:true}).click();
    assert.equal(await page.locator(".source-zoomed").count(),0);
    await page.locator(".visual-source-confirm").check();
    await page.locator(".include-all-preview").click();
    await page.waitForFunction(()=>document.querySelectorAll(".source-preview").length===0);
    await page.reload();
    await page.waitForFunction(()=>state.data?.claims?.length===5);
    const snapshot=await page.evaluate(()=>({decisions:state.data.claims.map(c=>c.decision),sources:state.data.sources.length}));
    assert.deepEqual(snapshot.decisions,Array(5).fill("PENDING"));
    assert.equal(snapshot.sources,1);
    assert.deepEqual(pageErrors,[]);assert.deepEqual(externalRequests,[]);
    writeFileSync(path.join(artifacts,"browser.json"),JSON.stringify({synthetic_only:true,model:"RECORDED_RESPONSE_REPLAY",desktop:"zh",mobile:"en",source_hash_bound:true,unconfirmed_selection:"BLOCKED",candidate_count:5,after_reload:snapshot,page_errors:pageErrors,external_requests:externalRequests},null,2)+"\n");
  } catch(error) {
    if(page){await page.screenshot({path:path.join(artifacts,"failure.png"),fullPage:true});writeFileSync(path.join(artifacts,"failure.txt"),String(error)+"\n"+await page.locator("body").innerText());}
    throw error;
  } finally {
    if(browser)await browser.close();
    child.stdin.end("stop\n");
    const result=await Promise.race([exited,new Promise(resolve=>setTimeout(()=>resolve("timeout"),15000))]);
    if(result==="timeout"){child.kill();throw new Error("Synthetic fixture did not shut down");}
    assert.equal(result,0,error);
    const receipt=JSON.parse(output.trim().split(/\r?\n/).at(-1));
    assert.equal(receipt.temporary_state_removed,true);
    for(const count of Object.values(receipt.database_counts))assert.equal(count,0);
    writeFileSync(path.join(artifacts,"server.json"),JSON.stringify(receipt,null,2)+"\n");
  }
  process.stdout.write("PDF regions UI PASS: real upload, parser, original render, confirmation gate, Chinese desktop, English mobile, pending Claims after reload, zero external actions and clean teardown; model replay only.\n");
})().catch(error=>{console.error(error);process.exitCode=1;});
