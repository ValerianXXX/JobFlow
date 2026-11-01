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
`不仅…，还…` can satisfy the existing short Chinese sentence exception. That
exception still requires a complete predicate and exact clause wording; it
cannot drop a negation. Filtered wording is not logged: only
`SOURCE_ASSERTION_CONFLICT` counts are retained. The bilingual preview explains
the skipped candidates; source cards say “AI candidates”, not confirmed Claims.

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

## Validation status

- Original 80 AI-related tests passed on the current parent before modification.
  After the change, 88 AI-related tests passed, including the eight frozen and
  boundary methods. Seven existing passive-attribution and three Chinese
  validator tests also passed.
- Actual browser checks use the fixed synthetic adapter in
  `scripts/assertion-status-acceptance.py`, the normal upload/confirmation routes
  and an isolated Windows DPAPI store. English and Chinese DOCX uploads each
  leave the two supported statements unselected and show two filtered conflicts.
  The original English four-line analysis was opened and checked. Saving a source
  with no selected Claims succeeds; a browser reload preserves it. The scenario
  with no accepted candidates is being completed separately below.
- A full 1,634-test regression is running against runtime `26d22b0`. Its result
  is pending, not a PASS. The first launch stopped during preflight because
  `wheel` was missing; the next run was intentionally stopped after discovering
  `setuptools 65.5.0` below the declared build minimum. The isolated environment
  now uses the existing validated `wheel 0.48.0` / `setuptools 80.10.2`; no product
  dependency requirement was weakened. Logs of both earlier starts are retained.

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
