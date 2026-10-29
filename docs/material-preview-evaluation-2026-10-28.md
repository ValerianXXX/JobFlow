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

Six offline grading regressions cover an empty result, altered numbers/years,
wrong entity ownership, team-to-person attribution, incorrect evidence binding
and automatic selection. These test the grader; they are not model observations.
Live results must be recorded separately with their exact script/source revision.
