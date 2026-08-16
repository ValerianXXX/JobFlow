"use strict";

// Unpacked extension resources are read from disk when the popup opens, while
// Edge can keep an older manifest/service worker alive. A one-time popup open
// therefore provides a safe, user-visible hot-upgrade path without asking the
// user to operate edge://extensions (which normal webpages cannot control).
const SOURCE_EXTENSION_VERSION = "0.7.0";
if (chrome.runtime.getManifest().version !== SOURCE_EXTENSION_VERSION) {
  chrome.runtime.reload();
  window.close();
}

const copy = {
  zh: {
    subtitle: "浏览器伴侣", notPaired: "尚未与当前 JobFlow 任务配对。请返回 JobFlow，点击“连接浏览器并开始”；已批准申请则点击“建立浏览器连接”。",
    reconnect: "连接当前 JobFlow 页面", returnToJobFlow: "请先返回 JobFlow 页面", reconnecting: "正在恢复本机配对……", reconnectFailed: "当前 JobFlow 页面没有待配对任务。请先点击页面中的连接按钮。",
    paired: "已配对：返回已批准的公司/ATS 申请页。", fill: "分析并填写当前页", continue: "检查后进入下一页", resume: "我已完成登录/验证，继续", manualNext: "等待你在网页点击下一步",
    capturePaired: "岗位任务已配对。JobFlow 会在可见标签页中搜索、核验公司岗位并读取申请表。", captureJob: "继续自动搜索与读取", captureForm: "继续读取申请表并生成审阅包",
    captureWorking: "正在搜索、核验官网并沿安全 Apply 路径继续……", capturePreparing: "本机 AI 正在后台生成审阅包，可能需要几分钟。你可以关闭这个小窗口；完成后 JobFlow 会自动更新。", captureNext: "岗位页已核验。JobFlow 正在沿官网公布的安全 Apply 链接进入申请表；若网页要求登录、验证或无法证明链接来源，会停下交给你。", captureReady: "审阅包已生成。返回 JobFlow 完成一次审阅。", localTimeout: "本机 JobFlow 暂时没有响应。请保持 JobFlow 窗口运行，然后重试；系统不会自动重复生成或执行外部操作。",
    captureBoundary: "准备阶段会在可见标签页中搜索，只打开核验后的公司官网岗位和其公布的 Apply 链接，并只读表单结构；不会填写、上传、点击网页控件，也不读取密码、验证码或现有输入值。",
    boundary: "JobFlow 可在你在场时逐页填写并点击明确的 Next/Continue，但绝不会点击最终 Submit、创建账号或绕过验证码。最终提交只能由你亲自点击。",
    working: "正在核对表单、预填并附加材料……", wrong: "请先打开 JobFlow 指定的公司申请页面。",
    permission: "首次使用需要允许浏览器搜索和公司招聘网站访问；授权只需一次，JobFlow 不会读取密码、验证码或其他标签页内容。", permissionDenied: "未获得浏览器搜索与公司网站访问权限。JobFlow 已停止，没有填写、上传或点击网页。再次打开 J 即可重新授权。", done: "已停在最终提交前。请检查内容并亲自点击提交。",
    handoff: "请在网页中亲自完成登录、CAPTCHA 或 MFA；不要把密码或验证码交给 JobFlow。完成并回到申请页后点击继续。",
    manual: "JobFlow 已填写可安全复用的内容。请补完当前页标出的字段，再点击进入下一页。", manualResume: "这个 Next/Continue 必须由你亲自点击。新页面加载后 JobFlow 会自动继续，不需要再次打开 J。", restartButton: "返回 JobFlow 重新开始", restart: "这次一次性下一步证明没有安全建立。请返回 JobFlow 结束并重新启动这项申请辅助；JobFlow 不会自动重试。", applyRestart: "页面可能已经填写或上传了一部分，但未能完成整页验证。本轮已停止并记入审计，绝不会自动重复填写或上传；请返回 JobFlow 重新开始。", navigating: "正在进入下一页并重新验真……", stalled: "页面在 20 秒内没有可靠前进。JobFlow 已停止且不会重试；请返回 JobFlow 结束本次辅助后重新开始。"
  },
  en: {
    subtitle: "Browser Companion", notPaired: "Not paired with the current JobFlow task. Return to JobFlow and choose Connect browser; for an approved application, choose Connect browser there.",
    reconnect: "Connect this JobFlow page", returnToJobFlow: "Return to the JobFlow page first", reconnecting: "Restoring the local pairing…", reconnectFailed: "This JobFlow page has no pending pairing. Choose its Connect button first.",
    paired: "Paired. Return to the approved company/ATS application page.", fill: "Analyze and fill this page", continue: "Review, then continue", resume: "Login/verification done — resume", manualNext: "Waiting for your page-level Next click",
    capturePaired: "The job task is paired. JobFlow searches in a visible tab, verifies the company job page, and reads the application form.", captureJob: "Continue automatic search and read", captureForm: "Continue form read and build review packet",
    captureWorking: "Searching, verifying the official site, and following the safe Apply route…", capturePreparing: "Local AI is building the review packet in the background and may need several minutes. You may close this popup; JobFlow updates automatically when it finishes.", captureNext: "The official role is verified. JobFlow is following the company-published Apply link; it stops for login, verification, or any route it cannot prove.", captureReady: "The review packet is ready. Return to JobFlow for one review.", localTimeout: "Local JobFlow did not respond in time. Keep the JobFlow window running and try again; no preparation or external action will be repeated automatically.",
    captureBoundary: "Preparation searches in a visible tab, opens only a verified company role and its published Apply URL, and reads only form structure. It does not fill, upload, click page controls, or read passwords, verification codes, or existing values.",
    boundary: "While you are present, JobFlow may fill each page and activate an explicit Next/Continue control. It never clicks final Submit, creates an account, or bypasses verification. Only you submit.",
    working: "Matching the page, filling approved fields, and attaching materials…", wrong: "Open the application page selected by JobFlow first.",
    permission: "First use needs browser-search and company-careers-site access. This is granted once; JobFlow does not read passwords, verification codes, or unrelated tabs.", permissionDenied: "Browser-search and company-site access were not granted. JobFlow stopped without filling, uploading, or clicking the page. Open J again to retry.", done: "Stopped before final submission. Review everything and click Submit yourself.",
    handoff: "Complete login, CAPTCHA, or MFA yourself. Never give JobFlow a password or verification code. Return to the application page, then resume.",
    manual: "Reusable fields are ready. Complete the remaining fields shown on this page, then continue.", manualResume: "You must click this Next/Continue yourself. JobFlow resumes automatically after the new page loads; you do not need to open J again.", restartButton: "Return to JobFlow — restart", restart: "The one-use Next proof was not armed safely. Return to JobFlow, end this application assist, and start it again. JobFlow will not retry automatically.", applyRestart: "The page may already contain some approved fields or an attachment, but whole-page verification did not finish. This run stopped and was audited; nothing will be filled or uploaded again automatically. Return to JobFlow and restart.", navigating: "Opening and re-validating the next page…", stalled: "The page did not reliably advance within 20 seconds. JobFlow stopped and will not retry; end this assist in JobFlow, then start again."
  }
};
let locale = "zh";
let status = null;
let currentTab = null;

