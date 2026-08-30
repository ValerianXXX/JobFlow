# Quick Start

## Install once

`Install JobFlow.cmd` uses Windows PowerShell 5.1 to bootstrap the exact complete runtime published on JobFlow's pinned stable GitHub release. It does not use a system Python, create a virtual environment, extract a source runtime, or execute the legacy source installer. The manifest must be schema v2, its signature must match JobFlow's pinned release key, and the complete-runtime archive must match the signed name, size, SHA-256, and runtime-closure identity. Otherwise it stops without activating a new version.

1. Extract the complete JobFlow source ZIP.
2. Double-click `Install JobFlow.cmd` while connected to the internet.
3. Wait for signed runtime activation and the installed health check to finish.
4. The installer then registers JobFlow's private, user-scoped browser channel and opens the official Browser Companion listing for the detected default Chrome or Edge browser.
5. Choose **Add to Chrome** or **Get** in Microsoft Edge. Confirm that the publisher and the published extension version exactly match the JobFlow release notes; a different version will stop safely and request an extension update.
6. Pin the JobFlow J icon if you want it visible in the toolbar.

The browser requires this one store-install gesture. JobFlow cannot bypass it. Developers testing source changes may use `Install JobFlow Browser Companion.cmd` and the unpacked Local AppData runtime, but ordinary users should install the signed store version.

The installer creates a fixed, versioned application under the current Windows account and a separate persistent data directory. After installation, you may remove the extracted source folder and start JobFlow from the Windows Start menu.

## Start JobFlow

Double-click `Start JobFlow.cmd`. Keep the PowerShell window open while JobFlow is running. Closing it stops the local server and the browser page will show a connection-refused message.

For a fictional, auto-cleaned tour, run `Start JobFlow Demo.cmd` instead.

## Update, roll back, or uninstall

- **Update:** use the installed **Update JobFlow** shortcut for the signed stable channel. Re-running `Install JobFlow.cmd` performs recovery and verifies the installed state; it is not a source-tree update mechanism. Only an exact signed schema-v2 complete runtime can become current. A missing, recovered, retry-required, or unknown state stops without an automatic retry; the existing healthy version and all candidate data remain unchanged.
- **Roll back:** choose **Roll Back JobFlow** from the Windows Start menu. JobFlow switches only to the previously health-checked version and keeps the same data.
- **Uninstall the application:** choose **Uninstall JobFlow** from the Start menu. By default this removes application code and browser-channel registration but preserves local user data for a later reinstall.
- **Remove all local user data:** open PowerShell and run the installed uninstall script with both explicit flags: `& "$env:LOCALAPPDATA\JobOps\bin\uninstall-installed-jobflow.ps1" -RemoveUserData -UserConfirmed`. This is irreversible. The browser extension must still be removed separately in Chrome or Edge.

## First setup

1. Connect an existing Agent or supported loopback model.
2. Import a resume and optional project or portfolio material.
3. Complete the Candidate Profile and Answer Bank review.
4. Review every proposed Claim and resolve conflicts.
5. Approve the exact resume positions and Claim wording that may be used in application materials.

Private values are stored with DPAPI and are never written into the public project tree.

## Process one job

1. Enter a job-search instruction or paste an official company job URL.
2. Authorize the visible, time-limited browser read.
3. Use the Browser Companion on the selected job page and application form when prompted.
4. Review the single application packet, missing answers, and planned materials.
5. Approve this application-specific user-present session.
6. Let JobFlow fill approved values, attach approved materials, and handle verified non-final navigation.
7. Review the completed page and personally click final Submit.

A local approval is not a submission. Final Submit is always user-only.

## Process saved local work

The dashboard can save a suggested processing interval and a time-limited local authorization window. This is a user-present convenience, not an unattended scheduler:

1. Choose the interval and authorization window, then select **Save processing cadence**.
2. Select **Process local queue now** whenever you want JobFlow to process already saved local work.
3. Use **Pause new job intake** as a kill switch, and **Resume new job intake** when ready.

This saved-work cadence does not register a Windows task or background service. Its due time never opens a website, starts the Browser Companion, uploads a file, or submits an application. Every application-processing run requires the user's explicit click.

## Optional read-only background discovery

This is separate from processing saved applications. When you explicitly enable it, JobFlow may register one current-user Windows task that wakes the local discovery runner every 15 minutes. The authorization lasts no longer than seven days and is limited to the exact HTTPS company-careers pages or supported public ATS sources you approve. Greenhouse, Lever, Ashby, and SmartRecruiters board-root URLs are mapped to their exact tenant-bound public jobs endpoint; individual job URLs are never widened to a whole board.

1. Add the exact public sources, job terms, and locations you want JobFlow to match.
2. Choose an authorization duration and confirm **Enable read-only discovery**.
3. Review new matches in the local candidate inbox.
4. Choose **Process this candidate** only when you want to begin the ordinary user-present workflow.
5. Use **Pause** or **Emergency stop** at any time. Three consecutive runs containing any source error also pause discovery automatically.

The task may read public job listings and add candidate metadata to the local inbox. It cannot open Apply, inspect an application form, fill or upload anything, navigate an ATS, create an application, send a message, or submit. Uninstall removes the task without deleting its encrypted configuration or candidate history. Rollback preserves the task and authorization under the maintenance lock; its next wake resolves the restored version before doing any work.

## Expected handoffs

JobFlow pauses for login, existing-account verification, CAPTCHA, MFA, legal declarations, signatures, sensitive questions, unsupported controls, and unknown answers. Complete the required step yourself and resume only when the UI says it is safe.

If JobFlow cannot determine whether a write or submission succeeded, it records an unknown result and never retries automatically.

## Troubleshooting

### Startup or `Failed to fetch`

1. Close the stale JobFlow tab.
2. Double-click `Check JobFlow.cmd`.
3. Follow the first failed check.
4. Start JobFlow again and use the newly opened page. Old session URLs intentionally stop working after restart.

The health check is offline and does not print private values or the project path.

### Browser Companion does not respond

- Confirm the extension is enabled.
- Confirm the JobFlow installer completed after the store extension was installed; this registers the private local channel for the current Windows account.
- Confirm the extension version matches the current release.
- Reload the JobFlow page after updating the extension.
- Use the JobFlow cancel action before selecting a different job URL.

### Create a safe support report

Open **Privacy-safe incident history and support** in JobFlow and select **Download diagnostics**. Review the downloaded JSON before attaching it to a support request. It contains versions, safe runtime states, aggregate counts, safety boundaries, and validated fixed error codes. It does not read or include resume text, applicant answers, Claim text, error messages, stack traces, URLs, local file paths, credentials, tokens, or secure references. Optional local incident capture is off by default, retains at most 32 fixed-code records when explicitly enabled, and can be cleared at any time. JobFlow never uploads the file or incident history for you.

### Windows and WSL

Run JobFlow itself from Windows with the `.cmd` files. WSL is used only when JobFlow detects an already configured WSL Agent or local model.

## Reporting a problem

Use synthetic or fully redacted data. Attach the locally generated diagnostic JSON when useful, and include expected behavior and actual behavior. Never attach a real resume, database, DPAPI file, token, absolute user path, or private screenshot to a public issue.

See the [security policy](../SECURITY.md) for private vulnerability reports.
