# Preserve source negation and planned-action status — 2026-11-01

A candidate could retain most source words while reversing the fact. The source
“The applicant did not manage the project budget” previously admitted “The
applicant managed the project budget.” A proposed deployment could likewise be
presented as a completed deployment. Word overlap and number matching alone do
not establish factual support.

This change adds a bounded host guard after structured AI analysis. It compares
recognized explicit actors, selected action inflections and literal objects or
qualifiers. It filters a candidate if that same event has conflicting negation
or prospective-action status in the cited source. Distinct actors, objects and
explicit periods remain distinct. English by-agent passive forms are compared
only when the actor is explicit; an unassigned passive never supplies an actor.

An explicit conflict cannot borrow a nearby positive clause. Even if the
original narrow citation passes word overlap, the existing maximum two adjacent
physical lines are checked for an obviously wrapped negation/intent prefix.
Blank and bullet boundaries remain boundaries. The Chinese extension recognizes
only an explicit actor/status prefix immediately preceding a known action.

The guard does not generate or rewrite Claims. Complete negative predicates are
now recognized in experience candidates, and an unchanged first clause from
the Chinese "not only ... but also ..." construction can satisfy the existing short Chinese sentence exception. That
exception still requires a complete predicate and exact clause wording; it
cannot drop a negation. Filtered wording is not logged: only
`SOURCE_ASSERTION_CONFLICT` counts are retained. The bilingual preview explains
the skipped candidates; source cards say “AI candidates”, not confirmed Claims.
An analysis retry now says that a replacement was requested. It no longer says
the first result was corrected when the replacement still leaves zero candidates.

## Fixed before/after comparison

The [protocol](assertion-status-protocol-2026-11-01.md), labels and 60-case corpus
were committed in `92a1e27` before product code changed. This is an exposed
development set, not a blind population estimate. A separate bounded-line
counterexample extension was committed in `055e6ae` before its corresponding
fix; the first prototype failed five assertions across eight methods.

| Measurement | Parent behavior | Changed behavior |
| --- | ---: | ---: |
| Unsupported contradictions incorrectly accepted | 23 / 24 | 0 / 24 |
| Supported controls incorrectly filtered | 1 / 36 | 0 / 36 |
| English contradictions incorrectly accepted | 11 / 12 | 0 / 12 |
| Chinese contradictions incorrectly accepted | 10 / 10 | 0 / 10 |
| Mixed-language contradictions incorrectly accepted | 2 / 2 | 0 / 2 |

The supported control failure was a complete short Chinese additive clause,
not an incorrect label. It remains in the same denominator. The corpus LF hash
is pinned by both the evaluation script and tests. See the full
[before](../evaluations/2026-11-01/assertion-status-before.json) and
[after](../evaluations/2026-11-01/assertion-status-after.json) rows, including
diagnostics and measured elapsed time. Model calls and model cost for this local
experiment are zero; no model benefit or historical billing estimate is inferred.

Historical same-output replay preserves all **197** accepted candidates from the
initial/text and visual-follow-up arms plus **68** from the two fact-preservation
responses, in the same order and with unchanged wording. All remain unselected;
the 12 fact-preservation context notes remain unchanged. The initial three
visual responses that were blocked stay blocked; later successful visual
responses are separate observations. Original source PDFs and DPAPI save/reopen
were exercised. See the [11-arm comparison](../evaluations/2026-11-01/assertion-replay-comparison.json)
and its full before/after files. This is a validator regression check, not a new
model sample or independent fact recoding.

Self-review also found four mixed-language counterexamples: an English `did not`
or `will` prefix was lost when parsing a Chinese action. Commit `6c5a03d` freezes
the two unsupported promotions and two valid equivalent rewrites; all four fail
before the fix and pass in `2ac8c48`. The original 60-case set is unchanged. The
[final 60-case run](../evaluations/2026-11-01/assertion-status-final.json) and
[final 11-arm replay](../evaluations/2026-11-01/assertion-final-replay-comparison.json)
repeat the relevant checks after that code change. All 265 prior candidates and
the source context remain unchanged.

## Validation status

- Original 80 AI-related tests passed on the current parent before modification.
  The first guard milestone passed 88; the final delivery passes **89**, including
  nine assertion-status methods and their counterexamples. Seven existing
  passive-attribution and three Chinese validator tests also passed.
- Actual browser checks use the fixed synthetic adapter in
  `scripts/assertion-status-acceptance.py`, the normal upload/confirmation routes
  and an isolated Windows DPAPI store. English and Chinese DOCX uploads each
  leave the two supported statements unselected and show two filtered conflicts.
  The original English four-line analysis was opened and checked. Saving a source
  with no selected Claims succeeds; a browser reload preserves all three sources,
  including one with no accepted candidates. The real bootstrap response confirms
  an editable DOCX master, zero confirmed Claims, no material-use permission and
  zero external actions. That zero-candidate source uses exactly one replacement
  fixture response. The final bilingual retry-copy check repeats that scenario.
  These are actual HTTP/browser/DPAPI checks with a fake analysis adapter, not
  independent-human or live-model acceptance.
