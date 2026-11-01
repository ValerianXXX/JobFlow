# Human message outcomes with original evidence

October 31, 2026, America/New_York. This is a source milestone, not a signed
desktop release, authenticated mailbox integration or independent user study.

## User-visible change

An imported message can use wording the conservative interpreter does not
recognize. Previously the user could associate it with an application, but could
not record a different message outcome. The inbox now keeps **Proposed status**
and **Your recorded message outcome** separate. For example, the synthetic phrase
"Please choose a time to speak with our hiring panel." remains an unknown
automated proposal; an explicit human review can record an interview invitation
and retain that exact passage as its basis.

1. Open the status inbox and inspect the original message text.
2. Choose an existing application, select application received, rejected,
   interview invitation or uncertain, and copy a short exact passage from the
   message. Include surrounding text if the passage occurs more than once.
3. Confirm the message, application and outcome, then save the message outcome.
4. Refresh or reopen to inspect the saved result and quotation. To change it,
   select a new result, review the source again and explicitly save. Undo review
   clears the current association and human result while preserving history.

A later manual link or dismissal also retires the current human result. This is
shown beside the controls. The original machine proposal, original EML and
normalized evidence are never rewritten. Reporting uncertainty is a supported
decision, not an error that must be converted into a definite outcome.

An exact quotation proves where the user's explanation came from; it does not
prove the interpretation is correct, authenticate the sender, distinguish all
forwarded-message semantics, establish delivery to an employer or confirm a
submission. The user must still evaluate the message in context. The feature
does not schedule an interview, send mail or change application execution state.

## Architecture and persistence

The pure `mail_review` module validates four outcome values, a 1-500 character
trimmed quote, its unique position in the retained subject/body, and source/quote
hashes. Source text is not normalized or rewritten to make a quote match. The
server calculates character offsets from the submitted text; browser UTF-16
offsets are never trusted. Overlapping repeated occurrences are ambiguous too.

`ApplicationStatusInbox.record_outcome` reads the existing encrypted evidence and
appends a `RECORD_OUTCOME` entry to the existing encrypted review journal. It uses
the same commit-before-memory publication path as other decisions. A failed save
leaves the previous journal and in-memory decision unchanged. The private HTTP
service reuses its synchronization lock and requires the current review reference;
a stale tab or duplicated request cannot replace a newer review.

The displayed human outcome is derived from the final review entry and revalidated
against the current source and application association. There is no separate
mutable result cache. A later link/dismiss/clear written by an older build therefore
also retires a previous human outcome when this build next reads the journal.
Changed or invalid source, span, quote hash or association produces a fixed error
without echoing private content. Reimport and paged synchronization retain the
review history and existing evidence identity.

No new private-record kind, database table, provider, model or dependency is
required. UI protocol is **41**; restart the backend and refresh the page when
changing source versions. Browser Companion remains 0.9.2 / protocol 2, and the
existing desktop/extension compatibility limits are unchanged. Mail content and
quotes remain in the encrypted store and explicit local inbox read model; they
are not added to bootstrap, diagnostics, MCP metadata or ordinary logs.

## Verification and comparison

The baseline's 37 related mail/workspace tests passed. Twelve new test methods
were frozen at `ab715c7` before implementation; the absent API/module and unsupported
service action produced 37 subcase errors. Those errors establish an unavailable
feature, not 37 defects in historical research or 37 failed real applications.

At runtime `9e0ebd5`, all **128 related Python tests passed**, including the original
120-case bilingual interpretation corpus and 60 earlier EML fixtures, paging,
workbench, onboarding and compatibility checks. The twelve new methods include
eight explicitly supplied human outcomes, two per supported status across English
and Chinese. Additional quote-binding cases at `36d7ffd` also pass. No classifier
label, source fixture, protected-state gate or old observation was changed.

| Task or boundary | Before | Verified implementation scope |
| --- | --- | --- |
| Record an unusual message outcome | Only the evidence link could be corrected | Eight synthetic English/Chinese outcome examples retain exact source passages |
| Preserve an uncertain machine proposal | Remains available | Human interpretation appears separately; the original proposal is unchanged |
| Correct, dismiss or undo a human result | No human outcome field | New journal entries retain earlier decisions; current result can be cleared |
| Reject unsupported source, status or stale review | No outcome action | Invalid quotes, ambiguity, protected status values, changed source and stale requests stop without publishing |
| New model calls or real mailbox access | None in this comparison | None; this measures local workflow behavior, not an AI quality improvement |

The actual browser scenario uses local HTTP, Windows DPAPI and original synthetic
EMLs. It records English and Chinese outcomes, rejects an invented/ambiguous quote
and a stale request, uses keyboard controls, reloads saved evidence and undoes a
review. Application state remains `AWAITING_APPROVAL`; the service regression
separately preserves `SUBMISSION_UNKNOWN`. Original sender authentication remains
false. The demo file picker stays disabled and no real email is read.

The initial functional browser pass is retained. Its tall-element screenshots
composited the sticky header over card content, so follow-up captures use actual
desktop and mobile viewports, showing the result and controls separately. The
[verification receipt](../evaluations/2026-10-31/mail-outcome/verification.json)
records exact source/test revisions, outcomes, image/log hashes and validation
levels. Full regression and hosted status are recorded separately as they finish;
related tests and browser simulation do not substitute for them.

[Chinese desktop result](../evaluations/2026-10-31/mail-outcome/desktop-zh-result.png) ·
[desktop controls](../evaluations/2026-10-31/mail-outcome/desktop-zh-controls.png) ·
[English narrow-screen result](../evaluations/2026-10-31/mail-outcome/mobile-en-result.png) ·
[narrow-screen controls](../evaluations/2026-10-31/mail-outcome/mobile-en-controls.png).
The coding assistant inspected all four actual-viewport captures. The two existing
mail/workspace browser suites also pass, including the separate synthetic
application rehearsal; that rehearsal is not a mail-triggered application action.

An initial packaging command could not find the optional `build` module. The
existing pip/setuptools toolchain then built the unsigned wheel offline with no
dependency installation, and all seven packaged runtime files changed here match
the exercised local bytes. Both packaging outcomes and the wheel hash are in the
receipt. This does not provide a production signer or clean-machine acceptance.

## Run and recover

Use the existing development setup and prepared project environment:

```powershell
python -m unittest discover -s tests -p test_mail_outcome.py -v
python -m unittest discover -s tests -p 'test_mail*contract.py' -v
node tests/mail_outcome_ui_e2e.cjs
node tests/mail_review_ui_e2e.cjs
node tests/workbench_ui_e2e.cjs
```

The browser test creates and removes an isolated synthetic DPAPI/SQLite fixture;
artifacts go to a new timestamped directory under `reports/mail-outcome-ui/`.
It makes no provider call and requires no mailbox credential. The other two
commands cover existing mail association and workspace/application rehearsal.
Do not run an old closed live-model campaign to validate this feature.

No production deployment or schema migration is needed for this draft. Before a
local code rollback, stop JobFlow and back up the same-account encrypted store
and database together. A normal reviewed revert restores the earlier UI/reader;
older builds ignore the new human outcome but retain journal entries. Use the
matching backend/UI pair, and keep the dated evidence. Restoring an older backup
would discard later reviews and needs a separate recovery decision; do not delete
individual records or forge approval flags.

Authenticated provider integration, independent held-out interpretation, actual
user review effort and first-user acceptance remain unverified. Full-material
semantic accuracy, live ATS validation, trusted production signing and independent
clean-Windows installation/upgrade/data-retention/rollback remain separate open
requirements of the overall JobFlow goal.
