# Security Policy

## Report privately

Use [GitHub private vulnerability reporting](https://github.com/ValerianXXX/JobFlow/security/advisories/new) for security issues. Do not open a public issue containing applicant data, credentials, private paths, secure references, DPAPI files, browser session data, or recruiting-site screenshots.

Include only:

- the affected JobFlow version or commit;
- a synthetic reproduction;
- the expected and observed security boundary;
- whether any browser, network, file, or external action occurred.

## Data model

- Applicant data is encrypted with Windows DPAPI outside the repository.
- Ordinary project records contain opaque references, hashes, counts, and safe status only.
- Decrypted values may exist only in memory or restricted temporary storage outside OneDrive and must be cleaned on success and failure.
- Knowledge sources are read-only.
- Agent credentials, cookies, tokens, passwords, OTPs, and browser sessions are never imported.

## Browser boundary

Browser Companion access is user-present, time-limited, origin-bound, and application-specific. It may inspect sanitized page structure, fill approved values, attach approved materials, and activate one verified non-final control. It must stop for login, CAPTCHA, MFA, account creation, credentials, legal or signature fields, unsupported controls, and final Submit.

Unknown submission state is never retried automatically.

## Supported versions

Only the latest tagged alpha release and the current default branch receive security fixes.
