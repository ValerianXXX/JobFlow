# Windows release-smoke race correction — October 28, 2026

The first cleanup repair did not pass hosted Windows verification.
[Run 40802559245](https://github.com/ValerianXXX/JobFlow/actions/runs/40802559245)
tested head `928b8127ff3af7eac5ff358e5e3c02a6f458cc95` through merge checkout
`22adcfdba7cee999f3c6132688874f5ce27a4a50`. Python 3.11, 3.12 and 3.13 each ran
1,179 regressions and failed the same three descendant-liveness assertions:
normal parent exit, nonzero parent exit and timeout. Later browser, boundary
and source-candidate steps were skipped; earlier successful runs do not replace
this failure. These are process-lifecycle failures, not a new identity denial.

## Reproduction and implementation

Two local observations explain defects in that runner:

- Delaying job assignment by 300 ms let the Windows venv redirector create its
  interpreter first. The job recorded only one process; its counter reached
  zero while a descendant was still alive. The new delayed-launcher regression
  failed on the previous implementation and passed after the repair.
- With the standalone Python 3.11.3 interpreter, a fixed 50-run synthetic probe
  found six returns with a still-unsignaled child. Observed additional exit
  time was 0–16 ms. The first repaired 50-run probe found none. This is a small
  timing observation on one host, not a proof of arbitrary-process reliability
  or a measured user-facing performance gain.

Windows startup now follows the existing bootstrap runner's approach:
`CreateProcess` with `CREATE_SUSPENDED`, assignment to an anonymous parent-owned
job, then `ResumeThread`. Even the native redirector cannot execute before
assignment. CPython's Windows binding retains the primary thread handle that
`Popen` normally closes; no new dependency, shell or background window is used.
An explicit handle list passes only synthetic stdin/stdout/stderr. The job
handle stays in the parent, with kill-on-close and no breakaway permission.

Cleanup first disallows new process associations, captures wait handles for the
remaining job processes, requests job termination, waits for those handles, and
checks that job accounting is empty. Enumeration is bounded to 4,096 processes
and cleanup retains its ten-second deadline. Enumeration/wait/containment
failure blocks the build. Captured PIDs are never used to kill unrelated
processes. Output remains bounded and errors expose only fixed safe codes.
No fixed sleep was added to make a failing assertion pass.

The original CLI/private-store/UI budgets remain 30/30/45 seconds. Existing
assertions, identity policy, archive checks, signing and clean-Windows gates
are unchanged. Non-Windows process-group behavior is unchanged.

## Verification and limits

The [pathless observation receipt](../evaluations/2026-10-28/release-smoke-races.json)
preserves failed CI job IDs, baseline and repaired observations, source hashes,
and the local scope. Local Windows checks passed eight process/diagnostic tests,
16 candidate tests and 33 release-readiness tests. The eight process tests also
passed using the cached Python 3.13.15 runtime on the same development host.
They include actual descendants and file handles, delayed assignment, refusal
to assign/resume, Unicode paths/arguments/environment, and separate output.
The temporary extracted runtime was removed after the test.

The clean repair commit `9bef886ed948dbe0395dea6276aabc052e59cb8a`
then produced two identical source archives: 3,099,169 bytes, SHA-256
`4adf6b9c8a198dc45656056ea9706c0ebdf62168c831022ed5001a8d98478058`.
All 572 archive files and the approved identity check passed. Its real isolated
source startup passed under Python 3.11.3 and 3.13.15: both locales, five
loopback requests, two offline candidates, zero queue mutations, no private
values emitted and no external actions. Source/runtime temporary directories
were removed. The [exact source-candidate receipt](../evaluations/2026-10-28/release-smoke-races-source-candidate.json)
binds this result to `9bef886`, not a later documentation or CI merge commit.
These checks use one development host and do not establish clean-Windows
installation, a production signature or live ATS/mailbox compatibility.

The old complete runtime for `928b812` was built twice identically and passed its
limited offline import/closure smoke: 39,509,593 bytes, SHA-256
`f74c2983dabab5e3a03a3f5d395133cc3d1a2999f0529fb3b4d16520a36a4fa2`.
It is **historical unsigned preparation with failed source CI**. That smoke did
not exercise HTTP UI, installation, upgrade, data retention or rollback. It
does not contain this follow-up repair and is not a release-ready package.
Its canonical evidence was not rewritten to claim otherwise.

Replacement CI completed: all three Python versions passed 1,182 regressions
and nine browser suites at `1e83d1b`. The source UI gate failed at the bootstrap
request timeout; see the [October 29 record](source-bootstrap-2026-10-29.md).
The old CI did not retain enough detail to establish its original HTTP failure
cause. The owner confirmed production signing and independent clean Windows
are unavailable; both acceptance blockers remain. There were zero new model calls, external application
actions, uploads or installed-product/data changes in this repair.

## Run and rollback

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_release_smoke_process.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_release_candidate.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_release_readiness.py -v
# Optional 50-run synthetic timing probe, using the standalone interpreter:
$probePython = & .\.venv\Scripts\python.exe -c "import sys; print(sys._base_executable)"
& $probePython evaluations/2026-10-28/probe-process-cleanup.py
```

Create the documented development venv first; CI already provisions it. The
timing probe reports observations rather than substituting for test assertions.
Source-candidate construction still requires a clean committed tree and the
existing verified Git executable. No configuration or database migration is
needed. Reverting this correction restores the known process races; retain
the failure record and do not promote that state to a signed release. No
running installation was changed, so there is no installed data to restore.

Microsoft's [creation flags](https://learn.microsoft.com/en-us/windows/win32/procthread/process-creation-flags),
[thread resumption](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-resumethread),
[nested jobs](https://learn.microsoft.com/en-us/windows/win32/procthread/nested-jobs),
[active-process limits](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_basic_limit_information),
[job process lists](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_basic_process_id_list)
and [job termination](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-terminatejobobject)
were checked on October 28, 2026 (America/New_York). The local experiments are
the evidence for the observed timing differences; the documentation alone is
not a claim that the hosted failure has been revalidated.
