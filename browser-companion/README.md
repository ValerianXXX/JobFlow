# JobFlow Browser Companion

This bundled Chrome/Edge Manifest V3 extension performs the user-present part of JobFlow's company and supported ATS workflow.

- It pairs only with the loopback JobFlow server, and only after the user opens the popup on the current JobFlow page and chooses the connect action. A loopback page cannot pair or rebind the extension silently.
- The fixed extension ID exposes only a read-only availability ping and binding-scoped status query to the loopback page, so Chrome's "When clicked" site-access setting remains supported.
- A bounded retry and tab-session recovery handle an extension or JobFlow page reload; pairing references stay in `sessionStorage` only and expire with the 30-minute authorization.
- It uses the user-click `activeTab` grant for the current page. It does not require permanent access to every website.
- A different JobFlow session or mode cannot replace an active browser task, and old asynchronous work cannot restore a stale binding after a reconnect.
- It reads form structure but never sends current field values, cookies, page bodies, passwords, or session tokens to JobFlow.
- It fills only bindings from the current approved packet and attaches only the approved encrypted materials, one page at a time.
- It may activate one unambiguous forward Next/Continue control only after page validation and a fresh, one-use local authorization.
- Login, account creation, CAPTCHA, MFA, legal/signature answers, and unknown fields always stop for the user.
- It has no final-submit implementation. The only programmatic click is scoped to an authorized non-final forward control.
- It observes a submit event only after the user physically clicks, then reports safe result signals. Unknown results require a manual answer and are never retried automatically.

Run `Install JobFlow Browser Companion.cmd` from the JobFlow project root. The installer creates a private runtime copy under the current Windows account's Local AppData and opens its `BrowserCompanion` folder. In `edge://extensions` or `chrome://extensions`, choose **Load unpacked** and select that opened runtime folder—not this source-template directory. Confirm version `0.6.5` and fixed extension ID `hhlliaaafegldkmcgmaoaelabipcaooj`. Job-specific AI and document preparation runs once in the local background; the companion polls its status with short requests, so its popup may be closed while the review packet is being built.

The installer also creates an installation-specific HMAC binding shared only by the local JobFlow service and that runtime extension copy. The secret is generated locally, never printed, and is excluded from Git and source ZIPs. Re-running the installer rotates the binding; afterward click **Reload** for the unpacked extension and refresh JobFlow once.
