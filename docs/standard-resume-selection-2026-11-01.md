# Select and restore approved resume facts

Observed November 1, 2026. Source preview, not a signed desktop release.
Runtime: `8df8d155fe438937f8c856590a3fd7b016f4764a`; parent draft #41:
`04c630375ff61f299fc1752bc5a9c547b14efc38`. UI protocol **43** loads the
backend and v51 assets together. Desktop 0.7.0 and Companion 0.9.2/protocol 2
are unchanged.

## User outcome

A PDF-source applicant can now choose which already approved facts belong in a
new standard resume. Previously every approved fact entered the draft; the
service ignored a requested subset and reused an all-fact draft. Selecting fewer
facts never deletes them, changes their approval, rewrites their wording or makes
a generated resume independent evidence.

1. Complete the existing PDF-source profile and approve current facts for resume
   use. This feature does not waive those prerequisites.
2. In **Choose facts for this resume**, select relevant statements. All are
   initially selected; **Clear selection** disables generation until one is chosen.
3. Generate the preview, save the DOCX, and use the existing local converter to
   inspect every PDF page. Length is measured after conversion, not predicted
   from a Claim count. No automatic shortening or ranking is performed.
4. Changing the selection clears the visible draft, PDF and adoption confirmation.
   Generate again before adopting the new content.
5. After a refresh or restart, choose **Restore saved draft** to retrieve the last
   generated selection and exact document. Unsubmitted checkbox changes are not
   saved. Restore retains the document's original heading language; generating
   again uses the selected UI language.
6. Adoption still starts a profile revision and requires renewed material
   permissions and editing-position review. No result is marked
   `MATERIALS_VALIDATED`, uploaded or submitted by this feature.

## Domain and persistence

The pure `resume_selection` module validates a canonical, nonempty subset of
the current approved resume-use Claim set. Empty, duplicate, unknown, null and
malformed selections are rejected before any draft write or cache reuse. The
selection receipt binds IDs to that Claim set's content hash. The document
builder formats exact approved wording only; the onboarding service continues
to own encrypted files, source revalidation and state transitions.

Draft reuse also matches locale and layout revision. V2 keeps each ordinary
bullet paragraph together across pages. A paragraph larger than an entire page
still requires manual layout review. Saved preview wording, counts and selection
metadata must agree; an inconsistent receipt cannot be restored, downloaded or
adopted. Existing all-fact V1 drafts remain readable without rewriting their
stored receipt; explicit regeneration produces the current layout.

Every file/adoption/restore request retains current profile, evidence, approval
and document-hash checks. Original PDFs, unselected Claims and previous encrypted
drafts remain intact. Selection and adoption are separate user decisions.

## Frozen comparison and evidence

The [protocol](../evaluations/protocols/standard-resume-selection-v1.json),
formatting generator and first seven tests were committed at `d2131d6` before
product changes. Two further tests cover explicit restore and legacy receipt
compatibility. An additional paragraph assertion follows the observed split.

| Fixed formatting fixture | Previous all-fact output | Explicit selected output |
| --- | ---: | ---: |
| English, 48 approved synthetic statements | 6 pages, 48 exact statements | 2 pages, 12 exact statements |
| Chinese, 48 approved synthetic statements | 6 pages, 48 exact statements | 2 pages, 12 exact statements |
| Unselected facts deleted from approved registry | 0 | 0 |
| New model calls | 0 | 0 |

The same twelve indices were frozen for both languages; all 96 authored
statements remain in the source fixtures. This is deliberate human selection,
not higher AI recall or automatic relevance assessment. The separately inspected
four final pages contain no clipping or missing selected wording. The first
English selected draft split one statement across pages; that artifact and the
original results are retained. V2 corrected this pagination without changing text.

