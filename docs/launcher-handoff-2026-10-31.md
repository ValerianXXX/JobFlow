# Launcher mutation regression: observed readiness and explicit handoff

Observed October 31, 2026. This change repairs one synthetic installer test's
synchronization and process cleanup. Production installer code, signer checks,
release approval, application behavior and runtime limits are unchanged.

## Failure retained and cause investigated

The quantity-validation branch's local full suite ran 1,548 tests in 7,128.987s:
1,543 passed, one failed and four were skipped because this Windows account could
not create the required symbolic links. The failure was
`test_late_project_launcher_mutation_cannot_change_committed_launcher_bytes`:
the installer did not reach the test's signal within 15 seconds. No mutation or
installed-file assertion ran. The failed path also left two capture streams open.

The same original case, isolated on the public delivery, failed at the same
assertion in 15.215s. Test, installer, bootstrap and lock-helper bytes were checked
against the original full-run checkout and matched. All five hosted jobs for
[PR #37](https://github.com/ValerianXXX/JobFlow/pull/37) passed separately; each
Windows Python 3.11/3.12/3.13 regression ran 1,548 tests without reported skips.
Those hosted passes do not erase the local observation.

A separate diagnostic used the existing dependency-free synthetic installer copy
and retained its original three-second pause. Only stage timestamps were added;
no original test or product limit was changed. With a diagnostic-only 60-second
observation ceiling, it reached the launcher gate at 23.625s and completed at
32.360s, exit 0. Entry was observed at 0.946s, Python search at 2.105–2.618s and
source verification at 3.568s. The late source mutation executed, and the installed
launcher retained the original content. This identifies a premature test gate;
it does not establish why local staging/health checks are slower or prove every
machine completes within a particular time.

## Repair and unchanged correctness requirements

The test now uses **one 45-second total deadline**, preserving the original
15-second readiness plus 30-second completion maximum. This lets staging use the
available budget while still requiring the entire operation to finish in time.

A fixture-only acknowledgement replaces the fixed three-second pause: the copied
installer announces the verified target, waits for the source mutation, records
that acknowledgement, and only then installs stable launchers. The parent proves
the acknowledgement has not happened early, changes the source and verifies that
the child consumed the acknowledgement. It still requires a successful process
exit and the original installed content, and additionally compares the exact
original/installed bytes. No signature, hash, source-lock or activation assertion
is removed or bypassed.

Capture uses owned files rather than unclosed pipes. Failure cleanup uses the
existing owned-process-tree helper, preventing descendants from keeping capture
handles open. Cleanup has its existing separate bound; it is not extra application
execution time. The synthetic fixture's acknowledgement wait is itself bounded.

## Verification and reproduction

| Observation | Result |
| --- | --- |
| Original complete local run | 1 failure / 4 symlink skips / 1,548 tests; retained |
| Original isolated case | Failed before mutation, 15.215s |
| Diagnostic with timestamps | Gate 23.625s; completed 32.360s; original content retained |
| Repaired isolated case | Passed, 30.385s; mutation and exact-byte assertions executed |
| Existing descendant-capture timeout check | Passed, 6.012s |
| New complete hosted run | Pending at delivery; follow the draft PR's exact head |

The repaired isolated test is not reported as a new complete 1,548-test local
pass. [Verification receipt](../evaluations/2026-10-31/launcher-handoff/verification.json)
retains original failures, terminal totals, diagnostic timestamps and hashes.
Earlier model observations and complete-material scores are unchanged. There
were no new model requests, external application actions or deployments.

In the existing Windows development environment:

```powershell
python -m unittest discover -s tests -p test_windows_launchers.py -k late_project_launcher_mutation -v
python -m unittest discover -s tests -p test_windows_launchers.py -k LauncherProcessCaptureTests -v
```

No new production setting or dependency is required. The test changes temporary
synthetic files only. It is not a production-signed installer, an actual browser
extension installation, or an independent clean-Windows install/upgrade/rollback
acceptance. The user-confirmed signing/machine limitations, complete-material
target and independent-human requirements remain open.

Rollback restores the prior test/docs revision on an isolated branch. No product
data, schema, signer approval or production configuration needs to be changed.
