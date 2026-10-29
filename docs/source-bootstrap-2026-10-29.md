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

## Encrypted-store follow-up

The independent [diagnostic run 40849236142](https://github.com/ValerianXXX/JobFlow/actions/runs/40849236142)
at `8a9e4a7` failed in source job `123364274644`: the request reached 5,000 ms
while the active labels were `bootstrap` and `encrypted_store`. Total smoke
time was 5,672 ms. This reproduces the timeout without the preceding long
regression job and identifies the waiting helper, but not its exact internal
PowerShell operation.

The helper previously relied on module autoloading for `Add-Type`, filesystem
commands and JSON output. Microsoft documents [automatic module loading](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_modules?view=powershell-5.1)
and a [module-analysis cache under LOCALAPPDATA](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_windows_powershell_5.1).
These were checked October 29, 2026. Each release smoke intentionally uses a
fresh private directory, so it cannot rely on the user's existing module cache.

The follow-up explicitly imports only Windows' built-in Utility and Management
modules from the system directory and disables automatic module discovery in
that disposable helper process. The DPAPI algorithm, CurrentUser scope,
ciphertext format, atomic replacement, private-reference checks and all
timeouts remain unchanged. It neither changes the user's module settings nor
introduces a persistent PowerShell host.

A regression supplying a synthetic ambient Utility module failed on the old
helper, then passed the real Put/Get/Test/Delete DPAPI roundtrip with explicit
system imports. This demonstrates a module-resolution defect and its repair.
Whether it resolves the hosted timing failure still requires the updated
source job; it must not be inferred from that synthetic regression alone.

## Verification and recovery

Run `test_release_smoke_process.py` and `test_release_candidate.py` for the
diagnostic/output boundary and candidate contract, then the source-candidate
job against a clean committed tree. The complete matrix remains required for
a stable milestone. The documentation records failure rather than replacing
it with a mock result. Reverting this diagnostic change restores the previous
job order/output; it does not remove the unresolved startup gate or authorize
production release. No configuration or data migration is needed.