- The full **1,634-test** regression finished against the earlier guard milestone
  `26d22b0`: **1 failure, 0 errors, 4 skips** in 4,027.760 seconds. The failure was
  the existing English-only public-documentation check: this document contained
  a literal Chinese example. The prose now describes that construction in English;
  the bilingual corpus and all assertions remain unchanged. The original failed
  run remains failed; the focused documentation recheck is recorded separately
  in the [follow-up receipt](../evaluations/2026-11-01/assertion-validation/full-regression-followup.json).
  This earlier run is not acceptance of final runtime `2ac8c48`, which adds the
  mixed-prefix fix, one test method, retry strings and the harness correction.
  Final-head hosted checks remain necessary. The first launch stopped during preflight because
  `wheel` was missing; the next run was intentionally stopped after discovering
  `setuptools 65.5.0` below the declared build minimum. The isolated environment
  now uses the existing validated `wheel 0.48.0` / `setuptools 80.10.2`; no product
  dependency requirement was weakened. Logs of both earlier starts are retained.
- The next hosted source-candidate job (`41298935701`, job `124825933496`)
  stopped at `PUBLIC_REPOSITORY_BOUNDARY_FAILED`: the two redacted test receipts
  had been published with the forbidden `.log` suffix. Current copies now use
  `.txt`. History is preserved. The history scanner recognizes only those two
  fixed path/SHA-256 pairs and still checks their text; any other path or changed
  historical version remains blocked, as do all current `.log` files. This is
  not an identity or release-approval exception. The separately frozen
  [history protocol](public-test-receipts-protocol-2026-11-01.md) and three real-Git
  tests cover unchanged public bytes, another path and changed-then-restored
  history. Existing public boundary checks remain enabled.
  The frozen history fixture initially failed one of three tests. After the
  correction, 20 public-boundary tests and 53 release-related tests pass. An
  explicit authenticated-Git audit of the actual 433-commit local history also
  passes, with zero current/history findings and no new identity exception.
  The [history-boundary receipt](../evaluations/2026-11-01/assertion-validation/history-boundary/verification.json)
  preserves the earlier failure and narrow scope; repository readiness still
  does not authorize a public release.
  The subsequent [source-candidate result](../evaluations/2026-11-01/assertion-validation/history-boundary/source-candidate-followup.json)
  on `b04201d` passes: two archives match, all 1,366 packaged files pass the
  boundary check, and the isolated loopback health/UI/offline-discovery smoke
  passes with zero uploads or external actions. The changed checker and tests
  exactly match their packaged Git bytes. This is a source package, not a signed
  Windows installer or independent installation/upgrade acceptance.
- A wheel built without publishing contains the exact final guard, runtime and
  UI source bytes; JavaScript syntax checks passed. Browser evidence, related
  logs, preparation failures and final artifact hashes are recorded in the
  [validation receipt](../evaluations/2026-11-01/assertion-validation/verification.json).
  An early harness observation queried a nonexistent top-level master field and
  reported false. Its original receipt is preserved; the actual nested bootstrap
  readiness response reports an editable master. The harness now reads that
  authoritative field, and the final zero-candidate scenario verifies it again.

## Reproduction and configuration

Use the repository's existing development setup and test environment. No model,
SDK, protocol version, cloud setting, database migration or new runtime
configuration is required. Hermes and existing local/cloud adapters are unchanged.
This guard grants no new data access, provider budget or tool authority.

```text
python -m unittest discover -s tests -p test_ai_assertion_status.py
python scripts/evaluate-assertion-status.py --output reports/new-status-run.json
python scripts/replay-complete-claims.py --observation initial --output reports/new-initial-replay.json
python scripts/replay-complete-claims.py --observation visual-followup --output reports/new-visual-replay.json
python scripts/replay-source-context.py --observation fact-preservation-v1 --output reports/new-fact-replay.json
python scripts/assertion-status-acceptance.py --output reports/new-synthetic-browser-run
```

Every output must be new. The browser harness prints a loopback URL, generates
three synthetic DOCX files, accepts only those bytes, starts with no approvals
or sources and cannot call a provider. It stops when a `stop` file is created in
that output directory or after 20 minutes. Its endpoint file is local session
information; do not publish it. The temporary encrypted state is removed on clean
shutdown. These commands do not test live AI, real resumes, independent users,
production signing, installation or ATS submission.

## Known gaps and release boundary

Zero literal conflicts is **not** semantic entailment. Nominalized plans,
inability, unrecognized named actors, implicit/quoted/conditional assertions,
complex coordination, different paraphrases and cross-language entailment remain
outside the bounded grammar. The [three known-limit probes](../evaluations/2026-11-01/assertion-known-limits.json)
are deliberately recorded separately: unsupported statements can still pass the
host when the source uses those forms. Do not turn the 60-case result into a
general accuracy claim or change the original full-material denominator.

The full-material 95% fact-recall target, complete critical-fact acceptance,
fresh comparative model measurements and independent-user acceptance remain
unfinished. No additional model campaign was run. Candidates still require user
review; source retention and editable-master adoption do not approve facts or
application materials. Final submission remains a user action.

No schema migration or production deployment is needed for this source change.
A normal reviewed code revert restores the earlier candidate-validation
behavior. Already retained originals and user decisions must be preserved;
re-analysis is explicit and never silently changes historical Claims. Production
signing and independent clean-Windows install/upgrade/data-retention/rollback
acceptance remain externally blocked, as previously confirmed by the user.
