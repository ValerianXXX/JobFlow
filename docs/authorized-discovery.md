# Authorized read-only discovery

JobFlow's optional background mode is intentionally narrower than its user-present application workflow.

## What an authorization permits

- Poll only exact HTTPS company-careers pages or exact supported public ATS sources that the user approved.
- A Greenhouse, Lever, Ashby, or SmartRecruiters board-root URL is deterministically mapped to that tenant's public read-only jobs endpoint. An individual job URL is never widened to the whole board.
- Match public job titles and locations against an encrypted local filter.
- Add public job candidates to a local discovery inbox, subject to a per-run limit and the user's pending-review capacity.

## What it never permits

- Opening an application form, clicking Apply, Next, Continue, or Submit.
- Logging in, creating an account, reading credentials, or handling CAPTCHA, MFA, OTP, or signatures.
- Filling fields, uploading files, sending messages, or creating an application approval automatically.
- Retrying an unknown external outcome.

## Lifecycle

Authorization is explicit, expires after at most seven days, and is bound to a generation. A run must claim a short lease and revalidate that generation before every persistent write. Pausing or using the kill switch increments the generation, invalidates an active run, and requires removal of the Windows user task. Three consecutive runs containing any source error cause a fail-closed automatic pause.

The encrypted configuration contains exact source bindings and search terms. Ordinary state stores only an opaque secure reference, hashes, counts, timestamps, fixed status codes, and append-only aggregate events. No private term or source URL is passed on a command line.

This contract does not claim that every live careers site or ATS feed is compatible. Static company pages and the four listed public ATS job-list formats are the declared surface. Each source still needs a current acceptance check, and every discovered role remains a review candidate until the user starts the existing user-present workflow.
