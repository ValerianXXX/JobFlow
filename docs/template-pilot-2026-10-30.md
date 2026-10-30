# Additional complete-material observations and actor attribution

Checked October 30, 2026. JobFlow still has not met complete-material or
independent-user acceptance. This stage adds two real model observations and a
small runtime refactor driven by an observed error, followed by offline replay.

## Frozen scope and actual calls

Protocol `MODEL_TEMPLATE_PILOT_V1` was committed at `fa2fa7b` before dispatch.
It selected the English timeline-card and Chinese asymmetric-sidebar PDFs from
the unchanged 36-material corpus. Each has 47 authored facts and three pages.
These two families had parser measurements but no prior recorded model output.
They share authored content with development examples, and the coding assistant
knows the corpus; they are not independent blind holdouts. They are now exposed
model regression families, including their other language variants. The frozen
manifest and gold facts remain unchanged.

The public Hermes selection was verified before and after: `gpt-5.6-sol`,
`openai-codex`, Hermes revision `afe53708ee7400919ae3e17a6dde9b02629e5c87`.
No model, provider, dependency or account setting was changed. Only hash-pinned
public synthetic material was sent through the existing zero-tool transport.
There was one adapter request per file, zero replacement attempts and zero tool
calls. The earlier source-context/fact-preservation campaign used four requests;
this final pair brings that campaign to its existing six-request ceiling.
Its exclusive common-Git journal is closed. Do not reset, resume or repeat it.

Both actual previews completed. End-to-end local measurements were 38.547 and
56.797 seconds; these include parsing, provider work and Windows encrypted
storage, not just inference latency. Provider-internal requests, token usage and
billed cost are unknown. No new paid service was enabled. Model results from
different earlier stages must not be pooled into a current-version quality score.

## Observed error and implementation

The English source leaves the performer of an ambiguity decision unspecified.
Its candidate names the applicant as the performer. The original validator
accepted it despite the missing attribution. All 69 accepted candidate statements,
94 authored facts and 10 source-context notes were inspected against the recorded
source transcripts. The review is a coding-assistant judgment, not independent
human adjudication or a certified semantic score.

The existing direct-subject checks now live in `claim_attribution.py`, preserving
their behavior and avoiding further growth of the main AI orchestration module.
A separate conservative check recognizes a bounded set of English passive
verbs and Chinese passive marking/classification forms. It rejects an active
candidate with a named performer unless the citation explicitly supports that
performer. A by-agent in a later coordinated action cannot supply the earlier
actor. An exact supported active clause remains usable. Rejection cannot expand
into adjacent lines to find a different action and invent attribution.

The unsupported candidate is filtered with `UNSUPPORTED_ACTOR`; only its reason
count appears in diagnostics. Source bytes remain retained and reviewable, other
candidates remain unselected, and no wording is mechanically rewritten. The
prompt also asks the model to preserve unassigned passive wording. That prompt
change has no fresh model comparison because the campaign is exhausted.

The grammar is deliberately limited. Synonyms outside its verb list, pronouns,
implicit subjects, complex coordination and broader semantic entailment remain
unverified. Broad citations containing mixed passive actors can require narrower
evidence. Passing this check is not proof of responsibility or factual truth.

## Same-response comparison

The repaired code at `6416258` reuses the exact two saved responses through actual
original-PDF parsing and Windows DPAPI. Task and analyzed text must match, each
response can be consumed once, and there is no provider fallback.

| Measure | English timeline | Chinese sidebar |
| --- | ---: | ---: |
| Complete authored facts present in extracted text | 47/47 | 47/47 |
| Accepted candidates before repair | 35 | 34 |
| Accepted candidates after replay | 34 | 34 |
| Observed unsupported actor candidate removed | 1 | 0 |
| Facts represented by reviewed supported candidates | 41/47 | 41/47 |
| Additional facts only in unconfirmed source context | 3 | 4 |
| Additional identity/contact facts in unconfirmed hints | 2 | 2 |
| Facts remaining only in source after rejection | 1 | 0 |

All other 68 candidate statements, context quotes and Profile hints are retained.
Original encrypted bytes match, and all temporary synthetic state was removed.
Both live and replay database counts for applications, approved Claims,
approvals and external actions were zero. These are preview tests, not completed
onboarding, approved application packets, employer interactions or browser tests.

The original English error is retained in the observation and review ledger.
Removing it does not create a replacement fact or improve recall automatically.
Context notes, header hints and Claims have different approval boundaries and
must not be added together into a claim of 95% Claim recall. Complete acceptance
across all 36 materials remains open; 28 still lack first model observations,
and the previously observed eight span different product versions.

## Verification and reproduction

Six passive-attribution test methods were frozen at `545654c`: nine failing
assertions/subtests before the repair. The same six pass after it; a seventh
cross-action agent-boundary test also passes. An earlier test draft used four
short generic English sentences that failed unrelated lexical-grounding checks;
those fixtures were made specific before freezing, without changing product
requirements. Both original failure logs are retained.

All 64 AI tests, 121 related source/onboarding/historical-review tests, seven
attribution tests and nine protocol/replay contracts pass. These overlapping
test groups are not summed into a completion-rate metric. Full hosted regression,
packaging and public-boundary results are reported in the delivery PR and receipt.

- [Closed protocol](../evaluations/protocols/model-template-pilot-v1.json)
- [Original live observation](../evaluations/2026-10-30/model-template-pilot-v1.json)
- [Immutable request journal](../evaluations/2026-10-30/model-template-pilot-v1.requests.jsonl)
- [Same-response replay](../evaluations/2026-10-30/model-template-replay-v1.json)
- [Per-fact and candidate review](../evaluations/2026-10-30/model-template-review-v1.json)
- [Verification receipt and retained log hashes](../evaluations/2026-10-30/model-template-verification-v1.json)

After the README's per-worktree development setup:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_passive_attribution.py' -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_template*.py' -v
.\.venv\Scripts\python.exe scripts/replay-template-pilot.py
```

The replay writes one fixed fresh report and refuses to overwrite it. Historical
review reads validate frozen inputs; current product pins are checked only before
fresh live dispatch. The already-published observation prevents another live run.
The two-case measurement used the existing validation interpreter with this
worktree's source imports. It was not a clean installation or a full local-suite
run in a newly populated environment. Hosted CI prepares its own environment.

No configuration, dependency, schema or migration changes. Rollback is a reviewed
revert of the attribution module and matching AI runtime change, followed by a
rebuild. It restores the earlier validation gap; it does not edit retained source
files, approved Claims or observations. Existing material must be explicitly
reprocessed and reviewed to use the new validation. Final submission remains the
user's action. Trusted signing, independent clean Windows and independent-user
acceptance remain unavailable.
