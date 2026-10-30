# Bounded loopback rejection - 29 October 2026

The full local run at `c6c64d33ebc83bfacc43641b588cd7ba5cf7a4c9`
failed with one failure, three errors and four skips out of 1,308 tests. Three
results involved missing optional MCP/document dependencies. The remaining error
was Windows socket error 10053 while reading a rejected development-extension
pairing request. That original observation is retained; a successful focused run
does not change its result.

## Repair

The clean-Windows acceptance probe rejected origin/browser checks before reading
the request body. Closing a socket with unread in-flight bytes can hide its JSON
error response on Windows. The probe also accepted duplicate `Content-Length`
headers. The onboarding server already rejected that framing, but its separate
small-body drain reset a socket timeout across buffered reads, allowing a slow
sender to extend the total wait.

Both handlers now share a bounded byte-disposal helper. It reads at most 64 KiB
under one 250 ms monotonic deadline, using one underlying read at a time and
restoring the socket's previous timeout. One known small body is consumed only
to its declared end. Ambiguous framing is never decoded or accepted; only a
bounded prefix is discarded before closing. A large or late body may still cause
a transport reset, so this is not a promise of JSON delivery for every malformed
request.

The probe rejects duplicate lengths, explicitly closes rejection responses and
tracks when its body has already been consumed. It cannot read an invalid JSON
body twice or accept a pipelined follow-up request on a rejected connection. A
reused valid connection resets the consumed-body marker for each new POST.

Origin, browser identity, exact extension version, native-binding proof and user
authorization checks retain their requirements. This change grants no release
approval, browser action, retry permission or clean-Windows acceptance. Normal
accepted uploads and whole-request/concurrency resource limits are outside this
small rejection-drain change.

## Verification

Eight contracts were frozen at
`41cb9193233359f45a894a8094223302d91115d6`: four passed and four failed before
implementation; the same eight pass after repair. The fake slow-reader contract
recorded 7.04 seconds under the old repeated-timeout loop and at most 0.4 seconds
under the single-deadline contract. That is a deterministic contract observation,
not measured browser latency. A test-harness import typo preceded the corrected
freeze; its one import error is retained separately, not counted as a product defect.

The final related run passes **107 tests in 53.109 seconds**, including the original
failing browser-probe assertion, onboarding HTTP, native-binding and six additional
boundary checks. Actual local sockets exercise a burst of rejected requests,
duplicate lengths, transfer encoding, incomplete bodies, connection reuse and
pipelining. Synthetic headers and binding fixtures do not establish that installed
Chrome/Edge store extensions or a clean Windows machine passed acceptance.

The new worktree was prepared with its declared `.[build,mcp,document]` dependencies,
an isolated signed Python 3.11.3 launcher and the already-pinned JavaScript tree.
The copied tree passed the existing validator before and after copying: 173 files,
18,516,238 bytes, SHA256
`df85481a30bcbc0f7276548d245eba0980d136c8085ca4a3662cb6f3c568302f`.
Earlier isolated MCP/parser checks pass. Compatibility metadata and the installed
CLI pass. No dependency version or production signature policy was weakened.

Full discovery started on the repaired source commit
`d281fbc3d54ed76514507abe2b913e99c41f692f`. At this note's initial observation it
was still running. Its later result must be recorded separately, including any
failures; the 107 focused passes do not stand in for full success. See the
[verification receipt](../evaluations/2026-10-29/probe-rejection-verification.json).

## Run, configuration and rollback

Follow the README Development prerequisites separately in this worktree, then run:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_probe_http_rejection.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_http_rejection_boundaries.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The byte/time caps are fixed internal limits, not a new environment setting.
There is no model invocation, UI change, database migration, mailbox access or
production action. Roll back this source change and restart the local service if
needed, preserving its existing encrypted data. The rollback restores the known
rejection limitations; it does not alter Profile v3's separate backup requirement.

Full-material semantic acceptance, real ATS/mailbox workflows, independent human
acceptance, trusted production signing and independent clean-Windows installation,
upgrade and rollback remain open. The user has confirmed the last two external
prerequisites are unavailable.
