# Visual materials and application-status preview

Verified October 27, 2026. This work builds on the release/compatibility checkpoint;
it does not make the unsigned source candidate a production Windows release.

## Material path and configuration

The default local text extractor is unchanged. Set process environment variables
before starting JobFlow (the `.env.example` file is not automatically loaded):

```powershell
$env:JOBFLOW_VISUAL_ENABLE = "true"
# Only after approving the already configured Agent/model data route:
$env:JOBFLOW_VISUAL_ALLOW_CONFIGURED_PROVIDER = "true"
.\.venv\Scripts\python.exe -m jobops.cli onboarding-center
```

Use the existing AI connection selector for Hermes or a loopback OpenAI-compatible
vision model. A loopback model does not need the second opt-in. Hermes follows its
configured provider, which may be a cloud service: locally rendered page images
and the transcription request are sent through that route. No new account, service,
OpenClaw installation, credential copy, or automatic provider fallback is required.
The optional image path for Ollama's native API is not implemented; its text path
remains available. Generic subprocess adapters must implement the documented
`JOBOPS_VISUAL_TRANSCRIPTION_V1` request in `multimodal.py`.

When enabled, PDFs and PNG/JPEG files enter visual transcription before the existing
strict Claim/entity pipeline. Limits are 16 MiB, six pages, 25 megapixels per rendered
page, and a bounded 6 MiB image payload. The entire document must pass the page,
text and normalized-region contract. Failure does not import a partial result.
Each candidate retains original file hash, page, region and source line references.
Coordinates and text are model proposals, not verified truth. Visual candidates
start unselected. The user must compare names, dates, amounts and personal/team
ownership with their original file and explicitly confirm that check; bulk selection
cannot bypass it. Normal Claim authorization still applies after inclusion.

Original materials and private state use the existing DPAPI store. Public diagnostic
metadata contains hashes/coordinates, not transcribed applicant text. Turn visual
mode off to return to the existing text path; no database migration is required.

## Reproducible comparison

Run `python scripts/evaluate-visual-materials.py --output reports/new-run.json` for
the local baseline. Add `--live-hermes --model <configured-model> --provider
<configured-provider>` only for an authorized small live run. The script refuses
to overwrite results. It creates original synthetic scanned, two-column and
portfolio pages; it never reads a real resume.

Evidence is in [evaluations/2026-10-27](../evaluations/2026-10-27/assessment.json).
The existing text extractor missed all six designated numeric facts (157 ms).
The real configured Hermes `gpt-5.6-sol` / `openai-codex` transcription preserved
all 13 source lines exactly, including dates, amounts and team/applicant ownership:
zero designated numeric omissions, number errors or attribution changes (15,703 ms).
Cost is **unknown**, not zero: this adapter did not report a usage/billing receipt.
One earlier call failed because the new path did not unwrap the existing Agent
response envelope. Its failed record is retained and a regression covers the fix.
This is one tiny synthetic transcription comparison, not a general quality estimate
or an end-to-end resume/Claim success claim. Region accuracy is not calibrated.

## Read-only mail status layer

`ReadOnlyStatusAdapter.read_messages(after, limit)` accepts only messages supplied
by an authorized future connector. `SyntheticStatusAdapter` is the only shipped
adapter. It does not open Outlook/Gmail, ask for OAuth, send mail, or read an inbox.

`propose_status` recognizes receipts, rejections and interview invitations in English
and Chinese. An exact application reference plus an authenticated allowed sender
is required for a confident link. Role-only matches, competing applications,
spoofed senders and conflicting phrases remain uncertain. Every link still awaits
human review. A future connector must attest sender authentication; a model cannot
assert it. Original messages and review journals use `application_mail_evidence`
and `application_mail_review` DPAPI records. Event hashes deduplicate repeated
collection; `prior_review_ref` resumes the journal. Accept/reject decisions are
retained and reversible. A rejection never rewrites the protected application
execution state, and an email receipt never implies the user clicked Submit.

`python scripts/evaluate-mail-tracking.py` verified three synthetic status messages,
duplicate collection and review/resume through the **real Windows DPAPI store**.
Eight targeted tests cover malformed visual output, provider consent, source
confirmation, reference collisions, sender spoofing, uncertain matches and review
history. The wider AI/onboarding regression ran 118 tests before the additional
envelope case; all passed. The browser regression checks that bulk selection does
not silently check source verification.

The frozen combined suite at `ec210453c4006a67de32286a423a26e2eaa17bbd`
ran 1,157 tests in 3,669.686 seconds: 1,148 passed, one failed, one errored and
seven skipped. The two failures were preparation gaps in this isolated worktree:
the exact JavaScript dependency tree and its local signed `.venv` launcher had
not been installed. After creating the local venv, installing `.[build]` and
running the committed `npm ci`, all 27 toolchain/wrapper/PowerShell checks passed,
including both failures and three formerly skipped launcher checks. Four symlink
privilege checks remain unavailable on this Windows host. No test or production
trust requirement was changed. The complete suite was not repeated or relabelled
as an all-green run. The release-branch merge changes history/documentation only;
the tested implementation is unchanged. [Recorded counts](../evaluations/2026-10-27/regression-summary.json).

Subsequent frozen GitHub [run 40612023771](https://github.com/ValerianXXX/JobFlow/actions/runs/40612023771)
at `279dd1d0cfc5f65eab51806cbbfb69a7fbc07fec` completed all **1,157 tests with zero
failures or skips** on Python 3.11 (6,348.954 s), 3.12 (6,397.693 s) and 3.13
(6,932.343 s), followed by all eight browser suites on each interpreter.
The 3.11/3.12 jobs then failed
the public-tree/history scanner on the escaped separator in
`tests/test_public_release.py`; that separate false positive is repaired in
release-branch commit `118761e6`. Historical-author authorization and protected
signing remain separate gates, regardless of regression results. Python 3.13
reached the old 120-minute limit during that subsequent scanner step; its
regression/browser results are complete, but the remaining package steps are not.
[Matrix receipt](../evaluations/2026-10-27/ci-regression-matrix.json).

After merging the scanner repair at checkpoint `c10d3da`, all 11 boundary tests
passed. A full local scan of 538 tracked files, 280 commits and 2,410 historical
blobs found zero content findings. The report still requires review for one
historical author identity; `public_release_ready` remains false. The next CI
uses the unchanged test matrix with 180 minutes of headroom.

Mailbox authorization, real provider adapters, actual mailbox delivery semantics
and a user-facing status inbox remain external/product follow-up. This checkpoint
delivers the reusable matching/persistence layer and synthetic validation, not live
email tracking. Final application submission remains a user action.

## Rollback and release

Disable the two optional flags first. Keep the prior release and same-account DPAPI
backup when testing source updates. No protected application-state migration or
mail connector activation is needed. Production signing and clean-machine
install/upgrade/data-preservation/rollback gates remain in the release checklist.

Official interface context checked October 27: [structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs).
No newer model or API was substituted for the already authorized Hermes route.
