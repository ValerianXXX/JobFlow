# Adopted standard-master handoff — November 1, 2026

## The user-visible failure

A continuous synthetic browser journey started with zero sources and Claims,
imported the pinned Chinese PDF, completed the questionnaire and review, chose
two of five confirmed facts, and generated and visually inspected the actual
one-page PDF. It adopted the resulting DOCX as revision 2, edited a salary
preference, completed review again and renewed material and tailoring permission.

The UI then reported `READY_FOR_OFFLINE_APPLICATION_PREPARATION`, but the actual
offline request failed with HTTP 400 `APPLICATION_PRIVATE_REFERENCE_INVALID`.
No application, application approval or external action was created. The
application engine accepted dedicated/uploaded masters but rejected the
`generated_resume_docx` kind produced by the adoption workflow. Separate earlier
tests of drafting and application preparation had not established this handoff.

The frozen protocol is [task-card.json](../evaluations/2026-11-01/first-use-revision/task-card.json).
Its real local HTTP, Windows DPAPI and native PDF conversion are separate from
the pinned document-response replay and deterministic fake application adapter.
The complete first failure is retained; no live inference or employer request
is used to obtain a passing result.

## What changed

The application engine now accepts a generated master only when all of these
existing records agree: the current completed state, current Claim approval,
exact DOCX reference/hash, template fingerprint, explicit adoption receipt,
historical generation state/Profile/Claim receipt, and retained original PDF.
An arbitrary generated application file is not a master. Wrong kinds, revoked
or synthetic references, changed hashes, missing adoption, unrelated source
bindings and incomplete states remain blocked.

The old generation approval is a historical receipt. It can outlive its original
permission window; today's material use still requires a separate current,
unexpired approval. Original source citations continue to identify the original
PDF, including older master-source citations. Existing adopted revisions use
this read-only compatibility path; no state or database migration is required.

## Two further failures found in final materials

The next native run progressed past master admission but stopped on Cover
Letter QA. A separate observation retained the real DOCX/PDF/page inputs before
ordinary failure cleanup. Visual and text inspection found a preserved source
URL wrapped after `analyst-`; the old paragraph check treated the two line
fragments as a changed word sequence. The frozen native fixture and additional
order/number/citation cases preserve this failure. The word-count tolerance,
page limit, placeholder scan and visual-evidence requirements are unchanged.
Reading-order comparison now retains punctuation as individual ordered atoms.
It permits wrapping at an existing separator while refusing changed separators,
reordered path segments and whitespace-joined words/digits. The same native
fixture retains its measured 0.0168 text-difference ratio; no threshold is raised.

That browser run reached the packet and restored it successfully, but actual
visual inspection found the same approved fact twice in the tailored resume.
The browser PASS and separate material-review FAIL are both retained. The
ranker was comparing a tuple's score to its set of used Claim IDs. Named ranking
fields now make the distinction explicit; IDs and exact wording cannot be reused
within the replacement plan, and candidates must fit the approved position.
Exhausted choices leave other positions untouched rather than inventing facts.
This does not remove pre-existing duplicate wording in an applicant's master.
Protocol V2 keeps the same task inputs and additionally checks two distinct
packet facts and one occurrence of each in the actual final DOCX.

## Verification evidence

The [verification receipt](../evaluations/2026-11-01/first-use-revision/verification.json)
binds the runtime, unsigned wheel and retained files by SHA-256. The five runs
repeat one synthetic task; they are not five independent users or AI samples.

| Observation | Workflow | Final-material inspection |
| --- | --- | --- |
| `baseline-20261101` | Blocked at adopted-master admission | No final packet |
| `admission-fixed-20261101` | Blocked by native Cover Letter reading-order QA | Not accepted |
| `native-qa-capture-20261101` | Same failure, with original native inputs retained | URL wrapping inspected; original QA failure retained |
| `final-20261101` | Reached and restored `AWAITING_APPROVAL` | Failed: repeated resume fact |
| `distinct-facts-20261101` | Reached and restored `AWAITING_APPROVAL` | Assistant inspected both actual native PDF pages; two final resume facts each appear once in the DOCX |

