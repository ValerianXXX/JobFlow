# Source startup verification — October 29, 2026

The owner confirmed on October 29 that no trusted production signing workflow
or independent clean Windows machine/VM is currently available. These are
confirmed external blockers, not unanswered approval requests. Continue source
development and CI; do not sign, publish, install into an existing user profile,
or claim signed installation, upgrade, retained-data or rollback acceptance.
Older unsigned artifacts remain bound to their original commits and evidence
expiry times. This decision does not promote them or grant production approval.

## Hosted results

| Draft | Source head | Run | Regression tests per Python version | Browser suites per Python version | Remaining failure |
| --- | --- | --- | --- | --- | --- |
| #7 | `1e83d1bd55a4eda8cc4e8d48b86a06e7efc33838` | [40815409407](https://github.com/ValerianXXX/JobFlow/actions/runs/40815409407) | 1,182 passed | 9 passed | Python 3.12 source gate: `bootstrap`, `timeout`, 5,688 ms total smoke |
| #8 | `03725d76f13e3efc5129aeac09cb1ce357c2e6c3` | [40815906233](https://github.com/ValerianXXX/JobFlow/actions/runs/40815906233) | 1,190 passed | 9 passed | Python 3.12 source gate: `bootstrap`, `timeout`, 5,687 ms total smoke |

Each matrix covers Python 3.11, 3.12 and 3.13. The 3.11/3.13 jobs passed; the
source gate ran only in 3.12. Identity, store-package and wheel checks passed.
The previous descendant-liveness failures did not recur. Both workflows
nevertheless failed, and source release readiness is not established.

The failed source smoke completed health and index requests before bootstrap.
It retained the original failure without the earlier temporary-directory
cleanup error. These observations locate the gate, not its internal root cause.
They do not establish that Python 3.12 itself is responsible.

## Diagnostic change

The source gate now runs in its own Windows job with the same pinned checkout,
Python setup, verified Git prerequisite, dependency profile and candidate
builder. It no longer waits behind the long installer regression matrix. All
three regression/browser jobs remain; any source-gate failure still fails CI.
No release upload or signing step is added.

Bootstrap keeps its five-second request deadline; CLI/private-store/UI process
budgets remain 30/30/45 seconds. On failure, diagnostics record only bounded
elapsed milliseconds and allowlisted component labels from active frames:
bootstrap, encrypted store, ATS evidence or schema validation. Frame paths,
locals, source text, exception text and private values are never emitted.
Actual DPAPI, HTTP, evidence integrity and queue-boundary checks still execute.

A local profile of the unchanged `9bef886` source archive passed the full
five-request smoke. One observed bootstrap took 984 ms, including 407 ms in
the DPAPI helper and approximately 511 ms constructing/verifying ATS evidence.
That evidence path performed 158 file hashes. This is one development-host
observation, not a hosted measurement or established timeout cause. No model
calls, mailbox access, installed-product changes or real application actions
were performed. Further hosted diagnostic results must determine the fix.

## Verification and recovery

Run `test_release_smoke_process.py` and `test_release_candidate.py` for the
diagnostic/output boundary and candidate contract, then the source-candidate
job against a clean committed tree. The complete matrix remains required for
a stable milestone. The documentation records failure rather than replacing
it with a mock result. Reverting this diagnostic change restores the previous
job order/output; it does not remove the unresolved startup gate or authorize
production release. No configuration or data migration is needed.
