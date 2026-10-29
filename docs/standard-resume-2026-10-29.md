# PDF master to editable standard resume — October 29, 2026

This is the first implementation batch of the [upgrade plan](next-upgrade-plan-2026-10-29.md): parser diagnostics, early document-tool discovery, and the PDF-only resume entry. Later OCR, live ATS, mailbox, Hermes task-execution and independent user-study milestones remain open.

Follow-up: [explicit local LibreOffice export and stronger reading-order QA](local-pdf-export-2026-10-29.md) are now available in a later source branch, using UI protocol 39. The Word-only behavior, protocol 38 and results below describe this original batch; they are retained as its observation record.

## Applicant flow

Previously a retained PDF could supply confirmed facts but could not become an editable master. This entry builds a **new single-column DOCX** from exact wording already approved for resume use. It does not reconstruct the original typography, translate facts, infer missing dates/employers or call another model. Sections without approved facts are omitted. Completed profile contact fields supply the header; salary, work authorization and street address are excluded.

1. Retain the PDF, review source-linked Claims, and complete the profile.
2. Explicitly approve the current wording for resume use.
3. Choose **Preview a standard resume** in the preparation panel. Chinese and English headings are available; factual wording stays unchanged.
4. Review the text. **Save a DOCX copy** explicitly downloads it to the browser's chosen destination. Optional **Convert and inspect PDF pages** uses local Word, with no cloud upload or automatic installation.
5. Confirm adoption and choose **Use as master and start revision**. Reconfirm the profile, material authorization and safe editing positions before preparing an application.

The original completed state, PDF and approvals remain intact. Original evidence stays bound to its old source; generated output is not independent evidence. PDF line coordinates are not reused as paragraph positions in the generated DOCX. Adoption does not set `MATERIALS_VALIDATED`, authorize uploads or submit an application.

## Dependencies and recovery

Use the existing development setup in README. No new SDK, model, service or credential is needed. UI protocol is **38**; restart the backend when changing source versions. Browser Companion remains **0.9.2 / protocol 2**.

Bootstrap discovers python-docx, Word COM registration, PDF preview libraries and the application PDF renderer. `DETECTED_NOT_VERIFIED` means registration only. Activation, access and conversion require a real export. LibreOffice remains a planned alternative, not supported in this batch.

If Word conversion fails or is absent, keep/re-download the DOCX and inspect the prerequisite. The original PDF and completed profile remain unchanged. Failures return fixed operation labels rather than COM stderr or document contents. Changed profile/evidence, expired or replaced approval, foreign draft reference and mismatched DOCX hash block reuse/adoption. A failed revision write removes partial references and leaves the previous completed state active.

Real browser testing exposed an existing export-helper issue: inherited open stdin could hold the PowerShell conversion until the 90-second limit. The helper now receives EOF independently of the server's control pipe. The timeout is unchanged, and a native PowerShell regression holds the parent input open while requiring the helper to finish.

Drafts and outputs use the existing encrypted private store. One new private-record kind stores draft metadata; no database migration is needed. Before adoption or source rollback, stop JobFlow and back up the account-bound private store and database together. Before any adoption, code can be reverted through a normal new commit. **After adoption, older code does not understand the retained original-source binding:** prefer a forward fix, or deliberately restore the complete pre-adoption backup under the same Windows account before using old code. Do not reapprove generated Claims with an old build, delete individual records or manually rewrite approvals. Restoring a backup also loses later changes, so review that recovery separately. Later profile edits require their own revision.

## Verification

Local milestone results on October 29: **152 distinct related Python regressions passed**, covering onboarding, document generation/QA, private storage, Claims, tailoring, source quality, demo boundaries and parser isolation. All **10 browser suites passed**. This is not a rerun of the entire installer matrix. Compatibility metadata checks also passed; signed distribution remained correctly unavailable. The [bounded observation receipt](../evaluations/2026-10-29/standard-resume.json) records the synthetic DOCX/PDF/render hashes, actual verification layers and limits.

Actual Edge automation exercised Chinese desktop and English mobile layouts, DOCX download, local Word conversion and page display, explicit adoption, renewed-approval requirements, reload persistence, and session/origin rejection. The one-page synthetic export was also visually inspected by the coding assistant; no clipping, overlap or altered numeric wording was observed. There were zero external browser requests and no live submission. The seeded fixture had two approved synthetic project statements; this small formatting result does not measure general extraction accuracy, completeness of a real resume, or applicant task success.

| Before this batch | Observed after this batch | Scope |
| --- | --- | --- |
| A PDF master could not become editable without another DOCX | The confirmed fixture generated/downloaded a standard DOCX and adopted it in a fresh revision | Synthetic profile, real UI/DPAPI |
| Export discovery was late and a long-lived open input pipe could stall conversion | Prerequisites are shown early; actual Word conversion completed after input isolation | Existing Windows workstation, not clean Windows |
| Native-extension failure could terminate the whole regression runner | Worker failure yields bounded diagnostics while correctness assertions remain | Local Python 3.11; historical 3.13 cause unresolved |
| Additional model inference would require quality/cost evidence | Standard-layout generation makes zero model calls, with zero model-call cost | Formatting step only; earlier extraction costs are separate |

Fast regressions cover exact Chinese wording/numbers, bilingual headings, wrong-use approval, missing confirmation, stale profile/hash/reference, immutable old state, original evidence after reapproval, revision-write rollback, converter failure and dependency discovery. These use a contract-store double. The browser fixture separately uses **actual Windows DPAPI**, an isolated synthetic profile and real loopback HTTP. Its seeded completed profile does not test AI extraction or an independent person's ability to complete onboarding.

```powershell
python -m unittest discover -s tests -p test_standard_resume.py
python -m unittest discover -s tests -p test_local_document_parser.py
node tests/standard_resume_ui_e2e.cjs
```

With usable Word, explicitly include conversion in browser acceptance:

```powershell
$env:JOBFLOW_TEST_WORD = '1'
node tests/standard_resume_ui_e2e.cjs
Remove-Item Env:JOBFLOW_TEST_WORD
```

Synthetic screenshots, DOCX and the result receipt stay under ignored `reports/standard-resume-ui/`. The suite is automatically discovered by `npm run test:e2e`. Default runs do not claim actual Word conversion.

Native-parser assertions now run in a bounded subprocess and preserve scan/OCR, missing-page and non-finite-coordinate requirements. Native exits report an exit code instead of terminating the unittest matrix; crash/timeout/malformed-output cases expose only fixed labels. This contains and diagnoses the historical Python 3.13 exit, but does **not** establish its root cause or repair the underlying native defect.

Production signing and independent clean-Windows installation/upgrade/data-preservation/rollback remain unavailable, as already confirmed by the owner. This is source-level work, not a new desktop release.
