# Complete synthetic materials and page coverage

Checked October 29, 2026. This follows the [scan-resolution stage](scan-resolution-2026-10-29.md). The expanded product goal remains active. Parser measurements are not model accuracy, approved Claims, job applications or independent human acceptance.

The measurements below record this original parser stage. The later [complete-Claim follow-up](complete-claims-2026-10-29.md) repairs decorative-rule ordering, exposes the compact-matrix family for regression, and adds a six-material pilot with retained failures. Its repaired parser result is not a fresh blind holdout result.

## Frozen inputs

`tests/fixtures/complete-materials-v1` contains 36 complete synthetic materials: English, Chinese and mixed language each have 12. Each has profile/contact context, two employments, a project, education, tools/language and portfolio limitations. There are 1,716 authored factual statements, including missing information, negative evidence and team/applicant/AI responsibility. Three longer materials have 55 statements; the others have 47. Formats include native PDF, DOCX, image-only PDF, a tall portfolio image and mixed text/scan PDF.

The manifest assigns eight template families to development and four to held-out use; all language variants stay together. Files, facts and the split were frozen before parser comparison. The normalized manifest SHA256 is `11d22cb621ed917f1f014804bde9ff911d39816cd5087e54fe19f1bb2bb170ce`. The first authoring receipt mistakenly labeled the Windows CRLF byte hash as an LF hash. The corpus loader rejected it before any observation; the identifier calculation was corrected without changing any source material or gold fact.

These are coding-assistant-authored fixtures, not independent natural resumes. Several families share content and simple design components. Horizontal-rule layouts are not a representative complex-table benchmark. The `hybrid_text_scan` family was exposed through an English coverage regression before the first aggregate held-out run; report it separately from the other nine held-out files. Neither set is blind to the author. Broader professions, dense portfolios, genuine tables and independently authored holdouts remain necessary.

Eighteen separate development derivatives cover a blank middle page, a missing middle page, rotation, blur, embedded malicious instructions and conflicting dates. They do not count as additional complete independent cases. The missing-input gold distinguishes absent facts from facts available in the supplied bytes. The instructions and conflicting addenda are untrusted source material, never execution authority.

## Changes grounded in the baseline

1. **Wrapped columns.** The optional geometry parser could select a separator inside long left-column prose. `pdf-regions-2` adds repeated word starts as candidates and penalizes separators crossing words. It still preserves ruled-row order and requires original-source review; it does not infer semantic table relationships. The default extraction route is unchanged.
2. **Page completeness.** Default onboarding text extraction now checks every page before analysis. Empty text pages and inconsistent explicit page sequences are rejected with content-free diagnostics. The geometry worker also checks declared pagination. A small shared coverage module separates this rule from document QA and onboarding; the fallback helper supplies actual per-page text instead of only an aggregate.
3. **Recovery.** The UI explains missing/unreadable pages in Chinese and English, asking for the complete source or separately consented visual processing. There is no automatic cloud fallback or source/Claim approval.
4. **Windows rendering.** Repeating the source filename in an already long temporary output path caused a real Poppler render to fail. Fresh outputs now use an opaque unique prefix. The same previously failing fixture then rendered successfully; all 162 PDF/converted-DOCX pages or original image outputs were produced, yielding 168 review panels when the tall images were split for inspection.
5. **Synthetic fixture privacy check.** The content scanner initially rejected the corpus's `applicant.example.test` addresses. It now recognizes valid subdomains of the existing `example.test` fixture namespace, with label/length boundaries and lookalike regressions. [RFC 6761 section 6.2](https://www.rfc-editor.org/rfc/rfc6761#section-6.2), checked October 29, 2026, reserves `.test` and its descendants. This narrower repository convention does not allow all test-domain mailboxes and does not change Git author approval; a synthetic author identity still requires review.

The first coverage implementation also rejected a complete numbered section followed by an unnumbered addendum. That false rejection is retained in `complete-development-intermediate.json`. Coverage now validates the declared sequence separately, allowing complete numbered chapters with unnumbered covers/addenda. A regression covers this and Chinese pagination. No gold facts or failures were removed.

