"use strict";

(() => {
  const words = {
    zh: {title:"检查最终申请材料", note:"这里是这份申请包保存的文件。预览和下载不会上传文件、批准或提交申请。", copy:"下载的副本不再由 JobFlow 加密保存，请妥善保管。", resume:"岗位简历", cover_letter:"求职信", portfolio:"作品集", preview:"预览", download:"下载", close:"关闭并清除", previous:"上一页", next:"下一页", page:"第 {page} 页，共 {count} 页", zoom:"缩放", fit:"适合宽度", original:"原始大小", loading:"正在读取本地文件…", unavailable:"文件不可用或已变化。请重新打开当前申请包。", previewLimit:"此文件无法在预览限制内显示。你仍可下载已核验的文件进行检查。", stale:"申请包已更新。请关闭此视图并重新打开申请包。", retry:"重新读取", empty:"这份申请包没有可供查看的最终文件。", verified:"显示的是与此申请包一致的文件。页面内容仍需你核对。", saved:"文件已交给浏览器下载。申请状态未改变。", mismatch:"收到的文件与申请包不一致，已停止下载。"},
    en: {title:"Review final application files", note:"These files are saved with this application packet. Viewing or downloading does not upload, approve or submit anything.", copy:"Downloaded copies are outside JobFlow's encrypted storage. Keep them private.", resume:"Job-specific resume", cover_letter:"Cover Letter", portfolio:"Portfolio", preview:"Preview", download:"Download", close:"Close and clear", previous:"Previous page", next:"Next page", page:"Page {page} of {count}", zoom:"Zoom", fit:"Fit width", original:"Original size", loading:"Reading the local file…", unavailable:"This file is unavailable or has changed. Reopen the current review packet.", previewLimit:"This file could not be shown within the preview limits. You can still download the verified file to inspect it locally.", stale:"This application packet changed. Close this view and reopen the packet.", retry:"Read again", empty:"This packet has no final files available to review.", verified:"This is the file bound to this packet. Please still check its contents.", saved:"The file was handed to your browser for download. Application status is unchanged.", mismatch:"The received file does not match the packet. Download stopped."}
  };
  const label = key => words[state.locale === "en" ? "en" : "zh"][key];
  const esc = escapeHtml;
  const dialog = document.createElement("dialog");
  dialog.className = "material-review-dialog";
  dialog.setAttribute("aria-labelledby", "materialReviewTitle");
  document.body.append(dialog);
  let generation = 0, viewGeneration = 0, binding = null, returnFocus = null, pageRequest = null;
  const requests = new Set(), urls = new Set();
  const current = (value, stamp) => state.serviceCompatible && stamp === generation && binding === value &&
    state.reviewPacket?.application_id === value.application_id && state.reviewPacket?.packet?.content_hash === value.expected_packet_hash;
  const message = error => error?.code === "REVIEW_PACKET_STALE" ? label("stale") :
    String(error?.code || "").startsWith("SOURCE_REVIEW_") ? label("previewLimit") : label("unavailable");

  function closeView() {
    viewGeneration++;
    pageRequest?.abort(); pageRequest = null;
    if (dialog.open) dialog.close();
    dialog.querySelectorAll("img").forEach(image => image.removeAttribute("src"));
    dialog.replaceChildren();
    const focus = returnFocus; returnFocus = null;
    if (focus?.isConnected) focus.focus({preventScroll:true});
  }
  function clear() {
    generation++; binding = null; closeView();
    requests.forEach(controller => controller.abort()); requests.clear();
    urls.forEach(url => URL.revokeObjectURL(url)); urls.clear();
  }
  async function request(route, payload, {binary=false, controller=new AbortController()}={}) {
    requests.add(controller);
    try {
      return await api(route, {method:"POST", headers:{"Content-Type":"application/json"},
        body:JSON.stringify(payload), signal:controller.signal, binary});
    } finally { requests.delete(controller); }
  }
  function matches(result, value, material) {
    return result?.application_id === value.application_id && result?.packet_hash === value.expected_packet_hash &&
      (!material || (result.material?.material_id === material.material_id && result.material?.sha256 === material.sha256));
  }
  async function showPage(material, page, value, stamp) {
    if (!current(value, stamp) || !dialog.open) return;
    const viewStamp = ++viewGeneration;
    pageRequest?.abort(); pageRequest = new AbortController();
    const status = dialog.querySelector(".material-page-status"), image = dialog.querySelector("img");
    image.removeAttribute("src"); image.hidden = true; status.textContent = label("loading");
    dialog.querySelectorAll(".material-page-step").forEach(button => { button.disabled = true; });
    try {
      const result = await request("application-material-page", {...value, material_id:material.material_id, page}, {controller:pageRequest});
      if (!current(value, stamp) || viewStamp !== viewGeneration || !dialog.open) return;
      if (!matches(result, value, material) || result.page?.source_sha256 !== material.sha256 ||
          result.page?.page !== page || !Number.isSafeInteger(result.page?.page_count) ||
          result.page.page_count < page || result.page.page_count > 60 ||
          !/^data:image\/png;base64,[A-Za-z0-9+/=]+$/.test(result.page?.image_data_url || "")) throw new Error("UNBOUND_PREVIEW");
      image.src = result.page.image_data_url; image.hidden = false;
      image.alt = `${label(material.purpose)} — ${label("page").replace("{page}", page).replace("{count}", result.page.page_count)}`;
      status.textContent = `${label("page").replace("{page}", page).replace("{count}", result.page.page_count)} · ${label("verified")}`;
      for (const button of dialog.querySelectorAll(".material-page-step")) {
        const next = page + Number(button.dataset.delta);
        button.disabled = next < 1 || next > result.page.page_count;
        button.onclick = () => showPage(material, next, value, stamp);
      }
    } catch (error) {
      if (error.name !== "AbortError" && current(value, stamp) && viewStamp === viewGeneration && dialog.open) status.textContent = message(error);
    }
  }
  function openPreview(material, button, value, stamp) {
    closeView(); returnFocus = button;
    dialog.innerHTML = `<header><h3 id="materialReviewTitle">${esc(label(material.purpose))} · ${esc(material.format)}</h3><button type="button" class="text-action material-close">${esc(label("close"))}</button></header>
      <p>${esc(label("note"))}</p><div class="material-page-toolbar"><button type="button" class="secondary material-page-step" data-delta="-1">${esc(label("previous"))}</button><button type="button" class="secondary material-page-step" data-delta="1">${esc(label("next"))}</button>
      <label>${esc(label("zoom"))}<select class="material-page-zoom"><option value="fit">${esc(label("fit"))}</option><option value="original">${esc(label("original"))}</option><option value="150">150%</option><option value="200">200%</option></select></label></div>
      <p class="material-page-status" role="status" aria-live="polite"></p><div class="material-page-surface" tabindex="0"><img hidden alt=""></div>`;
    dialog.querySelector(".material-close").onclick = closeView;
    dialog.querySelector(".material-page-zoom").onchange = event => {
      dialog.querySelector(".material-page-surface").className = `material-page-surface zoom-${event.target.value}`;
    };
    dialog.showModal(); showPage(material, 1, value, stamp);
  }
  async function download(material, button, value, stamp, status) {
    button.disabled = true; status.textContent = label("loading");
    try {
      const blob = await request("application-material-file", {...value, material_id:material.material_id}, {binary:true});
      if (!current(value, stamp)) return;
      const bytes = await blob.arrayBuffer();
      const hash = "sha256:" + [...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))].map(item => item.toString(16).padStart(2,"0")).join("");
      if (!current(value, stamp)) return;
      if (hash !== material.sha256 || blob.type.split(";",1)[0] !== String(material.content_type).split(";",1)[0]) {
        status.textContent = label("mismatch"); return;
      }
      const url = URL.createObjectURL(blob), link = document.createElement("a");
      urls.add(url); link.href = url; link.download = material.filename;
      document.body.append(link); link.click(); link.remove();
      setTimeout(() => { URL.revokeObjectURL(url); urls.delete(url); }, 2000);
      status.textContent = label("saved");
    } catch (error) {
      if (error.name !== "AbortError" && current(value, stamp)) status.textContent = message(error);
    } finally { if (current(value, stamp) && button.isConnected) button.disabled = false; }
  }
  async function render(result) {
    clear();
    if (!result?.packet?.content_hash) return;
    const root = document.querySelector("#reviewPacketBody");
    if (!root) return;
    root.querySelector(".application-material-review")?.remove();
    const section = document.createElement("section");
    section.className = "packet-section wide application-material-review";
    section.innerHTML = `<h4>${esc(label("title"))}</h4><p>${esc(label("note"))}</p><p class="material-copy-note">${esc(label("copy"))}</p><div class="material-file-list"></div><p class="material-file-status" role="status" aria-live="polite">${esc(label("loading"))}</p>`;
    root.prepend(section);
    binding = {application_id:result.application_id, expected_packet_hash:result.packet.content_hash};
    const value = binding, stamp = generation;
    const status = section.querySelector(".material-file-status"), list = section.querySelector(".material-file-list");
    try {
      const response = await request("application-materials", value);
      if (!current(value, stamp) || !section.isConnected) return;
      if (!matches(response, value) || !Array.isArray(response.materials)) throw new Error("UNBOUND_FILES");
      for (const item of response.materials) {
        const card = document.createElement("article");
        card.className = "material-file-card"; card.dataset.materialId = item.material_id;
        card.innerHTML = `<div><strong>${esc(label(item.purpose))} · ${esc(item.format)}</strong><small>${esc(item.filename)}</small></div><div class="material-file-actions"></div>`;
        const actions = card.querySelector(".material-file-actions");
        if (!item.available) {
          const note = document.createElement("p"); note.textContent = label("unavailable"); actions.append(note);
        } else {
          if (item.preview_supported) {
            const button = document.createElement("button"); button.type = "button"; button.className = "secondary material-preview";
            button.textContent = `${label("preview")} ${item.format}`; button.onclick = () => openPreview(item, button, value, stamp); actions.append(button);
          }
          const button = document.createElement("button"); button.type = "button"; button.className = "secondary material-download";
          button.textContent = `${label("download")} ${item.format}`; button.onclick = () => download(item, button, value, stamp, status); actions.append(button);
        }
        list.append(card);
      }
      status.textContent = response.materials.length ? "" : label("empty");
    } catch (error) {
      if (error.name === "AbortError" || !current(value, stamp) || !section.isConnected) return;
      status.textContent = message(error);
      const retry = document.createElement("button"); retry.type = "button"; retry.className = "secondary";
      retry.textContent = label("retry"); retry.onclick = () => render(state.reviewPacket); section.append(retry);
    }
  }
  dialog.addEventListener("cancel", event => { event.preventDefault(); closeView(); });
  window.addEventListener("pagehide", clear);
  window.JobFlowMaterialReview = {render, clear};
})();
