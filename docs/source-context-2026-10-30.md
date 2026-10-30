# Source constraints and verification needs - October 30, 2026

JobFlow can retain unconfirmed constraints and evidence gaps separately from
resume Claim candidates. The coding assistant verified the local review and
storage workflow with synthetic data. This does not establish full-material
understanding or independent user acceptance.

The earlier six-material review remains **241/282 facts represented in 197 Claim
candidates**. Of 41 omissions, 12 identity/contact facts belong outside the Claim
channel and 29 need appropriate constraint or evidence-gap destinations. This
change supplies that destination; it does not retroactively improve those
observations or turn missing evidence into achievements.

## Behavior and boundaries

The existing document-analysis request accepts an optional `review_notes` array.
Each model annotation has exactly `kind`, `field_id`, `line_start`, and `line_end`.
The host copies the referenced analyzed-text excerpt, binds it to source identity,
text range and content hash, and rechecks the excerpt before encrypted persistence.
Model-written values, paraphrases, confidence and approval flags are rejected.

Supported advisory Profile destinations are remote preference, preferred locations,
availability, work authorization, visa sponsorship and minimum salary. Evidence
gaps have no answer destination. Other fields remain in the normal Profile review.
There are at most 64 retained notes per source, eight lines and 2,000 characters
per note. Oversize quotations are rejected rather than silently shortened.
Notes from separate chunks retain global analyzed-text line numbers. Oversize-line
segmentation reports unavailable context coordinates instead of using wrong ranges.

The preview and saved-source list show original wording, line ranges and a review
status in Chinese and English. A Profile button opens collapsed question groups
and focuses the existing answer control. It neither copies text nor changes the
answer, disclosure policy, Claim selection or approval. An unknown condition stays
unknown. Source confirmation saves notes, not the truth of their contents.
Evidence gaps remain read-only source reminders; this milestone does not implement
a separate resolved/dismissed workflow for them.

Re-analysis stages replacement notes until the user confirms the new preview.
Discard preserves saved notes. Deleting one source removes only its notes; old
state without notes remains readable. Private quotations live in the existing
encrypted onboarding state and authenticated loopback UI, outside diagnostic
summaries and ordinary SQLite records. Raw ChatGPT-export quotes are explicitly
excluded. All external-action and final-submit boundaries remain unchanged.

Line references describe analyzed text, not original PDF page coordinates. A
transcription error can therefore survive exact quote validation. Keep original
page comparison and visual verification. Empty notes, processed input and cited
lines never certify factual completeness or current eligibility.

## Configuration and architecture

No new dependency, provider, paid service, database migration or setting is added.
Existing Hermes and local-model routes are retained. If the configured route is
cloud-backed, source text still goes to that provider under the existing explicit
configuration; adding this output contract is not a local-only guarantee. Notes
share the document-analysis call rather than introducing another production call,
but response size and billed cost can change.

`source_context.py` owns bounded selection, host quotation, deduplication and
rebinding. `DocumentAnalysisResult` keeps the old two-value candidates/summary
interface while exposing a separate copied private payload. Legacy adapters that
return only the tuple still work and report context unavailable. The onboarding
service stages/saves this payload; the UI renders it without answer writes.

## Verification

- Nine initial contracts were committed before implementation at `480d996`.
  Baseline: eight failures and one missing-feature error. The unchanged nine
  passed after implementation.
- Eighteen context boundary/persistence tests pass in 5.106 seconds, covering
  forged values, invalid coordinates, Chinese unknowns, bounds, chunk offsets,
  mutable aliases, hash binding, legacy state, failed save, reanalysis and deletion.
  The first additional integration run had one test-fixture attribute error;
  correcting the fixture path preserved every assertion.
- The existing onboarding module passes 75/75 in 44.347 seconds. Eighteen existing
  attribution, Chinese grounding and numeric-grounding tests pass in 0.014 seconds.
- All 14 JavaScript/Edge suites pass. The new source-context contract exercises
  English/Chinese, 1280/390 viewport widths, keyboard navigation, collapsed fields,
  escaped untrusted HTML, retained notes, legacy/invalid metadata and unchanged
  answers, with no external requests.
- Browser-operated verification used actual loopback HTTP and Windows DPAPI,
  with a fixed synthetic model response. Three source excerpts survived confirm
  and page reload. Unknown work authorization stayed empty/unknown after a jump;
  reanalysis showed three pending and three retained notes, then discard removed
  only the pending notes. Console warnings/errors were absent. The owned server
  and temporary encrypted state were closed/removed. This is assistant-operated
  simulation, not independent human or live-model acceptance.

