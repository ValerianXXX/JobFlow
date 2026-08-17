# JobFlow

JobFlow is a local-first, AI-centered job application workflow for Windows. It turns a resume, approved applicant facts, and an official job page into a reviewable application packet, then provides user-present browser assistance that stops before final submission.

> **Alpha software:** JobFlow does not claim universal ATS compatibility. Final Submit is always a trusted user action. Unknown external state is never retried automatically.

![JobFlow English synthetic demo](docs/screenshots/jobflow-demo-en.png)

## What JobFlow does

1. Imports resumes and supporting material into Windows DPAPI encrypted storage.
2. Connects to an existing Hermes, OpenClaw, or supported loopback model without copying model credentials into the project.
3. Uses AI to reconstruct experience entities, preserve metrics, and produce source-grounded Claim candidates.
4. Collects one reusable Candidate Profile and Answer Bank, with explicit review for sensitive or missing facts.
5. Starts from an official company career page, verifies the job and approved ATS route, and builds one application review packet.
6. After one application-specific approval, fills approved values, attaches approved materials, and advances only through verified non-final controls while the user is present.
7. Stops at final review so the user can inspect the page and personally click Submit.

## Quick start on Windows

1. Download and extract the latest source ZIP.
2. Double-click `Install JobFlow.cmd` once.
3. Install **JobFlow Browser Companion** from Chrome Web Store or Microsoft Edge Add-ons when the installer opens its listing.
4. Double-click `Start JobFlow.cmd` for normal use.
5. If startup fails, run `Check JobFlow.cmd` and follow the first failed check.

To explore without private data, run `Start JobFlow Demo.cmd`. The demo uses fictional content, disables real AI and file intake, and removes its temporary state when closed.

See the [full quick start](docs/quickstart.md) for Browser Companion setup and recovery guidance.

## AI connections

JobFlow can reuse an already configured:

- Hermes or OpenClaw Agent on Windows or WSL;
- Ollama, LM Studio, LocalAI, llama.cpp, or vLLM loopback server;
- advanced command adapter through `JOBOPS_AI_COMMAND_JSON`.

An AI connection is accepted only after a structured capability test. Agent requests use an isolated, zero-tool channel. JobFlow does not extract, return, log, or persist API keys, tokens, cookies, executable paths, or model credentials.

## Safety boundaries

- Private applicant values remain behind `secure-ref:*` references and DPAPI encryption.
- Claims require source evidence and applicant approval before external use.
- Login, CAPTCHA, MFA, account creation, credentials, legal declarations, signatures, and unknown answers stop for the user.
- Final Submit, automatic retry, email, recruiter contact, and unattended scheduling are not implemented.
- `SUBMISSION_UNKNOWN` never triggers an automatic retry.
- Knowledge sources are read-only.

## Current support

| Capability | Alpha status |
|---|---|
| Windows install, health check, and bilingual local UI | Available |
| Encrypted onboarding, Candidate Profile, Answer Bank, and Claim review | Available |
| Existing Agent or loopback-model connection | Available with capability gate |
| Visible official-company job discovery and verified Apply routing | Available with user authorization |
| User-present company, Greenhouse, Lever, and Workday assistance | Bound routes only; live compatibility varies |
| Approved field fill and material attachment | Available per approved application |
| Final submission or unattended operation | Not implemented |

Synthetic and saved-page tests are engineering evidence, not proof that every live ATS page is compatible.

## Development

```powershell
.\scripts\install-jobflow.ps1
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe -m jobops.public_release
```

The project is intentionally Windows-first because secure applicant storage uses DPAPI. Runtime state, databases, review packets, encrypted private files, and local release reports are excluded from the public repository.

## Documentation

- [Quick start](docs/quickstart.md)
- [Support](https://valerianxxx.github.io/JobFlow/support.html)
- [Browser Companion privacy](PRIVACY.md)
- [Architecture](docs/architecture.md)
- [Roadmap](docs/roadmap.md)
- [Release checklist](docs/release-checklist.md)
- [Security policy](SECURITY.md)
- [Contributing](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md)

## License

[MIT](LICENSE)
