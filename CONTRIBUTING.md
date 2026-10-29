# Contributing to JobFlow

JobFlow accepts focused, testable changes that preserve its privacy and external-action boundaries.

## Before opening an issue

- Reproduce the problem with synthetic or fully redacted data.
- Run `Check JobFlow.cmd` and record the first non-sensitive failure ID.
- Never attach a real resume, Candidate Profile, Answer Bank, database, DPAPI file, token, absolute user path, or private screenshot.

## Development setup

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[build,mcp,document]"
.\.venv\Scripts\python.exe -m jobops.compatibility
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

Use the synthetic demo and repository fixtures for browser work. A test must never visit a real recruiting site unless the repository owner has granted a separate, explicit live-acceptance authorization.

## Pull requests

- Keep each pull request bounded and explain the user outcome.
- Add regression coverage before changing a safety gate.
- Preserve Chinese and English product behavior when user-facing text changes.
- Report privacy, AI, browser, network, and external-action impact.
- Run the relevant tests and `python -m jobops.public_release`.
- Confirm knowledge writes, plaintext leakage, staging residue, and unapproved real external actions are zero.

Final Submit, automatic retry, credential handling, account creation, outbound email, recruiter contact, and unattended application scheduling are outside the current product boundary. Explicit local EML evidence import does not connect to a mailbox or change application execution state.
