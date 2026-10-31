# Native bundle-fixture path repair — October 31, 2026

This is a test-infrastructure correction on top of the [release verifier repair](release-trust-timing-2026-10-31.md). It does not change JobFlow application behavior, extend production path support, approve a release or establish full material understanding.

## Preserved failure and diagnosis

The parent preparation branch's local full run tested 1,525 cases in 3,859.662 seconds: two errors and four capability skips. Both errors occurred in `_prepare_bundle_fixture` while copying tracked evidence into the disposable repository, before the two intended release-guard assertions. The failing destination names were 263 and 260 characters long. That run remains failed; its four missing-Windows-symlink-capability cases remain unverified.

An actual Windows Python 3.11.3 filesystem probe reproduced the boundary. Ordinary copying worked at 259 characters and failed at 260, 263 and 300; explicit extended paths preserved the same bytes and modification time at all four lengths. Three frozen contracts then exercised long source/destination names, a 270-character parent directory and actual Git indexing with local `core.longpaths=false`. Before the repair, they produced one failure and two errors in 0.518 seconds. The identical contracts pass after the repair in 0.634 seconds. This is a reliability correction, not a speed improvement.

The first experimental contract run also exposed cleanup failures. Its raw observation is retained separately from the three frozen counterexamples. The first deep integration check copied all 980 tracked files without a byte/mtime mismatch but found another ordinary-path access in the synthetic signing-key timestamp assertion; that intermediate failed check is retained too.

## What changed

- Extract the existing tracked-file copying loop so it can be exercised with real, isolated Git repositories. Keep the complete tracked inventory; do not omit long evidence names.
- Use the repository's existing `_windows_extended_path` helper for source reads, destination creation/copying and synthetic key timestamp checks.
- Add `-c core.longpaths=true` only to fixture Git invocations. The counterexample verifies that the fixture's existing `.git/config` bytes remain unchanged. No user/global Git configuration or Windows registry setting is changed.
- Clean only the newly created fixture directory after checking that its resolved absolute path lies strictly below the test root. Use extended paths for cleanup and retain read-only Git-object handling.
- Preserve the original two tests' protected-stage, untouched-key, absent-output and no-transaction-leftover assertions. Production source, signing scripts, trust policy, workflow jobs, timeouts and dependencies remain unchanged from base `6bee3e47aa38538a9cc03d611224eea4a9ee1170`.

## Verification record

The [machine-readable receipt](../evaluations/2026-10-31/bundle-fixture-paths/verification.json) binds the source and portable logs. Exact terminal results are recorded there; pending checks are not treated as successful. The deep integration runner uses a 150-character parent directory, verifies all tracked copies, invokes both original tests and verifies removal of its owned temporary directories. It uses actual Windows filesystem, Git, PowerShell and synthetic DPAPI fixture operations; no production signing is performed.

The deep rerun passed both original cases in 33.670 seconds, including their untouched-key and absent-output assertions. The complete related `test_signed_update*.py` group passed **60/60**, with zero skips, in **272.320 seconds**. All 34 existing signed-update test names and per-method assertion counts remain present. An earlier invocation of the same group failed two cases because it omitted the existing pinned `JOBFLOW_TEST_NODE_PATH` prerequisite; that actual trust rejection remains recorded. The corrected invocation used the existing exact-hash Node 24.19.0 binary, with no code, assertion, policy or timeout changes between the two suite runs.

The parent PR #34 Windows 3.12 and 3.13 CI passed 1,525 Python cases in 3,324.258 and 3,834.935 seconds respectively, their browser checks and build steps. Those different-host results do not erase the local full-run failure; the 3.11 job remained pending at the latest observation. PR #35's exact delivery passed its native trust checks and source-candidate job; its full Windows matrix was still running when this follow-up began. Results belong to their recorded revision and host.

## Run and review

Use the project's supported Windows development setup and existing authenticated test prerequisites. Run:

```powershell
python -m unittest discover -s tests -p test_signed_update_fixture_paths.py -v
python -m unittest discover -s tests -p test_signed_updates.py -v
python -m unittest discover -s tests -p test_public_docs.py -v
python evaluations/2026-10-31/bundle-fixture-paths/run-deep-check.py
```

The first command creates synthetic long paths regardless of a short checkout. It neither enables an OS policy nor downloads/replaces tools. The second retains the full signed-update checks, including the two originally failing cases. The final command repeats the full tracked-inventory and deep integration probe; its file count reflects the revision being checked. Use the pinned fixture Node prerequisite documented in [native Node selection](release-node-selection-2026-10-31.md) when running the full repository or `test_signed_update*.py` suite. No new configuration is required.

References checked October 31, 2026: [Microsoft's Windows path semantics](https://learn.microsoft.com/en-us/windows/win32/fileio/maximum-file-path-limitation) and [Git for Windows long-path guidance](https://gitforwindows.org/faq#i-get-errors-trying-to-check-out-files-with-long-path-names). The helper pattern comes from this repository's existing MIT-licensed deep-source-archive test at the base revision. No third-party code or dependency was imported.

## Limits and rollback

There are zero new model calls and no measured AI-quality benefit in this change. Signed installation, upgrades, data preservation and rollback on an independent clean Windows machine remain blocked by the previously recorded missing signer and machine. Material-quality acceptance, independent first-use acceptance and the pending additional six-call request remain separate and unresolved.

No migration, production deployment, extension-store update or public release is needed for this test-only correction. A reviewed revert restores the earlier fixture behavior and its long-path limitation; keep all failed observations. Finish any owned test processes before changing a checkout. Do not clean historical failure directories or alter global settings as a workaround.
