# Isolated release Node selection

The full local run for mail-review runtime `d3ee30c` did **not** pass: 1,485 tests,
five failures, one error and four native-symlink skips in 4,843.308 seconds. Keep
that observation separately from the passing mail and JavaScript checks.

## Environment mismatch and repair

Three CI-staging tests expected exact Node 24.19.0 bytes; two signing-handoff
tests stopped at `JOBFLOW_RELEASE_NODE_NOT_TRUSTED` before the expected external
signature gate. The host-managed runtime was Node 24.22.1, validly signed by
OpenAI OpCo, LLC. Its publisher and bytes did not match this repository's release
policy. Rejection was correct; this is not a claim that the host runtime is
malicious or that mail logic failed.

The builder only searched shared host locations. Optional `-NodePath` now selects
a separate approved tool, consistent with the existing release-verification
entry. The explicit input must be a canonical absolute local file named
`node.exe`, at most 256 MiB. Relative/UNC/device/alternate-stream paths, missing
files and reparse points fail. A bad explicit selection never falls back.

The selected file still passes the existing locked Authenticode, publisher,
content-identity and single-hardlink checks before execution; writes/deletion are
denied while held and identity is checked afterward. No signer, unsigned
exception, trust-policy entry or production approval is added. Default discovery
is unchanged. No release command downloads or installs a tool.

## Provenance

On October 31, 2026, the
[official Node 24.19.0 release record](https://nodejs.org/en/blog/release/v24.19.0)
was checked against the existing repository pin. An isolated copy of its
[Windows x64 binary](https://nodejs.org/dist/v24.19.0/win-x64/node.exe)
was downloaded to ignored local reports and verified before execution:

- Product version: `24.19.0`.
- SHA-256: `3602f2bb1a10f2cbab4c36886218a33c1ab3db87290e73b033c46c77147d0237`.
- Authenticode: `Valid`, the existing allowed OpenJS Foundation subject.
- Signer thumbprint: `8EA1D142EA3F46023BACA38C23A7E7AE6AFCE30C`.

Neither host runtime nor PATH was replaced. The published hash and Authenticode
were checked; no independent PGP-key ceremony is claimed. This is the existing
test prerequisite, not a claim that 24.19.0 is the newest Node release.

## Run

Use this checkout's prepared Python environment and locked JavaScript tree.
Obtain the official binary in an isolated directory and verify the hash and
signature above. Set the test-only override in the current shell:

```powershell
$env:JOBFLOW_TEST_NODE_PATH = 'C:\isolated-tools\node-v24.19.0\node.exe'
.venv\Scripts\python.exe -m unittest discover -s tests -p test_release_node_path.py -v
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The helper checks the pinned bytes; it never skips an unexpected prerequisite.
CI still stages signed 24.19.0 and tests default discovery. Local signing-handoff
fixtures pass the override through the real production `-NodePath` argument.
Fixture keys/manifests remain synthetic and cannot sign a production update.

For an otherwise prepared handoff, add
`-NodePath 'C:\isolated-tools\node-v24.19.0\node.exe'` to the normal Prepare or
Finalize invocation in the [release checklist](release-checklist.md). Use the
same tool and original inputs in both stages. All publisher-evidence, signature,
source-version and runtime-closure gates still apply. The product and production
builder do not read `JOBFLOW_TEST_NODE_PATH`.

## Validation and uncertainty

Five path/trust methods were committed before implementation. Initially there
were nine failing assertions/subtests because the parameter was unavailable.
The unchanged five pass in 19.900 seconds with real Windows Authenticode and
locked file identity: official bytes, spaces, invalid paths, unsigned/renamed
files and hardlinks. Existing CI tests retain exact hashes, first-match rejection
and no-overwrite assertions.

All 14 existing packaging and two-stage handoff regression methods pass in
248.660 seconds, including the five previously failing Node cases. Prepare
still stops at `JOBFLOW_PROTECTED_SIGNATURE_REQUIRED`; no production signature
was supplied. Five documentation checks pass. A new complete Python run starts
from runtime `d9bec0ab172538f402bbab7a69da2446b9ac1554` using the isolated tool;
it is still running at this receipt's creation time, not reported as passed.

The sixth full-run issue was the bootstrap health-timeout test's outer 90-second
harness expiring. One diagnostic rerun, without changing health code or its
timeouts, passed in 11.388 seconds and checked exit 4, child cleanup, no temporary
health entries and no installed pointer. Its cause remains **unresolved**. That
isolated pass does not establish a stable full suite.

The [verification receipt](../evaluations/2026-10-31/release-node-verification-v1.json)
records commits, failures and current regression state. Later terminal results
belong in the draft PR and task ledger; creation-time evidence remains intact.
No model-quality, real ATS/mailbox or independent-human result is claimed.

There is no data migration, merge, release, installation or production signing.
Rollback is an ordinary source revert; remove the test-only shell override and
retain all evidence. It restores the inability to select an isolated tool in
the signing builder. Trusted production signing and independent clean Windows
installation, upgrade, data-preservation and rollback acceptance remain
unavailable as previously confirmed by the owner.
