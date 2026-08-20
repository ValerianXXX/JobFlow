# Changelog

Notable JobFlow changes are listed here. The project follows semantic versioning. Entries marked **release candidate** or **alpha** are not claims of universal live-site compatibility.

## [Unreleased]

### Added

- Added a complete company-direct local vertical from an official careers route through value-free planning, review approval, and a verified synthetic result, with network and real external actions held at zero.
- Added provider-host synthetic Greenhouse browser evidence for approved field prefill, approved material attachment, and explicit non-final Continue navigation.
- Extended the Greenhouse vertical through applicant confirmation, packet approval, and a verified synthetic result while keeping network and real external actions at zero.
- Added provider-host synthetic Lever browser evidence for approved field prefill, approved resume attachment, and a user-only final Submit boundary.
- Added provider-host synthetic Workday browser evidence for approved field prefill, approved resume attachment, and a user-only final Submit boundary.
- Added provider-host synthetic Ashby and SmartRecruiters browser evidence for approved field prefill, approved resume attachment, explicit non-final navigation, and a user-only final Submit boundary.
- Added full local Ashby and SmartRecruiters review verticals from an official-company route through private-value-free planning, packet approval, and a verified synthetic result, with network and real external actions held at zero.
- Added hash-bound three-step saved application sequences for Greenhouse, Ashby, and SmartRecruiters, including duplicate-field reconciliation and fail-closed upload, navigation, and final-submit evidence.
- Added Lever provider-host browser evidence for one hash-bound, explicit non-final Continue action while preserving the user-only final Submit boundary.
- Added provider-host browser evidence that Greenhouse, Lever, Workday, Ashby, and SmartRecruiters fields can be rebound after an exact-signature framework redraw; ambiguous redraws still stop safely.
- Added a hash-bound three-step company-direct application sequence with duplicate-field reconciliation and fail-closed upload, navigation, and final-submit checks.
- Added a hash-bound three-step Lever application sequence and stable ATS job-identity parsing across application child pages.

### Safety

- Greenhouse final Submit remains user-only, and live-site acceptance remains required per current route.
- Lever final Submit remains user-only, and live-site acceptance remains required per current route.
- Workday final Submit remains user-only, and live-site acceptance remains unverified.
- Ashby and SmartRecruiters final Submit remain user-only, and live-site acceptance remains required per current route.

## [0.6.0] - 2026-08-19

### Added

- Added explicit, expiring read-only discovery for exact user-approved HTTPS company-careers pages and supported public ATS feeds, with encrypted filters, a bounded local candidate inbox, and a current-user Windows wake task.
- Added visible configure, pause, resume, renewal, repair, candidate review, and emergency-stop controls with bilingual actionable errors.
- Added a machine-readable distinction between authorized background read-only discovery and prohibited unattended application operation.

### Changed

- Rollback now serializes with the read-only discovery task, switches only validated version pointers, and preserves the current authorization and task registration so the next wake resolves the restored version.

### Safety

- Read-only discovery authorizations expire within seven days, are generation-bound, fail closed after three consecutive runs containing source errors, and never grant Apply, browser inspection, form fill, upload, navigation, messaging, application creation, or submission authority.

## [0.5.1] - 2026-08-19

### Fixed

- Corrected the GitHub Actions first-install acceptance check so it validates the newly installed fixed runtime instead of looking for a source-tree virtual environment.
- Shortened the isolated acceptance path so the same check also passes on standard Windows runners without long-path support enabled.

### Safety

- The installer acceptance run now uses a validated, disposable per-run `LOCALAPPDATA` and `APPDATA`, suppresses browser registration and launch, checks the installed version pointer and installed health launcher, and removes only its exact temporary root.
- The acceptance evidence remains local and reports zero private-value reads, private-value emissions, network actions, and real external actions.

## [0.5.0] - 2026-08-19

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
- Added an evidence-scoped ATS support report that partitions eleven workflow stages into provider-specific verified and unverified sets.
- Added content hashes for every declared ATS fixture, test, and Browser Companion source file, plus a separately disclosed shared browser-runtime evidence bundle.

### Changed

- Reinstalling from a newer source package now stages and health-checks the new version before an atomic current-version switch.
- Rollback preserves the Candidate Profile, encrypted private material, queue state, and local reports.
- Provider cards and the machine-readable capability report now distinguish provider-specific evidence from generic Browser Companion runtime behavior instead of implying universal execution support.

### Safety

- Update checks never run in the background; each check begins with an explicit user action, follows only the fixed GitHub release channel, rejects unsigned or malformed assets, and preserves the current version until the new version passes validation.
- The release-signing tool reuses an existing key and provides no silent key-rotation switch.
- Runtime data paths reject path escape and reparse points, and installer ACLs are applied recursively for the current Windows user.
- Uninstall preserves user data by default; permanent data removal requires two explicit confirmation flags.
- The cadence never registers a Windows task, starts a background service, opens a website, or performs a browser, network, upload, or submission action.
- Ashby and SmartRecruiters fixtures are local engineering evidence only; both remain `live_site_verified=false` and require current page validation in real use.
- Support diagnostics never decrypt or export applicant values, resumes, answers, Claims, local paths, credentials, tokens, or secure references, and are never transmitted automatically.
- Local incident capture accepts no error messages, stack traces, URLs, paths, documents, or applicant values, retains at most 32 records, and can be cleared without affecting onboarding data.
- Greenhouse, Lever, Workday, Ashby, and SmartRecruiters disclose their exact unverified stages; no saved fixture or generic runtime test is treated as live-site acceptance.

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
