# JobFlow next upgrade plan — October 29, 2026

Status: engineering scope and acceptance targets. The [first implementation batch](standard-resume-2026-10-29.md) covers parser isolation and the PDF-master standard-layout entry. Later milestones remain proposed, not completed features or permission to publish. This plan follows the
[browser-operated acceptance review](user-acceptance-2026-10-29.md) at
`911f8c1ebbe48787c6ef191ab0563a9cf3b9f4ae` (draft #9).

Implementation follow-up: [local LibreOffice export and reading-order QA](local-pdf-export-2026-10-29.md) now cover the optional Word-free conversion part of M1 on the existing Windows workstation. Six paired synthetic layouts and actual browser adoption were checked. This is not the planned 36-material extraction study, independent clean Windows acceptance or full M1 completion.

Further M1 work: [local PDF regions and CJK validation](pdf-regions-and-cjk-2026-10-29.md)
fix an uncased-script rejection and improve text-column entity context on 36
short diagnostic probes. These probes do not replace the full-material corpus
below. A real scanned-Chinese follow-up still misread dates, numbers, organization
and responsibility, so scan accuracy and the 95% fact target remain open. Compare
resolution and independent local OCR on those retained failures before claiming
broader material understanding. Original-source review now supports zoom/fit and
collapsible position details; approval boundaries remain unchanged.

The expanded product-quality goal is now active. Its first [scan-resolution and
upload-recovery follow-up](scan-resolution-2026-10-29.md) retains all earlier
failures, compares the same scan bytes with four real Hermes requests and an
independent local OCR probe, and raises the opt-in visual PDF default to 220 DPI.
Both higher-resolution transcripts retained eight designated lines; downstream
Claim correctness and complete-material acceptance remain open. Continue with
independent-template full materials and provenance/contradiction handling before
calling M1 complete. The scope also includes gradual domain/interface refactoring,
recoverable workflows and separate human acceptance; it is not just dependency
or model-version replacement.

The next product milestone is a usable assisted application: import materials,
review reusable facts, produce a checked resume, prepare one chosen job,
review and fill its supported form, let the user submit, and retain verifiable
progress. Preserve Hermes, local storage, source evidence and explicit approval.
It does not require a new Agent framework or replacing Browser Companion.

## What is missing, and what already exists

| Gap | Existing implementation to retain | Required improvement |
| --- | --- | --- |
| PDF-only users cannot supply the required editable master | `document_builder.build_resume`, immutable DOCX masters, `resume_tailoring.py`, Claim approval | Add a standard-template creation path from approved facts; keep existing-layout tailoring as a separate choice |
| PDF export has an external desktop dependency | `export_docx_to_pdf` calls the Word COM script; document QA already checks rendered pages | Detect Word availability, test an optional local LibreOffice converter, and explicitly report unsupported export rather than silently losing layout |
| Model evidence is narrow | Real Hermes visual preview, source regions, 30-material extraction comparison and fixed numeric-fact evaluation | Broaden bilingual material cases and evaluate final facts and artifacts, not just transcription |
| A historical parser test process exited without a traceback | Production Docling already runs in a bounded subprocess; one test calls the native parser directly | Diagnose the actual failing path, preserve its evidence and make failure reporting robust; do not invent a second parser service |
| Current live ATS acceptance is missing | Companion adapters, per-page rebinding, approval gates and `LiveAcceptanceManager` already exist | Validate selected real routes; connect recovery and field explanations to a complete user journey |
| Mail is local-file review only | `ReadOnlyStatusAdapter`, `ApplicationStatusInbox`, reversible EML association | Add provider cursors and a disabled-by-default, user-initiated OAuth read-only connector; retain the EML path |
| Hermes tools have transport verification, not model-driven task completion | Three existing redacted read-only MCP tools | Test the configured Hermes model selecting the right tools and explaining the next required UI action |
| Installation is not generally available | Compatibility matrix, complete-runtime builder, signing handoff, health and rollback code | Supply trusted publisher and independent Windows acceptance evidence; this is an external gate, not a missing approval Boolean |

## References checked on October 29

These are sources of implementation patterns or candidates to evaluate. No
third-party implementation is copied by this plan, no new dependency is
installed, and vendor-reported performance is not a JobFlow result.

| Reference | Current primary evidence | Decision for JobFlow |
| --- | --- | --- |
| Reactive Resume | [Version-history source](https://github.com/reactive-resume/reactive-resume/blob/main/packages/api/src/features/resume/version-history.ts), [MIT license](https://github.com/reactive-resume/reactive-resume/blob/main/LICENSE) | Borrow the distinction between working versions, named versions and the exact version used for an application. Retain JobFlow's encrypted source and Claim links. Do not replace its stack with the whole resume product. |
| JSON Resume | [Current schema](https://github.com/jsonresume/jsonresume.org/blob/master/packages/schema/schema.json), [package and MIT declaration](https://github.com/jsonresume/jsonresume.org/blob/master/packages/schema/package.json) | Use a versioned interoperability projection for approved facts and templates. Do not replace stricter internal evidence validation with the schema's permissive fields. The old `jsonresume/resume-schema` repo [has moved](https://github.com/jsonresume/resume-schema/blob/master/README.md); follow the monorepo. |
| PaddleOCR | [Official project](https://github.com/PaddlePaddle/PaddleOCR), [Apache-2.0 license](https://github.com/PaddlePaddle/PaddleOCR/blob/main/LICENSE) | Evaluate a bounded local OCR/layout worker against existing extraction and Hermes vision, especially for Chinese and mixed-language scans. Select the smallest workable component. Verify runtime, weights, licenses and resource needs before integration; no default switch based on vendor benchmarks. |
| Stagehand | [Observe API](https://docs.stagehand.dev/v4/basics/observe), [observation service source](https://github.com/browserbase/stagehand/blob/main/packages/extension/services/observeService.ts), [MIT license](https://github.com/browserbase/stagehand/blob/main/LICENSE) | Keep observation, proposed actions and execution separate. Reuse only validated observations; changed page identity invalidates them. Study recovery and cache patterns while retaining the current companion executor and authorization. |
| Simplify Copilot | [Official autofill workflow](https://help.simplify.jobs/help/articles/2415391-use-copilot-to-autofill-applications) | Product-flow reference: pick the exact resume, fill, inspect missing fields, submit personally, then check receipt. Its documented product flow is not our benchmark, source code, or evidence of universal compatibility. |
| LibreOffice | [Official conversion filters](https://help.libreoffice.org/latest/en-US/text/shared/guide/convertfilters.html) | Candidate offline DOCX-to-PDF converter for machines without Word. Conversion support alone does not prove layout equivalence; evaluate per-page output before enabling it. |
| Gmail API / Microsoft Graph | [Gmail synchronization](https://developers.google.com/workspace/gmail/api/guides/sync), [Gmail scopes](https://developers.google.com/workspace/gmail/api/auth/scopes), [Graph message delta](https://learn.microsoft.com/en-us/graph/api/message-delta), [Graph permissions](https://learn.microsoft.com/en-us/graph/permissions-reference) | Use provider-native incremental cursors and delegated read-only access. Gmail `gmail.readonly` is restricted; Graph `Mail.ReadBasic` excludes body/attachments. Do not promise body classification using metadata-only permission. |
| Microsoft Artifact Signing | [Overview](https://learn.microsoft.com/en-us/azure/artifact-signing/overview), [setup and eligibility](https://learn.microsoft.com/en-us/azure/artifact-signing/quickstart) | Candidate trusted Windows code-signing provider, subject to the owner's account, identity, eligibility and cost approval. Authenticode signing does not replace JobFlow's signed update manifest, pinned verification key or complete runtime-closure attestation. |

Inspected Git blob identities: Reactive Resume `version-history.ts`
`480955eb132511903301fa72724370dfedb542c7`; JSON Resume `schema.json`
`81932bc1322a8acf39ad0238becc11169238f36c` and package manifest
`e05f3a30e19ad95cc7f2088f69b943e643b5bd3f` (declares 1.7.0);
Stagehand `observeService.ts` `9d042ffa191a2af222d8ed4b0094c457c8db8086`;
PaddleOCR README `d3a9c16b6b5672a872d6ad7473ff52aceb7712bb`.
These are file blob IDs, not repository commit IDs. Pin the selected repository
commit/package/weights and check licenses again before code reuse.

## Delivery order

Estimates below are engineering effort for one contributor, not guaranteed
calendar dates. External accounts, permitted live trials, participants,
signing and Windows machines are excluded. Each row produces a separate
reviewable milestone; a blocked external trial does not stop independent work.

| Milestone | Effort | Concrete work | Exit evidence |
| --- | --- | --- | --- |
| M0 — stable acceptance baseline | 1–2 days | Finish #9 checks; isolate the earlier optional-parser exit; add bounded fixed-label failure diagnostics; inventory Word/renderer/Hermes/companion prerequisites | Exact-head test record; reproducible failure or explicitly unresolved optional-parser limitation; setup reports the action needed for each missing prerequisite |
| M1 — materials to a usable resume | 4–6 days | Standard-template onboarding when only PDF/images exist; reuse approved facts and existing generator; DOCX/PDF preview and version binding; optional Word-free conversion; bilingual fixed corpus | Both existing-DOCX and PDF-only paths produce reviewable artifacts; factual/visual QA below passes; optional OCR is adopted only on measured benefit |
| M2 — verified assisted application | 4–6 days | Prioritize Greenhouse and Lever routes, then extend evidence for Ashby, SmartRecruiters and Workday; surface per-field source/status, selected file and next action; test interruptions and resumption | Local journey/recovery tests plus separately recorded user-present live route evidence; no claim that one route certifies an entire provider |
| M3 — useful status recovery | 3–5 days for one provider | Extend the adapter with opaque cursor and idempotent event identity; implement one OAuth read-only provider behind opt-in; match application references, roles and sender evidence; review conflicts and undo | Synthetic classification/matching matrix; contract tests; actual connector results only after account authorization. Provider two follows only after provider one is accepted |
| M4 — novice and Hermes completion | 2–3 days plus participant availability | Model-driven use of existing read-only Hermes tools; bilingual guided journey; five novice pilot sessions on synthetic applications; fix the largest repeated obstacles | Separate tool success, user task completion, corrections, interventions, latency and cost; operator simulation and independent human observations kept separate |
| R — signing and Windows acceptance, prepared during M0 | 2–3 days after external prerequisites exist | Freeze release tree, produce complete runtime and manifest, use approved trusted publisher, then test independent clean Windows install/upgrade/data preservation/recovery | Exact signed-artifact hashes and publisher evidence; accepted compatibility matrix; clean-Windows receipts; release stays blocked until evidence and owner approval |

M0 starts first. Prepare R's checklist and handoff immediately; confirmed
external blockers remain. M1 is the first substantial product change because
a correct form filler is not useful if the user cannot produce the required
resume. M2 consumes M1's approved artifact. M3 can proceed on synthetic/contract
work while live M2/R acceptance waits. M4 judges the assembled experience.

## M0: stop treating repeated passes as diagnosis

Baseline #8 at `3e232d7` passed its complete three-version matrix. The
historical #7 Python 3.13 run nevertheless exited during
`test_scan_and_missing_page_never_become_successful_empty_text`; its root
cause is unknown. In the #9 follow-up run
[40879658172](https://github.com/ValerianXXX/JobFlow/actions/runs/40879658172),
the source-candidate job had passed and the three full matrices were still
running when this plan was checked. Later outcomes must be appended as new
observations, not backdated into this checkpoint.

Use the exact failing fixture, Python and dependency versions in a bounded
small cold/warm reproduction. The production parser already isolates native
code; inspect the test's direct native calls and distinguish worker failure
from test-runner death. Preserve fixed error labels, exit codes and timings
without publishing private stderr. Do not delete the assertion, blanket-retry
it, or promote the optional parser to default. If the cause cannot be fixed,
retain the limitation and assess whether to omit that optional extra from
the first distribution through an explicit scope decision.
Its existing integration tests remain enabled; omitting an optional package
from distribution does not change the failed run's status.

## M1: two honest resume paths

1. **Existing editable master:** copy the approved DOCX, change only approved
   slots, show differences and preserve the original. Keep the existing
   layout fingerprint, Claim links and document QA.
2. **PDF/image only:** after source and Claim review, offer to create a new
   standard one- or two-page bilingual template. Reuse `build_resume` and
   strengthen its product-facing sections; do not call a new layout a faithful
   reconstruction of the scanned original. The user reviews and selects this
   generated DOCX as the master before tailoring.

Detect conversion capabilities before starting a long task. Evaluate
LibreOffice in an isolated process as an optional converter, with bounded
execution, local-only inputs, explicit dependency setup and per-page QA. If
no validated converter is available, retain the DOCX draft and show the
missing requirement; do not mark PDF QA complete. Existing Word output remains
an independently tested path. No silent software installation or cloud upload.

Extend the existing fixed material corpus to 36 labeled cases (12 Chinese,
12 English, 12 mixed), including scans, columns, tables, portfolios, weak
images and conflicting dates/ownership. Freeze a development/held-out split
before comparing candidates. Reuse existing extraction and preview graders.
All fixed offline checks run locally; start live model comparison with only
six difficult synthetic cases and an explicit call/cost ceiling within the
existing quota. Stop at the ceiling; unknown billing stays unknown.

Proposed acceptance targets, not current results:

- At least 95% recall of labeled supported facts across the held-out set,
  reported separately by language/layout; publish false additions and omissions.
- No unflagged wrong designated number, date or individual/team attribution
  on the fixed release set. Every externally usable statement has approved
  evidence; ambiguous facts remain pending.
- All release-set outputs pass text consistency, page limit and visual review:
  no clipping, overlap, broken links, substituted missing glyphs or blank pages.
- Record user corrections, re-uploads, completion time, model requests,
  returned usage and cost. An OCR component is retained only if it reduces
  errors or review work without unacceptable runtime/package overhead.

## M2: route-specific evidence and recoverable interaction

Retain the existing official-company route binding and `ExternalActionGateway`.
For the first accepted Greenhouse and Lever routes, give the user one visible
summary of required answers, unresolved items, approved source facts and the
exact resume version before filling. Extend existing observations/rebinding
rather than add a competing browser Agent.

Exercise missing fields, duplicate labels, framework redraw, file rejection,
expired authorization, reload, interruption and resumption. A changed JD,
claim, file, answer, route or page binding invalidates the relevant approval.
An uncertain submission remains unknown and must not trigger a retry.

Report four levels separately: fixture contract, public-page read-only
observation, authorized fill/upload to final review, and user submission with
verified receipt. Public-page inspection alone cannot certify fill/upload.
Use only jobs the user actually chooses and data they explicitly authorize
for those sites; never send synthetic applications to real employers. If
there is no appropriate live application, retain the external validation gap.

Initial gates: all fixed interruption/recovery cases pass; no duplicate fill,
wrong attachment, bypassed unknown answer or automatic final submit; at least
one specifically authorized real route per provider before labeling that
route live-verified. A receipt-less run is not a completed business outcome.

## M3: mailbox evidence, without confusing OAuth with authenticity

Start with one provider. Gmail fits a Gmail-only pilot if its OAuth setup is
available; otherwise build the same contract against the approved available
provider. Do not interpret the author's Git email as mailbox authorization.
Keep manual EML import useful while account access is unavailable.

The current `after` timestamp is insufficient for reliable provider sync.
Add an optional opaque cursor, bounded pages and replay-safe event identity,
preserving compatibility for local EML and synthetic adapters. Gmail history
expiration may require a bounded resync; Graph delta is folder-scoped. Resync
must not duplicate evidence or overwrite a user's corrected association.

First connector release is user-initiated read-only refresh, with a visible
folder/label/time scope and easy disconnect. Tokens remain in the private OS
store; scope filters minimize retrieval but do not narrow the provider's
actual OAuth grant. No send, delete or mailbox-write permission. Public Gmail
distribution must satisfy applicable restricted-scope requirements. Reading
body text with Graph needs an appropriate permission beyond `Mail.ReadBasic`.

OAuth authenticates access to the mailbox, not the sender. Record provider
message identity, thread/reference, source provenance and what sender
authentication is actually established. Imported `Authentication-Results`
headers cannot prove sender identity. Missing proof stays unknown.

Extend the existing 60-message fixture collection to at least 120 balanced
cases: repeated fetches, forwarded chains, two applications at one company,
the same role at different companies, negation, invitations, refusals,
conflicting quotations, unrelated mail and spoofed/unknown senders. Target
zero incorrect confident associations on this fixed set and at least 95%
candidate recall among cases labeled uniquely matchable. Report missed
matches and manual-review rate separately. Ambiguous outcomes require review;
email proposals do not rewrite protected application execution history.

## M4: measure completed work and human effort

Use the currently configured Hermes route to ask for readiness, pending
reviews and an application's next required action. Verify actual model tool
selection, grounded responses and honest handling of no data, revoked access
and unknown state. Existing SDK negotiation success is not this test. Keep
the tools read-only; no reinstall of OpenClaw or new mutating MCP tools is
needed for this milestone.

Then run a small formative pilot with five first-time users supplied by the
owner, using synthetic applications and source data. A proposed usability
gate is four of five completing both material preparation and one application
rehearsal without developer intervention. Record raw counts and obstacles;
five participants do not establish a population completion rate.

Keep separate measures for: valid AI output, correct supported facts, user
task completion, actual employer receipt, user edits, operator assistance,
end-to-end time and attributable cost. Hiring success is a later outcome,
not the definition of a passing software test. Browser simulation remains a
separate engineering evidence class.

## Release, data and rollback boundaries

The owner has already confirmed signing and independent clean Windows
resources are unavailable. Do not repeatedly request them during development.
Prepare a reviewable signer handoff, identity/privacy settings, runtime
inventory, complete candidate and Windows test matrix now. Check the signing
provider's current account eligibility and fees only when selecting it; no
service purchase or activation is implied by this plan.

Keep Windows executable signing, JobFlow update-manifest verification,
complete-runtime attestation and release approval as distinct gates. Test
matching companion versions, first install, start, upgrade, preserved DPAPI
data, failed health checks, rollback, interrupted updates and uninstall on
an independent stock machine/VM. Hosted source tests do not replace this.

New material versions and mail cursor records should be additive and
versioned. Any schema change needs a backed-up fixture migration and a
documented compatible reader/restore path; do not assume old binaries can
read a future schema. OCR and conversion adapters must be independently
disableable. Disconnecting mail stops access but does not silently erase
retained evidence. Revert code or restore a verified backup through an
explicit procedure; never rewrite histories to make a regression disappear.

Each milestone delivers a focused branch/commit/draft PR, configuration
instructions, exact tests, actual-versus-synthetic evidence, unresolved
blockers and rollback notes. Run related checks during changes and full
required checks at the stable checkpoint. Merge, public Release, extension
store update, production signing/service activation and deployment retain
their existing owner-approval boundaries.

The first implementation batch is M0 plus the M1 PDF-only template entry,
converter capability detection and bilingual artifact tests. No new default
model, broad OCR installation, mailbox access or hosted Agent migration is
part of that batch.
