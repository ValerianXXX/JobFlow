"use strict";
// Actual loopback HTTP/DPAPI and synthetic mail. No account, model or mailbox.
const assert = require("node:assert/strict");
const {spawn} = require("node:child_process");
const {mkdirSync, writeFileSync} = require("node:fs");
const path = require("node:path");
const {chromium} = require("playwright");
const root = path.resolve(__dirname,"..");

(async()=>{
  const python=process.env.JOBFLOW_TEST_PYTHON||path.join(root,".venv","Scripts","python.exe");
  const child=spawn(python,[path.join(__dirname,"workbench_ui_server.py"),"--stdio-control","--mail-review-fixture"],
    {cwd:root,windowsHide:true,stdio:["pipe","pipe","pipe"]});
  let browser, output="", stderr="";
  const exited=new Promise(resolve=>child.once("exit",resolve));
  child.stderr.on("data",part=>{stderr+=part;});
  try {
    const url=await new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>reject(new Error("Synthetic mail fixture startup timed out")),120000);
      child.once("error",error=>{clearTimeout(timer);reject(error);});
      child.once("exit",code=>{clearTimeout(timer);reject(new Error(`Fixture exited ${code}: ${stderr}`));});
      child.stdout.on("data",part=>{output+=part;if(output.includes("\n")){clearTimeout(timer);resolve(JSON.parse(output.trim().split("\n")[0]).url);}});
    });
    const origin=new URL(url).origin;
    const artifacts=path.join(root,"reports","mail-review-ui");mkdirSync(artifacts,{recursive:true});
    browser=await chromium.launch({channel:"msedge",headless:true});
    const page=await browser.newPage({viewport:{width:1365,height:900},reducedMotion:"reduce"});
    page.setDefaultTimeout(30000);
    const errors=[], external=[];
    page.on("pageerror",error=>errors.push(error.message));
    await page.route("**/*",route=>{
      if(route.request().url().startsWith(origin+"/"))return route.continue();
      external.push(route.request().url());return route.abort();
    });
    const headers={"X-JobOps-Session":"synthetic-workbench-ui"};
    const api=async(name,data)=>{
      const response=await page.request.post(origin+"/api/"+name,{headers,data});
      assert.equal(response.status(),200,await response.text());return response.json();
    };
    await page.goto(url);
    await page.getByRole("button",{name:"查看状态收件箱",exact:true}).click();
    const panel=page.locator("#statusInboxPanel");
    await panel.locator(".mail-event").first().waitFor();
    assert.equal(await panel.locator(".mail-event").count(),3);
    const initial=await api("status-inbox",{});
    assert.deepEqual(initial.events.map(event=>event.status_proposal).sort(),["INTERVIEW_INVITED","REJECTED","UNKNOWN"]);
    assert.ok(initial.events.every(event=>event.application_id===null&&event.authenticated_sender===false));
    const ambiguous=panel.locator(".mail-event").filter({hasText:"This is not an interview invitation."});
    await ambiguous.locator("summary").click();
    await ambiguous.getByRole("button",{name:"手动关联",exact:true}).click();
    assert.match(await page.locator("#workbenchNotice").innerText(),/核对/);
    await ambiguous.locator(".mail-application").selectOption("APP-DEFACED00001");
    await ambiguous.locator(".mail-confirm").check();
    await ambiguous.getByRole("button",{name:"手动关联",exact:true}).click();
    await page.waitForFunction(()=>document.querySelector("#statusInboxPanel")?.textContent.includes("已手动关联"));
    await ambiguous.locator("summary").click();
    await page.waitForFunction(()=>getComputedStyle(document.querySelector("#toast")).opacity==="0");
    await ambiguous.scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(artifacts,"desktop-zh.png")});
    await page.reload();
    await page.getByRole("button",{name:"查看状态收件箱",exact:true}).click();
    await page.waitForFunction(()=>document.querySelector("#statusInboxPanel")?.textContent.includes("已手动关联"));
    const localeResponse=page.waitForResponse(response=>response.url().endsWith("/api/save"));
    await page.getByText("EN",{exact:true}).click();
    assert.equal((await localeResponse).status(),200);
    await page.locator("#activityIndicator").waitFor({state:"hidden"});
    await page.setViewportSize({width:390,height:844});
    await ambiguous.scrollIntoViewIfNeeded();
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth),true);
    await page.screenshot({path:path.join(artifacts,"mobile-en.png")});
    await ambiguous.locator(".mail-confirm").check();
    const decisionResponse=page.waitForResponse(response=>response.url().endsWith("/api/review-status-message"));
    await ambiguous.getByRole("button",{name:"Undo review",exact:true}).click();
    assert.equal((await decisionResponse).status(),200);
    const restored=await api("status-inbox",{});
    const event=restored.events.find(item=>item.body.includes("This is not"));
    assert.equal(event.application_id,null);
    assert.equal(event.status_proposal,"UNKNOWN");
    assert.equal(event.review_history.length,2);
    assert.equal((await api("application-workspace",{application_id:"APP-DEFACED00001"})).status,"AWAITING_APPROVAL");
    await panel.getByRole("button",{name:"Close and clear",exact:true}).click();
    assert.equal(await panel.locator(".mail-event").count(),0);
    assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
    writeFileSync(path.join(artifacts,"verification.json"),JSON.stringify({status:"PASS",syntheticOnly:true,
      store:"REAL_WINDOWS_DPAPI",mailboxAccess:false,externalRequests:0,modelRequests:0,
      sizes:["1365x900","390x844"],locales:["zh","en"],manualLinkReloadUndo:true,
      applicationStateChanged:false,independentHumanAcceptance:false},null,2)+"\n");
    process.stdout.write("Mail review HTTP/DPAPI UI PASS: negation, plain/HTML history, review/reload/undo, bilingual desktop/mobile; no external actions.\n");
  } finally {
    if(browser)await browser.close();
    child.stdin.end("stop\n");
    const code=await Promise.race([exited,new Promise(resolve=>setTimeout(()=>resolve("timeout"),15000))]);
    if(code==="timeout"){child.kill();throw new Error("Synthetic mail fixture did not cleanly stop");}
    if(code!==0)throw new Error(`Synthetic mail fixture exit ${code}: ${stderr}`);
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
