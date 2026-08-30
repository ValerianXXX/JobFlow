# Roadmap

Checked items describe implemented engineering scope, not a claim of live compatibility with every recruiting site.

The current machine-readable status is available through `jobflow product-capabilities`; see [support-matrix.md](support-matrix.md).

## Available in the alpha

- [x] Bootstrap-first Windows installer, health check, and bilingual local UI. The public entry point does not use a system Python, create a virtual environment, or build the runtime from source; the legacy source installer remains regression-only.
- [x] DPAPI Candidate Profile, Answer Bank, source management, and versioned Claim review.
- [x] AI-required entity reconstruction, source grounding, metric preservation, and Claim quality gates.
- [x] Immutable Master Resume with applicant-approved tailoring positions and material-use authorization.
- [x] Visible official-company job discovery and verified company-published Apply routing.
- [x] One application-specific confirmation for approved answers, materials, and user-present assistance.
- [x] Transactional review queue with a user-selected pending limit.
- [x] Bound company, Greenhouse, Lever, Workday, Ashby, and SmartRecruiters browser contracts with per-page revalidation.
- [x] Approved field fill, approved material attachment, and one-use non-final navigation authorization.
- [x] Persistent progress, safe URL cancellation, emergency stop, and non-retryable unknown-state recovery.
- [x] User-present local processing cadence with explicit run-now, pause, and resume controls and no operating-system scheduler.
- [x] Explicit, expiring read-only discovery for exact approved HTTPS sources, with a current-user Windows task, candidate inbox, pause, emergency stop, and fail-closed recovery.
- [x] Synthetic Greenhouse, Lever, Workday, Ashby, SmartRecruiters, browser, security, migration, and release evidence.
- [x] Hash-bound multi-step saved-page evidence for company-hosted forms, Greenhouse, Lever, Workday, Ashby, and SmartRecruiters, with duplicate-field reconciliation and final-submit lock checks.
- [x] Provider-host Lever evidence for approved prefill, resume attachment, explicit non-final Continue, and a user-only final Submit boundary.
- [x] Redacted, hash-only, 30-day page/route acceptance evidence that never promotes a single run into universal provider support.
- [x] Current-tree and full-history privacy scanning plus deterministic source archives.
- [x] Local update verification, health-check, rollback, and pinned-key verification plumbing for fixed Windows installations; no background updater.

## Next priorities and separately authorized work

- [ ] Broader live compatibility evidence for provider-specific Greenhouse, Lever, Workday, Ashby, SmartRecruiters, and custom company forms; the existing expiring page/route report is not a universal compatibility claim.
- [ ] Publish the complete Windows runtime through the protected two-stage signing handoff. Runtime-closure engineering and the bootstrap-first consumer path are implemented, but local workstation QA cannot create an official signature or authorize the public asset.
- [ ] Complete and record a clean stock-Windows acceptance run against that externally signed public runtime. Until this evidence exists, the installer remains fail-closed when the pinned release lacks an acceptable schema-v2 asset and is not advertised as generally available one-click installation.
- [x] Modern-component binding for native controls, choice groups, LWC/custom selects, input or button ARIA comboboxes, open/closed Shadow DOM, and exact-signature rebinding after verified page redraws, with provider-host browser evidence for all six supported route families and redacted per-field failure diagnostics.
- [x] User-downloaded, schema-validated support diagnostics with no private values or automatic transmission.
- [x] Optional local fixed-code incident history with explicit opt-in, bounded retention, user clearing, and no automatic transmission.
- [x] Reduced duplicate applicant confirmation through canonical Candidate Profile mapping from resume import through encrypted application reuse.
- [ ] Unattended application processing remains disabled. Read-only discovery is the sole background scope and cannot open Apply, fill, upload, navigate, or submit.

Final submission, unattended application operation, credential handling, verification bypass, email, account creation, recruiter contact, and automatic retry remain outside the current product boundary.
