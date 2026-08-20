# Product support matrix

JobFlow publishes a machine-validated support disclosure instead of claiming universal recruiting-site compatibility.

Run:

```powershell
python .agents\skills\job-application-operator\scripts\jobops.py product-capabilities
```

To inspect the current machine's redacted, expiring page/route acceptance evidence, run:

```powershell
python .agents\skills\job-application-operator\scripts\jobops.py live-acceptance
```

The report distinguishes:

- `AVAILABLE`: implemented and backed by reproducible local evidence.
- `CONDITIONAL`: implemented, but dependent on a prepared local AI/runtime, browser state, or current live-site acceptance.
- `NOT_AVAILABLE`: not yet part of the product; an offline mock or fake-clock test does not change that status.
- `USER_ONLY`: a permanent human boundary, including final application submission.

Every row is hash-bound to its status, evidence references, known limits, user-presence rule, and safety boundary. The ATS report additionally hashes the current bytes of every declared fixture, test, and Browser Companion source file. It fails closed if evidence disappears, changes without a regenerated report, or no longer matches the declared provider scope.

The ATS report partitions eleven stages into `verified_stages` and `unverified_stages` for each provider. Those sets must be complete and non-overlapping. Generic Browser Companion evidence is published separately and is never promoted into provider-specific acceptance.

The live-acceptance report is a separate local disclosure. It records only SHA-256-bound evidence from an explicitly user-present public HTTPS page and expires it after 30 days. It never stores or reports an origin, URL, page text, applicant value, credential, or material path. Counts remain page/route-specific: one successful page does not establish provider-wide compatibility, and failed or blocked runs remain visible rather than being converted into passes. Reserved example domains, local hosts, synthetic fixtures, and isolated test modes never create live-acceptance evidence.

Redacted support diagnostics are `AVAILABLE` as a user-initiated local download from the visible support panel. Optional incident history is also `AVAILABLE`, but remains off by default and stores at most 32 fixed error codes with version and time metadata on the local device. It cannot accept messages, stack traces, URLs, paths, applicant values, documents, credentials, or tokens. Nothing is transmitted automatically; the user reviews and explicitly attaches a diagnostic file to a support request.

Expiring read-only background discovery is `AVAILABLE` for exact user-approved HTTPS company-careers pages and tenant-bound public Greenhouse, Lever, Ashby, and SmartRecruiters job-list endpoints. A recognized board root is mapped deterministically; an individual job URL is never widened. It may add public job metadata to a local candidate inbox for no more than seven days per authorization. This does not promote any candidate into an application and grants no browser, Apply, fill, upload, navigation, messaging, or submission authority. Unattended application operation remains `NOT_AVAILABLE`.

The checked-in provider set and current evidence boundary are:

- Company-hosted forms: discovery, route binding, form analysis, value-free planning, approved DOM prefill, approved file attachment, explicit non-final navigation, and modern-component rebinding.
- Greenhouse: discovery through the local review packet plus synthetic provider-host DOM prefill, approved file attachment, explicit non-final Continue, and a user-only final Submit boundary. Multi-page resume, result observation, and live-site acceptance remain unverified.
- Lever: discovery through the local review packet plus synthetic provider-host approved DOM prefill, approved resume attachment, a user-only final Submit boundary, and synthetic result observation. Provider-specific non-final navigation and live-site acceptance remain unverified.
- Workday: discovery, review, synthetic provider-host approved DOM prefill and resume attachment, explicit non-final navigation, representative multi-page resume, a user-only final Submit boundary, and synthetic result observation. Live-site acceptance remains unverified.
- Ashby: discovery, route binding, and form analysis only.
- SmartRecruiters: discovery, route binding, and form analysis only.
- Shared Browser Companion runtime: synthetic tests cover approved DOM prefill, approved file attachment, explicit non-final navigation, and modern-component rebinding independently of any provider claim.

The static provider matrix remains `live_site_verified=false`; current local page/route evidence is disclosed only in the separate expiring report. Final Submit remains `USER_ONLY`, and an unknown submission outcome is never retried automatically.
