# Onboarding request shutdown — 2026-10-31

An accepted local HTTP request could still write after the onboarding server
reported closure. Temporary fixture cleanup could consequently overlap a writer;
normal application shutdown also lacked an HTTP request completion barrier.
This change drains owned HTTP requests before successful closure. It does not
change facts, approvals, final submission, model selection or trusted signers.

## Evidence and scope

Hosted [run 41203196343](https://github.com/ValerianXXX/JobFlow/actions/runs/41203196343)
at PR #30 / `7b6b32b741dcb69cd4638a245aff7d27d705e1a2` passed its source candidate
and Windows 3.11/3.12 jobs. The Windows 3.13 job passed the standard-resume browser
assertions, then its fixture exited 1 with Windows error 145 (directory not empty)
inside `TemporaryDirectory.cleanup`. The fixture traceback identifies Python
3.11.3 even in that matrix job. This remains a failed historical job. Neither
cleanup nor any other acceptance check was disabled or retried to label it green.

Source inspection found daemon request threads, no joined request barrier and
continued keep-alive admission. Seven real-loopback contracts were frozen at
`abfb1a9` before editing: one passed and six failed. They reproduce writers that
outlive close, a second buffered request admitted after shutdown, idle/partial
reads that survive close, skipped draining after a service-close error and
repeated service closure. This is a confirmed defect consistent with the hosted
failure; it does not prove that every possible cause of that directory error has
been eliminated.

The first repair passed five contracts but could not wake two blocked reads on
Windows. A separate owned-socket probe confirmed that `shutdown` alone left the
read blocked. A second probe with a Python `makefile` reader confirmed that
detaching and closing that exact owned descriptor wakes the read. The original
seven contracts then passed unchanged.

Three additional fault contracts, frozen at `d317a94`, initially produced one
pass, one failure and one error: a disconnect error skipped draining, and a
failed thread start poisoned the standard-library join list. A fourth contract
at `c792977` exposed ownership loss when thread launch reported an error after
the worker had started. All original assertions remain. Those eleven passed
in 0.979 seconds. Two diagnostic contracts at `7f357e1` then exposed an overly
broad shutdown-only suppression of operating-system errors: disk-full and
permission failures must still be reported. The final **13 tests pass in 1.004
seconds** after narrowing suppression to expected socket errors. Existing
onboarding-center coverage passes **75 tests in 58.003 seconds**.

Runtime: `dfb2294ebc294b73b7b8c7c39d194795fb9c529c`, based on PR #31 at
`2a98f7fb2ee5e788bf9f4c1552faecffbc14038e`. The final runtime changes one module;
the contracts and evidence are separate commits. Complete checks and immutable
source/log hashes are recorded in the
[verification receipt](../evaluations/2026-10-31/onboarding-shutdown-verification-v1.json)
and draft PR. Results from the previous branch's long-running regression are
tracked separately and are not substituted for this runtime.

At the final runtime, all **17 JavaScript/browser suites** passed and their runner
exited 0; the five existing public-documentation checks also passed. A new full
Python regression is running separately. Its terminal result and hosted checks
must be read from the draft PR; neither is counted as passed in this receipt.

That previous local run has now finished: 1,490 tests, four existing native-symlink
skips and no failures in 5,367.991 seconds. It is the Node follow-up's result,
not a full-regression pass for this new HTTP change. The earlier bootstrap
timeout still has no proven root cause or repeat-stability evidence.

## Behavior

- Stop accepting connections and further buffered requests when close begins.
- Track the server's accepted sockets and successfully started request threads.
  Keep ownership if launch reports an error after the worker starts; do not join
  a thread whose start failed before it began.
- Disconnect owned sockets to release idle reads, incomplete uploads and stalled
  response writes. Detach before the public `socket.close(descriptor)` call so a
  later file-wrapper close cannot accidentally close a reused descriptor.
- Preserve existing service shutdown/revocation, then join the accepted handlers
  before returning. A disconnect or service-close failure remains an error, and
  the drain still runs. Concurrent successful close calls share one completion.
- Refuse self-closure from a request thread; the owning server thread must close
  it. The existing serve-loop shutdown contract is unchanged.

No client response is guaranteed after shutdown begins. A request already in its
application operation may finish persisting before the barrier returns. Inspect
saved state after restart; a lost response is not permission to automatically
retry an application or external action. Final Submit remains user-only.

This barrier covers HTTP request threads. It is not a hard shutdown deadline,
forced termination proof, power-loss guarantee or a new cancellation mechanism
for separately started guided-preparation workers. Those workers, blocked
application operations and broader recovery remain separate lifecycle work.
No arbitrary process, socket or historical temporary directory is searched for
and killed or deleted by this change.

## Actual browser observation

The standard-resume scenario ran on Windows/Edge with actual loopback HTTP,
DPAPI storage and a synthetic, already-confirmed PDF profile. It generated and
downloaded DOCX, enforced confirmation, adopted the new master, invalidated old
approvals and reopened saved state. Chinese desktop and English mobile images
were inspected; no horizontal overflow was observed. The child exited 0 and
the existing harness required its `temporary_state_removed` confirmation.

This was browser automation, not an independent first-time user. It did not test
AI extraction, a real applicant, Word/PDF conversion, an employer or a mailbox.
The optional Word capability remained detected but unverified. No new model
request or real external action was made. All screenshot and file content is
synthetic; the exact new suite results are in the receipt.

## Reproduce and recover

```powershell
python -m unittest discover -s tests -p "test_onboarding_shutdown*.py" -v
python -m unittest discover -s tests -p test_onboarding_center.py -v
node tests/standard_resume_ui_e2e.cjs
npm run test:e2e
python -m unittest discover -s tests -v
```

Use the existing development setup. No new product environment variable,
dependency or database migration is required. This milestone's isolated worktree
reuses the previous environment's exact dependency versions; its signed Python
launcher is authenticated, not development-signed. Packaging regression still
uses the explicitly selected authenticated Node prerequisite documented in the
previous milestone. These test tools do not grant production signing authority.

Before reverting, close the local application, preserve the existing encrypted
state/index and confirm no owned work remains. An ordinary reviewed revert of
this runtime change restores the previous behavior without transforming data;
that also restores the request/cleanup race. Do not downgrade while the new
process is still running, delete retained evidence, weaken cleanup assertions or
alter approval state to make rollback appear successful. No release, store update
or deployment is performed by this PR. Trusted signing and independent clean
Windows acceptance remain unavailable.

## References and remaining acceptance

On 2026-10-31, the installed Python 3.11.3 standard-library implementation was
inspected alongside the official
[threaded-server lifecycle documentation](https://docs.python.org/3.11/library/socketserver.html)
and [socket API](https://docs.python.org/3.11/library/socket.html). The current
3.11 documentation identifies patch version 3.11.18; that is not the local test
interpreter. Microsoft's [Winsock shutdown documentation](https://learn.microsoft.com/en-us/windows/win32/api/winsock/nf-winsock-shutdown)
distinguishes disabling communication from closing its socket. Local probes,
not a claim of identical behavior on every platform, motivated the descriptor
handling. Public APIs were used; no third-party implementation was copied and no
dependency was upgraded.

The complete material-quality target, independent-user trials, live ATS/provider
validation, connected-mailbox operation, signing and clean-Windows installation,
upgrade/data-retention/rollback acceptance remain open. This reliability repair
does not change their status or establish that JobFlow meets the whole GOAL.
