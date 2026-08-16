# Public Release Checklist

Run these checks only on the final clean commit. This checklist does not authorize a recruiting action.

## Automated gates

- [ ] Full tests, schemas, leak scans, knowledge checks, and external-action audits pass.
- [ ] `python -m jobops.public_release` reports zero current-tree and history findings.
- [ ] `python -m jobops.release_candidate` produces two identical archives and passes isolated startup smoke.
- [ ] `Start JobFlow Demo.cmd` remains synthetic-only.
- [ ] `python -m jobops.release_readiness` reports no blockers.

## Human gates

- [ ] Confirm a public Git author identity, preferably a GitHub noreply address.
- [ ] Freeze the exact commit and run fresh independent QA.
- [ ] Approve sanitized screenshots with no personal data or user paths.
- [ ] Complete a clean supported Windows profile test.
- [ ] Confirm repository metadata and private vulnerability reporting.
- [ ] Create an annotated or signed release tag.
- [ ] Obtain explicit user authorization before any push, asset upload, or Release publication.

## Release facts

- The source ZIP is the complete Windows application candidate; the wheel is only a CI packaging smoke artifact.
- Alpha releases do not claim universal live ATS compatibility.
- Real external actions must remain 0 during build and release QA.
- Separately authorized live acceptance tests must stop before final Submit and reconcile every nonzero browser action in the append-only audit.
