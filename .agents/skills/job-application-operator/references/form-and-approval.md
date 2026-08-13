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
| `final_submit_stop` | submit/image control | Always gated |
| `unknown_stop` | unrecognized field | Fail closed |

Classification uses label, id, name, type, autocomplete, options, placeholder, help, aria, section, adjacent text and page context in Chinese and English. Review output for stopped fields contains only state, reason, secure reference and redacted summary.

## Approval binding matrix

Every approval binds application/job IDs; JD snapshot and freshness hashes; route hash and canonical URL; ATS tenant/board/job identity; profile version; Claim set/version hash; form and answer hashes; Review Packet hash; every upload filename/purpose/hash; exact normalized external actions; site policy; issue/expiry; nonce; and approval version.

Any change invalidates the approval. `AWAITING_APPROVAL → APPROVED` requires the current persistent binding. Approval is atomically consumed when a synthetic submission attempt begins; replay fails. Production submission always fails with `PHASE_NOT_AUTHORIZED` in this release. `SUBMITTED → CONFIRMED` requires a validated receipt record.
