# Proposal, capability and performer scope - 2026-11-01

JobFlow's cited-word checks could admit a proposed deployment, an inability or
capacity to act, or another explicit performer's work as a completed applicant
experience. This extends the existing local source-status module; it neither
generates facts nor replaces the configured AI or user review.

## Behavior and boundaries

- Recognize explicit `proposal of` action nouns, such as `a deployment of`, and
  bounded Chinese proposal clauses. Preserve the literal object, qualifiers,
  dates and numbers instead of using shared words to match unrelated events.
- Represent capability, inability, failure and expectation separately from
  performed action, negation and prospective action. Keep supported source
  wording reviewable; never rewrite an inability as an accomplishment.
- Recognize bounded English role phrases or capitalized names and Chinese role
  phrases as literal performers. Known applicant aliases remain explicit. A
  different name/role does not establish an applicant alias or team membership.
- Inspect immediately wrapped status/performer context within existing citation
  bounds. Do not cross a blank or bullet boundary or borrow a neighboring claim.
- Preserve readable originals, editable-master eligibility, candidate wording,
  unselected defaults and source-only confirmation. Existing single-replacement,
  Claim/material permissions and final user submission remain unchanged.

No configuration, model, dependency, schema or protocol migration is required.
Hermes and local/cloud adapter controls are unchanged. UI protocol 44 keeps the
same request/response contract; v53 JavaScript uses the existing filter counts.

## Frozen comparison and observed regression

The [protocol](assertion-scope-protocol-2026-11-01.md), 84-row fixture, evaluator
and seven initial test methods were committed in `9dc91be` before runtime edits.
The [baseline](../evaluations/2026-11-01/assertion-scope-before.json) and
[final parser result](../evaluations/2026-11-01/assertion-scope-after.json) keep
the same fixture/evaluator hashes and all labels.

| Development-set outcome | Before | After |
| --- | ---: | ---: |
| Unsupported promotions incorrectly accepted | 35/36 | 0/36 |
| Supported controls incorrectly filtered | 0/48 | 0/48 |
| Nominalized proposal family incorrectly accepted | 11/12 | 0/12 |
| Capability/inability family incorrectly accepted | 12/12 | 0/12 |
| Literal performer family incorrectly accepted | 12/12 | 0/12 |

These are exposed development counterexamples, not population accuracy, new
model measurements or full-material acceptance. The original 60 cases remain
unchanged: [24 contradictions rejected and 36 controls retained](../evaluations/2026-11-01/assertion-status-scope-followup.json).
The three earlier known-limit observations remain immutable and are separately
rechecked by the new tests.

Historical replay caught a real regression in the first implementation: the
ordinary phrase `This profile lists completed work` was mistaken for a performer
followed by a verb. One supported summary disappeared in two observation arms.
The [failed first replay](../evaluations/2026-11-01/assertion-scope-replay-comparison.json)
is retained. Commit `d89683b` freezes the failing summary before the repair.
Literal role/name boundaries now reject that parse; no gold label was changed.
The [final replay](../evaluations/2026-11-01/assertion-scope-final-replay-comparison.json)
retains all 265 historical candidates, their wording/order and source context.
The three originally invalid visual responses remain invalid. The parent replay
was reused only after verifying its exact four validator-module hashes.

The parent AI suite passed 89 methods. The final parser passes 97, including the
new regression. The initial seven new methods had 44 failing subtest assertions;
the first implementation passed those but failed the historical summary replay.
This distinction is retained rather than treating the first green unit run as
acceptance. New live provider calls: zero. Original billed model costs: unknown.

Actual browser review found a second defect: the UI checked an unused actor
reason code, so a correctly filtered performer conflict had no specific
explanation. The failing UI contract was committed in `22959be` before the
one-line repair in `39ff70a`. The final contract passes in both languages at
1280 and 390 CSS pixels; it verifies unselected candidates, available source
confirmation and no outgoing request from rendering the notice.

The 20 existing JavaScript E2E suites pass on the initial scope implementation;
the new 21st contract separately covers the final notice correction. An actual
browser exercised English, Chinese and all-filtered DOCX uploads against the
loopback service and Windows DPAPI store, inspected cited lines, saved sources
with no selected candidates, and reloaded. All three original byte hashes match;
there are three saved sources, no pending source, no Claims, no approvals and
no external-action attempts. The DOCX master remains editable. The all-filtered
case makes exactly one replacement request (four fixture calls overall). All
temporary state was removed after receipt collection. This is an agent-operated
synthetic browser test, not an independent human or live-model acceptance test.

Full native regression is collected separately on the stable milestone; its
running state is never counted as a pass. The isolated test environment uses
the same 72 pinned packages as the parent environment. The available Node
executable and copied task-owned Playwright tree pass exact hash verification;
an unavailable npm executable is not reported as a successful fresh install.

The [hash-linked validation receipt](../evaluations/2026-11-01/assertion-scope-validation/verification.json)
contains the failed observations, focused results, browser originals/screenshots,
runtime pins and a running full-regression snapshot. All four changed runtime/UI
files match the built wheel exactly; no artifact was signed or published.

## Reproduction

Use the existing development installation and create fresh observation paths:

```text
python -m unittest discover -s tests -p test_ai_*.py
python scripts/evaluate-assertion-scope.py --output reports/new-scope-run.json
python scripts/evaluate-assertion-status.py --output reports/new-status-run.json
python scripts/assertion-status-acceptance.py --fixture scope --output reports/new-scope-browser-run
node tests/onboarding_assertion_scope_e2e.cjs
```

The browser harness uses only its three generated synthetic DOCX files and fixed
responses. It starts with no sources, Claims or approvals and cannot call a
provider. Save sources with every candidate unselected, inspect cited text,
switch languages and reload. The all-filtered example exercises exactly one
replacement request and still permits retaining the source. Its local endpoint
file contains session information and must not be published. Create a `stop`
file in the output directory to close the service and purge its disposable state.

## Remaining limits and release state

Intent paraphrases, counterfactuals and nested reported speech still admit
unsupported wording in the [separate known-limit probes](../evaluations/2026-11-01/assertion-scope-known-limits.json).
Quoted/discourse context, implicit subjects, arbitrary names, complex coordination
and cross-language entailment remain unverified. Unknown syntax is not certified
as supported merely because this literal guard detects no conflict.

All 36 full materials / 1,716 annotated facts and the >=95% supported-fact recall
and zero-unflagged-critical-error goals remain open. No extra model campaign or
independent-user acceptance occurred. The production signing and independent
clean-Windows installation/upgrade/data-retention/rollback prerequisites remain
unavailable as previously confirmed by the owner.

Parent `9176cf7` passes its local source-candidate build, but its hosted candidate
job [124831653238](https://github.com/ValerianXXX/JobFlow/actions/runs/41300954244/job/124831653238)
fails at the first bootstrap with `RELEASE_SMOKE_UI_FAILED`: the unchanged
5-second HTTP deadline expires while the encrypted-store operation is active.
This is a separate release/runtime issue, not a model comparison or a reason to
raise the deadline or relabel the failed run. Full final-runtime checks and
browser delivery evidence are recorded separately at the stable milestone.

No database migration or production action is needed for this code review.
A normal reviewed code revert restores the earlier scope checks and their known
false-acceptance risk. Preserve originals, saved reports and user decisions;
re-analysis is explicit and never silently changes historical Claims.
