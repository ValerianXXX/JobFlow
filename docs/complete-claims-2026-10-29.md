# Complete-material previews: failures, repairs and remaining acceptance

Observed October 29, 2026. This source milestone follows the [36-material parser baseline](complete-materials-2026-10-29.md). It is not a signed release, a completed application, or evidence that the full material-understanding target has been met.

## Changes

- `pdf-regions-3` checks the extent of line, rectangle and curve edges against a proposed column gutter. Short heading underlines inside a column no longer force row-wise interleaving; rules spanning the gutter still prevent reordering. This uses the existing [pdfplumber edge representation](https://github.com/jsvine/pdfplumber#objects), checked October 29, 2026. Its previously inspected 0.11.9 source is MIT licensed. No copied implementation, new dependency or OCR service is introduced.
- The numeric tokenizer no longer accepts a three-digit prefix of a longer sequence as a grouped number. Entity dates are checked separately, so concatenating `2023-02` and `2025-06` cannot manufacture `2202`. Equivalent amount formats remain supported; dates and Claim wording are never rewritten.
- Predicate recognition includes previously rejected actions such as English `coded` and common Chinese verification/reporting verbs. Chinese sentences of 12-19 characters must have a recognized predicate and exactly match a complete cited source sentence. Fragments, missing punctuation, changed numbers and partial matches dropping negation still fail. This is a format rule, not semantic proof.
- A conservative check rejects direct applicant/team/AI substitutions when the same complete action clause belongs to a different explicit actor in the citation. Rejected wording is absent from diagnostic counts. Paraphrases, implicit actors and general entailment remain outside this check.
- Strict visual validation is separated from rendering and request orchestration. Requests identify physical image dimensions and distinguish one tall screenshot from its printed page numbers. Boxes must refer to the whole physical image. Invalid JSON/page/region structure can receive at most one fresh full transcription using the same images and provider route. Malformed text is never mechanically repaired. Network, authorization and timeout errors are not automatically retried; two invalid responses fail the import.

Originals remain encrypted, candidates remain unselected, source review remains separate from Claim approval, and final submission remains user-only. No database migration or production change is needed.

## Fixed inputs and real observations

Six development files were selected before observing their complete-material model output. Their 282 authored facts include identity, contacts, missing information and negative evidence. All facts remain in the evaluation ledger, including contacts intentionally excluded from the Claim model. Similarity suggests review candidates; it never counts as correctness.

The existing route was Hermes `gpt-5.6-sol / openai-codex`, revision `afe53708ee7400919ae3e17a6dde9b02629e5c87`. The initial run used nine adapter requests and produced no usable preview: three text responses failed entity/date validation, and three visual responses contained incomplete JSON. Failed observations and raw synthetic responses remain unchanged.

| Stage | Observation | Evidence limit |
| --- | --- | --- |
| Initial real text calls | 0/3 previews; six requests including replacements | Production validation failed |
| Same-output replay after date fix | 3/3 previews; 71 unselected candidates | No new provider calls; bilingual filtering still lost supported wording |
| Same-output replay after bilingual fix | 3/3 previews; 100 unselected candidates: 34 English, 33 Chinese, 33 mixed | Validator improvement on saved outputs; semantic recall unmeasured |
| Initial real visual calls | 0/3 previews; three requests | No bracket completion or partial import |
| Fresh real visual follow-up | 3/3 previews; six requests; 34/30/33 unselected candidates | One transcription and one Claim request per file; replacement was not needed |

The initial pilot and follow-up used **15 adapter requests**, below the original cap of 18. Tool calls were zero. Provider-internal calls, tokens and billed cost are **unknown**, not zero. The route was unchanged. Application/approved-Claim/approval/external-action database counts were zero; isolated synthetic state was removed.

Visual transcripts retained 47/47, 46/47 and 47/47 complete normalized statements for the English scan, Chinese scan and mixed tall screenshot. The unmatched Chinese statement changes source wording about submitting inconsistent totals for review; this discrepancy remains recorded. These raw-text counts do not establish semantic Claim recall, calibrated boxes or absence of critical errors. This small prompt-and-validator follow-up is not a randomized estimate of model improvement. Bounded replacement has contract tests; its benefit was not demonstrated by these three first-attempt successes.

The direct ownership defect was reproduced after the live follow-up started. Its subsequent fix is checked by regressions and final same-output replay, not relabelled as part of the earlier live run. Receipts record the corresponding source hashes.

## Parser comparison and exposure

Geometry retains 870/870 statements in 18 readable development files and now 423/423 in the nine readable designated held-out files, versus 390/423 in the preceding milestone. The missing 33 came from English and mixed `compact_matrix` files. This entire family is now exposed regression material, as is the earlier hybrid family. **The repaired result is not a fresh blind holdout score.** Frozen files, manifest and gold did not change.

This is 1,293/1,293 statements across 27 text-readable files, but only 1,293/1,716 across all 36. Nine image-only/hybrid files remain blocked in the text-only comparison. Six pilot files do not constitute a full 36-material Claim acceptance run.

## Evidence and reproduction

Evidence under `evaluations/2026-10-29/`:

- `complete-claims-pilot-before.json`: immutable initial failures, LF SHA256 `04e1d542e1a7e8239f69c68c532eb0a1791df684fab98eecd2b973dc6e6eb046`.
- `complete-claims-visual-followup.json`: fresh real observations, LF SHA256 `c29b260a5cfb8ead88de20dffbea291cf85fa769bb7d5c0508c819ca3e15f551`.
- `complete-claims-replay-text.json` and `complete-claims-replay-visual.json`: final local replay, zero provider calls.
- `complete-parser-development-followup.json` and `complete-parser-exposed-followup.json`: parser observations with the exposure limits above.
- `complete-claims-verification.json`: local checks, browser receipts, retained failure corrections, package and outstanding gates.

Tests and replay need no model credentials. Full preview replay uses Windows DPAPI and temporary synthetic storage. Only hash-pinned public observations and frozen inputs are accepted; there is no provider fallback or arbitrary applicant path.

```powershell
python -m unittest discover -s tests -p test_ai_attribution_grounding.py -v
python -m unittest discover -s tests -p test_visual_replacement.py -v
python scripts/replay-complete-claims.py --output reports/complete-claims/new-text-replay.json
python scripts/replay-complete-claims.py --observation visual-followup --output reports/complete-claims/new-visual-replay.json
```

A separately budgeted live run uses `scripts/evaluate-complete-claims.py --live-hermes --output <fresh-path>` for six fixed inputs with an 18-request cap. `--visual-followup` selects only three visual inputs with a nine-request cap. These per-run caps are not an account billing budget; do not loop the command or infer free usage from an existing subscription.

Visual analysis stays off by default. After configuring and consenting to the provider route:

```powershell
$env:JOBFLOW_VISUAL_ENABLE='true'
$env:JOBFLOW_VISUAL_ALLOW_CONFIGURED_PROVIDER='true'
$env:JOBFLOW_VISUAL_PDF_DPI='220'
$env:JOBFLOW_VISUAL_MAX_REPAIR='1'
```

Set the last value to `0` to disable replacement and its possible second image request. Only literal `0` or `1` is accepted. `.env.example` is not automatically loaded. Explicit PDF parser selection still takes precedence, with no automatic cloud fallback.

The Edge test uses actual upload/HTTP/parser/render/DPAPI and fixed synthetic model output. Two invalid visual attempts reject the import in both languages. Ordinary source review, zoom, mobile scrolling and pending-Claim reload then pass. Screenshots were inspected. This is coding-assistant browser operation, separate from real Hermes observations and independent human acceptance.

## Remaining gates and rollback

Complete source-fact adjudication, entity/date/ownership association across all 36 materials, >=95% supported-fact recall and zero unflagged critical errors are **not established**. Lexical checks cannot prove semantic entailment. Direct actor substitutions do not cover all responsibility errors. Region boxes remain uncalibrated, and independently authored materials are still needed. The next stage must adjudicate facts and additional candidates, preserve mismatches, and cover paraphrased responsibility, date association, missing constraints and conflicting sources.

Rollback means reverting this source milestone on an isolated branch and rebuilding while retaining encrypted originals, records and approvals. `JOBFLOW_VISUAL_MAX_REPAIR=0` disables only replacement. Clearing `JOBFLOW_DOCUMENT_PARSER` selects the original text route with its documented column limits. Existing sources and approvals are not automatically rewritten. Signing, clean-Windows install/upgrade/data-preservation/rollback, live ATS/mailbox testing and independent human acceptance remain outstanding; owner-confirmed signing and clean-machine limitations are unchanged.
