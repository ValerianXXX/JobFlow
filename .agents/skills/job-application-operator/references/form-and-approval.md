# Form and approval gates

## Sensitive-field matrix

| Classification | Examples | Result |
|---|---|---|
| `ordinary_fixed` | portfolio, LinkedIn, public website | Prefill only a known fixed value |
| `private_fixed` | legal name, email, phone, address | Resolve from `secure-ref`; never log value |
| `sensitive_review` | start date, travel, relocation | `STOP_REQUIRED` |
| `work_authorization_stop` | authorization, sponsorship, visa | `STOP_REQUIRED` |
| `compensation_stop` | salary or compensation | `STOP_REQUIRED` |
| `legal_declaration_stop` | background, non-compete, truth attestation | `STOP_REQUIRED` |
| `signature_stop` | typed/electronic signature | `STOP_REQUIRED` |
| `voluntary_disclosure_stop` | EEO, race, gender, disability, veteran | Stop entire section |
| `account_creation_stop` | registration or password | `NEEDS_ACCOUNT_APPROVAL` |
| `file_upload_stop` | resume, cover letter or other file input | Separate external upload approval; blocked in this build |
| `navigation_control_stop` | Next, Continue or other multi-step navigation | Reviewed browser action; never clicked by offline analysis |
| `final_submit_stop` | submit/image control | Always gated |
| `unknown_stop` | unrecognized field | Fail closed |

Classification uses label, id, name, type, autocomplete, options, placeholder, help, aria, section, adjacent text and page context in Chinese and English. Review output for stopped fields contains only state, reason, secure reference and redacted summary.

`analyze-ats-form` accepts only a project-local UTF-8 HTML snapshot bound to a verified source-route hash. It never starts a browser or network request. Existing input/textarea/select values and hidden controls are discarded; the ordinary report contains no raw labels or selectors, only opaque control references, prompt hashes, counts and classifications. Form actions and iframes crossing the verified host become STOP blockers. CAPTCHA, MFA, login, account creation, upload and final-submit signals also stop.

A browser action plan binds the exact route hash, canonical URL, semantic form snapshot hash and each opaque control reference. It may propose only `ordinary_fixed` values represented by content hash or `private_fixed` values represented by `secure-ref`; protected and unknown controls cannot be changed into prefill actions even if a plan is tampered with. The offline adapter validates this plan against the current snapshot and performs zero field modifications, uploads, browser actions or network actions.

Workday-style multi-step forms use an ordered local snapshot sequence. Each saved page is independently hashed and classified, the sequence hash binds its exact order, and duplicate page snapshots fail closed. DOM-specific control references may change after a React re-render; a separate logical-field hash based on provider, semantic answer key, classification, control type and prompt hash deduplicates the same question across steps without retaining selectors or labels. Sequence analysis performs no Next/Continue navigation.

## Approval binding matrix

Every approval binds application/job IDs; JD snapshot and freshness hashes; route hash and canonical URL; ATS tenant/board/job identity; profile version; Claim set/version hash; form and answer hashes; Review Packet hash; every upload filename/purpose/hash; exact normalized external actions; site policy; issue/expiry; nonce; and approval version.

Any change invalidates the approval. `AWAITING_APPROVAL → APPROVED` requires the current persistent binding. Approval is atomically consumed when a synthetic submission attempt begins; replay fails. Production submission always fails with `PHASE_NOT_AUTHORIZED` in this release. `SUBMITTED → CONFIRMED` requires a validated receipt record.
