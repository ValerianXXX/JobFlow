# Changelog

Notable JobFlow changes are listed here. The project follows semantic versioning. Entries marked **release candidate** or **alpha** are not claims of universal live-site compatibility.

## [Unreleased]

### Added

- Added an explicit, user-initiated stable update path for fixed Windows installations, with a pinned RSA release key, canonical signed manifests, archive hash and content-boundary verification, post-switch health checks, and automatic rollback on failure.
- Added a local release-signing and signed-update-bundle workflow whose private key remains DPAPI-protected outside the repository and release artifacts.
- Added a fixed, versioned per-user Windows installation with stable Start menu launch, health-check, rollback, and uninstall entries.
- Added a marked persistent runtime data root for state, queues, reports, and application workspaces outside immutable application versions.
- Added a user-present local processing cadence with explicit pause, resume, and run-now controls for already saved work.
- Added saved-job JSON discovery and single-form synthetic browser contracts for Ashby and SmartRecruiters.
- Added a schema-validated, user-downloaded support diagnostic file containing only versions, safe runtime states, aggregate counts, safety boundaries, and a redacted error code.
- Added an optional local incident history that is off by default and stores only bounded fixed error codes, version metadata, and timestamps for explicit user-reviewed support export.
- Added a keyboard skip link, stronger live-region semantics, high-contrast support, and reduced-motion behavior to the bilingual local UI.

### Changed

- Reinstalling from a newer source package now stages and health-checks the new version before an atomic current-version switch.
- Rollback preserves the Candidate Profile, encrypted private material, queue state, and local reports.

### Safety

- Update checks never run in the background; each check begins with an explicit user action, follows only the fixed GitHub release channel, rejects unsigned or malformed assets, and preserves the current version until the new version passes validation.
- The release-signing tool reuses an existing key and provides no silent key-rotation switch.
- Runtime data paths reject path escape and reparse points, and installer ACLs are applied recursively for the current Windows user.
- Uninstall preserves user data by default; permanent data removal requires two explicit confirmation flags.
- The cadence never registers a Windows task, starts a background service, opens a website, or performs a browser, network, upload, or submission action.
- Ashby and SmartRecruiters fixtures are local engineering evidence only; both remain `live_site_verified=false` and require current page validation in real use.
- Support diagnostics never decrypt or export applicant values, resumes, answers, Claims, local paths, credentials, tokens, or secure references, and are never transmitted automatically.
- Local incident capture accepts no error messages, stack traces, URLs, paths, documents, or applicant values, retains at most 32 records, and can be cleared without affecting onboarding data.

## [0.4.1] - 2026-08-17

### Added

- Prepared Browser Companion 0.9.0 for Chrome Web Store and Microsoft Edge Add-ons distribution with complete English listing copy, synthetic store images, and a public privacy policy.
- Added a user-scoped native messaging host so the signed store extension can authenticate the local JobFlow installation without bundling private binding data.
- Added deterministic store-package generation and release tests for package contents, native-host identity, privacy disclosures, and required assets.

### Changed

- Store installation is now the normal user path. Unpacked extension loading remains available only for local source development.

### Fixed

- Corrected native-host file ACL propagation so the current Windows user can read and execute the installed host while access remains user-scoped.
- Browser Companion 0.9.1 recognizes the signed Chrome Web Store and Microsoft Edge Add-ons identities in addition to the deterministic development identity.
- The local JobFlow service now accepts CORS requests only from those three allowlisted extension origins, and the web UI probes all supported store identities without broadening website access.

### Safety

- The native host exposes only an installation identifier and local binding secret to explicitly allowed JobFlow extension IDs; applicant values and page contents never pass through it.
- Final Submit remains user-only, and unknown external outcomes remain non-retryable.

## [0.4.0] - 2026-08-16

### Added

- Added an adaptive Candidate Profile that reuses resume-backed identity and contact facts while asking only unresolved or sensitive questions once.
- Added persistent linear progress, one-click browser continuation, and a continuous AI Operator decision ledger.
- Added ranked official-company job discovery with AI-bound live-page verification and safe automatic fallback to the next candidate.

### Fixed

- Accepted ATS controls may now disappear after an exact, audited write without being misreported as site tampering; unapplied fields, navigation, and final Submit remain protected.
- Aligned offline ATS evidence, user-present prefill and upload support, scoped non-final navigation, and live compatibility wording across the schema and UI.

### Safety

- Browser Companion 0.8.0 keeps final Submit user-only, rejects unknown or changed page identity, and never automatically retries partial or unknown external outcomes.
- Official-job ranking and AI decisions remain hash-bound, value-minimized, and subject to local deterministic revalidation.

## [0.3.1] - 2026-08-16

### Fixed

- Reused confirmed resume identity, contact, and structured address fields instead of asking for them again per application.
- Applied approved fields in live page order and improved TEKsystems-style choice controls after ATS redraws.
- Reported the safe field label, page position, and failure reason when a partial page apply stops.

### Safety

- Upgraded the Browser Companion to 0.7.2 with automatic signed pairing, preflight detection, and safe initial-tab handoff while preserving user-only final submission and no automatic retry after partial or unknown outcomes.

## [0.3.0] - 2026-08-16

### Added

- Added a visible, user-started workflow for official-company job discovery and AI ranking of observed results.
- Added verified company-published Apply routing and a one-confirmation, user-present application pipeline.
- Added persistent preparation progress, safe URL cancellation, and automatic continuation after trusted non-final navigation.

### Changed

- Unified Windows installation, launch, and Browser Companion preparation.
- Moved saved-page and three-snapshot tools under advanced offline QA.
- Upgraded the Browser Companion to 0.7.0.

### Safety

- Final Submit remains user-only.
- Login, CAPTCHA, MFA, legal or sensitive questions, ambiguous routes, and changed page identity stop for the user.
- Partial or unknown external state is recorded once and never retried automatically.

## [0.2.5] - release candidate

- Added the provider-neutral AI Operator for Hermes, OpenClaw, and local models.
- Added AI-assisted understanding for component-based forms and Shadow DOM controls.
- Restored the intended page order and persistent progress indicator.
- Added an authorized live acceptance path that stops at `AWAITING_USER_SUBMIT`.

## [0.2.4] - 2026-08-15

- Moved long-running application-form analysis into an idempotent local background task.
- Improved Windows launcher isolation and deterministic release packaging.
- Replaced raw browser abort errors with actionable local guidance.

## [0.2.1] - release candidate

- Added native Windows Hermes discovery from the official Local AppData installation.
- Added zero-tool stdin use of the currently selected Hermes provider and model.
- Added WSL discovery for both `venv` and `.venv` installations.
- Added a clear release-readiness block for extracted source packages without Git metadata.

## [0.2.0] - release candidate

- Added user-present, per-application, multi-page assistance for bound company, Greenhouse, Lever, and Workday routes.
- Added approved-only field fill, approved material attachment, and one-use non-final navigation authorization.
- Added trusted user-submit observation and non-retryable `SUBMISSION_UNKNOWN` recovery.
- Added the fixed-ID Browser Companion and synthetic browser security tests.

## [0.1.0]

- Established the local evidence pipeline, encrypted applicant onboarding, Claim review, document QA, queueing, and the `AWAITING_APPROVAL` boundary.
