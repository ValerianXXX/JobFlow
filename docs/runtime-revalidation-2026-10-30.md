# Protected runtime revalidation

Observed October 30, 2026. This source change repairs two failures found by full
regression. It does not grant signing, publication, installation or deployment
approval, and it does not establish full material-understanding acceptance.

## Original observations

Full local discovery on source `d281fbc3d54ed76514507abe2b913e99c41f692f`
completed with **1,352 tests: 1,346 passes, two failures and four skips in
3,631.449 seconds**. The earlier partial observation remains in the
[loopback repair receipt](../evaluations/2026-10-29/probe-rejection-verification.json).
The completed failed run is retained separately, not replaced by focused passes.

The failures were:

- The public-English documentation check found Chinese token examples in the
  duration guide. The explanation now describes the supported equivalents in
  English; the actual bilingual duration fixtures and product support remain.
- A protected runtime execution test returned
  `JOBFLOW_RELEASE_PYTHON_RUNTIME_INVALID`. Its unchanged eight-test module
  also reproduced one failure. A staged leaf reached 261 characters; stock
  PowerShell 5.1 path-based existence checking reported it absent even though
  its native file and parent handles were valid.

After the path check was isolated, the actual runtime also returned native code
`0xC0000106`, `STATUS_NAME_TOO_LONG`. Reducing the generated staging prefix
while retaining all 32 random GUID characters removes unnecessary path overhead.
The same original runtime test now executes real Python with all locks held.
This is not a claim that arbitrary-depth executable paths work.

Hosted run [40999836350](https://github.com/ValerianXXX/JobFlow/actions/runs/40999836350)
on the previous head passed source-candidate. Its Python 3.11 job failed only the
English documentation test (1,352 tests, 3,177.378 seconds). Other jobs and new
head results must be read separately; one platform result is not transferred to
another commit.

## Repair and preserved checks

Staged file locks already carry a retained parent handle and leaf name. Their
revalidation now verifies that parent handle, exact parent/leaf/path binding,
retained file identity and hash, then reopens the same leaf relative to the
retained parent. The reopened handle must match the file ID, volume, single-link
count, size, hash and resolved path. Directory/reparse attributes are rejected.
Maximum-byte limits remain enforced. Ordinary input locks keep their existing
validation path.

The new branch also rejects altered parent/name bindings that the old path-only
check ignored. No lock is released to make the interpreter start; write/delete
sharing, identity checks, hardlink canaries, protected cleanup and unknown-child
retention keep their requirements. There is no path-based deletion fallback.

The staging directory is now `.jfi-` followed by the same full random GUID.
Ownership and cleanup use the retained handles/context, not a directory-name
pattern. No shared temporary directory, registry change, dependency update,
approval exception or production signing key is introduced.

## Verification

Nine new native contracts were frozen at `71bf98e` before the product edit:
five passed and four failed. The same nine then passed. A first discovery
invocation accidentally rediscovered an imported eight-test class; that log is
retained separately. The import was corrected before freezing, without changing
any assertion or original test.

The deep-file test creates a 280-character staged leaf even on a shorter checkout;
it does not become an easier test when the staging prefix shrinks. It also covers
short/ordinary inputs, mismatched name/parent/path/hash, a closed parent and the
original size limit.

The unchanged original staging module passes **8/8 in 10.745 seconds** after the
complete repair, including native execution, hardlink/reparse defenses and
cleanup. Public documentation checks pass **5/5**. The complete presign/update
related run, including the unchanged nine new contracts, passes **58/58 in
249.118 seconds**. The new full discovery is recorded separately in the
[verification receipt](../evaluations/2026-10-30/runtime-revalidation.json);
pending runs are not successful results.

## Run and rollback

Use the README's per-worktree Development prerequisites. The repair adds no
configuration setting. Relevant checks are:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_staged_runtime_revalidation.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_signed_update_staging_handles.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_public_docs.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

These fixtures use synthetic bytes and the existing local interpreter. They do
not use a production signing identity or validate an independently clean Windows
installation. Source audit reports external actions disabled. No knowledge vault
was discovered in this isolated worktree; vault-dependent personal workflows
were not run or represented as accepted.

Rollback is a normal reviewed revert of source commit `fb6b90a`. There is no
database/data migration, release metadata update or installed-runtime change.
Retain the original failure evidence; rollback restores the known deep-path
limitation. The existing Profile-v3 backup/rollback requirements are separate.

Trusted production signing and an independent clean Windows machine/VM are still
unavailable, as previously confirmed by the user. Full-material semantic targets,
live ATS/mailbox flows and independent human acceptance also remain open.

## Official references checked October 30

- [Windows path-length limitations](https://learn.microsoft.com/en-us/windows/win32/fileio/maximum-file-path-limitation)
  explain why file APIs and shell behavior can differ and why a registry setting
  alone does not make every application support long paths.
- [Final path from a retained handle](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-getfinalpathnamebyhandlew)
  documents the existing native API reused by this change.
- [NTSTATUS reference](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-erref/596a1078-e883-4972-9bbc-49e60bebca55)
  identifies the observed native loader status. No external implementation source
  was copied.
