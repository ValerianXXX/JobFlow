# Public Release Checklist

Run these checks only on the final clean commit. This checklist does not authorize a recruiting action.

## Automated gates

- [ ] After creating the final commit and confirming the worktree is clean, run the repository's trusted `scripts\run-release-verification.ps1` entry from the repository root. It binds the report to that HEAD, runs the full tests, schemas, leak scans, knowledge checks, JavaScript end-to-end suites, packaging checks, and JobFlow external-action audits, and fails if HEAD or the worktree differs at its checkpoints. This local run is best-effort evidence; it does not provide protected-environment source, dependency, or network isolation.
- [ ] `python -m jobops.public_release` reports zero current-tree and history findings.
- [ ] `python -m jobops.release_candidate` produces two identical archives and passes isolated startup smoke.
- [ ] The deterministic Browser Companion store ZIP version matches `browser-companion/manifest.json`, and the packaged Chrome and Edge listing URLs pass exact HTTPS host and extension-ID validation.
- [ ] Run `scripts/build-signed-update-bundle.ps1 -Stage Prepare` only with explicit absolute paths to the exact complete-runtime ZIP, embedded runtime closure, canonical build evidence, canonical publisher evidence, the pinned official embeddable Python ZIP named by `config/windows-runtime-source.json` via `-ReleasePythonArtifactPath`, and explicit version policy. The signing workflow never downloads Python or any other artifact. It verifies and holds the supplied local ZIP, safely extracts its exact bounded runtime closure into protected staging, and executes only that staged interpreter. Prepare must emit only `JobFlow-update-manifest.presign.json` and the pathless `JobFlow-update-signing-request.json`, then stop with `JOBFLOW_PROTECTED_SIGNATURE_REQUIRED`. It must never invoke a signer or create a formal manifest or signature. Prepare serializes concurrent writers and rolls back ordinary replacement failures. A failed rollback stops with `JOBFLOW_RELEASE_PRESIGN_RECOVERY_REQUIRED` and retains available backups. If the process or host crashes between its two non-authoritative output replacements, discard both files and rerun Prepare; a hash-mismatched pair fails closed and must never be signed.
- [ ] `Start JobFlow Demo.cmd` remains synthetic-only.
- [ ] Before protected outputs exist, `python -m jobops.release_readiness` reports `public_release_ready=false`, `runtime_closure_status=UNATTESTED`, and stable missing/invalid attestation blockers. It may report ready only after the exact signed manifest, signature, runtime ZIP, canonical runtime and publisher evidence, and clean-Windows evidence all verify against the frozen version and commit.
- [ ] `jobops verify-release` keeps `public_repository_ready` separate from `public_release_ready`; its local checkpoint remains best-effort and cannot substitute for the protected signed evidence consumed by `jobops.release_readiness`.
- [ ] Authorized discovery lifecycle tests prove registration, renewal, pause, expiry, repeated-failure pause, emergency stop, rollback serialization and continuity, and uninstall cleanup without any browser or application action.

## Human gates

- [ ] Confirm a public Git author identity, preferably a GitHub noreply address.
- [ ] Freeze the exact commit and run fresh independent QA whose report names that commit and the deterministic source ZIP SHA-256.
- [ ] Approve sanitized screenshots with no personal data or user paths.
- [ ] Complete a clean supported Windows profile test.
- [ ] Confirm both official Chrome and Edge listings are public, report the exact version required by `browser-companion/manifest.json`, and install and pair that version on clean browser profiles.
- [ ] Confirm repository metadata and private vulnerability reporting.
- [ ] Keep all public repository metadata, release notes, store copy, and support text English-only; bilingual product UI remains inside the application.
- [ ] Create an annotated or signed release tag.
- [ ] Do not upload a stable signed-update set until an external protected signing environment attests and locks the complete executable runtime closure. Local workstation QA is not public-signing authorization.
- [ ] Obtain the detached signature envelope from the external protected signer. No private key, signing command, credential, or protected signing implementation may be copied into this repository or the development workstation.
- [ ] Run `scripts/build-signed-update-bundle.ps1 -Stage Finalize` with the original exact inputs, including the same absolute `-ReleasePythonArtifactPath`, exact presign manifest and request, and the external signature envelope. Finalize never downloads a runtime. Confirm that it rebuilds both presign artifacts byte-for-byte, rechecks current evidence freshness and the clean candidate commit, verifies the pinned production public key, stages the exact runtime as `dist/JobFlow-v<version>-windows-x64-complete.zip`, stages `dist/JobFlow-runtime-build-evidence.json` and `dist/JobFlow-publisher-evidence.json`, and commits the formal manifest/signature pair last. A failed commit must roll back the staged payloads or stop with an explicit recovery gate.
- [ ] Run release QA in an isolated protected builder that immutably binds the full source tree, Git object/ref state, Python runtime and installed packages, JavaScript dependency tree, and network policy for the entire run. Start/end local hashes alone are not this attestation.
- [ ] Only after protected runtime-closure attestation and signed assets exist: from an older isolated fixed installation, click **Check for updates**, verify the expected version is installed, and verify a deliberately failed post-switch health check rolls back.
- [ ] On the clean Windows machine, export one canonical `JOBFLOW_CLEAN_WINDOWS_ACCEPTANCE_V1` observation bound to the exact app version, Browser Companion version, source commit, signed manifest/signature/archive hashes, runtime closure, and test time. Evidence older than 24 hours, from a different commit, or from different Chrome/Edge store versions is not reusable.
- [ ] Back in the frozen release checkout, import that file with `jobops import-clean-windows-acceptance --input <evidence.json> --version <version> --commit <40-character-commit>`. The importer accepts no alternate destination, rejects links, paths, secrets, stale evidence, and binding mismatches before writing, then atomically installs `dist/JobFlow-clean-windows-acceptance.json` and restores any prior evidence if final full-chain verification fails.
- [ ] Obtain explicit user authorization before any push, asset upload, or Release publication.

## Release facts

- The source ZIP is the complete Windows application candidate; the wheel is only a CI packaging smoke artifact.
- A Release without all three signed-update assets is not an update-capable stable Release and must not become GitHub's latest stable Release.
- Alpha releases do not claim universal live ATS compatibility.
- Real external actions must remain 0 during build and release QA.
- `LOCAL_BEST_EFFORT_VERIFICATION_PASS` is local QA evidence only. It never authorizes public signing, tagging, upload, or Release publication.
- No environment variable, configuration value, acknowledgement, or force switch can bypass protected evidence, current-time validation, exact presign binding, or the external production-signature gate.
- The repository does not contain protected signing capability. An absent external signature is an expected release gate, not a locally repairable signing failure.
- Separately authorized live acceptance tests must stop before final Submit and reconcile every nonzero browser action in the append-only audit.
