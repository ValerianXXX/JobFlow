"use strict";

const copy = {
  zh: {
    subtitle: "浏览器伴侣", notPaired: "尚未与 JobFlow 配对。请先在 JobFlow 的已批准申请中点击“开始自动预填”。",
    paired: "已配对：打开已批准的公司申请页，然后点击下方按钮。", fill: "预填并附加已批准材料",
    boundary: "JobFlow 不会点击提交、下一步或创建账号。完成后请你逐项检查并亲自点击最终提交。",
    working: "正在核对表单、预填并附加材料……", wrong: "请先打开 JobFlow 指定的公司申请页面。",
    permission: "需要你允许本次公司页面访问，才能进行预填。", done: "已停在最终提交前。请检查内容并亲自点击提交。"
  },
  en: {
    subtitle: "Browser Companion", notPaired: "Not paired. Start assisted prefill from an approved application in JobFlow first.",
    paired: "Paired. Open the approved company application page, then use the button below.", fill: "Fill and attach approved materials",
    boundary: "JobFlow never clicks Submit, Next, or account creation. Review every field and click the final Submit button yourself.",
    working: "Matching the form, filling approved fields, and attaching materials…", wrong: "Open the company application page selected by JobFlow first.",
    permission: "Allow access to this company page to continue.", done: "Stopped before final submission. Review everything and click Submit yourself."
  }
};
let locale = "zh";
let status = null;

const elements = Object.fromEntries(["subtitle","state","fill","boundary","message"].map((id) => [id, document.getElementById(id)]));

function render() {
  const text = copy[locale];
  elements.subtitle.textContent = text.subtitle;
  elements.state.textContent = status?.paired ? `${text.paired}\n${status.allowed_page_origin || ""}` : text.notPaired;
  elements.fill.textContent = text.fill;
  elements.fill.disabled = !status?.paired || status?.status === "AWAITING_USER_SUBMIT" || status?.status === "OBSERVING_RESULT_PAGE";
  elements.boundary.textContent = text.boundary;
}

async function refresh() {
  status = await chrome.runtime.sendMessage({type: "JOBFLOW_GET_STATUS"});
  render();
  if (status?.last_result?.status === "AWAITING_USER_SUBMIT") elements.message.textContent = copy[locale].done;
}

document.getElementById("zh").addEventListener("click", () => {locale = "zh"; render();});
document.getElementById("en").addEventListener("click", () => {locale = "en"; render();});
elements.fill.addEventListener("click", async () => {
  elements.message.textContent = copy[locale].working;
  elements.fill.disabled = true;
  try {
    const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
    if (!tab?.id || !tab.url) throw new Error(copy[locale].wrong);
    const url = new URL(tab.url);
    if (url.protocol !== "https:" || url.origin !== status.allowed_page_origin) throw new Error(copy[locale].wrong);
    const granted = await chrome.permissions.request({origins: [`${url.origin}/*`]});
    if (!granted) throw new Error(copy[locale].permission);
    const result = await chrome.runtime.sendMessage({
      type: "JOBFLOW_FILL_CURRENT", tab_id: tab.id, tab_url: tab.url
    });
    if (!result || result.status !== "AWAITING_USER_SUBMIT") {
      throw new Error(result?.message || result?.code || "JobFlow blocked the operation.");
    }
    elements.message.textContent = copy[locale].done;
    await refresh();
  } catch (error) {
    elements.message.textContent = String(error?.message || error);
    elements.fill.disabled = false;
  }
});

refresh().catch(() => {status = {paired: false}; render();});
