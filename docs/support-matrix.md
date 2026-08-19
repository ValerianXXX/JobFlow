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

Every row is hash-bound to its status, evidence references, known limits, user-presence rule, and safety boundary. The report fails closed if evidence disappears or the product policy drifts. Provider-specific rows remain explicitly subject to live acceptance; saved fixtures and synthetic verticals are not presented as universal live compatibility.

The checked-in provider set is company-hosted forms, Greenhouse, Lever, Workday, Ashby, and SmartRecruiters. Ashby and SmartRecruiters currently have saved provider-JSON discovery evidence plus single-form synthetic browser evidence. Workday has the representative multi-page sequence. Every provider remains `live_site_verified=false` until separately authorized live acceptance is completed.
