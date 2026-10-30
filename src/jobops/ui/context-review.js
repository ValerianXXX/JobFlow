"use strict";

(() => {
  const words = {
    en: {
      PENDING:"Needs review", PROFILE_REVIEWED:"Compared with the saved answer", PROFILE_UNKNOWN_REVIEWED:"Reviewed; answer still unknown",
      ANSWER_CHANGED:"Answer changed; review again", SOURCE_CHANGED:"Source analysis changed; review again", SOURCE_UNAVAILABLE:"Source cannot be verified",
      FOLLOW_UP:"Needs follow-up", DISMISSED:"Set aside with a reason", REOPEN:"Reopen for review",
      NEEDS_EVIDENCE:"More evidence needed", NEEDS_CLARIFICATION:"Applicant clarification needed", NOT_APPLICABLE:"Not applicable", OUTDATED:"Outdated", DUPLICATE:"Duplicate",
      savedAnswer:"Saved Profile answer", savedStatus:"Answer status", savedPolicy:"Use policy", history:"Saved decisions: {count}",
      open:"Review this excerpt", choose:"Choose a decision", reason:"Choose a reason", save:"Save review decision", saved:"Review decision saved. Profile answers and approvals are unchanged.",
      confirm:"I checked this excerpt and, where applicable, the saved answer shown above. I confirm this review decision.",
      boundary:"This records your review only. It does not confirm eligibility, resolve missing evidence, approve Claims or change Profile answers. Unsaved Profile edits are not saved here.",
      pending:"Confirm or discard the pending source analysis before reviewing this excerpt.", unavailable:"Review is unavailable for this source or completed Profile. Refresh the page to check its current state.",
      stale:"The source, saved answer or review changed. Save any Profile drafts, then refresh and review the current version.",
      failed:"Could not confirm that the review was saved. You can retry the same decision, or save your drafts and refresh. No Profile answer is changed by this action.",
      limit:"Private review history reached its capacity. Preserve it before continuing; existing records were not removed."
    },
    zh: {
      PENDING:"待核对", PROFILE_REVIEWED:"已对照保存的答案核对", PROFILE_UNKNOWN_REVIEWED:"已核对；答案仍未知",
      ANSWER_CHANGED:"答案已变更，需重新核对", SOURCE_CHANGED:"来源分析已变更，需重新核对", SOURCE_UNAVAILABLE:"无法核验来源",
      FOLLOW_UP:"仍需跟进", DISMISSED:"已注明理由并暂置", REOPEN:"重新打开核对",
      NEEDS_EVIDENCE:"需要更多证据", NEEDS_CLARIFICATION:"需要申请人补充说明", NOT_APPLICABLE:"不适用", OUTDATED:"已过期", DUPLICATE:"重复事项",
      savedAnswer:"已保存的资料答案", savedStatus:"答案状态", savedPolicy:"使用规则", history:"已保存的决定：{count} 次",
      open:"核对这条原文", choose:"选择核对决定", reason:"选择理由", save:"保存核对决定", saved:"核对决定已保存，资料答案和审批未改变。",
      confirm:"我已核对这条原文，并在适用时对照上方已保存的答案。我确认这项核对决定。",
      boundary:"这里只记录你的核对，不会确认资格、补齐证据、批准 Claim 或修改资料答案。资料页中尚未保存的编辑不会在此保存。",
      pending:"请先确认或放弃待处理的来源分析，再核对这条原文。", unavailable:"当前来源或已完成的资料不可核对。可刷新页面查看最新状态。",
      stale:"来源、已保存的答案或核对记录已变更。请先保存资料草稿，再刷新并核对当前版本。",
      failed:"未能确认核对决定是否保存。可重试相同决定，或保存草稿后刷新；此操作不会修改资料答案。",
      limit:"私有核对记录已达到容量上限，请先保留现有记录再继续。已有记录未被删除。"
    }
  };
  const reasons = {PROFILE_REVIEWED:[], FOLLOW_UP:["NEEDS_EVIDENCE","NEEDS_CLARIFICATION"], DISMISSED:["NOT_APPLICABLE","OUTDATED","DUPLICATE"], REOPEN:[]};
  const label = key => words[state.locale === "en" ? "en" : "zh"][key] || key;
  const hash = value => typeof value === "string" && /^sha256:[0-9a-f]{64}$/.test(value);
  const options = (keys, blank) => `<option value="">${escapeHtml(label(blank))}</option>` + keys.map(key=>`<option value="${key}">${escapeHtml(label(key))}</option>`).join("");
  const valid = view => view?.version === 1 && view.approved_for_external === false && view.automatic_answer_update === false && view.fact_completeness_verified === false;

  function savedAnswer(note) {
    const answer = state.savedContextAnswers?.[note.field_id];
    if (!answer) return "";
    const field = state.data.catalog.fields.find(item=>item.id===note.field_id);
    const display = value => field?.options?.find(item=>item.value===value)?.label?.[state.locale] || String(value ?? "");
    const value = Array.isArray(answer.value) ? answer.value.map(display).join(", ") : display(answer.value);
    const status = answer.source === "APPLICANT_PROVIDED_UNCONFIRMED" ? t("sourceContextUnconfirmed") : t(({UNKNOWN:"unknown",CONFIRMED:"confirmed",PREFER_NOT_TO_ANSWER:"preferNot",NOT_APPLICABLE:"notApplicable"})[answer.status] || "unknown");
    const policy = t(({reuse:"reuse",confirm_each_application:"confirmEach",prefer_not_to_answer:"preferPolicy",do_not_store:"doNotStore"})[answer.use_policy] || "unknown");
    return `<p class="context-saved-answer"><strong>${escapeHtml(label("savedAnswer"))}</strong>: ${escapeHtml(value || t("unknown"))}<br>${escapeHtml(label("savedStatus"))}: ${escapeHtml(status)} · ${escapeHtml(label("savedPolicy"))}: ${escapeHtml(policy)}</p>`;
  }

  function decorate() {
    for (const row of document.querySelectorAll("#sourceList [data-context-note]")) {
      row.querySelector(".context-decision-panel")?.remove();
      const note = state.data?.context_notes?.find(item=>item.note_id===row.dataset.contextNote);
      const view = state.data?.context_review, item = view?.notes?.[row.dataset.contextNote];
      if (!note || !valid(view) || !hash(item?.binding_sha256) || item.approved_for_external !== false || !(item.status in words.en)) continue;
      const status = row.querySelector(".context-review-status");
      status.textContent = label(item.status);
      status.dataset.reviewStatus = item.status;
      const available = item.available === true && !isReadonly() && (!note.field_id || Boolean(state.savedContextAnswers?.[note.field_id]));
      const decisions = note.kind === "profile_constraint" ? Object.keys(reasons) : ["FOLLOW_UP","DISMISSED","REOPEN"];
      const panel = document.createElement("div");
      panel.className = "context-decision-panel";
      panel.innerHTML = `${note.field_id?savedAnswer(note):""}<small>${escapeHtml(label("history").replace("{count}",String(item.history_count)))}${item.reason?` · ${escapeHtml(label(item.reason))}`:""}</small>
        ${!available?`<p class="context-review-unavailable">${escapeHtml(label(item.replacement_pending?"pending":"unavailable"))}</p>`:""}
        <details><summary>${escapeHtml(label("open"))}</summary><p>${escapeHtml(label("boundary"))}</p>
          <fieldset class="context-decision-controls"${available?"":" disabled"}><legend>${escapeHtml(label("choose"))}</legend>
            <label>${escapeHtml(label("choose"))}<select class="context-decision" aria-label="${escapeHtml(label("choose"))}">${options(decisions,"choose")}</select></label>
            <label class="context-reason-row" hidden>${escapeHtml(label("reason"))}<select class="context-reason" aria-label="${escapeHtml(label("reason"))}"></select></label>
            <label class="context-confirm-row"><input type="checkbox" class="context-confirm"><span>${escapeHtml(label("confirm"))}</span></label>
            <button type="button" class="secondary context-save" disabled>${escapeHtml(label("save"))}</button>
          </fieldset></details>`;
      row.append(panel);
    }
  }

  document.addEventListener("change", event => {
    const panel = event.target.closest(".context-decision-panel");
    if (!panel) return;
    const decision = panel.querySelector(".context-decision").value;
    const allowed = reasons[decision] || [];
    if (event.target.matches(".context-decision")) {
      panel.querySelector(".context-reason-row").hidden = !allowed.length;
      panel.querySelector(".context-reason").innerHTML = options(allowed,"reason");
      panel.querySelector(".context-confirm").checked = false;
    }
    panel.querySelector(".context-save").disabled = !decision || !panel.querySelector(".context-confirm").checked || (allowed.length && !allowed.includes(panel.querySelector(".context-reason").value));
  });
  document.addEventListener("click", async event => {
    const button = event.target.closest(".context-save");
    if (!button || button.disabled) return;
    const row = button.closest("[data-context-note]"), panel = button.closest(".context-decision-panel"), controls = panel.querySelector("fieldset");
    if (controls.disabled) return;
    const noteId = row.dataset.contextNote, item = state.data.context_review.notes[noteId];
    const payload = {note_id:noteId, expected_binding_sha256:item.binding_sha256, expected_review_id:item.review_id,
      expected_answer_sha256:item.answer_sha256, decision:panel.querySelector(".context-decision").value,
      reason:panel.querySelector(".context-reason").value, user_confirmed:panel.querySelector(".context-confirm").checked};
    controls.disabled = true;
    try {
      const result = await api("source-context-review", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
      if (result.status !== "SOURCE_CONTEXT_REVIEW_RECORDED" || result.answers_changed !== false || !valid(result.context_review)) throw new Error("Invalid review response");
      state.data.context_review = result.context_review;
      // Updating only these controls preserves unsaved Profile/Claim drafts and
      // selections in unrelated pending sources. No implicit save or refresh.
      decorate();
      row.querySelector(".context-decision-panel summary")?.focus();
      showToast(label("saved"));
    } catch (error) {
      controls.disabled = false;
      const key = error.code === "SOURCE_CONTEXT_REVIEW_STALE" ? "stale" : error.code === "SOURCE_CONTEXT_HISTORY_LIMIT" ? "limit" : "failed";
      showToast(label(key),true,12000);
    }
  });
  window.JobFlowContextReview = {decorate};
  decorate();
})();
