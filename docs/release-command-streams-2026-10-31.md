# Release command output and hosted timeouts — 2026-10-31

The release wrapper read a child's stdout to completion before reading stderr.
A child writing enough stderr could fill that pipe and wait for the parent,
while the parent waited for stdout to close. The reproduced result is a stuck
command, even when the child's intended exit and output are otherwise valid.

`Invoke-SanitizedTextCommand` now starts both asynchronous reads before waiting
for exit, then returns their exact text and the actual child exit code. Its
executable selection, sanitized environment, file locks, trust checks and error
handling are unchanged. No output is discarded or treated as success.

## Observations and limits

PR #31's [hosted run 41209739080](https://github.com/ValerianXXX/JobFlow/actions/runs/41209739080)
failed its Windows 3.11 and 3.12 regressions after 1,490 tests each. Both had two
errors: the explicit trusted-Node and trusted-path-with-spaces probes exceeded
their 30-second deadline. Total times were 6,018.161 and 6,104.833 seconds. The
source candidate passed; Windows 3.13 was still running at this observation.
The raw logs are retained with hashes in this milestone's receipt.

This is **not proven to be the exclusive cause of those hosted timeouts**. A
slow trust operation or another issue still needs hosted evidence. The earlier
local 1,490-test pass is separate; neither it nor these local probes establishes
hosted completion or production readiness.

Four native contracts were first frozen at `fa7482c`. That initial observation
had four errors: three blocked streams and one test-driver capture error. A
detached launcher needed explicit capture to forward its small JSON result.
The correction at `c297135` changes only capture; all assertions and deadlines
remain. Before the product repair, this corrected set produced **one pass and
three timeout errors in 37.364 seconds**.

After the repair the same four contracts pass in **4.923 seconds**. They verify
100,000-character ASCII stdout/stderr streams, stderr-first output, empty stdout,
separate exact output hashes and nonzero child exit codes. The existing smoke
owner contains the native process tree and reclaims it after success or failure.
No arbitrary process name or PID is searched for or terminated.

The five existing Node path/trust checks pass in **20.894 seconds**. Their
30-second per-probe budget and every existing assertion remain. These probes now
use that same owned-process runner, so a timed-out parent cannot leave its trust
child behind. On an error, a tiny allowlisted stage label and elapsed time can
identify the last completed step; no path, environment, certificate content or
child output is added to the diagnostic note. The labels do not affect approval.

Runtime `a6a7cdfc830e71bcf12c17f04f15a6e7f317e496` is based on PR #32 at
`d6be2d2ad1c7c90326dbe10a02f33c65bdaa798a`. The broader native packaging/pre-sign
regression is running separately. The new hosted full suite must also finish;
neither is recorded as passed before its terminal result. See the
[receipt and preserved logs](../evaluations/2026-10-31/release-command-streams-verification-v1.json)
and draft PR for the results bound to this revision.

## Reproduce and recover

```powershell
python -m unittest discover -s tests -p test_release_command_streams.py -v
python -m unittest discover -s tests -p test_release_node_path.py -v
python -m unittest discover -s tests -p test_release_packaging.py -v
python -m unittest discover -s tests -p test_signed_update_presign_v2.py -v
```

Use the existing authenticated Node test prerequisite described in the
[Node-selection record](release-node-selection-2026-10-31.md). No new product
configuration, dependency, model request or database migration exists. There is
no UI change or new browser, applicant, mailbox or independent-user acceptance
in this milestone. Signing/packaging tests use synthetic fixtures and retain
production-signature rejection; they do not sign or publish a production build.

An ordinary reviewed revert restores the previous command runner and its pipe
deadlock. Finish or stop owned local verification processes first; preserve
candidate outputs and failed logs. No installed application data is transformed.
Do not relax trusted signers, increase a test deadline or ignore stderr to make a
failed handoff appear valid.

The production text runner still has no new hard execution deadline or output
size cap. Concurrent drainage solves the demonstrated two-pipe deadlock; it does
not prove termination of an unresponsive program, unrestricted output memory
safety, every encoding, certificate-service availability or all release failures.
The separately bounded synthetic test owner is not a new production supervisor.

Microsoft's [.NET redirected-output documentation](https://learn.microsoft.com/en-us/dotnet/api/system.diagnostics.process.standardoutput?view=netframework-4.8.1)
was checked on 2026-10-31. It documents the dependency that can deadlock redirected
streams. The repair uses the same asynchronous-read pattern already present
elsewhere in this repository; no external implementation was copied.

Trusted production signing, independent clean Windows installation/upgrade/
retention/rollback, complete material-quality targets, independent first-time
users and live provider workflows remain open. No release, store update or
deployment is performed or approved by these changes.
