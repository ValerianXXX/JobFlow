"use strict";

// Real loopback HTTP and Windows DPAPI, original synthetic fixtures, no model/mailbox.
const assert = require("node:assert/strict");
const {spawn} = require("node:child_process");
const {mkdirSync} = require("node:fs");
const path = require("node:path");
const {chromium} = require("playwright");
const root = path.resolve(__dirname, "..");

(async () => {
  const python = process.env.JOBFLOW_TEST_PYTHON || path.join(root, ".venv", "Scripts", "python.exe");
  const child = spawn(python, [path.join(__dirname, "workbench_ui_server.py"), "--stdio-control"],
    {cwd:root, windowsHide:true, stdio:["pipe", "pipe", "pipe"]});
  let browser, output = "", error = "";
  child.stderr.on("data", chunk => {error += chunk;});
  const exited = new Promise(resolve => child.once("exit", resolve));
  try {
    const url = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("Synthetic fixture startup timed out")), 60000);
      child.once("error", err => {clearTimeout(timer); reject(err);});
      child.once("exit", code => {clearTimeout(timer); reject(new Error(`Fixture exited ${code}: ${error}`));});
      child.stdout.on("data", chunk => {
        output += chunk;
        if (output.includes("\n")) {clearTimeout(timer); resolve(JSON.parse(output.trim().split("\n")[0]).url);}
      });
    });
    const origin = new URL(url).origin;
    browser = await chromium.launch({channel:"msedge", headless:true});
    const page = await browser.newPage({viewport:{width:1365,height:900}});
    page.setDefaultTimeout(20000);
    const pageErrors = [], externalRequests = [];
    page.on("pageerror", error => pageErrors.push(error.message));
    await page.route("**/*", route => {
      if (route.request().url().startsWith(origin + "/")) return route.continue();
      externalRequests.push(route.request().url()); return route.abort();
    });
    const artifacts = path.join(root, "reports", "workbench-ui");
    mkdirSync(artifacts,{recursive:true});
    await page.goto(url);
    await page.locator(".source-preview").waitFor();
    const regionInjectionEscaped = await page.evaluate(() => {
      const summary = state.data.pending_sources[0].extraction_summary;
      const original = summary.visual_regions;
      try {
        summary.visual_regions = {invalid: "legacy metadata"};
        renderSourcePreviews();
        summary.visual_regions = [null, {page:1,line_start:1,bbox:['<img id="untrusted-region">',0,1,1]}];
        renderSourcePreviews();
        return !document.querySelector("#untrusted-region") && document.querySelector(".source-preview").textContent.includes('<img id="untrusted-region">');
      } finally {summary.visual_regions=original; renderSourcePreviews();}
    });
    assert.equal(regionInjectionEscaped,true);
    await page.getByRole("button",{name:"职位详情",exact:true}).click();
    const workspace = page.locator("#applicationWorkspacePanel");
    await workspace.getByRole("heading",{name:"Workflow Quality Analyst · Synthetic Demo Studio"}).waitFor();
    await workspace.locator("details").filter({hasText:"原文哈希已校验"}).locator("summary").click();
    assert.match(await workspace.innerText(), /Synthetic role: inspect fictional workflow evidence/);
    assert.match(await workspace.innerText(), /快照不可用或校验失败/);
    assert.match(await workspace.innerText(), /不能证明雇主已收到/);
    await page.screenshot({path:path.join(artifacts,"desktop-workspace.png")});
    await page.locator(".source-review-open").click();
    await page.locator(".source-original img").waitFor();
    assert.equal(await page.locator(".preview-select").isChecked(), false);
    assert.equal(await page.locator(".commit-preview").isDisabled(), true);
    await page.screenshot({path:path.join(artifacts,"desktop-source.png")});
    await page.getByRole("button",{name:"关闭原图",exact:true}).click();
    assert.equal(await page.locator(".source-original img").count(),0);
    await page.getByRole("button",{name:"查看状态收件箱",exact:true}).click();
    const inbox = page.locator("#statusInboxPanel");
    await inbox.locator(".mail-event").waitFor();
    assert.equal(await inbox.locator("input[type=file]").isDisabled(),true);
    await inbox.getByRole("button",{name:"手动关联",exact:true}).click();
    assert.match(await page.locator("#workbenchNotice").innerText(), /核对/);
    await inbox.locator(".mail-application").selectOption("APP-DEFACED00001");
    await inbox.locator(".mail-confirm").check();
    await inbox.getByRole("button",{name:"手动关联",exact:true}).click();
    await page.waitForFunction(() => document.querySelector(".mail-event")?.textContent.includes("已手动关联"));
    await page.reload();
    await page.getByRole("button",{name:"查看状态收件箱",exact:true}).click();
    await page.waitForFunction(() => document.querySelector(".mail-event")?.textContent.includes("已手动关联"));
    await inbox.locator(".mail-confirm").check();
    await inbox.getByRole("button",{name:"撤销审阅",exact:true}).click();
    await page.waitForFunction(() => document.querySelector(".mail-event")?.textContent.includes("等待人工核对"));
    await page.getByText("EN",{exact:true}).click();
    await page.locator("#activityIndicator").waitFor({state:"hidden"});
    assert.match(await page.locator("#workbenchNotice").innerText(), /Evidence link saved/);
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.getByRole("button",{name:"Job details",exact:true}).count(),1);
    await inbox.getByRole("button",{name:"Link manually",exact:true}).scrollIntoViewIfNeeded();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth),true);
    await page.screenshot({path:path.join(artifacts,"mobile-inbox.png")});
    await inbox.getByRole("button",{name:"Close and clear",exact:true}).click();
    assert.equal(await inbox.locator(".mail-event").count(),0);
    const response = await page.request.post(origin+"/api/application-workspace",{
      headers:{"X-JobOps-Session":"synthetic-workbench-ui"},data:{application_id:"APP-DEFACED00001"}});
    assert.equal(response.status(),200);
    assert.equal((await response.json()).status,"AWAITING_APPROVAL");
    // Exercise the actual UI route: service-only approval tests missed a demo
    // button that incorrectly tried to pair with a real Browser Companion.
    await page.getByRole("button",{name:"View review packet",exact:true}).click();
    await page.getByRole("button",{name:"Approve synthetic review packet",exact:true}).waitFor();
    assert.match(await page.locator("#packetDecisionPanel").innerText(), /authorizes no real external actions/);
    await page.getByRole("button",{name:"Approve synthetic review packet",exact:true}).click();
    await page.locator("#demoRehearsalControls").waitFor({state:"visible"});
    await page.locator("#demoRehearsalConfirm").check();
    await page.getByRole("button",{name:"Run to final confirmation",exact:true}).click();
    await page.locator("#demoFinalControls").waitFor({state:"visible"});
    assert.match(await page.locator("#demoExecutionPanel").innerText(), /temporary files were removed/);
    await page.locator("#demoFinalConfirm").check();
    await page.getByRole("button",{name:"Confirm fake final submission",exact:true}).click();
    await page.waitForFunction(() => document.querySelector("#demoExecutionStatus")?.textContent.includes("full synthetic loop is confirmed"));
    assert.equal(await page.locator("#demoFinalControls").isVisible(),false);
    assert.equal(await page.locator("#demoRehearsalControls").isVisible(),false);
    await page.waitForFunction(() => getComputedStyle(document.querySelector("#toast")).opacity === "0");
    await page.screenshot({path:path.join(artifacts,"mobile-rehearsal.png")});
    assert.deepEqual(pageErrors,[]); assert.deepEqual(externalRequests,[]);
    process.stdout.write("Workbench HTTP/DPAPI UI PASS: bilingual, desktop/mobile, source review, snapshot integrity, mail link/reload/undo/clear, UI approval through synthetic receipt; no external requests.\n");
  } finally {
    if (browser) await browser.close();
    child.stdin.end("stop\n");
    const result = await Promise.race([exited, new Promise(resolve => setTimeout(()=>resolve("timeout"),15000))]);
    if(result === "timeout") {child.kill(); throw new Error("Synthetic fixture did not cleanly shut down");}
  }
})().catch(error => {console.error(error); process.exitCode=1;});
