# Literal quantity grounding — October 31, 2026

The candidate validator previously checked whether each numeric coefficient
appeared in the cited text. It could accept an unchanged coefficient attached to
a different currency, scale or unit. It also accepted swapped values in an
otherwise identical clause: growth of 25% and retention of 40% could become
growth of 40% and retention of 25%. These are reproduced synthetic failures,
not errors observed in a new live model experiment.

## Behavior and architecture

`claim_quantities.py` now retains each literal coefficient together with its
recognized scale, sign, currency and adjacent unit. The existing numeric parser
and canonicalization move intact out of `ai_runtime.py`; the runtime keeps its
existing imports available and integrates the new report into candidate
grounding. Entirely absent coefficients still receive `UNSUPPORTED_NUMBER`.
Recognized context contradictions receive `UNSUPPORTED_NUMERIC_CONTEXT`.

Examples rejected by the same source-grounding path:

| Cited source | Unsupported candidate | Reason |
| --- | --- | --- |
| USD 3 million | USD 3 | Scale omitted |
| 20 milliseconds | 20 seconds | Unit changed |
| EUR 5000 | USD 5000 | Currency changed |
| 12 percentage points | 12 percent | Different measures |
| Growth 25%, retention 40% | Growth 40%, retention 25% | Same literal labels, exchanged values |
| Change of -25% | Change of 25% | Sign omitted |

This is a filter, not a fact generator. A rejected candidate is removed with a
reason; its wording is never mechanically repaired. Valid candidates remain
unselected, the original material stays available and approval is unchanged.
An explicit conflicting context cannot borrow a matching number from a nearby
sentence. A missing unit at an existing eligible physical line wrap can still
use the bounded source expansion. Normal numeric formatting, recognized aliases
and reordered plain lists remain supported. Diagnostics contain counts and
reason codes, not source text.

Currency codes take precedence over compatible redundant symbols: `USD $5000`
can support `USD 5000`, but not `CAD $5000`. An unqualified dollar or yen symbol
does not establish a country. No exchange rate, arithmetic or unit conversion is
performed. The adjacent scan is bounded to 40 characters before and 80 after a
number, avoiding repeated copies of the whole cited document.

## Verification and interpretation

The [receipt](../evaluations/2026-10-31/quantity-binding/verification.json)
binds commits, source hashes and retained observations. The initial 13 test
methods were committed before runtime changes: the old implementation produced
19 failed assertions, including subtests. The first implementation passed those
13. Three additional boundary methods then exposed two currency-code defects;
their failed run is retained before the final correction.

The unchanged final 16 methods pass, as do all 80 AI-related tests. These are
regression contracts, not 16 independent applicants or a measured model accuracy.
No tests, model prompt, approval gate or success definition were weakened. The
new worktree has its own populated Python environment and its own copy of the
unchanged locked JavaScript packages; all 173 dependency files match the existing
release-policy hash. The safe-start audit passed. Knowledge location was
explicitly set to an absent isolation sentinel and correctly returned
`ENV_MANIFEST_INVALID`; no private knowledge vault was located or read.

The pinned English timeline and Chinese sidebar model responses were replayed
through actual synthetic PDF parsing and DPAPI preview. Each retains the exact
same 34 candidates, assessment, source text, context notes and profile hints as
the historical replay. Original retention and owned temporary-state cleanup pass;
applications, approved Claims, approvals and external actions remain zero.
There were **zero new model calls**. The 4,562 ms and 3,688 ms replay durations
are local observations, not a matched latency improvement or inference cost.

The first evidence packaging attempt was rejected: eleven sanitized `.log`
files violated the existing file-suffix policy in both the working tree and
history. The failed local branch and commits remain intact. A separate delivery
branch starts at the same runtime commit and stores those sanitized observations
as ordinary scanned `.txt` files with fixed LF endings. No history is rewritten
or deleted, and no public-boundary policy is changed. An independent public
checkout verifies only the proposed branch plus fetched public repository refs;
the original shared checkout remains rejected because it retains the failed
local branch. The [packaging record](../evaluations/2026-10-31/quantity-binding/packaging-recovery.json)
preserves the exact failure and scope.

Complete local regression on the same runtime and this revision's hosted checks are tracked
separately in the receipt/PR; an unfinished check is not a pass. Parent
[PR #35](https://github.com/ValerianXXX/JobFlow/pull/35) has since passed its full
Windows 3.11/3.12/3.13 matrix. Those results belong to that earlier commit and do
not replace verification of this numeric change.

## Running and configuration

Use the existing [development setup](../README.md#development), including the
declared extras and locked Node packages. No new provider, dependency, setting,
database migration or cloud data transfer is introduced. Hermes and local-model
routes use the same source-grounding validator.

```powershell
python -m unittest discover -s tests -p 'test_ai_quantity*.py' -v
python -m unittest discover -s tests -p 'test_ai*.py' -v
python scripts/replay-template-pilot.py
```

The final command only reads the two pinned synthetic model responses. It writes
`reports/model-template-replay-v1.json` once and refuses to overwrite an existing
observation. Use a new prepared isolated checkout to repeat it; do not remove an
old report or invoke the live pilot to obtain a replacement. Full Windows
discovery additionally needs the existing [pinned Node test prerequisite](release-node-selection-2026-10-31.md).

## Remaining limits and rollback

The recognizer is deliberately bounded. Unrecognized units, accounting-style
parentheses, every currency convention, written-out numbers and general
cross-language semantic equivalence are not certified. Literal metric-order
checks require otherwise identical clause shapes. Rephrased metric/actor/date
relationships and a quantity attached to the wrong entity still need the
existing source review; coefficient/unit presence is not semantic entailment.
A matching context elsewhere in a broad citation is not proof of ownership.

No new whole-material accuracy, recall, cost reduction or independent-user
result is claimed. The latest reviewed two-template pilot still represents
41 of 47 source facts in supported candidates per template, with the documented
context/profile/source-only destinations; the larger corpus remains incompletely
measured. The separate six-call proposal remains awaiting a budget decision.
Production signing and independent clean-Windows installation, upgrade, retention
and rollback acceptance remain unavailable. Final submission stays user-only.

This source change needs ordinary review and later release approval; it does not
deploy or publish anything. A reviewed rollback can restore the previous source
validator without data migration, but restores its known numeric defects.
Preserve the new counterexamples and evidence, keep affected candidates under
manual source review, and reprocess unapproved drafts only with user action.
Do not silently rewrite approved facts or remove the failing contracts to make
an older validator appear correct.
