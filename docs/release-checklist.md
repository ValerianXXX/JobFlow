# Public Release Checklist

Run these checks only on the final clean commit. This checklist does not authorize a recruiting action.

## Automated gates

- [ ] Full tests, schemas, leak scans, knowledge checks, and external-action audits pass.
- [ ] `python -m jobops.public_release` reports zero current-tree and history findings.
- [ ] `python -m jobops.release_candidate` produces two identical archives and passes isolated startup smoke.
- [ ] `scripts/build-signed-update-bundle.ps1` produces and locally verifies the source ZIP, `JobFlow-update-manifest.json`, and `JobFlow-update-manifest.sig.json` against the pinned stable key.
- [ ] `Start JobFlow Demo.cmd` remains synthetic-only.
- [ ] `python -m jobops.release_readiness` reports no blockers.
- [ ] Authorized discovery lifecycle tests prove registration, renewal, pause, expiry, repeated-failure pause, emergency stop, rollback serialization and continuity, and uninstall cleanup without any browser or application action.

## Human gates

- [ ] Confirm a public Git author identity, preferably a GitHub noreply address.
- [ ] Freeze the exact commit and run fresh independent QA whose report names that commit and the deterministic source ZIP SHA-256.
- [ ] Approve sanitized screenshots with no personal data or user paths.
- [ ] Complete a clean supported Windows profile test.
- [ ] Confirm repository metadata and private vulnerability reporting.
- [ ] Create an annotated or signed release tag.
- [ ] Upload exactly the source ZIP named by the signed manifest, `JobFlow-update-manifest.json`, and `JobFlow-update-manifest.sig.json` to the matching stable GitHub Release; do not publish the signing key or its DPAPI ciphertext.
- [ ] From an older isolated fixed installation, click **Check for updates**, verify the expected version is installed, and verify a deliberately failed post-switch health check rolls back.
- [ ] Obtain explicit user authorization before any push, asset upload, or Release publication.

## Release facts

- The source ZIP is the complete Windows application candidate; the wheel is only a CI packaging smoke artifact.
- A Release without all three signed-update assets is not an update-capable stable Release and must not become GitHub's latest stable Release.
- Alpha releases do not claim universal live ATS compatibility.
- Real external actions must remain 0 during build and release QA.
- Separately authorized live acceptance tests must stop before final Submit and reconcile every nonzero browser action in the append-only audit.
