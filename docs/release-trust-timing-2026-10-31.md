# Native release trust module resolution and hosted failures

Scope: 31 October 2026, development source and owned Windows diagnostic
processes. No production signature, installed-user data or release is changed.

## Why this follow-up is necessary

The [prior release run](https://github.com/ValerianXXX/JobFlow/actions/runs/41219488658)
passed its source-candidate build but failed every Windows regression job:

| Python | Tests | Errors | Total seconds |
| --- | ---: | ---: | ---: |
| 3.11 | 1,507 | 2 | 6,631.129 |
| 3.12 | 1,507 | 2 | 6,612.986 |
| 3.13 | 1,507 | 2 | 7,276.616 |

All six errors were the explicit official Node and path-with-spaces probes.
Their original 30-second budgets expired after `AUTH_LOCKED` and before
`AUTH_RECHECKED`; the last completed stage was at 23,835-24,998 milliseconds.
The [concurrent output repair](release-command-streams-2026-10-31.md) fixed a
separately reproduced pipe deadlock but did not resolve these hosted failures.
The exact parent runtime remains failed; no later observation replaces it.

The same five unmodified trust contracts pass locally in 16.996 seconds. A real
instrumented probe completes in 4.843 seconds, including two signature checks
and four full hashes. This discrepancy means that local success alone cannot
identify the hosted cause or establish release reliability.

## Evidence-supported repair

The second hosted diagnostic, job `124625682799` in
[run 41231212099](https://github.com/ValerianXXX/JobFlow/actions/runs/41231212099),
reached child entry at 1,966 ms but did not reach the explicit security-module
import before its 30.047-second timeout. Between those markers the original
script set simple preference variables and called `Join-Path`, which required
implicit module discovery. The original five tests again ran and had two errors
in 87.290 seconds. These observations locate the stall before native signature
verification, not in hashing or file-identity compilation.

The proposed repair disables automatic module discovery only inside the isolated
signature program. It constructs the two fixed system-module paths with .NET,
explicitly imports Security and Utility, and uses the qualified signature and
JSON commands. It reuses the existing approach in
[secure-store.ps1](../.agents/skills/job-application-operator/scripts/secure-store.ps1),
from this repository at the base revision; no new dependency or external code is
introduced. Both native signature verifications, signer allowlists, file locks,
path rejection, stream/path hashes and the 30-second probe limits remain.

Four native module-boundary contracts were frozen before this change. Two valid
tool scenarios failed when synthetic module search paths interfered with command
resolution; the two rejection cases already passed. After the repair all four
unchanged contracts pass in 14.382 seconds. The original five path/trust checks
also pass in 16.701 seconds, and the real local segmented probe passes in 5.313
seconds. This is not evidence of a general local speed gain or a demonstrated
publisher-impersonation exploit. Hosted verification of the repair is pending.

## Diagnostic boundary

[profile_release_tool_trust.py](../scripts/profile_release_tool_trust.py) reads
the release source and constructs an isolated temporary harness containing its
actual functions. Fixed source markers must match exactly before instrumentation
is inserted. Only allowlisted timing markers are added; trust operations are
not mocked, removed or replaced. The caller owns the full native process tree
and retains the same 30-second limit. A timeout or invalid result exits nonzero.

The profile measures file-identity initialization, the isolated signature
command and full stream hashing. It reports labels, elapsed times, runtime
version and fixed source/tool hashes, never arbitrary child output, private
paths, certificate contents or environment values. Output must be a new JSON
file under this checkout's `reports` directory. The temporary harness is removed
only from its verified owned directory after process containment has settled.

The independent `native-tool-trust` CI job runs this diagnostic early and then
runs all five original Node path/trust contracts even if the profile failed.
The original full Windows matrix, source build, signer policy, file locks,
assertions and per-probe deadline remain in place. Failure of the diagnostic
still fails the workflow; it is not turned into a warning or success status.
No dependency is added; the diagnostic uses Python's standard library and the
existing Windows PowerShell trust path.

## Running

Use the exact, separately staged Node 24.19.0 test prerequisite described in
[Node selection](release-node-selection-2026-10-31.md), without replacing the
host runtime. Its path is selected by `JOBFLOW_TEST_NODE_PATH` and its SHA-256
must match the existing fixture. From the project checkout:

```powershell
python scripts/profile_release_tool_trust.py --output reports/native-trust-new.json
python -m unittest discover -s tests -p test_release_node_path.py -v
```

Do not overwrite an earlier result or use a development-signed executable to
satisfy the production signer policy. The profile checks a trusted tool, not a
signed JobFlow release. The new local worktree's environment audit completed;
knowledge-root discovery returned `KNOWLEDGE_ROOT_NOT_FOUND`. No knowledge
source or private application material is required by this diagnostic, and none
was copied to the worktree.

## Current evidence and remaining work

The initial diagnostic checkpoint is `0fe599e47c7adf4dc4a0717c4c7d41450170ae70`,
based on PR #34 at `3c9ac3f892ebdeeb62c0c3d095b639960675b283`. The unchanged
release-wrapper source has normalized LF SHA-256
`7e89ad4989eeb7135d41709939848bd6199e7061da0eadadd5efcb6fefcec281`.
The five native checks and seven packaging/CI contracts passed locally. The
first hosted diagnostic, job `124624476914` in
[run 41230817388](https://github.com/ValerianXXX/JobFlow/actions/runs/41230817388),
failed at 30.031 seconds. File-identity initialization took 558 ms, the first
isolated signature command took 23,630 ms, and its two hashes took 97 and 107 ms.
The second signature command was still running at timeout. All five original
contracts then ran: three passed and two timed out in 82.547 seconds. This narrows
the slow region to the signature subprocess; it does not yet distinguish process
startup, module import and native signature verification. Additional allowlisted
child markers use UTC aligned with parent start, purely for timing diagnostics;
the monotonic 30-second owner deadline remains unchanged.

This checkpoint does not claim a repair or a hosted pass. Complete material-quality targets, independent-user
acceptance, trustworthy production signing and clean Windows installation,
upgrade, retention and rollback remain open.

Microsoft's [Get-AuthenticodeSignature documentation](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.security/get-authenticodesignature?view=powershell-5.1),
[module autoloading preference](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_preference_variables?view=powershell-5.1#psmoduleautoloadingpreference)
and [Add-Type documentation](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.utility/add-type?view=powershell-5.1)
were checked on 31 October 2026. The former describes file-signature inspection;
the preference explicitly permits disabling automatic imports in favor of
`Import-Module`; Add-Type describes runtime compilation of supplied .NET source.
The timing and failure observations above, rather than these documents alone,
support the diagnosis. No external code implementation was copied.

An ordinary reviewed revert restores the original ambient module discovery and
removes the diagnostic/extra CI job. It may reintroduce the observed false
rejections and hosted timeout. Retain reports and original failures; finish owned
verification processes before reverting their source. There is no database migration,
new model route, application-state change, production deployment or rollback
of user data in this diagnostic checkpoint.
