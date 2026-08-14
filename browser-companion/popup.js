"use strict";

const copy = {
  zh: {
    subtitle: "浏览器伴侣", notPaired: "尚未与 JobFlow 配对。请先在 JobFlow 的已批准申请中点击“开始自动预填”。",
    paired: "已配对：返回已批准的公司/ATS 申请页。", fill: "分析并填写当前页", continue: "检查后进入下一页", resume: "我已完成登录/验证，继续",
    capturePaired: "岗位导入已配对。JobFlow 只读取你主动选择的两个页面。", captureJob: "读取当前公司岗位页", captureForm: "读取当前申请表并生成审阅包",
    captureWorking: "正在本机读取并核对当前页面……", captureNext: "岗位页已读取。请亲自点击公司页面的 Apply，再在申请表页打开 J 图标。", captureReady: "审阅包已生成。返回 JobFlow 完成一次审阅。",
    captureBoundary: "导入阶段只读取当前岗位页和表单结构，不填写、不上传、不点击网页按钮，也不读取密码、验证码或现有输入值。",
    boundary: "JobFlow 可在你在场时逐页填写并点击明确的 Next/Continue，但绝不会点击最终 Submit、创建账号或绕过验证码。最终提交只能由你亲自点击。",
    working: "正在核对表单、预填并附加材料……", wrong: "请先打开 JobFlow 指定的公司申请页面。",
    permission: "需要你允许本次申请页面访问，才能继续。", done: "已停在最终提交前。请检查内容并亲自点击提交。",
    handoff: "请在网页中亲自完成登录、CAPTCHA 或 MFA；不要把密码或验证码交给 JobFlow。完成并回到申请页后点击继续。",
    manual: "JobFlow 已填写可安全复用的内容。请补完当前页标出的字段，再点击进入下一页。", navigating: "正在进入下一页并重新验真……", stalled: "页面在 20 秒内没有可靠前进。JobFlow 已停止且不会重试；请返回 JobFlow 结束本次辅助后重新开始。"
  },
  en: {
    subtitle: "Browser Companion", notPaired: "Not paired. Start assisted prefill from an approved application in JobFlow first.",
    paired: "Paired. Return to the approved company/ATS application page.", fill: "Analyze and fill this page", continue: "Review, then continue", resume: "Login/verification done — resume",
    capturePaired: "Guided job import is paired. JobFlow reads only the two pages you explicitly choose.", captureJob: "Read this company job page", captureForm: "Read this application form and build review packet",
    captureWorking: "Reading and validating this page locally…", captureNext: "Job page captured. Click Apply on the company page yourself, then open J again on the application form.", captureReady: "The review packet is ready. Return to JobFlow for one review.",
    captureBoundary: "Import is read-only: no field fill, upload, page click, password, verification code, or existing input value is read or changed.",
    boundary: "While you are present, JobFlow may fill each page and activate an explicit Next/Continue control. It never clicks final Submit, creates an account, or bypasses verification. Only you submit.",
    working: "Matching the page, filling approved fields, and attaching materials…", wrong: "Open the application page selected by JobFlow first.",
    permission: "Allow access to this application page to continue.", done: "Stopped before final submission. Review everything and click Submit yourself.",
    handoff: "Complete login, CAPTCHA, or MFA yourself. Never give JobFlow a password or verification code. Return to the application page, then resume.",
    manual: "Reusable fields are ready. Complete the remaining fields shown on this page, then continue.", navigating: "Opening and re-validating the next page…", stalled: "The page did not reliably advance within 20 seconds. JobFlow stopped and will not retry; end this assist in JobFlow, then start again."
  }
};
let locale = "zh";
let status = null;

const elements = Object.fromEntries(["subtitle","state","fill","boundary","message"].map((id) => [id, document.getElementById(id)]));

