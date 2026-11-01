# Current Claim wording and review

October 31, 2026, America/New_York. This source milestone repairs wording history
and review invalidation. It is not a signed release, a new model evaluation or
evidence that the complete material-understanding target has been met.

## What changes

Previously, the browser reset a directly edited Claim's decision, but the backend
could retain an old confirmation when an edit arrived without a new decision.
Restoring a deleted Claim could retain rejection. A corrected ancestor could also
leave merged/split descendants confirmed. Human-written merged/split text carried
the same displayed AI-filtered status as an original candidate.

The backend now resets affected decisions independently of the browser. The UI
shows whether the current words are an original AI candidate, user edit, user
merge/split, or have incomplete history. Every label still requires fact review.
Neither a matching hash nor the legacy AI admission flag proves factual accuracy.

1. Edit a Claim in Profile & Claim review. Its decision returns to pending and
   Profile confirmation clears. Review the source, choose a decision and save.
2. Merge same-entity Claims or split one Claim. The new words remain pending and
   are labelled as user-authored; no additional AI call occurs.
3. After changing/restoring an ancestor, inspect every affected descendant and
   the original material. Save the upstream change first, then confirm each
   descendant in a subsequent review. A sibling remains pending until reviewed.
4. Reload to inspect saved wording and decisions. Completed onboarding still
   requires **Start revision** before any edit; external material approval remains
   a separate action on the new completed revision.

No-op saves preserve decisions. An explicit confirmation submitted together with
a direct edit confirms that new wording; the browser clears the old selection so
the user must choose again. Descendant confirmations in an ancestor-edit request
are ignored, because the browser submits all decisions and those may be stale.
Deleted items stay rejected; restoring them without new confirmation is pending.

Narrow screens now give each Claim its own full-width text/decision column. Desktop
layout and the existing merge, split, deletion and source controls are retained.

## Architecture and compatibility

The pure `claim_wording` module owns the current-wording digest, origin receipt and
transitive dependency traversal. The existing synchronized onboarding service owns
validation, encrypted state writes and decisions. The UI supplies edits and choices;
it cannot submit a trusted origin record. There is no new provider, database,
framework, model call or dependency.

A digest binds statement, category and entity fingerprint. Initial source selection
retains the digest of the **original** AI candidate before any user edit. Merge and
split retain parent Claim IDs and their wording digests at transformation time.
Subsequent manual edits preserve those historical parent snapshots. Original source
bytes and the immutable candidate bundle are not rewritten.

The private `ai_validated`/`analysis_mode` admission predicate remains unchanged to
keep rule-derived legacy Claims quarantined. It describes analysis ancestry only;
the new `wording_provenance` describes the current text. Missing legacy material
receipts are not backfilled with invented candidate proof. Known legacy merge/split
records keep their human origin but have no reconstructed parent snapshots. A
stored receipt that does not match current wording is displayed as unverified.
This check is not a general forensic audit of a tampered private store.

Optional metadata stays in existing encrypted onboarding state and its explicit
local review response. Original source bindings, Knowledge Gateway, completed
snapshots, Claim material approval and final-submit boundaries are unchanged.
No database migration or new configuration is required; `.env.example` continues
to apply. Hermes and configured local/cloud routes retain their existing settings.

UI protocol is **42**. Restart the backend and reload the UI together; the existing
compatibility check refuses mixed protocol versions. Desktop/Companion compatibility
is unchanged (source 0.7.0, Companion 0.9.2 / protocol 2). New optional metadata is
ignored by old code, which also lacks these labels and review invalidation rules.

## Verification and limits

Baseline: the seven existing selected approval/revision/transformation tests passed.
Nine new test methods were committed before implementation. After correcting the
test's existing-preview access path, the frozen baseline reported three failures
and six errors across methods/subcases: the absent metadata and three observed
review-state defects. The original fixture mistake and both failed logs remain
retained; they are not nine independent historical defects.

The unchanged frozen tests pass on tested runtime `c50b5bf` (9 methods, 12.868 s).
Delivery runtime `5f49b0b` has the identical Git tree. Before pushing, the three local
commits were replayed onto a fresh branch with the owner's account-linked noreply
author/committer; the original local branches remain unpushed and intact. The
public frozen-test equivalents are `54d1db3` and its fixture correction `80ee8bf`.
No code or assertion changed during that identity correction. The tests cover
implicit edit invalidation, explicit current-wording confirmation, no-op saves,
delete/restore, unchanged/edited import selection, merge/split ancestry, transitive
review invalidation, legacy history and mismatched receipts. Timing is not a speed
comparison because the test environments and concurrent workloads differed.

The first related-test command passed 100 real tests but also requested a nonexistent
`test_source_context` module, so the command correctly failed with one import error.
The actual persistence/review modules were then run separately: 13 passed. No
assertion or test was removed, and the original failed invocation remains visible.
All **19 JavaScript/browser suites passed**, including existing Companion, PDF,
source review, message-outcome, standard-resume and workbench flows. The complete
Python suite (1,581 collected locally) is running; hosted checks are recorded
separately when terminal. Related checks are not substituted for a complete pass.

An actual Windows/Edge browser run uses loopback HTTP, Windows DPAPI and synthetic
candidate fixtures. It exercises edit, merge, split, ancestor restore/change,
individual subsequent confirmation, both languages, reload and desktop/narrow
viewports. Initial screenshots exposed wasted mobile width; the follow-up uses a
single column and checks usable text width. These are coding-assistant-operated
synthetic scenarios, not independently recruited users or observed model output.
The source fixtures deliberately seed their original AI flags; no real model is
invoked and no real applicant material or mailbox is read.

A separate interactive browser check edited a synthetic Chinese statement, saved,
reloaded and reopened review. It observed the changed Claim pending, the two other
Claims still confirmed and Profile confirmation cleared. The actual viewport
[shows that persisted state](../evaluations/2026-10-31/claim-wording/interactive-reload-zh.jpg).
The test service and temporary encrypted store were closed and removed afterward.

[Verification receipt](../evaluations/2026-10-31/claim-wording/verification.json)
binds logs, screenshots, source/test commits and the unsigned wheel. New AI quality,
latency or cost improvement is **not established**. Full-material recall, unsupported
facts, attribution/number accuracy, live ATS/mailbox use and independent first-user
acceptance remain open. Trusted production signing and independent clean-Windows
installation/upgrade/rollback remain unavailable.

## Run and rollback

Use the repository's existing Development setup. Then:

```console
python -m unittest discover -s tests -p test_claim_wording_review.py -v
node tests/claim_wording_ui_e2e.cjs
```

The browser fixture requires Windows DPAPI, Edge and the existing locked Playwright
dependencies. It accepts only its synthetic fixture mode and removes its isolated
temporary state on exit. It does not accept a private data directory or credentials.

Before switching application versions, stop the local service and back up its
encrypted private store, reference database and onboarding state as one coherent
set using the existing backup process. Roll back code/UI together through a
reviewed normal commit or installed-version rollback; do not rewrite Git history or
manually alter decisions. Source data requires no conversion. An older build can
edit records without maintaining the new receipt, so returning to this build may
show incomplete history and require review. Restoring an old backup loses later
edits and must never be silently mixed with newer references. No production
deployment, signing, release, extension-store update or migration was performed.
