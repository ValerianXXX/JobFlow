# Fixed historical test-receipt boundary checks

The source-candidate job for `b0a5106` failed with
`PUBLIC_REPOSITORY_BOUNDARY_FAILED`. The two newly retained, redacted test
receipts used `.log`, which the unchanged path rule forbids, including history.
They contain public test names, a redacted traceback and terminal counts; they
are not application/runtime logs or applicant material.

Move the current copies to `.txt`, retain all history and keep current `.log`
paths forbidden. A history-only recognition may cover exactly the two already
published path/content pairs, with SHA-256 values fixed in the existing
`full-regression-followup.json`. Every historical version must match; another
path, different bytes, missing/oversized bytes or secret content must still fail.
Record recognized receipts in the report. Do not change author-identity policy,
global log policy, release approval or any existing privacy assertion.

Freeze the three real-Git history tests before implementing recognition. The
valid public history must become inspectable without deletion; changed historical
bytes must remain blocked even after a safe current version returns. Run existing
public-boundary and release-candidate checks as well as this fixture.