Use the README's per-worktree prerequisites, then:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_source_context*.py' -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_onboarding_center.py -v
npm run test:e2e
```

The controlled UI fixture is `tests/source_context_ui_server.py --stdio-control`.
It accepts no applicant path, starts only a loopback server and uses a fixed
synthetic response. Run it in an interactive terminal and send `STOP` to clean up.

## Frozen real-model follow-up

`SOURCE_CONTEXT_V1` is a distinct, fixed six-request protocol committed at
`fd4d4c1`. It reuses the six exposed synthetic analyzed texts with their original
hashes. Each case receives at most one request through the existing Hermes route;
repairs/retries cannot consume a second request. Two failed cases stop the rest.
The fixed exclusive journal reserves attempts before dispatch. It has no alternate
output or resume entry point; do not delete receipts to repeat the experiment.

This experiment measures document analysis on fixed transcripts. It does not
rerun image transcription, import original documents, update a Profile or submit
an application. Exact matches only nominate context excerpts for source review;
they are not an LLM judge or full semantic score. New Claim outputs also require
review before comparing them with the old 241/282 result. Model/provider internal
requests, token usage and billed cost stay unknown without provider receipts.

The [original observation](../evaluations/2026-10-30/source-context-live-v1.json)
closed after **two actual requests and two integration failures**; the other four
cases were not run. Both requests returned zero-tool responses, but the common
Agent/loopback chunk adapter discarded the optional context payload and returned
an ordinary tuple. The experimental collector failed with `AttributeError`; the
production service would instead have shown context unavailable. The earlier
local-subprocess tests had not covered this adapter path.

Five adapter tests were frozen at `3eb8ed0` and all five failed. After preserving
the private payload through the shared chunk helper, native/WSL Hermes wrappers
and loopback wrapper, the same five pass. The existing connection module passes
26/26 in 1.553 seconds. No response, budget, success status or original failure was
rewritten. The live protocol is closed; do not rerun it in another directory.

The [offline replay](../evaluations/2026-10-30/source-context-replay-v1.json)
uses those exact two responses and actual original synthetic PDFs. It verified
unchanged parser input, Windows DPAPI save, service reopen, matching retained
original bytes, unchanged six constraint answers, zero material Claims and zero
applications/approvals/external actions. It made **zero new model requests**.
Eight English and 14 Chinese notes persisted, taking 6.203 and 5.813 seconds in
this replay. These times are local pipeline observations, not fresh model latency.
The original adapter requests took 43.672 and 72.078 seconds. Billed cost,
provider-internal requests and token usage are unavailable.

## Source review: improvement and new omissions

The coding assistant reviewed all 94 source facts, 49 new candidates and 22 notes
in the two returned samples. The [decision ledger](../evaluations/2026-10-30/source-context-review-v1.json)
keeps Claim representation and exact context excerpts separate:

| Fixed transcript | Prior facts represented in Claims | New facts fully represented in Claims | Prior non-contact omission facts now retained as context | New missing/incomplete facts that were previously represented |
| --- | --- | --- | --- | --- |
| English chronological | 41 / 47 | 36 / 47 | 4 / 4 | 1 |
| Chinese chronological | 40 / 47 | 23 / 47 | 5 / 5 | 3 |

Nine previously missing constraint/evidence facts are now retained as unconfirmed
source excerpts. This is a measured retention improvement on these two exposed
samples. It is **not** overall quality acceptance: the new English output omits
the explicit completed-versus-proposed-work statement; the Chinese output loses
the complete individual-contributor/no-reports and full-time qualifiers, and the
statement that prototype addresses/business names are synthetic. The English
project name is present in wording but still absent from organization metadata.

Other qualifiers moved from Claim wording to separate source notes. For example,
the Chinese pilot and SQL Claims omit limitations still visible in their context
excerpts. A source note does not automatically constrain a later approved Claim;
users must review both. The broader notes can also duplicate source material and
increase review effort. That effort has not been measured with an independent
person. UI labels therefore describe source limits and verification needs,
rather than treating every note as proof of missing evidence.

Counting either a reviewed candidate or an exact context excerpt yields 44/47
English and 42/47 Chinese source facts present across those two channels. This
mixed-channel count is not comparable to Claim-only recall and is not a confirmed
Profile, approved application or whole-product score. No independent coder,
fresh holdout or statistical improvement claim is available.

Run the reproducible reference/denominator check with:

```powershell
.\.venv\Scripts\python.exe scripts/verify-source-context-review.py
.\.venv\Scripts\python.exe scripts/replay-source-context.py --output reports/my-context-replay.json
```

The first command verifies references and preserves the four new gaps; it does
not verify semantic judgments. Four negative/positive checker contracts pass.
The second uses the two pinned synthetic responses and actual local DPAPI; it
cannot contact a provider and refuses to overwrite an observation. Context
retention is implemented, but model completeness remains an open internal defect.
The next quality change must preserve complete entity facts and relevant limits
before compressing them into Claims, then use another frozen bounded comparison.

## Rollback and remaining work

The product implementation began at source commit `ab2f436`; use a normal reviewed revert
for rollback. No installed runtime, production signing, release channel or
database schema is changed. Back up encrypted application state through the
existing private-data procedure before any application downgrade; do not copy it
into Git. Older code will not display these notes. Restore the implementation to
review them again, or explicitly reanalyze retained sources under the desired
version; do not silently delete private state.

Outstanding work includes full 36-material/1,716-fact semantic acceptance,
independent review, complete profile handling, evidence-gap resolution, truthful
cost/latency measurement and real ATS/mailbox flows. Trusted production signing
and independent clean-Windows lifecycle acceptance remain unavailable. Neither
this source PR nor its tests authorize release.
