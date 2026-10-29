"use strict";

const assert = require("node:assert/strict");
const {spawn} = require("node:child_process");
const {mkdirSync,readFileSync,writeFileSync} = require("node:fs");
const {createHash} = require("node:crypto");
const path = require("node:path");
const {chromium} = require("playwright");
const root = path.resolve(__dirname,"..");

(async()=>{
  const python=process.env.JOBFLOW_TEST_PYTHON||path.join(root,".venv","Scripts","python.exe");
  const child=spawn(python,[path.join(__dirname,"standard_resume_ui_server.py"),"--stdio-control"],
    {cwd:root,windowsHide:true,stdio:["pipe","pipe","pipe"]});
  let browser,output="",stderr="";
  const exited=new Promise(resolve=>child.once("exit",resolve));
  child.stderr.on("data",chunk=>{stderr+=chunk;});
  child.stdout.on("data",chunk=>{output+=chunk;});
  const report={synthetic_only:true,seeded_completed_profile:true,ai_extraction_tested:false,live_submission:false,word_conversion:"NOT_RUN"};
  try{
    const url=await new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>reject(new Error("Synthetic fixture startup timed out")),120000);
      child.once("error",err=>{clearTimeout(timer);reject(err);});
      child.once("exit",code=>{clearTimeout(timer);reject(new Error(`Fixture exit ${code}: ${stderr}`));});
      child.stdout.on("data",()=>{if(output.includes("\n")){clearTimeout(timer);resolve(JSON.parse(output.split("\n")[0]).url);}});
    });
    browser=await chromium.launch({channel:"msedge",headless:true});
    const page=await browser.newPage({viewport:{width:1365,height:900}});
    page.setDefaultTimeout(60000);
    const origin=new URL(url).origin, errors=[], external=[];
    page.on("pageerror",error=>errors.push(error.message));
    await page.route("**/*",route=>{
      if(route.request().url().startsWith(origin+"/"))return route.continue();
      external.push(route.request().url());return route.abort();
    });
    const artifacts=path.join(root,"reports",process.env.JOBFLOW_TEST_LIBREOFFICE==="1"?"libreoffice-resume-ui":"standard-resume-ui");mkdirSync(artifacts,{recursive:true});
    await page.goto(url);
    await page.locator("#createStandardResume:not([disabled])").waitFor();
    await page.locator('[data-locale="zh"]').click();
    await page.locator("#activityIndicator").waitFor({state:"hidden"});
    await page.locator("#createStandardResume").click();
    await page.locator("#standardResumeDraft").waitFor({state:"visible"});
    assert.match(await page.locator("#standardResumeText").innerText(),/项目经历/);
    assert.match(await page.locator("#standardResumeText").innerText(),/20%/);
    assert.equal(await page.locator("#adoptStandardResume").isDisabled(),true);
    await page.screenshot({path:path.join(artifacts,"desktop-zh.png")});
    const downloadPromise=page.waitForEvent("download");await page.locator("#downloadStandardResume").click();
    const download=await downloadPromise;
    const downloaded=path.join(artifacts,"synthetic-standard-resume.docx");await download.saveAs(downloaded);
    assert.equal(readFileSync(downloaded).subarray(0,2).toString(),"PK");
    report.docx_sha256="sha256:"+createHash("sha256").update(readFileSync(downloaded)).digest("hex");
    const headers={"X-JobOps-Session":"synthetic-standard-resume"};
    const bootResponse=await page.request.get(url+"api/bootstrap",{headers});
    assert.equal(bootResponse.status(),200);
    const boot=await bootResponse.json();
    assert.equal(boot.standard_resume.available,true);
    report.capabilities=boot.document_capabilities;
    if(process.env.JOBFLOW_TEST_WORD==="1"||process.env.JOBFLOW_TEST_LIBREOFFICE==="1"){
      const provider=process.env.JOBFLOW_TEST_LIBREOFFICE==="1"?"LIBREOFFICE":"WORD";
      assert.equal(boot.document_capabilities.pdf_export_provider,provider);
      if(provider==="LIBREOFFICE")assert.match(await page.locator("#documentCapabilities").innerText(),/LibreOffice/);
      const pdfResponsePromise=page.waitForResponse(response=>response.url().endsWith("/api/standard-resume-pdf"),{timeout:120000});
      await page.locator("#previewStandardResumePdf").click();
      const pdfResponse=await pdfResponsePromise;
      const pdfResult=await pdfResponse.json();
      assert.equal(pdfResponse.status(),200,`PDF endpoint: ${pdfResult.code||pdfResult.status}, stage=${pdfResult.details?.failure_stage}; ${stderr}`);
      assert.equal(pdfResult.docx_sha256,report.docx_sha256);
      assert.equal(pdfResult.pdf_provider,provider);
      assert.equal(pdfResult.materials_validated,false);
      assert.equal(pdfResult.page.page_count,1);
      report.pdf_sha256=pdfResult.page.source_sha256;
      const rendered=Buffer.from(pdfResult.page.image_data_url.split(",")[1],"base64");
      report.render_sha256="sha256:"+createHash("sha256").update(rendered).digest("hex");
      writeFileSync(path.join(artifacts,"actual-render-1.png"),rendered);
      await page.locator("#standardResumePageImage[src]").waitFor({timeout:120000});
      assert.equal(await page.locator("#standardResumePageImage").evaluate(img=>img.complete&&img.naturalWidth>600),true);
      await page.locator("#standardResumePages").screenshot({path:path.join(artifacts,"actual-pdf-page.png")});
      if(provider==="WORD")report.word_conversion="REAL_LOCAL_WORD_PASSED";
      report.pdf_conversion=`REAL_LOCAL_${provider}_PASSED`;
      report.page_indicator=await page.locator("#standardResumePageNumber").innerText();
    }else if(boot.document_capabilities.pdf_export==="UNAVAILABLE"){
      assert.equal(await page.locator("#previewStandardResumePdf").isDisabled(),true);
      report.word_conversion="UNAVAILABLE_AND_DISABLED";
    }
    await page.locator('[data-locale="en"]').click();
    await page.locator("#activityIndicator").waitFor({state:"hidden"});
    await page.setViewportSize({width:390,height:844});
    await page.locator("#createStandardResume").click();
    await page.waitForFunction(()=>document.querySelector("#standardResumeText")?.textContent.includes("Projects"));
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth),true);
    await page.screenshot({path:path.join(artifacts,"mobile-en.png")});
    const forbidden=await page.request.post(origin+"/api/standard-resume-draft",{data:{}});
    assert.equal(forbidden.status(),403);
    const crossOrigin=await page.request.post(origin+"/api/standard-resume-draft",{headers:{...headers,Origin:"https://untrusted.example.test"},data:{}});
    assert.equal(crossOrigin.status(),403);
    await page.locator("#standardResumeConfirm").check();
    await page.locator("#adoptStandardResume").click();
    await page.locator("#standardResumePanel").waitFor({state:"hidden"});
    const adoptedResponse=await page.request.get(url+"api/bootstrap",{headers});
    assert.equal(adoptedResponse.status(),200);
    const adopted=await adoptedResponse.json();
    assert.equal(adopted.application_readiness.master_resume.editable_docx,true);
    assert.equal(adopted.external_claim_approval.current,false);
    assert.equal(adopted.status,"IN_PROGRESS");
    await page.reload();
    await page.locator("#applicationReadinessStatus").waitFor();
    assert.equal(await page.locator("#standardResumePanel").isHidden(),true);
    assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
    report.desktop_zh=true;report.mobile_en=true;report.download_verified=true;report.adoption_persisted=true;
    report.session_and_origin_checks=true;report.browser_errors=errors;report.external_requests=external;
    writeFileSync(path.join(artifacts,"result.json"),JSON.stringify(report,null,2)+"\n");
    console.log("Standard resume UI: PASS (synthetic PDF profile, actual DPAPI, bilingual, download, adoption, reload)");
  }finally{
    if(browser)await browser.close();
    if(child.exitCode===null)child.stdin.end("stop\n");
    const code=await exited;
    if(code!==0)throw new Error(`Fixture cleanup exit ${code}: ${stderr}`);
    if(!output.includes('"temporary_state_removed": true'))throw new Error("Synthetic private workspace cleanup was not confirmed");
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
