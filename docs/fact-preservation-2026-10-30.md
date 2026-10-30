# Complete entity facts and limitations - October 30, 2026

The document-analysis prompt now requires complete entity summaries and keeps
factual scope, responsibility and qualification in the relevant Claim candidate.
Source notes remain supplemental. This addresses omissions observed in the
[preceding source-context experiment](source-context-2026-10-30.md); it does not
make completeness a deterministic guarantee or establish product acceptance.

## Change and compatibility

For a stated work, education or project entity, preserve its exact name, full
role, supervision or individual-contributor qualifier, dates and explicitly
stated full-time/part-time status. A named project's organization metadata uses
that project name without inventing an employer. Keep completed/proposed work,
synthetic examples, unmeasured outcomes and applicant/team/AI responsibility
boundaries in the candidates they qualify. Do not move those facts solely into
separate source notes or infer attributes that are absent.

The request/output schema, encrypted state, provider selection, local-model
compatibility and manual approval boundaries remain unchanged. There is no new
dependency, setting, automatic retry, migration or production action. Existing
cloud-backed configurations can send source text to their configured provider.
Longer candidate output can change usage and cost; no saving is assumed.

## Frozen comparison and observed result

`SOURCE_FACT_PRESERVATION_V1` was committed at `93cb574` before dispatch. It
allowed exactly two new requests, one for each previously exposed English and
Chinese chronological transcript, with no retry and a stop on the first failed
arm. Source bytes, transcripts, previous reviews, product files and route were
hash-bound. Labels and target fact IDs were not sent to the model. The unchanged
route was Hermes `afe53708ee7400919ae3e17a6dde9b02629e5c87`, model `gpt-5.6-sol`,
provider `openai-codex`; this identifies the tested environment, not a claim that
it is the newest model.

Both responses returned and passed the host contract. The protocol is **closed**:
two new adapter requests, four across this and the preceding protocol, below the
original six-request campaign ceiling. The shared common-Git attempt journal
prevents a second worktree from restarting this protocol. Do not delete the
journal, change directories or remove observations to repeat it.

The coding assistant checked all **94 source facts, 68 candidates and 12 notes**.
Full-fact mappings include source ranges and preserve the same denominator:

| Fixed transcript | Before source-context change: full facts in candidates | Preceding context response: full facts in candidates | New response: full facts in candidates | New facts present only as unconfirmed context |
| --- | --- | --- | --- | --- |
| English chronological | 41 / 47 | 36 / 47 | 41 / 47 | 4 |
| Chinese chronological | 40 / 47 | 23 / 47 | 41 / 47 | 4 |

All four newly missing/incomplete facts from the preceding response are now
fully represented: completed versus proposed work, the Chinese individual role,
full-time status and synthetic prototype addresses/business names. All nine
earlier non-contact omissions remain retained in the appropriate candidate or
context channel. The English project name now also appears in entity metadata.
The reviewed pilot, SQL, language and ownership qualifiers are present in the
candidate wording. The assistant observed no new number, date, ownership or
qualifier error in these two outputs; an independent reviewer has not checked
that judgment. One additional English synthetic-header candidate is supported
by the source but receives no extra gold-fact credit.

Candidate-or-context retention is 45/47 in each sample. This mixed-channel count
is **not Claim recall**, confirmed Profile completion or the 95% full-material
acceptance target. Identity/contact facts stay in the 47-fact denominator and
are not relabeled as Claims. The original six-material 241/282 result remains
historical and unchanged. The other four materials were not rerun here.

Adapter elapsed time was 40.578 seconds English and 56.219 seconds Chinese;
complete analysis-arm elapsed time was 40.844 and 56.312 seconds. The preceding
requests were 43.672 and 72.078 seconds, respectively, but one exposed response
per prompt/case cannot establish a general latency improvement. Reported tool
calls were zero. Billed cost, token usage, provider-internal requests and
independent user correction effort are unknown, not zero.

## Evidence and reproduction

- [Frozen protocol](../evaluations/protocols/fact-preservation-v1.json)
- [Unedited live observation](../evaluations/2026-10-30/fact-preservation-live-v1.json)
  and [attempt receipts](../evaluations/2026-10-30/fact-preservation-live-v1.requests.jsonl)
- [Original-PDF and DPAPI replay](../evaluations/2026-10-30/fact-preservation-replay-v1.json)
- [Per-fact and candidate decision ledger](../evaluations/2026-10-30/fact-preservation-review-v1.json)
- [Verification receipt](../evaluations/2026-10-30/fact-preservation-verification.json)

The offline replay pins each original response by hash and checks the exact
analyzed text. It uses actual synthetic PDF parsing, Windows DPAPI, source
confirmation and service reopen. Six notes per source and matching original
bytes persisted; all six Profile constraint answers stayed unchanged. No Claim,
application, approval or external action was created. Temporary state was
removed. This is actual local pipeline verification with recorded model output,
not a second live observation or an independent person's acceptance.

After the README's per-worktree setup, reproduce without a provider call:

Use a full Git checkout for historical verification. The
[later verifier repair](frozen-evaluation-review-2026-10-30.md) checks the recorded
experiment commit instead of requiring today's product files to remain frozen.
It retains all original observations, protocol hashes and live-run guards.

```powershell
.\.venv\Scripts\python.exe scripts/verify-fact-preservation-review.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_fact_preservation*.py' -v
.\.venv\Scripts\python.exe scripts/replay-source-context.py --observation fact-preservation-v1 --output reports/my-fact-preservation-replay.json
```

The checker validates references, unchanged history and denominators. It cannot
certify semantic judgments. Its tests reject lost facts, unrelated candidate
references, invented context, rewritten history and false product acceptance.
The replay accepts only the two named, hash-pinned observations, has no provider
fallback and refuses to overwrite its output. The original observation remains
the default; historical receipts are not rewritten.

Twenty-nine source-context contracts passed after the prompt change. The seven
new budget/review contracts also pass. This change adds no UI behavior; the
preceding bilingual actual-browser checks belong to their recorded source
version. Full regression of the preceding context commit was still running when
this receipt was written; do not transfer a previous commit's pass to this one.

## Remaining work and rollback

The prompt still relies on model behavior. Complete-material acceptance across
the frozen 36-material corpus, protected holdouts, visual transcription,
conflicting sources, independent review and real workflow acceptance remain
open. Evidence-gap resolution and complete non-Claim Profile handling are also
separate work. Trusted production signing and independent clean-Windows
installation/upgrade/rollback remain unavailable.

Use a normal reviewed revert of the prompt change in `93cb574` for behavioral
rollback while retaining all experiment history. No database migration is
required. A prompt rollback does not change already retained private candidates;
review or explicitly reanalyze the source under the desired version. Existing
approval invalidation and final manual submission remain required. This draft
does not authorize a release or deployment.
