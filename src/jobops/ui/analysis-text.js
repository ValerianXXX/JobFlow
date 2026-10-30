"use strict";

(() => {
  const words = {
    en: {open:"Review analysis text", title:"Saved text used for this analysis", close:"Close and clear", previous:"Previous lines", next:"Next lines", jump:"Go to line", line:"Line number", loading:"Reading saved text…", range:"Showing lines {first}–{last} of {total}", note:"These are the extracted text lines sent for analysis, including blank lines. They may contain extraction errors. Compare the original file to verify layout, facts and omissions. Opening this view does not confirm any facts.", missing:"Saved analysis text is unavailable for this source. Check the original file; re-analysis is a separate action.", failure:"Could not verify these saved lines. Refresh the source review, or check the original file.", stale:"This analysis changed. Close this view and refresh the source review.", citation:"View cited lines", empty:"Blank line", target:"Requested lines", limit:"Enter a line number within this source."},
    zh: {open:"核对分析原文", title:"本次分析使用的已保存文本", close:"关闭并清除", previous:"上一段", next:"下一段", jump:"跳转到行", line:"行号", loading:"正在读取已保存文本…", range:"显示第 {first}–{last} 行，共 {total} 行", note:"这里显示当次分析收到的提取文本，包含空行，也可能包含提取错误。请对照原文件核查版式、事实和遗漏。打开此视图不会确认任何事实。", missing:"此来源没有可用的分析文本快照。请核查原文件；重新分析需要单独操作。", failure:"无法核验已保存文本。请刷新来源审阅，或核查原文件。", stale:"此来源的分析已更新。请关闭此视图并刷新来源审阅。", citation:"查看引用行", empty:"空行", target:"请求核对的行", limit:"请输入此来源范围内的行号。"}
  };
  const label = key => words[state.locale === "en" ? "en" : "zh"][key];
  const hash = value => typeof value === "string" && /^sha256:[0-9a-f]{64}$/.test(value);
  const rangeValid = (start, end, total) => Number.isSafeInteger(start) && Number.isSafeInteger(end) && start > 0 && end >= start && end <= total;
  const available = value => value?.status === "AVAILABLE" && value.fact_completeness_verified === false && hash(value.source_sha256) && hash(value.text_sha256) && Number.isSafeInteger(value.total_lines) && value.total_lines > 0;
  const dialog = document.createElement("dialog");
  dialog.className = "analysis-text-dialog";
  dialog.setAttribute("aria-labelledby", "analysisTextTitle");
  document.body.append(dialog);
  let active = null, generation = 0, returnFocus = null, previous = [], current = null;

  function makeButton(text, start, end, total) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "text-action analysis-text-open";
    button.textContent = text;
    if (rangeValid(start, end, total)) {
      button.dataset.analysisStart = String(start);
      button.dataset.analysisEnd = String(end);
    }
    return button;
  }

  function decorate() {
    const entries = [...document.querySelectorAll(".source-entry")].map((root, i) => ({root, source:state.data?.sources?.[i], preview:null}));
    for (const root of document.querySelectorAll(".source-preview")) {
      const preview = state.data?.pending_sources?.find(item => item.source_id === root.dataset.previewSource);
      entries.push({root, source:preview?.metadata, preview});
    }
    for (const {root, source, preview} of entries) {
      if (!source || root.querySelector(".analysis-text-controls")) continue;
      const descriptor = source.analysis_text_review;
      const controls = document.createElement("div");
      controls.className = "analysis-text-controls";
      root.dataset.analysisSource = preview?.source_id || source.source_id;
      if (!available(descriptor)) {
        controls.textContent = label("missing");
      } else {
        controls.append(makeButton(label("open"), 1, 1, descriptor.total_lines));
        const coverage = preview?.extraction_summary?.source_reference_coverage;
        const details = root.querySelector(".source-reference-coverage details");
        if (details && coverage?.status === "LINE_REFERENCES_ONLY") {
          const links = document.createElement("div");
          links.className = "analysis-gap-links";
          for (const item of (Array.isArray(coverage.uncited_line_ranges) ? coverage.uncited_line_ranges : []).slice(0, 40)) {
            if (rangeValid(item?.line_start, item?.line_end, descriptor.total_lines)) {
              links.append(makeButton(`${label("jump")} ${item.line_start}–${item.line_end}`, item.line_start, item.line_end, descriptor.total_lines));
            }
          }
          details.append(links);
        }
        (preview?.candidates || []).forEach((candidate, index) => {
          const citation = candidate.provenance;
          const row = root.querySelectorAll(".preview-candidate")[index];
          if (row && rangeValid(citation?.line_start, citation?.line_end, descriptor.total_lines)) {
            row.querySelector(".preview-meta").append(makeButton(label("citation"), citation.line_start, citation.line_end, descriptor.total_lines));
          }
        });
        const notes = preview?.context_notes || (state.data?.context_notes || []).filter(item => item.source_id === source.source_id);
        // Only link a rendered note after matching its quotation and validated range.
        for (const row of root.querySelectorAll(".source-context li")) {
          const note = notes.find(item => item.quote === row.querySelector("blockquote")?.textContent);
          if (rangeValid(note?.line_start, note?.line_end, descriptor.total_lines)) {
            row.append(makeButton(label("citation"), note.line_start, note.line_end, descriptor.total_lines));
          }
        }
      }
      const header = root.querySelector("header");
      if (header) header.after(controls); else root.append(controls);
    }
  }

  function clear() {
    generation++;
    active = null; current = null; previous = [];
    dialog.replaceChildren();
    if (returnFocus?.isConnected) returnFocus.focus();
    returnFocus = null;
  }
  dialog.addEventListener("close", clear);
  function controls() {
    dialog.querySelector(".analysis-prev").disabled = !current || (!previous.length && current.first_line === 1);
    dialog.querySelector(".analysis-next").disabled = !current || !current.next_line;
    dialog.querySelector(".analysis-jump button").disabled = !current;
  }
  async function read(start) {
    const revision = ++generation, request = active;
    current = null; controls();
    dialog.querySelector(".analysis-lines").replaceChildren();
    const status = dialog.querySelector(".analysis-text-status");
    status.textContent = label("loading");
    try {
      const result = await api("source-review-text", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({source_id:request.id, expected_source_sha256:request.descriptor.source_sha256, expected_text_sha256:request.descriptor.text_sha256, line_start:start})});
      if (revision !== generation || !dialog.open || active !== request) return;
      if (result.source_id !== request.id || result.source_sha256 !== request.descriptor.source_sha256 || result.text_sha256 !== request.descriptor.text_sha256 || result.fact_completeness_verified !== false || result.total_lines !== request.descriptor.total_lines || result.first_line !== start || !Array.isArray(result.lines) || !result.lines.length || result.lines.length > 100 || result.last_line !== start + result.lines.length - 1 || result.last_line > result.total_lines || result.next_line !== (result.last_line < result.total_lines ? result.last_line + 1 : null) || result.lines.some((line, i) => line.number !== start + i || typeof line.text !== "string") || result.lines.reduce((sum, line) => sum + line.text.length, 0) > 65536) throw new Error("Invalid saved text response");
      // JS counts UTF-16 code units; the host caps 32,768 Unicode code points.
      const target = dialog.querySelector(".analysis-lines");
      for (const line of result.lines) {
        const row = document.createElement("div"), number = document.createElement("span"), text = document.createElement("span");
        row.className = "analysis-line";
        row.dataset.line = String(line.number);
        if (line.number >= request.start && line.number <= request.end) row.classList.add("requested-line");
        number.className = "analysis-line-number"; number.textContent = String(line.number);
        text.className = "analysis-line-text"; text.textContent = line.text || "\u00a0";
        if (!line.text) text.setAttribute("aria-label", label("empty"));
        row.append(number, text); target.append(row);
      }
      current = result; controls();
      status.textContent = label("range").replace("{first}", result.first_line).replace("{last}", result.last_line).replace("{total}", result.total_lines);
      target.focus();
    } catch (error) {
      if (revision !== generation || !dialog.open || active !== request) return;
      status.textContent = label(error.code === "SOURCE_TEXT_REVIEW_STALE" ? "stale" : "failure");
    }
  }
  function open(button) {
    const root = button.closest("[data-analysis-source]"), id = root?.dataset.analysisSource;
    const pending = state.data?.pending_sources?.find(item => item.source_id === id);
    const source = root?.classList.contains("source-preview") ? pending?.metadata : state.data?.sources?.find(item => item.source_id === id);
    const descriptor = source?.analysis_text_review;
    if (!available(descriptor)) return;
    const start = Number(button.dataset.analysisStart), end = Number(button.dataset.analysisEnd);
    if (!rangeValid(start, end, descriptor.total_lines)) return;
    returnFocus = button; previous = []; active = {id, descriptor, start, end};
    dialog.innerHTML = `<header><h3 id="analysisTextTitle"></h3><button type="button" class="secondary analysis-close"></button></header><p class="analysis-text-note"></p><p class="analysis-text-status" role="status" aria-live="polite"></p><div class="analysis-lines" tabindex="0" role="region"></div><footer><button type="button" class="secondary analysis-prev"></button><button type="button" class="secondary analysis-next"></button><form class="analysis-jump"><label><span></span><input type="number" min="1" step="1" required></label><button type="submit" class="secondary"></button></form></footer>`;
    dialog.querySelector("h3").textContent = label("title");
    dialog.querySelector(".analysis-close").textContent = label("close");
    dialog.querySelector(".analysis-text-note").textContent = label("note");
    dialog.querySelector(".analysis-lines").setAttribute("aria-label", label("title"));
    dialog.querySelector(".analysis-prev").textContent = label("previous");
    dialog.querySelector(".analysis-next").textContent = label("next");
    dialog.querySelector(".analysis-jump span").textContent = label("line");
    dialog.querySelector(".analysis-jump button").textContent = label("jump");
    dialog.querySelector("input").max = String(descriptor.total_lines);
    dialog.querySelector("input").value = String(start);
    dialog.showModal();
    read(Math.max(1, start - 3));
  }
  document.addEventListener("click", event => {
    const button = event.target.closest(".analysis-text-open");
    if (!button) return;
    event.preventDefault(); event.stopPropagation(); open(button);
  });
  dialog.addEventListener("click", event => {
    if (event.target.closest(".analysis-close")) dialog.close();
    else if (event.target.closest(".analysis-next") && current?.next_line) {
      const next = current.next_line; previous.push(current.first_line); read(next);
    } else if (event.target.closest(".analysis-prev") && current) read(previous.length ? previous.pop() : Math.max(1, current.first_line - 100));
  });
  dialog.addEventListener("submit", event => {
    event.preventDefault();
    const value = Number(dialog.querySelector("input").value);
    if (!current || !rangeValid(value, value, active.descriptor.total_lines)) return;
    active.start = value; active.end = value; previous = []; read(Math.max(1, value - 3));
  });
  window.JobFlowAnalysisText = {decorate, locale() {if (dialog.open) dialog.close(); decorate();}};
  decorate();
})();
