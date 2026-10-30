# Historical experiment verification

Checked October 30, 2026. This repairs a regression in evidence verification;
it adds no product or model capability and does not establish complete-material
acceptance.

## Failure and cause

Full Python discovery at source commit
`c3093bf6d5b2fa7dccd5cb94d402a69ceef5cfcb` completed 1,410 tests in
3,700.455 seconds: 1,405 passed, one error, and four native symlink-privilege
skips. The fact-preservation review test called the live experiment's loader.
That loader correctly requires current product bytes to match a pre-dispatch
protocol. Applying it to a closed historical observation incorrectly required
all later product development to retain the old onboarding implementation.

The six frozen input files are unchanged. All five product files at the recorded
experiment commit `93cb574db68f0f55827e28e81ebef7d0b37a37ca` still match their
original hashes. Only the current `onboarding_center.py` differs, as expected
after source-review and Profile improvements. The failure also reproduced on
the later Profile-source delivery commit `c7bdf13`.

The original full-run log is retained locally at
`reports/transcript-validation-full-20261030.log`, SHA-256
`783be76fdc5768e9b5f9bc46a34d1e260a2e4468aa34bd0aea224bc425051345`.
Neither that failed run nor an earlier successful full run is relabelled as
passing this change.

## Corrected boundary

The offline review verifies the observation and replay hashes first, then the
plan, its recorded manifests, immutable input files, and product blobs from the
observation's full Git commit. It reads those blobs without executing historical
code. Missing Git, missing history, a timeout, an invalid reference or a hash
mismatch fails verification; there is no current-tree fallback.

The frozen live runner is unchanged. It still rejects changed current code,
already published observations and consumed request journals. No journal is
opened, no model is invoked, and no gold fact, observation, result or budget is
rewritten by this repair.

Five earlier negative tests now also check the specific rejection reason, so a
different earlier error cannot make dropped facts, unrelated citations,
invented notes or rewritten history appear properly rejected. Eight added
contracts exercise current-code evolution, unchanged live restrictions, input
and code tampering, manifest binding, observation repointing, unavailable
history and invalid Git references. All 14 focused tests pass in 1.538 seconds.
All 46 related review tests pass in 15.642 seconds, and all five public-document
checks pass. The standalone verifier also succeeds. Log hashes and scopes are
in the [delivery receipt](../evaluations/2026-10-30/frozen-review-verification.json).
Current full regression and hosted CI are reported separately in the PR.

## Reproduce and limitations

Use the README's development setup in a full Git checkout. Existing CI already
uses `fetch-depth: 0`; no workflow gate is disabled or changed. A downloaded
source ZIP or shallow clone without the recorded commit cannot perform this
historical Git check. Obtain the repository history explicitly before running
it; the verifier does not fetch over the network.

```powershell
.\.venv\Scripts\python.exe scripts/verify-fact-preservation-review.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_fact_preservation_review.py' -v
```

Successful verification preserves the 94-fact denominator and reference checks.
It does not independently certify the reviewer's semantic decisions, measure
the current model, or complete the 36-material acceptance gate. Actual billed
cost remains unknown for the historical model observations; this offline repair
makes zero new provider requests.

No dependency, configuration, application state or database migration changes.
No UI behavior changes, so this stage adds no new browser acceptance claim.
Rollback is a reviewed revert of the verifier, tests and documentation; it
restores the historical-verification defect but does not alter product data.
Signing, clean-Windows lifecycle and independent-user acceptance remain open.