The last run had zero application approvals, external-action attempts, external
browser requests or page errors. Its temporary synthetic store was removed.
Desktop and 390-pixel mobile views are retained. This supports the bounded local
workflow and final-file checks, not live submission or persuasive letter quality.
The replay letter repeats an opening fact in its evidence summary; its writing
quality is outside this structural check and remains a real-model acceptance item.

Relevant checks passed: 21 adoption/citation/reading-order tests, 9 distinct-fact/
manifest tests, 13 selection/readiness tests and 5 public-documentation tests.
These are separate commands, not a newly completed full suite. An earlier
55-test command failed with one historical-fixture chronology problem and two
nonexistent requested module names; its log is retained alongside the corrected
commands. The eight-method admission baseline, five-method distinct-fact baseline
and actual native QA failure are retained rather than relabeled as passes.

The source audit passed. No real knowledge-vault comparison was possible because
location returned `KNOWLEDGE_ROOT_NOT_FOUND`. Current full local/hosted regressions
are separate gates; this receipt does not assert their success. The wheel contains
the four changed runtime files byte-for-byte and is not production-signed.

## Reproduce the continuous task

Use the normal development dependencies (`.[build,mcp,document]` and the existing
JavaScript lockfile). Windows DPAPI, Edge, Poppler and a configured native PDF
exporter are required. For LibreOffice, set `JOBFLOW_PDF_EXPORT_PROVIDER=libreoffice`
and `JOBFLOW_LIBREOFFICE_BINARY` to the trusted local `soffice.com` or executable.
Do not use a development signature as production trust. Word remains an existing
alternative; this observation uses LibreOffice.

Run `node evaluations/2026-11-01/first-use-revision/verify-browser.cjs` from the
checkout. `JOBFLOW_TEST_PYTHON` may select an already prepared interpreter;
otherwise the runner uses the checkout's `.venv/Scripts/python.exe`.
`JOBFLOW_OBSERVATION_ID` selects a new report directory and cannot overwrite an
existing result. Reports remain under the ignored `reports/first-use-revision`.

The runner pauses before adoption. Inspect every `standard-page-*.png`, then
write `standard-visual-review.json` with `synthetic_only: true`, reviewer
`ASSISTANT_VISUAL_REVIEW`, and the exact page/file/SHA-256 list with each actual
result and reason. The runner checks the binding and stops after 15 minutes if
review is absent. This test control does not supply a product approval or claim
independent human validation. Every product confirmation is an ordinary UI
interaction in the isolated synthetic account.

The synthetic service accepts only the pinned PDF and local job snapshots,
uses the existing source validator and application engine, never seeds an
approved Profile or application, and deletes its temporary store on exit.
Native final files exported by the test fixture are synthetic test evidence,
not a customer-facing material-download feature.

## Limits and next acceptance

This is one synthetic task, not a fresh AI-quality sample or independent user.
No model-recall, relevance, editing-effort, latency or cost improvement is claimed.
New cloud/model calls are zero; total task cost is not measured. Browser wall
time includes operator visual-review pauses and is not a model benchmark.

The main live Browser Companion route, live employer form compatibility,
authenticated mail, the 36-material recall target, independent first-user
acceptance, trusted production signing and clean Windows installation/upgrade/
rollback remain separate requirements. The packet UI currently displays wording,
source bindings and upload metadata; it does not yet offer an inline viewer or
download for the final job-specific files. The test fixture's artifact export
does not close that user-facing review gap.

## Recovery and rollback

Restart on this source version and retry the local preparation after reviewing
readiness. Existing adopted master/Profile/source bytes need no migration and
are not overwritten. A failed request that created no application does not grant
submission or retry authority for any external state.

This change adds no provider, dependency, production action or schema. A normal
reviewed code revert restores the prior rejection for generated masters, so it
also restores the original workflow blockage. Keep code and UI from a coherent
version, preserve the encrypted store and database together, and retain the
parent selection feature's downgrade constraints. Do not delete facts, rewrite
history or restore old approval flags to make an older build accept the master.
