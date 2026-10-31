# Current-message evidence and recoverable mail import

Verified October 31, 2026, with original synthetic messages. This is a source
milestone, not a signed Windows release or an authenticated mailbox integration.
Final application submission and protected execution states are unchanged.

## What changed

Previously, an old invitation inside a quoted reply or a phrase such as “not an
interview invitation” could produce a definite status proposal. `mail_interpretation`
now separates current text from recognized quoted/forwarded history and excludes
recognized negated, conditional and example statements. Conflicting current
outcomes remain unknown. Known application references take priority over shared
role titles, while competing references remain ambiguous. A sender must still
be explicitly authenticated by an authorized adapter and match an allowed domain
before any candidate association is proposed. Even a confident proposal requires
human review; it never writes application execution state.

Explicit local EML import remains unauthenticated. The parser recognizes HTML
`blockquote`, `gmail_quote` and `type=cite` regions during collection without
altering the retained body, raw EML or legacy content identity. Status citations
use offsets into the original normalized subject/body. This does not recognize
every email client's HTML history convention. Attachments are not analyzed.
The pure `propose_status` helper accepts optional ignored body ranges; use the
EML adapter through `collect` to apply the HTML ranges automatically.

Manual association, acceptance, dismissal and undo now publish their in-memory
decision only after encrypted journal persistence succeeds. A failed write returns
`MAIL_REVIEW_SAVE_FAILED`, keeps the old decision and omits private storage errors.
Existing journal records and decisions are preserved. Reimport does not silently
reinterpret old proposals: records created before interpretation version 2 retain
their original proposal and need human review. There is no bulk reanalysis UI.

## Measured comparison

The frozen [corpus](../tests/mail_review_corpus.py) adds 120 original cases: 12
families of ten, with 60 English and 60 Chinese cases. Families cover receipts,
rejections, invitations, negation, quoted history, forwarded threads, multiple
roles at one company, shared roles across companies, unrelated/conditional text,
untrusted senders, conflicting outcomes and reference boundaries. The old 60 EML
fixtures remain unchanged and are measured separately; those are ten templates
repeated with format/header variations, not 60 independent business situations.

The new set was frozen before implementation, then used to develop the repair.
It is **not a blind holdout, production distribution or independent human study**.

| Fixed-set measure | Before | After |
| --- | ---: | ---: |
| Status proposals disagreeing with the label | 39 / 120 | 0 / 120 |
| Confident association offered when the label does not permit it | 30 / 120 | 0 / 120 |
| Expected confident proposals missed | 12 / 52 | 0 / 52 |
| Uniquely matchable cases retaining the expected candidate | 90 / 90 | 90 / 90 |
| Extra candidate IDs beyond the labeled set | 14 | 0 |
| Proposals explicitly marked uncertain | 50 / 120 | 68 / 120 |
| Messages still requiring human confirmation | 120 / 120 | 120 / 120 |
| Protected application-state writes / external actions | 0 / 0 | 0 / 0 |

The confident-association error measure includes wrongly treating a status as
definite; it does not mean 30 real applications were changed or 30 wrong employers
were contacted. Candidate recall was already 100% on this set. More cases now
remain uncertain, and actual user review time has not been measured. No reduction
in human effort or real-business completion is claimed.

Both runs preserve all old 60 EML labels with zero confident associations. The
120-case local median proposal time was approximately 0.036 ms before and 0.057 ms
after, under concurrent development load; this is not an isolated performance
benchmark. No model was called, so model-request cost for this comparison is zero;
hardware, electricity and maintenance cost were not measured.

[Before observation](../evaluations/2026-10-31/mail-review-before-v1.json) ·
[After observation](../evaluations/2026-10-31/mail-review-after-v1.json) ·
[Verification receipt](../evaluations/2026-10-31/mail-review-verification-v1.json).
Corpus SHA-256: `9ec7f6cf20b32691c61a3ff150a910f5b91222af2edbcbbf3cdfdae095812220`.

## Optional paging contract, not a connected mailbox

`ApplicationStatusInbox.collect_page` accepts one `ReadOnlyPagedStatusAdapter`
page. Only `SyntheticPagedStatusAdapter` is supplied. No OAuth, Gmail/Graph client,
background refresh, account discovery, mail sending or real mailbox access was
added, and no author's email is treated as permission to read that mailbox.

An adapter must identify its authorized account, folder/label and filtering policy
with a stable opaque `source_id`, attest sender checks separately, supply a stable
provider message identity, and enforce its own I/O deadline. A label or time filter
does not narrow the provider's OAuth grant. The adapter remains responsible for
validating provider continuation URLs against its approved service; the core does
not fetch URLs or interpret provider cursor contents.

Each call accepts 1–100 messages, with a 1,000-event inbox cap and at most 4,096
characters per cursor. Cursors are retained only in private encrypted
`application_mail_sync_checkpoint` records. Public results return opaque secure
references, counts and completion status, not cursors or account identifiers.
The old timestamp-based `read_messages` contract remains compatible but is not
presented as reliable provider synchronization.

Scoped event identity binds the provider message ID to the account/folder/filter
scope, independent of receipt ordering. Different accounts cannot share cursors.
Repeated pages keep prior decisions; changed content under the same provider
identity fails with `MAIL_MESSAGE_ID_CONFLICT` rather than overwriting evidence.
The input journal must retain the events and review history bound to a checkpoint.
Legacy unscoped messages are not guessed to belong to a newly connected account.

