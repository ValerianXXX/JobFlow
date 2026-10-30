# Keyboard focus after an interrupted operation

Hosted run [40965803779](https://github.com/ValerianXXX/JobFlow/actions/runs/40965803779) at `1eb6bb2ce9754a62b9e0fec0c5f9e4772f7e76e1` passed the source candidate and Windows Python 3.11/3.13 jobs. Python 3.12 passed its Python regression suite but failed the onboarding browser assertion: after a pending save and an emergency stop settled, focus was `emergencyStop` rather than `saveAnswers`. This is a JavaScript focus-lifecycle defect exposed by one runner, not evidence that the Python regression failed.

## Cause and change

The busy controller records the initiating control, blocks ordinary controls with `inert`, and keeps defined interruption controls reachable. A nested stop briefly makes its own control inert. Depending on browser scheduling, focus can remain on that control when it becomes available again. The old restoration rule only handled focus on the document body or root, so it omitted this valid browser state.

After the last activity settles, the controller now also restores the initiating control when focus remains on an explicitly named interruption control. It still preserves a deliberate move to another accessible element, refuses to focus a removed/disabled/hidden target, waits for all nested activities and preserves existing inert/disabled state. No retry, authorization, emergency-stop request, model or backend behavior changed. The static asset query advances to `v45-busy-focus`; the UI data protocol remains unchanged.

## Verification and limits

A new 10-case browser regression reproduces the observed retained-focus condition for all five interruption controls and checks body focus, deliberate movement and three invalid target states. Before the production change, five tests passed and five failed. After it, the exact same frozen test file passed all ten, with SHA-256 `d4218426f5aaea28a40fbbd906ee48fe631c302523c8a12631ec81c49875b5f0`.

The first test-development run had an additional fixture error: setting the HTML `hidden` property did not hide a button whose author CSS supplies `display`. The fixture was corrected to `display:none` before freezing the comparison. That initial six-failure run is retained separately; the production patch did not change or remove the hidden-target assertion.

The 74 onboarding Python tests passed in 43.313 seconds. The compatibility check passed its offline source-consistency scope and continues to report that the stable desktop cannot use the current store companion. It does not declare public-release readiness. The public CLI environment audit found zero external actions; knowledge discovery remained `KNOWLEDGE_ROOT_NOT_FOUND` in this isolated checkout, which does not prevent the synthetic UI test or authorize accessing a private vault.

An actual in-app browser run used the current UI and a clearly labeled loopback-only fixture with no applicant data. Keyboard Enter started a 15-second synthetic save; the actual emergency-stop button started a one-second synthetic interruption. After both settled, the DOM showed focus on `saveAnswers` and neither control remained inert. This verifies the UI sequence with in-memory effects, not an actual emergency-stop backend, live application or independent human acceptance.

The full JavaScript suite and hosted follow-up status are recorded in [the machine-readable receipt](../evaluations/2026-10-29/busy-focus-verification.json). The prior failure is retained; passing this focused comparison does not establish full-material semantic accuracy, email or ATS compatibility, signed packaging or clean-Windows lifecycle acceptance.

## Run and rollback

Use the existing documented project and locked Node environments:

```powershell
node tests/onboarding_busy_focus_e2e.cjs
node scripts/run-javascript-e2e.cjs
./.venv/Scripts/python.exe -m unittest discover -s tests -p test_onboarding_center.py -v
./.venv/Scripts/python.exe -m jobops.compatibility
```

No data migration or new configuration is required. Rollback is the prior reviewed UI code and matching static asset tag. Existing data, approvals, provider configuration and releases are unaffected. Production signing, clean Windows/VM acceptance and release approval remain unavailable or pending as previously recorded.
