# Scan rendering and upload rejection follow-up

Checked October 29, 2026. This is the first implementation stage of the expanded product-quality goal, following the [PDF-region/CJK milestone](pdf-regions-and-cjk-2026-10-29.md). Source changes and synthetic observations are not a signed release or full product acceptance.

## Problem and implementation

The previous visual path always rendered PDF pages at 110 DPI. A retained Chinese scan produced changed dates, amounts, organization names and ownership. Its Claim validator was checking against that incorrect transcript, so a grounded candidate did not prove agreement with the original pixels.

The optional visual route now defaults to 220 DPI. `JOBFLOW_VISUAL_PDF_DPI` accepts only an integer from 110 through 300. The renderer records the actual page size and image hash, encodes one page at a time and checks all page dimensions before rendering. Limits are 16 MiB source, six pages, 25 million pixels per page, 60 million pixels per document and 6 MiB of encoded image payload. Oversized or invalid input fails before a model request; the application does not silently reduce quality or send a partial document. Standalone images keep their supplied pixel dimensions.

The application still requires explicit visual opt-in, configured-provider consent, original-source review and separate Claim approval. No local OCR engine, language pack or new model provider is installed by the product. There is no database migration, UI protocol change or automatic selection.

## Fixed real-provider comparison

The same two frozen synthetic files were transcribed once per resolution, with opposite order for the second file. There were exactly four adapter requests, including all failures, no retries and zero model tool calls. The existing Hermes route remained `gpt-5.6-sol / openai-codex`, revision `afe53708ee7400919ae3e17a6dde9b02629e5c87`. No real applicant material or account action was used.

| Probe | DPI | Exact normalized source lines | Designated verbatim facts | Complete adjacent entity contexts | Adapter time | Encoded image characters |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Chinese single-column scan | 110 | 2/8 | 1/4 | 0/2 | 76.500 s | 79,570 |
| Same source | 220 | 8/8 | 4/4 | 2/2 | 47.609 s | 158,550 |
| English two-column scan | 220 | 8/8 | 4/4 | 2/2 | 22.703 s | 142,882 |
| Same source | 110 | 7/8 | 3/4 | 1/2 | 25.188 s | 71,690 |

The original PDF bytes and model/prompt were held fixed. Both source images were regenerated and their hashes matched the actual model input; coding-assistant visual inspection confirmed the damaged small glyphs at 110 DPI and the legible 220-DPI source. Larger rendering cannot recover information missing from an original scan. These are two short, already-known diagnostic probes, not independent templates or the planned 36 complete materials. One sample per arm does not establish causal effect size, general accuracy, latency improvement or cost saving. Tokens, provider-internal retries and billed cost are unknown; payload roughly doubled.

[Original live observation](../evaluations/2026-10-29/scan-vision-first.json) retains every transcript and image hash. This stage transcribed images only: it did not rerun downstream Claim generation, approval, document tailoring or application submission. The earlier failed scan/Claim observations remain unchanged.

## Independent local OCR comparator and correction

An evaluation-only adapter used existing Tesseract 5.5.0 with an isolated official Ubuntu Chinese language pack (`1:4.1.0-2build1`) and the existing English model. It received the identical rendered PNG bytes over stdin, used LSTM mode 1 / page segmentation mode 3, and returned line boxes. It has no model access or product-state writes.

The first adapter expected a `tsv` config file missing from that isolated language-data directory. Tesseract returned plain text with a successful process exit; the initial parser incorrectly treated it as zero TSV words. **The four initial local-OCR rows are invalid measurements, not evidence of zero recognition.** They are retained in the original observation. The adapter now selects TSV through `tessedit_create_tsv=1` and rejects a missing/malformed TSV header. A regression covers the exact false-empty condition.

Only the four local OCR arms were repeated, with no further model calls. [Corrected local observation](../evaluations/2026-10-29/scan-local-ocr-corrected.json): at 110 DPI, neither probe retained a complete designated line; at 220 DPI, each retained 7/8 lines. The Chinese OCR missed an organization heading; the English OCR misread a store count. The comparator therefore cannot act as authoritative truth or a silent production fallback. Its parameter choices are one bounded baseline, not an exhaustive OCR benchmark.

The initial transcription scorer also included unused parser-metadata defaults, including `source_review_required: false`; those fields described an empty metadata argument. The actual product metadata in each live row requires visual review. The revised scorer reports only text-retention metrics. No historical transcript or score has been rewritten. See the [adjudication and correction record](../evaluations/2026-10-29/scan-adjudication.json).

## Upload error recovery

