# JobFlow Browser Companion Privacy Policy

Effective date: August 16, 2026

Last updated: August 28, 2026

JobFlow Browser Companion is a local-first browser extension for the JobFlow desktop workflow. It helps a user read a job page they explicitly choose and, after a separate application review and approval, assist with approved form fields and approved application files. Final submission always remains a user action.

## Data the extension processes

The extension may process the following data only for the active JobFlow task:

- the URL, origin, visible job text, and sanitized form structure of the page the user chooses;
- approved application-field values supplied by the user's local JobFlow installation;
- approved resume, cover-letter, or portfolio files supplied by the user's local JobFlow installation;
- local task identifiers, page hashes, authorization state, and non-sensitive progress information needed to prevent stale or repeated actions.

The extension does not read browser history, cookies, passwords, authentication tokens, one-time codes, CAPTCHA or MFA responses, government identifiers, payment data, or values already entered in unrelated forms. It does not answer protected voluntary-disclosure questions and does not click final Submit.

## How data is used

Data is used only to:

1. verify that the active page belongs to the company or application route selected by the user;
2. create a sanitized description of the current job or application page for the local JobFlow workflow;
3. fill values and attach files that the user has reviewed and approved for that specific application;
4. stop safely when a page, field, route, authorization, or result is unknown or has changed.

## Local communication and storage

The extension communicates only with:

- the JobFlow service running on the user's own device at `127.0.0.1` or `localhost`; and
- the JobFlow native messaging host installed for the current Windows account.

The native host returns only the installation binding needed to authenticate the local JobFlow service. It does not receive page content, applicant answers, or application files.

The extension stores short-lived task state in browser session storage. That state expires or is cleared when the task ends, is cancelled, or becomes invalid. Applicant data retained by JobFlow is encrypted by the local desktop application and is governed by the user's local JobFlow settings.

JobFlow does not operate a remote collection server for Browser Companion data. The extension does not sell data, use it for advertising, transfer it to data brokers, or use it for credit, lending, insurance, or unrelated profiling.

JobFlow Browser Companion's use of information received from Chrome APIs complies with the Chrome Web Store User Data Policy, including the Limited Use requirements. The extension uses that information only to provide its disclosed, user-facing JobFlow workflow.

## Permissions

- `activeTab` and optional HTTPS site access: inspect or assist only on the page the user chooses.
- `scripting`: run the bounded page inspector and approved-field helper on that page.
- `storage`: keep expiring, non-plaintext task state for the active browser session.
- `alarms`: observe explicitly authorized local task progress without hidden retries.
- `nativeMessaging`: authenticate the extension to the user's local JobFlow installation.
- optional `search`: open a user-approved company-careers search when the user starts job discovery.
- localhost access: communicate with the local JobFlow service only.

## User controls

Users can cancel an active read or application-assistance task in JobFlow, revoke site access in the browser, disable or uninstall the extension, and remove local JobFlow data through the desktop workflow. An unknown submission result is never retried automatically.

## Changes

Material changes to this policy will be published in this repository and reflected in the extension-store listing before the updated extension is released.

## Contact

Privacy and security questions can be filed through the public [JobFlow issue tracker](https://github.com/ValerianXXX/JobFlow/issues). Do not include resumes, contact details, credentials, or other private applicant data in an issue.
