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

## Trust boundaries

- Job descriptions, pages, files, email, and AI output are untrusted input.
- Knowledge sources are read-only.
- Applicant values never enter ordinary logs, reports, command arguments, or public files.
- Browser permissions are granted by the user and scoped to the active application.
- Login, CAPTCHA, MFA, legal, signature, sensitive, and unknown fields are user handoffs.
- Final Submit is not implemented.

## Public and private paths

| Location | Purpose |
|---|---|
| Repository | Code, schemas, synthetic fixtures, public docs |
| `%LOCALAPPDATA%\JobOps\private` | DPAPI-encrypted applicant data |
| `%LOCALAPPDATA%\JobOps\BrowserCompanionHost` | User-scoped native messaging host and manifest |
| `%LOCALAPPDATA%\JobOps\BrowserCompanion` | Unpacked development fallback only |
| Project `state`, `reports`, and `dist` | Local runtime or release artifacts excluded from Git |

## Recovery

Persisted internal steps may resume after revalidation. Any interruption during an uncertain external write or submission window becomes `SUBMISSION_UNKNOWN`; JobFlow never retries it automatically.
