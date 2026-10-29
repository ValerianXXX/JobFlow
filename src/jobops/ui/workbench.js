"use strict";
// Private read models stay in this local page, never localStorage or telemetry.
(() => {
  const copy = {
    zh: {workbench:"申请记录与后续消息", openInbox:"查看状态收件箱", openWorkspace:"职位详情", close:"关闭并清除显示", next:"下一步", snapshots:"已保存的职位描述", snapshotBound:"关联本次审阅", snapshotVerified:"原文哈希已校验", snapshotNormalized:"文本哈希已校验（换行规范化）", missing:"没有可核验的职位快照；请补充资料。", unavailable:"快照不可用或校验失败", materials:"申请材料版本", materialNote:"这些记录证明材料已准备，不能证明雇主已收到。", bound:"与当前申请简历一致", versions:"审阅包版本", timeline:"流程记录", noRecords:"暂无记录", openPacket:"打开审阅包", mailTitle:"状态收件箱", messageText:"查看邮件正文（文本）", statusProposal:"状态提议", VIEW_ARCHIVED_RECORD:"查看已结束申请的历史记录", REVIEW_RECORDED_OUTCOME:"核对已记录的结果并由你决定后续处理", mailNote:"仅导入你选择的 EML 文件。发件人及文件中的日期未经邮箱认证；手动关联只保存证据，不改变申请执行状态。", chooseMail:"选择 EML 文件（最大 2 MiB）", importMail:"导入文件", refresh:"刷新", mailEmpty:"暂无状态消息", unknown:"无法确定", received:"收到申请", rejected:"拒绝", interview:"面试邀请", link:"手动关联", reject:"忽略关联", undo:"撤销审阅", confirm:"我已核对消息、所选申请与状态含义", select:"选择申请", pending:"等待人工核对", linked:"已手动关联", ignored:"已忽略", dateHeader:"邮件文件中的日期（未验证）", source:"对照原始页面", prev:"上一页", following:"下一页", page:"页", sourceNote:"对照原图检查事实；位置框仅为解析建议，不能证明内容准确。", hideSource:"关闭原图", busy:"正在读取本机资料…", saved:"已保存证据关联，申请执行状态未改变。", failed:"操作未完成，请刷新后重试或检查文件。", stale:"收件箱已变化，请刷新后重新核对。", confirmFirst:"请先选择申请并勾选核对确认。", sourceFailure:"无法预览该页，请使用原始文件核对。", demoImport:"合成演示不接收用户邮件文件。", fields:"需要确认的字段", VERIFY_WITH_EMPLOYER_NO_RETRY:"先核对雇主端结果；不要重复提交", REVIEW_STATUS_MESSAGES:"查看后续消息并准备下一阶段", RESOLVE_REQUIRED_FIELDS:"打开审阅包，补齐待确认字段", REVIEW_APPLICATION_PACKET:"检查本次材料和答案，再作审批决定", CONTINUE_USER_PRESENT_SESSION:"由你启动浏览器辅助并检查当前页面", REVIEW_PREPARATION_BLOCKER:"检查当前阻塞与最后完成的步骤"},
    en: {workbench:"Application records and updates", openInbox:"Open status inbox", openWorkspace:"Job details", close:"Close and clear", next:"Next step", snapshots:"Saved job descriptions", snapshotBound:"Bound to this review", snapshotVerified:"Original hash verified", snapshotNormalized:"Text hash verified (normalized line endings)", missing:"No verifiable job snapshot. Add the missing source.", unavailable:"Snapshot unavailable or integrity check failed", materials:"Application material versions", materialNote:"Prepared material is not proof of delivery to the employer.", bound:"Matches this application's resume", versions:"Review packet versions", timeline:"Workflow history", noRecords:"No records yet", openPacket:"Open review packet", mailTitle:"Status inbox", messageText:"View message text", statusProposal:"Proposed status", VIEW_ARCHIVED_RECORD:"Review the history of this closed application", REVIEW_RECORDED_OUTCOME:"Review the recorded outcome and decide what to do next", mailNote:"Import only an EML file you choose. Its sender and date are not mailbox-authenticated. Manual links preserve evidence without changing application execution state.", chooseMail:"Choose EML file (up to 2 MiB)", importMail:"Import file", refresh:"Refresh", mailEmpty:"No status messages yet", unknown:"Uncertain", received:"Application received", rejected:"Rejected", interview:"Interview invitation", link:"Link manually", reject:"Dismiss link", undo:"Undo review", confirm:"I checked the message, selected application and meaning of this status", select:"Choose application", pending:"Needs human review", linked:"Manually linked", ignored:"Dismissed", dateHeader:"Date from email file (unverified)", source:"Compare original page", prev:"Previous page", following:"Next page", page:"Page", sourceNote:"Check facts against the original. Region boxes are extraction proposals, not proof of accuracy.", hideSource:"Close original", busy:"Reading local records…", saved:"Evidence link saved. Application execution state is unchanged.", failed:"Could not complete this action. Refresh or check the file.", stale:"The inbox changed. Refresh and review it again.", confirmFirst:"Choose an application and confirm you checked the evidence.", sourceFailure:"Could not preview this page. Check the original file.", demoImport:"The synthetic demo does not accept user email files.", fields:"Fields needing confirmation", VERIFY_WITH_EMPLOYER_NO_RETRY:"Verify the employer's result before any further action; do not resubmit", REVIEW_STATUS_MESSAGES:"Review updates and prepare for the next stage", RESOLVE_REQUIRED_FIELDS:"Open the review packet and resolve required fields", REVIEW_APPLICATION_PACKET:"Review materials and answers before approving", CONTINUE_USER_PRESENT_SESSION:"Start browser assistance and inspect the current page", REVIEW_PREPARATION_BLOCKER:"Review the blocker and last completed step"}
  };
  Object.assign(copy.zh,{zoomSource:"放大原图",fitSource:"适应宽度"});
  Object.assign(copy.en,{zoomSource:"Zoom original",fitSource:"Fit width"});
  const label = key => copy[state.locale === "en" ? "en" : "zh"][key] || key;
  const esc = value => escapeHtml(String(value ?? "—"));
  const post = (path, value={}) => api(path, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(value)});
  let workspace = null, inbox = null, noticeKey = null;
  const notice = key => {noticeKey=key;document.querySelector("#workbenchNotice").textContent=key?label(key):"";};
  const button = (action, text, attrs="") => `<button type="button" class="secondary compact" data-workbench-action="${action}" ${attrs}>${esc(label(text))}</button>`;
  const rows = items => items.length ? items.join("") : `<p>${esc(label("noRecords"))}</p>`;
  function renderWorkspace(){
    const panel=document.querySelector("#applicationWorkspacePanel");
    if(!workspace){panel.replaceChildren();panel.classList.add("hidden");return;}
    const w=workspace;
    panel.innerHTML=`<header class="subheading"><h3 tabindex="-1">${esc(w.job.title)} · ${esc(w.job.company)}</h3>${button("close-workspace","close")}</header>
      <p class="workbench-next"><strong>${esc(label("next"))}: ${esc(label(w.next_action))}</strong><br>${esc(queueStatusLabel(w.status))} · ${esc(label("fields"))}: ${w.required_field_count}</p>
      ${w.review_versions.length?`<button type="button" class="primary open-review-packet" data-id="${esc(w.application_id)}">${esc(label("openPacket"))}</button>`:""}
      <div class="workbench-grid"><section><h4>${esc(label("snapshots"))}</h4>${w.snapshots.length?w.snapshots.map(s=>`<details><summary>${esc(s.captured_at)} · ${esc(s.content_hash.slice(0,19))}${s.bound_to_review?` · ${esc(label("snapshotBound"))}`:""} · ${esc(label(s.availability==="HASH_VERIFIED"?(s.hash_basis==="CANONICAL_LF"?"snapshotNormalized":"snapshotVerified"):"unavailable"))}</summary><pre>${esc(s.availability==="HASH_VERIFIED"?s.text:label("unavailable"))}</pre></details>`).join(""):`<p>${esc(label("missing"))}</p>`}</section>
      <section><h4>${esc(label("materials"))}</h4><p>${esc(label("materialNote"))}</p>${rows(w.materials.map(m=>`<p>${esc(m.kind)} · <code>${esc(m.content_hash.slice(0,19))}</code><br><small>${esc(m.created_at)}${m.matches_application_resume?` · ${esc(label("bound"))}`:""}</small></p>`))}<h4>${esc(label("versions"))}</h4>${rows(w.review_versions.map(v=>`<p>v${v.packet_version} · ${esc(v.status)} · ${esc(v.created_at)}</p>`))}</section></div>
      <h4>${esc(label("timeline"))}</h4>${rows(w.timeline.map(e=>`<p>${esc(e.created_at)} · ${esc(queueStatusLabel(e.from_state))} → ${esc(queueStatusLabel(e.to_state))}</p>`))}`;
    panel.classList.remove("hidden");
  }
  function statusLabel(value){return label({APPLICATION_RECEIVED:"received",REJECTED:"rejected",INTERVIEW_INVITED:"interview"}[value]||"unknown");}
  function renderInbox(){
    const panel=document.querySelector("#statusInboxPanel");
    if(!inbox){panel.replaceChildren();panel.classList.add("hidden");return;}
    panel.innerHTML=`<header class="subheading"><h3 tabindex="-1">${esc(label("mailTitle"))}</h3>${button("close-inbox","close")}</header><p>${esc(label("mailNote"))}</p>
      <div class="mail-import"><label>${esc(label("chooseMail"))}<input id="statusMailFile" type="file" accept=".eml" ${state.data?.demo_mode?"disabled":""}></label>${button("import-mail","importMail",state.data?.demo_mode?"disabled":"")}${button("refresh-inbox","refresh")}</div>
      ${inbox.events.length?inbox.events.map(e=>`<article class="mail-event" data-mail-event="${esc(e.event_id)}"><h4>${esc(e.subject)}</h4><p><strong>${esc(label("statusProposal"))}: ${esc(statusLabel(e.status_proposal))}</strong> · ${esc(label(e.review_status==="USER_MANUALLY_ASSOCIATED"?"linked":e.review_status==="USER_LINK_REJECTED"?"ignored":"pending"))}</p><small>${esc(label("dateHeader"))}: ${esc(e.received_at)}</small><details><summary>${esc(label("messageText"))}</summary><pre>${esc(e.body)}</pre></details>
      <label>${esc(label("select"))}<select class="mail-application"><option value="">—</option>${inbox.applications.map(a=>`<option value="${esc(a.application_id)}" ${e.application_id===a.application_id?"selected":""}>${esc(a.title)} · ${esc(a.company)} · ${esc(a.application_id)}</option>`).join("")}</select></label>
      <label class="mail-confirm-label"><input class="mail-confirm" type="checkbox">${esc(label("confirm"))}</label><div class="mail-actions">${button("link-mail","link")}${button("reject-mail","reject")}${button("undo-mail","undo")}</div><small>${esc(e.evidence_sha256)}</small></article>`).join(""):`<p>${esc(label("mailEmpty"))}</p>`}`;
    panel.classList.remove("hidden");
  }
  function decorateSources(){
    document.querySelectorAll(".source-preview").forEach(root=>{
      if(root.querySelector(".source-review-open")){root.querySelector(".source-review-open").textContent=label("source");return;}
      const pending=state.data?.pending_sources?.find(p=>p.source_id===root.dataset.previewSource);
      if(![".pdf",".png",".jpg",".jpeg"].includes(pending?.metadata?.extension))return;
      const controls=document.createElement("div");controls.className="source-review-controls";
      controls.innerHTML=button("source-page","source",'data-page="1"');
      controls.querySelector("button").classList.add("source-review-open");root.querySelector("header").after(controls);
      const candidates=root.querySelector(".preview-candidates"), comparison=document.createElement("div");comparison.className="source-comparison";
      candidates.before(comparison);const viewer=document.createElement("div");viewer.className="source-original hidden";comparison.append(viewer,candidates);
    });
  }
  async function showSource(root,page){
    const result=await post("source-review-page",{source_id:root.dataset.previewSource,page});
    if(!root.isConnected)return;
    const viewer=root.querySelector(".source-original");
    viewer.innerHTML=`<p>${esc(label("sourceNote"))}</p>${button("zoom-source","zoomSource",'aria-pressed="false"')}<div class="source-image-viewport" tabindex="0" role="region" aria-label="${esc(label("source"))}"><div class="source-page-image"><img alt="${esc(label("source"))} ${result.page}" src="${esc(result.image_data_url)}"><svg viewBox="0 0 1 1" preserveAspectRatio="none" aria-hidden="true">${result.regions.map(r=>`<rect x="${r.bbox[0]}" y="${r.bbox[1]}" width="${r.bbox[2]-r.bbox[0]}" height="${r.bbox[3]-r.bbox[1]}"/>`).join("")}</svg></div></div><p>${esc(label("page"))} ${result.page} / ${result.page_count}</p><div class="source-page-controls">${button("source-page","prev",`data-page="${result.page-1}" ${result.page===1?"disabled":""}`)}${button("source-page","following",`data-page="${result.page+1}" ${result.page===result.page_count?"disabled":""}`)}${button("hide-source","hideSource")}</div>`;
    viewer.classList.remove("hidden");root.querySelector(".source-comparison").classList.add("show-original");
  }
  function decorate(){
    document.querySelectorAll("[data-workbench-label]").forEach(el=>el.textContent=label(el.dataset.workbenchLabel));
    document.querySelectorAll("#pendingDashboardList [data-application], #recentDashboardList .compact-dashboard-item").forEach(root=>{
      if(root.querySelector(".open-workspace")){root.querySelector(".open-workspace").textContent=label("openWorkspace");return;}
      const recentIndex=[...document.querySelectorAll("#recentDashboardList .compact-dashboard-item")].indexOf(root);
      const id=root.dataset.application||root.querySelector("[data-id]")?.dataset.id||state.data?.dashboard?.recent_applications?.[recentIndex]?.application_id;if(!id)return;
      const b=document.createElement("button");b.type="button";b.className="secondary compact open-workspace";b.dataset.workbenchAction="workspace";b.dataset.id=id;b.textContent=label("openWorkspace");root.append(b);
    });
    decorateSources();
  }
  document.addEventListener("click",async event=>{
    const control=event.target.closest("[data-workbench-action],#statusInboxOpen");if(!control)return;
    const action=control.dataset.workbenchAction||"refresh-inbox";
    if(control.disabled)return;
    control.disabled=true;notice("busy");
    try{
      if(action==="workspace"){workspace=await post("application-workspace",{application_id:control.dataset.id});renderWorkspace();document.querySelector("#applicationWorkspacePanel h3").focus();}
      else if(action==="close-workspace"){workspace=null;renderWorkspace();}
      else if(action==="close-inbox"){inbox=null;renderInbox();}
      else if(action==="refresh-inbox"){inbox=await post("status-inbox");renderInbox();document.querySelector("#statusInboxPanel h3").focus();}
      else if(action==="import-mail"){
        if(state.data?.demo_mode)throw {code:"DEMO_FILE_INTAKE_DISABLED"};
        const file=document.querySelector("#statusMailFile").files[0];if(!file||file.size>2*1024*1024||!file.name.toLowerCase().endsWith(".eml"))throw {code:"MAIL_FILE_INVALID"};
        await api("import-status-eml",{method:"POST",headers:{"Content-Type":"application/octet-stream"},body:file});inbox=await post("status-inbox");renderInbox();
      }else if(["link-mail","reject-mail","undo-mail"].includes(action)){
        const root=control.closest(".mail-event"), appId=root.querySelector(".mail-application").value;
        if(!root.querySelector(".mail-confirm").checked||(action==="link-mail"&&!appId))throw {code:"MAIL_CONFIRM_REQUIRED"};
        await post("review-status-message",{event_id:root.dataset.mailEvent,application_id:appId||null,decision:{"link-mail":"MANUAL_LINK","reject-mail":"REJECT_LINK","undo-mail":"CLEAR_REVIEW"}[action],user_confirmed:true,expected_review_ref:inbox.review_ref});
        inbox=await post("status-inbox");renderInbox();notice("saved");return;
      }else if(action==="source-page")await showSource(control.closest(".source-preview"),Number(control.dataset.page));
      else if(action==="zoom-source"){const zoomed=control.closest(".source-original").querySelector(".source-page-image").classList.toggle("source-zoomed");control.textContent=label(zoomed?"fitSource":"zoomSource");control.setAttribute("aria-pressed",String(zoomed));}
      else if(action==="hide-source"){const root=control.closest(".source-preview"),viewer=root.querySelector(".source-original");viewer.replaceChildren();viewer.classList.add("hidden");root.querySelector(".source-comparison").classList.remove("show-original");}
      notice(null);
    }catch(error){const key=error.code==="MAIL_REVIEW_STALE"?"stale":error.code==="MAIL_CONFIRM_REQUIRED"?"confirmFirst":action==="source-page"?"sourceFailure":error.code==="DEMO_FILE_INTAKE_DISABLED"?"demoImport":"failed";notice(key);showToast(label(key),true);}
    finally{control.disabled=false;}
  });
  window.JobFlowWorkbench={decorate, locale(){decorate();renderWorkspace();renderInbox();notice(noticeKey);document.querySelectorAll(".source-original").forEach(viewer=>{viewer.replaceChildren();viewer.classList.add("hidden");viewer.parentElement.classList.remove("show-original");});}};
  decorate();
})();
