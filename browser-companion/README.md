# JobFlow Browser Companion

This bundled Chrome/Edge Manifest V3 extension performs the user-present part of JobFlow's company and supported ATS workflow.

- It pairs only with the loopback JobFlow server, and only after the user opens the popup on the current JobFlow page and chooses the connect action. A loopback page cannot pair or rebind the extension silently.
- The fixed extension ID exposes only a read-only availability ping and binding-scoped status query to the loopback page, so Chrome's "When clicked" site-access setting remains supported.
- A bounded retry and tab-session recovery handle an extension or JobFlow page reload; pairing references stay in `sessionStorage` only and expire with the 30-minute authorization.
- The first user-visible connection requests optional browser Search and HTTPS site access so the same visible tab can move from a company job page to its published ATS route without another extension click. Denial stops the workflow before any page read or write; JobFlow never grants this permission silently.
- A different JobFlow session or mode cannot replace an active browser task, and old asynchronous work cannot restore a stale binding after a reconnect.
- It reads form structure but never sends current field values, cookies, page bodies, passwords, or session tokens to JobFlow.
- It fills only bindings from the current approved packet and attaches only the approved encrypted materials, one page at a time.
- It supports native controls, grouped choices, validated LWC/custom selects, and input- or button-based ARIA comboboxes in browser-visible component roots. After a verified component redraw, it continues only when every remaining field and final-submit boundary can be uniquely rebound to the original structural signature.
- If a reviewed control disappears, changes kind, or becomes ambiguous, it stops with a redacted field-level diagnostic and never guesses, repeats, or broadens the approved write.
- It may activate one unambiguous `type=button` forward Next/Continue control only after page validation and a fresh, one-use local authorization. Submit-like forward controls always require the user's trusted click; after that click, JobFlow resumes automatically when the next page is stably loaded.
- Login, account creation, CAPTCHA, MFA, legal/signature answers, and unknown fields always stop for the user.
- It has no final-submit implementation. The only programmatic click is scoped to an authorized non-final forward control.
- It observes a submit event only after the user physically clicks, then reports safe result signals. Unknown results require a manual answer and are never retried automatically.

Run `Install JobFlow.cmd` from the JobFlow project root. The unified installer registers the private native messaging host used by the signed Chrome/Edge store extension. For local development only, the installer also creates a private runtime copy under the current Windows account's Local AppData; load that unpacked folder only when testing source changes. Confirm version `0.9.2`. JobFlow accepts the signed Chrome, Edge, and deterministic unpacked-development identities, then securely pairs the installed companion automatically; clicking J is only a recovery path when the browser blocks the automatic connection. Job discovery, page verification, AI material preparation, and post-approval page assistance continue in the local background; the popup may be closed while the home page shows persistent progress.

The installer also creates an installation-specific HMAC binding shared only by the local JobFlow service and that runtime extension copy. The secret is generated locally, never printed, and is excluded from Git and source ZIPs. Re-running the installer rotates the binding; afterward click **Reload** for the unpacked extension and refresh JobFlow once.
