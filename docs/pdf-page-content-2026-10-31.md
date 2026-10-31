# PDF page content before model analysis

Observed October 31, 2026. Runtime revision: `698430bc40459ef2f2fe64af5f80be86b06159b3`.
This source change detects a specific form of incomplete text extraction. It
does not add OCR, improve measured model recall or complete JobFlow acceptance.

## Reproduced problem and shared check

A synthetic scan visibly contained an employer, dates, quantities and an
individual/team boundary. Its only extractable text was `Page 1 of 1`. The
previous page-presence check accepted it. Another scan had an extractable title
above its raster body. Default logical/layout extraction, fallback extraction,
the geometry worker and the optional native worker could all return these
partial sources without identifying the missing image content.

The new shared module operates on existing local PDF character/image geometry:

- A page containing only recognized English/Chinese pagination is insufficient
  body text. Existing numbered-sequence validation remains separate.
- Clip image placements to the page. For images with no overlapping alphanumeric
  character center, measure their union area, counting repeated/overlapping
  placements once. At **12.5% of page area**, require explicit visual review or a
  readable complete source. This is a conservative routing policy, not a
  calibrated confidence score.
- Bound inspection to 256 image placements and 50,000 characters per page.
  Invalid geometry and resource limits remain distinguishable from missing text.
- Default and fallback complete-source extraction validate the same content-free
  page reports. A stale helper lacking those reports fails closed. Geometry and
  native preflight run inside their existing bounded local workers.

No partial source reaches model analysis in the verified rejection cases. No
provider is silently enabled. Existing Claim selection, original review,
approval, encryption and final user submission boundaries are retained.

## Evidence and scope

The separate [11-file regression corpus](../tests/fixtures/pdf-content-probes-v1/manifest.json)
contains six rejection inputs and five compatibility inputs. It includes header,
footer, tiled and rotated scans, a mixed two-page document, pagination-only text,
ordinary text, a small logo, repeated stamps, an off-page image and a searchable
raster. It is not added to the existing 36 whole materials.

The initial eight-method run produced six failed assertions and three secondary
test-harness errors. Fixing the harness to preserve each failed subcase produced
**24 failed assertions** before any product change. Three positive fixtures were
corrected after rendering: invisible searchable text replaced overlapping visible
lines, and actual decorative logos replaced miniaturized fact images. Their
compatibility check passed before product changes. All six rejection PDFs and
expected outcomes stayed unchanged. Original logs and commits are retained.

The first repaired eight methods passed; the expanded final group passed **17
tests**, including all 13 new contracts and four existing native-parser checks.
A related 103-test group passed separately; those four native tests overlap and
must not be counted twice as distinct coverage. The complete local suite and
exact-delivery hosted checks are reported separately in the draft PR.

The original **36 complete materials plus 18 challenges**, in English, Chinese
and mixed text, were replayed through both existing text and geometry routes at
the parent and this runtime. All 108 paired route/case observations retain the
same status, transcript hash and per-fact assessment. For the complete population,
both routes extract 27/36 sources; ordinary text retains 1,193/1,716 verbatim facts
and geometry retains 1,293/1,716, unchanged. Unsupported images/scans remain in
the denominator. These are parser diagnostics, not semantic Claim recall.
The 18 challenges also retain their earlier results. Runs shared a development
host with other checks, so their timings do not establish a performance benefit.

Actual local browser checks start from an empty synthetic database, upload all
three parser-route examples in both languages, show a recovery message and leave
zero retained sources/previews after rejection. A normal source then exercises
original rendering, confirmation gating, desktop/mobile review and reload. Its
five candidates remain pending. One recorded synthetic model response is replayed;
four deliberately malformed visual responses test the existing recovery path.
There are **zero real model requests**, external requests, applications, approved
Claims or external-action attempts. Temporary state is removed. This is browser
automation with synthetic inputs, not independent human acceptance.

The [verification receipt](../evaluations/2026-10-31/pdf-page-content/verification.json)
records frozen before/after inputs, complete-corpus comparison, source/artifact
hashes, exact observation scope and unfinished checks. Prior failed runs and
earlier model results are not replaced by these passes.
Local validation bytes and normalized Git source hashes are both recorded;
the Windows CJS checkout uses CRLF. The first metadata-packaging assertion stopped
on that distinction; no observations or product code were changed to recover it.

## Recovery, configuration and reproduction

No new dependency or setting is required. Source intake uses this check by
default. Existing `JOBFLOW_DOCUMENT_PARSER=pdf_regions` and `docling_native`
routes apply it as well; native parsing keeps its separately installed optional
dependencies. After rejection, check the affected original pages and supply a
complete editable document or explicitly configure the existing visual route.
Read [visual routing and data handling](visual-materials-and-status-tracking.md)
before enabling it. Cloud processing follows that explicit route's configuration.

In the prepared Windows development environment:

```powershell
python -m unittest discover -s tests -p test_pdf_content_coverage.py -v
python -m unittest discover -s tests -p test_local_document_parser.py -v
node tests/pdf_regions_ui_e2e.cjs
python scripts/evaluate-complete-materials.py --split development --include-challenges --output reports/content-development-new.json
python scripts/evaluate-complete-materials.py --split held_out --output reports/content-original-split-new.json
```

Use a new output path for every corpus observation. Existing corpus bytes and
historical split labels are preserved; previously exposed inputs are regression
inputs, not a fresh untouched holdout. The optional parser/test runtime and browser
requirements remain those of the existing development setup.

The non-strict `extract_pdf_text` diagnostic API intentionally still exposes raw
partial text; onboarding requests complete-page checks. This is not a generic
claim that every PDF utility now certifies source completeness.

## Limits and release boundary

Small screenshots, vector outlines and partly represented raster content can
still be omitted. A single text overlap does not prove that a whole image was
transcribed, and a searchable text layer can be wrong. Large decorative photos
can require additional review. The 12.5% policy has no independent calibration;
inspect original pages and retain missing/uncertain facts. There is no new
full-material 95% recall result, independent-user result or measured cost benefit.

No data migration or production change is required. Rollback restores the previous
source revision on a reviewed branch and retains encrypted originals; it restores
the known partial-intake weakness. Existing unapproved previews are not silently
rewritten: reprocess the original and review a replacement. Production signing,
independent clean-Windows install/upgrade/rollback, live mailbox/ATS and independent
human acceptance remain separate. Publishing or merging still needs approval.

## Reference and version verification

The implementation reuses the already installed `pdfplumber 0.11.9` object API,
not a new service. Its upstream tag resolved on October 31 to
`db828e3ac08ded001650e6ec66b23e719370d22e`; see the pinned
[character/image geometry documentation](https://github.com/jsvine/pdfplumber/blob/db828e3ac08ded001650e6ec66b23e719370d22e/README.md#objects)
and [MIT license](https://github.com/jsvine/pdfplumber/blob/db828e3ac08ded001650e6ec66b23e719370d22e/LICENSE.txt).
It supplies geometry, not OCR or semantic completeness. No upstream algorithm was
copied and no dependency was upgraded. Local verification also used `pypdf 5.9.0`,
Pillow 11.3.0 and the existing optional native parser.

The [GitHub release observation](../evaluations/2026-10-31/pdf-page-content/release-observation.json)
was refreshed at 23:34 UTC: v0.7.0 still had only its source archive and checksum,
and v0.6.0 remained the latest non-prerelease tag in the returned collection.
Chrome and Edge store pages could not be read through the research tool on this
date; their versions were not independently reverified. The README preserves
its explicit October 27 store observation rather than presenting it as new.
