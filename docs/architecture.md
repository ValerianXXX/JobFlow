# Architecture

JobFlow is a Windows-first local application with an optional Browser Companion. The repository contains code, schemas, tests, synthetic fixtures, and public documentation. Applicant data and runtime state live outside the public tree.

## Main components

### Local UI and server

The bilingual UI binds only to `127.0.0.1`. It manages onboarding, AI connection, application review, queue state, and user-present browser sessions.

### Secure storage

Candidate Profile, Answer Bank, master resume, material copies, and approval packets are encrypted with Windows DPAPI under Local AppData. Project records retain only opaque references and non-sensitive metadata.

### AI Operator

The AI Operator gives an approved Hermes, OpenClaw, or loopback model a constrained task contract and redacted workflow state. Document understanding runs through an isolated zero-tool channel. AI output must pass structure, source coverage, metric, and provenance checks before review.

### Evidence and workflow gates

External claims require approved personal evidence. The internal workflow moves through deterministic, persisted states and normally stops at `AWAITING_APPROVAL`. Changed evidence, forms, answers, materials, or routes invalidate approval.

### Browser Companion

The fixed-ID extension is distributed through Chrome Web Store and Microsoft Edge Add-ons. A user-scoped native messaging host, registered by the Windows installer, provides the extension with an installation-specific local binding secret. The secret is excluded from Git, store packages, and release archives. An unpacked Local AppData runtime exists only as a development fallback.

For one approved application, the companion can:

- inspect sanitized structure on a bound company or supported ATS origin;
- fill approved native fields, choice groups, validated custom selects, and ARIA comboboxes across browser-visible component roots;
- recover from a component redraw only when every remaining field and protected control can be uniquely rebound to its original structural signature;
- attach approved materials;
- activate one verified, explicitly non-final navigation control;
- observe the result after a trusted user Submit click.

It cannot submit, read credentials, bypass verification, create accounts, send messages, or retry unknown external state.
If a field, component, or protected control disappears or becomes ambiguous, the companion stops and reports a redacted field type and page position; it does not guess or replay the write.

### Authorized read-only discovery

An optional current-user Windows task may wake a local runner only after explicit authorization that expires within seven days. The encrypted configuration binds exact HTTPS public company or supported ATS sources and local match terms. Each run uses DNS and TLS validation, rejects redirects and private or mixed-address destinations, enforces response limits, and writes only public candidate metadata after revalidating the authorization generation.

This task has no Browser Companion or application authority. It cannot open Apply, inspect or modify an application form, fill fields, upload files, navigate, create an application, send messages, or submit. Pause, authorization expiry, three consecutive runs containing source errors, uninstall, and the emergency stop remove the Windows task. Rollback instead serializes with the task and preserves its authorization so the next wake resolves the restored application version. Application processing remains user-present.

### Signed desktop updates

Only a fixed per-user Windows installation can update itself. A visible user action opens the updater; there is no background poller, scheduler, service, or silent download. The updater reads the fixed `ValerianXXX/JobFlow` stable release endpoint, rejects unapproved redirect hosts, verifies a canonical manifest with the public RSA key pinned in the installed source, verifies the exact archive name, version, commit, size, SHA-256 digest, layout, and public-content boundary, and only then stages installation. The existing version remains active until the new version passes its health check. A failed check restores the previous validated version.

The release private key is stored separately under the current Windows user's Local AppData using DPAPI and a current-user-only ACL. It is never included in Git, source archives, manifests, signatures, diagnostics, or command output. Release-key rotation is intentionally not an ordinary command-line option.

## Trust boundaries

- Job descriptions, pages, files, email, and AI output are untrusted input.
- Knowledge sources are read-only.
- Applicant values never enter ordinary logs, reports, command arguments, or public files.
- Browser permissions are granted by the user and scoped to the active application.
- Login, CAPTCHA, MFA, legal, signature, sensitive, and unknown fields are user handoffs.
- Final Submit is not implemented. Read-only discovery is the only unattended scope; unattended application operation is not implemented.

## Public and private paths

| Location | Purpose |
|---|---|
| Repository | Code, schemas, synthetic fixtures, public docs |
| `%LOCALAPPDATA%\JobOps\private` | DPAPI-encrypted applicant data |
| `%LOCALAPPDATA%\JobOps\BrowserCompanionHost` | User-scoped native messaging host and manifest |
| `%LOCALAPPDATA%\JobOps\BrowserCompanion` | Unpacked development fallback only |
| `%LOCALAPPDATA%\JobOps\ReleaseSigning` | Publisher-only DPAPI release key; never distributed |
| Project `state`, `reports`, and `dist` | Local runtime or release artifacts excluded from Git |

## Recovery

Persisted internal steps may resume after revalidation. Any interruption during an uncertain external write or submission window becomes `SUBMISSION_UNKNOWN`; JobFlow never retries it automatically.
