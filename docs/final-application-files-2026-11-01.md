# Review the actual files before approving an application

The review packet previously showed proposed wording, source bindings and upload
filenames, but offered no way to inspect or download its final files. Test-only
artifact exports did not provide that user experience. The current local UI adds
PDF/image page previews and explicit downloads of the saved job-specific resume,
Cover Letter and supported bound portfolio files.

## Use the feature

Open an application from the pending queue, then use **Review final application
files** (or its Chinese equivalent) at the top of the packet. Preview the PDF, move between
pages, change zoom, or download the PDF and editable DOCX. Escape or **Close and
clear** removes the displayed page and returns focus. Closing the packet clears
its file list. Chinese/English switching, reload and mobile layout are supported.

The download button checks the received bytes against the displayed packet's
hash before handing the copy to the browser. Filenames include an application
suffix to distinguish copies from different applications. Downloaded copies are
outside JobFlow's encrypted storage; the UI explains this before downloading.
Viewing a file does not approve its facts, grant upload permission, start browser
assistance, or submit an application. Final Submit remains a user action.

No new configuration, dependency, model provider or database migration is needed.
Use the existing source installation and local onboarding center. Restart an older
running service after updating: UI protocol 44 and v52 assets must be used together.
Native document conversion remains part of preparing materials; this viewer reads
the already-saved PDF and does not run another model or Word/LibreOffice export.

## Access and limits

`application_material_review.py` resolves a small material selector from the exact
current encrypted packet. The browser supplies an application ID, expected packet
hash and selector, never a filesystem path or arbitrary secure reference. The
service checks packet content, application context, per-application material row,
reference kind/status and content hash. It rechecks the binding after file reads
and page rendering. An unavailable file does not prevent viewing another intact
file. Original masters, Profiles, Answer Banks and arbitrary attachments are not
exposed through this interface.

The three local POST routes are `application-materials`, `application-material-page`
and `application-material-file`. They use the existing loopback host, session and
origin checks, no-store responses and content-type protection. Downloads support
PDF, DOCX, PNG, JPEG and text files up to the existing 64 MiB private-file limit.
Inline PDF/image previews retain the existing 16 MiB, 60-page, 25-million-pixel
and 6 MiB PNG-output limits. Other formats are shown as unavailable. The original
file is not modified or silently converted to fit a preview limit.

Opening a stale packet, changing/revoking a material, corrupting its ciphertext,
or changing the packet during rendering stops that request. Late replies cannot
repopulate a closed preview. The browser holds preview images in memory and clears
their DOM references on close; this is not a claim of secure RAM or SSD erasure.
The application approval and external-action records are unchanged by these routes.

The packet also uses ordinary wording for field classifications and removes the
empty public-link summary. Internal classifier names remain in the underlying
structured evidence, rather than becoming user-facing instructions.

## Validation and reproduction

The new service contracts were frozen before implementation. Their ready baseline
failed because the routes/methods did not exist. Earlier test-fixture setup errors
(private-root placement and server call signature) are retained separately from
that baseline. The first UI run found a remaining raw classification in the prefill
summary; its failure is retained and the product wording was corrected.

The 98-test related Python command passed, covering the new read-only contracts,
existing material plans/execution bundles and onboarding tests. The focused browser
test uses actual loopback HTTP and two real PDF pages with a seeded synthetic
memory-store double. It verifies PDF/DOCX download bytes, paging, stale requests,
late-response disposal, close/reload, focus return and Chinese/English desktop/mobile
views at 200 percent zoom. It does not establish DPAPI or first-user acceptance.

Run the normal project dependencies and these checks from the checkout:

```powershell
python -m unittest discover -s tests -p test_application_material_review.py -v
node tests/material_review_ui_e2e.cjs
node scripts/run-javascript-e2e.cjs
```

The separate [continuous protocol](../evaluations/2026-11-01/material-review/task-card.json)
extends the previous empty-profile task with product preview and download actions.
It uses actual Windows DPAPI, native PDF tools and the same frozen replay/task inputs;
no Profile, Claim decision, adoption or application approval is seeded. Its runner
still pauses for an actual inspection of the first standard-resume PDF before
adoption. Follow the [native prerequisite and review-record instructions](adopted-master-handoff-2026-11-01.md)
and run `node evaluations/2026-11-01/material-review/verify-browser.cjs` with a new
`JOBFLOW_OBSERVATION_ID`. The reused fixture keeps output under
`reports/first-use-revision`; the initial runner incorrectly chose another reports
subdirectory, and failed before starting the UI. The fixture's path guard remains
unchanged. That failed attempt is not a product-journey failure or a passing result.

The [evidence receipt](../evaluations/2026-11-01/material-review/verification.json)
records the runtime commit, retained failures and artifact hashes. All 20 suites
discovered by the existing JavaScript runner passed. The continuous Windows run
also passed: from an empty profile it generated and adopted a standard resume,
edited and reconfirmed the profile, prepared an application, reloaded it, previewed
both final PDFs in the product and downloaded all four PDF/DOCX files. Their bytes
matched the packet. The application stayed `AWAITING_APPROVAL`, with zero application
approvals or external actions; temporary private state was removed. The final PDF
pages were inspected by the assistant, not an independent participant. The two
selected resume facts each occurred once in the final DOCX. Native QA ran without
overriding its results. The current full Python matrix remains pending at delivery;
older commits' results do not establish this runtime's full acceptance.

The unsigned source wheel was built offline using the existing build backend,
after the initial `python -m build` command found that optional frontend absent.
Each changed runtime file was checked byte-for-byte inside the wheel. This is not
a signed Windows installation package or clean-machine installation evidence.

No AI recall, persuasive writing, time/cost or business-outcome improvement is inferred
from displaying previously generated files. New live model calls are zero; total
task cost is unmeasured. Real ATS/mailbox access, the 36-material fact target,
independent users, trusted production signing and clean Windows installation,
upgrade and rollback remain separate acceptance requirements.

## Rollback

This feature only reads the existing encrypted artifacts. A normal reviewed code
revert removes the viewer/download controls without deleting materials, sources,
applications or approvals. Restore the matching UI/service protocol together and
preserve the database plus encrypted store. The parent first-use/adopted-master
repairs and their downgrade constraints remain applicable. No production release,
signing, deployment, migration or external submission is authorized by these tests.
