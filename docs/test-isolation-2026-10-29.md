# Full-regression follow-up - 29 October 2026

The initial local full run at source commit
`3024fb15cb65d94529a7ca71bbfffcbc0f97b7b1` **failed**: 1,293 tests in
5,971.070 seconds, seven failures, two errors and four skips. Do not replace this
observation with the earlier 203 related passes or with a passing hosted matrix.
The redacted [verification receipt](../evaluations/2026-10-29/test-isolation-verification.json)
keeps every failing test ID, original log hash and later observations.

## Diagnosis and bounded repairs

The product changes were not reverted to make the suite pass. These concrete
conditions were reproduced and repaired:

| Initial failures | Observed cause | Repair and verification |
| --- | --- | --- |
| Three material export contracts, standard-resume missing-converter handling, Word subprocess hygiene | The runner selected LibreOffice globally, while the tests mocked Word or supplied only a synthetic PowerShell exporter. They entered the other backend, so the intended error/redaction checks were never exercised. | The original five pass when explicitly selecting Word. Seven Word-specific tests now temporarily bind that selection and restore the caller's environment. Existing assertions remain, and capability discovery additionally asserts the provider identity. |
| JavaScript release-tree validation | This worktree had no `node_modules`; another worktree's dependencies cannot satisfy its bounded release validator. | Copy only the already-installed public dependency tree after the existing validator accepts its exact committed hash; validate the copied tree again. This is a local setup repair, with no dependency/policy change. The normal documented setup is locked `npm ci --ignore-scripts`. |
| Runtime-input wrapper, one-click health check and CLI version | A local `.venv` existed but had been created without installing the project. Isolated launcher imports correctly rejected it. | Install this worktree's existing `.[build]` requirements into its own environment, then run `pip check`, isolated CLI import, health and wrapper tests. No signature, canonical-path or dependency-integrity gate was relaxed. |

A first broader focused rerun exposed two additional environmental assumptions:
capability discovery and encrypted-PDF commit rollback mocked Word availability
without selecting Word. Those failures are also retained. They are included in
the seven repaired tests, rather than hidden by configuring an installed converter.

The final focused run passes **37 distinct tests**, including all original nine
failures/errors and the existing LibreOffice rejection, timeout, cleanup and stale
output contracts. It ran with an outer `libreoffice` selection. The seven Word
contracts also pass with an intentionally invalid outer provider, after which the
caller value is unchanged. These seven overlap the 37; they are not seven additional
distinct cases. Neither contract run is a new real Microsoft Word conversion test.

## Verification scope

- Local regression runner: Python 3.11.3, Windows; existing development environment.
- Worktree launcher environment: isolated Python 3.11.3 with the declared build
  dependencies; `pip check` and isolated CLI version pass.
- Locked JavaScript tree: 173 files, 18,516,238 bytes, SHA256
  `df85481a30bcbc0f7276548d245eba0980d136c8085ca4a3662cb6f3c568302f`.
- Source production modules, AI prompts, model adapters, dependency policies,
  application authorization and release approval values are unchanged by this repair.
- No UI changed. Prior actual browser observations retain their original source
  versions; the focused run is not a new browser or human acceptance study.
- No live model, mailbox, employer, signing or deployment action occurred.

The hosted source candidate and Windows Python 3.11 jobs for the original commit
were observed successful on 29 October. Python 3.12 and 3.13 remained in progress
at that observation. See the [original run](https://github.com/ValerianXXX/JobFlow/actions/runs/40951607759).
This is evidence for that commit and runner configuration only. The corrected local
full discovery and this branch's hosted result must be reported separately when
finished; this addendum does not assert their success in advance.

## Reproduce and roll back

Follow the README's complete Development setup separately in each worktree. In
particular, install the project into `.venv`, install the locked Node tree, and verify
both before the lengthy full suite. A source interpreter alone is not an installed
launcher environment. Source development and unsigned candidates do not satisfy
trusted production signing or independent clean-Windows acceptance.

To exercise the isolation issue after setup:

```powershell
$env:JOBFLOW_PDF_EXPORT_PROVIDER = 'libreoffice'
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_materials.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_standard_resume.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_windows_subprocess_hygiene.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_local_pdf_export.py -v
```

These contracts do not install office software or authorize conversion of private
documents. Full discovery remains `python -m unittest discover -s tests -v` with
the documented trusted prerequisites. Keep its output in a new local report file;
do not overwrite the initial failed observation.

Rollback of this test/documentation-only change restores prior test bindings;
there is no production data migration, application rollback or approval change.
The full 36-material semantic recall/critical-fact target, live ATS and mailbox
acceptance, trusted signing, clean-Windows lifecycle checks and independent human
acceptance remain open. Passing tests do not establish those capabilities.
