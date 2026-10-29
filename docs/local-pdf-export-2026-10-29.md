# Optional local PDF export and reading-order QA

October 29, 2026. Source-preview milestone, not a signed release or full applicant acceptance. Word remains the default. An explicitly selected LibreOffice installation can now perform the existing standard-resume and application-material PDF export locally. No model, cloud converter, installer or fallback is invoked. A saved DOCX remains available when conversion fails.

## Setup and applicant flow

Install and trust LibreOffice separately from the [official download page](https://www.libreoffice.org/download/). Before starting the existing JobFlow source launcher in the same shell:

```powershell
$env:JOBFLOW_PDF_EXPORT_PROVIDER = 'libreoffice'
# Only needed for a nonstandard installation; use an absolute path to soffice.com.
# $env:JOBFLOW_LIBREOFFICE_BINARY = 'C:\Program Files\LibreOffice\program\soffice.com'
python -m jobops.cli onboarding-center
```

Windows discovery uses the standard Program Files directories. An invalid explicit path or provider is an error; it never silently selects another executable. On Windows use the console `soffice.com`, not the asynchronous GUI launcher. Non-Windows discovery exists, but this milestone's actual office and private-storage acceptance ran only on the existing Windows workstation.

The bilingual preparation panel displays the configured exporter as `DETECTED_NOT_VERIFIED`. Choose **Convert and inspect PDF pages** to test an actual approved draft. The result records which provider generated it. Existing cached PDFs retain their original provider; changing configuration does not rewrite retained artifacts. Inspect every page and the extracted text before application use. UI protocol is **39**; restart the backend on source changes. Companion remains **0.9.2 / protocol 2**.

For rollback, select `JOBFLOW_PDF_EXPORT_PROVIDER=word` and restart. Retained DOCX/PDF bytes, original evidence and approvals remain unchanged; new output still needs existing material approval. No database migration is required. Do not remove the stronger QA checks to make a problematic document pass. Rolling back beyond the preceding standard-resume adoption change has the additional [whole-backup restrictions](standard-resume-2026-10-29.md#dependencies-and-recovery).

## Isolation and supported input

Each conversion uses the existing private staging area with a disposable office profile/cache and a 90-second process deadline. Windows starts the process suspended, binds its descendants to an owned Job, and reclaims that Job before cleanup. Output streams are discarded; fixed errors do not carry document contents or paths. When available, an NTFS short alias addresses the same staging directory without moving data. Extended absolute paths clean office caches beyond Windows MAX_PATH. No existing office profile or running user office process is terminated.

The optional importer rejects macros, embedded objects, external templates/media and dynamic fields other than page counters. Ordinary HTTP(S)/email hyperlinks remain inert during conversion. These checks and the isolated profile are not an operating-system sandbox; only run a trusted installed office binary. Complex or unsupported files fail closed and retain the DOCX. This change does not bypass the existing Claim, layout, visual review or final-submit boundaries.

## Measured results and retained failures

Official [command-line parameters](https://help.libreoffice.org/latest/en-US/text/shared/guide/start_parameters.html) and [conversion filters](https://help.libreoffice.org/latest/en-US/text/shared/guide/convertfilters.html) were checked on October 29. The isolated Windows package was LibreOffice **26.8.2.2**, build `9dea406ab7695df6325b11b0e19fdca8db4b7276`, MPL 2.0. Its 375,934,976-byte MSI matched the official SHA-256 `ff4b911403b67ee9e6249f820f9782c44bb32c94fdaa45b0da53a8e419e5307a` and had a valid Document Foundation Authenticode signature. It was administratively unpacked for testing, not installed into the product or redistributed.

The [receipt](../evaluations/2026-10-29/local-pdf-export.json) contains input/output/render hashes, attempts and limits. Six fixed synthetic DOCX layouts were fed to both actual converters: English, Chinese, mixed language, chronological table, two columns and an explicit two-page document. Within each paired run both providers received identical bytes. The rerun regenerated ZIP timestamps; cross-attempt byte equality is not claimed. The case definitions and factual wording did not change.

- Both providers produced all six PDFs after path handling was corrected. Five of six passed exact fact retention, page counts and the strengthened structural QA. The two-column case still fails linear reading order in both providers; it is not relabeled successful.
- All 14 comparison pages were visually inspected by the coding assistant. No clipping, overlapping text or missing glyphs were observed on this small set. Visual readability did not override the two-column text failure.
- The first LibreOffice UI run produced a PDF but failed while cleaning a 317-character cache path. The failed observation remains recorded; one early synthetic workspace remains pending local cleanup after automatic policy rejection. Subsequent isolated runs confirmed cleanup. A second issue produced no PDF on the deeper evaluation path; using the same directory's verified short alias fixed that observation.
- Actual Edge tests exercised Chinese desktop and English mobile, DPAPI, explicit DOCX download, LibreOffice PDF preview, adoption, reload and session/origin checks. Browser external requests were zero. A completed synthetic profile was seeded; this was not AI extraction, a clean Windows installation, a novice study or a real employer application.
- Formatting made zero model calls. Local model-call cost was zero; office licensing, CPU cost and general latency benefit were not measured. Fresh isolated LibreOffice startup was slower than Word in these single observations; it is an availability option, not a demonstrated speed improvement.

## Stronger QA discovered by the comparison

The old word-frequency comparison could pass interleaved columns or swapped numbers/ownership. QA now also requires every paragraph of at least eight tokens to remain contiguous in the linear PDF text. Counts are reported without private wording. Short labels continue through the existing token/visual checks; this is not a universal semantic-equivalence test. Conservative false alarms may require changing the layout, not bypassing QA.

Uniform single-section headers/footers are expanded from DOCX definitions for the actual page count. Simple PAGE/NUMPAGES fields use calculated expected values, not observed PDF values. Missing or incorrect expected stories still fail. More complex section/field arrangements retain the conservative original comparison. The 8% token threshold was not increased.

The historical complex release fixture itself has interleaved skills and education text. It remains unchanged and is now an explicit negative regression. A separate single-column fixture retains its synthetic factual wording and has new rendered, hash-bound visual evidence. An initial flattened draft incorrectly placed the skill fact under EDUCATION; actual visual review caught it before approval, and the explicit fixture mapping was repaired. Format-preservation tests continue using the original complex master. No historical review, approval or test has been deleted or fabricated.

Reproduce the conversion experiment with installed converters:

```powershell
python scripts/evaluate-local-pdf.py --providers word libreoffice
python -m unittest discover -s tests -p test_local_pdf_export.py
python -m unittest discover -s tests -p test_document_reading_order.py
$env:JOBFLOW_TEST_LIBREOFFICE = '1'
node tests/standard_resume_ui_e2e.cjs
```

The experiment intentionally returns failure while the fixed two-column case fails. It is separate from regression tests proving that unsafe results are blocked. The broader 36-material OCR/Claim comparison, current live ATS trials, authenticated mailbox, independent users, production signing and clean Windows acceptance remain open. JobFlow has not reached the original complete acceptance requirements.
