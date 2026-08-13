# State and recovery

State and append-only event are written in one transaction. `last_safe_state` records only a completed internal state; it never records a blocking state. Recovery revalidates current input/context hashes before movement.

| Blocking state | Automated behavior | Safe user path |
|---|---|---|
| `NEEDS_USER_INPUT` | Stop | Confirm unknowns, then revalidate |
| `NEEDS_ACCOUNT_APPROVAL` | Stop | Review account need; real creation remains disabled |
| `BLOCKED_LOGIN` | Stop | User handles login separately |
| `BLOCKED_CAPTCHA` | Stop | User handles challenge; never bypass |
| `SITE_CHANGED` | Invalidate approval; keep blocked | New local snapshot, then rerun the full route/form/packet chain |
| `APPROVAL_EXPIRED` | Invalidate approval | Fresh review and approval |
| `MATERIALS_NEEDS_CORRECTION` | Keep blocked until reprocessing | Correct inputs and rerun generation plus QA |
| `INELIGIBLE` | Never continue automatically | Close or explicit human override plus reanalysis |
| `SUBMISSION_UNKNOWN` | Never retry | Manually verify external evidence or close |

The migrations are forward-only and repeatable. Version 1 remains the legacy dry-run schema; version 2 adds exact bindings, append-only Claim/action records, private refs and recovery; version 3 adds transactional intake. Rollback is backup restoration or application downgrade after a compatibility review, not destructive table dropping.
