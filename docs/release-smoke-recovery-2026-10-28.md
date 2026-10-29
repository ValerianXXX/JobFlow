# Source-candidate smoke recovery — October 28, 2026

## Observed failure

[CI run 40787970518](https://github.com/ValerianXXX/JobFlow/actions/runs/40787970518)
tested PR head `7b2fbf7d2249829610b4e7a5e4f2c68c8263f3ef` through GitHub's
merge checkout `7dfb42e86ecaf1f1df4ad48ec65afef4152667dc`. Both resolve to tree
`f6316aa8f36dbe4fd4fddc9d705817971edda67f`; they are different commits and do
not identify the same source archive. The base was `b679a11d5b712a1777b378b65a21b02950b7db2d`.

The Python 3.12 job passed all 1,172 regressions, nine browser suites, store
packaging, the public content/identity checks, wheel construction and CLI startup.
The extracted source UI smoke returned a failure. Its parent discarded the child
diagnostic, then temporary-directory cleanup raised Windows errors 32/5 because
the extracted directory was still in use. The original HTTP stage and cause are
unknown; a cold-start timeout is a hypothesis, not an observed finding.

The complete [matrix receipt](../evaluations/2026-10-28/identity-approved-ci-matrix.json)
records all three jobs: Python 3.11 and 3.13 also passed their 1,172 regressions
and nine browser suites. Candidate packaging runs only on Python 3.12. The
overall workflow remains failed; its other successful jobs do not override that gate.

This is a new release validation defect. It does not invalidate the owner's
identity approval, and the earlier successful local candidate is not evidence
that this CI invocation passed.

## Repair and verification boundary

`release_smoke_process.py` starts isolated Python behind a one-byte startup
handshake. The parent assigns that process to an anonymous Windows job before
releasing the script, so its descendants inherit the same job. The parent owns
the only job handle, enables kill-on-close, explicitly stops the job on every
exit path, and waits for its active-process count to reach zero before removing
the extracted files. Capture uses temporary files, avoiding an inherited pipe
that can remain open after the immediate child exits. Non-Windows callers use
an owned process group; Windows is the supported release environment.

The child reports only a fixed stage, a fixed error category and bounded elapsed
milliseconds. Raw exceptions, child stderr and machine paths are not published.
Existing CLI/private-store/UI deadlines and all release assertions are unchanged.
Timeout or containment failure still fails the candidate build.
If cleanup encounters an independent file lock, the original failing gate is
retained with `temporary_cleanup=FAILED`. Cleanup failure alone also blocks the
build; it is never suppressed to produce a passing candidate.

Focused Windows verification passed five lifecycle/diagnostic tests, 16
candidate tests and 33 release-readiness contract tests. The lifecycle tests
start real descendant processes that hold an output handle and working directory:
both normal/nonzero parent exits and a forced timeout must reclaim the child and
allow directory deletion. Failed job assignment must prevent target execution.
The startup-stage test verifies that a bootstrap timeout returns failure without
publishing exception content. These are synthetic process checks, not signed
installation or clean-Windows acceptance.

The original CI UI failure has not been reproduced locally. A new exact-commit
candidate and CI result must be recorded separately; do not mark the startup
blocker resolved based on these focused checks alone. The complete runtime and
publisher request for `987e8c5` remain historical preparation for that commit,
not attestations for this code change.

The [local recovery receipt](../evaluations/2026-10-28/release-smoke-recovery.json)
binds the clean source build to `d7a2f01348a5ab20f2ebdc3b092f928fa686f350`.
Two builds produced the same 3,088,332-byte source archive, SHA-256
`64dd6454e4f52b57108373ba3e3ffb56b1166ea2879defbe5f5ec2c84998ee7e`.
All 568 archived files passed the boundary scan. Its real isolated loopback smoke
passed under Python 3.11.3 and 3.13.15, including both locales, two offline
candidates, zero queue mutations, zero external actions and no private values
emitted. The 3.13.15 executable came from the previously verified cached complete
runtime; all five lifecycle/diagnostic tests also passed under that interpreter.
Both temporary runtime/source directories were removed. This is two interpreter
versions on one development host, not independent clean-Windows acceptance.
The new hosted CI remains separate evidence.

## Run and rollback

```powershell
python -m unittest discover -s tests -p test_release_smoke_process.py -v
python -m unittest discover -s tests -p test_release_candidate.py -v
python -m unittest discover -s tests -p test_release_readiness.py -v
python -m jobops.release_candidate --git-path <trusted-absolute-git-executable>
```

Candidate construction still requires a clean committed tree, the approved
identity policy and the existing authenticated Git toolchain. This repair needs
no database migration, product configuration, Agent reinstall or deployment.
Reverting its commit restores the previous smoke launcher and its known cleanup
defect; it cannot grant signing/publication permission. Production signing and
independent clean-Windows install, upgrade, data-retention and rollback acceptance
remain external gates.

Windows API semantics were checked against Microsoft's
[job-object overview](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects),
[process assignment](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-assignprocesstojobobject),
[job termination](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-terminatejobobject)
and [accounting query](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-queryinformationjobobject)
documentation on October 28, 2026 (America/New_York).
