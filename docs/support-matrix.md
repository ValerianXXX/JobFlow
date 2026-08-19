# Product support matrix

JobFlow publishes a machine-validated support disclosure instead of claiming universal recruiting-site compatibility.

Run:

```powershell
python .agents\skills\job-application-operator\scripts\jobops.py product-capabilities
```

The report distinguishes:

- `AVAILABLE`: implemented and backed by reproducible local evidence.
- `CONDITIONAL`: implemented, but dependent on a prepared local AI/runtime, browser state, or current live-site acceptance.
- `NOT_AVAILABLE`: not yet part of the product; an offline mock or fake-clock test does not change that status.
- `USER_ONLY`: a permanent human boundary, including final application submission.

Every row is hash-bound to its status, evidence references, known limits, user-presence rule, and safety boundary. The ATS report additionally hashes the current bytes of every declared fixture, test, and Browser Companion source file. It fails closed if evidence disappears, changes without a regenerated report, or no longer matches the declared provider scope.

The ATS report partitions eleven stages into `verified_stages` and `unverified_stages` for each provider. Those sets must be complete and non-overlapping. Generic Browser Companion evidence is published separately and is never promoted into provider-specific acceptance.

Redacted support diagnostics are `AVAILABLE` as a user-initiated local download from the visible support panel. Optional incident history is also `AVAILABLE`, but remains off by default and stores at most 32 fixed error codes with version and time metadata on the local device. It cannot accept messages, stack traces, URLs, paths, applicant values, documents, credentials, or tokens. Nothing is transmitted automatically; the user reviews and explicitly attaches a diagnostic file to a support request.

The checked-in provider set and current evidence boundary are:

- Company-hosted forms: discovery, route binding, form analysis, value-free planning, approved DOM prefill, approved file attachment, explicit non-final navigation, and modern-component rebinding.
- Greenhouse: discovery through the local review packet. Provider-specific DOM execution remains unverified.
- Lever: discovery through the local review packet plus synthetic result observation. Provider-specific DOM execution remains unverified.
- Workday: discovery, review, explicit non-final navigation, representative multi-page resume, and synthetic result observation. Provider-specific file attachment remains unverified.
- Ashby: discovery, route binding, and form analysis only.
- SmartRecruiters: discovery, route binding, and form analysis only.
- Shared Browser Companion runtime: synthetic tests cover approved DOM prefill, approved file attachment, explicit non-final navigation, and modern-component rebinding independently of any provider claim.

Every provider remains `live_site_verified=false` until separately authorized, current-page acceptance is completed. Final Submit remains `USER_ONLY`, and an unknown submission outcome is never retried automatically.
