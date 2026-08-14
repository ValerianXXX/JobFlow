# JobFlow Browser Companion

This bundled Chrome/Edge Manifest V3 extension performs the user-present part of JobFlow's company and supported ATS workflow.

- It pairs only with the loopback JobFlow server.
- It requests access only to the exact company origin chosen by the user.
- It reads form structure but never sends current field values, cookies, page bodies, passwords, or session tokens to JobFlow.
- It fills only bindings from the current approved packet and attaches only the approved encrypted materials, one page at a time.
- It may activate one unambiguous forward Next/Continue control only after page validation and a fresh, one-use local authorization.
- Login, account creation, CAPTCHA, MFA, legal/signature answers, and unknown fields always stop for the user.
- It has no final-submit implementation. The only programmatic click is scoped to an authorized non-final forward control.
- It observes a submit event only after the user physically clicks, then reports safe result signals. Unknown results require a manual answer and are never retried automatically.

Load this directory as an unpacked extension from `edge://extensions` or `chrome://extensions`. The fixed unpacked extension ID is `hhlliaaafegldkmcgmaoaelabipcaooj`.