const elements = Object.fromEntries(["subtitle","version","state","fill","boundary","message"].map((id) => [id, document.getElementById(id)]));

function isJobFlowTab(tab) {
  try {
    const parsed = new URL(String(tab?.url || ""));
    return parsed.protocol === "http:" && ["127.0.0.1", "localhost"].includes(parsed.hostname);
  } catch (_error) {
    return false;
  }
}

function render() {
  const text = copy[locale];
  const capture = status?.mode === "JOB_CAPTURE";
  elements.subtitle.textContent = text.subtitle;
  elements.version.textContent = `v${chrome.runtime.getManifest().version}`;
  elements.state.textContent = status?.paired ? (capture
    ? `${text.capturePaired}\n${status.allowed_company_domain || ""}`
    : `${text.paired}\n${status.provider || ""} · ${status.current_step || 1}/${status.max_steps || 20}\n${status.allowed_page_origin || ""}`) : text.notPaired;
  elements.fill.textContent = !status?.paired
    ? (isJobFlowTab(currentTab) ? text.reconnect : text.returnToJobFlow)
    : capture
    ? (["AWAITING_APPLICATION_FORM_CAPTURE", "FORM_CAPTURE_FAILED"].includes(status?.status) ? text.captureForm : text.captureJob)
    : status?.status === "HANDOFF_REQUIRED" ? text.resume : status?.status === "MANUAL_NAVIGATION_REQUIRED" ? text.manualNext : ["MANUAL_NAVIGATION_RESTART_REQUIRED", "APPLY_RESTART_REQUIRED"].includes(status?.status) ? text.restartButton : status?.status === "PAGE_REVIEW_REQUIRED" ? text.continue : text.fill;
  elements.fill.disabled = (!status?.paired && !isJobFlowTab(currentTab)) || (status?.paired && (capture
    ? ["REVIEW_PACKET_READY", "DEFERRED", "PREPARING_APPLICATION"].includes(status?.status)
    : ["AWAITING_USER_SUBMIT", "OBSERVING_RESULT_PAGE", "AWAITING_NAVIGATION", "MANUAL_NAVIGATION_REQUIRED", "MANUAL_NAVIGATION_RESTART_REQUIRED", "APPLY_RESTART_REQUIRED", "CONFIRMED"].includes(status?.status)));
  elements.boundary.textContent = capture ? text.captureBoundary : text.boundary;
}