function render() {
  const text = copy[locale];
  const capture = status?.mode === "JOB_CAPTURE";
  elements.subtitle.textContent = text.subtitle;
  elements.state.textContent = status?.paired ? (capture
    ? `${text.capturePaired}\n${status.allowed_company_domain || ""}`
    : `${text.paired}\n${status.provider || ""} · ${status.current_step || 1}/${status.max_steps || 20}\n${status.allowed_page_origin || ""}`) : text.notPaired;
  elements.fill.textContent = capture
    ? (["AWAITING_APPLICATION_FORM_CAPTURE", "FORM_CAPTURE_FAILED"].includes(status?.status) ? text.captureForm : text.captureJob)
    : status?.status === "HANDOFF_REQUIRED" ? text.resume : status?.status === "PAGE_REVIEW_REQUIRED" ? text.continue : text.fill;
  elements.fill.disabled = !status?.paired || (capture
    ? ["REVIEW_PACKET_READY", "DEFERRED", "PREPARING_APPLICATION"].includes(status?.status)
    : ["AWAITING_USER_SUBMIT", "OBSERVING_RESULT_PAGE", "AWAITING_NAVIGATION", "CONFIRMED"].includes(status?.status));
  elements.boundary.textContent = capture ? text.captureBoundary : text.boundary;
}

async function refresh() {
  status = await chrome.runtime.sendMessage({type: "JOBFLOW_GET_STATUS"});
  render();
  if (status?.mode === "JOB_CAPTURE" && status?.status === "AWAITING_APPLICATION_FORM_CAPTURE") elements.message.textContent = copy[locale].captureNext;
  else if (status?.mode === "JOB_CAPTURE" && ["REVIEW_PACKET_READY", "DEFERRED"].includes(status?.status)) elements.message.textContent = copy[locale].captureReady;
  else if (status?.last_result?.status === "AWAITING_USER_SUBMIT") elements.message.textContent = copy[locale].done;
  else if (status?.status === "HANDOFF_REQUIRED") elements.message.textContent = copy[locale].handoff;
  else if (status?.status === "PAGE_REVIEW_REQUIRED") elements.message.textContent = copy[locale].manual;
  else if (status?.status === "AWAITING_NAVIGATION") elements.message.textContent = copy[locale].navigating;
  if (status?.last_result?.status === "NAVIGATION_STALLED") elements.message.textContent = copy[locale].stalled;
}

document.getElementById("zh").addEventListener("click", () => {locale = "zh"; render();});
document.getElementById("en").addEventListener("click", () => {locale = "en"; render();});
elements.fill.addEventListener("click", async () => {
  const capture = status?.mode === "JOB_CAPTURE";
  elements.message.textContent = capture ? copy[locale].captureWorking : copy[locale].working;
  elements.fill.disabled = true;
  try {
    const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
    if (!tab?.id || !tab.url) throw new Error(copy[locale].wrong);
    const url = new URL(tab.url);
    if (url.protocol !== "https:" || (!capture && url.origin !== status.allowed_page_origin)) throw new Error(copy[locale].wrong);
    const granted = await chrome.permissions.request({origins: [`${url.origin}/*`]});
    if (!granted) throw new Error(copy[locale].permission);
    const type = capture ? "JOBFLOW_CAPTURE_CURRENT" : status?.status === "PAGE_REVIEW_REQUIRED" ? "JOBFLOW_CONTINUE_CURRENT" : "JOBFLOW_FILL_CURRENT";
    const result = await chrome.runtime.sendMessage({type, tab_id: tab.id, tab_url: tab.url});
    const allowed = capture
      ? ["AWAITING_APPLICATION_FORM_CAPTURE", "REVIEW_PACKET_READY", "DEFERRED"]
      : ["AWAITING_USER_SUBMIT", "PAGE_REVIEW_REQUIRED", "HANDOFF_REQUIRED", "NAVIGATION_STARTED"];
    if (!result || !allowed.includes(result.status)) {
      throw new Error(result?.message || result?.code || "JobFlow blocked the operation.");
    }
    elements.message.textContent = capture
      ? (result.status === "AWAITING_APPLICATION_FORM_CAPTURE" ? copy[locale].captureNext : copy[locale].captureReady)
      : result.status === "AWAITING_USER_SUBMIT" ? copy[locale].done :
      result.status === "HANDOFF_REQUIRED" ? copy[locale].handoff :
      result.status === "PAGE_REVIEW_REQUIRED" ? copy[locale].manual : copy[locale].navigating;
    await refresh();
  } catch (error) {
    elements.message.textContent = String(error?.message || error);
    elements.fill.disabled = false;
  }
});

refresh().catch(() => {status = {paired: false}; render();});