The first render attempt used an incorrect configuration variable and failed
with `DOCX_PDF_EXPORT_UNAVAILABLE`. Correcting the invocation to the already
documented `JOBFLOW_LIBREOFFICE_BINARY` enabled the baseline. No converter or
dependency assertion was disabled. Actual rendering reused the existing local
LibreOffice 26.8.2 and Python 3.11.3 environment. Times are single observations on
a shared workstation, not a performance or cost estimate.

The first seven contract methods produced **13 failed assertions and two errors**
on the parent behavior. The related post-change group passed **31 tests**. The
final nine selection/restore tests passed separately; they overlap that group
and must not be counted as 40 distinct tests. Browser automation uses real
loopback HTTP and Windows DPAPI, seeded synthetic approvals, English/Chinese,
390-pixel layout, keyboard selection, DOCX download, native LibreOffice preview,
selection invalidation, reload/restore and adoption. It is not AI extraction,
an empty-profile journey, an independent user trial or a live application.

A separate in-app browser session restored the one-fact draft, displayed the
actual local PDF and verified that changing the selection disabled adoption.
The completed-profile UI reverted to its stored English locale on reload;
Chinese was selected again for inspection. Persistence of that UI language
choice remains a separate existing usability limitation, not a passing check.

The [verification receipt](../evaluations/2026-11-01/resume-selection/verification.json)
records hashes, retained failures, artifacts and terminal/pending checks. Exact
delivery CI and complete local regression are reported in the draft PR rather
than inferred from these focused results.

The later [terminal supplement](../evaluations/2026-11-01/resume-selection/terminal-verification.json)
records **all 19 JavaScript/browser suites passing** on the unchanged runtime,
plus a staged public-boundary pass and rechecked source/artifact hashes. The
initial receipt remains unchanged so its pending observations are not erased.
The complete Python run with required extras and exact-delivery hosted CI
remain separate checks; neither is implied by this result.

## Earlier full-suite failure retained

The previous equivalent runtime `c50b5bf` finished 1,581 local tests with three
failures, two errors and four skips in 3,957.262 seconds. Its environment lacked
the declared MCP/anyio and Docling extras. Hosted Python 3.11 at draft #41 ran
1,582 tests and failed only two assertions still expecting the historical v49
asset marker after v50 was introduced. The current change keeps both assertions
and updates their expected cache marker to v51. Required extras are installed
in this worktree's own environment; actual parser/MCP and those two UI contracts
passed in the related group. Neither observation relabels the original full
runs as passing. Their logs and exact summaries remain preserved.

## Run and recover

Use the README development environment with `.[build,mcp,document]` for the full
suite. No new model, provider, dependency requirement or service is added.

```powershell
python -m unittest discover -s tests -p 'test_standard_resume*.py'
$env:JOBFLOW_PDF_EXPORT_PROVIDER = 'libreoffice'
$env:JOBFLOW_LIBREOFFICE_BINARY = '<existing approved absolute soffice.com path>'
python scripts/evaluate-standard-resume-selection.py --selection subset --output reports/new-selection-run
$env:JOBFLOW_TEST_LIBREOFFICE = '1'
node tests/standard_resume_ui_e2e.cjs
```

Use a new output directory for each intentional observation. Configuration
selects an existing local converter; the product does not install it or silently
send material to a cloud service. Word remains the default exporter.

There is no database schema migration. Before using new drafts, stop JobFlow and
back up its database and account-bound private store together. Prefer a forward
fix after saving a selected draft: older code does not understand subset cache
identity or the new restore action. A downgrade requires deliberate restoration
of a coherent pre-change backup under the same Windows account; later changes
would be lost. After adoption, also retain the original-source restrictions in
the [standard-resume recovery guide](standard-resume-2026-10-29.md#dependencies-and-recovery).
Never delete facts or rewrite approval records to force a downgrade.

The 36-material factual-recall target, independent applicants, real ATS/mailbox
validation, trusted production signing and independent clean-Windows acceptance
remain open. Public release, store update and production deployment still require
separate approval.
