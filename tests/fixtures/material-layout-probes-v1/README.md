# Synthetic material layout probes v1

These 36 short diagnostic documents are original synthetic examples, not real
applicant data and not the planned 36 complete resume/portfolio acceptance set.
There are 12 layouts in each of English, Chinese and mixed language: 24 PDFs,
six DOCX files and six PNGs. Each has two entities and four designated facts.

`manifest.json` freezes input hashes, exact entity/fact wording and a 24-case
development / 12-case held-out split. Each language has eight/four cases.
The split rotates layouts across languages; templates remain related between
partitions. This is not an independent-template generalization estimate.
Same-organization roles have different titles, not a deliberately contradictory
date. Scans/PNGs require a separate OCR/vision path. Ruled-table rows deliberately
remain unchanged in the optional geometric parser.

`scripts/build-material-corpus.py` authors a new corpus directory using reportlab
and a supplied local Noto Sans SC font. Reportlab is an authoring dependency,
not a new JobFlow runtime dependency. No font binaries are distributed. ASCII
PDF runs use Helvetica and CJK runs use the standard CID STSong-Light font.
Regeneration can change container timestamps; evaluate the committed input
bytes rather than silently replacing hashes. Evaluators reject changed inputs.

Run `scripts/evaluate-material-corpus.py --split all --output NEW.json` without
a model. It measures retained verbatim facts and complete entity neighborhoods,
not approved Claim accuracy. The separate live collectors have explicit flags
and hard request limits. See [results and limitations](../../../docs/pdf-regions-and-cjk-2026-10-29.md).
