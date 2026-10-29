# Readable synthetic release document

This new single-column sample uses the exact synthetic factual wording from `../release-document-qa/complex-master-resume.docx`. The old layout and its historical visual review remain unchanged there. The old PDF's skills and education columns interleave in linear text and are now a negative regression in `test_document_reading_order.py`.

`build_fixture.py` creates a new layout and exports it with the selected local converter. It does not update a real master, authorize applicant Claims, or write visual approval. The positive release check requires this sample's actual PDF, raster and a separately inspected `visual-review.json`. `automated-render-probe.json` remains insufficient by itself. Format-preservation tests continue using the original complex master outside this directory.

The first draft exposed a synthetic table-header ordering error during visual inspection; the final fixture explicitly keeps the skill fact under SKILLS and the university fact under EDUCATION. Source wording is unchanged. This small sample does not establish universal document or ATS compatibility.