async function refresh() {
  [currentTab] = await chrome.tabs.query({active: true, currentWindow: true});
  status = await chrome.runtime.sendMessage({type: "JOBFLOW_GET_STATUS"});
  render();
  if (status?.mode === "JOB_CAPTURE" && status?.status === "AWAITING_APPLICATION_FORM_CAPTURE") elements.message.textContent = copy[locale].captureNext;
  else if (status?.mode === "JOB_CAPTURE" && status?.status === "PREPARING_APPLICATION") elements.message.textContent = copy[locale].capturePreparing;
  else if (status?.mode === "JOB_CAPTURE" && ["REVIEW_PACKET_READY", "DEFERRED"].includes(status?.status)) elements.message.textContent = copy[locale].captureReady;
  else if (status?.last_result?.status === "AWAITING_USER_SUBMIT") elements.message.textContent = copy[locale].done;
  else if (status?.status === "HANDOFF_REQUIRED") elements.message.textContent = copy[locale].handoff;
  else if (status?.status === "PAGE_REVIEW_REQUIRED") elements.message.textContent = copy[locale].manual;
  else if (status?.status === "MANUAL_NAVIGATION_REQUIRED") elements.message.textContent = copy[locale].manualResume;
  else if (status?.status === "MANUAL_NAVIGATION_RESTART_REQUIRED") elements.message.textContent = copy[locale].restart;
  else if (status?.status === "APPLY_RESTART_REQUIRED") elements.message.textContent = copy[locale].applyRestart;
  else if (status?.status === "AWAITING_NAVIGATION") elements.message.textContent = copy[locale].navigating;
  if (status?.last_result?.status === "NAVIGATION_STALLED") elements.message.textContent = copy[locale].stalled;
}

async function waitForPairing() {
  for (let attempt = 0; attempt < 12; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 350));
    await refresh();
    if (status?.paired) return true;
  }
  return false;
}

async function ensureAutomationPermissions() {
  const request = {permissions: ["search"], origins: ["https://*/*"]};
  if (await chrome.permissions.contains(request)) return true;
  return await chrome.permissions.request(request);
}

