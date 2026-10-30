# Documented experience and Profile destinations

Milestone date: 29 October 2026, America/New_York. UTC observations are preserved in the accompanying evaluation records. This is source preparation, not a release or a claim that JobFlow meets the complete-material acceptance target.

## Correctness problem

Profile generation used the current year minus the earliest year mentioned in confirmed work or internship entities. A job held only from 2018-01-01 through 2018-12-31 therefore became eight years at the frozen 2026-10-29 evaluation date. The same calculation included career gaps and treated a generic duration as sufficient for skill-specific requirements.

The new `experience.py` module calculates calendar coverage from confirmed, non-deleted work entities. It unions intervals so gaps contribute nothing and overlapping or repeated jobs are not counted twice. Source Claim IDs, calculation method and UTC snapshot date remain inside the encrypted Profile. Internship time is counted separately as excluded, rather than silently treated as equivalent to work tenure.

Dates use explicit ISO year, month or day precision. For partial dates, the lower bound counts only complete calendar months guaranteed by all possible endpoints; the upper bound counts every potentially touched month. Thus January–December with only month precision spans 10–12 guaranteed/possible months, while January 1–December 31 spans exactly 12. These are calendar bounds, not full-time equivalents or hours worked. Unsupported localized date strings need confirmation rather than guessed conversion. Missing, reversed, future or conflicting dates leave an unresolved entity. `Present`, `Current`, `Ongoing` and the supported Chinese equivalents use the Profile snapshot date and do not silently accrue time in an immutable Profile later. Bilingual token fixtures remain in the duration tests.

Only exact calendar totals supply the legacy scalar `years_experience`; otherwise it remains `UNKNOWN`. Eligibility can use a documented lower bound for a simple generic experience requirement. It cannot infer years of Python, an industry, management or full-time work. A short documented history does not establish that the applicant has no other experience, so insufficient evidence remains unknown. Ranges and unsupported expressions also require review. Decimal requirements are parsed as decimals rather than their final digit.

## Validation

The frozen 18 test methods were committed at `327f9995b40fbadb80d463a0461932963c190d13` before implementation, against `c6c64d33ebc83bfacc43641b588cd7ba5cf7a4c9`. Every method exposed a failure or error: unittest reported 28 failed assertions/subtests and four errors across those 18 methods. These are not 32 independent test methods. The original log is retained locally and hashed in the verification record.

After repair, the unchanged 18 methods passed. Eleven additional tests cover leap years, adjacent jobs, partial months, missing source references, conflicting work/internship identity, invalid metadata, UTC dates, v2 readability and required v3 provenance. All 29 passed. The final related suite passed 149 tests in 647.820 seconds, including onboarding, schema contracts, material generation and the existing offline application preparation suite. An earlier 116-test pass before the schema-version refinement remains recorded separately.

The prior source-review head's full run **failed**: 1,308 tests, one failure, three errors and four symlink skips in 3,662.019 seconds. Its build-only interpreter lacked MCP/anyio and the optional Docling packages, causing three integration failures/errors. A rejected clean-browser pairing request also raised WinError 10053. A second full run in this worktree was stopped after discovering the inherited dependency gap; its partial output is retained, not counted as a full pass. The declared `.[build,mcp,document]` dependencies were then installed into this worktree using constraints from the already available versions, with no project dependency-policy change. Isolated imports and `pip check` passed. The HTTP rejection repair and a valid complete regression remain follow-up work; this milestone does not claim them complete. See the [verification record](../evaluations/2026-10-29/experience-duration-verification.json).

The tests exercise actual Profile construction and eligibility evaluation with synthetic confirmed-Claim fixtures. They do not approve real Claims, verify an employer or complete an application. Web assets and model prompts were unchanged. No new AI requests or paid experiments were run, and no model-quality or user-effort improvement is claimed.

## Profile hint measurement

Before the duration change, the [six-case receipt](../evaluations/2026-10-29/profile-hint-destinations-before.json) replayed the existing hash-pinned synthetic observations through real parsers, Windows DPAPI source confirmation and service reopen:

- All six email hints matched their source, remained encrypted and survived reopening. Their source marker stayed `APPLICANT_PROVIDED_UNCONFIRMED`, and Profile review stayed `PENDING`.
- The six numeric synthetic display names were not split into given/family-name fields. That conservative behavior is not permission to guess a legal name or proof that normal names work universally.
- Remote-only preferences were not imported. Work authorization stayed unknown. Other constraints, missing-information notes and verification limitations still need distinct reviewed destinations.
- No Claim was selected or confirmed; no completed Profile, application, approval or external action was created. Each temporary state directory was removed.

This is an unapproved-hint persistence measurement, not Profile completion. The historical 241/282 Claim-candidate representation result remains unchanged. Original live-model costs remain unknown; these six replays made no provider calls.

Reproduce only these pinned synthetic sources on Windows:

```powershell
python scripts/measure-profile-hint-destinations.py --output reports/profile-hints-new.json
python -m unittest discover -s tests -p "test_experience_duration*.py" -v
python -m unittest discover -s tests -v
```

The collector accepts an output destination only, refuses to overwrite observations and cannot accept applicant files or fall back to a provider. Real onboarding still requires the existing secure import and review flow.

## Compatibility, operation and rollback

No new dependency, model configuration or database schema migration is required. New completed Profiles carry `profile_schema_version: 3` and a validated `experience_calculation`. The new reader accepts existing version-2 and legacy unversioned Profiles. A v2 generated duration without provenance no longer passes a years gate; use **Start revision**, review the source dates and Claims, and complete the ordinary Profile/Claim approvals. Existing completion and approval records are not rewritten. An independently supplied unversioned legacy scalar retains its established explicit-input contract for generic requirements, with finite/nonnegative numeric validation; it is not upgraded into source-derived evidence.

Older application code does not understand v3 Profiles. Before a separately approved deployment, retain the existing encrypted-data backup and earlier immutable completion records. Roll back the application together with the matching pre-upgrade data backup if v3 Profiles have been created; preserve post-upgrade records separately for recovery with the newer reader. Do not strip provenance, relabel v3 as v2 or edit approval records to make an older binary accept them. This recovery requirement is documented preparation, not clean-Windows or production rollback acceptance.

Production signing and an independent clean Windows environment remain unavailable. Remaining product work includes non-Claim constraint destinations, broader semantic/full-material evaluation, field-specific and full-time duration verification, source/Claim review, live ATS/mailbox acceptance and independent user testing. This fix prevents a documented calculation error; it does not establish the full product goal.
