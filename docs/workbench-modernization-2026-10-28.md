# JobFlow application workspace and evidence review preview

Verified **2026-10-28**. This source preview builds on release-repair PR #5 and material-understanding PR #6. It is not a published Windows installer. Verification used synthetic documents, fictional messages and isolated databases; no real mailbox or resume was accessed.

## Delivered behavior

- **Job details:** open an application from the pending or recent list to see retained job descriptions, integrity checks, material hashes and versions, review versions, workflow history and the next action. New snapshots preserve their canonical UTF-8/LF bytes. Historical Windows CRLF snapshots pass only when line-ending normalization reproduces the exact registered hash; the viewer labels that verification basis and never rewrites the original. Missing or altered content remains unavailable. Prepared materials do not prove employer receipt, and `SUBMISSION_UNKNOWN` never enables automatic retry.
- **Original-page comparison:** view a retained encrypted PDF/PNG/JPEG beside candidate Claims, with pagination and proposed source regions. Decryption and rendering stay in memory for the authenticated local page. Coordinates are independently validated on each response; legacy provenance containing HTML is displayed as text. Region boxes do not establish factual accuracy or individual responsibility. Existing Claim approval and original-source confirmation remain required.
- **Status inbox:** explicitly choose one EML file, retaining the encrypted original, normalized evidence and review history. Proposals distinguish receipt, rejection, interview invitation and uncertainty. Select an application and confirm a manual association, dismiss it, or undo the review. Reload restores committed records; stale review submissions are rejected. Partial import failures remove only newly created evidence, preserving prior or deduplicated records. Reading also rechecks the evidence hash against the review journal. There is no mailbox OAuth, automatic mail reading, outbound email or application execution-state mutation.
- **Read-only Hermes entry point:** three stdio MCP tools expose aggregate readiness, pending application IDs and one application's state. They use read-only SQLite connections and never initialize the private-data service. Results exclude resumes, names, companies, messages, paths and secure references. There are no approval, fill, upload or submit tools.
- **Optional local parsing:** Docling Native PDF Pipeline supplies text and page coordinates while the existing extractor remains the default. Explicit selection fails closed without cloud fallback. This is neither OCR nor the full Docling layout-model pipeline.

This iteration adds no browser-agent framework, changes no production model, and does not reinstall OpenClaw. It also fixes an existing narrow-screen refresh-control overflow, localized inbox feedback after language switching, and an obsolete CLI path in the operating Skill.

## Source-code references and decisions

Official source was inspected on the verification date. Relevant adoption/reuse licenses were checked, and source revisions are pinned below. **No upstream product source code was copied.**

