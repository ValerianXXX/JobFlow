# Quick Start

## Install once

1. Extract the complete JobFlow source ZIP.
2. Double-click `Install JobFlow.cmd`.
3. Wait for installation to finish. The installer opens the browser extension page and the Local AppData Browser Companion folder.
4. Enable **Developer mode**, choose **Load unpacked**, and select the opened Local AppData runtime folder. Do not select the project source folder.
5. Confirm the displayed JobFlow Browser Companion version matches the release notes.

The browser requires this one manual security gesture. JobFlow cannot bypass it.

## Start JobFlow

Double-click `Start JobFlow.cmd`. Keep the PowerShell window open while JobFlow is running. Closing it stops the local server and the browser page will show a connection-refused message.

For a fictional, auto-cleaned tour, run `Start JobFlow Demo.cmd` instead.

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
- Confirm it was loaded from the Local AppData runtime folder.
- Reload the JobFlow page after updating the extension.
- Use the JobFlow cancel action before selecting a different job URL.

### Windows and WSL

Run JobFlow itself from Windows with the `.cmd` files. WSL is used only when JobFlow detects an already configured WSL Agent or local model.

## Reporting a problem

Use synthetic or fully redacted data. Include the JobFlow version, the first health-check failure ID, expected behavior, and actual behavior. Never attach a real resume, database, DPAPI file, token, absolute user path, or private screenshot to a public issue.

See the [security policy](../SECURITY.md) for private vulnerability reports.