The evidence journal commits before the cursor checkpoint. If checkpoint saving
fails, messages remain retained and the error says so. Reload the **latest** review
journal and retry the old checkpoint; repeated identities deduplicate. Do not resume
from the checkpoint's historical `review_ref` after subsequent user edits. The
caller must serialize synchronization and review with the existing local service
lock. This library contract does not add cross-process concurrency control.

An incomplete page must advance its cursor. One call never loops to fetch all
pages, retries a provider request or expands scope. `MAIL_CURSOR_EXPIRED` requires
an explicitly scoped restart. A complete page can retain a continuation token for
the next user-initiated refresh, including messages with the same timestamp.

```python
from jobops.mail_tracking import ApplicationStatusInbox
from jobops.mail_sync import SyntheticPagedStatusAdapter

# private_store is the existing PrivateOnboarding instance. Use supplied
# synthetic StatusMessage objects; this example does not read an account.
inbox = ApplicationStatusInbox(private_store, prior_review_ref=latest_review_ref)
adapter = SyntheticPagedStatusAdapter(messages, source_id="synthetic-folder-v1")
result = inbox.collect_page(adapter, applications, checkpoint_ref=last_checkpoint_ref, limit=50)
# Persist/use result["review_ref"] and result["checkpoint_ref"] privately.
# The next refresh must start from the latest review journal, including user edits.
```

## Verification and reproduction

Use the project's prepared Python environment. Every evaluation refuses to
overwrite an observation; choose a fresh result filename.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_mail*contract.py' -v
.\.venv\Scripts\python.exe scripts/evaluate-mail-review.py --output reports/mail-review-new.json
.\.venv\Scripts\python.exe scripts/evaluate-mail-sync.py --output reports/mail-sync-native-new.json
node tests/mail_review_ui_e2e.cjs
```

The eight pre-existing visual/mail tests and eight workspace tests pass. The initial
frozen mail suite recorded 47 failed assertions across seven test methods, including
four persistence failures. Eleven paging contracts initially errored because the
interface did not exist. Integration probes then exposed five HTML-history cases,
one missing private-storage kind and one stale-journal check; all are retained in
the logs. The final 21 contract methods pass, including all 120 new cases, 60 old
EMLs, six HTML examples, repeated imports, storage faults and scope/cursor recovery.

[Native validation](../evaluations/2026-10-31/mail-sync-native-v1.json) uses the real
Windows DPAPI store and SQLite index for three synthetic messages, one per page,
with the same timestamp. It verifies failed review persistence, failed checkpoint
persistence, restart/retry, retained corrections and a known-plaintext probe; it
finishes in 17.144 seconds and removes its temporary state. This limited probe is
not a general security proof or an authenticated delivery test.

The browser scenario uses actual loopback HTTP/DPAPI and three fixture-imported
EMLs. It verifies status uncertainty, plain/HTML quoted history, required manual
confirmation, association/reload/undo, unchanged application state, and Chinese
desktop / English mobile layout. It does not operate the disabled demo file picker.
The first browser run failed because the new test expected a nonexistent locale
dropdown; the test now uses the existing EN control. The original failure is kept.
Screenshot capture also waits for the actual locale save to settle.

[Chinese desktop capture](screenshots/mail-review-desktop-zh-20261031.png) ·
[English mobile capture](screenshots/mail-review-mobile-en-20261031.png).
These were inspected by the coding assistant, not an independent participant.

The complete Python regression was started at runtime
`d3ee30c0ad31fa24c190660395f0c701352b5ff9`; it is **pending at this document's checkpoint**.
The subsequent full JavaScript run and exact-head hosted CI are recorded separately
in the receipt/PR as results become available. Earlier full-suite passes do not
substitute for this run. Signing, clean Windows and independent users remain open.

## Sources, boundaries and rollback

Official references checked October 31, 2026: Python's
[MIME body selection](https://docs.python.org/3.11/library/email.message.html#email.message.EmailMessage.get_body),
[Gmail synchronization](https://developers.google.com/workspace/gmail/api/guides/sync)
and [Graph per-folder delta state](https://learn.microsoft.com/en-us/graph/delta-query-messages).
Gmail history may expire and require full synchronization; Graph continuation
tokens are opaque and folder-scoped. These informed the interface, not a claim
of provider or account validation. No third-party code or dependency was added.

Remaining work includes provider-specific consent and sender proof, native-client
verification, a held-out/independently labeled set, additional mail layouts and
unrecognized language, explicit review of old proposals, measured user effort,
and any cross-process connector coordination. There is no automatic mailbox
reading or protected-state recovery from an email. Broader JobFlow material/ATS
acceptance and trusted signing/clean-Windows blockers are unchanged.

No database schema migration, production deployment or release is required for
this source branch. Before rollback, stop local work and preserve the same-account
encrypted store and SQLite index. Use an ordinary reviewed revert or a matching
pre-upgrade backup. Older code can still read the unchanged review-journal list,
but cannot continue new cursor checkpoints and may make the old lexical mistakes.
Do not rewrite reviews, erase evidence, reuse a cursor under another account, or
mark approval gates passed to make rollback appear successful.