| Source | Inspected implementation | Decision for JobFlow |
|---|---|---|
| [Reactive Resume, MIT](https://github.com/reactive-resume/reactive-resume/tree/d644b9963290ae71cd8cbc50f36063ef2036fcc4) | `packages/api/src/features/resume/service.ts` and `version-history.ts`: conflict checks and independent version history | Reuse existing material/review versions; reject stale mail decisions using the latest review reference, without replacing the editor or storage model |
| [Docling 2.144.0, MIT](https://github.com/docling-project/docling/tree/a857fd208ccd61fb4a5d230384554ce778f216df) | `docling/document_converter.py`, Native PDF options and provenance | Keep an isolated optional adapter, independent page-coverage checks and source locations; no measured text-quality gain justifies changing the default |
| [Stagehand, MIT](https://github.com/browserbase/stagehand/tree/737480022acbf5ec3450885f120fc33ee561a9b4) | `packages/extension/services/observeService.ts`: observation, candidates, real-element resolution and unresolved-result rejection | Preserve JobFlow's existing observation/execution separation, page rebinding, scoped authorization and user-only final Submit; regress these rather than add a duplicate executor |
| [Hermes source](https://github.com/NousResearch/hermes-agent/tree/bf0203a794a07aac3ff764972ab9087baa4e4f4e) | The researched v0.22.5 MCP security implementation and [official MCP documentation](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp) | Use an explicit stdio command and only three read-only tools; test the installed v0.16.0 instead of treating newer documentation as proof of local capability |
| [OpenResume](https://github.com/xitanggg/open-resume/blob/main/LICENSE) | Current license is AGPL-3.0 | Do not treat it as MIT or copy its implementation into this project |

MCP uses the actual negotiation and structured-result implementation in [official Python SDK 2.3.0](https://pypi.org/project/mcp/2.3.0/), with both the [2026-07-28 specification](https://modelcontextprotocol.io/specification/2026-07-28) and legacy-client paths exercised. The [Docling options reference](https://docling-project.github.io/docling/reference/pipeline_options/) documents Native PDF's lack of OCR, layout and table models; it must not be advertised as a scanned-document understanding upgrade.

## Reproducible comparison

Per-case results are in [evaluation-workbench-2026-10-28.json](evaluation-workbench-2026-10-28.json). Original synthetic inputs come from `tests/workbench_fixtures.py`; `scripts/evaluate-workbench.py` collects results. No customer or applicant samples were used.

The materials contain English synthetic text and do not establish Chinese-resume parsing quality: 10 single-column text PDFs, 10 two-column text PDFs with interleaved content streams, five scanned PDFs and five portfolio images. Visual analysis was disabled in the existing-text condition. Both conditions used the same document-quality preflight. This comparison stops at text extraction; downstream model entity reconstruction and Claim generation were not run.

| Measure | Existing text path | Docling Native candidate |
|---|---:|---:|
| Materials | 30 | 30 |
| Explicitly blocked extraction | 10 | 10 |
| Specified numbers absent | 20 | 20 |
| Whole fact strings absent | 30 | 30 |
| Source regions returned | 0 | 120 |
| Median across all cases, one local measurement | 18 ms | 2,963 ms |
| Model calls / model cost | 0 / $0 | 0 / $0 |

All missing numbers and fact strings came from the blocked scans/images. Both paths preserved the specified strings and numbers in the 20 text PDFs. **No reduction in omission or numeric-error rates was demonstrated.** String presence does not establish entity attribution, column reading order, team/individual ownership or generated-Claim correctness; those semantic measures were not evaluated. Region-location accuracy was not compared with human annotations. Timings were collected while other local regression work was active and are not an isolated performance benchmark. Costs count model calls only, excluding hardware, electricity and maintenance. Earlier small real-model visual trials remain in the [original record](visual-materials-and-status-tracking.md), not as directly comparable observations from this experiment.

The 60 EML cases vary 10 Chinese/English templates across plain text, HTML, forged authentication headers, conflicting statuses and unrelated content. They are not 60 independent business scenarios or a representative production distribution.

| Mail measure | Result |
|---|---|
| Proposed status disagreed with the template label | 0 / 60 |
| Incorrect automatic association | 0 / 60 |
| Human review required | 60 / 60 |
| Authenticated-mailbox matching recall | Not measured; no mailbox connector |
| Actual employer status recovery or confirmation | Not measured; file evidence and manual association only |

Zero incorrect automatic associations follows from treating every local EML as unauthenticated, not from proven automatic-matching accuracy. Sender, `Authentication-Results` and Date headers can be forged; the UI labels the date as an unverified file-header timestamp. Negation, quoted/forwarded content, threads and broader language coverage need additional labeled cases. Users must review proposed statuses.

## Verification levels

| Level | Evidence and limits |
|---|---|
| Synthetic/unit | Snapshot hashes and rejected paths; legacy CRLF compatibility; no event-payload exposure; EML limits and inactive HTML; duplicate import; association/undo; stale review; evidence replacement and partial-write rollback; source-page limits and hash checks; invalid-region rejection; session/Origin protection; actual native-parser subprocess and no cloud fallback on failure |
| SDK contract and real transport | Official SDK 2.3.0 calls all three tools through real stdio child processes; auto negotiates 2026-07-28 and legacy negotiates 2025-11-25. No submit tool exists; SQLite writes are rejected |
| Installed Hermes client | Hermes v0.16.0 (reported upstream `afe53708`), Python 3.11.15, MCP SDK 1.26.0. Its real MCP connector negotiated 2025-11-25 and successfully called all three tools, using temporary HERMES_HOME and synthetic state. Zero model calls and no user-config changes |
| Actual local UI | Windows DPAPI, real loopback HTTP and Edge; both UI languages; 1365x900 desktop and 390x844 narrow screen; source open/close; verified/missing snapshots; confirmation required; association/reload/undo/clear; demo import disabled; untrusted-region HTML inert; zero external requests |
| Still unverified | Model-selected tools inside a Hermes conversation; authenticated mailbox access and employer outcomes; broad semantic/location evaluation; signed installation and clean-Windows installation, upgrade and rollback |

The browser suite is `tests/workbench_ui_e2e.cjs`. Its synthetic candidate deliberately lacks an AI completeness proof, so material-confirmation controls stay disabled. Screenshots are not evidence of successful model analysis.

Local verification receipt (2026-10-28):

- One full Windows/Python 3.11.3 discovery run during development completed 1,169 tests in 3,628 seconds: 1,163 passed, two documentation checks failed, and four existing symbolic-link checks were skipped because this Windows user cannot create those links. The failures were this preview document's original language (introduced here) and a missing explicit `author identity` phrase in the inherited release checklist. Both documents were corrected; all five public-documentation and 33 release-readiness checks then passed without changing the tests or authorization policy.
- The final focused suites contain 13 new tests: eight workspace/mail/source tests, two MCP tests and three native-parser tests. These passed, including three added after full discovery started. The source-region hardening was checked separately in the workspace tests and real-browser suite. Existing workflow-gate and visual/mail checks also passed.
- All nine JavaScript suites passed during this iteration; affected UI suites were rerun after related changes. Dependency consistency, compatibility metadata, CLI startup, wheel-content inspection and public tree/history content scans passed. Content scans do not approve historical author identities.
- The development run is not a frozen final-commit full-suite attestation. Subsequent GitHub [run 40710106182](https://github.com/ValerianXXX/JobFlow/actions/runs/40710106182), bound to source commit `5d1ef2c2517736fb143c442703141e3cc1fefff4`, completed all **1,172 tests with zero failures, errors or skips** and all **nine browser suites** on Python 3.11, 3.12 and 3.13. The four checks unavailable on this development host passed on each hosted Windows runner. Dependency checks, compatibility, public content boundaries, wheel builds and CLI startup also passed.
- The 3.11 and 3.13 jobs succeeded. The 3.12 job additionally built the extension store package, then stopped its source-candidate build with `PUBLIC_REPOSITORY_IDENTITY_REVIEW_REQUIRED`. The **overall workflow failed at that authorization gate**; it is not a failed regression suite or signed-installation acceptance. No gate was disabled or identity approval invented. This paragraph and the [machine-readable receipt](../evaluations/2026-10-28/workbench-ci-matrix.json) document the tested source commit; later documentation-only commits do not retroactively change its result.

![Synthetic original-page comparison in the Chinese UI](screenshots/workbench-source-synthetic.png)

![Synthetic application records in the Chinese UI](screenshots/workbench-application-synthetic.png)

![English narrow-screen status inbox](screenshots/workbench-inbox-mobile-synthetic.png)

## Run and configure

Install `.[build,mcp,document]` for all development checks; normal source use can still install `.[build]`. Start `python -m jobops.cli onboarding-center`, open **Job details** on an application, or **Application records and updates > Open status inbox**. The demo remains `python -m jobops.cli demo` and rejects user-file imports.

Optional native text parsing:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[document]"
$env:JOBFLOW_DOCUMENT_PARSER = "docling_native"
.\.venv\Scripts\python.exe -m jobops.cli onboarding-center
```

Limits: 16 MiB, six PDF pages with text layers, and a 60-second worker timeout. Remote services, external plugins and picture models are disabled. Unset that process environment variable to restore the default extractor; `.env.example` is not loaded automatically. The parser itself makes no model calls, but subsequent Claim analysis still uses the configured AI route and may use a cloud provider. This is not a claim that the whole workflow runs locally.

Original-page display supports PDFs up to 16 MiB/60 pages or single-frame PNG/JPEG, with additional pixel and response-size limits. Oversized sources require original-file review instead of inline preview; factual confirmation is not bypassed. EML limits are 2 MiB, 50 MIME parts and 50,000 characters; the inbox holds at most 1,000 messages. It does not load remote images, attachments, scripts or links.

Read-only MCP:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[mcp]"
.\.venv\Scripts\python.exe -m jobops.mcp_server --project 'C:\path\to\JobFlow'
```

The process waits for MCP messages on stdin/stdout and has no HTTP address. Initialize the project in the local UI first. Users may add this example to their own Hermes configuration; this work did not modify that configuration:

```yaml
mcp_servers:
  jobflow:
    command: 'C:\path\to\JobFlow\.venv\Scripts\python.exe'
    args: ['-m', 'jobops.mcp_server', '--project', 'C:\path\to\JobFlow']
    tools:
      include: [get_readiness, list_pending_reviews, get_application_summary]
```

For WSL Hermes, use the corresponding `/mnt/c/.../python.exe` command while retaining the Windows `--project` path passed to Windows Python. MCP needs no model key. Its redacted workflow metadata enters the calling Agent's context; that Agent's configuration determines whether it is then sent to a model provider.

Reproduce collection and focused checks:

```powershell
.\.venv\Scripts\python.exe scripts/evaluate-workbench.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_workbench.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_jobflow_mcp.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_local_document_parser.py
node tests/workbench_ui_e2e.cjs
```

Reports go to ignored `reports/`. Full checks remain `python -m unittest discover -s tests -v` and `npm run test:e2e`. `scripts/probe-hermes-mcp.py` is a separate developer probe: run it with an installed Hermes interpreter, point `--hermes-root` to its source, and supply a synthetic JobFlow stdio configuration (`command`, `args`, `env`) on stdin. It creates disposable Hermes configuration and requires exactly one synthetic `SUBMISSION_UNKNOWN` application. It does not connect to an existing account. The probe uses Hermes internal client interfaces, not a runtime dependency or a promise of compatibility with every Hermes release.

## Publication, data and rollback

There is no database migration, production deployment, formal Release or store update. Mail originals use existing private-reference storage and DPAPI; reviews append encrypted versions. Application transitions, approval states and historical facts are not rewritten. Capacity limits never automatically delete older evidence.

For source rollback, close JobFlow, preserve the encrypted data directory and database backup, then return to the previous reviewed source version. Never copy real data into Git. An older version may not display the new inbox/coordinates; retain that data for a later upgrade. Unset `JOBFLOW_DOCUMENT_PARSER` to disable the candidate independently, or remove the MCP entry you added and restart the client. No Agent reinstall or production migration rollback is required.

Source 0.7.0 targets Companion 0.9.2; stable desktop 0.6.0 remains incompatible with the store extension. The owner approved one specific historical identity exception on October 28, and the policy was applied in `7b2fbf7d2249829610b4e7a5e4f2c68c8263f3ef`. Its full tree/history identity scan and reproducible source-candidate build passed. The earlier failed CI receipt above remains historical evidence for its named commit. Trusted production signing and clean-Windows acceptance are still incomplete; the exception does not authorize a release. See the [compatibility matrix](compatibility.md) and [release-repair record](modernization-2026-10-27.md).
