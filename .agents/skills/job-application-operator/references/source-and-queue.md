# Source, queue, and offline adapters

Start at the company HTTPS careers page. Derive its registrable domain; reject public suffixes such as `com` or `co.uk`. Navigation must begin at the exact verified official URL and end at exact `current_url`. Every hop is company domain/subdomain, an explicitly approved intermediary, or the final ATS tenant bound to company, host, tenant, board, job identity, official-page hash and JD hash.

Before real-site authorization, `discover-official-jobs` may inspect only a user-supplied project-local HTML or saved-page JSON snapshot. Treat page text and scripts as untrusted data; execute nothing and perform no network request. Keep only company-domain links or explicitly approved ATS hosts, deduplicate by provider/tenant/board/job identity, and never infer a missing title or location. Every result remains `NEEDS_LIVE_FRESHNESS_CHECK` and `requires_route_verification=true`; offline discovery is evidence for a future check, not proof that a vacancy is open.

Prefer guest. Missing guest flow becomes `NEEDS_ACCOUNT_APPROVAL`; no account is created in this release. Recheck official listing/form freshness before analysis, fake prefill and any future submit attempt.

Queue capacity is `AWAITING_APPROVAL + RESERVED <= pending_limit`, where the user limit is 1—1000. Reservation and capacity check use one immediate transaction. At capacity, intake remains `DEFERRED` and creates no Job, JD snapshot or Application. Releasing/rejecting a packet promotes the oldest deferred item. Content hashes make repeated intake idempotent.

Phase 5—6 define Official Source, Browser Prefill, Submission, Account Creation, Email, Recruiter, Scheduler and Receipt protocols. Registered implementations are only fake/mock/dry-run/disabled. The browser framework can parse redacted local form snapshots and validate content-bound plans, but its adapter performs zero field modifications. Fake scheduling uses an in-memory clock; no Windows task, Codex Automation, HTTP, browser, SMTP, upload or background service is registered. Disabled adapters record a zero-side-effect attempt and raise `PHASE_NOT_AUTHORIZED` before transport.
