# Material preview evaluation — October 28, 2026

The earlier live comparison measured visual transcription only. This follow-up
protocol also exercises the actual `OnboardingCenterService.preview_source`
path: PDF rendering, the configured zero-tool Hermes bridge, strict entity/Claim
validation, original-file encryption, encrypted pending preview and refusal to
include a visual Claim without the user's original-source confirmation.

## Frozen input and scoring

The input is the existing three-page synthetic raster PDF in
`evaluations/2026-10-27/visual-hermes-repaired-2026-10-27.synthetic.pdf`, SHA-256
`bbdc34d94ec92aaf277e11bbc4f8acd4a74cc2998c4f5d9c48083bd21d738e25`.
It has one scanned resume page, one two-column resume page and one portfolio
screenshot page. All three original pages were visually inspected with Poppler
before the live follow-up. The runner accepts no input-file argument and refuses
a changed fixture hash, so it cannot silently evaluate a real resume.

Six fixed numeric facts are declared before collection: 12 stores, an 18% time
reduction, 1,200 records, a team budget of $12,500, 24 team scenarios and six
applicant reports. A match requires the correct organization, source year(s),
team/applicant wording where the source makes it explicit, a source-line span,
the correct page and original-file hash. Every candidate must remain unselected
and unverified. A value assigned to a different organization is not credited.

These deterministic checks are deliberately narrow. An unmatched paraphrase is
not automatically a numerical error or misattribution. Those counts stay null
until the retained synthetic statements and source are manually adjudicated.
The adjudication is an explicit review, not an independent reviewer or calibrated
semantic benchmark. Bounding-box geometry is still uncalibrated.

The baseline uses the same file and production preview service with visual mode
off. A scan with no readable text should stop before model analysis. The candidate
uses visual mode and the already configured Hermes route. It preserves the
production one-replacement limit for structured analysis; the evaluator allows at
most three adapter requests in total and does not add a model fallback. The
public model/provider selection and Hermes revision are checked before and after.
Internal provider retries, token counts and billed cost are not exposed by this
adapter and must remain unknown; adapter requests are not API-call counts.

## Run

```powershell
python -m unittest discover -s tests -p test_material_preview_evaluation.py -v
python scripts/evaluate-material-preview.py --output reports/preview-baseline.json
# Explicitly use the existing authorized Hermes route for this synthetic input:
python scripts/evaluate-material-preview.py --live-hermes --output reports/preview-live.json
```

The current runner targets Windows DPAPI and existing Hermes in WSL Ubuntu. It
requires the normal JobFlow dependencies and does not install or reconfigure an
Agent. Images and derived text follow Hermes's configured provider, which can be
cloud-hosted. Only the fixed synthetic fixture is sent. Each output is exclusive:
choose a new filename to preserve every failed and successful observation.

The sandbox contains its own project, database and sibling DPAPI directory.
No source material is committed or approved for use in an application. The
intentional attempt to select a candidate without original-source confirmation
must fail with `VISUAL_SOURCE_VERIFICATION_REQUIRED`. The sandbox is removed on
exit. Application submission, email, browser actions and scheduling are outside
this evaluation.

Eight offline regressions cover an empty result, altered numbers/years,
wrong entity ownership, team-to-person attribution, incorrect evidence binding
and automatic selection, malformed observation envelopes and the request limit.
These test the collector/grader; they are not model observations.
Live results must be recorded separately with their exact script/source revision.

## Observed preview result

The [unaltered live receipt](../evaluations/2026-10-28/material-preview-live-1.json)
was collected with script/source checkpoint
`ad926e2f2550e59d80d0223a33fb35a3d7683c87` at `2026-10-29T02:09:05Z`
(October 28 in America/New_York). The configured route remained
`gpt-5.6-sol` / `openai-codex`; Hermes revision was
`afe53708ee7400919ae3e17a6dde9b02629e5c87` before and after. No model or
provider was changed. Two adapter requests returned zero tool calls: one visual
transcription and one structured document analysis; no replacement was needed.

| Observed measure | Existing text preview | Optional visual preview |
| --- | --- | --- |
| Result | `ONBOARDING_DOCUMENT_QUALITY_FAILED` | `SOURCE_PREVIEW_READY` |
| Designated numeric facts retained | 0 / 6 | 6 / 6 |
| Claim candidates | No preview | 10, covering four entities |
| Elapsed whole preview | 515 ms | 34,360 ms |
| Adapter requests | 0 | 2 |
| Model billing / tokens | No model request | Unknown / unknown |

All 13 transcription lines match the fixed original. The coding assistant then
reviewed the original pages and all ten statements: the six designated values,
their organizations, source years and explicit team/applicant ownership were
preserved, with zero observed omissions, changed numbers or wrong attribution in
that declared scope. [Separate assistant review](../evaluations/2026-10-28/material-preview-assistant-review.json)
records these counts without changing the raw collector's null adjudication
fields. This is not an independent human review or the owner's confirmation.

The source bytes survived a real Windows DPAPI round trip unchanged. Every
candidate remained unselected and `visual_verified=false`. Selecting a candidate
without original-source confirmation failed with
`VISUAL_SOURCE_VERIFICATION_REQUIRED`; zero material Claims were committed. The
temporary project and private store were removed. This proves a bounded synthetic
source-preview flow, not application completion or production resume accuracy.

After collection, the runner was hardened to retain malformed observation
envelopes and route-recheck failures, and to report explicit database table
counters. Eight offline tests, five public-document checks and a baseline-only
DPAPI run passed. No additional live request was made for those collector-only
changes, and no new counter was backfilled into the original observation. The
receipt's script hash and commit identify exactly which collector ran.