document.getElementById("zh").addEventListener("click", () => {locale = "zh"; render();});
document.getElementById("en").addEventListener("click", () => {locale = "en"; render();});
elements.fill.addEventListener("click", async () => {
  const capture = status?.mode === "JOB_CAPTURE";
  if (!status?.paired) {
    elements.message.textContent = copy[locale].reconnecting;
    elements.fill.disabled = true;
    try {
      if (!currentTab?.id || !isJobFlowTab(currentTab)) throw new Error(copy[locale].returnToJobFlow);
      elements.message.textContent = copy[locale].permission;
      if (!await ensureAutomationPermissions()) throw new Error(copy[locale].permissionDenied);
      await chrome.scripting.executeScript({target: {tabId: currentTab.id}, files: ["pair.js"]});
      if (!await waitForPairing()) throw new Error(copy[locale].reconnectFailed);
      if (status?.mode === "JOB_CAPTURE") {
        const result = await chrome.runtime.sendMessage({type: "JOBFLOW_RUN_GUIDED_AUTOPILOT"});
        if (result?.status === "BLOCKED") throw new Error(result.message || result.code || copy[locale].wrong);
      } else if (status?.mode === "APPLICATION_ASSIST" && status?.status === "READY") {
        const result = await chrome.runtime.sendMessage({type: "JOBFLOW_RUN_APPLICATION_AUTOPILOT"});
        if (result?.status === "BLOCKED") throw new Error(result.message || result.code || copy[locale].wrong);
      }
    } catch (error) {
      elements.message.textContent = String(error?.message || error);
      elements.fill.disabled = false;
    }
    return;
  }
  elements.message.textContent = capture ? copy[locale].captureWorking : copy[locale].working;
  elements.fill.disabled = true;
  try {
    if (capture && ["AWAITING_JOB_DISCOVERY", "AWAITING_JOB_PAGE_CAPTURE", "AWAITING_APPLICATION_FORM_CAPTURE"].includes(status?.status)) {
      if (!await ensureAutomationPermissions()) throw new Error(copy[locale].permissionDenied);
      const result = await chrome.runtime.sendMessage({type: "JOBFLOW_RUN_GUIDED_AUTOPILOT"});
      if (!result || result.status === "BLOCKED") throw new Error(result?.message || result?.code || copy[locale].wrong);
      await refresh();
      return;
    }
    const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
    if (!tab?.id || !tab.url) throw new Error(copy[locale].wrong);
    const url = new URL(tab.url);
    if (url.protocol !== "https:" || (!capture && url.origin !== status.allowed_page_origin)) throw new Error(copy[locale].wrong);
    const type = capture ? "JOBFLOW_CAPTURE_CURRENT" : status?.status === "PAGE_REVIEW_REQUIRED" ? "JOBFLOW_CONTINUE_CURRENT" : "JOBFLOW_FILL_CURRENT";
    const result = await chrome.runtime.sendMessage({type, tab_id: tab.id, tab_url: tab.url});
    const allowed = capture
      ? ["AWAITING_APPLICATION_FORM_CAPTURE", "PREPARING_APPLICATION", "REVIEW_PACKET_READY", "DEFERRED"]
      : ["AWAITING_USER_SUBMIT", "PAGE_REVIEW_REQUIRED", "MANUAL_NAVIGATION_REQUIRED", "HANDOFF_REQUIRED", "NAVIGATION_STARTED", "APPLY_RESTART_REQUIRED"];
    if (!result || !allowed.includes(result.status)) {
      throw new Error(result?.code === "COMPANION_LOCAL_REQUEST_TIMEOUT" ? copy[locale].localTimeout : (result?.message || result?.code || "JobFlow blocked the operation."));
    }
    elements.message.textContent = capture
      ? (result.status === "AWAITING_APPLICATION_FORM_CAPTURE" ? copy[locale].captureNext : result.status === "PREPARING_APPLICATION" ? copy[locale].capturePreparing : copy[locale].captureReady)
      : result.status === "AWAITING_USER_SUBMIT" ? copy[locale].done :
      result.status === "HANDOFF_REQUIRED" ? copy[locale].handoff :
      result.status === "MANUAL_NAVIGATION_REQUIRED" ? copy[locale].manualResume :
      result.status === "APPLY_RESTART_REQUIRED" ? copy[locale].applyRestart :
      result.status === "PAGE_REVIEW_REQUIRED" ? copy[locale].manual : copy[locale].navigating;
    await refresh();
  } catch (error) {
    elements.message.textContent = String(error?.message || error);
    elements.fill.disabled = false;
  }
});

refresh().catch(() => {status = {paired: false}; render();});
