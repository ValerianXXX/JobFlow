# Browser Companion store submission

This file contains the canonical English listing copy and review notes for Chrome Web Store and Microsoft Edge Add-ons.

> **Unreleased submission draft:** version `0.9.2` is the source-tree candidate. This document does not claim that either browser store has published it.

## Product identity

- Name: `JobFlow Browser Companion`
- Category: `Productivity`
- Language: `English`
- Version: `0.9.2`
- Single purpose: connect the local JobFlow workflow to the job or application page explicitly chosen by the user, then perform bounded, reviewed assistance without final submission.

## Short description

Connect local JobFlow to a chosen careers page for reviewed job reading, approved prefill, file attachment, and user-only final Submit.

## Full description

JobFlow Browser Companion securely connects the local JobFlow desktop workflow to the company careers or application page you choose.

Before an application exists, it can read the visible text of one selected company job page and a sanitized description of the application form the user opens. After JobFlow prepares an application and the user reviews and approves it, Browser Companion can fill approved fields, attach approved resume or supporting files, and activate one clearly identified non-final Next or Continue control.

Safety boundaries are built into the workflow:

- Final Submit is always clicked by the user.
- Unknown or changed pages and fields stop safely.
- Login, account creation, passwords, CAPTCHA, MFA, OTP, signatures, and protected voluntary disclosures are never automated.
- Unknown submission results are never retried automatically.
- Page content and applicant data stay on the user's computer and are exchanged only with the local JobFlow service.

JobFlow desktop must be installed on the same Windows account. The installer registers a private native messaging host that authenticates the local connection; no applicant data passes through that host.

## Permission justifications

### Remote code

JobFlow Browser Companion does not use remote code. All executable JavaScript is packaged with the extension. The native messaging host only authenticates the local installation and never provides executable code.

### activeTab

Required to inspect or assist on only the tab the user deliberately selected. It avoids permanent access to every website.

### scripting

Required to run JobFlow's bounded DOM inspector and approved-field helper in the selected tab. The script classifies controls, discards existing values, fills only hash-bound approved values, and never clicks final Submit.

### storage

Required for expiring session state such as the current application identifier, permitted origin, page hash, and progress state. Applicant field values and files are not persisted in extension storage.

### alarms

Required to observe an explicitly authorized local workflow across multi-page application steps and report progress. Alarms do not authorize hidden retry or unattended submission.

### nativeMessaging

Required to authenticate the store-installed extension to the JobFlow installation on the same Windows account. The native host returns only a random installation binding; it does not receive page content, applicant values, or files.

### localhost host access

Required to communicate with the user's local JobFlow service on `127.0.0.1` or `localhost`. No remote JobFlow collection server is used.

### optional HTTPS host access

Requested only for the company or ATS origin chosen for the current task. Users can keep site access set to `When clicked` and grant access per site.

### optional search

Used only when the user explicitly starts company-careers discovery. The permission is optional and is not needed for manual URL intake.

## Data-use disclosures

The extension handles the chosen page URL, visible website content, sanitized form structure, user-approved contact/application values, and approved application files. Processing occurs locally. Data is not sold, used for advertising, transferred to data brokers, or used for unrelated purposes. See the public privacy policy:

`https://valerianxxx.github.io/JobFlow/privacy.html`

## Reviewer instructions

1. Install JobFlow on a Windows test account using `Install JobFlow.cmd` from the public source package.
2. Confirm the installer reports that the Browser Companion native host is registered.
3. Install the submitted extension package.
4. Start JobFlow and open its local URL at `127.0.0.1`.
5. Use JobFlow's synthetic demo or a reviewer-controlled test careers page. Do not enter real applicant data.
6. Start a 30-minute guided read. The extension pairs only with the local JobFlow origin and only after its HMAC installation proof succeeds.
7. Observe that unsupported, changed, login, CAPTCHA, account, legal, signature, unknown, or final-submit controls stop safely.

No production account, password, API key, or remote JobFlow service is required. If the local native host is absent, the extension shows a repair instruction rather than accessing the page.

## Assets

- Extension icon: `browser-companion/icons/icon-128.png`
- Small promo tile: `docs/store-assets/small-promo-440x280.png`
- Screenshot 1: `docs/store-assets/screenshot-1-local-workflow-1280x800.png`
- Screenshot 2: `docs/store-assets/screenshot-2-approved-prefill-1280x800.png`
- Optional marquee: `docs/store-assets/marquee-1400x560.png`
- Store package: generated by `scripts/build_browser_companion_store_package.py`