Coverage has limits: unnumbered omissions, a scanned body with only a readable footer, images alongside a text layer and complex numbering schemes are not proved complete. Original-source review remains necessary. A blank page may be intentional; it still requires another explicitly reviewed route instead of silent omission.

## Measured local results

All comparisons use the actual local parsers and fixed bytes, with **zero model requests**. Matching requires a complete normalized statement; duplicate statements require distinct occurrences. Numbers alone do not count. These measures do not establish correct organization/date/ownership association in a generated Claim.

| Population | Earlier default text | Earlier optional geometry | Current optional geometry |
| --- | ---: | ---: | ---: |
| 18 readable development materials | 837/870 statements | 864/870 | 870/870 |
| All 24 development materials, including six image-only inputs | 837/1,152 | 864/1,152 | 870/1,152 |
| Nine readable held-out materials | Current default: 356/423 | Not measured before tuning | 390/423 |
| All 12 designated held-out materials, including three hybrid inputs | Current default: 356/564 | Not measured before tuning | 390/564 |

The new complete-corpus stage did not run the visual/model route. Image-only and hybrid inputs remain blocked in this text-only comparison. The 18 readable development files retaining all statements does not imply general correctness: the nine readable held-out files retain only 390/423 statements through geometry, below 95% even for this limited raw-text metric. The full supported-Claim recall and zero-unflagged-critical-error targets remain **unmeasured and unmet**.

Both current text routes reject all six blank/missing-page derivatives. Earlier default text accepted all six; earlier geometry accepted the three missing-page derivatives. Rotated and blurred scans remain blocked without a visual route. The six instruction/conflict addenda are readable after correcting the addendum false rejection; downstream prompt-injection resistance and conflict resolution have **not** been measured by this parser run.

Original baseline, intermediate false-rejection, first held-out and final observations are retained under `evaluations/2026-10-29/complete-*.json`. Final held-out extraction was repeated only after the development pagination correction and receipt-hash expansion; it is not an independent new sample. The coding assistant inspected rendered contact sheets; independent user acceptance has not occurred. Local raw transcripts and full-size renders remain under ignored `reports/complete-materials/` and can be reproduced from the frozen files.

The [verification receipt](../evaluations/2026-10-29/complete-verification.json) records 135 related Python checks, the actual Edge import/review workflow, source hashes, the unsigned wheel and retained failures. The added browser checks initially hit a fixed-input whitelist and an Edge inspector-response-cache problem. The harness now permits exactly the two pinned synthetic hashes and verifies the actual XHR status/body plus both localized messages; it does not mock the response. Original parser, source-review and pending-Claim assertions remain in place. The final screenshot pass also waits for fully visible messages, and both language captures were inspected. Earlier intermittent native startup/visibility failures from the preceding stage remain unresolved.

## Run and rollback

The product retains the existing text route by default. For optional geometry, set `JOBFLOW_DOCUMENT_PARSER=pdf_regions` before starting JobFlow. There is no provider call on this route. No new product dependency or database migration is introduced.

```powershell
python scripts/evaluate-complete-materials.py --split development --include-challenges --output reports/complete-materials/new-development.json
python scripts/evaluate-complete-materials.py --split held_out --output reports/complete-materials/new-held-out.json
python -m unittest discover -s tests -p test_document_coverage.py -v
python -m unittest discover -s tests -p test_complete_material_corpus.py -v
```

Each evaluation refuses an existing output, records source hashes and writes incremental checkpoints. The authoring script requires a separate runtime with reportlab/Pillow/python-docx/pypdfium2 and a locally licensed CJK font; font binaries are not committed. Normal tests consume frozen inputs and do not regenerate them. No model, credential or private applicant path is accepted by the evaluator.

To roll back, revert this source commit on an isolated branch and rebuild, preserving encrypted originals, records and approvals. Clearing `JOBFLOW_DOCUMENT_PARSER` returns to the existing text extractor, including its documented column limitations. Reverting the coverage rule restores the previous silent-omission risk; it does not resolve missing evidence. Signing, production release, clean-machine installation/upgrade/rollback, live ATS/mailbox tests and independent human acceptance remain separate outstanding gates.