During the full import regression, Windows aborted the client connection before the expected JSON rejection arrived. An isolated rerun failed at a second early-rejection case. The existing server and test were unchanged from the preceding commit: duplicate Content-Length and Transfer-Encoding were rejected, but their unread bytes could cause a TCP reset on close.

For these two error codes, the server now discards a raw prefix with a 64 KiB cap and one 250 ms total deadline. It does not interpret an ambiguous length, decode chunking, accept a body or execute another request. The connection still closes. Bounded reads and timeout restoration are covered separately, and the existing HTTP assertions remain intact. Large/slow hostile bodies can still be closed without guaranteed delivery of the error response; bounded resource use takes precedence.

The 74-test import suite passed after this repair. Browser verification also exposed separate standard-resume visibility and native fixture-startup timeouts. They were retained; no frontend, encrypted-store implementation, timeout or assertion was relaxed to address them. A direct startup trace sampled an existing DPAPI read during synthetic fixture construction. An independent open-parent-stdin DPAPI lifecycle passed, so that trace does not establish a stdin deadlock. The standard-resume and workbench flows subsequently passed alone with the same limits. These covered both languages, desktop/mobile layouts, actual download/adoption/reload and mail link/undo on synthetic records. All eleven browser suites have a passing observation; this is not consistent first-attempt acceptance. The standard-resume harness now preserves per-run screenshots and failure-state diagnostics. Intermittent native startup/visibility behavior remains unresolved; later successful runs do not erase the earlier failures.

## Run and rollback

For the existing configured visual route, set process environment before starting JobFlow:

```powershell
$env:JOBFLOW_VISUAL_ENABLE='true'
$env:JOBFLOW_VISUAL_ALLOW_CONFIGURED_PROVIDER='true'
$env:JOBFLOW_VISUAL_PDF_DPI='220'
python -m jobops.cli onboarding-center
```

The provider flag means rendered pages may follow Hermes to its configured cloud provider. Do not enable it when that data route is unwanted. `.env.example` is documentation and is not loaded automatically. An explicit `JOBFLOW_DOCUMENT_PARSER=pdf_regions` or `docling_native` takes precedence for PDF and does not run this visual path.

The opt-in fixed synthetic evaluator is:

```powershell
python scripts/evaluate-scan-resolution.py --live-hermes --output reports/scan-resolution/new-observation.json
```

It refuses existing observations and is capped at four adapter requests. For a local-only comparison, omit `--live-hermes` and supply `--local-ocr-command /path/to/trusted-argv.json`. The JSON array launches `scripts/tesseract-probe-adapter.py` in an environment with an existing trusted binary (`JOBFLOW_OCR_TEST_BINARY`) and language data (`TESSDATA_PREFIX` if needed). The local adapter is a benchmark utility, not a supported native Windows OCR integration. No shell string or arbitrary applicant-file option is accepted by the evaluator.

Set `JOBFLOW_VISUAL_PDF_DPI=110` and restart to reproduce the old resolution, with its documented errors. Disable `JOBFLOW_VISUAL_ENABLE` to stop visual transcription entirely. A source rollback removes the new bounds/metadata and restores the prior error-response race; secure originals and approval records are not migrated or deleted. Neither rollback nor disabling an optional route makes prior incorrect facts acceptable.

## References and remaining acceptance

The existing [pdfplumber 0.11.9 source](https://github.com/jsvine/pdfplumber/tree/db828e3ac08ded001650e6ec66b23e719370d22e) is MIT licensed. Its [rendering documentation](https://github.com/jsvine/pdfplumber#creating-a-pageimage-with-to_image) describes pixel resolution and antialiasing. Runtime here used pypdfium2 5.14.0 and Pillow 11.3.0; no library upgrade or copied upstream implementation was required. The [Tesseract quality guide](https://tesseract-ocr.github.io/tessdoc/ImproveQuality.html), [language-data guide](https://tesseract-ocr.github.io/tessdoc/Data-Files.html) and [TSV CLI contract](https://tesseract-ocr.github.io/tessdoc/Command-Line-Usage.html#tsv-output) were checked October 29. The tested language pack derives from Apache-2.0 `tessdata_fast`; binary files are not distributed in this repository.

Remaining work includes independent-template complete materials, region calibration, rotation/noise/low-resolution failures, multilingual Claim accuracy, contradiction handling, user recovery and independent user acceptance. Trusted signing and an independent clean Windows machine remain unavailable by owner confirmation. Current-source CI, signed installation, live ATS and authenticated mailbox acceptance must be reported separately. See the [verification receipt](../evaluations/2026-10-29/scan-verification.json) for this milestone's exact tests, failures and scope.
