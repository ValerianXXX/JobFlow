# Quick Start

## Install once

1. Extract the complete JobFlow source ZIP.
2. Double-click `Install JobFlow.cmd`.
3. Wait for installation to finish. The installer registers JobFlow's private, user-scoped browser channel and opens the Browser Companion store listing.
4. Choose **Add to Chrome** or **Get** in Microsoft Edge. Confirm that the publisher and version match the release notes.
5. Pin the JobFlow J icon if you want it visible in the toolbar.

The browser requires this one store-install gesture. JobFlow cannot bypass it. Developers testing source changes may use `Install JobFlow Browser Companion.cmd` and the unpacked Local AppData runtime, but ordinary users should install the signed store version.

The installer creates a fixed, versioned application under the current Windows account and a separate persistent data directory. After installation, you may remove the extracted source folder and start JobFlow from the Windows Start menu.

## Start JobFlow

Double-click `Start JobFlow.cmd`. Keep the PowerShell window open while JobFlow is running. Closing it stops the local server and the browser page will show a connection-refused message.

For a fictional, auto-cleaned tour, run `Start JobFlow Demo.cmd` instead.

## Update, roll back, or uninstall

- **Update:** download and extract the newer release, then run its `Install JobFlow.cmd`. The new version is health-checked before it becomes current. Candidate data, queues, reports, and encrypted private files are preserved.
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

JobFlow does not register a Windows scheduled task or background service. A due time never opens a website, starts the browser companion, uploads a file, or submits an application. Every local run requires the user's explicit click.

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

Open **Advanced diagnostics** in JobFlow and select **Download diagnostics**. Review the downloaded JSON before attaching it to a support request. It contains versions, safe runtime states, aggregate counts, safety boundaries, and an optional redacted error code. It does not read or include resume text, applicant answers, Claim text, local file paths, credentials, tokens, or secure references. JobFlow does not upload the file for you.

### Windows and WSL

Run JobFlow itself from Windows with the `.cmd` files. WSL is used only when JobFlow detects an already configured WSL Agent or local model.

## Reporting a problem

Use synthetic or fully redacted data. Attach the locally generated diagnostic JSON when useful, and include expected behavior and actual behavior. Never attach a real resume, database, DPAPI file, token, absolute user path, or private screenshot to a public issue.

See the [security policy](../SECURITY.md) for private vulnerability reports.
